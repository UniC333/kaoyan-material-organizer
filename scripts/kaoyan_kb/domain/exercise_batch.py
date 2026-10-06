from __future__ import annotations

import re
from typing import Any

from common import kb_layout, load_json_or_default
from kaoyan_kb.domain.exercise_locator import (
    load_exercise_locator_index,
    normalize_exercise_category,
    normalize_exercise_label,
    resolve_section_anchor,
)
from kaoyan_kb.domain.page_locator import load_page_locator_index, normalize_book_title


BATCH_CONTRACT_VERSION = "m6.exercise-batch.v1"
FOCUS_TOKENS = ("重点", "着重", "没理解", "不理解", "没懂", "不懂", "没透", "不熟")
PAGE_RANGE_PATTERN = re.compile(
    r"(?:第\s*)?(\d{1,4})\s*(?:~|～|—|–|-|至|到)\s*(?:第\s*)?(\d{1,4})\s*页",
    flags=re.IGNORECASE,
)
QUANTITY_PATTERN = re.compile(r"前\s*(\d{1,3})\s*(?:道|个)?题")
PAGE_START_PATTERN = re.compile(r"(?:第\s*)?(\d{1,4})\s*页\s*(?:起|开始|开头)")
CHOICE_LIST_PATTERN = re.compile(
    r"(?:错题|错误|选错的选项|选项)\s*[:：]?\s*"
    r"(?P<choices>(?:\d{1,3}\s*[A-Da-d]\s*(?:[、，,]\s*)?)+)"
    r"(?=$|[。；;\n]|[，,]\s*[\u4e00-\u9fff])"
)


def _numbers(value: str) -> list[str]:
    return [normalize_exercise_label(item) for item in re.findall(r"(?<![\d.])\d{1,3}(?![\d.])", value)]


def _requested_exercise_range(text: str) -> dict[str, int]:
    """Only a unique, explicit request limit may constrain listed exercises."""
    quantities: set[int] = set()
    for clause in re.split(r"[，,。；;\n]", text):
        for quantity in QUANTITY_PATTERN.finditer(clause):
            before, after = clause[:quantity.start()], clause[quantity.end():]
            limit = re.search(
                r"(?:(?:只|仅)(?:讲解|讲|看|复习|分析|检查)|限定(?:在|于)?|限于|范围为)\s*$",
                before,
            )
            wrong_answers = re.match(
                r"\s*(?:的\s*)?(?:错误|错题|选错的选项)\s*"
                r"(?:有(?:这些)?|是|如下)?\s*[:：]?\s*(?=$|(?:第\s*)?\d)", after,
            )
            if not limit and not wrong_answers:
                continue
            prefix = before[:limit.start()] if limit else before
            if re.search(r"(?:不|别|不要|无需|不用|不必|并非|不是)\s*$", prefix):
                continue
            # A historical limit is feedback unless an explicit current-request
            # marker follows it in this clause.
            markers = re.findall(r"之前|以前|此前|上次|昨天|曾经|这次|本次|现在|目前", before)
            if not wrong_answers and markers and markers[-1] in {"之前", "以前", "此前", "上次", "昨天", "曾经"}:
                continue
            count = int(quantity.group(1))
            if count > 0:
                quantities.add(count)
    return {"start": 1, "end": next(iter(quantities))} if len(quantities) == 1 else {}


def parse_exercise_batch_request(query: str) -> dict[str, Any]:
    """Parse a deterministic multi-exercise request without treating page/chapter numbers as labels."""
    text = str(query or "")
    exercise_range = _requested_exercise_range(text)
    start_match = PAGE_START_PATTERN.search(text)
    page_start = {"number": int(start_match.group(1)), "semantics": "section_start"} if start_match else {}
    page_match = PAGE_RANGE_PATTERN.search(text)
    page_range: dict[str, Any] = {}
    if page_match:
        start, end = int(page_match.group(1)), int(page_match.group(2))
        page_range = {
            "start": min(start, end),
            "end": max(start, end),
            "semantics": "question_scope",
        }

    selected: list[tuple[str, str]] = []
    invalid_options: dict[str, str] = {}
    choice_context = any(token in text for token in ("单选", "多选", "选择", "选项"))
    # Only split adjoining pairs inside a complete, explicitly introduced
    # answer list. Formula fragments retain the ordinary boundary checks.
    selection_text = CHOICE_LIST_PATTERN.sub(
        lambda match: match.group(0).replace(
            match.group('choices'),
            re.sub(r'([A-Da-d])\s*(?=\d)', r'\1、', match.group('choices')),
        ), text,
    )
    # A formula exponent/coefficient is not a question-option shorthand.
    # Lowercase letters and invalid options require explicit choice context.
    for match in re.finditer(r"(?<![A-Za-z0-9.^/\\*+\-=])(\d{1,3})\s*([A-Za-z])(?![A-Za-z0-9])", selection_text):
        if re.search(r"[\^/\\*+\-=]\s*$", selection_text[:match.start()]):
            continue
        if re.match(r"\s*[\^/\\*+\-=]", selection_text[match.end():]):
            continue
        if not choice_context and match.group(2) not in {"A", "B", "C", "D"}:
            continue
        label = normalize_exercise_label(match.group(1))
        option = match.group(2).upper()
        if option in {"A", "B", "C", "D"}:
            selected.append((label, option))
        elif (
            match.group(2).isupper()
            and re.search(r"(?:^|第|[、，,:：\s])$", selection_text[:match.start()])
            and re.match(r"(?:$|题|[、，,。；;\s])", selection_text[match.end():])
        ):
            invalid_options[label] = option

    listed: list[str] = []
    label_text = QUANTITY_PATTERN.sub(' ', text)
    for match in re.finditer(r"第\s*([0-9、，,和及与\s]+?)\s*(?:这|两|几|个)?\s*题", label_text):
        listed.extend(_numbers(match.group(1)))

    focus: list[str] = []
    for clause in re.split(r"[，,。；;\n]", label_text):
        if any(token in clause for token in FOCUS_TOKENS):
            # Only collect an explicit question list, never all numbers in a
            # clause that may also contain pages, subquestions or formulas.
            for match in re.finditer(r"(?<![\d.])(?:第\s*)?([0-9、和及与\s]+?)\s*(?:这|两|几|个)*\s*题", clause):
                focus.extend(_numbers(match.group(1)))

    ordered: list[str] = []
    option_by_label: dict[str, str] = {}
    for label, option in selected:
        if label not in ordered:
            ordered.append(label)
        option_by_label[label] = option
    for label in listed + focus + list(invalid_options):
        if label and label not in ordered:
            ordered.append(label)

    category = normalize_exercise_category(text)
    if selected and not category:
        category = "single-choice"
    focus_set = set(focus)
    items = [
        {
            "exercise_label": label,
            "requested_option": option_by_label.get(label, invalid_options.get(label, "")),
            "emphasis": label in focus_set,
            "parse_status": "invalid_option" if label in invalid_options else "valid",
        }
        for label in ordered
    ]
    return {
        "is_batch": len(items) >= 2,
        "page_range": page_range,
        "page_start": page_start,
        "exercise_range": exercise_range,
        "exercise_category": category,
        "items": items,
    }


def _active_pdf_sources(book_title: str) -> dict[str, dict[str, Any]]:
    layout = kb_layout()
    requested = normalize_book_title(book_title)
    return {
        str(item.get("source_id") or ""): item
        for path in layout["manifests"].joinpath("sources").glob("*.json")
        for item in [load_json_or_default(path, {})]
        if item.get("status") == "active"
        and item.get("material_type") == "book-pdf"
        and normalize_book_title(item.get("source_name")) == requested
        and str(item.get("source_id") or "")
    }


def _resolve_range_source(
    *,
    subject: str,
    book_title: str,
    page_range: dict[str, Any],
    allowed_source_ids: set[str],
) -> dict[str, Any]:
    index = load_page_locator_index()
    availability = dict(index.get("_availability") or {"available": True})
    if not availability.get("available", False):
        return {
            "status": "unavailable",
            "reason": str(availability.get("reason") or "page_locator_index_unavailable"),
            "source_id": "",
        }
    requested_title = normalize_book_title(book_title)
    source_ids: list[str] = []
    page_mappings: list[dict[str, int]] = []
    for printed_page in range(int(page_range["start"]), int(page_range["end"]) + 1):
        matches = {
            (
                str(item.get("source_id") or ""),
                int(item.get("pdf_page", 0) or 0),
                int(item.get("printed_page", 0) or 0),
            )
            for item in index.get("entries", []) or []
            if item.get("source_asset_kind") == "pdf"
            and str(item.get("subject") or "") == str(subject or "")
            and normalize_book_title(item.get("book_title")) == requested_title
            and str(item.get("source_id") or "") in allowed_source_ids
            and int(item.get("printed_page", 0) or 0) == printed_page
        }
        if len(matches) != 1:
            return {
                "status": "ambiguous" if matches else "not_found",
                "reason": "printed-page-range-not-uniquely-mapped",
                "source_id": "",
                "printed_page": printed_page,
            }
        source_id, pdf_page, mapped_printed = next(iter(matches))
        source_ids.append(source_id)
        page_mappings.append({"printed_page": mapped_printed, "pdf_page": pdf_page})
    if len(set(source_ids)) != 1:
        return {"status": "ambiguous", "reason": "printed-page-range-crosses-sources", "source_id": ""}
    pdf_pages = [item["pdf_page"] for item in page_mappings]
    if any(current != previous + 1 for previous, current in zip(pdf_pages, pdf_pages[1:])):
        return {
            "status": "unavailable",
            "reason": "printed-page-range-not-contiguous",
            "source_id": source_ids[0],
            "page_mappings": page_mappings,
        }
    return {
        "status": "exact",
        "reason": "",
        "source_id": source_ids[0],
        "page_mappings": page_mappings,
    }


def resolve_exercise_batch_targets(
    *,
    subject: str,
    book_title: str,
    chapter: str | None,
    query: str,
    parsed: dict[str, Any],
) -> dict[str, Any]:
    """Resolve each requested label to one formal relation while retaining per-item blockers."""
    if not book_title:
        return {
            "status": "blocked",
            "reason": "book-title-missing",
            "section_root": "",
            "source_id": "",
            "targets": [{**item, "status": "blocked", "reason": "book-title-missing"} for item in parsed.get("items", [])],
        }
    sources = _active_pdf_sources(book_title)
    if not sources:
        return {
            "status": "blocked",
            "reason": "active-book-pdf-source-not-found",
            "section_root": "",
            "source_id": "",
            "targets": [{**item, "status": "blocked", "reason": "active-book-pdf-source-not-found"} for item in parsed.get("items", [])],
        }
    section_anchor = resolve_section_anchor(book_title=book_title, chapter=str(chapter or ""), query=query)
    section_root = str(section_anchor.get("section_root") or "")
    if section_anchor.get("status") == "ambiguous":
        return {
            "status": "blocked",
            "reason": "section-anchor-ambiguous",
            "section_root": section_root,
            "source_id": "",
            "targets": [{**item, "status": "blocked", "reason": "section-anchor-ambiguous"} for item in parsed.get("items", [])],
        }

    page_range = dict(parsed.get("page_range") or {})
    range_resolution = {"status": "not_requested", "source_id": "", "page_mappings": []}
    allowed_source_ids = set(sources)
    if page_range:
        range_resolution = _resolve_range_source(
            subject=subject,
            book_title=book_title,
            page_range=page_range,
            allowed_source_ids=allowed_source_ids,
        )
        if range_resolution.get("status") != "exact":
            reason = str(range_resolution.get("reason") or "page-range-unavailable")
            return {
                "status": "blocked",
                "reason": reason,
                "section_root": section_root,
                "source_id": "",
                "page_range_resolution": range_resolution,
                "targets": [{**item, "status": "blocked", "reason": reason} for item in parsed.get("items", [])],
            }
        allowed_source_ids = {str(range_resolution.get("source_id") or "")}

    index = load_exercise_locator_index()
    availability = dict(index.get("_availability") or {"available": True})
    if not availability.get("available", False):
        reason = str(availability.get("reason") or "exercise_locator_index_unavailable")
        return {
            "status": "blocked",
            "reason": reason,
            "section_root": section_root,
            "source_id": str(range_resolution.get("source_id") or ""),
            "targets": [{**item, "status": "blocked", "reason": reason} for item in parsed.get("items", [])],
        }

    category = str(parsed.get("exercise_category") or "")
    page_start_resolution = {"status": "not_requested"}
    if parsed.get('page_start'):
        start_page = int(parsed['page_start']['number'])
        page_start_resolution = _resolve_range_source(
            subject=subject, book_title=book_title,
            page_range={'start': start_page, 'end': start_page}, allowed_source_ids=allowed_source_ids,
        )
        reason = str(page_start_resolution.get('reason') or '')
        if page_start_resolution.get('status') == 'exact':
            source_id = str(page_start_resolution['source_id'])
            pdf_page = page_start_resolution['page_mappings'][0]['pdf_page']
            anchors = load_json_or_default(kb_layout()['indexes'] / 'pdf_book_anchors' / f'{source_id}.json', {})
            candidates = {
                match.group(1)
                for anchor in anchors.get('anchors', []) or []
                if int(anchor.get('page_start', 0) or 0) == pdf_page
                for match in [re.match(r'^(\d+(?:\.\d+){2,})\s+.*本节试题精选', str(anchor.get('title') or ''))]
                if match
            }
            if len(candidates) != 1:
                reason = 'section-anchor-ambiguous' if candidates else 'section-start-anchor-not-found'
            else:
                heading = next(iter(candidates))
                anchored_section = heading.rsplit('.', 1)[0]
                requested_parts = section_root.split('.') if section_root else []
                anchored_parts = anchored_section.split('.')
                if requested_parts and anchored_parts[:len(requested_parts)] != requested_parts:
                    reason = 'section-start-anchor-conflict'
                else:
                    section_root = anchored_section
                    allowed_source_ids = {source_id}
                    page_start_resolution.update(section_root=section_root, requested_section=heading)
        if reason or page_start_resolution.get('status') != 'exact':
            reason = reason or 'section-start-anchor-not-found'
            return {
                'status': 'blocked', 'reason': reason, 'section_root': section_root, 'source_id': '',
                'page_start_resolution': {**page_start_resolution, 'status': 'blocked', 'reason': reason},
                'targets': [{**item, 'status': 'blocked', 'reason': reason} for item in parsed.get('items', [])],
            }
    page_numbers = set(range(int(page_range["start"]), int(page_range["end"]) + 1)) if page_range else set()
    targets: list[dict[str, Any]] = []
    used_sources: set[str] = set()
    for request_item in parsed.get("items", []) or []:
        if request_item.get("parse_status") != "valid":
            targets.append({**request_item, "status": "blocked", "reason": "invalid-requested-option"})
            continue
        label = normalize_exercise_label(request_item.get("exercise_label"))
        exercise_range = parsed.get('exercise_range') or {}
        if exercise_range and not int(exercise_range['start']) <= int(label) <= int(exercise_range['end']):
            targets.append({**request_item, 'status': 'blocked', 'reason': 'exercise-outside-requested-range'})
            continue
        matches = [
            item
            for item in index.get("relations", []) or []
            if str(item.get("source_id") or "") in allowed_source_ids
            and normalize_exercise_label(item.get("exercise_label")) == label
            and (not category or str(item.get("category") or "") == category)
            and (not section_root or str(item.get("section_root") or "") == section_root)
            and (
                not page_numbers
                or bool(page_numbers & {int(value) for value in item.get("question_printed_pages", []) or []})
            )
        ]
        exact = [item for item in matches if item.get("relation_status") == "exact"]
        if len(exact) == 1:
            relation = exact[0]
            source_id = str(relation.get("source_id") or "")
            used_sources.add(source_id)
            targets.append(
                {
                    **request_item,
                    "status": "exact",
                    "reason": "",
                    "relation": relation,
                    "printed_page": min(int(value) for value in relation.get("question_printed_pages", []) or []),
                }
            )
        elif len(exact) > 1:
            targets.append({**request_item, "status": "blocked", "reason": "exercise-relation-ambiguous"})
        elif matches:
            targets.append({**request_item, "status": "blocked", "reason": "exercise-relation-needs-review", "relation": matches[0]})
        else:
            targets.append({**request_item, "status": "blocked", "reason": "exercise-relation-not-found"})
    if not page_range and len(used_sources) > 1:
        targets = [{**item, "status": "blocked", "reason": "book-source-ambiguous", "relation": {}} for item in targets]
        used_sources.clear()
    source_id = str(range_resolution.get("source_id") or "") or (next(iter(used_sources)) if len(used_sources) == 1 else "")
    return {
        "status": "exact" if any(item.get("status") == "exact" for item in targets) else "blocked",
        "reason": "",
        "section_root": section_root,
        "section_anchor": section_anchor,
        "source_id": source_id,
        "page_range_resolution": range_resolution,
        "page_start_resolution": page_start_resolution,
        "targets": targets,
    }
