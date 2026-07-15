#!/usr/bin/env python3
from __future__ import annotations

import argparse
from collections import defaultdict
from pathlib import Path

from common import INDEX_DIRNAME, default_vault_root_arg, save_json, save_text

QA_ROOT_NAME = "10_问答沉淀"
REGISTRY_JSON = "saved_qa_registry.json"
REGISTRY_MD = "13_问答沉淀索引.md"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--vault-root", default=default_vault_root_arg())
    return parser.parse_args()


def wiki_link_for(path: str | Path, vault_root: Path) -> str:
    relative = Path(path).relative_to(vault_root).with_suffix("")
    return f"[[{relative.as_posix()}]]"


def iter_saved_notes(vault_root: Path) -> list[Path]:
    paths: list[Path] = []
    for path in sorted(vault_root.rglob("*.md")):
        if QA_ROOT_NAME not in path.parts:
            continue
        if path.name.startswith("00_"):
            continue
        paths.append(path)
    return paths


def parse_metadata(lines: list[str]) -> dict[str, str]:
    mapping = {
        "- 学科：": "subject",
        "- 章节：": "chapter_title",
        "- 记录日期：": "saved_at",
        "- 章节正文：": "chapter_body",
        "- 章内问答入口：": "question_entry",
        "- 印刷页：": "printed_page",
        "- 证据 ID：": "evidence_id",
        "- 图片范围：": "image_span",
        "- 分片 ID：": "chunk_id",
    }
    payload: dict[str, str] = {}
    for line in lines:
        text = line.strip()
        for prefix, key in mapping.items():
            if text.startswith(prefix):
                payload[key] = text[len(prefix) :].strip()
    return payload


def collect_section(lines: list[str], heading: str) -> list[str]:
    collected: list[str] = []
    in_section = False
    for line in lines:
        text = line.rstrip()
        if text.startswith("## "):
            if text.strip() == heading:
                in_section = True
                continue
            if in_section:
                break
        if in_section and text.strip():
            collected.append(text)
    return collected


def clean_bullet(text: str) -> str:
    value = text.strip()
    return value[2:].strip() if value.startswith("- ") else value


def parse_note(path: Path, vault_root: Path) -> dict:
    lines = path.read_text(encoding="utf-8").splitlines()
    title = ""
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("# "):
            title = stripped[2:].strip()
            break
    meta = parse_metadata(lines)
    answer_lines = collect_section(lines, "## 当前回答")
    weak_spots = [clean_bullet(line) for line in collect_section(lines, "## 易混点") if clean_bullet(line)]
    next_questions = [clean_bullet(line) for line in collect_section(lines, "## 下一步可继续问") if clean_bullet(line)]
    answer_summary = " ".join(clean_bullet(line) for line in answer_lines).strip()
    return {
        "subject": meta.get("subject", ""),
        "chapter_title": meta.get("chapter_title", ""),
        "saved_at": meta.get("saved_at", ""),
        "question": title,
        "answer_summary": answer_summary[:240],
        "weak_spots": weak_spots,
        "next_questions": next_questions,
        "note_path": str(path),
        "note_link": wiki_link_for(path, vault_root),
        "chapter_body": meta.get("chapter_body", ""),
        "question_entry": meta.get("question_entry", ""),
        "page_anchor": {
            "printed_page": int(meta["printed_page"]) if meta.get("printed_page", "").isdigit() else None,
            "evidence_id": meta.get("evidence_id", ""),
            "image_span": meta.get("image_span", ""),
            "chunk_id": meta.get("chunk_id", ""),
        },
    }


def build_chapter_summaries(notes: list[dict]) -> list[dict]:
    grouped: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for note in notes:
        grouped[(note["subject"], note["chapter_title"])].append(note)

    summaries: list[dict] = []
    for (subject, chapter_title), items in grouped.items():
        ordered = sorted(items, key=lambda item: (item.get("saved_at", ""), item.get("question", "")), reverse=True)
        weak_spots: list[str] = []
        next_questions: list[str] = []
        for item in ordered:
            for weak in item.get("weak_spots", []):
                if weak and weak not in weak_spots:
                    weak_spots.append(weak)
            for question in item.get("next_questions", []):
                if question and question not in next_questions:
                    next_questions.append(question)
        summaries.append(
            {
                "subject": subject,
                "chapter_title": chapter_title,
                "saved_qa_count": len(items),
                "recent_questions": [item["question"] for item in ordered[:5]],
                "recent_note_links": [item["note_link"] for item in ordered[:5]],
                "recent_answer_summaries": [item["answer_summary"] for item in ordered[:3] if item["answer_summary"]],
                "saved_weak_spots": weak_spots[:8],
                "saved_next_questions": next_questions[:8],
            }
        )
    return sorted(summaries, key=lambda item: (-item["saved_qa_count"], item["subject"], item["chapter_title"]))


def render_registry(notes: list[dict], chapters: list[dict]) -> str:
    lines = [
        "# 问答沉淀索引",
        "",
        f"- 已沉淀问答数：{len(notes)}",
        f"- 已涉及章节数：{len(chapters)}",
        "",
        "| 学科 | 章节 | 沉淀数 | 最近已问 | 入口 |",
        "| --- | --- | --- | --- | --- |",
    ]
    for chapter in chapters:
        latest = chapter["recent_questions"][0] if chapter.get("recent_questions") else "待补充"
        entry = chapter["recent_note_links"][0] if chapter.get("recent_note_links") else "待补充"
        lines.append(f"| {chapter['subject']} | {chapter['chapter_title']} | {chapter['saved_qa_count']} | {latest} | {entry} |")
    for chapter in chapters:
        lines.extend(["", f"## {chapter['subject']} - {chapter['chapter_title']}", "", f"- 已沉淀问答：{chapter['saved_qa_count']}", "", "### 最近已问过的问题", ""])
        if chapter["recent_note_links"]:
            for link in chapter["recent_note_links"]:
                lines.append(f"- {link}")
        else:
            lines.append("- 暂无")
        if chapter["saved_weak_spots"]:
            lines.extend(["", "### 问答里反复暴露的易混点", ""])
            for item in chapter["saved_weak_spots"]:
                lines.append(f"- {item}")
        if chapter["saved_next_questions"]:
            lines.extend(["", "### 问答里留下的后续追问", ""])
            for item in chapter["saved_next_questions"]:
                lines.append(f"- {item}")
    return "\n".join(lines).rstrip() + "\n"


def main() -> int:
    args = parse_args()
    vault_root = Path(args.vault_root)
    notes = [parse_note(path, vault_root) for path in iter_saved_notes(vault_root)]
    chapters = build_chapter_summaries(notes)
    payload = {"notes": notes, "chapters": chapters}
    index_root = vault_root / INDEX_DIRNAME
    index_root.mkdir(parents=True, exist_ok=True)
    save_json(index_root / REGISTRY_JSON, payload)
    save_text(index_root / REGISTRY_MD, render_registry(notes, chapters))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
