#!/usr/bin/env python3
from __future__ import annotations

import argparse
from collections import defaultdict
from pathlib import Path

from common import ensure_learning_dirs, is_placeholder, load_json, markdown_list, normalize_context, save_json

INDEX_JSON = "chapter_knowledge_index.json"
CARD_INDEX_MD = "00_知识点卡片索引.md"
MANAGED_MARKER = "<!-- managed-by: rebuild_learning_cards.py -->"
MANUAL_PRESERVE_MARKER = "<!-- manual-preserve -->"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--context-json", required=True)
    parser.add_argument("--clean-generated", action="store_true")
    return parser.parse_args()


def chunk_lookup(chunk_dir: Path) -> dict[str, dict]:
    lookup: dict[str, dict] = {}
    for path in sorted(chunk_dir.glob("chunk-*.json")):
        payload = load_json(path)
        chunk_id = str(payload.get("chunk_id", "")).strip()
        if chunk_id:
            lookup[chunk_id] = payload
    return lookup


def clean_text(value: str) -> str:
    return str(value or "").strip()


def concept_sort_key(concept: dict, priority_names: set[str]) -> tuple[int, int, str, str]:
    page_start = concept.get("page_start")
    try:
        page_num = int(str(page_start).replace("第", "").replace("页", ""))
    except ValueError:
        page_num = 10**9
    name = clean_text(concept.get("name", ""))
    section = clean_text(concept.get("section", ""))
    priority_rank = 0 if name in priority_names else 1
    return (priority_rank, page_num, section, name)


def dedupe_text(items: list[str]) -> list[str]:
    seen: list[str] = []
    for item in items:
        text = clean_text(item)
        if text and text not in seen and not is_placeholder(text):
            seen.append(text)
    return seen


def example_names(chunk: dict) -> list[str]:
    names: list[str] = []
    for item in chunk.get("example_types", []):
        if isinstance(item, dict):
            name = clean_text(item.get("name") or item.get("pattern") or "")
        else:
            name = clean_text(item)
        if name and name not in names:
            names.append(name)
    return names


def build_card_payloads(context: dict, chapter_index: dict, chunk_map: dict[str, dict]) -> list[dict]:
    priority_names = set(chapter_index.get("priority_concepts", []))
    concept_entries = list(chapter_index.get("concept_index", []))
    concept_entries.sort(key=lambda item: concept_sort_key(item, priority_names))

    payloads: list[dict] = []
    for concept in concept_entries:
        name = clean_text(concept.get("name", ""))
        if not name or is_placeholder(name):
            continue
        chunk = chunk_map.get(clean_text(concept.get("chunk_id", "")), {})
        focus_summary = clean_text(chunk.get("focus_summary", "")) or clean_text(concept.get("summary", ""))
        confusions = dedupe_text(concept.get("confusions", []))
        followups = dedupe_text(concept.get("followup_questions", []))
        examples = example_names(chunk)
        payloads.append(
            {
                "name": name,
                "chapter_title": clean_text(context.get("chapter_title", "")),
                "subject": clean_text(context.get("subject", "")),
                "section": clean_text(concept.get("section", "")),
                "summary": clean_text(concept.get("summary", "")),
                "key_rule": clean_text(concept.get("key_rule", "")),
                "focus_summary": focus_summary,
                "page_start": clean_text(concept.get("page_start", "")) or clean_text(chunk.get("source_refs", {}).get("page_start", "")),
                "page_end": clean_text(concept.get("page_end", "")) or clean_text(chunk.get("source_refs", {}).get("page_end", "")),
                "chunk_id": clean_text(concept.get("chunk_id", "")),
                "batch_id": clean_text(context.get("batch_id", "")),
                "confusions": confusions,
                "followups": followups,
                "examples": examples,
                "is_priority": name in priority_names,
            }
        )
    return payloads


def render_card(payload: dict) -> str:
    lines = [
        MANAGED_MARKER,
        f"# {payload['name']}",
        "",
        f"- 所属学科: {payload['subject']}",
        f"- 所属章节: {payload['chapter_title']}",
        f"- 所属小节: {payload['section']}",
        f"- 一句话定义: {payload['summary'] or '待补充'}",
        f"- 关键规则/公式: {payload['key_rule'] or '待补充'}",
    ]
    if payload["is_priority"]:
        lines.append("- 当前状态: 优先回看")
    lines.extend(
        [
            "",
            "## 这张卡先看什么",
            "",
            payload["focus_summary"] or "待补充",
            "",
            "## 易混点",
            "",
            markdown_list(payload["confusions"], empty_text="待补充"),
        ]
    )
    if payload["examples"]:
        lines.extend(["", "## 例题或题型入口", ""])
        lines.extend(f"- {item}" for item in payload["examples"][:4])
    if payload["followups"]:
        lines.extend(["", "## 后续可问", ""])
        lines.extend(f"- {item}" for item in payload["followups"][:4])
    lines.extend(
        [
            "",
            "## 来源回链",
            "",
            f"- 批次编号: {payload['batch_id']}",
            f"- 片段编号: {payload['chunk_id']}",
            f"- 页段: {payload['page_start']} - {payload['page_end']}",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def render_card_index(card_payloads: list[dict]) -> str:
    lines = [
        "# 知识点卡片索引",
        "",
        "- 用途：优先从这里回看本章高信号卡片，而不是翻完整批次目录。",
        "",
    ]
    priority_cards = [item for item in card_payloads if item["is_priority"]]
    if priority_cards:
        lines.extend(["## 优先回看", ""])
        for item in priority_cards:
            lines.append(f"- [[40_知识点卡片/{item['name']}]]")
        lines.append("")

    grouped: dict[str, list[dict]] = defaultdict(list)
    for item in card_payloads:
        grouped[item["section"]].append(item)
    for section in grouped:
        lines.extend([f"## {section}", ""])
        for item in grouped[section]:
            suffix = "（优先）" if item["is_priority"] else ""
            lines.append(f"- [[40_知识点卡片/{item['name']}]]{suffix}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def is_generated_card(path: Path) -> bool:
    text = path.read_text(encoding="utf-8")
    if MANUAL_PRESERVE_MARKER in text:
        return False
    return MANAGED_MARKER in text or ("## 来源回链" in text and "- 一句话定义:" in text)


def clean_generated_cards(cards_dir: Path, desired_files: set[str]) -> None:
    for path in sorted(cards_dir.glob("*.md")):
        if path.name == CARD_INDEX_MD or path.name in desired_files:
            continue
        if is_generated_card(path):
            path.unlink()


def update_chapter_index(index_path: Path, chapter_index: dict, card_payloads: list[dict]) -> None:
    chapter_index["card_files"] = [f"{item['name']}.md" for item in card_payloads]
    chapter_index["priority_card_files"] = [f"{item['name']}.md" for item in card_payloads if item["is_priority"]]
    save_json(index_path, chapter_index)


def main() -> int:
    args = parse_args()
    context_path = Path(args.context_json)
    context = normalize_context(load_json(context_path))
    dirs = ensure_learning_dirs(context_path.parent)
    index_path = dirs["question_index"] / INDEX_JSON
    if not index_path.exists():
        raise SystemExit(f"[ERROR] missing chapter knowledge index: {index_path}")

    chapter_index = load_json(index_path)
    chunk_map = chunk_lookup(dirs["chunk_extracts"])
    card_payloads = build_card_payloads(context, chapter_index, chunk_map)

    desired_files: set[str] = set()
    for payload in card_payloads:
        card_path = dirs["cards"] / f"{payload['name']}.md"
        card_path.write_text(render_card(payload), encoding="utf-8")
        desired_files.add(card_path.name)

    (dirs["cards"] / CARD_INDEX_MD).write_text(render_card_index(card_payloads), encoding="utf-8")
    update_chapter_index(index_path, chapter_index, card_payloads)

    if args.clean_generated:
        clean_generated_cards(dirs["cards"], desired_files)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
