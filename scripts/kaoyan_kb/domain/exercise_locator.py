from __future__ import annotations

import re
import logging
from collections import defaultdict
from typing import Any

from common import kb_layout, ensure_kb_layout, load_json_or_default, now_iso, save_json
from kaoyan_kb.domain.evidence_publication import is_publishable_source_evidence
from kaoyan_kb.domain.index_freshness import directory_json_inputs, fingerprint_index_inputs


EXERCISE_LOCATOR_INDEX_NAME = "exercise_locator_index.json"
QUEUE_NAME = "exercise-locator"
WORKED_EXAMPLE_PATTERN = re.compile(
    r"(?m)^\s*(?:#{1,6}\s*)?(?:【\s*|\[\s*)?例\s*([0-9]+(?:\.[0-9]+)*)?(?:\s*】|\s*\]|(?=\s|[：:、.．▶►]|$))"
)
WORKED_SOLUTION_PATTERN = re.compile(
    r"(?m)^\s*(?:#{1,6}\s*)?(?:【\s*)?(?:解|解答|证明|分析与求解|译)(?:\s*】|(?=\s|[：:、.．]|$))"
)
MARKDOWN_HEADING_PATTERN = re.compile(r"(?m)^\s*#{1,6}\s+")
WORKED_ANSWER_BOUNDARY_PATTERN = re.compile(
    r"(?m)^\s*(?:(?:定理|定义)\s*\d*|\(\s*\d+\s*\)\s*若函数|本章学习诊断)"
)
CONTAINER_HEADING_PATTERN = re.compile(
    r"^(?:题型|题理|专题|方法|模型|考点|类型)\s*(?:[0-9]+|[零一二三四五六七八九十两]+)(?:\s*[：:、-]?\s*.*)?$"
)
CONTAINER_ORDINAL_PATTERN = re.compile(
    r"^(?P<kind>题型|题理|专题|方法|模型|考点|类型)\s*(?P<ordinal>[0-9]+|[零一二三四五六七八九十两]+)(?=\s|[：:、\-]|$)"
)
CHINESE_DIGITS = {"零": 0, "一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "两": 2}


def requested_chapter_id(*, chapter_number: Any = None, book_id: str = "", book_title: str = "", chapter: str = "") -> str:
    """Resolve only an observed book's numbered chapter naming convention.

    The series index has no chapter table. CH-TANG2027-B-CALC-NN is
    evidenced by the published pair records, not by an ordinal manifest ID.
    Unknown conventions remain unresolved; never interpret CH-MATH-0001 as
    the first chapter of a book.
    """
    if chapter_number is None:
        match = re.search(r"第\s*([0-9零一二三四五六七八九十两]+)\s*章", chapter)
        chapter_number = _container_ordinal_value(match.group(1)) if match else None
    if chapter_number is None:
        return ""
    from kaoyan_kb.domain.query.read_session import read_json
    path = kb_layout()["indexes"] / "exercise_pair_index.json"
    if not path.is_file():
        return ""
    normalize = lambda value: re.sub(r"[\s《》]+", "", str(value or "")).lower()
    items = [item for item in read_json(path).get("items", [])
             if (book_id and item.get("book_id") == book_id)
             or (not book_id and book_title and normalize(item.get("book_title")) == normalize(book_title))]
    stems = {match.group(1) for item in items
             if (match := re.fullmatch(r"(CH-TANG2027-B-CALC)-\d{2}", str(item.get("chapter_id") or "")))}
    return f"{next(iter(stems))}-{int(chapter_number):02d}" if len(stems) == 1 else ""


# Evidence roles are defined here once; anchors are the remaining distinctive
# tokens. 收敛 (DF=12/3 chapters) and 发散 (4/2) do not meet 20/4.
CONTENT_EVIDENCE_USE = {
    "instruction": frozenset({
        "讲一下", "解释一下", "检查过程", "核对过程", "问答验收", "帮我", "请问",
        "讲解", "解释", "证明", "判断", "是否", "选择题", "填空题", "解答题",
        "选择", "求解", "怎么做", "如何做", "教材", "讲义", "基础篇", "强化篇",
        "高等数学", "高数", "书上", "原书", "答案", "题解", "这道题", "例题",
        "存在", "设", "求", "的", "第", "写", "吗",
    }),
    "generic_math": frozenset({
        "lim", "sin", "cos", "tan", "ln", "log", "exp", "inf",
        "函数", "连续", "积分", "极限", "可导", "导数", "微分", "单调",
    }),
    "anchor": "remaining_distinctive_tokens",
}
GENERIC_MATH_CONTENT_STOPLIST = CONTENT_EVIDENCE_USE["generic_math"]
DIRECTION_ATTRIBUTES = {
    "单调递减": "decreasing", "单调递增": "increasing",
    "单调不减": "nondecreasing", "单调不增": "nonincreasing",
}
BOUND_ATTRIBUTES = {"有界": "bounded", "有上界": "upper_bounded", "有下界": "lower_bounded"}


def analyze_exercise_content(query: str, item: dict[str, Any], *, book_title: str = "") -> dict[str, Any]:
    """Analyze support, never infer contradiction from missing substring hits.

    All results are ephemeral diagnostics. Direction and boundedness associations
    do not count as anchor hits; no algebraic or conceptual equivalence is used.
    """
    from build_search_index import tokenize
    from kaoyan_kb.domain.query.request import normalize_text
    from kaoyan_kb.domain.query.source_targets import catalogue
    text = normalize_text(query)
    titles = [book_title, str(item.get("book_title") or "")]
    for series in catalogue():
        titles.extend([series.get("canonical_title", ""), *series.get("aliases", [])])
        for volume in series.get("volumes", []):
            titles.extend([volume.get("title", ""), *volume.get("aliases", [])])
    for title in sorted(set(titles), key=len, reverse=True):
        if title:
            text = text.replace(normalize_text(title), " ")
    text = re.sub(r"第?\s*[0-9零一二三四五六七八九十两]+\s*[章节页]|[0-9]+\s*页", " ", text)
    for container in container_path_from_query(text):
        text = text.replace(container, " ")
    text = re.sub(r"例\s*\d+(?:\.\d+)*|第?\s*\d+\s*题", " ", text)
    text = re.sub(r"\bp\s*\d+\b|第?\s*[零一二三四五六七八九十两0-9]+\s*问", " ", text)
    before = []

    def add(token: str, category: str, kind: str = "term") -> None:
        entry = {"token": token, "category": category, "kind": kind}
        if entry not in before:
            before.append(entry)

    for word in sorted(CONTENT_EVIDENCE_USE["instruction"], key=lambda w: (-len(w), w)):
        if word in text:
            add(word, "instruction")
            text = text.replace(word, " ")
    has_content = bool(re.search(r"[a-z0-9\u4e00-\u9fffα-ω]", text))
    content_text = text
    for phrase in DIRECTION_ATTRIBUTES:
        if phrase in text:
            add(phrase, "diagnostic", "direction")
            add("单调", "generic_math")
            text = text.replace(phrase, " ")
    # Keep each continuous relation whole; never admit its isolated function or
    # prime fragments as independent anchors. Whitespace is layout only.
    compact = re.sub(r"\s+", "", text)
    for match in re.finditer(r"[a-zα-ω0-9_'().{}^+*/=→<>!\-]+", compact):
        fragment = match.group().strip(".")
        if re.search(r"[a-zα-ω]", fragment) and re.search(r"[/=→+*<>-]", fragment):
            add(fragment, "anchor", "formula_relation")
    for word in sorted(GENERIC_MATH_CONTENT_STOPLIST, key=lambda w: (-len(w), w)):
        pattern = (r"(?<![a-z])" + re.escape(word) + r"(?![a-z])") if word.isascii() else re.escape(word) + r"(?:性)?"
        if re.search(pattern, text):
            add(word, "generic_math")
            text = re.sub(pattern, " ", text)
    # Identity support keeps each Chinese run whole. Search still uses the
    # existing tokenizer's default n-grams; their substrings are not identities.
    for token in tokenize(text, chinese_ngrams=False):
        if len(token) >= 2 and not token.isdigit():
            add(token, "anchor")
    after = [entry for entry in before if entry["category"] == "anchor"]
    state = "no_content" if not has_content else "has_anchor" if after else "generic_only"
    sides = {
        side: re.sub(r"\s+", "", normalize_text("\n".join(
            str((item.get(side) or {}).get(key) or "") for key in ("content", "text"))))
        for side in ("question", "solution", "answer")
    }
    hits = []
    for entry in after:
        locations = [side for side, haystack in sides.items() if entry["token"] in haystack]
        if locations:
            hits.append({**entry, "locations": locations})
    question_hit = any("question" in hit["locations"] for hit in hits)
    solution_hit = any(set(hit["locations"]) & {"solution", "answer"} for hit in hits)
    attributes = lambda value: [kind for phrase, kind in {**BOUND_ATTRIBUTES, **DIRECTION_ATTRIBUTES}.items() if phrase in value]
    requested_attributes = attributes(content_text)
    candidate_attributes = {side: attributes(value) for side, value in sides.items()}
    bounded_kinds = set(BOUND_ATTRIBUTES.values())
    weak = bool(bounded_kinds.intersection(requested_attributes) and any(
        bounded_kinds.intersection(values) for values in candidate_attributes.values()))
    return {
        "has_content_description": has_content, "tokens_before_filter": before,
        "tokens_after_filter": after, "state": state, "anchor_hits": hits,
        "hit_location": "question" if question_hit else "solution" if solution_hit else "none",
        "question_hit": question_hit, "solution_hit": solution_hit,
        "direction_matches": [phrase for phrase in DIRECTION_ATTRIBUTES if phrase in content_text
                              and any(phrase in value for value in sides.values())],
        "requested_attributes": requested_attributes, "candidate_attributes": candidate_attributes,
        "weak_associations": ["boundedness"] if weak else [],
    }


def exercise_content_matches(query: str, item: dict[str, Any], *, book_title: str = "") -> bool:
    """Compatibility support check; locating exceptions belong to the route gate."""
    analysis = analyze_exercise_content(query, item, book_title=book_title)
    return analysis["state"] == "no_content" or bool(analysis["anchor_hits"])


def normalize_exercise_label(value: Any) -> str:
    match = re.search(r"(?:第\s*)?(\d{1,3})(?:\s*题)?", str(value or ""))
    return f"{int(match.group(1)):02d}" if match else ""


def _evidence_pdf_page(evidence: dict[str, Any]) -> int:
    def page_value(value: Any) -> int:
        match = re.search(r"\d+", str(value or ""))
        return int(match.group(0)) if match else 0

    explicit = page_value(evidence.get("pdf_page"))
    if explicit > 0:
        return explicit
    locator = evidence.get("locator") if isinstance(evidence.get("locator"), dict) else {}
    root_page = page_value(locator.get("page_start"))
    if root_page > 0:
        return root_page
    for span in evidence.get("source_spans", []) or []:
        if not isinstance(span, dict):
            continue
        nested = span.get("locator") if isinstance(span.get("locator"), dict) else {}
        page = page_value(nested.get("page_start"))
        if page > 0:
            return page
    return 0


def _container_ordinal_value(value: str) -> int | None:
    """Parse the small Chinese/Arabic ordinals used by structural headings."""
    text = str(value or "").strip()
    if text.isdigit():
        return int(text)
    if not text or any(char not in {*CHINESE_DIGITS, "十"} for char in text):
        return None
    total = 0
    current = 0
    for char in text:
        if char == "十":
            total += (current or 1) * 10
            current = 0
        else:
            current = CHINESE_DIGITS[char]
    return total + current


def stable_container_label(value: Any) -> str:
    """Return the display-derived key kept in persisted relation identifiers."""
    return re.sub(r"[\s：:、\-—（）()\[\]【】]+", "", str(value or "").strip()).lower()


def normalize_container_label(value: Any) -> str:
    """Return a comparison key, equating numbered Chinese structural headings."""
    text = str(value or "").strip()
    match = CONTAINER_ORDINAL_PATTERN.match(text)
    if not match:
        return stable_container_label(text)
    ordinal = _container_ordinal_value(match.group("ordinal"))
    if ordinal is None:
        return stable_container_label(text)
    canonical = f"{match.group('kind')}{ordinal}{text[match.end():]}"
    return stable_container_label(canonical)


def container_path_from_query(query: str) -> list[str]:
    """Extract the learner-facing structural heading before an example label."""
    text = str(query or "")
    match = re.search(
        r"((?:题型|题理|专题|方法|模型|考点|类型)\s*(?:[0-9]+|[零一二三四五六七八九十两]+)(?:\s*[：:、-]?\s*[^例，。；,;\n]*?)?)\s*(?=例\s*[0-9])",
        text,
    )
    if not match:
        return []
    # Natural Chinese commonly inserts a possessive/locative particle before
    # the example label (for example “题型四的例1”).  It is not part of the
    # printed structural heading and must not affect exact matching.
    value = re.sub(r"(?:的|中|里|内)$", "", match.group(1).strip()).strip()
    return [value] if value else []


def _container_heading(line: str) -> str:
    plain = re.sub(r"^\s*#{1,6}\s*", "", str(line or "").strip())
    return plain if CONTAINER_HEADING_PATTERN.fullmatch(plain) else ""


def _location_key(*, book_id: str, chapter_id: str, printed_page: int, container_path: list[str], exercise_label: str, ordinal: int) -> str:
    # Keep persisted location keys display-derived so this comparison-only
    # normalization does not rewrite established textbook identities.
    path = "/".join(stable_container_label(item) for item in container_path) or "root"
    return f"worked:{book_id}|{chapter_id}|p{printed_page}|{path}|{exercise_label}|{ordinal}"


def _candidate_location(item: dict[str, Any]) -> dict[str, Any]:
    question = dict(item.get("question") or {})
    return {
        "exercise_label": str(item.get("exercise_label") or ""),
        "container_path": list(item.get("container_path") or []),
        "printed_pages": list(question.get("printed_pages") or []),
        "location_key": str(item.get("location_key") or ""),
    }


def _container_path_matches(requested: list[str], candidate: list[str]) -> bool:
    if len(requested) != len(candidate):
        return False
    return all(actual == expected or actual.startswith(expected) for expected, actual in zip(requested, candidate))


def normalize_exercise_category(value: Any) -> str:
    text = str(value or "").strip().lower()
    if text in {"single-choice", "comprehensive"}:
        return text
    if any(token in text for token in ("单项选择", "单选", "选择题")):
        return "single-choice"
    if any(token in text for token in ("综合应用", "综合题")):
        return "comprehensive"
    return ""


def extract_exercise_block(content: Any, exercise_label: Any, *, category: str = "", phase: str = "") -> str:
    """Return one numbered exercise block, or an empty string when it is not unique."""
    label = normalize_exercise_label(exercise_label)
    if not label:
        return ""
    target = int(label)
    lines = str(content or "").splitlines()
    starts: list[int] = []
    labels: list[tuple[int, int]] = []
    section_boundaries: list[int] = []
    marker = re.compile(r"^\s*#{0,6}\s*(\d{1,3})[.．、]\s*")
    requested_category = normalize_exercise_category(category)
    has_requested_category_heading = bool(requested_category) and any(
        re.match(r"^\s*#{1,6}\s*", line)
        and normalize_exercise_category(line) == requested_category
        for line in lines
    )
    active_category = ""
    category_closed = False
    in_answer_section = False
    for index, line in enumerate(lines):
        is_heading = bool(re.match(r"^\s*#{1,6}\s*", line))
        is_answer_section_heading = is_heading and "答案与解析" in line
        heading_category = normalize_exercise_category(line) if is_heading else ""
        if heading_category:
            active_category = heading_category
            category_closed = False
        elif re.match(r"^\s*#{1,6}\s*\d+(?:\.\d+)+(?:\s|$)", line):
            # A numbered textbook section can follow an answer set on the
            # same OCR page (for example, ``# 5.2 二叉树的概念``).  It is
            # not another exercise label, so it must end the preceding
            # category before its nested headings are considered.
            active_category = ""
            category_closed = True
            in_answer_section = False
            section_boundaries.append(index)
        if is_answer_section_heading:
            in_answer_section = True
        match = marker.match(line)
        if not match:
            continue
        number = int(match.group(1))
        labels.append((index, number))
        if phase == "question" and (is_heading or in_answer_section):
            continue
        if phase == "answer":
            category_matches = not requested_category or active_category == requested_category or (not has_requested_category_heading and not active_category and not category_closed)
        elif phase == "question":
            category_matches = not requested_category or active_category == requested_category or (not active_category and not category_closed)
        else:
            category_matches = (
                not requested_category
                or (active_category == requested_category if has_requested_category_heading else active_category in {"", requested_category})
            )
        if number == target and category_matches:
            starts.append(index)
    if len(starts) != 1:
        return ""
    start = starts[0]
    end = len(lines)
    for index, _number in labels:
        if index > start:
            end = index
            break
    for index in section_boundaries:
        if index > start:
            end = min(end, index)
            break
    return "\n".join(lines[start:end]).strip()


def resolve_section_anchor(*, book_title: str, chapter: str, query: str = "") -> dict[str, Any]:
    """Resolve an exercise heading and retain the formal anchor used to normalize it."""
    combined = " ".join(part for part in (str(chapter or ""), str(query or "")) if part)
    numbers = re.findall(r"(?:第\s*)?(\d+(?:\.\d+)+)\s*(?:节)?", combined)
    if not numbers:
        chapter_match = re.search(r"(?:第\s*)?([0-9零一二三四五六七八九十两]+)\s*章", combined)
        return {
            "status": "not_requested",
            "requested_section": "",
            "section_root": str(_container_ordinal_value(chapter_match.group(1))) if chapter_match else "",
            "candidates": [],
        }
    requested = numbers[0]
    parts = requested.split(".")
    if len(parts) <= 2:
        return {
            "status": "not_applicable",
            "requested_section": requested,
            "section_root": requested,
            "candidates": [],
        }

    layout = kb_layout()
    source_ids = {
        str(item.get("source_id") or "")
        for path in layout["manifests"].joinpath("sources").glob("*.json")
        for item in [load_json_or_default(path, {})]
        if item.get("status") == "active"
        and item.get("material_type") == "book-pdf"
        and str(item.get("source_name") or "") == str(book_title or "")
    }
    candidates: list[dict[str, Any]] = []
    for source_id in sorted(source_ids):
        anchors = load_json_or_default(layout["indexes"] / "pdf_book_anchors" / f"{source_id}.json", {})
        for anchor in anchors.get("anchors", []) or []:
            title = str(anchor.get("title") or "").strip()
            if not re.match(rf"^{re.escape(requested)}(?:\s|$)", title):
                continue
            if any(token in title for token in ("本节试题精选", "答案与解析")):
                candidates.append(
                    {
                        "source_id": source_id,
                        "title": title,
                        "pdf_page": int(anchor.get("page_start", 0) or 0),
                        "anchor_type": str(anchor.get("anchor_type") or ""),
                    }
                )
    if not candidates:
        return {
            "status": "not_found",
            "requested_section": requested,
            "section_root": requested,
            "candidates": [],
        }
    identities = {
        (".".join(parts[:-1]), int(item.get("pdf_page", 0) or 0))
        for item in candidates
    }
    if len(identities) != 1:
        return {
            "status": "ambiguous",
            "requested_section": requested,
            "section_root": ".".join(parts[:-1]),
            "candidates": candidates,
        }
    section_root, pdf_page = next(iter(identities))
    return {
        "status": "exact",
        "requested_section": requested,
        "section_root": section_root,
        "title": candidates[0]["title"],
        "pdf_page": pdf_page,
        "source_ids": sorted({str(item.get("source_id") or "") for item in candidates if item.get("source_id")}),
        "candidates": candidates,
    }


def resolve_section_scope(*, book_title: str, chapter: str, query: str = "") -> str:
    """Resolve an exercise/answer heading to its parent section using formal PDF anchors."""
    return str(resolve_section_anchor(book_title=book_title, chapter=chapter, query=query).get("section_root") or "")


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


def _relation_printed_pages(
    relation: dict[str, Any],
    evidence_by_id: dict[str, dict[str, Any]],
    *,
    side: str,
) -> list[int]:
    """Map one relation side to reviewed printed pages without assuming a PDF offset."""
    source_id = str(relation.get("source_id") or "")
    pdf_pages = [int(value) for value in relation.get(f"{side}_pdf_pages", []) or [] if int(value or 0)]
    evidence_ids = [str(value) for value in relation.get(f"{side}_evidence_ids", []) or [] if str(value)]
    printed_pages: list[int] = []
    for pdf_page in pdf_pages:
        matches = {
            int(evidence.get("printed_page", 0) or 0)
            for evidence_id in evidence_ids
            for evidence in [evidence_by_id.get(evidence_id, {})]
            if str(evidence.get("source_id") or "") == source_id
            and is_publishable_source_evidence(evidence)
            and _evidence_pdf_page(evidence) == pdf_page
            and int(evidence.get("printed_page", 0) or 0) > 0
        }
        if len(matches) != 1:
            return []
        printed_page = matches.pop()
        if printed_page not in printed_pages:
            printed_pages.append(printed_page)
    return printed_pages


def _with_relation_printed_pages(
    relation: dict[str, Any], evidence_by_id: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    """Persist both page coordinate systems on an exact PDF exercise relation."""
    enriched = dict(relation)
    question_pdf_pages = [int(value) for value in enriched.get("question_pdf_pages", []) or [] if int(value or 0)]
    answer_pdf_pages = [int(value) for value in enriched.get("answer_pdf_pages", []) or [] if int(value or 0)]
    question_printed_pages = _relation_printed_pages(enriched, evidence_by_id, side="question")
    answer_printed_pages = _relation_printed_pages(enriched, evidence_by_id, side="answer")
    enriched["question_printed_pages"] = question_printed_pages
    enriched["answer_printed_pages"] = answer_printed_pages
    if (
        enriched.get("relation_status") == "exact"
        and (
            not question_pdf_pages
            or not answer_pdf_pages
            or len(question_pdf_pages) != len(question_printed_pages)
            or len(answer_pdf_pages) != len(answer_printed_pages)
        )
    ):
        enriched["relation_status"] = "needs_review"
        enriched["mapping_failure_reason"] = "formal-question-or-answer-page-mapping-missing"
    return enriched


def _grounded_printed_page_records(evidences: list[dict[str, Any]]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    seen: set[tuple[str, str, int, str]] = set()
    for evidence in evidences:
        if not is_publishable_source_evidence(evidence):
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

    contiguous_groups: list[tuple[str, str, list[dict[str, Any]]]] = []
    for (book_id, chapter_id), records in grouped.items():
        records.sort(key=lambda item: (int(item["printed_page"]), str(item["evidence_id"])))
        segment: list[dict[str, Any]] = []
        previous_page: int | None = None
        for record in records:
            printed_page = int(record["printed_page"])
            if segment and previous_page is not None and printed_page > previous_page + 1:
                contiguous_groups.append((book_id, chapter_id, segment))
                segment = []
            segment.append(record)
            previous_page = printed_page
        if segment:
            contiguous_groups.append((book_id, chapter_id, segment))

    candidates: list[dict[str, Any]] = []
    for book_id, chapter_id, records in contiguous_groups:
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
        unnumbered_by_page: dict[int, int] = defaultdict(int)
        container_headings = [
            (match.start(), _container_heading(match.group(0)))
            for match in re.finditer(r"(?m)^.*$", combined)
            if _container_heading(match.group(0))
        ]
        ordinals: dict[tuple[int, tuple[str, ...], str], int] = defaultdict(int)
        for index, marker in enumerate(examples):
            segment_end = examples[index + 1].start() if index + 1 < len(examples) else len(combined)
            solution_markers = list(WORKED_SOLUTION_PATTERN.finditer(combined, marker.end(), segment_end))
            if solution_markers:
                boundary_matches = [
                    match
                    for pattern in (MARKDOWN_HEADING_PATTERN, WORKED_ANSWER_BOUNDARY_PATTERN)
                    if (match := pattern.search(combined, solution_markers[0].end(), segment_end))
                ]
                if boundary_matches:
                    segment_end = min(match.start() for match in boundary_matches)
                    solution_markers = list(WORKED_SOLUTION_PATTERN.finditer(combined, marker.end(), segment_end))
            solution_start = solution_markers[0].start() if solution_markers else segment_end
            relation_status = "exact" if len(solution_markers) == 1 else "needs_review" if solution_markers else "question_only"
            question = _span_side(intervals, marker.start(), solution_start, combined)
            question_pages = list(question.get("printed_pages", []))
            page_token = min(question_pages) if question_pages else 0
            printed_number = str(marker.group(1) or "").strip()
            if printed_number:
                exercise_label = f"例{printed_number}"
            else:
                unnumbered_by_page[page_token] += 1
                exercise_label = f"例（P{page_token}页内{unnumbered_by_page[page_token]}）"
            container_path = [heading for offset, heading in container_headings if offset < marker.start()][-1:]
            ordinal_key = (page_token, tuple(stable_container_label(item) for item in container_path), exercise_label)
            ordinals[ordinal_key] += 1
            container_ordinal = ordinals[ordinal_key]
            location_key = _location_key(
                book_id=book_id,
                chapter_id=chapter_id,
                printed_page=page_token,
                container_path=container_path,
                exercise_label=exercise_label,
                ordinal=container_ordinal,
            )
            relation_id = (
                f"EXW-{book_id}-{chapter_id}-p{page_token}-{exercise_label}"
                if not container_path and container_ordinal == 1
                else f"EXW-{book_id}-{chapter_id}-p{page_token}-{stable_container_label(container_path[-1]) if container_path else 'root'}-{exercise_label}-{container_ordinal}"
            )
            candidates.append({
                "relation_id": relation_id,
                "relation_kind": "same-book-worked-example",
                "relation_status": relation_status,
                "book_id": book_id,
                "book_title": records[0].get("book_title", ""),
                "chapter_id": chapter_id,
                "chapter_title": records[0].get("chapter_title", ""),
                "exercise_label": exercise_label,
                "container_path": container_path,
                "container_ordinal": container_ordinal,
                "location_key": location_key,
                "question": question,
                "answer": _span_side(intervals, solution_start, segment_end, combined) if solution_markers else {},
            })

    repeated_on_page: dict[tuple[str, str, int, tuple[str, ...], str], list[dict[str, Any]]] = defaultdict(list)
    for candidate in candidates:
        question_pages = list((candidate.get("question") or {}).get("printed_pages", []))
        page_token = min(question_pages) if question_pages else 0
        path = tuple(stable_container_label(item) for item in candidate.get("container_path", []) or [])
        repeated_on_page[(candidate["book_id"], candidate["chapter_id"], page_token, path, candidate["exercise_label"])].append(candidate)
    for (_book_id, _chapter_id, _page_token, _path, _printed_label), items in repeated_on_page.items():
        if len(items) < 2:
            continue
        for ordinal, candidate in enumerate(items, start=1):
            candidate["container_ordinal"] = ordinal
            question_pages = list((candidate.get("question") or {}).get("printed_pages", []))
            page_token = min(question_pages) if question_pages else 0
            candidate["location_key"] = _location_key(
                book_id=candidate["book_id"], chapter_id=candidate["chapter_id"], printed_page=page_token,
                container_path=list(candidate.get("container_path") or []), exercise_label=str(candidate["exercise_label"]), ordinal=ordinal,
            )
            container_items = list(candidate.get("container_path") or [])
            candidate["relation_id"] = (
                f"EXW-{candidate['book_id']}-{candidate['chapter_id']}-p{page_token}-{candidate['exercise_label']}"
                if not container_items and ordinal == 1
                else f"EXW-{candidate['book_id']}-{candidate['chapter_id']}-p{page_token}-{stable_container_label(container_items[-1]) if container_items else 'root'}-{candidate['exercise_label']}-{ordinal}"
            )

    grouped_candidates: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for candidate in candidates:
        grouped_candidates[str(candidate.get("location_key") or "")].append(candidate)
    relations: list[dict[str, Any]] = []
    for items in grouped_candidates.values():
        relation = dict(items[0])
        if len(items) != 1:
            relation["relation_status"] = "needs_review"
            relation["candidates"] = items
        relations.append(relation)
    return sorted(relations, key=lambda item: item["relation_id"])


def exercise_locator_input_fingerprint(layout: dict[str, Any]) -> str:
    queue_root = layout["review_queues"] / QUEUE_NAME
    approvals = []
    for path in sorted(queue_root.glob("*.json")) if queue_root.is_dir() else []:
        payload = load_json_or_default(path, {})
        approvals.append(
            (
                f"review-queues/{QUEUE_NAME}/{path.name}/approved-relations",
                list(payload.get("approved_relations", []) or []),
            )
        )
    return fingerprint_index_inputs(
        files=[
            *directory_json_inputs("manifests/sources", layout["manifests"] / "sources"),
            *directory_json_inputs("evidence", layout["evidence"]),
            *directory_json_inputs("indexes/pdf-book-anchors", layout["indexes"] / "pdf_book_anchors"),
        ],
        projections=approvals,
    )


def build_exercise_locator_index() -> dict[str, Any]:
    layout = ensure_kb_layout()
    all_evidences = [load_json_or_default(path, {}) for path in sorted(layout["evidence"].glob("*.json"))]
    evidence_by_id = {
        str(item.get("evidence_id") or ""): item
        for item in all_evidences
        if str(item.get("evidence_id") or "")
    }
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
        if not is_publishable_source_evidence(evidence):
            continue
        page = _evidence_pdf_page(evidence)
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
        for evidence in sorted(pages, key=_evidence_pdf_page):
            page = _evidence_pdf_page(evidence)
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
    relations = [_with_relation_printed_pages(item, evidence_by_id) for item in relations]
    content_validated_relations: list[dict[str, Any]] = []
    for relation in relations:
        if relation.get("relation_status") != "exact":
            content_validated_relations.append(relation)
            continue
        evidence_ids = list(relation.get("question_evidence_ids", []) or []) + list(relation.get("answer_evidence_ids", []) or [])
        anchor, _ = assemble_exact_relation(
            relation,
            evidences=[evidence_by_id[evidence_id] for evidence_id in evidence_ids if evidence_id in evidence_by_id],
        )
        if anchor.get("status") != "exact_answer_evidence":
            relation = {
                **relation,
                "relation_status": "needs_review",
                "mapping_failure_reason": str(anchor.get("reason") or "exercise-content-not-uniquely-sliced"),
            }
        content_validated_relations.append(relation)
    relations = content_validated_relations
    relations.extend(build_worked_example_relations(all_evidences))
    payload = {
        "schema_version": "exercise-locator.v3",
        "generated_at": now_iso(),
        "input_fingerprint": exercise_locator_input_fingerprint(layout),
        "relations": sorted(relations, key=lambda item: item["relation_id"]),
        "summary": {"relation_count": len(relations), "review_count": sum(len(items) for items in review_by_source.values()) + sum(item.get("relation_status") == "needs_review" for item in relations)},
    }
    save_json(layout["indexes"] / EXERCISE_LOCATOR_INDEX_NAME, payload)
    return payload


def load_exercise_locator_index() -> dict[str, Any]:
    layout = kb_layout()
    index_path = layout["indexes"] / EXERCISE_LOCATOR_INDEX_NAME
    unavailable = {
        "relations": [],
        "_availability": {
            "available": False,
            "path": str(index_path),
            "reason": "exercise_locator_index_missing",
            "detail": "The selected knowledge base has no formal exercise locator index.",
        },
    }
    if not index_path.is_file():
        return unavailable
    payload = load_json_or_default(index_path, {})
    if not isinstance(payload, dict) or not isinstance(payload.get("relations"), list):
        unavailable["_availability"].update(
            reason="exercise_locator_index_invalid",
            detail="The exercise locator index must contain a list-valued relations field.",
        )
        return unavailable
    if str(payload.get("input_fingerprint") or "") != exercise_locator_input_fingerprint(layout):
        unavailable["_availability"].update(
            reason="exercise_locator_index_stale",
            detail="Formal exercise locator inputs changed after the index was built; run kb.py sync --indexes-only.",
        )
        return unavailable
    if str(payload.get("schema_version") or "") != "exercise-locator.v3":
        unavailable["_availability"].update(
            reason="exercise_locator_index_version_mismatch",
            detail="The formal exercise locator must be rebuilt as exercise-locator.v3.",
        )
        return unavailable
    result = dict(payload)
    result["_availability"] = {"available": True, "path": str(index_path), "reason": "", "detail": ""}
    return result


def _unavailable_relation(index: dict[str, Any]) -> dict[str, Any]:
    availability = dict(index.get("_availability") or {})
    if availability.get("available", True):
        return {}
    return {
        "relation_status": "unavailable",
        "unavailable_reason": str(availability.get("reason") or "exercise_locator_index_unavailable"),
        "unavailable_detail": str(availability.get("detail") or ""),
        "_availability": availability,
    }


def assemble_exact_relation(
    relation: dict[str, Any],
    *,
    evidences: list[dict[str, Any]] | None = None,
    exercise_label: str = "",
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Load and slice one exact relation into the shared query/ask anchor shape."""
    label = normalize_exercise_label(exercise_label or relation.get("exercise_label"))
    base = {
        "relation_id": str(relation.get("relation_id") or ""),
        "exercise_label": label,
        "exercise_category": str(relation.get("category") or ""),
        "question_pdf_pages": [int(value) for value in relation.get("question_pdf_pages", []) or []],
        "question_printed_pages": [int(value) for value in relation.get("question_printed_pages", []) or []],
        "answer_pdf_pages": [int(value) for value in relation.get("answer_pdf_pages", []) or []],
        "answer_printed_pages": [int(value) for value in relation.get("answer_printed_pages", []) or []],
        "question_evidence_ids": [str(value) for value in relation.get("question_evidence_ids", []) or [] if str(value)],
        "answer_evidence_ids": [str(value) for value in relation.get("answer_evidence_ids", []) or [] if str(value)],
    }
    if relation.get("relation_status") != "exact":
        return {
            "status": "needs_review",
            "reason": str(relation.get("mapping_failure_reason") or "exercise-relation-not-exact"),
            **base,
            "question_content": "",
            "answer_content": "",
            "content_scope": "needs_review",
        }, list(evidences or [])
    if (
        not base["question_pdf_pages"]
        or not base["answer_pdf_pages"]
        or len(base["question_pdf_pages"]) != len(base["question_printed_pages"])
        or len(base["answer_pdf_pages"]) != len(base["answer_printed_pages"])
    ):
        return {
            "status": "needs_review",
            "reason": "formal-question-or-answer-page-mapping-missing",
            **base,
            "question_content": "",
            "answer_content": "",
            "content_scope": "needs_review",
        }, list(evidences or [])

    loaded = list(evidences or [])
    existing_ids = {str(item.get("evidence_id") or "") for item in loaded}
    layout = kb_layout()
    for evidence_id in base["question_evidence_ids"] + base["answer_evidence_ids"]:
        path = layout["evidence"] / f"{evidence_id}.json"
        if evidence_id not in existing_ids and path.is_file():
            loaded.append(load_json_or_default(path, {}))
            existing_ids.add(evidence_id)
    evidence_by_id = {str(item.get("evidence_id") or ""): item for item in loaded}
    question_text = "\n".join(
        str(evidence_by_id[item].get("content") or "")
        for item in base["question_evidence_ids"]
        if item in evidence_by_id
    )
    answer_text = "\n".join(
        str(evidence_by_id[item].get("content") or "")
        for item in base["answer_evidence_ids"]
        if item in evidence_by_id
    )
    category = str(relation.get("category") or "")
    question_content = extract_exercise_block(question_text, label, category=category, phase="question")
    answer_content = extract_exercise_block(answer_text, label, category=category, phase="answer")
    question_paths = [
        str(evidence_by_id[item].get("source_image_path") or "")
        for item in base["question_evidence_ids"]
        if item in evidence_by_id and evidence_by_id[item].get("source_image_path")
    ]
    answer_paths = [
        str(evidence_by_id[item].get("source_image_path") or "")
        for item in base["answer_evidence_ids"]
        if item in evidence_by_id and evidence_by_id[item].get("source_image_path")
    ]
    payload = {
        **base,
        "question_source_image_paths": list(dict.fromkeys(question_paths)),
        "answer_source_image_paths": list(dict.fromkeys(answer_paths)),
        "question_content": question_content,
        "answer_content": answer_content,
        "content_scope": "exercise_exact" if question_content and answer_content else "needs_review",
    }
    if not question_content or not answer_content:
        return {"status": "needs_review", "reason": "exercise-content-not-uniquely-sliced", **payload}, loaded
    return {"status": "exact_answer_evidence", **payload}, loaded


def find_exact_relation(*, source_id: str, question_pdf_page: int, exercise_label: str) -> dict[str, Any]:
    label = normalize_exercise_label(exercise_label)
    index = load_exercise_locator_index()
    unavailable = _unavailable_relation(index)
    if unavailable:
        return unavailable
    matches = [item for item in index.get("relations", []) if item.get("source_id") == source_id and label == item.get("exercise_label") and question_pdf_page in set(item.get("question_pdf_pages", []))]
    return matches[0] if len(matches) == 1 and matches[0].get("relation_status") == "exact" else {}


def list_exact_relations_for_question_page(*, source_id: str, question_pdf_page: int, category: str = "") -> dict[str, Any]:
    """List formally accepted, uniquely sliced exercises on one question page."""
    index = load_exercise_locator_index()
    unavailable = _unavailable_relation(index)
    if unavailable:
        return {
            "status": "unavailable",
            "relations": [],
            "unavailable_reason": unavailable.get("unavailable_reason", "exercise_locator_index_unavailable"),
            "unavailable_detail": unavailable.get("unavailable_detail", ""),
            "_availability": dict(unavailable.get("_availability") or {}),
        }
    normalized_category = normalize_exercise_category(category)
    layout = kb_layout()
    candidates: list[dict[str, Any]] = []
    for item in index.get("relations", []) or []:
        if item.get("relation_status") != "exact":
            continue
        if str(item.get("source_id") or "") != str(source_id or ""):
            continue
        if int(question_pdf_page or 0) not in {int(page) for page in item.get("question_pdf_pages", []) or []}:
            continue
        if normalized_category and normalize_exercise_category(item.get("category")) != normalized_category:
            continue
        evidence_ids = [str(value) for value in item.get("question_evidence_ids", []) or [] if str(value)]
        question_text = "\n".join(
            str(load_json_or_default(layout["evidence"] / f"{evidence_id}.json", {}).get("content") or "")
            for evidence_id in evidence_ids
        )
        label = normalize_exercise_label(item.get("exercise_label"))
        question_content = extract_exercise_block(question_text, label, category=str(item.get("category") or ""), phase="question")
        if not label or not question_content:
            continue
        candidates.append({**item, "exercise_label": label, "question_content": question_content})
    return {
        "status": "exact",
        "relations": sorted(candidates, key=lambda item: (str(item.get("category") or ""), str(item.get("exercise_label") or ""))),
    }


def list_exact_worked_example_relations(*, book_id: str, printed_page: int) -> dict[str, Any]:
    """List grounded same-book examples, including printed unnumbered examples."""
    index = load_exercise_locator_index()
    unavailable = _unavailable_relation(index)
    if unavailable:
        return {
            "status": "unavailable",
            "relations": [],
            "unavailable_reason": unavailable.get("unavailable_reason", "exercise_locator_index_unavailable"),
            "unavailable_detail": unavailable.get("unavailable_detail", ""),
            "_availability": dict(unavailable.get("_availability") or {}),
        }
    relations = [
        {**item, "question_content": str((item.get("question") or {}).get("content") or "")}
        for item in index.get("relations", []) or []
        if item.get("relation_kind") == "same-book-worked-example"
        and item.get("relation_status") == "exact"
        and str(item.get("book_id") or "") == str(book_id or "")
        and int(printed_page or 0) in {int(page) for page in (item.get("question") or {}).get("printed_pages", []) or []}
    ]
    return {
        "status": "exact",
        "relations": sorted(relations, key=lambda item: str(item.get("exercise_label") or "")),
    }


def find_unique_relation_for_scope(*, book_title: str, chapter: str, exercise_label: str, category: str = "", query: str = "") -> dict[str, Any]:
    """Resolve a relation without a page only when book and current chapter are unique."""
    if not book_title or not chapter:
        return {}
    label = normalize_exercise_label(exercise_label)
    section_anchor = resolve_section_anchor(book_title=book_title, chapter=chapter, query=query)
    if section_anchor.get("status") == "ambiguous":
        return {}
    scope = str(section_anchor.get("section_root") or "")
    section_number = re.search(r"^\d+\.\d+$", scope)
    chapter_number = re.search(r"(?:第\s*)?([0-9零一二三四五六七八九十两]+)\s*章", chapter)
    normalized_category = normalize_exercise_category(category or query)
    if not label or (not scope and not chapter_number):
        return {}
    scope = scope or str(_container_ordinal_value(chapter_number.group(1)))
    layout = kb_layout()
    sources = {
        str(item.get("source_id") or ""): str(item.get("source_name") or "")
        for path in layout["manifests"].joinpath("sources").glob("*.json")
        for item in [load_json_or_default(path, {})]
        if item.get("status") == "active" and item.get("material_type") == "book-pdf"
    }
    index = load_exercise_locator_index()
    unavailable = _unavailable_relation(index)
    if unavailable:
        return unavailable
    matches = [
        item for item in index.get("relations", [])
        if item.get("relation_status") == "exact"
        and normalize_exercise_label(item.get("exercise_label")) == label
        and (not normalized_category or item.get("category") == normalized_category)
        and sources.get(str(item.get("source_id") or "")) == book_title
        and (
            str(item.get("section_root") or "") == scope
            if section_number
            else str(item.get("section_root") or "").split(".", 1)[0] == scope
        )
    ]
    checked = []
    for item in matches:
        expected = requested_chapter_id(chapter_number=int(scope.split('.', 1)[0]), book_id=str(item.get("book_id") or ""), book_title=book_title)
        actual = str(item.get("chapter_id") or "")
        if expected and actual and expected != actual:
            continue
        if expected and not actual:
            # 294 existing PDF relations have no chapter_id: the design's
            # missing-data exception applies; section_root never proves ID.
            logging.getLogger(__name__).warning("chapter check skipped: relation %s has no chapter_id", item.get("relation_id"))
        checked.append(item)
    return checked[0] if len(checked) == 1 else {}


def find_exact_worked_example_relation(
    *, book_id: str, printed_page: int, exercise_label: str, container_path: list[str] | None = None
) -> dict[str, Any]:
    index = load_exercise_locator_index()
    unavailable = _unavailable_relation(index)
    if unavailable:
        return unavailable
    normalized_requested = exercise_label.replace(" ", "")
    requested_container = [normalize_container_label(item) for item in (container_path or []) if normalize_container_label(item)]
    matches = [
        item for item in index.get("relations", [])
        if item.get("relation_kind") == "same-book-worked-example"
        and item.get("book_id") == book_id
        and (
            item.get("exercise_label") == normalized_requested
            or str(item.get("exercise_label") or "").startswith(f"{normalized_requested}（P{printed_page}页内")
        )
        and printed_page in set((item.get("question") or {}).get("printed_pages", []))
    ]
    if requested_container:
        matches = [
            item for item in matches
            if _container_path_matches(requested_container, [normalize_container_label(value) for value in item.get("container_path", []) or []])
        ]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        return {
            "relation_status": "needs_review",
            "relation_id": "",
            "candidates": matches,
            "candidate_locations": [_candidate_location(item) for item in matches],
        }
    return {}
