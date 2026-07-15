#!/usr/bin/env python3
from __future__ import annotations

import argparse
from collections import defaultdict
from pathlib import Path

from common import default_vault_root_arg, load_json, resolve_subject, sanitize_name

INDEX_DIRNAME = "99_索引与状态"
REGISTRY_JSON = "chapter_knowledge_registry.json"
GLOBAL_CONCEPT_JSON = "global_concept_registry.json"
CHAPTER_BRIDGE_JSON = "chapter_bridge_registry.json"
COURSE_DIRNAME = "00_课程入口"
TEXTBOOK_INDEX_DIRNAME = "20_教材章节总览"
SUBJECT_ENTRY_TEMPLATE = "00_{subject}入口.md"
SUBJECT_INDEX_NAME = "00_教材章节总入口.md"
QA_NOTE_ROOT = "10_问答沉淀"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--vault-root", default=default_vault_root_arg())
    return parser.parse_args()


def wiki_link_for(path: str | Path, vault_root: Path) -> str:
    candidate = Path(path)
    relative = candidate.relative_to(vault_root).with_suffix("")
    return f"[[{relative.as_posix()}]]"


def ensure_line(path: Path, marker: str, line: str) -> None:
    if not path.exists():
        return
    content = path.read_text(encoding="utf-8")
    if marker in content:
        return
    if not content.endswith("\n"):
        content += "\n"
    content += line + "\n"
    path.write_text(content, encoding="utf-8")


def context_payload_for(chapter_dir: str | Path) -> dict:
    context_path = Path(chapter_dir) / "00_批次上下文.json"
    if not context_path.exists():
        return {}
    return load_json(context_path)


def collect_unique_texts(values: list[str], limit: int) -> list[str]:
    unique: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if not text or text in unique:
            continue
        unique.append(text)
        if len(unique) >= limit:
            break
    return unique


def build_subject_groups(chapters: list[dict]) -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = defaultdict(list)
    for chapter in chapters:
        grouped[str(chapter.get("subject", "")).strip()].append(chapter)
    return grouped


def build_source_groups(chapters: list[dict]) -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = defaultdict(list)
    for chapter in chapters:
        context = context_payload_for(chapter.get("chapter_dir", ""))
        source_name = str(context.get("source_name") or chapter.get("subject") or "未归类教材").strip()
        chapter["source_name"] = source_name
        chapter["context_payload"] = context
        grouped[source_name].append(chapter)
    return grouped


def sort_chapters(chapters: list[dict]) -> list[dict]:
    return sorted(
        chapters,
        key=lambda item: (
            0 if item.get("quality_level") == "高质量成品" else 1,
            0 if item.get("quality_level") == "学习成品" else 1,
            str(item.get("chapter_title", "")),
        ),
    )


def summarize_quality(chapters: list[dict]) -> tuple[int, int, int]:
    total = len(chapters)
    high_quality = sum(1 for item in chapters if item.get("quality_level") == "高质量成品")
    ready = sum(1 for item in chapters if item.get("quality_level") in {"高质量成品", "学习成品"})
    return total, high_quality, ready


def next_focus_text(chapters: list[dict]) -> str:
    for chapter in chapters:
        if chapter.get("quality_level") != "高质量成品":
            return f"优先继续补 {chapter.get('chapter_title', '未命名章节')}：{chapter.get('knowledge_status') or '待补充'}"
    if chapters:
        return "当前各章已形成可学习入口，下一步优先做跨章节串联和高价值追问沉淀。"
    return "当前还没有可用章节。"


def chapter_overview_text(chapter: dict) -> str:
    overview = str(chapter.get("chapter_overview", "")).strip()
    if overview:
        return overview
    sections = [str(item.get("focus_summary", "")).strip() for item in chapter.get("major_sections", []) if str(item.get("focus_summary", "")).strip()]
    return "；".join(sections[:3])


def repeated_concepts_for_source(source_chapters: list[dict], global_concepts: list[dict], subject: str) -> list[dict]:
    chapter_titles = {str(item.get("chapter_title", "")).strip() for item in source_chapters}
    results = []
    for concept in global_concepts:
        refs = [
            ref
            for ref in concept.get("references", [])
            if str(ref.get("subject", "")).strip() == subject and str(ref.get("chapter_title", "")).strip() in chapter_titles
        ]
        chapter_count = len({str(ref.get("chapter_title", "")).strip() for ref in refs})
        if chapter_count >= 2:
            results.append(
                {
                    "concept_name": concept.get("concept_name", ""),
                    "aliases": concept.get("aliases", []),
                    "followup_questions": concept.get("followup_questions", []),
                    "chapter_count": chapter_count,
                    "references": refs,
                }
            )
    return sorted(results, key=lambda item: (-item["chapter_count"], item["concept_name"]))


def chapter_bridges_for_source(source_chapters: list[dict], chapter_bridges: list[dict], subject: str, source_name: str) -> list[dict]:
    chapter_titles = {str(item.get("chapter_title", "")).strip() for item in source_chapters}
    results = []
    for bridge in chapter_bridges:
        if str(bridge.get("subject", "")).strip() != subject:
            continue
        if str(bridge.get("source_name", "")).strip() != source_name:
            continue
        if str(bridge.get("from_chapter", "")).strip() not in chapter_titles:
            continue
        if str(bridge.get("to_chapter", "")).strip() not in chapter_titles:
            continue
        results.append(bridge)
    return results


def source_followup_questions(chapters: list[dict], repeated_concepts: list[dict]) -> list[str]:
    prompts: list[str] = []
    for concept in repeated_concepts:
        prompts.extend(concept.get("followup_questions", []))
    for chapter in sort_chapters(chapters):
        prompts.extend(chapter.get("saved_next_questions", []))
        prompts.extend(chapter.get("learning_path", []))
        prompts.extend(chapter.get("question_prompts", []))
    return collect_unique_texts(prompts, 10)


def bridge_questions(bridges: list[dict]) -> list[str]:
    prompts: list[str] = []
    for bridge in bridges:
        prompts.extend(bridge.get("transition_questions", []))
    return collect_unique_texts(prompts, 8)


def source_weak_spots(chapters: list[dict]) -> list[str]:
    items: list[str] = []
    for chapter in sort_chapters(chapters):
        items.extend(chapter.get("weak_spots", []))
    return collect_unique_texts(items, 8)


def qa_notes_for_source(subject_root: Path, chapters: list[dict]) -> list[Path]:
    qa_root = subject_root / COURSE_DIRNAME / QA_NOTE_ROOT
    note_paths: list[Path] = []
    for chapter in chapters:
        chapter_dir = qa_root / sanitize_name(str(chapter.get("chapter_title", "")))
        if not chapter_dir.exists():
            continue
        for path in sorted(chapter_dir.glob("*.md"), reverse=True):
            if path.name.startswith("00_"):
                continue
            note_paths.append(path)
    unique: list[Path] = []
    seen: set[str] = set()
    for path in note_paths:
        key = str(path).lower()
        if key in seen:
            continue
        seen.add(key)
        unique.append(path)
        if len(unique) >= 6:
            break
    return unique


def recent_saved_questions_for_source(chapters: list[dict]) -> list[str]:
    prompts: list[str] = []
    for chapter in sort_chapters(chapters):
        prompts.extend(chapter.get("recent_saved_questions", []))
    return collect_unique_texts(prompts, 8)


def render_source_index(
    subject: str,
    source_name: str,
    chapters: list[dict],
    repeated_concepts: list[dict],
    followup_questions: list[str],
    weak_spots: list[str],
    qa_notes: list[Path],
    bridges: list[dict],
    saved_questions: list[str],
    vault_root: Path,
) -> str:
    ordered = sort_chapters(chapters)
    total, high_quality, ready = summarize_quality(ordered)
    lines = [
        f"# {source_name}章节总览",
        "",
        f"- 学科：{subject}",
        f"- 已接入章节数：{total}",
        f"- 高质量成品：{high_quality}",
        f"- 已形成可学入口：{ready}",
        f"- 当前下一步：{next_focus_text(ordered)}",
        "",
        "| 章节 | 质量层级 | 图片数 | 优先知识点 | 正文 | 提问入口 |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for chapter in ordered:
        context = chapter.get("context_payload", {})
        image_count = context.get("image_count", 0)
        priority_count = len(chapter.get("priority_concepts", []))
        chapter_body = wiki_link_for(chapter["chapter_body"], vault_root)
        question_entry = wiki_link_for(chapter["question_entry"], vault_root)
        lines.append(
            f"| {chapter.get('chapter_title', '未命名章节')} | {chapter.get('quality_level') or '待评估'} | {image_count} | {priority_count} | {chapter_body} | {question_entry} |"
        )

    lines.extend(["", "## 推荐起点", ""])
    for chapter in ordered[: min(3, len(ordered))]:
        lines.append(
            f"- {chapter.get('chapter_title', '未命名章节')}：{chapter_overview_text(chapter) or chapter.get('knowledge_status') or '待补充'}"
        )

    if repeated_concepts:
        lines.extend(["", "## 可跨章节串联的概念", ""])
        for concept in repeated_concepts[:6]:
            lines.append(f"- {concept['concept_name']}：已涉及 {concept['chapter_count']} 章")
            for question in concept.get("followup_questions", [])[:2]:
                lines.append(f"  可继续问：{question}")
    else:
        lines.extend(["", "## 可跨章节串联的概念", "", "- 当前这套教材样本里还没有稳定沉淀出跨两章以上的重复主题，后续继续按章节推进后会自动补上。"])

    lines.extend(["", "## 按章节推进建议", ""])
    if bridges:
        for bridge in bridges[:6]:
            lines.append(f"- {bridge.get('bridge_title', '')}")
            for item in bridge.get("carry_over_points", [])[:2]:
                lines.append(f"  带着它去看：{item}")
    else:
        lines.append("- 当前这套教材还没有形成可用的章节递进链。")

    lines.extend(["", "## 可直接继续追问", ""])
    if followup_questions:
        for question in followup_questions:
            lines.append(f"- {question}")
    else:
        lines.append("- 当前还没有汇总出足够稳定的教材级追问。")

    lines.extend(["", "## 高频易混点", ""])
    if weak_spots:
        for item in weak_spots:
            lines.append(f"- {item}")
    else:
        lines.append("- 当前还没有汇总出足够稳定的教材级易混点。")

    lines.extend(["", "## 最近问答沉淀", ""])
    if qa_notes:
        for note in qa_notes:
            lines.append(f"- {wiki_link_for(note, vault_root)}")
    else:
        lines.append("- 当前还没有与这套教材对应的问答沉淀。")

    lines.extend(["", "## 问过以后还值得继续追的点", ""])
    if saved_questions:
        for item in saved_questions:
            lines.append(f"- {item}")
    else:
        lines.append("- 当前还没有因问答沉淀而留下的后续追问。")

    lines.extend(["", "## 逐章入口", ""])
    for chapter in ordered:
        lines.append(f"- {chapter.get('chapter_title', '未命名章节')}")
        lines.append(f"  正文：{wiki_link_for(chapter['chapter_body'], vault_root)}")
        lines.append(f"  提问入口：{wiki_link_for(chapter['question_entry'], vault_root)}")
    return "\n".join(lines) + "\n"


def render_subject_index(subject: str, source_payloads: list[dict], vault_root: Path) -> str:
    total = sum(item["chapter_count"] for item in source_payloads)
    high_quality = sum(item["high_quality_count"] for item in source_payloads)
    lines = [
        f"# {subject}教材章节总入口",
        "",
        "- 用途：先从这里选教材来源，再进入对应章节总览、章节正文和提问入口。",
        f"- 已接入章节数：{total}",
        f"- 高质量成品：{high_quality}",
        "",
        "| 教材来源 | 章节数 | 高质量成品 | 追问数 | 当前下一步 | 入口 |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for item in source_payloads:
        lines.append(
            f"| {item['source_name']} | {item['chapter_count']} | {item['high_quality_count']} | {item['followup_count']} | {item['next_focus']} | {wiki_link_for(item['index_path'], vault_root)} |"
        )

    lines.extend(["", "## 已接入教材", ""])
    for item in source_payloads:
        lines.append(f"- {item['source_name']}：{wiki_link_for(item['index_path'], vault_root)}")
    return "\n".join(lines) + "\n"


def main() -> int:
    args = parse_args()
    vault_root = Path(args.vault_root)
    registry_path = vault_root / INDEX_DIRNAME / REGISTRY_JSON
    global_concept_path = vault_root / INDEX_DIRNAME / GLOBAL_CONCEPT_JSON
    registry_payload = load_json(registry_path)
    global_concepts = load_json(global_concept_path).get("concepts", []) if global_concept_path.exists() else []
    chapter_bridges = load_json(vault_root / INDEX_DIRNAME / CHAPTER_BRIDGE_JSON).get("bridges", []) if (vault_root / INDEX_DIRNAME / CHAPTER_BRIDGE_JSON).exists() else []
    subject_groups = build_subject_groups(registry_payload.get("chapters", []))

    for subject, chapters in subject_groups.items():
        if not subject:
            continue
        subject_label, subject_config = resolve_subject(subject)
        subject_root = vault_root / subject_config["dir"]
        textbook_index_root = subject_root / COURSE_DIRNAME / TEXTBOOK_INDEX_DIRNAME
        textbook_index_root.mkdir(parents=True, exist_ok=True)

        source_payloads = []
        for source_name, source_chapters in sorted(build_source_groups(chapters).items(), key=lambda item: item[0]):
            repeated = repeated_concepts_for_source(source_chapters, global_concepts, subject_label)
            bridges = chapter_bridges_for_source(source_chapters, chapter_bridges, subject_label, source_name)
            followups = collect_unique_texts(source_followup_questions(source_chapters, repeated) + bridge_questions(bridges), 12)
            weak_spots = source_weak_spots(source_chapters)
            qa_notes = qa_notes_for_source(subject_root, source_chapters)
            saved_questions = recent_saved_questions_for_source(source_chapters)
            file_name = f"10_{sanitize_name(source_name)}章节总览.md"
            index_path = textbook_index_root / file_name
            index_path.write_text(
                render_source_index(subject_label, source_name, source_chapters, repeated, followups, weak_spots, qa_notes, bridges, saved_questions, vault_root),
                encoding="utf-8",
            )
            chapter_count, high_quality_count, _ = summarize_quality(source_chapters)
            source_payloads.append(
                {
                    "source_name": source_name,
                    "chapter_count": chapter_count,
                    "high_quality_count": high_quality_count,
                    "followup_count": len(followups),
                    "next_focus": next_focus_text(sort_chapters(source_chapters)),
                    "index_path": index_path,
                }
            )

        subject_index_path = textbook_index_root / SUBJECT_INDEX_NAME
        subject_index_path.write_text(render_subject_index(subject_label, source_payloads, vault_root), encoding="utf-8")

        subject_entry = subject_root / COURSE_DIRNAME / SUBJECT_ENTRY_TEMPLATE.format(subject=subject_root.name.split("_", 1)[-1])
        ensure_line(
            subject_entry,
            "教材章节总览",
            f"- 教材章节总览：{wiki_link_for(subject_index_path, vault_root)}",
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
