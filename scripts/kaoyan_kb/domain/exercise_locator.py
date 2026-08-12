from __future__ import annotations

import re
from collections import defaultdict
from typing import Any

from common import ensure_kb_layout, load_json_or_default, save_json


EXERCISE_LOCATOR_INDEX_NAME = "exercise_locator_index.json"
QUEUE_NAME = "exercise-locator"
WORKED_EXAMPLE_PATTERN = re.compile(r"【\s*例\s*(\d+(?:\.\d+)+)\s*】")
WORKED_SOLUTION_PATTERN = re.compile(r"【\s*(?:解|解答|分析与求解)\s*】")


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


def _grounded_printed_page_records(evidences: list[dict[str, Any]]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    seen: set[tuple[str, str, int, str]] = set()
    for evidence in evidences:
        if not evidence.get("source_grounded") or evidence.get("verification_status") not in {"reviewed", "source_grounded"} or evidence.get("mapping_status") == "stale":
            continue
        evidence_id = str(evidence.get("evidence_id") or "").strip()
        content = str(evidence.get("content") or "").strip()
        if not evidence_id or not content:
            continue
        for ref in evidence.get("page_classification_refs", []) or []:
            if not isinstance(ref, dict):
                continue
            book_id = str(ref.get("book_id") or "").strip()
            chapter_id = str(ref.get("chapter_id") or evidence.get("chapter_id") or "").strip()
            printed_page = ref.get("printed_page")
            if not book_id or not chapter_id or printed_page is None:
                continue
            key = (book_id, chapter_id, int(printed_page), evidence_id)
            if key in seen:
                continue
            seen.add(key)
            records.append({
                "book_id": book_id,
                "book_title": str(ref.get("book_title") or evidence.get("book_title") or "").strip(),
                "chapter_id": chapter_id,
                "chapter_title": str(ref.get("chapter_title") or evidence.get("chapter_title") or "").strip(),
                "printed_page": int(printed_page),
                "source_image_path": str(ref.get("source_image_path") or "").strip(),
                "source_id": str(ref.get("source_id") or evidence.get("source_id") or "").strip(),
                "evidence_id": evidence_id,
                "content": content,
            })
    return records


def _span_side(intervals: list[tuple[int, int, dict[str, Any]]], start: int, end: int, content: str) -> dict[str, Any]:
    selected = [record for left, right, record in intervals if left < end and right > start]
    evidence_ids = list(dict.fromkeys(str(item["evidence_id"]) for item in selected))
    printed_pages = sorted({int(item["printed_page"]) for item in selected})
    image_paths = list(dict.fromkeys(str(item.get("source_image_path") or "") for item in selected if item.get("source_image_path")))
    first = selected[0] if selected else {}
    return {
        "book_id": str(first.get("book_id") or ""),
        "book_title": str(first.get("book_title") or ""),
        "source_id": str(first.get("source_id") or ""),
        "evidence_ids": evidence_ids,
        "printed_pages": printed_pages,
        "source_image_path": image_paths[0] if image_paths else "",
        "source_image_paths": image_paths,
        "content": content[start:end].strip(),
    }


def build_worked_example_relations(evidences: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Derive unique same-book example/solution relations from grounded page evidence."""
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for record in _grounded_printed_page_records(evidences):
        grouped[(record["book_id"], record["chapter_id"])].append(record)

    candidates: list[dict[str, Any]] = []
    for (book_id, chapter_id), records in grouped.items():
        records.sort(key=lambda item: (int(item["printed_page"]), str(item["evidence_id"])))
        parts: list[str] = []
        intervals: list[tuple[int, int, dict[str, Any]]] = []
        cursor = 0
        for record in records:
            start = cursor
            parts.append(record["content"])
            cursor += len(record["content"])
            intervals.append((start, cursor, record))
            parts.append("\n")
            cursor += 1
        combined = "".join(parts)
        examples = list(WORKED_EXAMPLE_PATTERN.finditer(combined))
        for index, marker in enumerate(examples):
            segment_end = examples[index + 1].start() if index + 1 < len(examples) else len(combined)
            solution_markers = list(WORKED_SOLUTION_PATTERN.finditer(combined, marker.end(), segment_end))
            solution_start = solution_markers[0].start() if solution_markers else segment_end
            relation_status = "exact" if len(solution_markers) == 1 else "needs_review" if solution_markers else "question_only"
            exercise_label = f"例{marker.group(1)}"
            candidates.append({
                "relation_id": f"EXW-{book_id}-{chapter_id}-{exercise_label}",
                "relation_kind": "same-book-worked-example",
                "relation_status": relation_status,
                "book_id": book_id,
                "book_title": records[0].get("book_title", ""),
                "chapter_id": chapter_id,
                "chapter_title": records[0].get("chapter_title", ""),
                "exercise_label": exercise_label,
                "question": _span_side(intervals, marker.start(), solution_start, combined),
                "answer": _span_side(intervals, solution_start, segment_end, combined) if solution_markers else {},
            })

    grouped_candidates: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for candidate in candidates:
        grouped_candidates[(candidate["book_id"], candidate["chapter_id"], candidate["exercise_label"])].append(candidate)
    relations: list[dict[str, Any]] = []
    for items in grouped_candidates.values():
        relation = dict(items[0])
        if len(items) != 1:
            relation["relation_status"] = "needs_review"
            relation["candidates"] = items
        relations.append(relation)
    return sorted(relations, key=lambda item: item["relation_id"])


def build_exercise_locator_index() -> dict[str, Any]:
    layout = ensure_kb_layout()
    all_evidences = [load_json_or_default(path, {}) for path in sorted(layout["evidence"].glob("*.json"))]
    sources = {
        str(item.get("source_id") or ""): item
        for path in layout["manifests"].joinpath("sources").glob("*.json")
        for item in [load_json_or_default(path, {})]
        if item.get("status") == "active" and item.get("material_type") == "book-pdf"
    }
    pages_by_source: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for evidence in all_evidences:
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
    relations.extend(build_worked_example_relations(all_evidences))
    payload = {"schema_version": "exercise-locator.v1", "relations": sorted(relations, key=lambda item: item["relation_id"]), "summary": {"relation_count": len(relations), "review_count": sum(len(items) for items in review_by_source.values()) + sum(item.get("relation_status") == "needs_review" for item in relations)}}
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


def find_exact_worked_example_relation(*, book_id: str, printed_page: int, exercise_label: str) -> dict[str, Any]:
    matches = [
        item for item in load_exercise_locator_index().get("relations", [])
        if item.get("relation_kind") == "same-book-worked-example"
        and item.get("book_id") == book_id
        and item.get("exercise_label") == exercise_label.replace(" ", "")
        and printed_page in set((item.get("question") or {}).get("printed_pages", []))
    ]
    return matches[0] if len(matches) == 1 else {}
