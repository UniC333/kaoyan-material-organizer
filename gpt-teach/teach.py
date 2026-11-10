#!/usr/bin/env python3
"""GPT 讲题层：本地证据检索 + Codex CLI(GPT) 讲解。

流程：
  1. 调用 scripts/ask_local_knowledge.py --format teaching-json 做本地证据检索
     （复用现有问答链路与证据门禁；最小第一步“只给一级提示”：教学要求在请求解析层剥离白名单短语，教学决策与证据投影在本文件内按模式分流，门禁与索引不动）；
  2. 把证据包拼成教学 prompt，交给 `codex exec`（已用 ChatGPT Plus 登录）生成讲解；
  3. 输出讲解正文。

本地来源门禁与投影先行；prompt 保留归因要求。只有证据包中标记为 exact/已确认且带引用的内容
才能当作教材原文来讲；blocked / asset_only / unconfirmed 时必须明说证据不可用，
通用讲解要明确标注"非教材原文"，不得冒充。
教学模式未定或缺少题目时固定确认，在画像、prompt 与 GPT 之前短路。
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import textwrap
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import context

TOOL_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
ASK_SCRIPT = TOOL_SCRIPTS / "ask_local_knowledge.py"
sys.path.insert(0, str(TOOL_SCRIPTS))
from kaoyan_kb.domain.query.teaching_intent import split_teaching_requirement
# Explicit executable > PATH. Resolve at import without a machine-specific path.
CODEX_BIN = os.environ.get("CODEX_BIN") or shutil.which("codex") or "codex"

MAX_FIELD_CHARS = 6000

LATEX_RULES = (
    "数学公式排版规范（必须严格遵守，否则公式无法渲染）：\n"
    "- 行内公式用 $…$，复杂公式用 $$…$$ 独立成行；\n"
    "- 可用 \\begin{cases} 等环境，但 cases 内换行只用普通 \\\\，禁用 \\\\[4pt] 这类带间距的换行；\n"
    "- 指数一律写成 2^{1/x} 这样的行内除法形式，禁用嵌套 \\frac、\\dfrac；\n"
    "- 禁用 \\large \\Large \\huge 等一切字号命令，保持默认字号；\n"
    "- 不使用 HTML 公式卡片。"
)


def parse_progress_context(raw):
    try:
        progress = json.loads(raw)
    except json.JSONDecodeError:
        print("[WARN] --progress-context 不是合法 JSON，忽略学习进度。", file=sys.stderr)
        return None
    fields = ("subject", "chapter", "current_gate", "verified", "status", "updated_at")
    if (not isinstance(progress, dict)
            or any(field not in progress for field in fields)
            or any(not isinstance(progress[field], str)
                   for field in fields if field != "verified")
            or not isinstance(progress["verified"], list)
            or any(not isinstance(item, str) for item in progress["verified"])
            or progress["status"] not in ("独立完成", "定位完成", "提示后待验证")):
        print("[WARN] --progress-context 字段缺失或无效，忽略学习进度。", file=sys.stderr)
        return None
    return {field: progress[field] for field in fields}


def parse_args():
    p = argparse.ArgumentParser(description="本地证据检索 + GPT 讲解")
    p.add_argument("--subject", required=True, help="学科，如 数学 / 408 / 英语")
    p.add_argument("--question", required=True, help="原始问题（自然语言）")
    p.add_argument("--book-title", default=None)
    p.add_argument("--chapter", default=None)
    p.add_argument("--printed-page", type=int, default=None)
    p.add_argument("--exercise-label", default=None)
    p.add_argument("--progress-context", type=parse_progress_context, default=None,
                   help="学生学习进度快照（JSON 字符串，含 subject/chapter/current_gate/verified/status/updated_at）")
    p.add_argument("--project-root", default=None, help="显式指定学生背景的项目根目录")
    p.add_argument("--no-profile", action="store_true", help="关闭学生背景加载")
    p.add_argument("--require-profile", action="store_true", help="要求学生背景存在，加载失败则退出")
    p.add_argument("--topk", type=int, default=3)
    p.add_argument("--model", default="gpt-6.1-sol", help="传给 codex exec 的 -m 参数（默认 gpt-6.1-sol）")
    p.add_argument("--effort", default="high",
                   help="model_reasoning_effort 档位：low/medium/high/xhigh/max/ultra（默认 high）")
    p.add_argument("--timeout", type=int, default=300, help="codex exec 超时秒数")
    p.add_argument("--dry-run", action="store_true", help="只打印拼好的 prompt，不调用 GPT")
    p.add_argument("--show-evidence", action="store_true", help="先打印证据包摘要再讲解")
    return p.parse_args()


def run_local_ask(args):
    cmd = [
        sys.executable, str(ASK_SCRIPT),
        "--subject", args.subject,
        "--question", args.question,
        "--format", "teaching-json",
        "--topk", str(args.topk),
    ]
    if args.book_title:
        cmd += ["--book-title", args.book_title]
    if args.chapter:
        cmd += ["--chapter", args.chapter]
    if args.printed_page is not None:
        cmd += ["--printed-page", str(args.printed_page)]
    if args.exercise_label:
        cmd += ["--exercise-label", args.exercise_label]
    proc = subprocess.run(
        # Keep the caller's cwd so ask can discover its runtime config.
        cmd, capture_output=True, text=True, timeout=120
    )
    if proc.returncode != 0:
        raise SystemExit("[ERROR] 本地检索失败:\n" + proc.stderr[-2000:])
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise SystemExit("[ERROR] 本地检索返回非法 JSON: %s" % exc)


def _clip(text, limit=MAX_FIELD_CHARS):
    text = str(text or "")
    if len(text) <= limit:
        return text
    return text[:limit] + "\n…（已截断）"


def decide_teaching_action(view):
    """Read the existing parsed action; unsupported internal modes stay placeholders.

    Natural-language verification/acceptance signals are parsed as clarify;
    this does not implement verification or acceptance workflows.
    """
    if view.get("source_targets_version"):
        action, _, _ = split_teaching_requirement(view.get("original_query", ""))
        children = [(item.get("result") or {}).get("request_resolution") or {}
                    for item in view.get("items", [])]
        if action or any(child.get("teaching_action") for child in children):
            return "clarify", "跨目标教学要求须逐题确认"
    parsed = str((view.get("request_resolution") or {}).get("teaching_action") or "")
    if parsed == "hint":
        return "hint", ""
    if parsed == "clarify":
        reason = "教学要求或边界尚未确定，按 clarify 待学生确认"
        print("[INFO] " + reason, file=sys.stderr)
        return "clarify", reason
    if parsed in ("verify", "accept", "locate"):
        reason = "teaching_action=%s 尚未实现，按 full 处理" % parsed
        print("[INFO] " + reason, file=sys.stderr)
        return "full", reason
    return "full", ""


def build_evidence_packet(view, mode="full"):
    """Render the evidence packet from a local answer view.

    In "hint" and "clarify" modes the packet projects the problem text, location and
    confirmed concept anchors only: it must NOT project source_answer_text,
    the generic bundle's conclusion/explanation/citations, or the page
    content bundle's content, so the model cannot leak the final answer
    into a hint (reveal budget enforced here, at the projection layer).
    The question line uses the problem fragment (teaching requirement
    already stripped) in both hint and clarify modes; an empty target stays empty.
    """
    lines = []
    question = str(view.get("question", ""))
    if mode in ("hint", "clarify"):
        request_resolution = view.get("request_resolution") or {}
        question = str(request_resolution.get("problem_query") or "")
    lines.append("问题：" + question)
    mode_label = {"hint": "hint（一级提示）", "clarify": "clarify（待澄清）"}.get(mode, "full（完整讲解）")
    lines.append("教学模式：" + mode_label)
    lines.append("学科：" + str(view.get("subject", "")))
    br = view.get("book_resolution") or {}
    lines.append("定位教材：%s（状态 %s）" % (br.get("book_title", ""), br.get("status", "")))
    lines.append("answer_mode：" + str(view.get("answer_mode", "")))
    pv = view.get("page_verification") or {}
    lines.append("教材讲解是否允许：" + str(pv.get("textbook_explanation_allowed")))
    if pv.get("summary"):
        lines.append("核验摘要：" + str(pv["summary"]))
    ag = view.get("answer_grounding") or {}
    if ag.get("status") not in ("", "not_applicable", None):
        lines.append(("答案门禁：%s %s %s" % (
            ag.get("status"), ag.get("failure_reason", ""),
            ag.get("next_action", ""))).strip())
    gab = view.get("generic_answer_bundle") or {}
    if gab.get("status") not in ("", "not_applicable", None):
        lines.append("通用答案包状态：" + str(gab.get("status")))
        if mode not in ("hint", "clarify"):
            if gab.get("conclusion"):
                lines.append("本地结论：" + _clip(gab["conclusion"], 1500))
            if gab.get("explanation"):
                lines.append("本地解释：" + _clip(gab["explanation"]))
            if gab.get("citations"):
                lines.append("引用：" + json.dumps(gab["citations"], ensure_ascii=False)[:800])
        if gab.get("failure_reason"):
            lines.append("阻塞原因：" + str(gab["failure_reason"]))
        if gab.get("next_action"):
            lines.append("建议下一步：" + str(gab["next_action"]))
    tb = view.get("teaching_bundle") or {}
    if tb.get("status") not in ("", "not_applicable", None):
        lines.append("习题讲解包状态：" + str(tb.get("status")))
        if tb.get("problem_text"):
            lines.append("题干（教材原文）：" + _clip(tb["problem_text"]))
        if mode not in ("hint", "clarify") and tb.get("source_answer_text"):
            lines.append("原书答案（教材原文）：" + _clip(tb["source_answer_text"]))
        cit = tb.get("citations") or {}
        pages = [x for x in (cit.get("problem_printed_pages") or [])
                 + (cit.get("solution_printed_pages") or []) if x]
        if pages:
            lines.append("印刷页码：" + str(pages))
    pcb = view.get("page_content_bundle") or {}
    if pcb.get("status") not in ("", "not_applicable", None):
        lines.append("页面内容包状态：" + str(pcb.get("status")))
        if mode not in ("hint", "clarify") and pcb.get("content"):
            lines.append("页面正文（教材原文）：" + _clip(pcb["content"]))
        if pcb.get("failure_reason"):
            lines.append("阻塞原因：" + str(pcb["failure_reason"]))
        if pcb.get("next_action"):
            lines.append("建议下一步：" + str(pcb["next_action"]))
    return "\n".join(lines)


CLARIFY_TEXT = "这次你希望我只给一级提示，还是完整讲解？请把题目与教学要求分开写；如果不同题要求不同，请逐题说明。"
MISSING_PROBLEM_TEXT = "请先告诉我要讲哪道题，可写书名、页码和题号，或直接贴题干；再注明“只给一级提示”或“完整讲解”。"


def fixed_clarification(problem):
    return CLARIFY_TEXT if problem and str(problem).strip() else MISSING_PROBLEM_TEXT


def build_prompt(args, packet, profile=None, mode="full", question=None):
    if mode == "clarify":
        return fixed_clarification(question)
    display_question = args.question if question is None else question
    prompt_context = ""
    progress = getattr(args, "progress_context", None)
    if progress is not None:
        lines = ["【本次题目与显式教材上下文】", "问题：" + display_question,
                 "学科：" + args.subject]
        for field, label in (("book_title", "教材"), ("chapter", "章节"),
                             ("printed_page", "印刷页码"), ("exercise_label", "题号")):
            value = getattr(args, field, None)
            if value is not None:
                lines.append("%s：%s" % (label, value))
        prompt_context = "\n".join(lines) + "\n\n"
    if profile is not None:
        today = datetime.now(ZoneInfo("Asia/Shanghai")).date()
        prompt_context += "\n" + context.render_background(
            profile, context.active_overrides(profile, today)
        ) + "\n\n"
    if progress is not None:
        prompt_context += (
            "【学生进度背景】\n"
            "以下是学习状态记录，不是教材原文，不得作为引用、页码或教材结论的依据。\n"
            + json.dumps(progress, ensure_ascii=False) + "\n"
            "仅用于调整讲解起点和待验证步骤，不改变证据门禁；"
            "即使记录准确或标为已验证，也不能据此生成教材原文或页码引用。\n\n"
        )
    if mode == "hint":
        rule3 = (
            "只给一级提示：指出下一步的方向或关键切入点，不给最终结论、数值与完整证明链；"
            "提示后请学生先自己尝试。推导与原书冲突时停止判断，请用户核对答案原图。\n"
            "一级提示的边界：只给方向或切入点（例如应使用的方法或定理、应先处理的部分），"
            "不给可直接得出结论的中间结果（如代入后可直接算出的表达式、化简后的关键等式）。"
        )
        rule5 = "用中文回答，结构清晰：先给提示，再请学生尝试；最后列出证据来源（书名、页码、题号）。"
    else:
        rule3 = (
            "若原书答案存在，先给出已确认的原书答案/结论，再补充思路讲解；"
            "推导与原书冲突时停止判断，请用户核对答案原图。"
            "补充讲解按来源角色分段：已确认的教材内容、基于题干的独立推导、通用概念各自分开写，"
            "每项实质内容（开头摘要、列表项、独立公式、证明单元、末尾总结都算）都必须落在明确且唯一的来源标记作用域内；"
            "一个段落、列表项或证明单元只归属一种来源角色，混合角色的内容必须拆开写，"
            "不得把通用定理陈述藏进本题推导，也不得把本题推导全部标为通用。"
            "通用内容用完整标记“通用讲解（非教材原文）”标出，不得附教材页码、题号或原文引用；"
            "基于题干的独立推导用“本题推导”明确标出，写清本题前提与推导步骤，不声称教材逐字如此；"
            "已确认的教材内容只写证据包中明确标识的内容，概括或转述要标明为结论转述，不得伪装逐字引文；"
            "补充思路不得笼统归为通用，也不得继承前面教材内容的身份，exact 状态不等于整份输出都是教材内容。"
            "最后的来源列表只支持明确标识的教材内容，不覆盖通用段或生成的独立推导；"
            "没有某类内容时不强制填充该类小节；无可用教材证据时优先遵守规则 2，不得为填充教材小节而编造教材内容。"
        )
        rule5 = "用中文回答，结构清晰：先给结论，再讲关键思路，最后列出证据来源（书名、页码、题号）。"
    return textwrap.dedent(
        "你是考研指导老师，学科：%s。下面是本地考研知识库对用户问题的检索结果"
        "（含证据门禁状态），请基于它为用户讲解。\n\n"
        "%s【本地证据包】\n%s\n\n"
        "【讲解规则】\n"
        "1. 证据门禁优先：只有证据包中明确标记为已确认（exact/已确认）且带引用的内容，"
        "才能当作教材原文来讲；讲解时保留书中变量名、符号顺序和结构条件，不要混入其他解法。\n"
        "2. 如果证据包显示 blocked / asset_only / unconfirmed（证据不可用），必须先明确告诉用户"
        "“教材原文证据暂不可用”并给出建议下一步；之后可以给一份明确标注为"
        "“通用讲解（非教材原文）”的独立讲解，不得把通用讲解冒充成教材内容，"
        "不得编造页码、题号或原文引用。\n"
        "3. %s\n"
        "4. %s\n"
        "5. %s\n"
        "6. 直接输出讲解正文。不要读写任何文件，不要执行任何命令，不要提及工具或实现细节。\n"
    ) % (args.subject, prompt_context, packet, rule3, LATEX_RULES, rule5)


def call_gpt(prompt, args):
    cmd = [CODEX_BIN, "exec", "--skip-git-repo-check", "-s", "read-only",
           "-m", args.model, "-c", "model_reasoning_effort=%s" % args.effort,
           prompt]
    env = dict(os.environ)
    env["PATH"] = env.get("PATH", "") + ":/opt/hatch-image/bin"
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True,
            timeout=args.timeout, stdin=subprocess.DEVNULL, env=env,
        )
    except subprocess.TimeoutExpired:
        raise SystemExit("[ERROR] GPT 调用超时（%ss）" % args.timeout)
    out = (proc.stdout or "").strip()
    if proc.returncode != 0 and not out:
        raise SystemExit("[ERROR] GPT 调用失败:\n" + (proc.stderr or "")[-2000:])
    keep = []
    for ln in out.splitlines():
        s = ln.strip()
        if s in ("codex", "tokens used"):
            continue
        keep.append(ln)
    cleaned = "\n".join(keep).strip()
    return cleaned or out


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    args = parse_args()
    view = run_local_ask(args)
    action, _decision_reason = decide_teaching_action(view)
    request_resolution = view.get("request_resolution") or {}
    if view.get("source_targets_version"):
        # The multi-target contract has no top-level request_resolution. Parse
        # its original_query as a whole; never aggregate child full over hint.
        problem = split_teaching_requirement(view.get("original_query", ""))[2]
    else:
        problem = str(request_resolution.get("problem_query") or "")
    if action == "clarify" or not problem.strip():
        if args.dry_run:
            print("【固定澄清输出；无模型 prompt】")
        print(fixed_clarification(problem))
        return 0
    display_question = problem if action == "hint" else args.question
    profile = None
    if not args.no_profile:
        root = context.find_project_root(args.project_root)
        profile = context.load_profile(root)
        if profile is None and args.require_profile:
            raise SystemExit("[ERROR] --require-profile 要求学生背景存在，但加载失败（见上方 WARN）。")
    packet = build_evidence_packet(view, mode=action)
    if args.show_evidence:
        print("===== 本地证据包 =====")
        print(packet)
        print("=====================\n")
    prompt = build_prompt(args, packet, profile, mode=action, question=display_question)
    if args.dry_run:
        print(prompt)
        return 0
    print(call_gpt(prompt, args))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
