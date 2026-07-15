#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
from pathlib import Path

from common import (
    build_provenance_record,
    build_source_span,
    ensure_kb_layout,
    ensure_learning_dirs,
    format_page_label,
    is_placeholder,
    load_json,
    load_json_or_default,
    markdown_list,
    normalize_context,
    save_json,
)
from config import load_runtime_config
from ocr.cache import cache_paths_for_request

PLAN_JSON = "00_分片计划.json"
CHAPTER_BODY = "01_章节整理正文.md"

PLACEHOLDER_TOKENS = (
    "待补充",
    "待判定",
    "待整理",
    "待确认",
    "待细化",
    "至少保留 1 到 2 个",
    "优先保留 2 到 3 个",
    "优先提炼 1^∞",
    "如果本段以例题为主",
    "先明确本段主线",
)

GENERIC_SECTION_TOKENS = (
    "试题精选",
    "答案解析",
    "题解与解析",
    "题型训练",
    "代表例题",
    "归纳总结",
    "归纳",
    "总结",
    "拓展",
    "导读",
    "习题",
    "练习",
    "题目",
)

GENERIC_CONCEPT_SUFFIXES = (
    "核心概念",
    "关键规则",
    "解析主线",
    "题型起手",
    "代表例题",
    "归纳主线",
)

WEAK_TEXT_SNIPPETS = (
    "做过渡整理",
    "后续应先判断它更偏概念、方法还是题型",
    "仍需人工复核",
    "优先提炼",
    "这一段的主线到底是什么",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--context-json", required=True)
    parser.add_argument("--chunk-plan-json")
    return parser.parse_args()


def text_needs_refresh(value: str) -> bool:
    text = str(value or "").strip()
    if not text:
        return True
    if is_placeholder(text):
        return True
    if any(token in text for token in PLACEHOLDER_TOKENS):
        return True
    return any(token in text for token in WEAK_TEXT_SNIPPETS)


def clean_chapter_title(title: str) -> str:
    value = str(title or "").strip()
    value = value.replace("图片批次验收", "").replace("图片批次", "").strip()
    return value or "本章"


def chapter_topic_base(chapter_title: str) -> str:
    title = clean_chapter_title(chapter_title)
    title = re.sub(r"^第[0-9一二三四五六七八九十百零两]+章", "", title).strip()
    title = re.sub(r"\s+", " ", title).strip(" ：:-")
    return title or clean_chapter_title(chapter_title)


def clean_section_name(section: str, chapter_title: str) -> str:
    value = str(section or "").strip()
    if not value:
        return clean_chapter_title(chapter_title)
    value = value.replace("待细化", "").strip(" -+")
    value = value.replace("题目段", "题型训练")
    value = value.replace("解析段", "题解与解析")
    value = re.sub(r"\s+", " ", value).strip(" -+")
    if value.endswith("图片批次"):
        value = value[:-4].strip()
    return value or clean_chapter_title(chapter_title)


def section_name_needs_refresh(section: str, chapter_title: str) -> bool:
    value = clean_section_name(section, chapter_title)
    if text_needs_refresh(value):
        return True
    if any(token in value for token in ("待人工复核", "待复核", "待补充", "待细化", "待确认", "待判定")):
        return True
    chapter_clean = clean_chapter_title(chapter_title)
    chapter_base = chapter_topic_base(chapter_title)
    if value == chapter_clean or value == chapter_base:
        return True
    if chapter_clean and value.startswith(chapter_clean) and any(token in value for token in ("待人工复核", "待补充", "待细化", "题型起手")):
        return True
    if chapter_base and value.startswith(chapter_base) and any(token in value for token in ("待人工复核", "待补充", "待细化", "题型起手")):
        return True
    return False


def strip_section_prefix(section: str) -> str:
    value = str(section or "").strip()
    value = re.sub(r"^[0-9]+(?:\.[0-9]+)*\s*", "", value)
    value = re.sub(r"^第[0-9一二三四五六七八九十百零两]+[章节篇]\s*", "", value)
    return value.strip(" ：:-")


def extract_specific_section_topic(section: str) -> str:
    value = strip_section_prefix(section)
    if not value:
        return ""
    parts = [part.strip(" ：:-") for part in re.split(r"\s*\+\s*|、|，|,|；|;|/|／", value) if part.strip(" ：:-")]
    specific_parts = []
    for part in parts:
        if any(token in part for token in GENERIC_SECTION_TOKENS):
            continue
        specific_parts.append(part)
    return "、".join(specific_parts[:2])


def build_learning_stage(section: str, usage: str) -> str:
    text = clean_section_name(section, "")
    has_exercise = any(token in text for token in ("试题", "题型", "习题", "练习", "题目"))
    has_analysis = any(token in text for token in ("答案解析", "题解", "解析"))
    has_summary = any(token in text for token in ("归纳", "总结", "拓展"))
    if has_exercise and has_analysis:
        return "题型复盘"
    if usage == "exercise" or has_exercise:
        return "题型训练"
    if usage == "analysis" or has_analysis:
        return "题解与解析"
    if usage == "example":
        return "代表例题"
    if usage == "rule":
        return "关键规则"
    if usage == "summary" or has_summary:
        return "归纳总结"
    return "核心概念"


def derive_topic(section: str, chapter_title: str) -> str:
    cleaned_section = clean_section_name(section, chapter_title)
    cleaned_title = clean_chapter_title(chapter_title)
    topic = extract_specific_section_topic(cleaned_section) or cleaned_section
    if cleaned_title and cleaned_section.startswith(cleaned_title):
        tail = cleaned_section[len(cleaned_title) :].strip(" ：:-")
        if tail:
            topic = f"{cleaned_title}{tail}"
        else:
            topic = cleaned_title
    topic = topic.replace("题型训练", "").replace("题解与解析", "").strip(" ：:-")
    topic = extract_specific_section_topic(topic) or topic
    if any(token in topic for token in GENERIC_SECTION_TOKENS):
        topic = ""
    return topic or chapter_topic_base(chapter_title) or cleaned_title or "本章内容"


def infer_usage(chunk: dict) -> str:
    usage_hints = [str(ref.get("usage_hint", "")).strip() for ref in chunk.get("image_refs", [])]
    joined = " ".join(
        [
            chunk.get("section_guess", ""),
            chunk.get("focus_hint", ""),
            " ".join(usage_hints),
        ]
    )
    if any(token in joined for token in ("题解", "解析")):
        return "analysis"
    if any(token in joined for token in ("习题", "练习", "题型", "试题", "题目")):
        return "exercise"
    if "例题" in joined:
        return "example"
    if any(token in joined for token in ("定理", "公式", "法则", "规则")):
        return "rule"
    if any(token in joined for token in ("归纳", "总结", "拓展")):
        return "summary"
    return "concept"


def synthesize_focus_summary(section: str, chapter_title: str, usage: str) -> str:
    topic = derive_topic(section, chapter_title)
    display_section = clean_section_name(section, chapter_title)
    learning_stage = build_learning_stage(section, usage)
    if learning_stage == "题型复盘":
        return f"这一段用 {topic} 的试题和解析做一轮复盘，整理时应先按题型分组，再把每类题的触发信号、解题步骤和常见卡错点串成可回看的主线。"
    if usage == "exercise":
        return f"这一段围绕 {topic} 的题型训练展开，整理时应先按题型分组，再提炼每类题的起手判断、关键步骤和易错点。"
    if usage == "analysis":
        return f"这一段围绕 {topic} 的题解与解析展开，整理时应优先串起题型信号、解题步骤和常见易错点，再回看对应题目。"
    if usage == "example":
        return f"这一段以 {topic} 的代表例题为主，整理时应抓住题目触发信号、标准解法和可迁移的做题套路。"
    if usage == "rule":
        return f"这一段主要梳理 {topic} 的关键规则与公式，整理时应把适用条件、结论形式和常见误用放在一起看。"
    if usage == "summary":
        return f"这一段主要对 {topic} 做归纳总结，整理时应把前面分散的概念、规则和题型收束成可回看的主线。"
    return f"这一段主要建立 {display_section} 的核心概念和基础口径，整理时应先把定义边界、关键关系和常见混淆点理顺。"


def synthesize_concept_name(section: str, chapter_title: str, usage: str) -> str:
    topic = derive_topic(section, chapter_title)
    learning_stage = build_learning_stage(section, usage)
    if learning_stage == "题型复盘":
        return f"{topic}题型复盘主线"
    if usage == "exercise":
        return f"{topic}题型起手"
    if usage == "analysis":
        return f"{topic}解析主线"
    if usage == "example":
        return f"{topic}代表例题"
    if usage == "rule":
        return f"{topic}关键规则"
    if usage == "summary":
        return f"{topic}归纳主线"
    return f"{topic}核心概念"


def synthesize_key_rule(section: str, chapter_title: str, usage: str) -> str:
    topic = derive_topic(section, chapter_title)
    learning_stage = build_learning_stage(section, usage)
    if learning_stage == "题型复盘":
        return f"先把 {topic} 这组题按题型分开，再把每类题的起手判断、关键步骤和常见返工点理顺。"
    if usage == "exercise":
        return f"先判断这道题属于 {topic} 的哪类题型，再决定起手步骤和所用规则。"
    if usage == "analysis":
        return f"先识别题型信号，再按 {topic} 的标准步骤复盘解题过程。"
    if usage == "example":
        return f"不要只记答案，要把 {topic} 例题的触发条件和迁移方式一起记住。"
    if usage == "rule":
        return f"先确认适用条件，再写 {topic} 的对应规则或公式。"
    if usage == "summary":
        return f"回看 {topic} 时，优先串起主线，再补容易混的细节。"
    return f"先把 {topic} 的定义边界、核心关系和常见混淆点说顺。"


def synthesize_question_prompt(section: str, chapter_title: str, usage: str) -> str:
    topic = derive_topic(section, chapter_title)
    learning_stage = build_learning_stage(section, usage)
    if learning_stage == "题型复盘":
        return f"{topic}这一组试题最值得先复盘的起手判断和方法切换点是什么？"
    if usage == "exercise":
        return f"{topic}这类题最稳的起手判断是什么？"
    if usage == "analysis":
        return f"{topic}的解析里，最值得复盘的步骤切换点是什么？"
    if usage == "example":
        return f"{topic}代表例题最值得迁移的解题套路是什么？"
    if usage == "rule":
        return f"{topic}的关键规则最容易在哪个条件上用错？"
    if usage == "summary":
        return f"回顾 {topic} 时，最值得先串起来的是哪几条主线？"
    return f"{topic}最容易和哪个相邻概念混在一起？"


def synthesize_example_type(section: str, chapter_title: str, usage: str) -> str:
    topic = derive_topic(section, chapter_title)
    learning_stage = build_learning_stage(section, usage)
    if learning_stage == "题型复盘":
        return f"{topic}题型复盘题"
    if usage == "exercise":
        return f"{topic}代表题型"
    if usage == "analysis":
        return f"{topic}解析步骤复盘题"
    if usage == "example":
        return f"{topic}代表例题"
    if usage == "rule":
        return f"{topic}规则辨析题"
    if usage == "summary":
        return f"{topic}归纳总结题"
    return f"{topic}概念辨析题"


def synthesize_confusion(section: str, chapter_title: str, usage: str) -> str:
    topic = derive_topic(section, chapter_title)
    learning_stage = build_learning_stage(section, usage)
    if learning_stage == "题型复盘":
        return f"{topic}这一组复盘最容易出现“会看试题和解析，但不会自己总结题型主线”的问题。"
    if usage == "exercise":
        return f"{topic}这类题最容易出现“题目能看懂，但不会自己起手”的问题。"
    if usage == "analysis":
        return f"{topic}这一段最容易出现“会看解析，但不会自己复现步骤”的问题。"
    if usage == "example":
        return f"{topic}代表例题最容易只记答案，不记触发条件和迁移方式。"
    if usage == "rule":
        return f"{topic}的规则最容易在适用条件还没核对清楚时就直接套用。"
    if usage == "summary":
        return f"{topic}回顾时最容易出现“每个点都见过，但主线串不起来”的问题。"
    return f"{topic}最容易在定义边界和相邻概念上混淆。"


def seed_concepts(concept_hints: list, section: str, chapter_title: str, usage: str, focus_summary: str) -> list[dict]:
    concepts = []
    for item in concept_hints or []:
        if isinstance(item, dict):
            name = item.get("name", "").strip() or synthesize_concept_name(section, chapter_title, usage)
            summary = item.get("summary", "").strip() or focus_summary
            key_rule = item.get("key_rule", "").strip() or synthesize_key_rule(section, chapter_title, usage)
            confusions = [str(text).strip() for text in item.get("confusions", []) if text]
            followups = [str(text).strip() for text in item.get("followup_questions", []) if text]
            concepts.append(
                {
                    "name": name,
                    "summary": summary,
                    "key_rule": key_rule,
                    "confusions": confusions or [synthesize_confusion(section, chapter_title, usage)],
                    "followup_questions": followups or [synthesize_question_prompt(section, chapter_title, usage)],
                }
            )
        else:
            text = str(item).strip()
            if text:
                concepts.append(
                    {
                        "name": text,
                        "summary": focus_summary,
                        "key_rule": synthesize_key_rule(section, chapter_title, usage),
                        "confusions": [synthesize_confusion(section, chapter_title, usage)],
                        "followup_questions": [synthesize_question_prompt(section, chapter_title, usage)],
                    }
                )
    return concepts


def fallback_concepts(chunk: dict, section: str, chapter_title: str, usage: str, focus_summary: str) -> list[dict]:
    return [
        {
            "name": synthesize_concept_name(section, chapter_title, usage),
            "summary": focus_summary,
            "key_rule": synthesize_key_rule(section, chapter_title, usage),
            "confusions": [synthesize_confusion(section, chapter_title, usage)],
            "followup_questions": [synthesize_question_prompt(section, chapter_title, usage)],
        }
    ]


def concept_name_needs_refresh(name: str, section: str, chapter_title: str) -> bool:
    text = str(name or "").strip()
    if not text:
        return True
    normalized_section = clean_section_name(section, chapter_title)
    if normalized_section and text.startswith(normalized_section):
        suffix = text[len(normalized_section) :].strip()
        if suffix in GENERIC_CONCEPT_SUFFIXES:
            return True
    if any(token in text for token in ("待细化", "待补充", "待判定")):
        return True
    return False


def seed_named_items(items: list, text_key: str, fallback_text: str) -> list[dict]:
    seeded = []
    for item in items or []:
        if isinstance(item, dict):
            name = item.get("name") or item.get(text_key) or fallback_text
            text = item.get(text_key) or item.get("content") or item.get("summary") or fallback_text
            name = str(name).strip()
            text = str(text).strip()
            if text_needs_refresh(name) or text_needs_refresh(text):
                continue
            seeded.append({"name": name, text_key: text})
        else:
            text = str(item).strip()
            if text and not text_needs_refresh(text):
                seeded.append({"name": text, text_key: text})
    if not seeded and fallback_text:
        seeded.append({"name": fallback_text, text_key: fallback_text})
    return seeded


def concept_list_needs_refresh(items: list[dict]) -> bool:
    if not items:
        return True
    for item in items:
        if text_needs_refresh(item.get("name", "")) or text_needs_refresh(item.get("summary", "")) or text_needs_refresh(item.get("key_rule", "")):
            return True
    return False


def named_item_list_needs_refresh(items: list[dict], text_key: str) -> bool:
    if not items:
        return True
    for item in items:
        if text_needs_refresh(item.get("name", "")) or text_needs_refresh(item.get(text_key, "")):
            return True
    return False


def text_list_needs_refresh(items: list[str]) -> bool:
    if not items:
        return True
    for item in items:
        if text_needs_refresh(item):
            return True
    return False


def pick_value(existing_value, seeded_value):
    if existing_value is None:
        return seeded_value
    if isinstance(existing_value, list) and not existing_value:
        return seeded_value
    if isinstance(existing_value, str) and text_needs_refresh(existing_value):
        return seeded_value
    return existing_value


def chunk_files_for(context: dict, chunk: dict) -> list[dict]:
    source_id = str(context.get("source_id", "")).strip()
    if not source_id:
        return []
    layout = ensure_kb_layout()
    source_payload = load_json_or_default(layout["sources"] / f"{source_id}.json", {})
    files = list(source_payload.get("files", []))
    try:
        image_start = int(chunk.get("image_start", 0) or 0)
        image_end = int(chunk.get("image_end", 0) or 0)
    except (TypeError, ValueError):
        return []
    if image_start <= 0 or image_end < image_start:
        return []
    return files[image_start - 1 : image_end]


def page_label_for_image_index(context: dict, image_index: int, fallback: str) -> str:
    if str(context.get("page_sequence_mode", "")).strip() == "ordered":
        start_page = context.get("start_page_number")
        try:
            if start_page is not None:
                return format_page_label(int(start_page) + image_index - 1)
        except (TypeError, ValueError):
            pass
    return str(fallback or "").strip()


def build_chunk_source_spans(context: dict, chunk: dict) -> tuple[list[dict], bool]:
    files = chunk_files_for(context, chunk)
    spans: list[dict] = []
    try:
        image_start = int(chunk.get("image_start", 0) or 0)
        image_end = int(chunk.get("image_end", 0) or 0)
    except (TypeError, ValueError):
        return [], False
    for offset, file_payload in enumerate(files):
        image_index = image_start + offset
        page_label = page_label_for_image_index(context, image_index, chunk.get("page_start", ""))
        spans.append(
            build_source_span(
                source_id=str(context.get("source_id", "")).strip(),
                file_id=str(file_payload.get("file_id", "")).strip(),
                source_file_sha256=str(file_payload.get("sha256", "")).strip(),
                chapter_id=str(context.get("chapter_id", "")).strip(),
                chunk_id=str(chunk.get("chunk_id", "")).strip(),
                page_start=page_label,
                page_end=page_label,
                image_start=image_index,
                image_end=image_index,
                origin_type="chunk_extract",
                verification_status="source_grounded",
            )
        )
    expected_count = image_end - image_start + 1
    source_grounded = bool(spans) and len(spans) == expected_count and all(span["file_id"] for span in spans)
    return spans, source_grounded


def load_matching_ocr_candidates(source_spans: list[dict]) -> list[dict]:
    if not source_spans:
        return []
    runtime = load_runtime_config()
    normalized_dir = runtime.ocr_cache_root / "normalized"
    if not normalized_dir.exists():
        return []

    sha_to_span = {
        str(span.get("source_file_sha256", "")).strip(): span
        for span in source_spans
        if str(span.get("source_file_sha256", "")).strip()
    }
    if not sha_to_span:
        return []

    candidates: list[dict] = []
    for normalized_path in sorted(normalized_dir.glob("*.json")):
        payload = load_json_or_default(normalized_path, {})
        source_sha = str(payload.get("source_file_sha256", "")).strip()
        if source_sha not in sha_to_span:
            continue
        request_key = str(payload.get("request_key", "")).strip()
        overlay_payload = (
            load_json_or_default(cache_paths_for_request(runtime.ocr_cache_root, request_key)["overlay"], {})
            if request_key
            else {}
        )
        overlay_by_block = {
            str(item.get("block_id", "")).strip(): item
            for item in overlay_payload.get("items", [])
            if isinstance(item, dict) and str(item.get("block_id", "")).strip()
        }
        base_span = sha_to_span[source_sha]
        for item in payload.get("chunk_candidates", []):
            candidate = dict(item)
            overlay_item = overlay_by_block.get(str(candidate.get("block_id", "")).strip(), {})
            raw_text = str(candidate.get("text", ""))
            corrected_text = str(overlay_item.get("corrected_text", "")).strip()
            review_status = str(overlay_item.get("review_status", "")).strip()
            candidate["raw_text"] = raw_text
            candidate["review_status"] = review_status or "pending"
            candidate["corrected_text"] = corrected_text
            candidate["note"] = str(overlay_item.get("note", "")).strip()
            if review_status == "accepted" and corrected_text:
                candidate["text"] = corrected_text
                candidate["text_source"] = "review_overlay"
            else:
                candidate["text_source"] = "normalized_ocr"
            candidate["source_span"] = build_source_span(
                source_id=str(base_span.get("source_id", "")).strip(),
                file_id=str(base_span.get("file_id", "")).strip(),
                source_file_sha256=source_sha,
                chapter_id=str(base_span.get("chapter_id", "")).strip(),
                chunk_id=str(base_span.get("chunk_id", "")).strip(),
                page_start=base_span.get("locator", {}).get("page_start", ""),
                page_end=base_span.get("locator", {}).get("page_end", ""),
                image_start=base_span.get("locator", {}).get("image_start", ""),
                image_end=base_span.get("locator", {}).get("image_end", ""),
                block_ids=[str(candidate.get("block_id", "")).strip()],
                bbox=list(candidate.get("bbox", []) or []),
                origin_type="ocr_block_candidate",
                verification_status="source_grounded",
            )
            candidates.append(candidate)
    return candidates


def load_matching_page_classification_refs(source_spans: list[dict]) -> list[dict]:
    if not source_spans:
        return []
    runtime = load_runtime_config()
    index_path = runtime.ocr_cache_root / "indexes" / "page_classification_index.json"
    index_payload = load_json_or_default(index_path, {})
    if not index_payload:
        return []

    sha_keys = {
        str(span.get("source_file_sha256", "")).strip()
        for span in source_spans
        if str(span.get("source_file_sha256", "")).strip()
    }
    if not sha_keys:
        return []

    refs: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for item in index_payload.get("items", []):
        source_sha = str(item.get("source_file_sha256", "")).strip()
        if source_sha not in sha_keys:
            continue
        for ref in item.get("refs", []):
            if str(ref.get("classification_status", "")).strip() != "confirmed":
                continue
            if not str(ref.get("chapter_id", "")).strip():
                continue
            page_id = str(ref.get("page_id", "")).strip()
            section_id = str(ref.get("section_id", "")).strip()
            dedupe_key = (page_id, section_id)
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            refs.append(
                {
                    "source_file_sha256": source_sha,
                    "book_id": ref.get("book_id", ""),
                    "book_title": ref.get("book_title", ""),
                    "page_id": page_id,
                    "printed_page": ref.get("printed_page"),
                    "chapter_id": ref.get("chapter_id", ""),
                    "chapter_title": ref.get("chapter_title", ""),
                    "section_id": ref.get("section_id", ""),
                    "section_title": ref.get("section_title", ""),
                    "classification_status": ref.get("classification_status", ""),
                    "classification_method": ref.get("classification_method", ""),
                    "chapter_view_path": ref.get("chapter_view_path", ""),
                    "section_view_path": ref.get("section_view_path", ""),
                }
            )
    refs.sort(key=lambda item: (int(item.get("printed_page") or 0), str(item.get("page_id", ""))))
    return refs


def has_confirmed_page_classification_refs(refs: list[dict[str, Any]]) -> bool:
    for item in refs:
        if not isinstance(item, dict):
            continue
        if str(item.get("classification_status", "")).strip() == "confirmed" and str(item.get("chapter_id", "")).strip():
            return True
    return False


def seed_learning_status(chunk: dict, usage: str) -> dict:
    if usage in {"analysis", "example", "exercise"}:
        return {
            "can_review": "能顺着这一段的题目或解析看懂主要方法，但还需要把题型信号和步骤顺序再说顺。",
            "can_write": "遇到同类题时可以模仿起步，但方法切换和完整落笔还需要继续练。",
        }
    if usage == "rule":
        return {
            "can_review": "能先把这一段的定义、规则和结论按顺序回看清楚。",
            "can_write": "书面表达时仍需把条件、结论和适用场景写得更规范。",
        }
    return {
        "can_review": "先把这一段的主线、概念和方法口径复述顺。",
        "can_write": "后续仍需通过代表题把这段内容真正落到会做上。",
    }


def normalize_existing_concepts(existing_items: list[dict], section: str, chapter_title: str, usage: str, focus_summary: str) -> list[dict]:
    normalized = []
    for item in existing_items or []:
        name = str(item.get("name", "")).strip()
        if text_needs_refresh(name) or concept_name_needs_refresh(name, section, chapter_title):
            name = synthesize_concept_name(section, chapter_title, usage)
        summary = str(item.get("summary", "")).strip()
        if text_needs_refresh(summary):
            summary = focus_summary
        key_rule = str(item.get("key_rule", "")).strip()
        if text_needs_refresh(key_rule):
            key_rule = synthesize_key_rule(section, chapter_title, usage)
        confusions = [str(text).strip() for text in item.get("confusions", []) if text and not text_needs_refresh(text)]
        if not confusions:
            confusions = [synthesize_confusion(section, chapter_title, usage)]
        followups = [str(text).strip() for text in item.get("followup_questions", []) if text and not text_needs_refresh(text)]
        if not followups:
            followups = [synthesize_question_prompt(section, chapter_title, usage)]
        normalized.append(
            {
                "name": name,
                "summary": summary,
                "key_rule": key_rule,
                "confusions": confusions,
                "followup_questions": followups,
            }
        )
    return normalized


def _resolve_chunk_content(existing: dict, chunk: dict, context: dict) -> dict[str, Any]:
    existing_origin_type = str(existing.get("origin_type", "")).strip()
    profile_hint_fields = (
        "concept_hints",
        "formula_hints",
        "example_hints",
        "confusion_hints",
        "question_prompt_hints",
    )
    prefer_profile_hints = existing_origin_type in {"title_inference", "profile_hint"} and (
        any(chunk.get(field) for field in profile_hint_fields)
        or not text_needs_refresh(str(chunk.get("focus_hint", "")).strip())
    )
    chapter_title = context["chapter_title"]
    section = clean_section_name(chunk.get("section_guess", "待补充"), chapter_title)
    usage = infer_usage(chunk)
    profile_hint_used = False
    title_inference_used = False
    focus_summary = chunk.get("focus_hint", "").strip()
    if text_needs_refresh(focus_summary):
        focus_summary = synthesize_focus_summary(section, chapter_title, usage)
        profile_hint_used = True
        title_inference_used = True

    learning_status = existing.get("learning_status") or {}
    if (
        not learning_status
        or text_needs_refresh(learning_status.get("can_review", ""))
        or text_needs_refresh(learning_status.get("can_write", ""))
    ):
        learning_status = seed_learning_status(chunk, usage)

    seeded_concepts = seed_concepts(chunk.get("concept_hints", []), section, chapter_title, usage, focus_summary) or fallback_concepts(chunk, section, chapter_title, usage, focus_summary)
    core_concepts = existing.get("core_concepts")
    if prefer_profile_hints and chunk.get("concept_hints"):
        core_concepts = seeded_concepts
        profile_hint_used = True
    elif not (core_concepts or []):
        core_concepts = seeded_concepts
        profile_hint_used = True
        if not [item for item in chunk.get("concept_hints", []) if item]:
            title_inference_used = True
    else:
        core_concepts = normalize_existing_concepts(core_concepts or [], section, chapter_title, usage, focus_summary)

    primary_concept_name = ""
    for item in core_concepts or []:
        candidate = str(item.get("name", "")).strip()
        if candidate and not text_needs_refresh(candidate):
            primary_concept_name = candidate
            break
    section_from_primary_concept = False
    if section_name_needs_refresh(section, chapter_title) and primary_concept_name:
        section = primary_concept_name
        section_from_primary_concept = True
        if profile_hint_used:
            focus_summary = synthesize_focus_summary(section, chapter_title, usage)

    rules_or_formulas = existing.get("rules_or_formulas")
    seeded_rules = seed_named_items(chunk.get("formula_hints", []), "content", synthesize_key_rule(section, chapter_title, usage))
    if prefer_profile_hints and chunk.get("formula_hints"):
        rules_or_formulas = seeded_rules
        profile_hint_used = True
    elif named_item_list_needs_refresh(rules_or_formulas or [], "content"):
        rules_or_formulas = seeded_rules
        profile_hint_used = True
        if not [item for item in chunk.get("formula_hints", []) if item]:
            title_inference_used = True

    example_types = existing.get("example_types")
    seeded_examples = seed_named_items(chunk.get("example_hints", []), "pattern", synthesize_example_type(section, chapter_title, usage))
    if prefer_profile_hints and chunk.get("example_hints"):
        example_types = seeded_examples
        profile_hint_used = True
    elif named_item_list_needs_refresh(example_types or [], "pattern"):
        example_types = seeded_examples
        profile_hint_used = True
        if not [item for item in chunk.get("example_hints", []) if item]:
            title_inference_used = True

    confusions = existing.get("confusions")
    if prefer_profile_hints and chunk.get("confusion_hints"):
        confusions = [str(item).strip() for item in chunk.get("confusion_hints", []) if item and not text_needs_refresh(item)]
        profile_hint_used = True
    elif text_list_needs_refresh(confusions or []):
        confusions = [str(item).strip() for item in chunk.get("confusion_hints", []) if item and not text_needs_refresh(item)]
        if not confusions:
            confusions = [synthesize_confusion(section, chapter_title, usage)]
            title_inference_used = True
        profile_hint_used = True

    question_prompts = existing.get("question_prompts")
    if prefer_profile_hints and chunk.get("question_prompt_hints"):
        question_prompts = [str(item).strip() for item in chunk.get("question_prompt_hints", []) if item and not text_needs_refresh(item)]
    elif text_list_needs_refresh(question_prompts or []):
        question_prompts = [str(item).strip() for item in chunk.get("question_prompt_hints", []) if item and not text_needs_refresh(item)]
        if not question_prompts:
            question_prompts = [synthesize_question_prompt(section, chapter_title, usage)]

    return {
        "chapter_title": chapter_title,
        "section": section,
        "usage": usage,
        "prefer_profile_hints": prefer_profile_hints,
        "profile_hint_used": profile_hint_used,
        "title_inference_used": title_inference_used,
        "focus_summary": focus_summary,
        "learning_status": learning_status,
        "core_concepts": core_concepts,
        "rules_or_formulas": rules_or_formulas,
        "example_types": example_types,
        "confusions": confusions,
        "question_prompts": question_prompts,
        "section_from_primary_concept": section_from_primary_concept,
    }


def _resolve_chunk_provenance(
    context: dict,
    chunk: dict,
    *,
    profile_hint_used: bool,
    title_inference_used: bool,
) -> dict[str, Any]:
    source_spans, source_grounded = build_chunk_source_spans(context, chunk)
    page_classification_refs = load_matching_page_classification_refs(source_spans)
    has_confirmed_page_refs = has_confirmed_page_classification_refs(page_classification_refs)
    origin_type = "chunk_extract"
    verification_status = "source_grounded" if source_grounded else "needs_review"
    if title_inference_used:
        origin_type = "title_inference"
        verification_status = "source_grounded" if source_grounded and has_confirmed_page_refs else "needs_review"
    elif profile_hint_used:
        origin_type = "profile_hint"
        verification_status = "source_grounded" if source_grounded and has_confirmed_page_refs else "needs_review"

    provenance = build_provenance_record(
        origin_type=origin_type,
        verification_status=verification_status,
        source_spans=source_spans,
        source_grounded=source_grounded,
        profile_hint_used=profile_hint_used,
        title_inference_used=title_inference_used,
    )
    return {
        "source_spans": source_spans,
        "source_grounded": source_grounded,
        "page_classification_refs": page_classification_refs,
        "origin_type": origin_type,
        "verification_status": verification_status,
        "provenance": provenance,
    }


def merge_chunk(existing: dict | None, chunk: dict, context: dict) -> dict:
    existing = existing or {}
    content = _resolve_chunk_content(existing, chunk, context)
    chapter_title = content["chapter_title"]
    section = content["section"]
    prefer_profile_hints = content["prefer_profile_hints"]
    section_from_primary_concept = content["section_from_primary_concept"]
    provenance_fields = _resolve_chunk_provenance(
        context,
        chunk,
        profile_hint_used=content["profile_hint_used"],
        title_inference_used=content["title_inference_used"],
    )

    existing_chunk_title = str(existing.get("chunk_title", "")).strip()
    existing_section = str(existing.get("section", "")).strip()
    chunk_title = (
        section
        if (
            prefer_profile_hints
            or section_from_primary_concept
            or text_needs_refresh(existing_chunk_title)
            or existing_chunk_title.lower().startswith("chunk ")
        )
        else (existing_chunk_title or section or chunk["chunk_title"])
    )
    section_value = (
        section
        if section_from_primary_concept or section_name_needs_refresh(existing_section, chapter_title)
        else pick_value(existing.get("section"), section)
    )

    return {
        "chunk_id": chunk["chunk_id"],
        "chunk_title": chunk_title,
        "chapter_title": context["chapter_title"],
        "chapter_theme": pick_value(existing.get("chapter_theme"), clean_chapter_title(context["chapter_title"])),
        "section": section_value,
        "focus_summary": content["focus_summary"] if prefer_profile_hints else pick_value(existing.get("focus_summary"), content["focus_summary"]),
        "core_concepts": content["core_concepts"],
        "rules_or_formulas": content["rules_or_formulas"],
        "example_types": content["example_types"],
        "confusions": content["confusions"],
        "question_prompts": content["question_prompts"],
        "learning_status": content["learning_status"],
        "source_refs": {
            "batch_id": context["batch_id"],
            "image_start": chunk["image_start"],
            "image_end": chunk["image_end"],
            "page_start": chunk["page_start"],
            "page_end": chunk["page_end"],
            "section_guess": section,
            "file_ids": [span.get("file_id", "") for span in provenance_fields["source_spans"]],
        },
        "origin_type": provenance_fields["origin_type"],
        "verification_status": provenance_fields["verification_status"],
        "source_grounded": provenance_fields["source_grounded"],
        "source_spans": provenance_fields["source_spans"],
        "ocr_chunk_candidates": load_matching_ocr_candidates(provenance_fields["source_spans"]),
        "page_classification_refs": provenance_fields["page_classification_refs"],
        "provenance": provenance_fields["provenance"],
    }


def render_kv_items(items: list[dict], name_key: str, text_key: str) -> str:
    if not items:
        return "- 待补充"
    rendered = []
    for item in items:
        name = item.get(name_key, "待命名")
        text = item.get(text_key, "待补充")
        if str(name).strip() == str(text).strip():
            rendered.append(f"- {text}")
        else:
            rendered.append(f"- {name}: {text}")
    return "\n".join(rendered)


def render_chunk_md(data: dict) -> str:
    lines = [
        f"# {data['chunk_title']}",
        "",
        f"- 片段编号: {data['chunk_id']}",
        f"- 所属小节: {data['section']}",
        f"- 对应图片: {data['source_refs']['image_start']} - {data['source_refs']['image_end']}",
        f"- 对应页段: {data['source_refs']['page_start']} - {data['source_refs']['page_end']}",
        "",
        "## 本片段主旨",
        "",
        data["focus_summary"],
        "",
        "## 核心概念",
        "",
        render_kv_items(data["core_concepts"], "name", "summary"),
        "",
        "## 规则 / 公式 / 结论",
        "",
        render_kv_items(data["rules_or_formulas"], "name", "content"),
        "",
        "## 例题或典型题型",
        "",
        render_kv_items(data["example_types"], "name", "pattern"),
        "",
        "## 易混点",
        "",
        markdown_list(data["confusions"], empty_text="待补充"),
        "",
        "## 当前学习状态",
        "",
        f"- 会看: {data['learning_status'].get('can_review', '待补充')}",
        f"- 会做/会落笔: {data['learning_status'].get('can_write', '待补充')}",
    ]
    if data["question_prompts"]:
        lines.extend(["", "## 本片段后续可问", "", markdown_list(data["question_prompts"], empty_text="待补充")])
    return "\n".join(lines) + "\n"


def render_chapter_body(context: dict, chunks: list[dict]) -> str:
    lines = [
        f"# {context['chapter_title']}章节整理",
        "",
        f"- 学科: {context['subject']}",
        f"- 来源: {context['source_name']}",
        f"- 批次编号: {context['batch_id']}",
        f"- 页码位置: {context['page_number_position_label']}",
    ]
    if context.get("input_path_warning"):
        lines.append(f"- 输入异常提示: {context['input_path_warning']}")

    for chunk in chunks:
        lines.extend(
            [
                "",
                f"## {chunk['chunk_title']}",
                "",
                f"- 小节: {chunk['section']}",
                f"- 图片范围: {chunk['source_refs']['image_start']} - {chunk['source_refs']['image_end']}",
                f"- 页段: {chunk['source_refs']['page_start']} - {chunk['source_refs']['page_end']}",
                f"- 主旨: {chunk['focus_summary']}",
                "",
                "### 核心概念",
                "",
                render_kv_items(chunk["core_concepts"], "name", "summary"),
                "",
                "### 规则 / 公式 / 结论",
                "",
                render_kv_items(chunk["rules_or_formulas"], "name", "content"),
                "",
                "### 例题或典型题型",
                "",
                render_kv_items(chunk["example_types"], "name", "pattern"),
                "",
                "### 易混点",
                "",
                markdown_list(chunk["confusions"], empty_text="待补充"),
                "",
                "### 当前阶段要求",
                "",
                f"- 会看: {chunk['learning_status'].get('can_review', '待补充')}",
                f"- 会做/会落笔: {chunk['learning_status'].get('can_write', '待补充')}",
            ]
        )
        if chunk["question_prompts"]:
            lines.extend(["", "### 这一块后续最值得追问", "", markdown_list(chunk["question_prompts"], empty_text="待补充")])
    return "\n".join(lines) + "\n"


def render_card(context: dict, chunk: dict, concept: dict) -> str:
    followups = concept.get("followup_questions", [])[:3]
    lines = [
        f"# {concept.get('name', '待命名知识点')}",
        "",
        f"- 所属章节: {context['chapter_title']}",
        f"- 所属小节: {chunk['section']}",
        f"- 一句话定义: {concept.get('summary', '待补充')}",
        f"- 关键规则/公式: {concept.get('key_rule', '待补充')}",
        "",
        "## 易混点",
        "",
        markdown_list(concept.get("confusions", []), empty_text="待补充"),
    ]
    if followups:
        lines.extend(["", "## 后续可问", "", markdown_list(followups, empty_text="待补充")])
    lines.extend(
        [
            "",
            "## 来源回链",
            "",
            f"- 批次编号: {context['batch_id']}",
            f"- 片段编号: {chunk['chunk_id']}",
            f"- 图片范围: {chunk['source_refs']['image_start']} - {chunk['source_refs']['image_end']}",
            f"- 页段: {chunk['source_refs']['page_start']} - {chunk['source_refs']['page_end']}",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> int:
    args = parse_args()
    context_path = Path(args.context_json)
    context = normalize_context(load_json(context_path))
    batch_dir = context_path.parent
    dirs = ensure_learning_dirs(batch_dir)
    chunk_plan_path = Path(args.chunk_plan_json) if args.chunk_plan_json else dirs["chunk_plan"] / PLAN_JSON
    plan = load_json(chunk_plan_path)

    chunks = []
    for chunk in plan["chunks"]:
        chunk_json_path = dirs["chunk_extracts"] / f"{chunk['chunk_id']}.json"
        existing = load_json(chunk_json_path) if chunk_json_path.exists() else None
        payload = merge_chunk(existing, chunk, context)
        save_json(chunk_json_path, payload)
        (dirs["chunk_extracts"] / f"{chunk['chunk_id']}.md").write_text(render_chunk_md(payload), encoding="utf-8")
        chunks.append(payload)

        for concept in payload["core_concepts"]:
            name = concept.get("name", "").strip()
            if not name or is_placeholder(name):
                continue
            (dirs["cards"] / f"{name}.md").write_text(render_card(context, payload, concept), encoding="utf-8")

    (dirs["chapter_notes"] / CHAPTER_BODY).write_text(render_chapter_body(context, chunks), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
