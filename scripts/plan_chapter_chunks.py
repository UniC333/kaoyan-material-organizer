#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

from common import (
    ensure_learning_dirs,
    find_chunk_rule,
    is_placeholder,
    load_json,
    normalize_context,
    parse_manifest_table,
    parse_page_token,
    register_chapter_manifest,
    register_chunk_manifests,
    register_source_material,
    resolve_chapter_profile,
    save_json,
    split_chunks,
)

PLAN_JSON = "00_\\u5206\\u7247\\u8ba1\\u5212.json".encode("utf-8").decode("unicode_escape")
PLAN_MD = "01_\\u5206\\u7247\\u603b\\u89c8.md".encode("utf-8").decode("unicode_escape")
MANIFEST_NAME = "00_\\u7ae0\\u8282\\u56fe\\u7247\\u6e05\\u5355.md".encode("utf-8").decode("unicode_escape")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--context-json", required=True)
    parser.add_argument("--max-images-per-chunk", type=int)
    return parser.parse_args()


def render_overview(context: dict, chunks: list[dict]) -> str:
    lines = [
        "# 分片总览",
        "",
        f"- 批次编号：{context['batch_id']}",
        f"- 章节标题：{context['chapter_title']}",
        f"- 分片数量：{len(chunks)}",
        f"- 页码规则：{context['page_number_position_label']}",
        "",
        "| 片段 | 图片范围 | 页段 | 所属小节 | 待复核 |",
        "| --- | --- | --- | --- | --- |",
    ]
    for chunk in chunks:
        lines.append(f"| {chunk['chunk_id']} | {chunk['image_start']}-{chunk['image_end']} | {chunk['page_start']}-{chunk['page_end']} | {chunk['section_guess']} | {chunk['needs_review']} |")
    return "\n".join(lines) + "\n"


def usage_counter(rows: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        usage = row['usage_hint'].strip()
        counts[usage] = counts.get(usage, 0) + 1
    return counts


def usage_text(rows: list[dict]) -> str:
    return " ".join(row['usage_hint'].strip() for row in rows if row['usage_hint'].strip())


def usage_has(rows: list[dict], *keywords: str) -> bool:
    text = usage_text(rows)
    return any(keyword in text for keyword in keywords)


def section_or_usage_has(section_guess: str, rows: list[dict], *keywords: str) -> bool:
    text = f"{section_guess} {usage_text(rows)}"
    return any(keyword in text for keyword in keywords)


def clean_chapter_title(title: str) -> str:
    value = str(title or "").strip()
    value = value.replace("图片批次验收", "").replace("图片批次", "").strip()
    return value or "本章"


def clean_section_name(section: str, chapter_title: str) -> str:
    value = str(section or "").strip()
    if not value:
        return clean_chapter_title(chapter_title)
    value = value.replace("待细化", "").strip(" -+")
    value = value.replace("题目段", "题型训练")
    value = value.replace("解析段", "题解与解析")
    return value or clean_chapter_title(chapter_title)


def infer_usage_kind(section_guess: str, rows: list[dict]) -> str:
    counts = usage_counter(rows)
    if (counts.get("题解/解析") or usage_has(rows, "解析")) and (counts.get("习题") or usage_has(rows, "题目", "习题", "练习", "试题", "题型")):
        return "review"
    if counts.get("题解/解析") or usage_has(rows, "解析"):
        return "analysis"
    if counts.get("习题") or usage_has(rows, "题目", "习题", "练习", "试题", "题型"):
        return "exercise"
    if counts.get("例题") or usage_has(rows, "例题"):
        return "example"
    if counts.get("定理/公式"):
        return "rule"
    if counts.get("概念定义"):
        return "concept"
    if section_or_usage_has(section_guess, rows, "归纳", "总结", "拓展"):
        return "summary"
    return "mixed"


def derive_topic(section_guess: str, chapter_title: str) -> str:
    section = clean_section_name(section_guess, chapter_title)
    title = clean_chapter_title(chapter_title)
    if title and section.startswith(title):
        tail = section[len(title) :].strip(" ：:-")
        topic = title if not tail else f"{title}{tail}"
    else:
        topic = section or title or "本章内容"
    topic = topic.replace("题型训练", "").replace("题解与解析", "").strip(" ：:-")
    return topic or title or section or "本章内容"


def build_generic_focus(section_guess: str, rows: list[dict], chapter_title: str) -> str:
    usage_kind = infer_usage_kind(section_guess, rows)
    topic = derive_topic(section_guess, chapter_title)
    section_name = clean_section_name(section_guess, chapter_title)
    if usage_kind == "review":
        return f"这一段用 {topic} 的试题和答案解析做集中复盘，整理时应先按题型分组，再把每类题的触发信号、解题步骤和常见返工点串成可回看的主线。"
    if usage_kind == "analysis":
        return f"这一段围绕 {topic} 的题解与解析展开，整理时应优先串起题型信号、解题步骤和常见易错点，再回看对应题目。"
    if usage_kind == "exercise":
        return f"这一段围绕 {topic} 的题型训练展开，整理时应先按题型分组，再提炼每类题的起手判断、关键步骤和易错点。"
    if usage_kind == "example":
        return f"这一段以 {topic} 的代表例题为主，整理时应抓住题目触发信号、标准解法和可迁移的做题套路。"
    if usage_kind == "rule":
        return f"这一段主要梳理 {topic} 的关键规则与公式，整理时应把适用条件、结论形式和常见误用放在一起看。"
    if usage_kind == "concept":
        return f"这一段主要建立 {section_name} 的核心概念和基础口径，整理时应先把定义边界、关键关系和常见混淆点理顺。"
    if usage_kind == "summary":
        return f"这一段主要对 {topic} 做归纳总结，整理时应把前面分散的概念、规则和题型收束成可回看的主线。"
    return f"这一段主要围绕 {section_name} 做过渡整理，后续应先判断它更偏概念、方法还是题型。"


def build_generic_formula_hints(section_guess: str, rows: list[dict], chapter_title: str) -> list[str]:
    usage_kind = infer_usage_kind(section_guess, rows)
    topic = derive_topic(section_guess, chapter_title)
    hints: list[str] = []
    if usage_kind == "review":
        hints.append(f"先把 {topic} 相关题目按题型分开，再整理每类题反复调用的规则、判断条件和步骤骨架。")
    if usage_kind == "rule":
        hints.append(f"先确认 {topic} 的适用条件，再整理可直接复述的规则、公式和结论。")
    if usage_kind == "concept":
        hints.append(f"围绕 {topic} 同步整理定义口径、关键记号和适用边界。")
    if usage_kind in {"analysis", "example", "exercise"}:
        hints.append(f"围绕 {topic} 提炼解题时反复调用的核心规则、触发条件和步骤骨架。")
    if usage_kind == "summary":
        hints.append(f"把 {topic} 这一段能直接复述的主线、套路和结论收成简明清单。")
    return hints


def build_generic_example_hints(section_guess: str, rows: list[dict], chapter_title: str) -> list[str]:
    usage_kind = infer_usage_kind(section_guess, rows)
    topic = derive_topic(section_guess, chapter_title)
    if usage_kind == "review":
        return [f"{topic}题型复盘题"]
    if usage_kind == "analysis":
        return [f"{topic}解析步骤复盘题"]
    if usage_kind == "exercise":
        return [f"{topic}代表题型"]
    if usage_kind == "example":
        return [f"{topic}代表例题"]
    if usage_kind == "rule":
        return [f"{topic}规则辨析题"]
    if usage_kind == "concept":
        return [f"{topic}概念辨析题"]
    if usage_kind == "summary":
        return [f"{topic}归纳总结题"]
    return []


def build_generic_confusions(section_guess: str, rows: list[dict], chapter_title: str) -> list[str]:
    usage_kind = infer_usage_kind(section_guess, rows)
    topic = derive_topic(section_guess, chapter_title)
    if usage_kind == "review":
        return [f"{topic}这一段最容易出现“会看试题和解析，但不会自己总结题型主线”的问题。"]
    if usage_kind == "analysis":
        return [f"{topic}这一段最容易出现“会看解析，但不会自己复现步骤”的问题。"]
    if usage_kind == "exercise":
        return [f"{topic}这类题最容易出现“题目能看懂，但不会自己起手”的问题。"]
    if usage_kind == "example":
        return [f"{topic}代表例题最容易只记答案，不记触发条件和迁移方式。"]
    if usage_kind == "rule":
        return [f"{topic}的规则最容易在适用条件还没核对清楚时就直接套用。"]
    if usage_kind == "concept":
        return [f"{topic}最容易在定义边界和相邻概念上混淆。"]
    if usage_kind == "summary":
        return [f"{topic}回顾时最容易出现“每个点都见过，但主线串不起来”的问题。"]
    return [f"{clean_section_name(section_guess, chapter_title)} 这一段仍需人工复核具体小节边界和知识点主次。"]


def build_generic_question_prompts(section_guess: str, rows: list[dict], chapter_title: str) -> list[str]:
    usage_kind = infer_usage_kind(section_guess, rows)
    topic = derive_topic(section_guess, chapter_title)
    if usage_kind == "review":
        return [f"{topic}这一组题最值得先复盘的起手判断和方法切换点是什么？"]
    if usage_kind == "analysis":
        return [f"{topic}的解析里，最值得复盘的步骤切换点是什么？"]
    if usage_kind == "exercise":
        return [f"{topic}这类题最稳的起手判断是什么？"]
    if usage_kind == "example":
        return [f"{topic}代表例题最值得迁移的解题套路是什么？"]
    if usage_kind == "rule":
        return [f"{topic}的关键规则最容易在哪个条件上用错？"]
    if usage_kind == "summary":
        return [f"回顾 {topic} 时，最值得先串起来的是哪几条主线？"]
    return [f"{topic}最容易和哪个相邻概念混在一起？"]


def main() -> int:
    args = parse_args()
    context_path = Path(args.context_json)
    context = normalize_context(load_json(context_path))
    batch_dir = context_path.parent
    dirs = ensure_learning_dirs(batch_dir)
    manifest_rows = parse_manifest_table(batch_dir / MANIFEST_NAME)
    if not manifest_rows:
        raise SystemExit("[ERROR] no manifest rows found")
    if not context.get("chapter_id") or not context.get("source_id"):
        material_root = Path(context["material_path"])
        include_paths = []
        for row in manifest_rows:
            candidate = material_root / row["relative_path"]
            if candidate.exists():
                include_paths.append(candidate)
        source_payload = register_source_material(
            subject=context["subject"],
            source_name=context.get("source_name", ""),
            material_type=context.get("mode", "chapter-photo"),
            material_path=material_root,
            include_paths=include_paths or None,
        )
        context["source_id"] = source_payload["source_id"]
        chapter_payload = register_chapter_manifest(context, source_payload=source_payload)
        context["chapter_id"] = chapter_payload["chapter_id"]
        save_json(context_path, context)
    max_images_per_chunk = args.max_images_per_chunk or (4 if context['subject'] == '数学' else 6)
    chunk_rows = split_chunks(manifest_rows, max(1, max_images_per_chunk))
    tbd = "\\u5f85\\u8865\\u5145".encode("utf-8").decode("unicode_escape")
    profile = resolve_chapter_profile(context)
    chunks = []
    for idx, rows in enumerate(chunk_rows, 1):
        section_candidates = [row['section_hint'].strip() for row in rows if not is_placeholder(row['section_hint'])]
        section_guess = section_candidates[0] if section_candidates else tbd
        image_start = rows[0]['seq']
        image_end = rows[-1]['seq']
        chunk_rule = find_chunk_rule(profile, image_start, image_end)
        cleaned_section = clean_section_name(section_guess, context['chapter_title'])
        focus_hint = chunk_rule['focus_hint'] if chunk_rule else build_generic_focus(cleaned_section, rows, context['chapter_title'])
        formula_hints = chunk_rule.get('formula_hints', []) if chunk_rule else build_generic_formula_hints(cleaned_section, rows, context['chapter_title'])
        example_hints = chunk_rule.get('example_hints', []) if chunk_rule else build_generic_example_hints(cleaned_section, rows, context['chapter_title'])
        confusion_hints = chunk_rule.get('confusion_hints', []) if chunk_rule else build_generic_confusions(cleaned_section, rows, context['chapter_title'])
        concept_hints = chunk_rule.get('concept_hints', []) if chunk_rule else []
        question_prompt_hints = chunk_rule.get('question_prompt_hints', []) if chunk_rule else build_generic_question_prompts(cleaned_section, rows, context['chapter_title'])
        chunks.append({
            'chunk_id': f'chunk-{idx:03d}',
            'chunk_title': cleaned_section if cleaned_section != tbd else f'chunk {idx}',
            'chunk_index': idx,
            'image_start': image_start,
            'image_end': image_end,
            'page_start': parse_page_token(rows[0]['page_hint']),
            'page_end': parse_page_token(rows[-1]['page_hint']),
            'section_guess': chunk_rule.get('section', cleaned_section) if chunk_rule else cleaned_section,
            'needs_review': any(is_placeholder(row['page_hint']) or is_placeholder(row['usage_hint']) or is_placeholder(row['section_hint']) for row in rows),
            'image_refs': rows,
            'focus_hint': focus_hint,
            'concept_hints': concept_hints,
            'formula_hints': formula_hints,
            'example_hints': example_hints,
            'confusion_hints': confusion_hints,
            'question_prompt_hints': question_prompt_hints,
        })
    registered_chunks = register_chunk_manifests(context, chunks)
    by_logical_id = {item["logical_chunk_id"]: item for item in registered_chunks}
    for chunk in chunks:
        manifest = by_logical_id.get(chunk["chunk_id"], {})
        if manifest:
            chunk["chunk_kb_id"] = manifest["chunk_kb_id"]
    payload = {
        'batch_id': context['batch_id'],
        'chapter_title': context['chapter_title'],
        'chapter_id': context.get('chapter_id', ''),
        'source_id': context.get('source_id', ''),
        'page_number_position': context.get('page_number_position', 'unknown'),
        'chunk_count': len(chunks),
        'chunks': chunks,
    }
    save_json(dirs['chunk_plan'] / PLAN_JSON, payload)
    (dirs['chunk_plan'] / PLAN_MD).write_text(render_overview(context, chunks), encoding='utf-8')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
