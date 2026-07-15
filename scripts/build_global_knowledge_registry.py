#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
from pathlib import Path

from common import INDEX_DIRNAME, default_vault_root_arg, load_json, sanitize_name, save_json

REGISTRY_JSON = "chapter_knowledge_registry.json"
REGISTRY_MD = "10_章节知识问答总入口.md"
GLOBAL_CONCEPT_JSON = "global_concept_registry.json"
GLOBAL_CONCEPT_MD = "11_跨章节知识串联.md"
CHAPTER_BRIDGE_JSON = "chapter_bridge_registry.json"
CHAPTER_BRIDGE_MD = "12_同教材章节递进.md"
CARD_REUSE_JSON = "card_reuse_candidates.json"
CARD_REUSE_MD = "14_跨章节卡片复用候选.md"
MASTER_CARD_JSON = "master_card_candidates.json"
MASTER_CARD_MD = "16_跨章节主卡片候选.md"

GENERIC_THEME_TOKENS = (
    "核心概念",
    "关键规则",
    "解析主线",
    "题型起手",
    "题型复盘主线",
    "代表例题",
    "归纳主线",
    "归纳总结",
    "题解与解析",
    "题型训练",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--vault-root", default=default_vault_root_arg())
    return parser.parse_args()


def iter_indexes(vault_root: Path) -> list[Path]:
    return sorted(vault_root.rglob("chapter_knowledge_index.json"))


def subject_from_path(path: Path) -> str:
    parts = set(path.parts)
    if "10_数学" in parts:
        return "数学"
    if "20_英语" in parts:
        return "英语"
    if "30_408" in parts:
        return "408"
    if "40_政治" in parts:
        return "政治"
    return "未知"


def build_registry_entry(index_path: Path) -> dict:
    payload = load_json(index_path)
    batch_dir = index_path.parent.parent
    audit_path = batch_dir / "00_知识归纳状态.json"
    audit_payload = load_json(audit_path) if audit_path.exists() else {}
    context_path = batch_dir / "00_批次上下文.json"
    context_payload = load_json(context_path) if context_path.exists() else {}
    return {
        "subject": subject_from_path(index_path),
        "chapter_title": payload.get("chapter_title", "未命名章节"),
        "batch_id": payload.get("batch_id", ""),
        "chapter_dir": str(batch_dir),
        "source_name": context_payload.get("source_name", ""),
        "question_entry": str(index_path.parent / "03_知识点问答入口.md"),
        "chapter_body": str(batch_dir / "20_章节整理" / "01_章节整理正文.md"),
        "concept_count": len(payload.get("concept_index", [])),
        "question_count": len(payload.get("question_prompts", [])),
        "saved_qa_count": int(payload.get("saved_qa_count", 0)),
        "weak_spot_count": len(payload.get("weak_spots", [])),
        "knowledge_status": audit_payload.get("knowledge_status", ""),
        "quality_level": audit_payload.get("quality_level", ""),
        "chapter_body_ready": audit_payload.get("chapter_body_ready", False),
        "chapter_overview": payload.get("chapter_overview", ""),
        "learning_path": payload.get("learning_path", []),
        "priority_concepts": payload.get("priority_concepts", []),
        "major_sections": payload.get("major_sections", []),
        "concept_index": payload.get("concept_index", []),
        "question_prompts": payload.get("question_prompts", []),
        "recent_saved_questions": payload.get("recent_saved_questions", []),
        "saved_weak_spots": payload.get("saved_weak_spots", []),
        "saved_next_questions": payload.get("saved_next_questions", []),
        "example_types": payload.get("example_types", []),
        "weak_spots": payload.get("weak_spots", []),
        "chunks": payload.get("chunks", []),
    }


CHINESE_DIGITS = {
    "零": 0,
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
}


def parse_chinese_number(raw: str) -> int | None:
    text = str(raw or "").strip()
    if not text:
        return None
    if text.isdigit():
        return int(text)
    if text == "十":
        return 10
    if "十" in text:
        left, _, right = text.partition("十")
        tens = CHINESE_DIGITS.get(left, 1 if left == "" else None)
        if tens is None:
            return None
        ones = CHINESE_DIGITS.get(right, 0 if right == "" else None)
        if ones is None:
            return None
        return tens * 10 + ones
    total = 0
    for char in text:
        if char not in CHINESE_DIGITS:
            return None
        total = total * 10 + CHINESE_DIGITS[char]
    return total or None


def chapter_order_key(chapter_title: str) -> tuple[int, str]:
    title = normalize_text(chapter_title)
    match = re.search(r"第\s*([0-9一二三四五六七八九十两零]+)\s*章", title)
    if match:
        number = parse_chinese_number(match.group(1))
        if number is not None:
            return number, title
    return 9999, title


def normalize_text(value: str) -> str:
    text = str(value or "").strip()
    text = re.sub(r"\s+", " ", text)
    return text


def clean_theme_text(value: str) -> str:
    text = normalize_text(value)
    text = re.sub(r"^第[0-9一二三四五六七八九十百零两]+章", "", text).strip()
    text = re.sub(r"^[0-9]+(?:\.[0-9]+)*\s*", "", text)
    text = text.strip(" ：:-")
    for token in GENERIC_THEME_TOKENS:
        text = text.replace(token, " ")
    text = re.sub(r"\s+", " ", text).strip(" ：:-")
    return text


def concept_theme_key(concept: dict, entry: dict) -> str:
    candidates = [
        concept.get("name", ""),
        concept.get("section", ""),
        entry.get("chapter_title", ""),
    ]
    for candidate in candidates:
        cleaned = clean_theme_text(str(candidate))
        if cleaned:
            return cleaned.lower()
    return normalize_text(concept.get("name", "")).lower()


def concept_display_name(concept: dict, entry: dict) -> str:
    name = normalize_text(concept.get("name", ""))
    if name:
        return name
    section = clean_theme_text(concept.get("section", ""))
    if section:
        return section
    return normalize_text(entry.get("chapter_title", "")) or "未命名概念"


def build_global_concepts(entries: list[dict]) -> list[dict]:
    concepts: dict[str, dict] = {}
    for entry in entries:
        for concept in entry.get("concept_index", []):
            name = concept_display_name(concept, entry)
            theme_key = concept_theme_key(concept, entry)
            if not name or not theme_key:
                continue
            bucket = concepts.setdefault(
                theme_key,
                {
                    "concept_name": name,
                    "theme_key": theme_key,
                    "aliases": [],
                    "subjects": [],
                    "chapters": [],
                    "summaries": [],
                    "key_rules": [],
                    "followup_questions": [],
                    "references": [],
                },
            )
            if len(name) < len(bucket["concept_name"]):
                bucket["concept_name"] = name
            if name not in bucket["aliases"]:
                bucket["aliases"].append(name)
            subject = entry.get("subject", "")
            chapter_title = entry.get("chapter_title", "")
            if subject and subject not in bucket["subjects"]:
                bucket["subjects"].append(subject)
            chapter_label = f"{subject} - {chapter_title}".strip(" -")
            if chapter_label and chapter_label not in bucket["chapters"]:
                bucket["chapters"].append(chapter_label)
            summary = str(concept.get("summary", "")).strip()
            if summary and summary not in bucket["summaries"]:
                bucket["summaries"].append(summary)
            key_rule = str(concept.get("key_rule", "")).strip()
            if key_rule and key_rule not in bucket["key_rules"]:
                bucket["key_rules"].append(key_rule)
            for question in concept.get("followup_questions", []):
                question = str(question).strip()
                if question and question not in bucket["followup_questions"]:
                    bucket["followup_questions"].append(question)
            bucket["references"].append(
                {
                    "subject": subject,
                    "chapter_title": chapter_title,
                    "section": concept.get("section", ""),
                    "card_file": concept.get("card_file", ""),
                    "page_start": concept.get("page_start", ""),
                    "page_end": concept.get("page_end", ""),
                    "chunk_id": concept.get("chunk_id", ""),
                    "question_entry": entry.get("question_entry", ""),
                    "chapter_body": entry.get("chapter_body", ""),
                }
            )
    result = []
    for bucket in concepts.values():
        bucket["reference_count"] = len(bucket["references"])
        result.append(bucket)
    return sorted(result, key=lambda item: (item["reference_count"] * -1, item["concept_name"]))


def build_chapter_bridges(entries: list[dict]) -> list[dict]:
    grouped: dict[tuple[str, str], list[dict]] = {}
    for entry in entries:
        subject = str(entry.get("subject", "")).strip()
        source_name = str(entry.get("source_name", "")).strip()
        if not subject or not source_name:
            continue
        grouped.setdefault((subject, source_name), []).append(entry)

    bridges: list[dict] = []
    for (subject, source_name), chapters in grouped.items():
        ordered = sorted(chapters, key=lambda item: chapter_order_key(item.get("chapter_title", "")))
        for index in range(len(ordered) - 1):
            current = ordered[index]
            nxt = ordered[index + 1]
            current_priority = [str(item).strip() for item in current.get("priority_concepts", []) if str(item).strip()]
            next_priority = [str(item).strip() for item in nxt.get("priority_concepts", []) if str(item).strip()]
            carry_points = []
            if current_priority:
                carry_points.append(f"先把 {current_priority[0]} 说顺，再进入 {nxt.get('chapter_title', '下一章')}。")
            if len(current_priority) > 1:
                carry_points.append(f"带着 {current_priority[1]} 的口径去看 {nxt.get('chapter_title', '下一章')} 里的新结构或新题型。")
            if next_priority:
                carry_points.append(f"进入 {nxt.get('chapter_title', '下一章')} 时，优先抓 {next_priority[0]} 这条主线。")

            transition_questions = []
            if current_priority:
                transition_questions.append(
                    f"{current.get('chapter_title', '当前章')} 里的 {current_priority[0]}，到 {nxt.get('chapter_title', '下一章')} 会变成什么具体抓手？"
                )
            if next_priority:
                transition_questions.append(
                    f"如果先学 {nxt.get('chapter_title', '下一章')}，最该回头补 {current.get('chapter_title', '当前章')} 的哪个基础点？"
                )
                transition_questions.append(
                    f"{nxt.get('chapter_title', '下一章')} 里的 {next_priority[0]}，最依赖前一章哪条口径？"
                )

            bridges.append(
                {
                    "bridge_id": f"{subject}-{sanitize_name(source_name)}-{index + 1:03d}",
                    "subject": subject,
                    "source_name": source_name,
                    "from_chapter": current.get("chapter_title", ""),
                    "to_chapter": nxt.get("chapter_title", ""),
                    "bridge_title": f"{current.get('chapter_title', '')} -> {nxt.get('chapter_title', '')}",
                    "carry_over_points": carry_points,
                    "transition_questions": transition_questions,
                    "from_question_entry": current.get("question_entry", ""),
                    "to_question_entry": nxt.get("question_entry", ""),
                    "from_chapter_body": current.get("chapter_body", ""),
                    "to_chapter_body": nxt.get("chapter_body", ""),
                }
            )
    return bridges


def build_card_reuse_candidates(concepts: list[dict]) -> list[dict]:
    candidates: list[dict] = []
    for concept in concepts:
        refs = concept.get("references", [])
        chapter_pairs = []
        card_files = []
        seen_pairs = set()
        for ref in refs:
            pair = (str(ref.get("subject", "")).strip(), str(ref.get("chapter_title", "")).strip())
            if pair[0] and pair[1] and pair not in seen_pairs:
                seen_pairs.add(pair)
                chapter_pairs.append({"subject": pair[0], "chapter_title": pair[1]})
            card_file = str(ref.get("card_file", "")).strip()
            if card_file and card_file not in card_files:
                card_files.append(card_file)
        if len(chapter_pairs) < 2:
            continue
        aliases = [alias for alias in concept.get("aliases", []) if alias and alias != concept.get("concept_name", "")]
        summary = next((item for item in concept.get("summaries", []) if str(item).strip()), "")
        key_rule = next((item for item in concept.get("key_rules", []) if str(item).strip()), "")
        candidates.append(
            {
                "concept_name": concept.get("concept_name", ""),
                "theme_key": concept.get("theme_key", ""),
                "chapter_count": len(chapter_pairs),
                "reference_count": int(concept.get("reference_count", len(refs))),
                "aliases": aliases,
                "card_files": card_files,
                "chapters": chapter_pairs,
                "summary": summary,
                "key_rule": key_rule,
                "followup_questions": concept.get("followup_questions", [])[:5],
                "reuse_priority": len(chapter_pairs) * 10 + len(aliases) * 2 + len(card_files),
            }
        )
    return sorted(candidates, key=lambda item: (-item["reuse_priority"], -item["chapter_count"], item["concept_name"]))


def build_master_card_candidates(concepts: list[dict], reuse_candidates: list[dict]) -> list[dict]:
    results: list[dict] = []
    seen_theme_keys: set[str] = set()
    for item in reuse_candidates:
        concept_name = str(item.get("concept_name", "")).strip()
        if not concept_name:
            continue
        theme_key = str(item.get("theme_key", "")).strip()
        if theme_key:
            seen_theme_keys.add(theme_key)
        aliases = [str(alias).strip() for alias in item.get("aliases", []) if str(alias).strip()]
        card_files = [str(name).strip() for name in item.get("card_files", []) if str(name).strip()]
        chapters = item.get("chapters", [])
        chapter_count = int(item.get("chapter_count", len(chapters)))
        results.append(
            {
                "concept_name": concept_name,
                "suggested_master_card_name": concept_name,
                "theme_key": theme_key,
                "consolidation_scope": "cross-chapter",
                "chapter_count": chapter_count,
                "source_card_files": card_files,
                "chapters": chapters,
                "stable_summary": str(item.get("summary", "")).strip(),
                "stable_rule": str(item.get("key_rule", "")).strip(),
                "aliases_to_merge": aliases,
                "followup_questions": [str(text).strip() for text in item.get("followup_questions", []) if str(text).strip()][:5],
                "reference_entries": [],
                "consolidation_notes": [
                    note
                    for note in [
                        "先统一命名，再决定是否保留章节内别名卡片。" if aliases else "",
                        "先保留章节回链，再把稳定定义上收为主卡片。" if chapter_count >= 2 else "",
                        "后续问答优先指向主卡片，再回到章节卡片看局部语境。",
                    ]
                    if note
                ],
                "master_priority": chapter_count * 10 + len(card_files) + len(aliases),
            }
        )

    for concept in concepts:
        theme_key = str(concept.get("theme_key", "")).strip()
        if not theme_key or theme_key in seen_theme_keys:
            continue
        refs = concept.get("references", [])
        card_files: list[str] = []
        chapters: list[dict] = []
        for ref in refs:
            card_file = str(ref.get("card_file", "")).strip()
            if card_file and card_file not in card_files:
                card_files.append(card_file)
            chapter_ref = {
                "subject": str(ref.get("subject", "")).strip(),
                "chapter_title": str(ref.get("chapter_title", "")).strip(),
            }
            if chapter_ref["subject"] and chapter_ref["chapter_title"] and chapter_ref not in chapters:
                chapters.append(chapter_ref)
        aliases = [
            str(alias).strip()
            for alias in concept.get("aliases", [])
            if str(alias).strip() and str(alias).strip() != str(concept.get("concept_name", "")).strip()
        ]
        if len(card_files) < 2:
            continue
        chapter_count = len(chapters)
        scope = "cross-chapter" if chapter_count >= 2 else "same-chapter-multi-card"
        results.append(
            {
                "concept_name": str(concept.get("concept_name", "")).strip(),
                "suggested_master_card_name": str(concept.get("concept_name", "")).strip(),
                "theme_key": theme_key,
                "consolidation_scope": scope,
                "chapter_count": chapter_count,
                "source_card_files": card_files,
                "chapters": chapters,
                "stable_summary": str(next((item for item in concept.get("summaries", []) if str(item).strip()), "")).strip(),
                "stable_rule": str(next((item for item in concept.get("key_rules", []) if str(item).strip()), "")).strip(),
                "aliases_to_merge": aliases,
                "followup_questions": [str(text).strip() for text in concept.get("followup_questions", []) if str(text).strip()][:5],
                "reference_entries": refs,
                "consolidation_notes": [
                    note
                    for note in [
                        "先把同主题卡片合并成一张稳定主卡，再把题型/解析口径降为从属回链。" if scope == "same-chapter-multi-card" else "",
                        "保留章节语境回链，避免把题型入口和概念定义混成一张空泛大卡。",
                        "后续问答优先命中主卡片，再决定是否回到某一章的局部卡片。",
                    ]
                    if note
                ],
                "master_priority": chapter_count * 10 + len(card_files) * 2 + len(aliases),
            }
        )
    return sorted(results, key=lambda item: (-item["master_priority"], item["concept_name"]))


def render_registry(entries: list[dict]) -> str:
    lines = [
        "# 章节知识问答总入口",
        "",
        "- 用途：从这里先定位学科、章节，再进入对应章节正文、知识卡片和提问入口。",
        "- 建议问法：先问章节主线，再问具体概念，最后问下一步练什么。",
        "",
        "| 学科 | 章节 | 质量层级 | 知识点数 | 追问数 | 易混点数 | 入口 |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for entry in entries:
        lines.append(
            f"| {entry['subject']} | {entry['chapter_title']} | {entry.get('quality_level') or '待评估'} | {entry['concept_count']} | {entry['question_count']} | {entry['weak_spot_count']} | {Path(entry['question_entry']).name} |"
        )

    for entry in entries:
        lines.extend(
            [
                "",
                f"## {entry['subject']} - {entry['chapter_title']}",
                "",
                f"- 质量层级：{entry.get('quality_level') or '待评估'}",
                f"- 知识归纳状态：{entry.get('knowledge_status') or '待补充'}",
                f"- 章节正文：{entry['chapter_body']}",
                f"- 问答入口：{entry['question_entry']}",
            ]
        )
        if entry["chapter_overview"]:
            lines.extend(["", "### 章节总述", "", entry["chapter_overview"]])
        if entry["learning_path"]:
            lines.extend(["", "### 建议学习顺序", ""])
            lines.extend(f"- {item}" for item in entry["learning_path"][:4])
        lines.extend(["", "### 可直接追问的知识点", ""])
        if entry["concept_index"]:
            for concept in entry["concept_index"][:12]:
                lines.append(f"- {concept['name']}：{concept['section']}")
        else:
            lines.append("- 待补充")

        lines.extend(["", "### 优先追问清单", ""])
        if entry["question_prompts"]:
            for prompt in entry["question_prompts"][:8]:
                lines.append(f"- {prompt}")
        else:
            lines.append("- 待补充")
    return "\n".join(lines) + "\n"


def render_global_concepts(concepts: list[dict]) -> str:
    lines = [
        "# 跨章节知识串联",
        "",
        "- 用途：从概念出发，回看它出现在哪些章节，以及后续可以怎么追问。",
        "",
        "| 概念 | 涉及章节数 | 学科 | 入口 |",
        "| --- | --- | --- | --- |",
    ]
    for concept in concepts:
        first_ref = concept["references"][0] if concept.get("references") else {}
        entry_name = Path(first_ref.get("question_entry", "")).name if first_ref.get("question_entry") else "待补充"
        lines.append(
            f"| {concept['concept_name']} | {concept.get('reference_count', len(concept['references']))} | {'、'.join(concept['subjects']) or '待补充'} | {entry_name} |"
        )
    for concept in concepts:
        lines.extend(
            [
                "",
                f"## {concept['concept_name']}",
                "",
                f"- 涉及学科：{'、'.join(concept['subjects']) or '待补充'}",
                f"- 涉及章节：{concept.get('reference_count', len(concept['references']))}",
                "",
                "### 章节分布",
                "",
            ]
        )
        aliases = [alias for alias in concept.get("aliases", []) if alias and alias != concept["concept_name"]]
        if aliases:
            lines.extend(["### 同主题别名", ""])
            for alias in aliases[:5]:
                lines.append(f"- {alias}")
            lines.append("")
        for ref in concept["references"][:8]:
            lines.append(f"- {ref['subject']}｜{ref['chapter_title']}｜{ref['section']}｜{ref['page_start']} - {ref['page_end']}")
        if concept["summaries"]:
            lines.extend(["", "### 典型定义/说明", ""])
            for summary in concept["summaries"][:3]:
                lines.append(f"- {summary}")
        if concept["key_rules"]:
            lines.extend(["", "### 关键口径", ""])
            for rule in concept["key_rules"][:3]:
                lines.append(f"- {rule}")
        if concept["followup_questions"]:
            lines.extend(["", "### 后续可继续问", ""])
            for question in concept["followup_questions"][:5]:
                lines.append(f"- {question}")
    return "\n".join(lines) + "\n"


def render_chapter_bridges(bridges: list[dict]) -> str:
    lines = [
        "# 同教材章节递进",
        "",
        "- 用途：当跨章节重复概念还不够多时，先按同一本教材的章节递进关系继续学和继续问。",
        "",
        "| 学科 | 教材来源 | 章节递进 | 入口 |",
        "| --- | --- | --- | --- |",
    ]
    for bridge in bridges:
        lines.append(
            f"| {bridge.get('subject', '')} | {bridge.get('source_name', '')} | {bridge.get('bridge_title', '')} | {Path(bridge.get('to_question_entry', '')).name if bridge.get('to_question_entry') else '待补充'} |"
        )
    for bridge in bridges:
        lines.extend(
            [
                "",
                f"## {bridge.get('bridge_title', '')}",
                "",
                f"- 学科：{bridge.get('subject', '')}",
                f"- 教材来源：{bridge.get('source_name', '')}",
                f"- 前一章正文：{bridge.get('from_chapter_body', '')}",
                f"- 下一章正文：{bridge.get('to_chapter_body', '')}",
                f"- 下一章提问入口：{bridge.get('to_question_entry', '')}",
                "",
                "### 带着什么过去看",
                "",
            ]
        )
        if bridge.get("carry_over_points"):
            for item in bridge["carry_over_points"][:4]:
                lines.append(f"- {item}")
        else:
            lines.append("- 当前还没有稳定提炼出章节递进抓手。")
        lines.extend(["", "### 可继续追问", ""])
        if bridge.get("transition_questions"):
            for question in bridge["transition_questions"][:4]:
                lines.append(f"- {question}")
        else:
            lines.append("- 当前还没有稳定提炼出章节递进追问。")
    return "\n".join(lines) + "\n"


def render_card_reuse_candidates(candidates: list[dict]) -> str:
    lines = [
        "# 跨章节卡片复用候选",
        "",
        "- 用途：优先回看这些已经在多个章节出现的知识点，判断是直接复用、补厚原卡，还是再拆出更稳定的通用卡片。",
        "",
        "| 候选概念 | 涉及章节数 | 别名数 | 现有卡片名 |",
        "| --- | --- | --- | --- |",
    ]
    for item in candidates:
        card_text = "、".join(item.get("card_files", [])[:3]) or "待补充"
        lines.append(f"| {item['concept_name']} | {item['chapter_count']} | {len(item.get('aliases', []))} | {card_text} |")
    for item in candidates:
        lines.extend(
            [
                "",
                f"## {item['concept_name']}",
                "",
                f"- 涉及章节：{item['chapter_count']}",
                f"- 当前可见卡片名：{'、'.join(item.get('card_files', [])) or '待补充'}",
            ]
        )
        if item.get("aliases"):
            lines.extend(["- 可能需要统一的别名：", *[f"  - {alias}" for alias in item["aliases"][:6]]])
        if item.get("summary"):
            lines.extend(["", "### 稳定定义抓手", "", f"- {item['summary']}"])
        if item.get("key_rule"):
            lines.extend(["", "### 稳定规则抓手", "", f"- {item['key_rule']}"])
        lines.extend(["", "### 出现章节", ""])
        for chapter in item.get("chapters", [])[:8]:
            lines.append(f"- {chapter['subject']}｜{chapter['chapter_title']}")
        if item.get("followup_questions"):
            lines.extend(["", "### 后续可继续问", ""])
            for question in item["followup_questions"][:4]:
                lines.append(f"- {question}")
    return "\n".join(lines) + "\n"


def render_master_card_candidates(candidates: list[dict]) -> str:
    lines = [
        "# 跨章节主卡片候选",
        "",
        "- 用途：把已经出现多来源聚合迹象的知识点收束成后续可提升为主卡片的候选，既包含跨章节重复，也包含同章多卡可合并的主题簇。",
        "",
        "| 候选概念 | 收束范围 | 涉及章节数 | 建议主卡片名 | 当前来源卡片 |",
        "| --- | --- | --- | --- | --- |",
    ]
    if not candidates:
        lines.append("| - | - | - | - | 当前还没有稳定的主卡片候选 |")
    for item in candidates:
        card_text = "、".join(item.get("source_card_files", [])[:3]) or "待补充"
        lines.append(
            f"| {item.get('concept_name', '')} | {item.get('consolidation_scope', '')} | {item.get('chapter_count', 0)} | {item.get('suggested_master_card_name', '')} | {card_text} |"
        )
    for item in candidates:
        lines.extend(
            [
                "",
                f"## {item.get('concept_name', '')}",
                "",
                f"- 建议主卡片名：{item.get('suggested_master_card_name', '')}",
                f"- 收束范围：{item.get('consolidation_scope', '')}",
                f"- 当前来源卡片：{'、'.join(item.get('source_card_files', [])) or '待补充'}",
                f"- 涉及章节数：{item.get('chapter_count', 0)}",
            ]
        )
        if item.get("stable_summary"):
            lines.extend(["", "### 稳定定义抓手", "", f"- {item['stable_summary']}"])
        if item.get("stable_rule"):
            lines.extend(["", "### 稳定规则抓手", "", f"- {item['stable_rule']}"])
        if item.get("aliases_to_merge"):
            lines.extend(["", "### 建议合并的别名", ""])
            for alias in item["aliases_to_merge"][:6]:
                lines.append(f"- {alias}")
        if item.get("chapters"):
            lines.extend(["", "### 当前涉及章节", ""])
            for chapter in item["chapters"][:8]:
                lines.append(f"- {chapter.get('subject', '')} - {chapter.get('chapter_title', '')}")
        if item.get("consolidation_notes"):
            lines.extend(["", "### 收束建议", ""])
            for note in item["consolidation_notes"][:4]:
                lines.append(f"- {note}")
        if item.get("followup_questions"):
            lines.extend(["", "### 后续可继续问", ""])
            for question in item["followup_questions"][:4]:
                lines.append(f"- {question}")
    return "\n".join(lines) + "\n"


def main() -> int:
    args = parse_args()
    vault_root = Path(args.vault_root)
    entries = [build_registry_entry(path) for path in iter_indexes(vault_root)]
    global_concepts = build_global_concepts(entries)
    chapter_bridges = build_chapter_bridges(entries)
    card_reuse_candidates = build_card_reuse_candidates(global_concepts)
    master_card_candidates = build_master_card_candidates(global_concepts, card_reuse_candidates)
    index_dir = vault_root / INDEX_DIRNAME
    index_dir.mkdir(parents=True, exist_ok=True)
    save_json(index_dir / REGISTRY_JSON, {"chapters": entries})
    save_json(index_dir / GLOBAL_CONCEPT_JSON, {"concepts": global_concepts})
    save_json(index_dir / CHAPTER_BRIDGE_JSON, {"bridges": chapter_bridges})
    save_json(index_dir / CARD_REUSE_JSON, {"candidates": card_reuse_candidates})
    save_json(index_dir / MASTER_CARD_JSON, {"candidates": master_card_candidates})
    (index_dir / REGISTRY_MD).write_text(render_registry(entries), encoding="utf-8")
    (index_dir / GLOBAL_CONCEPT_MD).write_text(render_global_concepts(global_concepts), encoding="utf-8")
    (index_dir / CHAPTER_BRIDGE_MD).write_text(render_chapter_bridges(chapter_bridges), encoding="utf-8")
    (index_dir / CARD_REUSE_MD).write_text(render_card_reuse_candidates(card_reuse_candidates), encoding="utf-8")
    (index_dir / MASTER_CARD_MD).write_text(render_master_card_candidates(master_card_candidates), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
