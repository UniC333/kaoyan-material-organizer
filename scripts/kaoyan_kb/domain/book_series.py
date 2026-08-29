from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from common import ensure_kb_layout, load_json_or_default, save_json
from config import load_runtime_config
from kaoyan_kb.domain.exercise_locator import container_path_from_query, normalize_container_label, stable_container_label


BOOK_SERIES_INDEX_NAME = "book_series_index.json"
EXERCISE_PAIR_INDEX_NAME = "exercise_pair_index.json"
EXERCISE_TYPES = {"choice", "fill_blank", "worked"}
WORKED_EXAMPLE_PATTERN = re.compile(
    r"(?m)^\s*(?:#{1,6}\s*)?(?:【\s*|\[\s*)?例\s*([0-9]+(?:\.[0-9]+)*)?(?:\s*】|\s*\]|(?=\s|[：:、.．▶►]|$))"
)
WORKED_SOLUTION_PATTERN = re.compile(
    r"(?m)^\s*(?:#{1,6}\s*)?(?:【\s*)?(?:解|解答|证明|分析与求解)(?:\s*】|(?=\s|[：:、.．]|$))"
)
MARKDOWN_HEADING_PATTERN = re.compile(r"(?m)^\s*#{1,6}\s+")
WORKED_PAGE_GAP_PATTERN = re.compile(r"(?m)^\s*#\s+__NONCONTIGUOUS_PAGE_GAP__\s*$")
WORKED_ANSWER_BOUNDARY_PATTERN = re.compile(
    r"(?m)^\s*(?:(?:定理|定义)\s*\d*|\(\s*\d+\s*\)\s*若函数|本章学习诊断)"
)
CONTAINER_HEADING_PATTERN = re.compile(
    r"^(?:题型|专题|方法|模型|考点|类型)\s*(?:[0-9]+|[零一二三四五六七八九十两]+)(?:\s*[：:、-]?\s*.*)?$"
)
SOURCE_REQUEST_TOKENS = ("教材", "讲义", "题集", "题目照片", "照片", "图中", "书上", "书里", "原书")
SELF_AUTHORED_TOKENS = ("自拟", "我编", "原创题")
PAGE_CONTENT_TOKENS = ("理解", "没看懂", "看不懂", "不明白", "解释", "讲解", "怎么来的", "为什么", "代码", "公式", "定义", "段落", "原文", "这一页", "这里")
EXERCISE_REQUEST_PATTERN = re.compile(
    r"(?:这|那|该|本)\s*(?:道)?\s*题|题目照片|原题|题解|解答|答案|"
    r"怎么做|如何作答|求解|解题|怎么\s*解(?!释)|如何\s*解(?!释)|"
    r"检查(?:一下)?(?:这道题|该题|本题|[^，。！？]*过程)|核对[^，。！？]*过程|"
    r"(?:第\s*)?\d+\s*(?:题|问)|例题|例\s*\d+(?:\.\d+)*|"
    r"选择题|填空题|解答题|证明题|计算题|选项|证明过程|计算过程|计算结果"
)


def _container_heading(line: str) -> str:
    plain = re.sub(r"^\s*#{1,6}\s*", "", str(line or "").strip())
    return plain if CONTAINER_HEADING_PATTERN.fullmatch(plain) else ""


def normalize_alias(value: Any) -> str:
    text = str(value or "").strip().lower()
    return re.sub(r"[\s·・:：_\-—（）()《》]+", "", text)


def exercise_key(
    series_id: str,
    stage: str,
    part: str,
    chapter_number: int,
    exercise_type: str,
    exercise_number: int,
) -> str:
    if exercise_type not in EXERCISE_TYPES:
        raise ValueError(f"unsupported exercise type: {exercise_type}")
    return f"{series_id}|{stage}|{part}-{chapter_number:02d}|{exercise_type}|{exercise_number}"


def _load_series_files() -> list[tuple[Path, dict[str, Any]]]:
    runtime = load_runtime_config()
    records: list[tuple[Path, dict[str, Any]]] = []
    if not runtime.vault_root.is_dir():
        return records
    for path in sorted(runtime.vault_root.rglob("series.json")):
        payload = load_json_or_default(path, {})
        if payload.get("schema_version") == "book-series.v1" and payload.get("series_id"):
            records.append((path, payload))
    return records


def build_book_series_index() -> dict[str, Any]:
    layout = ensure_kb_layout()
    records: list[dict[str, Any]] = []
    for path, payload in _load_series_files():
        root = path.parent.resolve()
        volumes: list[dict[str, Any]] = []
        for raw in payload.get("volumes", []):
            if not isinstance(raw, dict):
                continue
            volume = dict(raw)
            configured_root = Path(str(volume.get("root") or ""))
            volume_root = configured_root if configured_root.is_absolute() else root / configured_root
            volume["root"] = str(volume_root.resolve())
            volumes.append(volume)
        aliases = [str(item).strip() for item in payload.get("aliases", []) if str(item).strip()]
        canonical_title = str(payload.get("canonical_title") or "").strip()
        records.append(
            {
                "series_id": str(payload["series_id"]),
                "canonical_title": canonical_title,
                "normalized_title": normalize_alias(canonical_title),
                "aliases": aliases,
                "normalized_aliases": sorted({normalize_alias(item) for item in [canonical_title, *aliases] if item}),
                "subject": str(payload.get("subject") or ""),
                "exam_variant": str(payload.get("exam_variant") or ""),
                "series_root": str(root),
                "series_path": str(path.resolve()),
                "volumes": volumes,
                "pairings": list(payload.get("pairings", [])),
            }
        )
    result = {
        "schema_version": "book-series-index.v1",
        "series": sorted(records, key=lambda item: item["series_id"]),
        "summary": {"series_count": len(records), "volume_count": sum(len(item["volumes"]) for item in records)},
    }
    save_json(layout["indexes"] / BOOK_SERIES_INDEX_NAME, result)
    return result


def load_book_series_index() -> dict[str, Any]:
    runtime = load_runtime_config()
    return load_json_or_default(runtime.kb_root / "indexes" / BOOK_SERIES_INDEX_NAME, {"series": []})


def load_exercise_pair_index() -> dict[str, Any]:
    runtime = load_runtime_config()
    return load_json_or_default(runtime.kb_root / "indexes" / EXERCISE_PAIR_INDEX_NAME, {"items": []})


def _detect_stage(text: str) -> str:
    if "强化" in text or "advanced" in text.lower():
        return "advanced"
    if "基础" in text or "basic" in text.lower():
        return "basic"
    return ""


def _detect_role(text: str) -> str:
    if any(token in text for token in ("答案", "解析", "题解", "书解")):
        return "paired_answer"
    if any(token in text for token in ("原题", "题目", "怎么做")):
        return "question"
    return ""


def _detect_part(text: str) -> str:
    if any(token in text for token in ("高数", "高等数学")):
        return "CALC"
    if any(token in text for token in ("线代", "线性代数")):
        return "LA"
    if any(token in text for token in ("概率", "概率统计")):
        return "PROB"
    return ""


def _detect_exercise_type(text: str) -> str:
    if "选择" in text:
        return "choice"
    if "填空" in text:
        return "fill_blank"
    if any(token in text for token in ("解答", "计算题", "证明题")):
        return "worked"
    return ""


def _number_after(pattern: str, text: str) -> int | None:
    match = re.search(pattern, text, flags=re.IGNORECASE)
    return int(match.group(1)) if match else None


def _chinese_number(token: str) -> int | None:
    digits = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
    if token.isdigit():
        return int(token)
    if token == "十":
        return 10
    if "十" in token:
        head, tail = token.split("十", 1)
        tens = digits.get(head, 1) if head else 1
        ones = digits.get(tail, 0) if tail else 0
        return tens * 10 + ones
    return digits.get(token)


def parse_exercise_request(query: str) -> dict[str, Any]:
    text = str(query or "")
    example_match = re.search(r"例\s*([0-9]+(?:\.[0-9]+)*)", text)
    exercise_label = f"例{example_match.group(1)}" if example_match else ""
    chapter_match = re.search(r"第\s*([0-9零一二三四五六七八九十两]+)\s*章", text)
    chapter_number = _chinese_number(chapter_match.group(1)) if chapter_match else None
    exercise_number = _number_after(r"第\s*(\d+)\s*题", text)
    if exercise_number is None:
        exercise_number = _number_after(r"(?:选择题|填空题|解答题|题)\s*(\d+)\b", text)
    page_match = re.search(r"第\s*(\d+)\s*页", text)
    if not page_match:
        page_match = re.search(r"(?<![A-Za-z0-9])P\s*(\d+)(?![0-9])", text, flags=re.IGNORECASE)
    page_number = int(page_match.group(1)) if page_match else None
    return {
        "stage": _detect_stage(text),
        "role_intent": _detect_role(text),
        "part": _detect_part(text),
        "chapter_number": chapter_number,
        "exercise_type": _detect_exercise_type(text),
        "exercise_number": exercise_number,
        "exercise_label": exercise_label,
        "printed_page": page_number,
    }


def has_exercise_request_signal(
    query: str,
    *,
    exercise_label: str = "",
    exercise_number: int | None = None,
    requested_option: str = "",
) -> bool:
    """Return whether the request identifies or asks to solve a sourced exercise."""
    text = str(query or "")
    return bool(
        str(exercise_label or "").strip()
        or exercise_number is not None
        or str(requested_option or "").strip()
        or EXERCISE_REQUEST_PATTERN.search(text)
    )


def classify_source_request(
    *,
    query: str,
    book_title: str | None,
    page_anchor: dict[str, Any],
    exercise_label: str = "",
    exercise_number: int | None = None,
    requested_option: str = "",
    book_route: dict[str, Any] | None = None,
) -> str:
    """Classify source-grounded requests without treating ``理解/解释`` as answers."""
    text = str(query or "")
    if any(token in text for token in SELF_AUTHORED_TOKENS):
        return "generic"
    explicit_source = bool(
        book_title
        or page_anchor.get("requested_page") is not None
        or page_anchor.get("book_id")
        or (book_route or {}).get("series_id")
        or any(token in text for token in SOURCE_REQUEST_TOKENS)
    )
    if not explicit_source:
        return "generic"
    if has_exercise_request_signal(
        text,
        exercise_label=exercise_label or str(page_anchor.get("requested_exercise_label") or ""),
        exercise_number=exercise_number,
        requested_option=requested_option,
    ):
        return "exercise"
    if page_anchor.get("requested_page") is not None or any(token in text for token in PAGE_CONTENT_TOKENS):
        return "page_content"
    return "generic"


def _grounded_page_records(evidences: list[dict[str, Any]]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    seen: set[tuple[str, str, int, str]] = set()
    for evidence in evidences:
        if evidence.get("verification_status") != "source_grounded" or not bool(evidence.get("source_grounded")):
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
            records.append(
                {
                    "book_id": book_id,
                    "book_title": str(ref.get("book_title") or evidence.get("book_title") or "").strip(),
                    "chapter_id": chapter_id,
                    "chapter_title": str(ref.get("chapter_title") or evidence.get("chapter_title") or "").strip(),
                    "printed_page": int(printed_page),
                    "source_image_path": str(ref.get("source_image_path") or "").strip(),
                    "evidence_id": evidence_id,
                    "content": content,
                }
            )
    return records


def _span_side(records: list[dict[str, Any]], intervals: list[tuple[int, int, dict[str, Any]]], start: int, end: int, content: str) -> dict[str, Any]:
    selected = [record for left, right, record in intervals if left < end and right > start]
    evidence_ids = list(dict.fromkeys(str(item["evidence_id"]) for item in selected))
    printed_pages = sorted({int(item["printed_page"]) for item in selected})
    source_image_paths = list(dict.fromkeys(str(item.get("source_image_path") or "") for item in selected if item.get("source_image_path")))
    first = selected[0] if selected else (records[0] if records else {})
    return {
        "book_id": str(first.get("book_id") or ""),
        "book_title": str(first.get("book_title") or ""),
        "evidence_id": evidence_ids[0] if evidence_ids else "",
        "evidence_ids": evidence_ids,
        "printed_pages": printed_pages,
        "source_image_path": source_image_paths[0] if source_image_paths else "",
        "source_image_paths": source_image_paths,
        "content": content[start:end].strip(),
    }


def worked_example_pairs_from_evidence(evidences: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Build fail-closed same-book example/solution pairs from reviewed page evidence."""
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for record in _grounded_page_records(evidences):
        grouped.setdefault((record["book_id"], record["chapter_id"]), []).append(record)

    candidates: list[dict[str, Any]] = []
    for (book_id, chapter_id), records in grouped.items():
        records.sort(key=lambda item: (int(item["printed_page"]), str(item["evidence_id"])))
        parts: list[str] = []
        intervals: list[tuple[int, int, dict[str, Any]]] = []
        cursor = 0
        previous_page: int | None = None
        for record in records:
            current_page = int(record["printed_page"])
            if previous_page is not None and current_page > previous_page + 1:
                gap_text = "# __NONCONTIGUOUS_PAGE_GAP__\n"
                parts.append(gap_text)
                cursor += len(gap_text)
            page_text = str(record["content"])
            start = cursor
            parts.append(page_text)
            cursor += len(page_text)
            intervals.append((start, cursor, record))
            parts.append("\n")
            cursor += 1
            previous_page = current_page
        combined = "".join(parts)
        examples = list(WORKED_EXAMPLE_PATTERN.finditer(combined))
        unnumbered_by_page: dict[int, int] = {}
        container_headings = [
            (match.start(), _container_heading(match.group(0)))
            for match in re.finditer(r"(?m)^.*$", combined)
            if _container_heading(match.group(0))
        ]
        ordinals: dict[tuple[int, tuple[str, ...], str], int] = {}
        for index, marker in enumerate(examples):
            segment_end = examples[index + 1].start() if index + 1 < len(examples) else len(combined)
            page_gap = WORKED_PAGE_GAP_PATTERN.search(combined, marker.end(), segment_end)
            if page_gap:
                segment_end = page_gap.start()
            solution_markers = list(WORKED_SOLUTION_PATTERN.finditer(combined, marker.end(), segment_end))
            if solution_markers:
                boundary_matches = [
                    match
                    for match in (
                        MARKDOWN_HEADING_PATTERN.search(combined, solution_markers[0].end(), segment_end),
                        WORKED_ANSWER_BOUNDARY_PATTERN.search(combined, solution_markers[0].end(), segment_end),
                    )
                    if match
                ]
                if boundary_matches:
                    segment_end = min(match.start() for match in boundary_matches)
                    solution_markers = list(WORKED_SOLUTION_PATTERN.finditer(combined, marker.end(), segment_end))
            if len(solution_markers) == 1:
                solution_start = solution_markers[0].start()
                pair_status = "exact_pair"
                question_end = solution_start
            elif not solution_markers:
                pair_status = "question_only"
                question_end = segment_end
                solution_start = segment_end
            else:
                pair_status = "needs_review"
                question_end = solution_markers[0].start()
                solution_start = solution_markers[0].start()
            question = _span_side(records, intervals, marker.start(), question_end, combined)
            solution = _span_side(records, intervals, solution_start, segment_end, combined) if solution_markers else {}
            question_pages = list(question.get("printed_pages", []))
            page_token = min(question_pages) if question_pages else 0
            printed_number = str(marker.group(1) or "").strip()
            if printed_number:
                exercise_label = f"例{printed_number}"
            else:
                unnumbered_by_page[page_token] = unnumbered_by_page.get(page_token, 0) + 1
                exercise_label = f"例（P{page_token}页内{unnumbered_by_page[page_token]}）"
            container_path = [heading for offset, heading in container_headings if offset < marker.start()][-1:]
            ordinal_key = (page_token, tuple(stable_container_label(item) for item in container_path), exercise_label)
            ordinals[ordinal_key] = ordinals.get(ordinal_key, 0) + 1
            container_ordinal = ordinals[ordinal_key]
            path_key = "/".join(stable_container_label(item) for item in container_path) or "root"
            exercise_key = (
                f"worked:{book_id}|{chapter_id}|p{page_token}|{exercise_label}"
                if not container_path and container_ordinal == 1
                else f"worked:{book_id}|{chapter_id}|p{page_token}|{path_key}|{exercise_label}|{container_ordinal}"
            )
            candidates.append(
                {
                    # Handouts commonly restart at 例1 in each subsection. The
                    # first question page keeps those labels distinct while the
                    # public exercise label remains the one printed in the book.
                    "exercise_key": exercise_key,
                    "pair_kind": "same_book_worked_example",
                    "series_id": "",
                    "book_id": book_id,
                    "book_title": question.get("book_title", ""),
                    "chapter_id": chapter_id,
                    "chapter_title": records[0].get("chapter_title", ""),
                    "exercise_label": exercise_label,
                    "container_path": container_path,
                    "container_ordinal": container_ordinal,
                    "location_key": f"worked:{book_id}|{chapter_id}|p{page_token}|{path_key}|{exercise_label}|{container_ordinal}",
                    "exercise_type": "worked",
                    "pair_status": pair_status,
                    "question": question,
                    "solution": solution,
                }
            )

    by_key: dict[str, list[dict[str, Any]]] = {}
    for item in candidates:
        by_key.setdefault(str(item["exercise_key"]), []).append(item)
    result: list[dict[str, Any]] = []
    for items in by_key.values():
        if len(items) == 1:
            result.append(items[0])
            continue
        first = dict(items[0])
        first["pair_status"] = "needs_review"
        first["candidates"] = items
        result.append(first)
    return sorted(result, key=lambda item: (str(item.get("book_id")), str(item.get("chapter_id")), str(item.get("exercise_label"))))


def _answer_grounding_base(*, required: bool) -> dict[str, Any]:
    return {
        "required": required,
        "status": "answer_not_found" if required else "not_applicable",
        "can_conclude": not required,
        "problem": {},
        "solution": {},
        "failure_reason": "" if not required else "尚未定位原书题解。",
        "next_action": "" if not required else "请补充教材名、页码或题号后重新检索原书答案。",
    }


def resolve_answer_grounding(
    *,
    query: str,
    book_title: str | None,
    page_anchor: dict[str, Any],
    book_route: dict[str, Any],
    exercise_route: dict[str, Any],
) -> dict[str, Any]:
    request = parse_exercise_request(query)
    request_kind = classify_source_request(
        query=query,
        book_title=book_title,
        page_anchor=page_anchor,
        exercise_label=str(request.get("exercise_label") or ""),
        exercise_number=request.get("exercise_number"),
        book_route=book_route,
    )
    sourced = request_kind == "exercise"
    grounding = _answer_grounding_base(required=sourced)
    if not sourced:
        return grounding

    page_status = str(page_anchor.get("match_status") or "")
    if page_status == "unavailable":
        grounding.update(
            status="answer_unavailable",
            failure_reason="本地证据链当前不可用，无法定位原题和原书答案。",
            next_action="请先修复配置或正式页码索引。",
        )
        return grounding
    if page_status == "ambiguous":
        grounding.update(
            status="answer_ambiguous",
            failure_reason="原题页存在多个教材候选，无法唯一配对原书答案。",
            next_action="请先确认教材名称。",
        )
        return grounding

    if exercise_route.get("match_status") == "exact_exercise":
        pair_status = str(exercise_route.get("pair_status") or "")
        grounding["problem"] = dict(exercise_route.get("question") or {})
        grounding["solution"] = dict(exercise_route.get("solution") or {})
        if pair_status == "exact_pair" and grounding["problem"] and grounding["solution"]:
            grounding.update(status="exact_answer", can_conclude=True, failure_reason="", next_action="")
        elif pair_status == "needs_review":
            grounding.update(status="answer_ambiguous", failure_reason="题目与题解存在多个候选，尚未形成唯一配对。", next_action="请先审核题目—题解关系。")
        else:
            grounding.update(failure_reason="原题已定位，但原书题解尚未接入。", next_action="请先接入或发布对应题解。")
        return grounding

    index = load_exercise_pair_index()
    items = [item for item in index.get("items", []) if item.get("pair_kind") == "same_book_worked_example"]
    requested_book_id = str(page_anchor.get("book_id") or "")
    requested_title = normalize_alias(page_anchor.get("book_title") or book_title)
    # The page route may carry a reviewed page-local label for repeated or
    # printed unnumbered examples.  It is more specific than the raw query
    # parser's base label (for example ``例1``), so keep it authoritative.
    exercise_label = str(page_anchor.get("requested_exercise_label") or request.get("exercise_label") or "").replace(" ", "")
    requested_container = list(page_anchor.get("requested_container_path") or container_path_from_query(query))
    requested_page = page_anchor.get("requested_page")
    if requested_book_id:
        items = [item for item in items if str(item.get("book_id") or "") == requested_book_id]
    elif requested_title:
        items = [item for item in items if normalize_alias(item.get("book_title")) == requested_title]
    if requested_page is not None:
        page = int(requested_page)
        items = [item for item in items if page in list((item.get("question") or {}).get("printed_pages", []))]
    if exercise_label:
        page_local_prefix = f"{exercise_label}（P{int(requested_page)}页内" if requested_page is not None else ""
        items = [
            item for item in items
            if str(item.get("exercise_label") or "").replace(" ", "") == exercise_label
            or (page_local_prefix and str(item.get("exercise_label") or "").replace(" ", "").startswith(page_local_prefix))
        ]
    if requested_container:
        requested_container_key = [normalize_container_label(value) for value in requested_container]
        items = [
            item for item in items
            if len(item.get("container_path", []) or []) == len(requested_container_key)
            and all(
                normalize_container_label(actual) == expected or normalize_container_label(actual).startswith(expected)
                for expected, actual in zip(requested_container_key, item.get("container_path", []) or [])
            )
        ]

    if len(items) > 1:
        candidates = [
            " / ".join(item.get("container_path") or []) or f"P{(item.get('question') or {}).get('printed_pages', ['?'])[0]}"
            for item in items
        ]
        grounding.update(status="answer_ambiguous", failure_reason="找到多个可能的原题—题解关系。", next_action=f"请补充结构标题，例如：{'、'.join(candidates)}。")
        return grounding
    if not items:
        if page_status == "exact_asset":
            grounding.update(status="answer_asset_only", failure_reason="只定位到原题图片，尚未确认原书答案正文。", next_action="如需继续，请明确允许人工核对答案原图，或先发布题解证据。")
        return grounding

    item = items[0]
    grounding["problem"] = dict(item.get("question") or {})
    grounding["solution"] = dict(item.get("solution") or {})
    pair_status = str(item.get("pair_status") or "")
    if pair_status == "exact_pair" and grounding["problem"] and grounding["solution"]:
        grounding.update(status="exact_answer", can_conclude=True, failure_reason="", next_action="")
    elif pair_status == "needs_review":
        grounding.update(status="answer_ambiguous", failure_reason="题目—题解关系需要审核。", next_action="请先审核配对候选。")
    else:
        grounding.update(failure_reason="原题已定位，但原书答案未确认。", next_action="请先补齐对应题解证据。")
    return grounding


def resolve_book_route(
    *, query: str, book_title: str | None = None, context: dict[str, Any] | None = None
) -> dict[str, Any]:
    index = load_book_series_index()
    context = context or {}
    combined = " ".join(item for item in (str(book_title or ""), str(query or "")) if item)
    normalized = normalize_alias(combined)
    candidates: list[dict[str, Any]] = []
    for series in index.get("series", []):
        aliases = list(series.get("normalized_aliases", []))
        if any(alias and alias in normalized for alias in aliases):
            candidates.append(series)
    base = {
        "match_status": "not_found",
        "series_id": "",
        "canonical_title": "",
        "matched_alias": "",
        "stage": "",
        "role_intent": "",
        "volume_ids": [],
        "candidates": [],
    }
    if not candidates:
        return base
    if len(candidates) > 1:
        base["match_status"] = "book_ambiguous"
        base["candidates"] = [{"series_id": item["series_id"], "canonical_title": item["canonical_title"]} for item in candidates]
        return base
    series = candidates[0]
    request = parse_exercise_request(combined)
    stage = request["stage"] or str(context.get("stage") or "")
    role_intent = request["role_intent"] or str(context.get("role_intent") or "")
    volumes = list(series.get("volumes", []))
    if stage:
        volumes = [item for item in volumes if stage in list(item.get("stages", []))]
    if role_intent == "question":
        volumes = [item for item in volumes if item.get("role") == "question_book"]
    elif role_intent == "paired_answer":
        # Keep both sides available; the exercise route performs the pairing.
        volumes = [item for item in volumes if item.get("role") in {"question_book", "solution_book"}]
    ready = [item for item in volumes if item.get("status") == "active"]
    matched_alias = next((item for item in series.get("aliases", []) if normalize_alias(item) in normalized), series["canonical_title"])
    base.update(
        {
            "match_status": "exact_series" if stage or role_intent or len(ready) == 1 else "book_ambiguous",
            "series_id": series["series_id"],
            "canonical_title": series["canonical_title"],
            "matched_alias": matched_alias,
            "stage": stage,
            "role_intent": role_intent,
            "volume_ids": [str(item.get("book_id") or "") for item in ready],
            "candidates": [
                {"book_id": item.get("book_id", ""), "title": item.get("title", ""), "role": item.get("role", ""), "status": item.get("status", "")}
                for item in volumes
            ],
        }
    )
    return base


def resolve_exercise_route(*, query: str, book_route: dict[str, Any]) -> dict[str, Any]:
    request = parse_exercise_request(query)
    result = {
        "match_status": "not_requested",
        "exercise_key": "",
        "request": request,
        "pair_status": "",
        "question": {},
        "solution": {},
        "candidates": [],
    }
    if request["exercise_number"] is None and request["printed_page"] is None:
        return result
    if book_route.get("match_status") in {"not_found", "book_ambiguous"} and not book_route.get("series_id"):
        result["match_status"] = "book_ambiguous"
        return result
    items = [item for item in load_exercise_pair_index().get("items", []) if item.get("series_id") == book_route.get("series_id")]
    stage = request["stage"] or str(book_route.get("stage") or "")
    filters = {
        "stage": stage,
        "part": request["part"],
        "chapter_number": request["chapter_number"],
        "exercise_type": request["exercise_type"],
        "exercise_number": request["exercise_number"],
        "exercise_label": request["exercise_label"],
    }
    for key, value in filters.items():
        if value not in (None, ""):
            items = [item for item in items if item.get(key) == value]
    if request["printed_page"] is not None:
        page = int(request["printed_page"])
        items = [item for item in items if page in list((item.get("question") or {}).get("printed_pages", []))]
    if len(items) == 1:
        item = items[0]
        result.update(
            {
                "match_status": "exact_exercise",
                "exercise_key": item.get("exercise_key", ""),
                "pair_status": item.get("pair_status", ""),
                "question": dict(item.get("question") or {}),
                "solution": dict(item.get("solution") or {}),
            }
        )
    elif items:
        result["match_status"] = "exercise_ambiguous"
        result["candidates"] = [
            {"exercise_key": item.get("exercise_key", ""), "question_pages": (item.get("question") or {}).get("printed_pages", [])}
            for item in items
        ]
    else:
        result["match_status"] = "not_found"
    return result


def build_exercise_pair_index() -> dict[str, Any]:
    layout = ensure_kb_layout()
    series_index = load_book_series_index()
    volume_map: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}
    pending_solution: set[tuple[str, str]] = set()
    for series in series_index.get("series", []):
        for volume in series.get("volumes", []):
            volume_map[str(volume.get("book_id") or "")] = (series, volume)
            if volume.get("role") == "solution_book" and volume.get("status") != "active":
                for stage in volume.get("stages", []):
                    pending_solution.add((series["series_id"], str(stage)))
    grouped: dict[str, dict[str, Any]] = {}
    all_evidences = [load_json_or_default(path, {}) for path in sorted(layout["evidence"].glob("*.json"))]
    for evidence in all_evidences:
        key = str(evidence.get("exercise_key") or "")
        book_id = str(evidence.get("book_id") or "")
        if not key or book_id not in volume_map or evidence.get("verification_status") != "source_grounded":
            continue
        series, volume = volume_map[book_id]
        item = grouped.setdefault(
            key,
            {
                "exercise_key": key,
                "series_id": series["series_id"],
                "stage": evidence.get("exercise_stage", ""),
                "part": evidence.get("exercise_part", ""),
                "chapter_number": evidence.get("exercise_chapter_number"),
                "exercise_type": evidence.get("exercise_type", ""),
                "exercise_number": evidence.get("exercise_number"),
                "question_records": [],
                "solution_records": [],
            },
        )
        record = {
            "book_id": book_id,
            "evidence_id": evidence.get("evidence_id", ""),
            "printed_pages": sorted({int(ref.get("printed_page")) for ref in evidence.get("page_classification_refs", []) if ref.get("printed_page") is not None}),
            "content": evidence.get("content", ""),
            "title": evidence.get("title", ""),
        }
        target = "question_records" if volume.get("role") == "question_book" else "solution_records"
        item[target].append(record)
    items: list[dict[str, Any]] = []
    for item in grouped.values():
        questions = item.pop("question_records")
        solutions = item.pop("solution_records")
        if len(questions) > 1 or len(solutions) > 1:
            status = "needs_review"
        elif questions and solutions:
            status = "exact_pair"
        elif questions and (item["series_id"], item["stage"]) in pending_solution:
            status = "solution_pending"
        elif questions:
            status = "question_only"
        else:
            status = "needs_review"
        item["pair_status"] = status
        item["question"] = questions[0] if len(questions) == 1 else {"candidates": questions}
        item["solution"] = solutions[0] if len(solutions) == 1 else ({"candidates": solutions} if solutions else {})
        items.append(item)
    worked_items = worked_example_pairs_from_evidence(all_evidences)
    for item in worked_items:
        linked = volume_map.get(str(item.get("book_id") or ""))
        if not linked:
            continue
        series, volume = linked
        item["series_id"] = str(series.get("series_id") or "")
        stages = [str(stage) for stage in volume.get("stages", []) if str(stage)]
        item["stage"] = stages[0] if len(stages) == 1 else ""
    items.extend(worked_items)
    items.sort(
        key=lambda item: (
            str(item.get("series_id") or item.get("book_id") or ""),
            str(item.get("stage") or ""),
            str(item.get("part") or item.get("chapter_id") or ""),
            int(item.get("chapter_number") or 0),
            str(item.get("exercise_type") or ""),
            int(item.get("exercise_number") or 0),
            str(item.get("exercise_label") or ""),
        )
    )
    payload = {
        "schema_version": "exercise-pair-index.v1",
        "items": items,
        "summary": {status: sum(1 for item in items if item["pair_status"] == status) for status in ("exact_pair", "question_only", "solution_pending", "needs_review")},
    }
    save_json(layout["indexes"] / EXERCISE_PAIR_INDEX_NAME, payload)
    return payload
