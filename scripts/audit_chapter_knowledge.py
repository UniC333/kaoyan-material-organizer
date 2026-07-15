#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from common import ensure_learning_dirs, is_placeholder, load_json, normalize_context, save_json

STATUS_NAME = "00_章节状态总览.md"
AUDIT_JSON = "00_知识归纳状态.json"
CHAPTER_BODY = "01_章节整理正文.md"
QA_ENTRY = "03_知识点问答入口.md"
Q_INDEX = "00_本章后续追问索引.md"
INDEX_JSON = "chapter_knowledge_index.json"
WEAK_TOKENS = ("待补充", "待判定", "待整理", "待确认", "待细化", "至少保留", "优先保留", "优先提炼", "如果本段以例题为主", "先明确本段主线")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--context-json", required=True)
    return parser.parse_args()


def filled_text(value: Any) -> bool:
    text = str(value or "").strip()
    if not text or is_placeholder(value):
        return False
    return not any(token in text for token in WEAK_TOKENS)


def nonempty_text(value: Any) -> bool:
    return bool(str(value or "").strip()) and not is_placeholder(value)


def filled_named_items(items: list[dict], text_key: str) -> bool:
    if not items:
        return False
    for item in items:
        if filled_text(item.get("name")) and filled_text(item.get(text_key)):
            return True
    return False


def filled_concepts(items: list[dict]) -> bool:
    if not items:
        return False
    for item in items:
        if filled_text(item.get("name")) and filled_text(item.get("summary")) and filled_text(item.get("key_rule")):
            return True
    return False


def missing_fields(chunk: dict) -> list[str]:
    missing: list[str] = []
    section_text = str(chunk.get('section', ''))
    needs_examples = any(keyword in section_text for keyword in ("例题", "题型", "解析", "练习", "试题", "习题", "综合应用"))
    if not filled_text(chunk.get("focus_summary")):
        missing.append("主旨")
    if not filled_concepts(chunk.get("core_concepts", [])):
        missing.append("核心概念")
    if not filled_named_items(chunk.get("rules_or_formulas", []), "content"):
        missing.append("规则/公式")
    if needs_examples and not filled_named_items(chunk.get("example_types", []), "pattern"):
        missing.append("例题/题型")
    if not any(filled_text(item) for item in chunk.get("confusions", [])):
        missing.append("易混点")
    learning_status = chunk.get("learning_status", {})
    if not filled_text(learning_status.get("can_review")) or not filled_text(learning_status.get("can_write")):
        missing.append("学习状态")
    return missing


def chunk_is_specific(chunk: dict) -> bool:
    focus = str(chunk.get("focus_summary", "")).strip()
    if any(token in focus for token in WEAK_TOKENS):
        return False
    if "这一段主要围绕" in focus and "过渡整理" in focus:
        return False
    examples = chunk.get("example_types", [])
    for item in examples:
        if not filled_text(item.get("name")) or not filled_text(item.get("pattern")):
            return False
    rules = chunk.get("rules_or_formulas", [])
    for item in rules:
        if not filled_text(item.get("name")) or not filled_text(item.get("content")):
            return False
    return True


def specific_chunk_count(chunks: list[dict]) -> int:
    return sum(1 for chunk in chunks if chunk_is_specific(chunk))


def build_chunk_audit(chunks: list[dict]) -> list[dict]:
    payload: list[dict] = []
    for chunk in chunks:
        missing = missing_fields(chunk)
        payload.append(
            {
                "chunk_id": chunk["chunk_id"],
                "section": chunk.get("section", "待补充"),
                "page_start": chunk.get("source_refs", {}).get("page_start", "待补充"),
                "page_end": chunk.get("source_refs", {}).get("page_end", "待补充"),
                "ready": not missing,
                "missing_fields": missing,
            }
        )
    return payload


def derive_knowledge_status(quality_level: str) -> str:
    if quality_level == "高质量成品":
        return "已形成可持续提问的章节成品"
    if quality_level in {"学习成品", "可提问"}:
        return "已进入可提问状态，仍可继续补强"
    if quality_level == "已建结构":
        return "已建结构，待继续补录"
    return "待开始知识归纳"


def derive_quality_level(
    chunk_audit: list[dict],
    chunks: list[dict],
    chapter_body_ready: bool,
    index_ready: bool,
    card_count: int,
    question_count: int,
    overview_ready: bool,
    learning_path_count: int,
    priority_concept_count: int,
) -> str:
    ready_chunks = sum(1 for item in chunk_audit if item["ready"])
    total_chunks = len(chunk_audit)
    fully_ready = total_chunks > 0 and ready_chunks == total_chunks
    specific_ready_chunks = specific_chunk_count(chunks)
    learning_path_target = 3 if total_chunks >= 3 else max(2, total_chunks)
    priority_target = 4 if total_chunks >= 4 else max(2, total_chunks)
    card_target = max(2, min(5, total_chunks))
    question_target = max(2, min(5, total_chunks + 1))
    if (
        fully_ready
        and specific_ready_chunks == total_chunks
        and chapter_body_ready
        and index_ready
        and overview_ready
        and learning_path_count >= learning_path_target
        and priority_concept_count >= priority_target
        and card_count >= card_target
        and question_count >= question_target
    ):
        return "高质量成品"
    if fully_ready and specific_ready_chunks >= max(1, total_chunks - 1) and chapter_body_ready and index_ready and card_count >= max(2, total_chunks) and question_count >= max(2, min(4, total_chunks)):
        return "学习成品"
    if ready_chunks > 0 and chapter_body_ready and index_ready:
        return "可提问"
    if total_chunks > 0:
        return "已建结构"
    return "待开始"


def derive_next_step(
    chunk_audit: list[dict],
    chunks: list[dict],
    chapter_body_ready: bool,
    index_ready: bool,
    card_count: int,
    question_count: int,
    overview_ready: bool,
    learning_path_count: int,
    priority_concept_count: int,
    quality_level: str,
) -> str:
    incomplete = [item for item in chunk_audit if not item["ready"]]
    if incomplete:
        first = incomplete[0]
        return f"优先补 {first['chunk_id']}（{first['section']}），当前缺：{'、'.join(first['missing_fields'])}。"
    weak_chunks = [chunk for chunk in chunks if not chunk_is_specific(chunk)]
    if weak_chunks:
        first = weak_chunks[0]
        return f"优先把 {first['chunk_id']}（{first.get('section', '待补充')}）从占位表达补成可学习表述，再继续冲高质量成品。"
    if not chapter_body_ready:
        return "先生成或补完整章正文，再检查各片段衔接。"
    if not index_ready:
        return "先刷新章节索引与问答入口，再进入后续提问和回补。"
    if card_count == 0:
        return "先补出至少一组真实知识点卡片，保证后续回看有稳定落点。"
    if question_count == 0:
        return "先补少量高价值追问，保证后续提问入口不是空壳。"
    if not overview_ready:
        return "先补章节总述，保证“这章主要讲了什么”能直接回答。"
    if learning_path_count < 3:
        return "先补建议学习顺序，保证系统能回答“下一步怎么学”。"
    if priority_concept_count < 4:
        return "先补优先知识点，保证章节主线和关键抓手更集中。"
    if quality_level == "学习成品":
        return "当前已可学习，下一步优先补强高价值题型、代表例题和跨片段串联。"
    if quality_level == "高质量成品":
        return "当前可直接回看正文、卡片和问答入口，后续优先补高价值题型和易混点。"
    return "当前可直接回看正文、卡片和问答入口，后续优先补高价值题型和易混点。"


def render_status(context: dict, audit: dict) -> str:
    incomplete = [item for item in audit["chunk_audit"] if not item["ready"]]
    lines = [
        "# 章节状态总览",
        "",
        f"- 批次编号：{context['batch_id']}",
        f"- 学科：{context['subject']}",
        f"- 材料来源：{context['source_name']}",
        f"- 处理范围：{context['scope']}",
        f"- 当前状态：{context.get('status_label', '待补充')}",
        f"- 知识归纳状态：{audit['knowledge_status']}",
        f"- 质量层级：{audit['quality_level']}",
        f"- 图片总数：{context.get('image_count', 0)}",
        f"- 页码映射：{context.get('page_number_source_label', '待补充')}",
        f"- 分片总数：{audit['chunk_total']}",
        f"- 已完成片段：{audit['ready_chunk_count']}",
        f"- 待补片段：{audit['pending_chunk_count']}",
        f"- 知识点卡片：{audit['card_count']}",
        f"- 可直接追问：{audit['question_prompt_count']}",
        f"- 章节总述：{'已完成' if audit['overview_ready'] else '待补'}",
        f"- 学习顺序：{audit['learning_path_count']} 条",
        f"- 优先知识点：{audit['priority_concept_count']} 个",
        f"- 下一步：{audit['next_step']}",
    ]
    if context.get("input_path_warning"):
        lines.append(f"- 输入目录提醒：{context['input_path_warning']}")

    lines.extend(["", "## 建议先看", ""])
    lines.append(f"- [01_章节整理正文.md](./20_章节整理/{CHAPTER_BODY})")
    lines.append(f"- [03_知识点问答入口.md](./50_提问索引/{QA_ENTRY})")
    lines.append(f"- [00_本章后续追问索引.md](./50_提问索引/{Q_INDEX})")

    lines.extend(["", "## 片段完成度", ""])
    if incomplete:
        for item in incomplete[:8]:
            lines.append(
                f"- {item['chunk_id']}：{item['section']}（{item['page_start']} - {item['page_end']}），待补 {'、'.join(item['missing_fields'])}"
            )
    else:
        lines.append("- 当前所有 chunk 已达到可学习的最小标准。")

    return "\n".join(lines) + "\n"


def main() -> int:
    args = parse_args()
    context_path = Path(args.context_json)
    context = normalize_context(load_json(context_path))
    batch_dir = context_path.parent
    dirs = ensure_learning_dirs(batch_dir)

    chunk_files = sorted(dirs["chunk_extracts"].glob("chunk-*.json"))
    chunks = [load_json(path) for path in chunk_files]
    chunk_audit = build_chunk_audit(chunks)

    chapter_body_path = dirs["chapter_notes"] / CHAPTER_BODY
    chapter_body_ready = chapter_body_path.exists() and chapter_body_path.stat().st_size > 0
    index_json_path = dirs["question_index"] / INDEX_JSON
    index_ready = index_json_path.exists()
    question_count = 0
    overview_ready = False
    learning_path_count = 0
    priority_concept_count = 0
    if index_ready:
        index_payload = load_json(index_json_path)
        question_count = len(index_payload.get("question_prompts", []))
        overview_ready = filled_text(index_payload.get("chapter_overview", ""))
        learning_path_count = len(index_payload.get("learning_path", []))
        priority_concept_count = len(index_payload.get("priority_concepts", []))
    card_count = len(list(dirs["cards"].glob("*.md")))

    ready_chunk_count = sum(1 for item in chunk_audit if item["ready"])
    pending_chunk_count = len(chunk_audit) - ready_chunk_count
    quality_level = derive_quality_level(
        chunk_audit,
        chunks,
        chapter_body_ready,
        index_ready,
        card_count,
        question_count,
        overview_ready,
        learning_path_count,
        priority_concept_count,
    )
    knowledge_status = derive_knowledge_status(quality_level)
    next_step = derive_next_step(
        chunk_audit,
        chunks,
        chapter_body_ready,
        index_ready,
        card_count,
        question_count,
        overview_ready,
        learning_path_count,
        priority_concept_count,
        quality_level,
    )

    audit_payload = {
        "batch_id": context["batch_id"],
        "chapter_title": context["chapter_title"],
        "knowledge_status": knowledge_status,
        "quality_level": quality_level,
        "chunk_total": len(chunk_audit),
        "ready_chunk_count": ready_chunk_count,
        "pending_chunk_count": pending_chunk_count,
        "chapter_body_ready": chapter_body_ready,
        "chapter_index_ready": index_ready,
        "card_count": card_count,
        "question_prompt_count": question_count,
        "overview_ready": overview_ready,
        "learning_path_count": learning_path_count,
        "priority_concept_count": priority_concept_count,
        "next_step": next_step,
        "chunk_audit": chunk_audit,
    }

    save_json(batch_dir / AUDIT_JSON, audit_payload)
    (batch_dir / STATUS_NAME).write_text(render_status(context, audit_payload), encoding="utf-8")

    context["knowledge_status"] = knowledge_status
    context["quality_level"] = quality_level
    context["knowledge_ready"] = ready_chunk_count == len(chunk_audit) and chapter_body_ready and index_ready and card_count > 0 and question_count > 0
    context["knowledge_audit_path"] = str(batch_dir / AUDIT_JSON)
    context["next_step"] = next_step
    save_json(context_path, context)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
