from __future__ import annotations

from .request import build_page_verification_summary
from typing import Any


def render_text(result: dict) -> str:
    lines = [
        "# 本地知识查询结果",
        "",
        f"- 学科：{result['subject']}",
        f"- 查询：{result['query']}",
        f"- 意图：{result['intent']}",
        f"- 命中层：{result['answer_mode']}",
        "",
    ]
    if result["fallback_note"]:
        lines.extend(["## 回退说明", "", f"- {result['fallback_note']}", ""])
    book_route = dict(result.get("book_route") or {})
    if book_route.get("match_status") != "not_found":
        lines.extend(["## 书系路由", "", f"- 状态：{book_route.get('match_status', '')}", f"- 书系：{book_route.get('canonical_title') or '未确定'}", f"- 阶段：{book_route.get('stage') or '未确定'}", ""])
    exercise_route = dict(result.get("exercise_route") or {})
    if exercise_route.get("match_status") not in {None, "", "not_requested"}:
        lines.extend(["## 习题路由", "", f"- 状态：{exercise_route.get('match_status', '')}", f"- 习题键：{exercise_route.get('exercise_key') or '未确定'}", f"- 配对状态：{exercise_route.get('pair_status') or '未确定'}", ""])
    grounding = dict(result.get("answer_grounding") or {})
    if grounding.get("required"):
        lines.extend(
            [
                "## 原书答案门控",
                "",
                f"- 状态：{grounding.get('status', 'answer_not_found')}",
                f"- 可输出结论：{'是' if grounding.get('can_conclude') else '否'}",
                f"- 失败原因：{grounding.get('failure_reason') or '无'}",
                f"- 下一步：{grounding.get('next_action') or '可按原书答案继续核对。'}",
                "",
            ]
        )
    page_anchor = dict(result.get("page_anchor") or {})
    if page_anchor.get("requested_page") is not None:
        page_crosscheck = dict(result.get("page_crosscheck") or {})
        verification = dict(
            result.get("page_verification")
            or build_page_verification_summary(
                page_anchor,
                result["answer_mode"],
                page_crosscheck,
                request_resolution=dict(result.get("request_resolution") or {}),
                answer_grounding=dict(result.get("answer_grounding") or {}),
                teaching_bundle=dict(result.get("teaching_bundle") or {}),
                page_content_bundle=dict(result.get("page_content_bundle") or {}),
            )
        )
        lines.extend(
            [
                "## 页码核验摘要",
                "",
                f"- 页面定位：{verification['page_location_status']}",
                f"- 题号正文核验：{verification['exercise_verification_status']}",
                f"- 小节起始页交叉核验：{verification.get('page_crosscheck_status', 'not_requested')}",
                f"- 命中层：{verification['answer_mode']}",
                f"- 可否按教材正文讲解：{'可以' if verification['textbook_explanation_allowed'] else '不可以'}",
                f"- 结论：{verification['summary']}",
                f"- 教材：{page_anchor.get('book_title') or page_anchor.get('requested_book_title') or '未确定'}",
                f"- 印刷页：{page_anchor.get('requested_page')}",
            ]
        )
        if page_anchor.get("source_image_path"):
            lines.append(f"- 原图：{page_anchor['source_image_path']}")
        if page_anchor.get("source_asset_kind") == "pdf":
            lines.append(f"- PDF 页：{page_anchor.get('pdf_page')} | 来源：{page_anchor.get('source_asset_path')}")
        lines.append("")
    exercise_anchor = dict(result.get("exercise_anchor") or {})
    if exercise_anchor.get("status") != "not_requested":
        lines.extend(["## 课后题答案定位", "", f"- 状态：{exercise_anchor.get('status')}"])
        textbook_location = dict(result.get("textbook_location") or {})
        if textbook_location.get("container_path"):
            lines.append(f"- 结构标题：{' / '.join(textbook_location['container_path'])}")
        elif textbook_location.get("candidates"):
            choices = [" / ".join(item.get("container_path") or []) or str(item.get("exercise_label") or "") for item in textbook_location["candidates"]]
            lines.append(f"- 候选结构：{'；'.join(choices)}")
        if exercise_anchor.get("exercise_label"):
            lines.append(f"- 题号：{exercise_anchor['exercise_label']}")
        if exercise_anchor.get("answer_pdf_pages"):
            lines.append(f"- 答案印刷页：{exercise_anchor.get('answer_printed_pages', [])} | 答案 PDF 页：{exercise_anchor['answer_pdf_pages']}")
    grounding = dict(result.get("answer_grounding") or {})
    if grounding.get("required"):
        lines.extend(["", "## 原书答案门控", "", f"- 状态：{grounding.get('status')}", f"- 可输出结论：{'是' if grounding.get('can_conclude') else '否'}", f"- 原因：{grounding.get('failure_reason') or '原题与原书答案均已确认。'}", f"- 下一步：{grounding.get('next_action') or '可按原书答案继续核对。'}"])
        lines.append("")
    teaching_context = dict(result.get("teaching_context") or {})
    if teaching_context.get("history_used"):
        lines.extend(
            [
                "## 有界历史教学上下文",
                "",
                f"- 范围匹配：{teaching_context.get('scope_match', 'none')}",
                f"- 本次使用：{', '.join(teaching_context.get('history_used', []))}",
                "- 作用边界：只调整讲解方式，不修改事实与引用。",
                "",
            ]
        )
    if result["syllabus_route"]:
        lines.extend(["## 考纲路由", ""])
        for item in result["syllabus_route"]:
            lines.append(f"- {item['title']} (`{item['node_id']}`)")
        lines.append("")
    if result["claim_hits"]:
        lines.extend(["## 主张命中", ""])
        for claim in result["claim_hits"][:5]:
            lines.append(f"- {claim['text']} [{claim['claim_type']}]")
        lines.append("")
    if result["references"]:
        lines.extend(["## 证据引用", ""])
        for ref in result["references"]:
            section_text = f" | 小节 {ref['section_title']}" if ref.get("section_title") else ""
            role = f" | 角色 {ref['role']}" if ref.get("role") else ""
            page_text = f" | 印刷页 {ref['printed_page']} | PDF 页 {ref['pdf_page']}" if ref.get("printed_page") or ref.get("pdf_page") else ""
            lines.append(f"- {ref['title']} | 页段 {ref['page_span']} | 图片 {ref['image_span']}{page_text} | chunk {ref['chunk_id']}{section_text}{role}")
        lines.append("")
    if result["fallback_hits"]:
        lines.extend(["## 章节回退", ""])
        for item in result["fallback_hits"]:
            lines.append(f"- {item['chapter_title']} | {item['chapter_overview']}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def render_exercise_batch_text(payload: dict[str, Any]) -> str:
    summary = dict(payload.get("summary") or {})
    lines = [
        "# 王道 408 多题查询结果",
        "",
        f"- 批次状态：{payload.get('batch_status', 'blocked')}",
        f"- 请求：{summary.get('requested_count', 0)} 题；精确：{summary.get('exact_count', 0)} 题；阻塞：{summary.get('blocked_count', 0)} 题",
        "",
    ]
    for item in payload.get("items", []) or []:
        focus = "（重点）" if item.get("emphasis") else ""
        lines.extend([f"## 第 {int(item.get('exercise_label') or 0)} 题{focus}", ""])
        grounding = dict(item.get("answer_grounding") or {})
        lines.append(f"- 状态：{grounding.get('status', 'answer_not_found')}")
        teaching = dict(item.get("teaching_bundle") or {})
        if item.get("status") == "exact":
            lines.extend(["", teaching.get("problem_text", ""), "", teaching.get("source_answer_text", ""), ""])
        else:
            lines.extend([f"- 原因：{grounding.get('failure_reason', '')}", f"- 下一步：{grounding.get('next_action', '')}", ""])
    return "\n".join(lines).rstrip() + "\n"
