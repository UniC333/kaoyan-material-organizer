from __future__ import annotations

import re
from collections import defaultdict
from typing import Any

from common import ensure_kb_layout, load_json_or_default, save_json


EXERCISE_LOCATOR_INDEX_NAME = "exercise_locator_index.json"
QUEUE_NAME = "exercise-locator"


def normalize_exercise_label(value: Any) -> str:
    match = re.search(r"(?:第\s*)?(\d{1,3})(?:\s*题)?", str(value or ""))
    return f"{int(match.group(1)):02d}" if match else ""


def _root(section: str) -> str:
    parts = section.split(".")
    return ".".join(parts[:-1]) if len(parts) > 1 else section


def _category(text: str) -> str:
    if "综合应用题" in text:
        return "comprehensive"
    if "单项选择题" in text:
        return "single-choice"
    return ""


def _heading(line: str) -> tuple[str, str] | None:
    match = re.match(r"^#{1,6}\s*(\d+(?:\.\d+)+)\s*(.*)$", line.strip())
    return (match.group(1), match.group(2).strip()) if match else None


def _label(line: str, *, answer: bool, category: str) -> str:
    pattern = r"^\s*#?\s*(\d{1,3})[.．、]\s*"
    match = re.match(pattern, line)
    if not match:
        return ""
    if answer and not (line.lstrip().startswith("#") or "【解答】" in line or "【解析】" in line):
        # A single-choice answer is commonly emitted as plain `01. C` by OCR.
        if category != "single-choice" or not re.match(r"^\s*\d{1,3}[.．、]\s*[A-D](?:\s|$)", line):
            return ""
    return normalize_exercise_label(match.group(1))


def _unique_occurrences(items: list[dict[str, Any]], *, page_key: str) -> list[dict[str, Any]]:
    unique: dict[tuple[tuple[str, ...], tuple[int, ...]], dict[str, Any]] = {}
    for item in items:
        evidence_key = tuple(str(value) for value in item.get("question_evidence_ids" if page_key == "question_pdf_pages" else "answer_evidence_ids", []) or [])
        pages = tuple(int(value) for value in item.get(page_key, []) or [])
        unique[(evidence_key, pages)] = item
    return list(unique.values())


def build_exercise_locator_index() -> dict[str, Any]:
    layout = ensure_kb_layout()
    sources = {
        str(item.get("source_id") or ""): item
        for path in layout["manifests"].joinpath("sources").glob("*.json")
        for item in [load_json_or_default(path, {})]
        if item.get("status") == "active" and item.get("material_type") == "book-pdf"
    }
    pages_by_source: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for path in sorted(layout["evidence"].glob("*.json")):
        evidence = load_json_or_default(path, {})
        source_id = str(evidence.get("source_id") or "")
        if source_id not in sources or evidence.get("origin_type") != "pdf_page_ocr":
            continue
        if evidence.get("verification_status") != "reviewed" or not evidence.get("source_grounded") or evidence.get("mapping_status") == "stale":
            continue
        page = int((evidence.get("locator") or {}).get("page_start") or 0)
        if page:
            pages_by_source[source_id].append(evidence)

    relations: list[dict[str, Any]] = []
    review_by_source: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for source_id, pages in pages_by_source.items():
        queue_path = layout["review_queues"] / QUEUE_NAME / f"{source_id}.json"
        existing_queue = load_json_or_default(queue_path, {})
        approved_relations = [item for item in existing_queue.get("approved_relations", []) or [] if isinstance(item, dict)]
        approved_by_key = {
            (str(item.get("section_root") or ""), str(item.get("category") or ""), normalize_exercise_label(item.get("exercise_label"))): item
            for item in approved_relations
            if item.get("question_evidence_ids") and item.get("answer_evidence_ids")
        }
        questions: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
        answers: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
        mode = ""
        root = ""
        category = ""
        active_question: dict[str, Any] | None = None
        active_answer: dict[str, Any] | None = None
        for evidence in sorted(pages, key=lambda item: int((item.get("locator") or {}).get("page_start") or 0)):
            page = int((evidence.get("locator") or {}).get("page_start") or 0)
            lines = str(evidence.get("content") or "").splitlines()
            for line in lines:
                marker = _heading(line)
                if marker:
                    section, title = marker
                    if "本节试题精选" in title:
                        mode, root, category, active_question, active_answer = "question", _root(section), "", None, None
                    elif "答案与解析" in title:
                        mode, root, category, active_question, active_answer = "answer", _root(section), "", None, None
                    elif mode and _root(section) != root:
                        # A new sibling section ends the preceding exercise set;
                        # never carry answer labels into the next section.
                        mode, root, category, active_question, active_answer = "", "", "", None, None
                plain_heading = re.sub(r"^#+\s*", "", line.strip())
                if mode and plain_heading.startswith(("归纳总结", "思维拓展", "提示", "购买王道书")):
                    # These headings can follow an answer section on the same
                    # page. Their numbered paragraphs are not exercise labels.
                    mode, root, category, active_question, active_answer = "", "", "", None, None
                    continue
                found_category = _category(line)
                if found_category:
                    category = found_category
                if not mode or not root or not category:
                    continue
                label = _label(line, answer=mode == "answer", category=category)
                if not label:
                    continue
                key = (root, category, label)
                if mode == "question":
                    active_question = {"source_id": source_id, "section_root": root, "category": category, "exercise_label": label, "question_evidence_ids": [evidence.get("evidence_id", "")], "question_pdf_pages": [page]}
                    questions[key].append(active_question)
                else:
                    active_answer = {"source_id": source_id, "section_root": root, "category": category, "exercise_label": label, "answer_evidence_ids": [evidence.get("evidence_id", "")], "answer_pdf_pages": [page]}
                    answers[key].append(active_answer)
            if active_question and mode == "question" and page not in active_question["question_pdf_pages"]:
                active_question["question_pdf_pages"].append(page)
                active_question["question_evidence_ids"].append(evidence.get("evidence_id", ""))
            if active_answer and mode == "answer" and page not in active_answer["answer_pdf_pages"]:
                active_answer["answer_pdf_pages"].append(page)
                active_answer["answer_evidence_ids"].append(evidence.get("evidence_id", ""))
        for key in sorted(set(questions) | set(answers)):
            q_items = _unique_occurrences(questions.get(key, []), page_key="question_pdf_pages")
            a_items = _unique_occurrences(answers.get(key, []), page_key="answer_pdf_pages")
            approved = approved_by_key.get(key)
            if approved:
                relations.append({"relation_id": str(approved.get("relation_id") or f"EXR-{source_id}-{key[0]}-{key[1]}-{key[2]}"), "relation_status": "exact", "source_id": source_id, **approved})
                continue
            if len(q_items) == len(a_items) == 1:
                question, answer = q_items[0], a_items[0]
                relations.append({
                    "relation_id": f"EXR-{source_id}-{key[0]}-{key[1]}-{key[2]}",
                    "relation_status": "exact",
                    **question,
                    **answer,
                })
            else:
                review_by_source[source_id].append({"kind": "exercise-relation-ambiguous", "section_root": key[0], "category": key[1], "exercise_label": key[2], "question_candidates": q_items, "answer_candidates": a_items})
    for source_id in sources:
        items = review_by_source[source_id]
        queue_path = layout["review_queues"] / QUEUE_NAME / f"{source_id}.json"
        existing_queue = load_json_or_default(queue_path, {})
        save_json(queue_path, {"queue_type": QUEUE_NAME, "source_id": source_id, "approved_relations": list(existing_queue.get("approved_relations", []) or []), "items": items, "summary": {"open_count": len(items)}})
    payload = {"schema_version": "exercise-locator.v1", "relations": sorted(relations, key=lambda item: item["relation_id"]), "summary": {"relation_count": len(relations), "review_count": sum(len(items) for items in review_by_source.values())}}
    save_json(layout["indexes"] / EXERCISE_LOCATOR_INDEX_NAME, payload)
    return payload


def load_exercise_locator_index() -> dict[str, Any]:
    return load_json_or_default(ensure_kb_layout()["indexes"] / EXERCISE_LOCATOR_INDEX_NAME, {"relations": []})


def find_exact_relation(*, source_id: str, question_pdf_page: int, exercise_label: str) -> dict[str, Any]:
    label = normalize_exercise_label(exercise_label)
    matches = [item for item in load_exercise_locator_index().get("relations", []) if item.get("source_id") == source_id and label == item.get("exercise_label") and question_pdf_page in set(item.get("question_pdf_pages", []))]
    return matches[0] if len(matches) == 1 and matches[0].get("relation_status") == "exact" else {}


def find_unique_relation_for_scope(*, book_title: str, chapter: str, exercise_label: str) -> dict[str, Any]:
    """Resolve a relation without a page only when book and current chapter are unique."""
    if not book_title or not chapter:
        return {}
    label = normalize_exercise_label(exercise_label)
    section_number = re.search(r"(?:第\s*)?(\d+(?:\.\d+)+)\s*(?:节)?", chapter)
    chapter_number = re.search(r"(?:第\s*)?(\d+)\s*章", chapter)
    if not label or (not section_number and not chapter_number):
        return {}
    scope = section_number.group(1) if section_number else chapter_number.group(1)
    layout = ensure_kb_layout()
    sources = {
        str(item.get("source_id") or ""): str(item.get("source_name") or "")
        for path in layout["manifests"].joinpath("sources").glob("*.json")
        for item in [load_json_or_default(path, {})]
        if item.get("status") == "active" and item.get("material_type") == "book-pdf"
    }
    matches = [
        item for item in load_exercise_locator_index().get("relations", [])
        if item.get("relation_status") == "exact"
        and normalize_exercise_label(item.get("exercise_label")) == label
        and sources.get(str(item.get("source_id") or "")) == book_title
        and (
            str(item.get("section_root") or "") == scope
            if section_number
            else str(item.get("section_root") or "").split(".", 1)[0] == scope
        )
    ]
    return matches[0] if len(matches) == 1 else {}
