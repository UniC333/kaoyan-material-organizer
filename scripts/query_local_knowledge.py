#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from pathlib import Path
from typing import Any

from common import INDEX_DIRNAME, default_vault_root_arg, ensure_kb_layout, is_publishable_claim, is_publishable_source_evidence, learner_file_map, load_all_json, load_json, resolve_subject, runtime_context_payload, validate_entity_contract
from kaoyan_kb.domain.page_locator import evidence_matches_locator, load_page_locator_index, parse_exercise_label, resolve_page_locator
from kaoyan_kb.domain.exercise_locator import assemble_exact_relation, container_path_from_query, find_exact_relation, find_exact_worked_example_relation, find_unique_relation_for_scope, list_exact_relations_for_question_page, list_exact_worked_example_relations, normalize_exercise_category, normalize_exercise_label, resolve_section_anchor
from kaoyan_kb.domain.book_series import classify_source_request, has_exercise_request_signal, parse_exercise_request, resolve_answer_grounding, resolve_book_route, resolve_exercise_route
from kaoyan_kb.domain.exercise_batch import BATCH_CONTRACT_VERSION, parse_exercise_batch_request, resolve_exercise_batch_targets
from kaoyan_kb.domain.teaching_context import build_bounded_teaching_context
from learner_events import load_events
from retrieve_knowledge import retrieve as retrieve_index


PRIMARY_CALCULUS_BOOK_TITLE = "高等数学辅导讲义基础篇"
SUPPLEMENTAL_CALCULUS_BOOK_TITLE = "李正元数一"
CONCEPT_SUFFIXES = ("定理", "法则", "公式", "定义")
GENERIC_ANSWER_MODES = {"canonical_claim", "accepted_evidence"}
GENERIC_QUERY_NOISE = (
    "请问",
    "帮我",
    "讲一下",
    "讲解",
    "解释一下",
    "解释",
    "说明一下",
    "说明",
    "告诉我",
    "什么是",
    "是什么",
    "有什么区别",
    "有什么不同",
    "怎么区别",
    "如何区别",
    "怎么区分",
    "如何区分",
    "有什么联系",
    "有什么关系",
    "区别",
    "联系",
    "关系",
    "比较",
    "对比",
    "怎么做",
    "如何做",
    "怎么理解",
    "为什么",
    "的定义",
    "定义",
    "概念",
    "吗",
    "呢",
)


CURRENT_TASK_PATH = Path("01_任务") / "当前任务.md"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--vault-root", default=default_vault_root_arg())
    parser.add_argument("--subject")
    parser.add_argument("--chapter")
    parser.add_argument("--book-title")
    parser.add_argument("--query")
    parser.add_argument("--printed-page", type=int)
    parser.add_argument("--exercise-label")
    parser.add_argument("--topk", type=int, default=3)
    parser.add_argument("--format", choices=("text", "json"), default="text")
    return parser.parse_args()


def normalize_text(value: Any) -> str:
    text = str(value or "").strip().lower()
    text = re.sub(r"\s+", " ", text)
    return text


def resolve_current_task_book(*, vault_root: Path, subject: str, explicit_book_title: str | None) -> dict[str, str]:
    """Use an explicit current-task default only for unspecific math requests."""
    explicit = str(explicit_book_title or "").strip()
    if explicit:
        return {"status": "explicit", "source": "explicit", "book_title": explicit, "current_task_path": ""}
    if subject != "数学":
        return {"status": "not_applicable", "source": "", "book_title": "", "current_task_path": ""}
    path = Path(vault_root) / CURRENT_TASK_PATH
    if not path.is_file():
        return {"status": "not_found", "source": "", "book_title": "", "current_task_path": str(path)}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return {"status": "unavailable", "source": "", "book_title": "", "current_task_path": str(path)}
    titles: list[str] = []
    for line in lines:
        if not any(token in line for token in ("数学", "高数")):
            continue
        if not any(token in line for token in ("唯一当前主教材", "当前唯一主教材", "默认按当前", "当前只跟")):
            continue
        titles.extend(match.strip() for match in re.findall(r"《([^》]+)》", line) if match.strip())
    unique = list(dict.fromkeys(titles))
    if len(unique) == 1:
        return {"status": "exact", "source": "current_task_default", "book_title": unique[0], "current_task_path": str(path)}
    return {
        "status": "ambiguous" if len(unique) > 1 else "not_found",
        "source": "",
        "book_title": "",
        "current_task_path": str(path),
    }


def chapter_matches(chapter: str | None, *values: Any) -> bool:
    if not chapter:
        return True
    needle = normalize_text(chapter)
    if not needle:
        return True
    needle_ordinal = chapter_ordinal(needle)
    for value in values:
        hay = normalize_text(value)
        if not hay:
            continue
        if needle in hay or hay in needle:
            return True
        if needle_ordinal is not None and chapter_ordinal(hay) == needle_ordinal:
            return True
    return False


def chapter_ordinal(text: str) -> int | None:
    match = re.search(r"第\s*([0-9]+|[零一二三四五六七八九十两]+)\s*章", text)
    if not match:
        return None
    token = match.group(1)
    if token.isdigit():
        return int(token)
    return chinese_number_to_int(token)


def chinese_number_to_int(token: str) -> int | None:
    mapping = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
    if token == "十":
        return 10
    if token.startswith("十"):
        tail = mapping.get(token[1:], 0)
        return 10 + tail
    if token.endswith("十"):
        head = mapping.get(token[0], 0)
        return head * 10
    if "十" in token:
        head, tail = token.split("十", 1)
        if head not in mapping or tail not in mapping:
            return None
        return mapping[head] * 10 + mapping[tail]
    return mapping.get(token)


def detect_intent(query: str) -> str:
    text = normalize_text(query)
    if any(token in text for token in ("书上有", "书里有", "教材有", "书上怎么", "书里怎么", "教材怎么", "原文", "类似例题", "类似推导")):
        return "source_verify"
    if any(token in text for token in ("区分", "区别", "容易混", "易混", "比较", "对比")):
        return "compare"
    if any(token in text for token in ("下一步", "怎么学", "计划", "先看什么", "先追")):
        return "plan"
    if any(token in text for token in ("为什么错", "诊断", "卡住", "不会", "问题在哪")):
        return "diagnose"
    return "define"


def extract_explicit_concepts(query: str) -> list[str]:
    """Extract only theorem/definition-like concepts explicitly named by the learner."""
    text = re.sub(r"(定理|法则|公式|定义)\s*[和与、/]\s*", r"\1|", str(query or ""))
    segments = re.split(r"[|，。；：！？,;:!?\n]", text)
    concepts: list[str] = []
    prefix_markers = (
        "讲一下", "讲解", "解释", "说明", "比较", "对比", "关于", "根据", "利用", "使用", "结合", "应用",
        "问和", "问与", "题和", "题与", "由",
    )
    suffix_pattern = "|".join(CONCEPT_SUFFIXES)
    for segment in segments:
        for match in re.finditer(rf"[A-Za-z\u4e00-\u9fff]{{2,24}}(?:{suffix_pattern})", segment):
            candidate = match.group(0)
            last_boundary = -1
            boundary_length = 0
            for marker in prefix_markers:
                index = candidate.rfind(marker)
                if index >= last_boundary:
                    last_boundary = index
                    boundary_length = len(marker)
            if last_boundary >= 0:
                candidate = candidate[last_boundary + boundary_length :]
            candidate = re.sub(r"^(?:和|与|及|的)+", "", candidate).strip()
            suffix = next((item for item in CONCEPT_SUFFIXES if candidate.endswith(item)), "")
            if not suffix or len(candidate) - len(suffix) < 2:
                continue
            if candidate not in concepts:
                concepts.append(candidate)
    return concepts[:3]


def parse_page_anchor(query: str) -> dict[str, Any]:
    text = str(query or "")
    match = re.search(r"(?:第?\s*([0-9]+)\s*页|(?<![A-Za-z0-9])[Pp]\s*[.．]?\s*([0-9]+)(?![0-9]))", text)
    requested_page = int(match.group(1) or match.group(2)) if match else None
    requested_position = None
    if any(token in text for token in ("最下方", "最下面", "页底", "底部", "最底下", "下方")):
        requested_position = "bottom"
    elif any(token in text for token in ("最上方", "最上面", "页首", "顶部", "上方")):
        requested_position = "top"
    elif any(token in text for token in ("中间", "中部")):
        requested_position = "middle"
    exercise_label = parse_exercise_label(text)
    if not exercise_label:
        exercise_match = re.search(r"(?:第\s*)?(\d{1,3})\s*题", text)
        exercise_label = normalize_exercise_label(exercise_match.group(1)) if exercise_match else ""
    return {
        "requested_page": requested_page,
        "requested_position": requested_position,
        "requested_exercise_label": exercise_label,
    }


def parse_requested_option(query: str, exercise_label: str = "") -> str:
    """Resolve one directly requested option without treating an option letter as a question id."""
    text = str(query or "")
    label = normalize_exercise_label(exercise_label)
    if label:
        number = int(label)
        match = re.search(rf"(?:第\s*)?0*{number}\s*(?:题)?\s*(?:的\s*)?([A-D])(?:\s*项)?", text, flags=re.IGNORECASE)
        if match:
            return match.group(1).upper()
    mentions = [
        match.group(1).upper()
        for pattern in (r"(?<![A-Za-z])([A-D])\s*(?:项|选项)", r"(?:选项)\s*([A-D])(?![A-Za-z])")
        for match in re.finditer(pattern, text, flags=re.IGNORECASE)
    ]
    unique = list(dict.fromkeys(mentions))
    return unique[0] if len(unique) == 1 else ""


def needs_exercise_identity(query: str, requested_option: str = "") -> bool:
    return has_exercise_request_signal(query, requested_option=requested_option)


def _normalize_exercise_match_text(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).lower()
    text = text.replace("操作数栈", "运算数栈")
    return re.sub(r"\s+", "", text)


def _chinese_ngrams(value: str, sizes: tuple[int, ...] = (3, 4)) -> set[str]:
    result: set[str] = set()
    for chunk in re.findall(r"[\u4e00-\u9fff]+", value):
        for size in sizes:
            result.update(chunk[index : index + size] for index in range(0, len(chunk) - size + 1))
    return result


def _is_distinctive_exercise_term(term: str) -> bool:
    generic_roots = ("表达式", "选项", "题目", "问题", "判断", "这个", "下面", "哪个", "怎么")
    return not any(term in root or root in term for root in generic_roots)


def infer_exercise_from_exact_page(*, locator: dict[str, Any], query: str, category: str = "", requested_option: str = "") -> dict[str, Any]:
    """Infer a missing exercise label only from exact formal relations on the resolved page."""
    base = {
        "status": "not_found",
        "source": "page_content",
        "exercise_label": "",
        "requested_option": requested_option,
        "candidate_labels": [],
        "matched_terms": [],
    }
    payloads: list[dict[str, Any]] = []
    pdf_page = int(locator.get("pdf_page", 0) or 0)
    if pdf_page:
        payloads.append(list_exact_relations_for_question_page(
            source_id=str(locator.get("source_id") or ""),
            question_pdf_page=pdf_page,
            category=category,
        ))
    book_id = str(locator.get("book_id") or "")
    printed_page = int(locator.get("requested_page") or locator.get("printed_page") or 0)
    if book_id and printed_page:
        payloads.append(list_exact_worked_example_relations(book_id=book_id, printed_page=printed_page))
    unavailable_payload = next((payload for payload in payloads if payload.get("status") == "unavailable"), None)
    if unavailable_payload:
        return {
            **base,
            "status": "unavailable",
            "unavailable_reason": unavailable_payload.get("unavailable_reason", "exercise_locator_index_unavailable"),
            "unavailable_detail": unavailable_payload.get("unavailable_detail", ""),
            "_availability": dict(unavailable_payload.get("_availability") or {}),
        }
    relations = [relation for payload in payloads for relation in payload.get("relations", []) or []]
    labels = [str(item.get("exercise_label") or "") for item in relations if item.get("exercise_label")]
    base["candidate_labels"] = labels
    if not relations:
        return base
    if len(relations) == 1:
        return {**base, "status": "inferred_unique", "exercise_label": labels[0]}

    query_terms = _chinese_ngrams(_normalize_exercise_match_text(query))
    candidate_terms = [_chinese_ngrams(_normalize_exercise_match_text(item.get("question_content"))) for item in relations]
    frequency: dict[str, int] = {}
    for terms in candidate_terms:
        for term in terms:
            frequency[term] = frequency.get(term, 0) + 1
    qualified: list[tuple[int, list[str]]] = []
    for index, terms in enumerate(candidate_terms):
        unique_matches = sorted(
            (term for term in query_terms & terms if frequency.get(term) == 1 and _is_distinctive_exercise_term(term)),
            key=lambda term: (-len(term), term),
        )
        four_char = [term for term in unique_matches if len(term) == 4]
        three_char = [term for term in unique_matches if len(term) == 3]
        if four_char or len(three_char) >= 2:
            qualified.append((index, four_char + three_char))
    if len(qualified) != 1:
        return {**base, "status": "ambiguous"}
    index, matched_terms = qualified[0]
    return {
        **base,
        "status": "inferred_unique",
        "exercise_label": str(relations[index].get("exercise_label") or ""),
        "matched_terms": matched_terms,
    }


def resolve_request(
    *,
    query: str,
    book_title: str | None,
    chapter: str | None,
    printed_page: int | None,
    exercise_label: str | None,
) -> dict[str, Any]:
    """Resolve routing hints while preserving explicit CLI overrides."""
    text = str(query or "")
    parsed = parse_page_anchor(text)
    page = int(printed_page) if printed_page is not None else parsed.get("requested_page")
    if printed_page is not None:
        page_semantics = "exact_page"
    elif page is None:
        page_semantics = "none"
    elif re.search(rf"(?:从\s*)?(?:第\s*)?{page}\s*页\s*(?:起|开始|开头)", text):
        page_semantics = "section_start"
    elif any(token in text for token in ("附近", "左右", "前后", "大约", "约在", "以后", "往后", "之后", "后面")):
        page_semantics = "approximate_page"
    else:
        page_semantics = "exact_page"

    raw_cli_label = str(exercise_label or "").strip()
    if re.fullmatch(r"例(?:\d+(?:\.\d+)*)?（P\d+页内\d+）", raw_cli_label):
        label = raw_cli_label
    else:
        label = parse_exercise_label(raw_cli_label) or normalize_exercise_label(exercise_label)
    if not label:
        parsed_label = str(parsed.get("requested_exercise_label") or "")
        # Worked examples use the printed ``例`` prefix to select the
        # same-book question/answer relation.  Stripping it to ``08`` sends
        # the request down the numbered-exercise/PDF path and leaves a valid
        # photo-page example incorrectly marked unverified.
        label = parse_exercise_label(parsed_label) or normalize_exercise_label(parsed_label)
    requested_option = parse_requested_option(text, label)
    section_anchor = resolve_section_anchor(book_title=str(book_title or ""), chapter=str(chapter or ""), query=text)
    section_scope = str(section_anchor.get("section_root") or "")
    category = normalize_exercise_category(text)
    if requested_option and not category:
        category = "single-choice"
    label_source = "cli" if exercise_label and label else "query" if label else ""
    container_path = container_path_from_query(text)
    source_request_kind = classify_source_request(
        query=text,
        book_title=book_title,
        page_anchor={"requested_page": page, "requested_exercise_label": label},
        exercise_label=label,
        requested_option=requested_option,
    )
    return {
        "original_query": text,
        "source_request_kind": source_request_kind,
        "book_title": str(book_title or ""),
        "chapter_input": str(chapter or ""),
        "section_root": section_scope,
        "section_anchor": section_anchor,
        "page": {"number": page, "semantics": page_semantics, "explicit_cli": printed_page is not None},
        "exercise_label": label,
        "container_path": container_path,
        "exercise_category": category,
        "requested_option": requested_option,
        "exercise_resolution": {
            "status": "explicit" if label else "not_requested",
            "source": label_source,
            "exercise_label": label,
            "requested_option": requested_option,
            "candidate_labels": [],
            "matched_terms": [],
            "container_path": container_path,
        },
    }


def build_page_crosscheck(request_resolution: dict[str, Any], page_anchor: dict[str, Any]) -> dict[str, Any]:
    """Cross-check a soft page clue against the formal section heading anchor."""
    page_request = dict(request_resolution.get("page") or {})
    semantics = str(page_request.get("semantics") or "none")
    required = semantics in {"section_start", "approximate_page"} and page_request.get("number") is not None
    section_anchor = dict(request_resolution.get("section_anchor") or {})
    result = {
        "required": required,
        "status": "not_requested" if not required else "unverified",
        "semantics": semantics,
        "requested_printed_page": page_request.get("number"),
        "resolved_pdf_page": int(page_anchor.get("pdf_page", 0) or 0),
        "section_anchor_status": str(section_anchor.get("status") or "not_requested"),
        "section_anchor_pdf_page": int(section_anchor.get("pdf_page", 0) or 0),
        "tolerance_pdf_pages": 2 if semantics == "approximate_page" else 0,
        "delta_pdf_pages": None,
        "reason": "",
    }
    if not required:
        return result
    page_status = str(page_anchor.get("match_status") or "not_found")
    if page_status == "unavailable":
        result.update(status="unavailable", reason="正式页码索引当前不可用，无法核验小节起始页。")
        return result
    if section_anchor.get("status") == "ambiguous":
        result.update(status="ambiguous", reason="小节标题存在多个正式页码锚点，无法唯一核验起始页。")
        return result
    if section_anchor.get("status") != "exact":
        result.update(status="unverified", reason="尚未定位该小节标题的正式页码锚点。")
        return result
    resolved_pdf_page = int(page_anchor.get("pdf_page", 0) or 0)
    section_pdf_page = int(section_anchor.get("pdf_page", 0) or 0)
    if page_status not in {"exact_evidence", "exact_asset"} or not resolved_pdf_page or not section_pdf_page:
        result.update(status="unverified", reason="页码线索尚未映射到可与小节锚点比较的 PDF 页。")
        return result
    delta = resolved_pdf_page - section_pdf_page
    tolerance = int(result["tolerance_pdf_pages"])
    result["delta_pdf_pages"] = delta
    if abs(delta) <= tolerance:
        result.update(status="confirmed", reason="页码线索与正式小节标题锚点一致。")
    else:
        result.update(status="conflict", reason="页码线索与正式小节标题锚点冲突。")
    return result


def explicit_page_subject_error(*, subject: str | None, query: str, printed_page: int | None) -> str:
    """Return a safe CLI error when an explicit-page request lacks its subject."""
    if str(subject or "").strip():
        return ""
    requested_page = printed_page if printed_page is not None else parse_page_anchor(query).get("requested_page")
    if requested_page is None:
        return ""
    return (
        "[ERROR] explicit page requests require --subject; "
        f"for example: --subject 数学 --printed-page {requested_page} "
        "--book-title <教材名>"
    )


def build_page_verification_summary(
    page_anchor: dict[str, Any],
    answer_mode: str,
    page_crosscheck: dict[str, Any] | None = None,
    *,
    request_resolution: dict[str, Any] | None = None,
    answer_grounding: dict[str, Any] | None = None,
    teaching_bundle: dict[str, Any] | None = None,
    page_content_bundle: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Expose page location, exercise verification, and teaching permission separately."""
    status = str(page_anchor.get("match_status") or "not_requested")
    exercise_status = str(page_anchor.get("exercise_match_status") or "not_requested")
    crosscheck = dict(page_crosscheck or {})
    crosscheck_required = bool(crosscheck.get("required"))
    crosscheck_status = str(crosscheck.get("status") or "not_requested")
    crosscheck_ok = not crosscheck_required or crosscheck_status == "confirmed"
    requested_page = page_anchor.get("requested_page")
    request_kind = str((request_resolution or {}).get("source_request_kind") or "generic")
    grounding = dict(answer_grounding or {})
    teaching = dict(teaching_bundle or {})
    page_content = dict(page_content_bundle or {})
    if request_kind == "page_content":
        textbook_explanation_allowed = (
            status == "exact_evidence"
            and crosscheck_ok
            and str(page_content.get("status") or "") == "exact"
        )
    elif request_kind == "exercise":
        page_ok = requested_page is None or status == "exact_evidence"
        textbook_explanation_allowed = (
            page_ok
            and exercise_status == "matched"
            and crosscheck_ok
            and str(grounding.get("status") or "") == "exact_answer"
            and bool(grounding.get("can_conclude"))
            and str(teaching.get("status") or "") == "exact"
        )
    else:
        textbook_explanation_allowed = False
    if crosscheck_required and not crosscheck_ok:
        summary = str(crosscheck.get("reason") or "页码线索与小节标题锚点尚未完成一致性核验。")
    elif status == "exact_asset":
        summary = "教材原页已定位；教材正文未确认，不能按书上原题讲解。"
    elif request_kind == "page_content" and not textbook_explanation_allowed:
        summary = str(page_content.get("failure_reason") or "教材页面正文尚未完成审核发布。")
    elif request_kind == "exercise" and not textbook_explanation_allowed:
        summary = str(grounding.get("failure_reason") or teaching.get("failure_reason") or "原题与原书答案尚未完成精确核验。")
    elif request_kind not in {"page_content", "exercise"}:
        summary = "本次请求不属于教材正文或原书习题讲解；不会仅凭页码定位开放教材讲解。"
    elif status == "exact_evidence" and not textbook_explanation_allowed:
        summary = "教材页正文已有证据，但请求的题号尚未在正文中核验。"
    elif request_kind == "page_content" and textbook_explanation_allowed:
        summary = "教材原页与同源、同页的审核正文证据已核验，可在证据范围内按教材讲解。"
    elif textbook_explanation_allowed and requested_page is None:
        summary = "原题与原书答案的唯一关系已核验，可在证据范围内按教材讲解。"
    elif textbook_explanation_allowed:
        summary = "教材原页与所需结构化证据已核验，可在证据范围内按教材讲解。"
    elif status == "not_requested":
        summary = "本次未请求按页核验。"
    else:
        summary = "教材原页尚未完成可用于按书讲解的核验。"
    return {
        "page_location_status": status,
        "exercise_verification_status": exercise_status,
        "page_crosscheck_status": crosscheck_status,
        "answer_mode": answer_mode,
        "textbook_explanation_allowed": textbook_explanation_allowed,
        "summary": summary,
    }


def _page_number_from_value(value: Any) -> int | None:
    match = re.search(r"([0-9]+)", str(value or ""))
    return int(match.group(1)) if match else None


def evidence_page_refs(evidence: dict[str, Any]) -> list[dict[str, Any]]:
    return [item for item in list(evidence.get("page_classification_refs", []) or []) if isinstance(item, dict)]


def find_requested_page_ref(evidence: dict[str, Any], requested_page: int | None) -> dict[str, Any]:
    if requested_page is None:
        return {}
    for ref in evidence_page_refs(evidence):
        if int(ref.get("printed_page", 0) or 0) == requested_page:
            return ref
    return {}


def _page_anchor_score(evidence: dict[str, Any], anchor: dict[str, Any]) -> float:
    requested_page = anchor.get("requested_page")
    if requested_page is None:
        return 0.0
    refs = evidence_page_refs(evidence)
    if not refs:
        return 0.0
    if find_requested_page_ref(evidence, requested_page):
        return 6.0
    return -1.5


def _page_locator_matches_requested_page(locator: dict[str, Any], requested_page: int) -> bool:
    for key in ("page_start", "page_end"):
        value = _page_number_from_value(locator.get(key))
        if value == requested_page:
            return True
    return False


def _position_matches_bbox(candidate: dict[str, Any], same_page_candidates: list[dict[str, Any]], requested_position: str | None) -> bool:
    if not requested_position:
        return True
    bbox = list(candidate.get("bbox") or [])
    if len(bbox) < 4:
        return True
    all_bottoms = [float((item.get("bbox") or [0, 0, 0, 0])[3]) for item in same_page_candidates if len(item.get("bbox") or []) >= 4]
    all_tops = [float((item.get("bbox") or [0, 0, 0, 0])[1]) for item in same_page_candidates if len(item.get("bbox") or []) >= 4]
    if not all_bottoms or not all_tops:
        return True
    min_top = min(all_tops)
    max_bottom = max(all_bottoms)
    span = max(max_bottom - min_top, 1.0)
    center = (float(bbox[1]) + float(bbox[3])) / 2.0
    ratio = (center - min_top) / span
    if requested_position == "top":
        return ratio <= 0.34
    if requested_position == "middle":
        return 0.25 <= ratio <= 0.75
    if requested_position == "bottom":
        return ratio >= 0.60
    return True


def build_page_anchor(evidences: list[dict[str, Any]], anchor: dict[str, Any]) -> dict[str, Any]:
    requested_page = anchor.get("requested_page")
    requested_position = anchor.get("requested_position")
    payload = {
        "requested_page": requested_page,
        "requested_position": requested_position,
        "matched_evidence_id": "",
        "matched_chunk_id": "",
        "snippets": [],
    }
    if requested_page is None:
        return payload
    matches = [item for item in evidences if find_requested_page_ref(item, requested_page)]
    if len(matches) != 1:
        return payload
    matched = matches[0]
    payload["matched_evidence_id"] = matched.get("evidence_id", "")
    payload["matched_chunk_id"] = matched.get("chunk_id", "")
    chunk_path = str(matched.get("chunk_extract_path", "")).strip()
    if not chunk_path:
        return payload
    path = Path(chunk_path)
    if not path.exists():
        return payload
    chunk_payload = load_json(path)
    candidates = [item for item in list(chunk_payload.get("ocr_chunk_candidates", []) or []) if isinstance(item, dict)]
    same_page_candidates = [
        item
        for item in candidates
        if _page_locator_matches_requested_page(
            (((item.get("source_span") or {}).get("locator")) or {}),
            requested_page,
        )
    ]
    filtered = [item for item in same_page_candidates if _position_matches_bbox(item, same_page_candidates, requested_position)]
    chosen = filtered or same_page_candidates
    snippets: list[str] = []
    for item in chosen:
        text = str(item.get("text", "")).strip()
        if text and text not in snippets:
            snippets.append(text)
    payload["snippets"] = snippets[:3]
    return payload


def exact_evidence_hits_for_locator(subject: str, chapter: str | None, locator: dict[str, Any]) -> list[dict[str, Any]]:
    layout = ensure_kb_layout()
    matches: list[dict[str, Any]] = []
    for evidence_id in locator.get("evidence_ids", []) or []:
        path = layout["evidence"] / f"{evidence_id}.json"
        if not path.is_file():
            continue
        evidence = load_json(path)
        if not is_publishable_source_evidence(evidence) or evidence.get("subject") != subject:
            continue
        # The locator already binds this evidence to the requested book and
        # rendered PDF page.  A page-level OCR record may expose only the
        # chapter title (rather than a subsection such as 3.1), so a supplied
        # subsection must not downgrade exact page evidence to exact_asset.
        if evidence_matches_locator(evidence, locator):
            matches.append(evidence)
    return sorted(matches, key=lambda item: item.get("evidence_id", ""))


def apply_hard_page_route(
    *,
    subject: str,
    chapter: str | None,
    book_title: str | None,
    request: dict[str, Any],
    retrieval_hits: list[dict],
    claims: list[dict],
) -> tuple[dict[str, Any], list[dict], list[dict], list[dict]]:
    locator = resolve_page_locator(
        subject=subject,
        book_title=book_title,
        printed_page=int(request["requested_page"]),
        exercise_label=str(request.get("requested_exercise_label") or ""),
    )
    locator["requested_position"] = request.get("requested_position")
    locator["requested_container_path"] = list(request.get("requested_container_path") or [])
    if locator["match_status"] != "exact_asset":
        return locator, [], [], []

    exact_evidences = exact_evidence_hits_for_locator(subject, chapter, locator)
    exact_ids = {item.get("evidence_id") for item in exact_evidences}
    exact_claims = [
        claim for claim in claims
        if exact_ids.intersection(set(claim.get("evidence_ids", []) or []))
    ]
    exact_retrieval = [
        hit for hit in retrieval_hits
        if hit.get("entity_id") in exact_ids or exact_ids.intersection(set(hit.get("references", []) or []))
    ]
    if exact_evidences:
        legacy_anchor = build_page_anchor(exact_evidences, request)
        for key in ("matched_evidence_id", "matched_chunk_id", "snippets"):
            locator[key] = legacy_anchor.get(key, locator.get(key))
        first_exact = exact_evidences[0]
        locator["matched_evidence_id"] = locator.get("matched_evidence_id") or first_exact.get("evidence_id", "")
        locator["matched_chunk_id"] = locator.get("matched_chunk_id") or first_exact.get("chunk_id", "")
        if not locator.get("snippets") and first_exact.get("content"):
            locator["snippets"] = [str(first_exact["content"]).strip()[:500]]
        locator["match_status"] = "exact_evidence"
        label = str(locator.get("requested_exercise_label") or "")
        if label:
            normalized_label = normalize_text(label).replace(" ", "")
            haystack = normalize_text("\n".join(locator.get("snippets", []))).replace(" ", "")
            locator["exercise_match_status"] = "matched" if normalized_label in haystack else "unverified"
    return locator, exact_retrieval, exact_claims, exact_evidences


def apply_exercise_relation(locator: dict[str, Any], evidences: list[dict[str, Any]]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    requested_label = str(locator.get("requested_exercise_label") or "").strip()
    if requested_label.startswith("例"):
        if locator.get("match_status") != "exact_evidence":
            return {"status": "unverified", "exercise_label": requested_label}, evidences
        relation = find_exact_worked_example_relation(
            book_id=str(locator.get("book_id") or ""),
            printed_page=int(locator.get("requested_page") or 0),
            exercise_label=requested_label,
            container_path=list(locator.get("requested_container_path") or []),
        )
        if relation.get("relation_status") == "unavailable":
            locator["exercise_match_status"] = "unavailable"
            return {
                "status": "unavailable",
                "exercise_label": requested_label,
                "reason": relation.get("unavailable_reason", "exercise_locator_index_unavailable"),
                "detail": relation.get("unavailable_detail", ""),
                "_availability": dict(relation.get("_availability") or {}),
            }, evidences
        if not relation:
            locator["exercise_match_status"] = "unverified"
            return {"status": "unverified", "exercise_label": requested_label, "reason": "source-answer-not-found"}, evidences
        if relation.get("relation_status") != "exact":
            locator["exercise_match_status"] = "unverified"
            return {
                "status": "ambiguous",
                "exercise_label": requested_label,
                "relation_id": relation.get("relation_id", ""),
                "candidate_labels": [
                    str(item.get("exercise_label") or "")
                    for item in relation.get("candidates", []) or []
                    if item.get("exercise_label")
                ],
                "candidate_locations": list(relation.get("candidate_locations") or []),
                "reason": "source-answer-relation-needs-review",
            }, evidences
        question = dict(relation.get("question") or {})
        answer = dict(relation.get("answer") or {})
        linked_ids = list(answer.get("evidence_ids") or [])
        layout = ensure_kb_layout()
        linked = [
            evidence
            for item in linked_ids
            if (layout["evidence"] / f"{item}.json").is_file()
            for evidence in [load_json(layout["evidence"] / f"{item}.json")]
            if is_publishable_source_evidence(evidence)
        ]
        existing_ids = {item.get("evidence_id") for item in evidences}
        locator["exercise_match_status"] = "matched"
        return {
            "status": "exact_answer_evidence",
            "relation_id": relation.get("relation_id", ""),
            "exercise_label": requested_label,
            "container_path": list(relation.get("container_path") or []),
            "container_ordinal": relation.get("container_ordinal"),
            "location_key": relation.get("location_key", ""),
            "question_printed_pages": question.get("printed_pages", []),
            "answer_printed_pages": answer.get("printed_pages", []),
            "question_evidence_ids": question.get("evidence_ids", []),
            "answer_evidence_ids": linked_ids,
            "question_source_image_paths": question.get("source_image_paths", []),
            "answer_source_image_paths": answer.get("source_image_paths", []),
            "question_content": question.get("content", ""),
            "answer_content": answer.get("content", ""),
        }, evidences + [item for item in linked if item.get("evidence_id") not in existing_ids]

    label = normalize_exercise_label(requested_label)
    if locator.get("match_status") != "exact_evidence" or not label:
        return {"status": "not_requested" if not label else "unverified"}, evidences
    pdf_page = int(locator.get("pdf_page", 0) or 0)
    relation = find_exact_relation(
        source_id=str(locator.get("source_id") or ""),
        question_pdf_page=pdf_page,
        exercise_label=label,
    )
    if relation.get("relation_status") == "unavailable":
        locator["exercise_match_status"] = "unavailable"
        return {
            "status": "unavailable",
            "exercise_label": label,
            "reason": relation.get("unavailable_reason", "exercise_locator_index_unavailable"),
            "detail": relation.get("unavailable_detail", ""),
            "_availability": dict(relation.get("_availability") or {}),
        }, evidences
    if not relation:
        locator["exercise_match_status"] = "unverified"
        return {"status": "unverified", "exercise_label": label, "candidate_pages": []}, evidences
    layout = ensure_kb_layout()
    linked_ids = list(relation.get("answer_evidence_ids", []) or [])
    linked = []
    for evidence_id in linked_ids:
        path = layout["evidence"] / f"{evidence_id}.json"
        if path.is_file():
            evidence = load_json(path)
            if is_publishable_source_evidence(evidence):
                linked.append(evidence)
    locator["exercise_match_status"] = "matched"
    existing_ids = {item.get("evidence_id") for item in evidences}
    combined = evidences + [item for item in linked if item.get("evidence_id") not in existing_ids]
    anchor, combined = assemble_exact_relation(relation, evidences=combined, exercise_label=label)
    if anchor.get("status") != "exact_answer_evidence":
        locator["exercise_match_status"] = "unverified"
    return anchor, combined


def _exact_relation_anchor(relation: dict[str, Any], evidences: list[dict[str, Any]], label: str) -> dict[str, Any]:
    anchor, _ = assemble_exact_relation(relation, evidences=evidences, exercise_label=label)
    return anchor


def apply_scoped_exercise_relation(*, book_title: str | None, chapter: str | None, exercise_label: str, category: str = "", query: str = "") -> tuple[dict[str, Any], list[dict[str, Any]]]:
    relation = find_unique_relation_for_scope(book_title=str(book_title or ""), chapter=str(chapter or ""), exercise_label=exercise_label, category=category, query=query)
    label = normalize_exercise_label(exercise_label)
    if relation.get("relation_status") == "unavailable":
        return {
            "status": "unavailable",
            "exercise_label": label,
            "reason": relation.get("unavailable_reason", "exercise_locator_index_unavailable"),
            "detail": relation.get("unavailable_detail", ""),
            "_availability": dict(relation.get("_availability") or {}),
        }, []
    if not relation:
        return {"status": "unverified", "exercise_label": label, "reason": "current-book-and-chapter-not-unique-or-uncovered"}, []
    layout = ensure_kb_layout()
    ids = list(relation.get("question_evidence_ids", []) or []) + list(relation.get("answer_evidence_ids", []) or [])
    evidence = []
    for item in ids:
        path = layout["evidence"] / f"{item}.json"
        if not path.is_file():
            continue
        payload = load_json(path)
        if is_publishable_source_evidence(payload):
            evidence.append(payload)
    return assemble_exact_relation(relation, evidences=evidence, exercise_label=label)


def build_answer_grounding(
    *,
    query: str,
    book_title: str | None,
    page_anchor: dict[str, Any],
    exercise_anchor: dict[str, Any],
    evidences: list[dict[str, Any]],
    page_crosscheck: dict[str, Any] | None = None,
) -> dict[str, Any]:
    request_kind = classify_source_request(
        query=query,
        book_title=book_title,
        page_anchor=page_anchor,
        exercise_label=str(exercise_anchor.get("exercise_label") or ""),
    )
    required = request_kind == "exercise"
    grounding = {
        "required": required,
        "status": "answer_not_found" if required else "not_applicable",
        "can_conclude": not required,
        "problem": {},
        "solution": {},
        "failure_reason": "尚未定位原书题解。" if required else "",
        "next_action": "请补充教材名、页码或题号后重新定位原书答案。" if required else "",
    }
    if not required:
        return grounding
    crosscheck = dict(page_crosscheck or {})
    if crosscheck.get("required") and crosscheck.get("status") != "confirmed":
        crosscheck_status = str(crosscheck.get("status") or "unverified")
        if crosscheck_status in {"conflict", "ambiguous"}:
            grounding.update(
                status="answer_ambiguous",
                failure_reason=str(crosscheck.get("reason") or "页码线索与小节标题锚点冲突。"),
                next_action="请核对教材版本、小节编号或起始页后重新检索。",
            )
        else:
            grounding.update(
                status="answer_unavailable",
                failure_reason=str(crosscheck.get("reason") or "页码线索尚未完成小节锚点核验。"),
                next_action="请先补齐正式页码映射或小节标题锚点。",
            )
        return grounding
    page_status = str(page_anchor.get("match_status") or "")
    if page_status == "unavailable":
        grounding.update(status="answer_unavailable", failure_reason="本地证据链当前不可用。", next_action="请先修复配置或正式页码索引。")
        return grounding
    if page_status == "exact_asset":
        grounding.update(status="answer_asset_only", failure_reason="只定位到原题图片，原书答案正文未确认。", next_action="如需继续，请明确允许人工核对答案原图，或先发布题解证据。")
        return grounding
    if page_status == "ambiguous":
        grounding.update(status="answer_ambiguous", failure_reason="原题页存在多个教材候选。", next_action="请先确认教材名称。")
        return grounding
    if exercise_anchor.get("status") == "unavailable":
        grounding.update(
            status="answer_unavailable",
            failure_reason=f"习题关系索引当前不可用（{exercise_anchor.get('reason') or 'exercise_locator_index_unavailable'}）。",
            next_action="请先运行 kb.py sync --indexes-only 重建习题关系索引。",
        )
        return grounding
    if exercise_anchor.get("status") == "ambiguous":
        if exercise_anchor.get("reason") == "exercise-label-missing":
            candidates = "、".join(str(item) for item in exercise_anchor.get("candidate_labels", []) if str(item))
            suffix = f"；本页候选题号为 {candidates}" if candidates else ""
            grounding.update(
                status="answer_ambiguous",
                failure_reason=f"教材和页码已确认，但当前描述不足以唯一确定题目{suffix}。",
                next_action="目前只缺题号，请告诉我是第几题。",
            )
        else:
            grounding.update(status="answer_ambiguous", failure_reason="题目与原书答案的关系存在歧义。", next_action="请先审核题目—题解关系。")
        return grounding
    if exercise_anchor.get("status") != "exact_answer_evidence":
        if exercise_anchor.get("reason") == "exercise-label-missing":
            grounding.update(
                failure_reason="教材和页码已确认，但当前描述不足以唯一确定题目。",
                next_action="目前只缺题号，请告诉我是第几题。",
            )
        elif exercise_anchor.get("reason") == "page-exercise-relations-not-found":
            grounding.update(
                failure_reason="教材原页已确认，但该页没有可用于答案配对的正式题目关系。",
                next_action="请补充准确题号；若仍无法定位，再审核该页题目—题解关系。",
            )
        return grounding

    evidence_by_id = {str(item.get("evidence_id") or ""): item for item in evidences}
    question_ids = [str(item) for item in exercise_anchor.get("question_evidence_ids", []) if str(item)]
    answer_ids = [str(item) for item in exercise_anchor.get("answer_evidence_ids", []) if str(item)]
    problem_content = str(exercise_anchor.get("question_content") or "").strip() or "\n".join(str(evidence_by_id[item].get("content") or "") for item in question_ids if item in evidence_by_id).strip()
    answer_content = str(exercise_anchor.get("answer_content") or "").strip() or "\n".join(str(evidence_by_id[item].get("content") or "") for item in answer_ids if item in evidence_by_id).strip()
    grounding["problem"] = {
        "evidence_ids": question_ids,
        "book_title": str(book_title or page_anchor.get("book_title") or ""),
        "exercise_label": str(exercise_anchor.get("exercise_label") or ""),
        "printed_pages": list(exercise_anchor.get("question_printed_pages", []) or []),
        "pdf_pages": list(exercise_anchor.get("question_pdf_pages", []) or []),
        "source_image_paths": list(exercise_anchor.get("question_source_image_paths", []) or []),
        "content": problem_content,
    }
    grounding["solution"] = {
        "evidence_ids": answer_ids,
        "book_title": str(book_title or page_anchor.get("book_title") or ""),
        "exercise_label": str(exercise_anchor.get("exercise_label") or ""),
        "printed_pages": list(exercise_anchor.get("answer_printed_pages", []) or []),
        "pdf_pages": list(exercise_anchor.get("answer_pdf_pages", []) or []),
        "source_image_paths": list(exercise_anchor.get("answer_source_image_paths", []) or []),
        "content": answer_content,
    }
    if question_ids and answer_ids and problem_content and answer_content:
        grounding.update(status="exact_answer", can_conclude=True, failure_reason="", next_action="")
    return grounding


def build_teaching_bundle(grounding: dict[str, Any], request_resolution: dict[str, Any]) -> dict[str, Any]:
    if not grounding.get("required"):
        return {
            "status": "not_applicable",
            "problem_text": "",
            "source_answer_text": "",
            "requested_option": "",
            "exercise_label": "",
            "citations": {
                "problem_evidence_ids": [],
                "solution_evidence_ids": [],
                "problem_pdf_pages": [],
                "solution_pdf_pages": [],
            },
            "failure_reason": "",
        }
    can_conclude = bool(grounding.get("can_conclude")) and grounding.get("status") == "exact_answer"
    problem = dict(grounding.get("problem") or {})
    solution = dict(grounding.get("solution") or {})
    return {
        "status": "exact" if can_conclude else "blocked",
        "problem_text": str(problem.get("content") or "") if can_conclude else "",
        "source_answer_text": str(solution.get("content") or "") if can_conclude else "",
        "requested_option": str(request_resolution.get("requested_option") or ""),
        "exercise_label": str(request_resolution.get("exercise_label") or problem.get("exercise_label") or ""),
        "citations": {
            "problem_evidence_ids": list(problem.get("evidence_ids", []) or []),
            "solution_evidence_ids": list(solution.get("evidence_ids", []) or []),
            "problem_pdf_pages": list(problem.get("pdf_pages", []) or []),
            "solution_pdf_pages": list(solution.get("pdf_pages", []) or []),
        },
        "failure_reason": "" if can_conclude else str(grounding.get("failure_reason") or "原题或原书答案未确认。"),
    }


def _empty_page_content_bundle(
    *,
    status: str = "not_applicable",
    failure_reason: str = "",
    next_action: str = "",
    page_anchor: dict[str, Any] | None = None,
) -> dict[str, Any]:
    anchor = dict(page_anchor or {})
    printed_page = anchor.get("requested_page") if anchor.get("requested_page") is not None else anchor.get("printed_page")
    pdf_page = anchor.get("pdf_page")
    return {
        "status": status,
        "source_id": str(anchor.get("source_id") or anchor.get("book_id") or ""),
        "book_title": str(anchor.get("book_title") or anchor.get("requested_book_title") or ""),
        "evidence_ids": [],
        "printed_pages": [int(printed_page)] if printed_page is not None else [],
        "pdf_pages": [int(pdf_page)] if pdf_page is not None else [],
        "content": "",
        "failure_reason": failure_reason,
        "next_action": next_action,
    }


def _reviewed_page_evidence(evidence: dict[str, Any]) -> bool:
    return is_publishable_source_evidence(evidence)


def build_page_content_bundle(
    *,
    request_resolution: dict[str, Any],
    page_anchor: dict[str, Any],
    evidences: list[dict[str, Any]],
    page_crosscheck: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Expose only reviewed evidence from the exact requested textbook page."""
    if str(request_resolution.get("source_request_kind") or "generic") != "page_content":
        return _empty_page_content_bundle()
    crosscheck = dict(page_crosscheck or {})
    if crosscheck.get("required") and str(crosscheck.get("status") or "") != "confirmed":
        return _empty_page_content_bundle(
            status="blocked",
            failure_reason=str(crosscheck.get("reason") or "页码线索与正式小节锚点尚未完成交叉核验。"),
            next_action="请先核对教材版本、小节编号和起始页。",
            page_anchor=page_anchor,
        )
    page_status = str(page_anchor.get("match_status") or "not_found")
    if page_status == "exact_asset":
        return _empty_page_content_bundle(
            status="asset_only",
            failure_reason="教材原页已定位，但教材正文尚未审核发布。",
            next_action="如需继续，请先人工核对原页或发布审核后的页面证据。",
            page_anchor=page_anchor,
        )
    if page_status != "exact_evidence":
        reasons = {
            "unavailable": "本地证据链当前不可用。",
            "ambiguous": "存在多个教材或页面候选，无法唯一定位页面正文。",
            "unmapped": "该印刷页尚未建立正式页码映射。",
            "not_requested": "尚未给出可唯一定位的教材页码。",
            "not_found": "正式页码索引中没有找到该印刷页。",
        }
        return _empty_page_content_bundle(
            status="blocked",
            failure_reason=reasons.get(page_status, "教材页面正文尚未确认。"),
            next_action="请先补齐唯一教材、印刷页码和审核证据。",
            page_anchor=page_anchor,
        )

    anchor_ids = [str(item) for item in page_anchor.get("evidence_ids", []) if str(item)]
    if page_anchor.get("matched_evidence_id"):
        anchor_ids.insert(0, str(page_anchor["matched_evidence_id"]))
    anchor_ids = list(dict.fromkeys(anchor_ids))
    by_id = {str(item.get("evidence_id") or ""): item for item in evidences}
    source_id = str(page_anchor.get("source_id") or page_anchor.get("book_id") or "")
    printed_page = page_anchor.get("requested_page")
    pdf_page = page_anchor.get("pdf_page")
    reviewed: list[dict[str, Any]] = []
    for evidence_id in anchor_ids:
        evidence = by_id.get(evidence_id)
        if not evidence or not _reviewed_page_evidence(evidence):
            continue
        if source_id and evidence.get("source_id") and str(evidence.get("source_id")) != source_id:
            continue
        if printed_page is not None and evidence.get("printed_page") is not None and int(evidence["printed_page"]) != int(printed_page):
            continue
        if pdf_page is not None and evidence.get("pdf_page") is not None and int(evidence["pdf_page"]) != int(pdf_page):
            continue
        if str(evidence.get("content") or "").strip():
            reviewed.append(evidence)
    if not reviewed:
        return _empty_page_content_bundle(
            status="blocked",
            failure_reason="页面已定位，但没有同源、同页且审核通过的正文证据。",
            next_action="请先审核并发布该页 evidence。",
            page_anchor=page_anchor,
        )

    return {
        "status": "exact",
        "source_id": source_id,
        "book_title": str(page_anchor.get("book_title") or page_anchor.get("requested_book_title") or ""),
        "evidence_ids": [str(item.get("evidence_id") or "") for item in reviewed],
        "printed_pages": [int(printed_page)] if printed_page is not None else [],
        "pdf_pages": [int(pdf_page)] if pdf_page is not None else [],
        "content": "\n\n".join(str(item.get("content") or "").strip() for item in reviewed),
        "failure_reason": "",
        "next_action": "",
    }


def attach_exercise_index_runtime(result: dict[str, Any]) -> None:
    availability = dict((result.get("exercise_anchor") or {}).get("_availability") or {})
    if not availability:
        return
    runtime = result.setdefault("runtime_context", {})
    runtime["exercise_locator_index_available"] = bool(availability.get("available", False))
    runtime["exercise_locator_index_path"] = str(availability.get("path") or "")
    runtime["exercise_locator_unavailable_reason"] = str(availability.get("reason") or "")


def tokenize(query: str) -> list[str]:
    tokens: list[str] = []
    for chunk in re.findall(r"[A-Za-z0-9]+|[\u4e00-\u9fff]+", query):
        value = normalize_text(chunk)
        if not value:
            continue
        if re.fullmatch(r"[\u4e00-\u9fff]+", value):
            max_n = min(4, len(value))
            for size in range(1, max_n + 1):
                for idx in range(0, len(value) - size + 1):
                    tokens.append(value[idx : idx + size])
        else:
            tokens.append(value)
    seen: list[str] = []
    for token in tokens:
        if token and token not in seen:
            seen.append(token)
    return seen


def compare_parts(query: str) -> list[str]:
    cleaned = re.sub(r"(怎么区分|如何区分|怎么区别|如何区别|区别|区分|比较|对比|有什么不同|有什么区别)", " ", query)
    parts = re.split(r"[和与跟及、/]|vs|VS", cleaned)
    normalized = [
        re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", normalize_text(part))
        for part in parts
        if normalize_text(part)
    ]
    compact = [part for part in normalized if len(part) <= 12]
    return compact[:3]


def generic_topic_terms(query: str, intent: str = "define") -> list[str]:
    """Extract topic terms while dropping question and routing boilerplate."""
    text = unicodedata.normalize("NFKC", str(query or "")).lower()
    for phrase in sorted(GENERIC_QUERY_NOISE, key=len, reverse=True):
        text = text.replace(phrase, " ")
    text = text.replace("的", " ")
    text = re.sub(r"[？?。！!，,；;：:（）()\[\]{}]", " ", text)
    parts = re.split(r"\s*(?:和|与|跟|及|、|/|vs)\s*", text, flags=re.IGNORECASE)
    terms: list[str] = []
    for part in parts:
        term = re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", part)
        if not term or term in {"我", "想", "了解", "一下", "请", "帮"}:
            continue
        if len(term) == 1 and not re.search(r"[\u4e00-\u9fff]", term):
            continue
        if term not in terms:
            terms.append(term)
    if intent == "compare":
        compare_terms: list[str] = []
        for raw in compare_parts(query):
            term = re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", str(raw).lower())
            if term and term not in {"什么", "不同"} and term not in compare_terms:
                compare_terms.append(term)
        return compare_terms
    return terms[:5]


def topic_coverage(candidate_text: Any, topic_terms: list[str] | None) -> dict[str, Any]:
    """Return deterministic topic coverage for one candidate."""
    terms = [str(term).strip() for term in (topic_terms or []) if str(term).strip()]
    normalized = re.sub(r"\s+", "", unicodedata.normalize("NFKC", str(candidate_text or "")).lower())
    matched = [term for term in terms if term in normalized]
    return {
        "topic_terms": terms,
        "matched_terms": list(dict.fromkeys(matched)),
        "ok": bool(terms) and len(matched) == len(terms),
    }


def _claim_topic_text(claim: dict[str, Any]) -> str:
    variants = claim.get("variants") or []
    if isinstance(variants, str):
        variants = [variants]
    return "\n".join(
        str(value or "")
        for value in [claim.get("text"), claim.get("canonical_text"), *variants]
    )


def score_text(text: str, tokens: list[str], full_query: str) -> float:
    hay = normalize_text(text)
    if not hay:
        return 0.0
    score = 0.0
    if full_query and full_query in hay:
        score += 1.5
    for token in tokens:
        if token in hay:
            score += 0.25
    return score


def title_match_score(title: str, aliases: list[str], keywords: list[str], tokens: list[str], parts: list[str], intent: str) -> float:
    haystacks = [normalize_text(title), *[normalize_text(alias) for alias in aliases], *[normalize_text(keyword) for keyword in keywords]]
    score = 0.0
    for hay in haystacks:
        if not hay:
            continue
        score += score_text(hay, tokens, normalize_text(title)) * 0.4
    if intent == "compare" and parts:
        matched_parts = 0
        for part in parts:
            if any(part in hay for hay in haystacks):
                matched_parts += 1
                score += 1.2
        if matched_parts >= 2:
            score += 3.0
    if normalize_text(title) in parts:
        score += 2.0
    return score


def load_syllabus(subject: str) -> tuple[dict, dict]:
    layout = ensure_kb_layout()
    tree_path = layout["syllabus"] / f"{subject}.json"
    if not tree_path.exists():
        return {"subject": subject, "nodes": []}, {"subject": subject, "aliases": {}}
    tree = load_json(tree_path)
    aliases_path = layout["syllabus"] / f"{subject}.aliases.json"
    aliases = load_json(aliases_path) if aliases_path.exists() else {"aliases": {}}
    return tree, aliases


def route_syllabus_nodes(subject: str, query: str, topk: int, intent: str) -> list[dict]:
    tree, aliases = load_syllabus(subject)
    full_query = normalize_text(query)
    tokens = tokenize(query)
    parts = compare_parts(query) if intent == "compare" else []
    routed: list[dict] = []
    for node in tree.get("nodes", []):
        alias_list = aliases.get("aliases", {}).get(node["node_id"], [])
        score = score_text(node.get("title", ""), tokens, full_query) * 1.6
        for alias in alias_list:
            score += score_text(alias, tokens, full_query) * 1.2
        for keyword in node.get("keywords", []):
            score += score_text(keyword, tokens, full_query) * 0.6
        score += title_match_score(node.get("title", ""), alias_list, node.get("keywords", []), tokens, parts, intent)
        if score > 0:
            routed.append({"node_id": node["node_id"], "title": node["title"], "score": round(score, 2)})
    routed.sort(key=lambda item: (-item["score"], item["node_id"]))
    return routed[: max(topk, 1)]


def learner_snapshot(subject: str) -> dict:
    files = learner_file_map()
    model = load_json(files["learner_model"]) if files["learner_model"].exists() else {}
    return model.get("subjects", {}).get(subject, {})


def learner_compare_candidates(subject: str, chapter: str | None) -> list[dict]:
    files = learner_file_map()
    refinement = load_json(files["refinement_queue"]) if files["refinement_queue"].exists() else {"items": []}
    results = []
    for item in refinement.get("items", []):
        if item.get("subject") != subject:
            continue
        if chapter and not chapter_matches(chapter, item.get("chapter_title", "")):
            continue
        if item.get("candidate_type") in {"补比较 claim 候选", "映射修正候选", "补诊断解释候选"}:
            results.append(item)
    return results


def claim_hits_from_retrieval(
    subject: str,
    chapter: str | None,
    node_ids: list[str],
    retrieval_hits: list[dict],
    intent: str,
    book_title: str | None = None,
    topic_terms: list[str] | None = None,
) -> list[dict]:
    layout = ensure_kb_layout()
    evidence_by_id = {
        str(evidence.get("evidence_id") or "").strip(): evidence
        for evidence in load_all_json(layout["evidence"])
        if str(evidence.get("evidence_id") or "").strip()
    }
    results: list[dict] = []
    intent_weight = {
        "compare": {"comparison": 1.8, "confusion": 1.5, "definition": 0.8, "rule": 0.6},
        "diagnose": {"confusion": 1.8, "comparison": 1.0, "rule": 0.7, "definition": 0.5},
        "plan": {"example_type": 1.3, "rule": 0.8, "definition": 0.6},
        "define": {"definition": 1.4, "rule": 1.0, "comparison": 0.5, "confusion": 0.4},
    }
    type_weights = intent_weight.get(intent, {})
    for retrieval_hit in retrieval_hits:
        if retrieval_hit.get("doc_type") != "claim":
            continue
        claim_id = str(retrieval_hit.get("entity_id", "")).strip()
        path = layout["claims"] / f"{claim_id}.json"
        if not claim_id or not path.exists():
            continue
        claim = load_json(path)
        if claim.get("subject") != subject:
            continue
        if chapter and not chapter_matches(chapter, claim.get("chapter_title", ""), claim.get("chapter_hint", "")):
            continue
        if node_ids and claim.get("syllabus_node_id") not in node_ids:
            continue
        if not is_publishable_claim(claim, evidence_by_id):
            continue
        if not _claim_support_evidence(claim, book_title):
            continue
        if topic_terms is not None and not topic_coverage(_claim_topic_text(claim), topic_terms).get("matched_terms"):
            continue
        score = float(retrieval_hit.get("score", 0.0) or 0.0)
        score += type_weights.get(claim.get("claim_type", ""), 0.0)
        if claim.get("syllabus_node_id") in node_ids:
            score += 0.8
        if score > 0 or node_ids:
            hit = dict(claim)
            hit["score"] = round(score, 2)
            results.append(hit)
    results.sort(key=lambda item: (-item["score"], -int(item.get("support_count", 0)), item.get("claim_id", "")))
    return results


def evidence_hits_from_retrieval(
    subject: str,
    chapter: str | None,
    node_ids: list[str],
    claim_list: list[dict],
    retrieval_hits: list[dict],
    tokens: list[str],
    full_query: str,
    page_anchor: dict[str, Any] | None = None,
    book_title: str | None = None,
    topic_terms: list[str] | None = None,
) -> list[dict]:
    layout = ensure_kb_layout()
    evidence_by_id = {
        str(evidence.get("evidence_id") or "").strip(): evidence
        for evidence in load_all_json(layout["evidence"])
        if str(evidence.get("evidence_id") or "").strip()
    }
    claim_evidence_ids = {
        eid
        for claim in claim_list
        if is_publishable_claim(claim, evidence_by_id)
        for eid in claim.get("evidence_ids", [])
    }
    results: list[dict] = []
    seen: set[str] = set()
    for retrieval_hit in retrieval_hits:
        evidence_ids = list(retrieval_hit.get("references", []))
        if retrieval_hit.get("doc_type") == "evidence":
            evidence_ids.append(retrieval_hit.get("entity_id", ""))
        for evidence_id in evidence_ids:
            evidence_id = str(evidence_id).strip()
            if not evidence_id or evidence_id in seen:
                continue
            path = layout["evidence"] / f"{evidence_id}.json"
            if not path.exists():
                continue
            evidence = load_json(path)
            if not is_publishable_source_evidence(evidence):
                continue
            seen.add(evidence_id)
            if evidence.get("subject") != subject:
                continue
            if book_title is not None and not evidence_matches_book(evidence, book_title):
                continue
            if chapter and not evidence_matches_chapter(evidence, chapter):
                continue
            if topic_terms is not None and not topic_coverage(
                f"{evidence.get('title', '')}\n{evidence.get('content', '')}", topic_terms
            ).get("matched_terms"):
                continue
            accepted_nodes = [item.get("node_id") for item in evidence.get("accepted_syllabus_nodes", [])]
            if node_ids and accepted_nodes and not set(node_ids).intersection(accepted_nodes):
                continue
            score = float(retrieval_hit.get("score", 0.0) or 0.0)
            score += score_text(evidence.get("title", ""), tokens, full_query)
            score += score_text(evidence.get("content", ""), tokens, full_query) * 0.5
            if evidence_id in claim_evidence_ids:
                score += 1.0
            score += _page_anchor_score(evidence, page_anchor or {})
            if score > 0 or evidence_id in claim_evidence_ids:
                hit = dict(evidence)
                hit["score"] = round(score, 2)
                results.append(hit)
    results.sort(key=lambda item: (-item["score"], item.get("evidence_id", "")))
    return results


def fallback_claim_hits(
    subject: str,
    chapter: str | None,
    node_ids: list[str],
    tokens: list[str],
    full_query: str,
    intent: str,
    book_title: str | None = None,
    topic_terms: list[str] | None = None,
) -> list[dict]:
    layout = ensure_kb_layout()
    evidence_by_id = {
        str(evidence.get("evidence_id") or "").strip(): evidence
        for evidence in load_all_json(layout["evidence"])
        if str(evidence.get("evidence_id") or "").strip()
    }
    type_weights = {
        "compare": {"comparison": 1.8, "confusion": 1.5, "definition": 0.8, "rule": 0.6},
        "diagnose": {"confusion": 1.8, "comparison": 1.0, "rule": 0.7, "definition": 0.5},
        "plan": {"example_type": 1.3, "rule": 0.8, "definition": 0.6},
        "define": {"definition": 1.4, "rule": 1.0, "comparison": 0.5, "confusion": 0.4},
    }.get(intent, {})
    results: list[dict] = []
    for claim in load_all_json(layout["claims"]):
        if claim.get("subject") != subject or not is_publishable_claim(claim, evidence_by_id):
            continue
        if chapter and not chapter_matches(chapter, claim.get("chapter_title", ""), claim.get("chapter_hint", "")):
            continue
        if node_ids and claim.get("syllabus_node_id") not in node_ids:
            continue
        if not _claim_support_evidence(claim, book_title):
            continue
        if topic_terms is not None and not topic_coverage(_claim_topic_text(claim), topic_terms).get("matched_terms"):
            continue
        score = score_text(claim.get("text", ""), tokens, full_query) + type_weights.get(claim.get("claim_type", ""), 0.0)
        if claim.get("syllabus_node_id") in node_ids:
            score += 0.8
        if score > 0 or node_ids:
            hit = dict(claim)
            hit["score"] = round(score, 2)
            results.append(hit)
    return sorted(results, key=lambda item: (-item["score"], -int(item.get("support_count", 0)), item.get("claim_id", "")))


def fallback_evidence_hits(
    subject: str,
    chapter: str | None,
    node_ids: list[str],
    claim_list: list[dict],
    tokens: list[str],
    full_query: str,
    page_anchor: dict[str, Any],
    book_title: str | None = None,
    topic_terms: list[str] | None = None,
) -> list[dict]:
    layout = ensure_kb_layout()
    evidence_by_id = {
        str(evidence.get("evidence_id") or "").strip(): evidence
        for evidence in load_all_json(layout["evidence"])
        if str(evidence.get("evidence_id") or "").strip()
    }
    claim_evidence_ids = {
        eid
        for claim in claim_list
        if is_publishable_claim(claim, evidence_by_id)
        for eid in claim.get("evidence_ids", [])
    }
    results: list[dict] = []
    for evidence in load_all_json(layout["evidence"]):
        if not is_publishable_source_evidence(evidence) or evidence.get("subject") != subject:
            continue
        if not is_publishable_source_evidence(evidence):
            continue
        if book_title is not None and not evidence_matches_book(evidence, book_title):
            continue
        if chapter and not evidence_matches_chapter(evidence, chapter):
            continue
        if topic_terms is not None and not topic_coverage(
            f"{evidence.get('title', '')}\n{evidence.get('content', '')}", topic_terms
        ).get("matched_terms"):
            continue
        accepted_nodes = [item.get("node_id") for item in evidence.get("accepted_syllabus_nodes", [])]
        if node_ids and accepted_nodes and not set(node_ids).intersection(accepted_nodes):
            continue
        score = score_text(evidence.get("title", ""), tokens, full_query)
        score += score_text(evidence.get("content", ""), tokens, full_query) * 0.5
        score += 1.0 if evidence.get("evidence_id") in claim_evidence_ids else 0.0
        score += _page_anchor_score(evidence, page_anchor)
        if score > 0 or evidence.get("evidence_id") in claim_evidence_ids:
            hit = dict(evidence)
            hit["score"] = round(score, 2)
            results.append(hit)
    return sorted(results, key=lambda item: (-item["score"], item.get("evidence_id", "")))


def is_stale_evidence(evidence: dict[str, Any]) -> bool:
    return evidence.get("verification_status") == "stale" or evidence.get("mapping_status") == "stale"


def evidence_matches_chapter(evidence: dict[str, Any], chapter: str | None) -> bool:
    if not chapter:
        return True
    refs = list(evidence.get("page_classification_refs", []) or [])
    for ref in refs:
        if chapter_matches(
            chapter,
            ref.get("chapter_title", ""),
            ref.get("section_title", ""),
        ):
            return True
    return chapter_matches(
        chapter,
        evidence.get("title", ""),
        evidence.get("chapter_title", ""),
        evidence.get("context_json_path", ""),
        evidence.get("chunk_extract_path", ""),
    )


def retrieval_hit_matches_chapter(hit: dict[str, Any], chapter: str | None) -> bool:
    """Fail closed when an indexed candidate cannot prove chapter membership."""
    if not chapter:
        return True
    layout = ensure_kb_layout()
    doc_type = str(hit.get("doc_type") or "")
    entity_id = str(hit.get("entity_id") or "")
    if doc_type == "evidence" and entity_id:
        path = layout["evidence"] / f"{entity_id}.json"
        return path.is_file() and evidence_matches_chapter(load_json(path), chapter)
    if doc_type == "claim" and entity_id:
        path = layout["claims"] / f"{entity_id}.json"
        if not path.is_file():
            return False
        claim = load_json(path)
        return chapter_matches(chapter, claim.get("chapter_id", ""), claim.get("chapter_title", ""))
    return False


def _chapter_registry_matches_book(item: dict[str, Any], book_title: str | None) -> bool:
    if book_title is None:
        return True
    requested = str(book_title or "").strip()
    if not requested:
        return False
    candidates = [item.get("book_title"), item.get("source_name"), item.get("book_id")]
    return any(_book_title_matches(value, requested) for value in candidates if str(value or "").strip())


def fallback_chapter_hits(
    vault_root: Path,
    subject: str,
    chapter: str | None,
    tokens: list[str],
    full_query: str,
    book_title: str | None = None,
    topic_terms: list[str] | None = None,
) -> list[dict]:
    path = vault_root / INDEX_DIRNAME / "chapter_knowledge_registry.json"
    if not path.exists():
        return []
    payload = load_json(path)
    results = []
    for item in payload.get("chapters", []):
        if item.get("subject") != subject:
            continue
        if not _chapter_registry_matches_book(item, book_title):
            continue
        if chapter and chapter not in str(item.get("chapter_title", "")):
            continue
        if topic_terms is not None and not topic_coverage(
            "\n".join(
                str(item.get(key) or "")
                for key in ("chapter_title", "chapter_overview", "question_entry")
            ),
            topic_terms,
        ).get("matched_terms"):
            continue
        score = score_text(item.get("chapter_title", ""), tokens, full_query)
        score += score_text(item.get("chapter_overview", ""), tokens, full_query)
        if score > 0:
            results.append(
                {
                    "chapter_title": item.get("chapter_title", ""),
                    "chapter_body": item.get("chapter_body", ""),
                    "question_entry": item.get("question_entry", ""),
                    "chapter_overview": item.get("chapter_overview", ""),
                    "score": round(score, 2),
                }
            )
    results.sort(key=lambda item: (-item["score"], item.get("chapter_title", "")))
    return results


def build_reference_items(evidences: list[dict], chapter: str | None = None) -> list[dict]:
    references = []
    page_entries = load_page_locator_index().get("entries", []) if any(item.get("origin_type") == "pdf_page_ocr" for item in evidences[:3]) else []
    for evidence in evidences[:3]:
        locator = evidence.get("locator", {})
        page_refs = list(evidence.get("page_classification_refs", []) or [])
        primary_ref = preferred_page_ref(evidence, page_refs)
        use_evidence_chapter = should_prefer_evidence_chapter(evidence, primary_ref)
        fallback_book_title = evidence.get("book_title", "")
        fallback_chapter_title = evidence.get("chapter_title", "")
        display_chapter_title = chapter or fallback_chapter_title
        use_requested_chapter = bool(chapter and not primary_ref)
        pdf_page = 0
        pdf_entry: dict[str, Any] = {}
        if evidence.get("origin_type") == "pdf_page_ocr":
            try:
                pdf_page = int(locator.get("page_start", 0) or 0)
            except (TypeError, ValueError):
                pdf_page = 0
            pdf_entry = next(
                (
                    item for item in page_entries
                    if str(item.get("source_id") or "") == str(evidence.get("source_id") or "")
                    and int(item.get("pdf_page", 0) or 0) == pdf_page
                ),
                {},
            )
        references.append(
            {
                "evidence_id": evidence.get("evidence_id", ""),
                "title": evidence.get("title", ""),
                "chunk_id": evidence.get("chunk_id", ""),
                "page_span": f"{locator.get('page_start', '')}-{locator.get('page_end', '')}",
                "image_span": f"{locator.get('image_start', '')}-{locator.get('image_end', '')}",
                "printed_page": int(pdf_entry.get("printed_page", 0) or primary_ref.get("printed_page", 0) or 0),
                "pdf_page": int(pdf_entry.get("pdf_page", 0) or (pdf_page if evidence.get("origin_type") == "pdf_page_ocr" else 0)),
                "page_classification_refs": page_refs,
                "book_id": primary_ref.get("book_id", ""),
                "book_title": fallback_book_title or primary_ref.get("book_title", ""),
                "book_chapter_title": display_chapter_title if (use_evidence_chapter or use_requested_chapter) else (primary_ref.get("chapter_title", "") or fallback_chapter_title),
                "section_title": primary_ref.get("section_title", ""),
                "chapter_view_path": primary_ref.get("chapter_view_path", ""),
                "section_view_path": primary_ref.get("section_view_path", ""),
            }
        )
    return references


def preferred_page_ref(evidence: dict[str, Any], page_refs: list[dict[str, Any]]) -> dict[str, Any]:
    chapter_id = str(evidence.get("chapter_id", "")).strip()
    if chapter_id:
        for ref in page_refs:
            if str(ref.get("chapter_id", "")).strip() == chapter_id:
                return ref
    return page_refs[0] if page_refs else {}


def should_prefer_evidence_chapter(evidence: dict[str, Any], primary_ref: dict[str, Any]) -> bool:
    evidence_chapter_id = str(evidence.get("chapter_id", "")).strip()
    ref_chapter_id = str(primary_ref.get("chapter_id", "")).strip()
    return bool(evidence_chapter_id and ref_chapter_id and evidence_chapter_id != ref_chapter_id)


def infer_route_from_hits(subject: str, topk: int, claims: list[dict], evidences: list[dict]) -> list[dict]:
    tree, _ = load_syllabus(subject)
    node_title_map = {item["node_id"]: item["title"] for item in tree.get("nodes", [])}
    inferred: list[dict] = []
    seen = set()
    for claim in claims:
        node_id = claim.get("syllabus_node_id", "")
        if node_id and node_id not in seen:
            inferred.append({"node_id": node_id, "title": node_title_map.get(node_id, node_id), "score": 0.5})
            seen.add(node_id)
    for evidence in evidences:
        for node in evidence.get("accepted_syllabus_nodes", []):
            node_id = node.get("node_id", "")
            if node_id and node_id not in seen:
                inferred.append({"node_id": node_id, "title": node.get("title", node_title_map.get(node_id, node_id)), "score": 0.4})
                seen.add(node_id)
    return inferred[: max(topk, 3)]


def retrieval_hit_matches_chapter(hit: dict[str, Any], chapter: str | None) -> bool:
    if not chapter:
        return True
    layout = ensure_kb_layout()
    entity_id = str(hit.get("entity_id", "")).strip()
    if hit.get("doc_type") == "evidence":
        path = layout["evidence"] / f"{entity_id}.json"
        return path.exists() and evidence_matches_chapter(load_json(path), chapter)
    if hit.get("doc_type") == "claim":
        path = layout["claims"] / f"{entity_id}.json"
        if not path.exists():
            return False
        claim = load_json(path)
        return chapter_matches(chapter, claim.get("chapter_title", ""), claim.get("chapter_hint", ""))
    return False


def retrieve_hits(
    subject: str,
    query: str,
    topk: int,
    chapter: str | None = None,
    book_title: str | None = None,
) -> list[dict]:
    try:
        payload = retrieve_index(ensure_kb_layout(), subject=subject, query=query, topk=max(topk * 5, 20))
    except SystemExit:
        return []
    hits = [
        hit
        for hit in payload.get("results", [])
        if retrieval_hit_matches_chapter(hit, chapter) and _retrieval_hit_matches_book(hit, book_title)
    ]
    return hits[: max(topk, 5)]


def route_from_retrieval_hits(subject: str, hits: list[dict], topk: int) -> list[dict]:
    if not hits:
        return []
    tree, _ = load_syllabus(subject)
    node_title_map = {item["node_id"]: item["title"] for item in tree.get("nodes", [])}
    scores: dict[str, float] = {}
    for hit in hits:
        for node_id in hit.get("syllabus_node_ids", []):
            if not node_id:
                continue
            scores[node_id] = max(scores.get(node_id, 0.0), float(hit.get("score", 0.0)))
    routed = [
        {"node_id": node_id, "title": node_title_map.get(node_id, node_id), "score": round(score, 2)}
        for node_id, score in scores.items()
    ]
    routed.sort(key=lambda item: (-item["score"], item["node_id"]))
    return routed[: max(topk, 3)]


def pick_node_for_part(routed: list[dict], part: str) -> dict | None:
    for item in routed:
        if normalize_text(part) in normalize_text(item.get("title", "")):
            return item
    return None


def best_claim_for_node(claims: list[dict], node_id: str) -> dict | None:
    preferred = [claim for claim in claims if claim.get("syllabus_node_id") == node_id]
    if not preferred:
        return None
    preferred.sort(
        key=lambda item: (
            {"comparison": 0, "confusion": 1, "definition": 2, "rule": 3, "example_type": 4}.get(item.get("claim_type", ""), 9),
            -float(item.get("score", 0)),
            item.get("claim_id", ""),
        )
    )
    return preferred[0]


def best_evidence_for_node(evidences: list[dict], node_id: str) -> dict | None:
    for evidence in evidences:
        accepted = [item.get("node_id") for item in evidence.get("accepted_syllabus_nodes", [])]
        if node_id in accepted:
            return evidence
    return None


def build_compare_bundle(routed: list[dict], claims: list[dict], evidences: list[dict], parts: list[str]) -> dict | None:
    if not routed:
        return None
    if parts:
        for node in routed:
            title = normalize_text(node.get("title", ""))
            if all(part in title for part in parts[:2]):
                primary_claim = best_claim_for_node(claims, node["node_id"])
                if primary_claim:
                    return {
                        "mode": "single_node",
                        "node": node,
                        "summary": f"{node['title']} 这个考点本身就在讲两者的判定边界与区分口径，当前问题应优先回到该节点回答。",
                        "primary_claim": primary_claim,
                    }
                primary_evidence = best_evidence_for_node(evidences, node["node_id"])
                if primary_evidence:
                    return {
                        "mode": "single_node_evidence",
                        "node": node,
                        "summary": f"{node['title']} 这个考点本身就在讲两者的判定边界与区分口径，当前问题先基于该节点的正式证据回答。",
                        "primary_evidence": primary_evidence,
                    }
        if len(parts) >= 2:
            left = pick_node_for_part(routed, parts[0])
            right = pick_node_for_part(routed, parts[1])
            if left and right and left["node_id"] != right["node_id"]:
                left_claim = best_claim_for_node(claims, left["node_id"])
                right_claim = best_claim_for_node(claims, right["node_id"])
                if left_claim and right_claim:
                    return {
                        "mode": "pair",
                        "left_node": left,
                        "right_node": right,
                        "summary": f"{left['title']} 主要回答“{parts[0]}是什么或如何定义”；{right['title']} 主要回答“{parts[1]}怎么判断、比较或表达”。",
                        "left_claim": left_claim,
                        "right_claim": right_claim,
                    }
        # A comparison question must not fall through to the top two routed
        # nodes: that fallback can turn a generic phrase such as “有什么区别”
        # into an unrelated comparison.
        return None
    if len(routed) < 2:
        return None
    left = routed[0]
    right = routed[1]
    left_claim = best_claim_for_node(claims, left["node_id"])
    right_claim = best_claim_for_node(claims, right["node_id"])
    if not left_claim or not right_claim:
        return None
    return {
        "mode": "pair",
        "left_node": left,
        "right_node": right,
        "summary": f"{left['title']} 和 {right['title']} 回答的不是同一层面的问题，比较时要先分清定义边界，再看判定或评价口径。",
        "left_claim": left_claim,
        "right_claim": right_claim,
    }


def _resolve_query_hits(
    subject: str,
    chapter: str | None,
    query: str,
    topk: int,
    intent: str,
    tokens: list[str],
    full_query: str,
    page_anchor_request: dict[str, Any],
    book_title: str | None = None,
    topic_terms: list[str] | None = None,
) -> tuple[list[dict], list[dict], list[dict], list[dict], bool]:
    retrieval_hits = retrieve_hits(subject, query, topk, chapter, book_title)
    routed = route_syllabus_nodes(subject, query, max(topk, 3), intent)
    if not routed:
        routed = route_from_retrieval_hits(subject, retrieval_hits, topk)
    node_ids = [item["node_id"] for item in routed]
    index_routed = bool(retrieval_hits)
    if index_routed:
        claims = claim_hits_from_retrieval(
            subject,
            chapter,
            node_ids,
            retrieval_hits,
            intent,
            book_title,
            topic_terms,
        )[:8]
        evidences = evidence_hits_from_retrieval(
            subject,
            chapter,
            node_ids,
            claims,
            retrieval_hits,
            tokens,
            full_query,
            page_anchor_request,
            book_title,
            topic_terms,
        )[:5]
    else:
        claims = fallback_claim_hits(
            subject,
            chapter,
            node_ids,
            tokens,
            full_query,
            intent,
            book_title,
            topic_terms,
        )[:8]
        evidences = fallback_evidence_hits(
            subject,
            chapter,
            node_ids,
            claims,
            tokens,
            full_query,
            page_anchor_request,
            book_title,
            topic_terms,
        )[:5]
    if not routed and (claims or evidences):
        routed = infer_route_from_hits(subject, topk, claims, evidences)
    return retrieval_hits, routed, claims, evidences, index_routed


def _resolve_answer_fallback(
    vault_root: Path,
    subject: str,
    chapter: str | None,
    tokens: list[str],
    full_query: str,
    topk: int,
    intent: str,
    claims: list[dict],
    evidences: list[dict],
    compare_bundle: dict | None,
    book_title: str | None = None,
    topic_terms: list[str] | None = None,
) -> tuple[str, str, list[dict]]:
    answer_mode = "canonical_claim" if claims else "accepted_evidence"
    fallback_note = ""
    fallback: list[dict] = []
    if not claims and not evidences:
        fallback = fallback_chapter_hits(
            vault_root,
            subject,
            chapter,
            tokens,
            full_query,
            book_title,
            topic_terms,
        )[: max(topk, 2)]
        if fallback:
            answer_mode = "chapter_fallback"
            fallback_note = "当前未命中正式主张，仅基于当前教材范围内的章节层回退。"
        else:
            answer_mode = "unconfirmed"
            fallback_note = "当前教材范围内没有找到与问题主题匹配的可发布证据，已停止跨书或章节回退。"
    elif intent in {"compare", "diagnose"} and not compare_bundle and not any(item.get("claim_type") in {"comparison", "confusion"} for item in claims):
        fallback_note = "当前缺少专门的比较/诊断主张，回答会更多依赖定义类主张和证据拼装。"
    return answer_mode, fallback_note, fallback


def _normalized_book_title(value: Any) -> str:
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", unicodedata.normalize("NFKC", str(value or "")).lower())


def _book_title_matches(actual: Any, requested: Any) -> bool:
    left = _normalized_book_title(actual)
    right = _normalized_book_title(requested)
    return bool(left and right and (left == right or left in right or right in left))


def evidence_matches_book(evidence: dict[str, Any], book_title: str | None) -> bool:
    """Require an evidence record to identify the requested textbook."""
    requested = str(book_title or "").strip()
    if not requested:
        return False
    titles = [evidence.get("book_title"), evidence.get("source_name")]
    titles.extend(
        ref.get("book_title")
        for ref in evidence.get("page_classification_refs", []) or []
        if isinstance(ref, dict)
    )
    return any(_book_title_matches(title, requested) for title in titles if str(title or "").strip())


def _claim_support_evidence(claim: dict[str, Any], book_title: str | None) -> list[dict[str, Any]]:
    """Load every claim support and require publishable, same-book evidence."""
    evidence_ids = [str(item).strip() for item in claim.get("evidence_ids", []) or [] if str(item).strip()]
    if not evidence_ids:
        return []
    layout = ensure_kb_layout()
    loaded: list[dict[str, Any]] = []
    for evidence_id in evidence_ids:
        path = layout["evidence"] / f"{evidence_id}.json"
        if not path.is_file():
            return []
        evidence = load_json(path)
        if not is_publishable_source_evidence(evidence):
            return []
        if book_title is not None and not evidence_matches_book(evidence, book_title):
            return []
        loaded.append(evidence)
    return loaded


def _retrieval_hit_matches_book(hit: dict[str, Any], book_title: str | None) -> bool:
    """Validate indexed candidates against the requested book before exposing them."""
    if book_title is None:
        return True
    requested = str(book_title or "").strip()
    if not requested:
        return False
    layout = ensure_kb_layout()
    entity_id = str(hit.get("entity_id") or "").strip()
    if hit.get("doc_type") == "evidence" and entity_id:
        path = layout["evidence"] / f"{entity_id}.json"
        if not path.is_file():
            return False
        evidence = load_json(path)
        return is_publishable_source_evidence(evidence) and evidence_matches_book(evidence, requested)
    if hit.get("doc_type") == "claim" and entity_id:
        path = layout["claims"] / f"{entity_id}.json"
        if not path.is_file():
            return False
        claim = load_json(path)
        return bool(_claim_support_evidence(claim, requested))
    return False


def _generic_candidate_records(
    *,
    claims: list[dict],
    evidences: list[dict],
    compare_bundle: dict | None,
    book_title: str,
    topic_terms: list[str],
) -> tuple[list[dict[str, Any]], list[str], bool]:
    """Return only publishable same-book candidates and their covered topics."""
    records: list[dict[str, Any]] = []
    covered_terms: list[str] = []
    safe = True

    def add_terms(text: Any) -> None:
        coverage = topic_coverage(text, topic_terms)
        for term in coverage.get("matched_terms", []):
            if term not in covered_terms:
                covered_terms.append(term)

    for claim in claims:
        support = _claim_support_evidence(claim, book_title)
        if not support:
            safe = False
            continue
        records.append(
            {
                "kind": "claim",
                "claim_id": str(claim.get("claim_id") or ""),
                "evidence_ids": [str(item.get("evidence_id") or "") for item in support if item.get("evidence_id")],
                "text": _claim_topic_text(claim),
            }
        )
        add_terms(_claim_topic_text(claim))

    for evidence in evidences:
        if not is_publishable_source_evidence(evidence) or not evidence_matches_book(evidence, book_title):
            safe = False
            continue
        evidence_id = str(evidence.get("evidence_id") or "").strip()
        if not evidence_id:
            safe = False
            continue
        records.append(
            {
                "kind": "evidence",
                "evidence_id": evidence_id,
                "evidence_ids": [evidence_id],
                "text": f"{evidence.get('title', '')}\n{evidence.get('content', '')}",
            }
        )
        add_terms(records[-1]["text"])

    # Keep the selected comparison components explicit.  They are the only
    # candidates a comparison summary is allowed to depend on.
    if compare_bundle:
        selected = []
        for key in ("primary_claim", "left_claim", "right_claim"):
            value = compare_bundle.get(key)
            if isinstance(value, dict):
                selected.append(("claim", value))
        for key in ("primary_evidence", "left_evidence", "right_evidence"):
            value = compare_bundle.get(key)
            if isinstance(value, dict):
                selected.append(("evidence", value))
        for kind, value in selected:
            if kind == "claim":
                add_terms(_claim_topic_text(value))
            else:
                add_terms(f"{value.get('title', '')}\n{value.get('content', '')}")

    return records, covered_terms, safe


def build_generic_gate(
    *,
    answer_mode: str,
    book_title: str,
    intent: str,
    claims: list[dict],
    evidences: list[dict],
    compare_bundle: dict | None,
    topic_terms: list[str],
) -> dict[str, Any]:
    """Build a fail-closed gate for ordinary, book-scoped answers."""
    requested = str(book_title or "").strip()
    terms = [str(item).strip() for item in topic_terms if str(item).strip()]
    base = {
        "status": "blocked",
        "book_title": requested,
        "topic_terms": terms,
        "matched_terms": [],
        "dependency_evidence_ids": [],
        "candidate_count": 0,
        "relevance_ok": False,
        "same_book_ok": False,
        "structured_answer_ok": answer_mode in GENERIC_ANSWER_MODES,
        "failure_reason": "",
        "next_action": "",
    }
    if not requested:
        base.update(
            failure_reason="未能确定当前教材，已停止通用检索。",
            next_action="请明确教材名称，或先在当前任务中确认唯一主教材。",
        )
        return base
    if not terms:
        base.update(
            failure_reason="问题中没有提取到稳定的主题词，已停止不确定回答。",
            next_action="请补充具体概念、公式或两个要比较的对象。",
        )
        return base

    records, covered_terms, safe = _generic_candidate_records(
        claims=claims,
        evidences=evidences,
        compare_bundle=compare_bundle,
        book_title=requested,
        topic_terms=terms,
    )
    dependency_ids = list(
        dict.fromkeys(
            str(evidence_id).strip()
            for record in records
            for evidence_id in record.get("evidence_ids", [])
            if str(evidence_id).strip()
        )
    )
    relevance_ok = bool(records) and all(term in covered_terms for term in terms)
    same_book_ok = bool(records) and safe and bool(dependency_ids)
    base.update(
        matched_terms=covered_terms,
        dependency_evidence_ids=dependency_ids,
        candidate_count=len(records),
        relevance_ok=relevance_ok,
        same_book_ok=same_book_ok,
    )
    if not records:
        base.update(
            failure_reason="当前教材范围内没有找到与问题主题匹配的可发布证据。",
            next_action="请补充章节、页码或先发布该概念的同书审核证据。",
        )
    elif not same_book_ok:
        base.update(
            failure_reason="检索候选未能全部证明属于当前教材的可发布证据。",
            next_action="请补充当前教材的正式证据；不会用其他教材内容填补。",
        )
    elif not relevance_ok:
        base.update(
            failure_reason="当前教材证据没有覆盖问题中的全部有效主题词。",
            next_action="请拆分问题或补充覆盖全部主题词的同书证据。",
        )
    elif not base["structured_answer_ok"]:
        base.update(
            failure_reason="当前只有章节层回退，不能把章节概览当作通用知识结论。",
            next_action="请先补充审核通过的主张或正文证据。",
        )
    else:
        base["status"] = "exact"
    return base


def _formal_concept_excerpt(evidence: dict[str, Any], concept: str) -> str:
    """Return reviewed theorem/definition text, rejecting mere worked-example mentions."""
    lines = [str(line).strip() for line in str(evidence.get("content") or "").splitlines()]
    statement_signals = ("设", "若", "如果", "当", "则", "存在", "取得", "达到", "连续", "可导", "满足")
    rejected_prefixes = ("例", "解", "证明", "由", "利用", "根据", "应用", "本题", "问题", "练习")
    for index, line in enumerate(lines):
        if concept not in line:
            continue
        cleaned = re.sub(r"^[#\s>*\-0-9.、（）()]+", "", line).strip()
        if not cleaned or cleaned.startswith(rejected_prefixes) or len(cleaned) > 100:
            continue
        window_lines = [item for item in lines[index : index + 8] if item]
        window = "\n".join(window_lines)
        if ("？" in cleaned or "?" in cleaned) and not any(signal in "\n".join(window_lines[1:]) for signal in statement_signals):
            continue
        if not any(signal in window for signal in statement_signals):
            continue
        return window[:1600].strip()
    return ""


def _concept_evidence_pages(evidence: dict[str, Any]) -> tuple[list[int], list[int]]:
    references = build_reference_items([evidence], None)
    printed_pages = sorted({int(item.get("printed_page", 0) or 0) for item in references if int(item.get("printed_page", 0) or 0)})
    pdf_pages = sorted({int(item.get("pdf_page", 0) or 0) for item in references if int(item.get("pdf_page", 0) or 0)})
    return printed_pages, pdf_pages


def _empty_concept_evidence(*, book_title: str, reason: str) -> dict[str, Any]:
    return {
        "status": "not_found",
        "book_title": book_title,
        "source_id": "",
        "evidence_ids": [],
        "printed_pages": [],
        "pdf_pages": [],
        "content": "",
        "failure_reason": reason,
    }


def _find_formal_concept_evidence(
    *,
    subject: str,
    concept: str,
    book_title: str,
    excluded_evidence_ids: set[str],
    topk: int,
) -> dict[str, Any]:
    tokens = tokenize(concept)
    full_query = normalize_text(concept)
    retrieval_hits = retrieve_hits(subject, concept, max(topk, 12), None, book_title)
    if retrieval_hits:
        evidences = evidence_hits_from_retrieval(
            subject,
            None,
            [],
            [],
            retrieval_hits,
            tokens,
            full_query,
            {},
            book_title,
            [concept],
        )
    else:
        evidences = fallback_evidence_hits(
            subject,
            None,
            [],
            [],
            tokens,
            full_query,
            {},
            book_title,
            [concept],
        )
    for evidence in evidences:
        evidence_id = str(evidence.get("evidence_id") or "")
        if not evidence_id or evidence_id in excluded_evidence_ids:
            continue
        if not evidence_matches_book(evidence, book_title):
            continue
        if not is_publishable_source_evidence(evidence):
            continue
        excerpt = _formal_concept_excerpt(evidence, concept)
        if not excerpt:
            continue
        printed_pages, pdf_pages = _concept_evidence_pages(evidence)
        return {
            "status": "exact",
            "book_title": str(evidence.get("book_title") or book_title),
            "source_id": str(evidence.get("source_id") or ""),
            "evidence_ids": [evidence_id],
            "printed_pages": printed_pages,
            "pdf_pages": pdf_pages,
            "content": excerpt,
            "failure_reason": "",
        }
    return _empty_concept_evidence(
        book_title=book_title,
        reason="未检索到与该概念同名、已审核且独立成文的定理或定义证据。",
    )


def build_concept_routes(result: dict[str, Any], *, topk: int) -> list[dict[str, Any]]:
    """Add a fail-closed, secondary-book concept route after an exact primary answer."""
    request_resolution = dict(result.get("request_resolution") or {})
    grounding = dict(result.get("answer_grounding") or {})
    primary_book = str(result.get("book_title") or (result.get("book_resolution") or {}).get("book_title") or "")
    if (
        result.get("subject") != "数学"
        or request_resolution.get("source_request_kind") != "exercise"
        or grounding.get("status") != "exact_answer"
        or not grounding.get("can_conclude")
        or not _book_title_matches(primary_book, PRIMARY_CALCULUS_BOOK_TITLE)
        or _book_title_matches(primary_book, SUPPLEMENTAL_CALCULUS_BOOK_TITLE)
    ):
        return []
    concepts = extract_explicit_concepts(str(result.get("query") or ""))
    if not concepts:
        return []
    primary_ids = {
        str(evidence_id)
        for side_name in ("problem", "solution")
        for evidence_id in (grounding.get(side_name) or {}).get("evidence_ids", [])
        if str(evidence_id)
    }
    routes: list[dict[str, Any]] = []
    for concept in concepts:
        primary = _find_formal_concept_evidence(
            subject="数学",
            concept=concept,
            book_title=primary_book,
            excluded_evidence_ids=primary_ids,
            topk=topk,
        )
        if primary.get("status") == "exact":
            supplement = {
                **_empty_concept_evidence(book_title=SUPPLEMENTAL_CALCULUS_BOOK_TITLE, reason="主书已定位独立正文，无需跨书补充。"),
                "status": "not_needed",
                "attempted": False,
                "trigger": "",
            }
        else:
            supplement = {
                **_find_formal_concept_evidence(
                    subject="数学",
                    concept=concept,
                    book_title=SUPPLEMENTAL_CALCULUS_BOOK_TITLE,
                    excluded_evidence_ids=set(),
                    topk=topk,
                ),
                "attempted": True,
                "trigger": "primary_concept_evidence_not_found",
            }
        routes.append({"concept": concept, "requested_by": "user_explicit", "primary": primary, "supplement": supplement})
    return routes


def attach_concept_routes(result: dict[str, Any], *, topk: int) -> None:
    result["concept_routes"] = build_concept_routes(result, topk=topk)


def build_textbook_location(request_resolution: dict[str, Any], exercise_anchor: dict[str, Any]) -> dict[str, Any]:
    """Expose a human-readable example anchor without weakening answer gating."""
    status = str(exercise_anchor.get("status") or "not_requested")
    return {
        "status": "exact" if status == "exact_answer_evidence" else status,
        "requested_container_path": list(request_resolution.get("container_path") or []),
        "container_path": list(exercise_anchor.get("container_path") or []),
        "exercise_label": str(exercise_anchor.get("exercise_label") or request_resolution.get("exercise_label") or ""),
        "printed_pages": list(exercise_anchor.get("question_printed_pages") or []),
        "location_key": str(exercise_anchor.get("location_key") or ""),
        "candidates": list(exercise_anchor.get("candidate_locations") or []),
    }


def query_knowledge(
    vault_root: Path,
    subject: str,
    chapter: str | None,
    query: str,
    topk: int,
    printed_page: int | None = None,
    book_title: str | None = None,
    exercise_label: str | None = None,
) -> dict:
    intent = detect_intent(query)
    book_resolution = resolve_current_task_book(vault_root=vault_root, subject=subject, explicit_book_title=book_title)
    effective_book_title = str(book_resolution.get("book_title") or "")
    book_route = resolve_book_route(query=query, book_title=effective_book_title)
    request_resolution = resolve_request(query=query, book_title=effective_book_title, chapter=chapter, printed_page=printed_page, exercise_label=exercise_label)
    page_anchor_request = {
        "requested_page": request_resolution["page"]["number"],
        "requested_position": parse_page_anchor(query).get("requested_position"),
        "requested_exercise_label": request_resolution["exercise_label"],
        "requested_container_path": list(request_resolution.get("container_path") or []),
    }
    effective_chapter = str(chapter or request_resolution.get("section_root") or "")
    resolved_label = str(request_resolution.get("exercise_label") or "")
    page_semantics = str(request_resolution["page"]["semantics"])
    generic_request = str(request_resolution.get("source_request_kind") or "generic") == "generic"
    topic_terms = generic_topic_terms(query, intent) if generic_request else None
    scoped_exercise = bool(resolved_label and page_semantics != "exact_page" and not book_route.get("series_id"))
    if scoped_exercise:
        exercise_anchor, evidences = apply_scoped_exercise_relation(
            book_title=effective_book_title,
            chapter=effective_chapter,
            exercise_label=resolved_label,
            category=str(request_resolution.get("exercise_category") or ""),
            query=query,
        )
        if page_anchor_request.get("requested_page") is not None:
            page_anchor, _, _, _ = apply_hard_page_route(subject=subject, chapter=effective_chapter, book_title=effective_book_title, request=page_anchor_request, retrieval_hits=[], claims=[])
        else:
            page_anchor = {"requested_page": None, "match_status": "not_requested"}
        page_crosscheck = build_page_crosscheck(request_resolution, page_anchor)
        if exercise_anchor.get("status") == "exact_answer_evidence":
            # The scoped exact relation is the formal exercise match.  Page
            # location and page/section crosscheck remain independent gates.
            page_anchor["exercise_match_status"] = "matched"
            references = build_reference_items(evidences, effective_chapter)
            for ref in references:
                if ref.get("evidence_id") in set(exercise_anchor.get("question_evidence_ids", [])): ref["role"] = "question"
                elif ref.get("evidence_id") in set(exercise_anchor.get("answer_evidence_ids", [])): ref["role"] = "answer"
            result = {"subject": subject, "chapter": effective_chapter, "book_title": effective_book_title, "book_resolution": book_resolution, "query": query, "intent": intent, "answer_mode": "accepted_evidence", "fallback_note": "", "syllabus_route": [], "retrieval_hits": [], "claim_hits": [], "evidence_hits": evidences, "fallback_hits": [], "references": references, "page_anchor": page_anchor, "page_crosscheck": page_crosscheck, "exercise_anchor": exercise_anchor, "query_path": {"exercise_relation_first": True, "retrieval_candidate_set_used": False, "retrieval_hit_count": 0, "hard_page_filter_applied": False}, "teaching_context": build_bounded_teaching_context(load_events(), subject=subject, chapter=effective_chapter, query=query), "request_resolution": request_resolution, "runtime_context": runtime_context_payload(vault_root_override=vault_root)}
            result["answer_grounding"] = build_answer_grounding(query=query, book_title=effective_book_title, page_anchor=page_anchor, exercise_anchor=exercise_anchor, evidences=evidences, page_crosscheck=page_crosscheck)
            result["textbook_location"] = build_textbook_location(request_resolution, exercise_anchor)
            if not result["answer_grounding"].get("can_conclude"):
                result["answer_mode"] = "exercise_unconfirmed"
                result["fallback_note"] = str(result["answer_grounding"].get("failure_reason") or "页码线索尚未完成小节锚点核验。")
            result["teaching_bundle"] = build_teaching_bundle(result["answer_grounding"], request_resolution)
            result["page_content_bundle"] = build_page_content_bundle(
                request_resolution=request_resolution,
                page_anchor=page_anchor,
                evidences=evidences,
                page_crosscheck=page_crosscheck,
            )
            result["page_verification"] = build_page_verification_summary(
                page_anchor,
                result["answer_mode"],
                page_crosscheck,
                request_resolution=request_resolution,
                answer_grounding=result["answer_grounding"],
                teaching_bundle=result["teaching_bundle"],
                page_content_bundle=result["page_content_bundle"],
            )
            attach_concept_routes(result, topk=topk)
            attach_exercise_index_runtime(result)
            return result
        result = {"subject": subject, "chapter": effective_chapter, "book_title": effective_book_title, "book_resolution": book_resolution, "query": query, "intent": intent, "answer_mode": "exercise_unconfirmed", "fallback_note": "题号问答需要在当前教材和当前章节中唯一匹配并裁剪出原题和答案；当前关系未覆盖、存在歧义或需要复核。", "syllabus_route": [], "retrieval_hits": [], "claim_hits": [], "evidence_hits": [], "fallback_hits": [], "references": [], "page_anchor": page_anchor, "page_crosscheck": page_crosscheck, "exercise_anchor": exercise_anchor, "query_path": {"exercise_relation_first": True, "retrieval_candidate_set_used": False, "retrieval_hit_count": 0, "hard_page_filter_applied": False}, "teaching_context": build_bounded_teaching_context(load_events(), subject=subject, chapter=effective_chapter, query=query), "request_resolution": request_resolution, "runtime_context": runtime_context_payload(vault_root_override=vault_root)}
        result["answer_grounding"] = build_answer_grounding(query=query, book_title=effective_book_title, page_anchor=result["page_anchor"], exercise_anchor=exercise_anchor, evidences=[], page_crosscheck=page_crosscheck)
        result["textbook_location"] = build_textbook_location(request_resolution, exercise_anchor)
        result["teaching_bundle"] = build_teaching_bundle(result["answer_grounding"], request_resolution)
        result["page_content_bundle"] = build_page_content_bundle(
            request_resolution=request_resolution,
            page_anchor=page_anchor,
            evidences=[],
            page_crosscheck=page_crosscheck,
        )
        result["page_verification"] = build_page_verification_summary(
            page_anchor,
            result["answer_mode"],
            page_crosscheck,
            request_resolution=request_resolution,
            answer_grounding=result["answer_grounding"],
            teaching_bundle=result["teaching_bundle"],
            page_content_bundle=result["page_content_bundle"],
        )
        attach_concept_routes(result, topk=topk)
        attach_exercise_index_runtime(result)
        return result
    tokens = tokenize(query)
    full_query = normalize_text(query)
    parts = compare_parts(query) if intent == "compare" else []
    retrieval_hits, routed, claims, evidences, index_routed = _resolve_query_hits(
        subject,
        effective_chapter or None,
        query,
        topk,
        intent,
        tokens,
        full_query,
        page_anchor_request,
        book_title=effective_book_title,
        topic_terms=topic_terms,
    )
    if topic_terms is not None:
        routed = [
            item
            for item in routed
            if topic_coverage(item.get("title", ""), topic_terms).get("matched_terms")
        ]
    hard_page_route = page_semantics == "exact_page" and page_anchor_request.get("requested_page") is not None
    if hard_page_route:
        routed_book_title = effective_book_title
        if book_route.get("series_id"):
            active = [item for item in book_route.get("candidates", []) if item.get("status") == "active"]
            exercise_request = parse_exercise_request(query)
            if exercise_request.get("role_intent") == "paired_answer" and exercise_request.get("exercise_number") is not None:
                questions = [item for item in active if item.get("role") == "question_book"]
                if len(questions) == 1:
                    routed_book_title = str(questions[0].get("title") or routed_book_title or "")
            elif len(active) == 1:
                routed_book_title = str(active[0].get("title") or routed_book_title or "")
        page_anchor, retrieval_hits, claims, evidences = apply_hard_page_route(
            subject=subject,
            chapter=effective_chapter or None,
            book_title=routed_book_title,
            request=page_anchor_request,
            retrieval_hits=retrieval_hits,
            claims=claims,
        )
        exercise_resolution = dict(request_resolution.get("exercise_resolution") or {})
        if (
            page_anchor.get("match_status") == "exact_evidence"
            and not resolved_label
            and needs_exercise_identity(query, str(request_resolution.get("requested_option") or ""))
        ):
            exercise_resolution = infer_exercise_from_exact_page(
                locator=page_anchor,
                query=query,
                category=str(request_resolution.get("exercise_category") or ""),
                requested_option=str(request_resolution.get("requested_option") or ""),
            )
            request_resolution["exercise_resolution"] = exercise_resolution
            if exercise_resolution.get("status") == "inferred_unique":
                resolved_label = str(exercise_resolution.get("exercise_label") or "")
                request_resolution["exercise_label"] = resolved_label
                page_anchor_request["requested_exercise_label"] = resolved_label
                page_anchor["requested_exercise_label"] = resolved_label
        if resolved_label:
            exercise_anchor, evidences = apply_exercise_relation(page_anchor, evidences)
        elif exercise_resolution.get("status") == "unavailable":
            page_anchor["exercise_match_status"] = "unavailable"
            exercise_anchor = {
                "status": "unavailable",
                "reason": exercise_resolution.get("unavailable_reason", "exercise_locator_index_unavailable"),
                "detail": exercise_resolution.get("unavailable_detail", ""),
                "_availability": dict(exercise_resolution.get("_availability") or {}),
            }
        elif exercise_resolution.get("status") == "ambiguous":
            page_anchor["exercise_match_status"] = "unverified"
            exercise_anchor = {
                "status": "ambiguous",
                "reason": "exercise-label-missing",
                "candidate_labels": list(exercise_resolution.get("candidate_labels") or []),
            }
        elif exercise_resolution.get("source") == "page_content":
            if page_anchor.get("match_status") == "exact_evidence":
                page_anchor["exercise_match_status"] = "unverified"
            exercise_anchor = {
                "status": "unverified",
                "reason": "exercise-label-missing" if exercise_resolution.get("candidate_labels") else "page-exercise-relations-not-found",
                "candidate_labels": list(exercise_resolution.get("candidate_labels") or []),
            }
        else:
            exercise_anchor = {"status": "not_requested"}
    else:
        page_anchor = build_page_anchor(evidences, page_anchor_request)
        exercise_anchor = {"status": "not_requested"}
    page_crosscheck = build_page_crosscheck(request_resolution, page_anchor)
    exercise_route = resolve_exercise_route(query=query, book_route=book_route)
    compare_bundle = build_compare_bundle(routed, claims, evidences, parts) if intent == "compare" else None
    refine_candidates = learner_compare_candidates(subject, effective_chapter or None)
    answer_mode, fallback_note, fallback = _resolve_answer_fallback(
        vault_root,
        subject,
        effective_chapter or None,
        tokens,
        full_query,
        topk,
        intent,
        claims,
        evidences,
        compare_bundle,
        effective_book_title,
        topic_terms,
    )
    if hard_page_route:
        fallback = []
        status = page_anchor.get("match_status")
        if status == "exact_evidence":
            answer_mode = "accepted_evidence"
            fallback_note = ""
            if page_anchor_request.get("requested_exercise_label") and exercise_anchor.get("status") not in {"exact_answer_evidence", "same_page_evidence"}:
                answer_mode = "exercise_unconfirmed"
                fallback_note = "题目页已精确定位，但未建立可唯一归因的跨页答案关系；不会用语义检索替代答案页。"
            elif (request_resolution.get("exercise_resolution") or {}).get("status") in {"ambiguous", "not_found", "unavailable"}:
                answer_mode = "exercise_unconfirmed"
                fallback_note = "题目页已精确定位，但缺少可唯一确认的题号；不会用语义检索猜测题目。"
        elif status == "exact_asset":
            answer_mode = "page_asset"
            fallback_note = "已精确定位教材原页，但该页尚无可用的结构化 OCR 证据；请基于原图核对，不应声称逐字引用。"
        elif status == "ambiguous":
            answer_mode = "page_ambiguous"
            fallback_note = "多本教材包含该印刷页，需要先确认教材名称。"
        elif status == "unmapped":
            answer_mode = "page_unmapped"
            fallback_note = "已识别教材，但该印刷页尚未建立正式页码映射。"
        elif status == "unavailable":
            answer_mode = "page_unavailable"
            reason = str(page_anchor.get("unavailable_reason") or "page_locator_unavailable")
            fallback_note = f"本地证据链当前不可用（{reason}）；不能据此判断教材是否包含该页或原文。"
        else:
            answer_mode = "page_not_found"
            fallback_note = "正式页定位索引中没有找到该印刷页。"
    if book_route.get("match_status") == "book_ambiguous" and (exercise_route.get("request") or {}).get("exercise_number") is not None:
        answer_mode = "book_ambiguous"
        fallback = []
        retrieval_hits = []
        claims = []
        evidences = []
        fallback_note = "已识别为接力题典1800书系，但还需要基础/强化、学科章节或题型才能唯一定位题目。"
    elif exercise_route.get("match_status") == "exact_exercise":
        pair_status = str(exercise_route.get("pair_status") or "")
        answer_mode = "exercise_pair" if pair_status == "exact_pair" else pair_status
        if pair_status == "exact_pair":
            fallback_note = ""
    elif book_route.get("series_id") and (exercise_route.get("request") or {}).get("exercise_number") is not None and not hard_page_route:
        answer_mode = "exercise_ambiguous" if exercise_route.get("match_status") == "exercise_ambiguous" else "exercise_not_found"
        fallback = []
        retrieval_hits = []
        claims = []
        evidences = []
        fallback_note = "已识别接力题典1800习题条件，但相应习题证据尚未发布或条件仍不能唯一定位；不会回退到其他教材。"
    generic_gate = {
        "status": "not_applicable",
        "book_title": "",
        "topic_terms": [],
        "matched_terms": [],
        "dependency_evidence_ids": [],
        "candidate_count": 0,
        "relevance_ok": False,
        "same_book_ok": False,
        "structured_answer_ok": False,
        "failure_reason": "",
        "next_action": "",
    }
    if generic_request:
        generic_gate = build_generic_gate(
            answer_mode=answer_mode,
            book_title=effective_book_title,
            intent=intent,
            claims=claims,
            evidences=evidences,
            compare_bundle=compare_bundle,
            topic_terms=list(topic_terms or []),
        )
        if generic_gate.get("status") != "exact" and not str(answer_mode).startswith("exercise"):
            answer_mode = "unconfirmed"
            fallback = []
            unsafe_book_scope = bool(effective_book_title) or bool(claims) or any(
                str(item.get("book_title") or item.get("source_name") or "").strip()
                for item in evidences
            )
            if unsafe_book_scope:
                retrieval_hits = []
                routed = []
                claims = []
                evidences = []
                compare_bundle = None
                if not hard_page_route:
                    page_anchor = {"requested_page": None, "match_status": "not_requested"}
            fallback_note = str(
                generic_gate.get("failure_reason")
                or "当前教材范围内没有足够稳定的可发布证据，已停止不确定回答。"
            )
    query_path = {
        "retrieval_candidate_set_used": index_routed,
        "retrieval_hit_count": len(retrieval_hits),
        "normal_path": "retrieval/search index -> candidate doc ids -> targeted json reads",
        "full_json_scan_used_for_answer": not index_routed,
        "full_json_scan_policy": "index-miss fallback only; normal indexed path is targeted reads",
        "page_locator_index_used": hard_page_route,
        "page_locator_index_available": bool(page_anchor.get("locator_available", True)) if hard_page_route else None,
        "page_locator_unavailable_reason": str(page_anchor.get("unavailable_reason") or "") if hard_page_route else "",
        "hard_page_filter_applied": hard_page_route,
        "exercise_relation_inference_used": (request_resolution.get("exercise_resolution") or {}).get("source") == "page_content",
    }
    teaching_context = build_bounded_teaching_context(
        load_events(),
        subject=subject,
        chapter=effective_chapter or None,
        query=query,
    )

    references = build_reference_items(evidences, effective_chapter or None)
    for ref in references:
        if ref.get("evidence_id") in set(exercise_anchor.get("question_evidence_ids", []) or []):
            ref["role"] = "question"
        elif ref.get("evidence_id") in set(exercise_anchor.get("answer_evidence_ids", []) or []):
            ref["role"] = "answer"
    runtime_context = runtime_context_payload(vault_root_override=vault_root)
    if hard_page_route:
        runtime_context["page_locator_index_available"] = bool(page_anchor.get("locator_available", True))
        runtime_context["page_locator_index_path"] = str(page_anchor.get("locator_index_path") or "")
        runtime_context["page_locator_unavailable_reason"] = str(page_anchor.get("unavailable_reason") or "")
    if book_route.get("series_id") or exercise_route.get("match_status") == "exact_exercise":
        answer_grounding = resolve_answer_grounding(
            query=query,
            book_title=effective_book_title,
            page_anchor=page_anchor,
            book_route=book_route,
            exercise_route=exercise_route,
        )
    else:
        answer_grounding = build_answer_grounding(
            query=query,
            book_title=effective_book_title,
            page_anchor=page_anchor,
            exercise_anchor=exercise_anchor,
            evidences=evidences,
            page_crosscheck=page_crosscheck,
        )
    if answer_grounding.get("required") and not answer_grounding.get("can_conclude") and page_crosscheck.get("required"):
        answer_mode = "exercise_unconfirmed"
        fallback_note = str(answer_grounding.get("failure_reason") or fallback_note)
    teaching_bundle = build_teaching_bundle(answer_grounding, request_resolution)
    page_content_bundle = build_page_content_bundle(
        request_resolution=request_resolution,
        page_anchor=page_anchor,
        evidences=evidences,
        page_crosscheck=page_crosscheck,
    )
    page_verification = build_page_verification_summary(
        page_anchor,
        answer_mode,
        page_crosscheck,
        request_resolution=request_resolution,
        answer_grounding=answer_grounding,
        teaching_bundle=teaching_bundle,
        page_content_bundle=page_content_bundle,
    )
    result = {
        "subject": subject,
        "chapter": effective_chapter,
        "book_title": effective_book_title,
        "book_resolution": book_resolution,
        "book_route": book_route,
        "exercise_route": exercise_route,
        "answer_grounding": answer_grounding,
        "query": query,
        "intent": intent,
        "answer_mode": answer_mode,
        "fallback_note": fallback_note,
        "syllabus_route": routed[: max(topk, 3)],
        "retrieval_hits": retrieval_hits[: max(topk, 5)],
        "claim_hits": claims,
        "evidence_hits": evidences,
        "fallback_hits": fallback,
        "references": references,
        "learner_snapshot": learner_snapshot(subject),
        "teaching_context": teaching_context,
        "compare_bundle": compare_bundle,
        "refinement_candidates": refine_candidates[:3],
        "query_path": query_path,
        "page_anchor": page_anchor,
        "page_crosscheck": page_crosscheck,
        "exercise_anchor": exercise_anchor,
        "textbook_location": build_textbook_location(request_resolution, exercise_anchor),
        "page_verification": page_verification,
        "runtime_context": runtime_context,
        "request_resolution": request_resolution,
        "teaching_bundle": teaching_bundle,
        "page_content_bundle": page_content_bundle,
        "generic_gate": generic_gate,
    }
    attach_concept_routes(result, topk=topk)
    attach_exercise_index_runtime(result)
    return result


def _batch_failure_text(reason: str) -> tuple[str, str]:
    messages = {
        "invalid-requested-option": ("用户选项不是 A、B、C、D。", "请更正该题所选选项后重试。"),
        "book-title-missing": ("未能唯一确定王道教材。", "请补充教材名称。"),
        "active-book-pdf-source-not-found": ("当前教材没有已启用的正式 PDF 来源。", "请先完成该教材资料接入与发布。"),
        "printed-page-range-not-uniquely-mapped": ("题目页范围没有形成唯一正式页码映射。", "请先核对教材版本及页码映射。"),
        "printed-page-range-not-contiguous": ("题目页范围的正式 PDF 映射不连续。", "请先复核该页码区间的正式映射。"),
        "printed-page-range-crosses-sources": ("题目页范围跨越了多个教材来源。", "请缩小范围或明确教材版本。"),
        "section-anchor-ambiguous": ("小节定位存在多个正式候选。", "请补充准确小节编号。"),
        "exercise_locator_index_missing": ("正式习题关系索引不存在。", "请先运行 kb.py sync --indexes-only。"),
        "exercise_locator_index_stale": ("正式习题关系索引已过期。", "请先运行 kb.py sync --indexes-only。"),
        "exercise_locator_index_version_mismatch": ("正式习题关系索引版本过旧。", "请先运行 kb.py sync --indexes-only。"),
        "exercise-relation-ambiguous": ("该题存在多个正式题目—答案关系。", "请补充小节、题型或页码范围。"),
        "exercise-relation-needs-review": ("该题的题目—答案关系仍需人工复核。", "请先复核并发布该题关系。"),
        "exercise-relation-not-found": ("未找到该题的正式题目—答案关系。", "请先确认题号或发布对应关系。"),
        "book-source-ambiguous": ("同名教材命中了多个来源或版本。", "请明确教材版本或页码范围。"),
    }
    return messages.get(reason, ("该题当前无法唯一定位原题和原书答案。", "请补充教材、小节、题型或页码信息。"))


def _blocked_batch_item(target: dict[str, Any], *, book_title: str) -> dict[str, Any]:
    reason = str(target.get("reason") or "exercise-relation-not-found")
    failure_reason, next_action = _batch_failure_text(reason)
    label = str(target.get("exercise_label") or "")
    grounding = {
        "required": True,
        "status": "answer_unavailable" if "index" in reason or "page" in reason else "answer_ambiguous" if "ambiguous" in reason else "answer_not_found",
        "can_conclude": False,
        "problem": {},
        "solution": {},
        "failure_reason": failure_reason,
        "next_action": next_action,
    }
    return {
        "exercise_label": label,
        "requested_option": str(target.get("requested_option") or ""),
        "emphasis": bool(target.get("emphasis")),
        "status": "blocked",
        "relation_id": str((target.get("relation") or {}).get("relation_id") or ""),
        "textbook_location": {
            "status": "blocked",
            "exercise_label": label,
            "printed_pages": [],
            "question_printed_pages": [],
            "question_pdf_pages": [],
            "answer_printed_pages": [],
            "answer_pdf_pages": [],
        },
        "page_verification": {},
        "answer_grounding": grounding,
        "citation_coverage_ok": False,
        "teaching_bundle": {
            "status": "blocked",
            "problem_text": "",
            "source_answer_text": "",
            "requested_option": str(target.get("requested_option") or ""),
            "exercise_label": label,
            "citations": {
                "problem_evidence_ids": [],
                "solution_evidence_ids": [],
                "problem_pdf_pages": [],
                "solution_pdf_pages": [],
            },
            "failure_reason": failure_reason,
        },
        "book_title": book_title,
    }


def _project_batch_item(target: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    grounding = dict(result.get("answer_grounding") or {})
    teaching = dict(result.get("teaching_bundle") or {})
    exact = bool(
        grounding.get("status") == "exact_answer"
        and grounding.get("can_conclude")
        and teaching.get("status") == "exact"
    )
    problem_ids = set((grounding.get("problem") or {}).get("evidence_ids", []) or [])
    solution_ids = set((grounding.get("solution") or {}).get("evidence_ids", []) or [])
    cited_ids = {str(item.get("evidence_id") or "") for item in result.get("references", []) or []}
    relation = dict(target.get("relation") or {})
    textbook_location = dict(result.get("textbook_location") or {})
    textbook_location.update(
        {
            "question_printed_pages": list(relation.get("question_printed_pages") or []),
            "question_pdf_pages": list(relation.get("question_pdf_pages") or []),
            "answer_printed_pages": list(relation.get("answer_printed_pages") or []),
            "answer_pdf_pages": list(relation.get("answer_pdf_pages") or []),
        }
    )
    return {
        "exercise_label": str(target.get("exercise_label") or ""),
        "requested_option": str(target.get("requested_option") or ""),
        "emphasis": bool(target.get("emphasis")),
        "status": "exact" if exact else "blocked",
        "relation_id": str(relation.get("relation_id") or (result.get("exercise_anchor") or {}).get("relation_id") or ""),
        "textbook_location": textbook_location,
        "page_verification": dict(result.get("page_verification") or {}),
        "answer_grounding": grounding,
        "citation_coverage_ok": bool(exact and problem_ids and solution_ids and problem_ids <= cited_ids and solution_ids <= cited_ids),
        "teaching_bundle": teaching,
        "book_title": str(result.get("book_title") or ""),
    }


def query_exercise_batch(
    vault_root: Path,
    subject: str,
    chapter: str | None,
    query: str,
    topk: int,
    book_title: str | None = None,
    printed_page: int | None = None,
    *,
    view: str = "query",
) -> dict[str, Any] | None:
    parsed = parse_exercise_batch_request(query)
    if not parsed.get("is_batch"):
        return None
    if printed_page is not None and not parsed.get("page_range"):
        parsed["page_range"] = {"start": int(printed_page), "end": int(printed_page), "semantics": "question_scope"}
    book_resolution = resolve_current_task_book(vault_root=vault_root, subject=subject, explicit_book_title=book_title)
    effective_book_title = str(book_resolution.get("book_title") or "")
    resolved = resolve_exercise_batch_targets(
        subject=subject,
        book_title=effective_book_title,
        chapter=chapter,
        query=query,
        parsed=parsed,
    )
    section_root = str(resolved.get("section_root") or chapter or "")
    category = str(parsed.get("exercise_category") or "")
    category_label = "单项选择题" if category == "single-choice" else "综合应用题" if category == "comprehensive" else "练习题"
    items: list[dict[str, Any]] = []
    for target in resolved.get("targets", []) or []:
        if target.get("status") != "exact":
            items.append(_blocked_batch_item(target, book_title=effective_book_title))
            continue
        label = str(target.get("exercise_label") or "")
        requested_option = str(target.get("requested_option") or "")
        item_question = f"{effective_book_title} {section_root} {category_label} 第{int(label)}题"
        if requested_option:
            item_question += f" {requested_option}项"
        item_result = query_knowledge(
            vault_root,
            subject,
            section_root or chapter,
            item_question,
            topk,
            int(target.get("printed_page")) if parsed.get("page_range") else None,
            effective_book_title,
            label,
        )
        items.append(_project_batch_item(target, item_result))
    exact_count = sum(item.get("status") == "exact" for item in items)
    blocked_count = len(items) - exact_count
    batch_status = "exact" if items and blocked_count == 0 else "partial" if exact_count else "blocked"
    payload = {
        "batch_contract_version": BATCH_CONTRACT_VERSION,
        "view": view,
        "subject": subject,
        "book_resolution": book_resolution,
        "runtime_context": runtime_context_payload(vault_root_override=vault_root),
        "request_resolution": {
            "original_query": query,
            "source_request_kind": "exercise_batch",
            "book_title": effective_book_title,
            "chapter_input": str(chapter or ""),
            "section_root": section_root,
            "exercise_category": category,
            "page_range": dict(parsed.get("page_range") or {}),
            "source_id": str(resolved.get("source_id") or ""),
            "exercise_resolution": {
                "status": "explicit",
                "source": "query",
                "exercise_labels": [str(item.get("exercise_label") or "") for item in parsed.get("items", []) or []],
            },
        },
        "batch_status": batch_status,
        "summary": {"requested_count": len(items), "exact_count": exact_count, "blocked_count": blocked_count},
        "items": items,
    }
    validate_entity_contract("exercise_batch_view", payload)
    return payload


def render_exercise_batch_text(payload: dict[str, Any]) -> str:
    summary = dict(payload.get("summary") or {})
    lines = [
        "# 王道 408 多题查询结果",
        "",
        f"- 批次状态：{payload.get('batch_status', 'blocked')}",
        f"- 请求：{summary.get('requested_count', 0)} 题；精确：{summary.get('exact_count', 0)} 题；阻塞：{summary.get('blocked_count', 0)} 题",
        "",
    ]
    for item in payload.get("items", []) or []:
        focus = "（重点）" if item.get("emphasis") else ""
        lines.extend([f"## 第 {int(item.get('exercise_label') or 0)} 题{focus}", ""])
        grounding = dict(item.get("answer_grounding") or {})
        lines.append(f"- 状态：{grounding.get('status', 'answer_not_found')}")
        teaching = dict(item.get("teaching_bundle") or {})
        if item.get("status") == "exact":
            lines.extend(["", teaching.get("problem_text", ""), "", teaching.get("source_answer_text", ""), ""])
        else:
            lines.extend([f"- 原因：{grounding.get('failure_reason', '')}", f"- 下一步：{grounding.get('next_action', '')}", ""])
    return "\n".join(lines).rstrip() + "\n"


def render_text(result: dict) -> str:
    lines = [
        "# 本地知识查询结果",
        "",
        f"- 学科：{result['subject']}",
        f"- 查询：{result['query']}",
        f"- 意图：{result['intent']}",
        f"- 命中层：{result['answer_mode']}",
        "",
    ]
    if result["fallback_note"]:
        lines.extend(["## 回退说明", "", f"- {result['fallback_note']}", ""])
    book_route = dict(result.get("book_route") or {})
    if book_route.get("match_status") != "not_found":
        lines.extend(["## 书系路由", "", f"- 状态：{book_route.get('match_status', '')}", f"- 书系：{book_route.get('canonical_title') or '未确定'}", f"- 阶段：{book_route.get('stage') or '未确定'}", ""])
    exercise_route = dict(result.get("exercise_route") or {})
    if exercise_route.get("match_status") not in {None, "", "not_requested"}:
        lines.extend(["## 习题路由", "", f"- 状态：{exercise_route.get('match_status', '')}", f"- 习题键：{exercise_route.get('exercise_key') or '未确定'}", f"- 配对状态：{exercise_route.get('pair_status') or '未确定'}", ""])
    grounding = dict(result.get("answer_grounding") or {})
    if grounding.get("required"):
        lines.extend(
            [
                "## 原书答案门控",
                "",
                f"- 状态：{grounding.get('status', 'answer_not_found')}",
                f"- 可输出结论：{'是' if grounding.get('can_conclude') else '否'}",
                f"- 失败原因：{grounding.get('failure_reason') or '无'}",
                f"- 下一步：{grounding.get('next_action') or '可按原书答案继续核对。'}",
                "",
            ]
        )
    page_anchor = dict(result.get("page_anchor") or {})
    if page_anchor.get("requested_page") is not None:
        page_crosscheck = dict(result.get("page_crosscheck") or {})
        verification = dict(
            result.get("page_verification")
            or build_page_verification_summary(
                page_anchor,
                result["answer_mode"],
                page_crosscheck,
                request_resolution=dict(result.get("request_resolution") or {}),
                answer_grounding=dict(result.get("answer_grounding") or {}),
                teaching_bundle=dict(result.get("teaching_bundle") or {}),
                page_content_bundle=dict(result.get("page_content_bundle") or {}),
            )
        )
        lines.extend(
            [
                "## 页码核验摘要",
                "",
                f"- 页面定位：{verification['page_location_status']}",
                f"- 题号正文核验：{verification['exercise_verification_status']}",
                f"- 小节起始页交叉核验：{verification.get('page_crosscheck_status', 'not_requested')}",
                f"- 命中层：{verification['answer_mode']}",
                f"- 可否按教材正文讲解：{'可以' if verification['textbook_explanation_allowed'] else '不可以'}",
                f"- 结论：{verification['summary']}",
                f"- 教材：{page_anchor.get('book_title') or page_anchor.get('requested_book_title') or '未确定'}",
                f"- 印刷页：{page_anchor.get('requested_page')}",
            ]
        )
        if page_anchor.get("source_image_path"):
            lines.append(f"- 原图：{page_anchor['source_image_path']}")
        if page_anchor.get("source_asset_kind") == "pdf":
            lines.append(f"- PDF 页：{page_anchor.get('pdf_page')} | 来源：{page_anchor.get('source_asset_path')}")
        lines.append("")
    exercise_anchor = dict(result.get("exercise_anchor") or {})
    if exercise_anchor.get("status") != "not_requested":
        lines.extend(["## 课后题答案定位", "", f"- 状态：{exercise_anchor.get('status')}"])
        textbook_location = dict(result.get("textbook_location") or {})
        if textbook_location.get("container_path"):
            lines.append(f"- 结构标题：{' / '.join(textbook_location['container_path'])}")
        elif textbook_location.get("candidates"):
            choices = [" / ".join(item.get("container_path") or []) or str(item.get("exercise_label") or "") for item in textbook_location["candidates"]]
            lines.append(f"- 候选结构：{'；'.join(choices)}")
        if exercise_anchor.get("exercise_label"):
            lines.append(f"- 题号：{exercise_anchor['exercise_label']}")
        if exercise_anchor.get("answer_pdf_pages"):
            lines.append(f"- 答案印刷页：{exercise_anchor.get('answer_printed_pages', [])} | 答案 PDF 页：{exercise_anchor['answer_pdf_pages']}")
    grounding = dict(result.get("answer_grounding") or {})
    if grounding.get("required"):
        lines.extend(["", "## 原书答案门控", "", f"- 状态：{grounding.get('status')}", f"- 可输出结论：{'是' if grounding.get('can_conclude') else '否'}", f"- 原因：{grounding.get('failure_reason') or '原题与原书答案均已确认。'}", f"- 下一步：{grounding.get('next_action') or '可按原书答案继续核对。'}"])
        lines.append("")
    teaching_context = dict(result.get("teaching_context") or {})
    if teaching_context.get("history_used"):
        lines.extend(
            [
                "## 有界历史教学上下文",
                "",
                f"- 范围匹配：{teaching_context.get('scope_match', 'none')}",
                f"- 本次使用：{', '.join(teaching_context.get('history_used', []))}",
                "- 作用边界：只调整讲解方式，不修改事实与引用。",
                "",
            ]
        )
    if result["syllabus_route"]:
        lines.extend(["## 考纲路由", ""])
        for item in result["syllabus_route"]:
            lines.append(f"- {item['title']} (`{item['node_id']}`)")
        lines.append("")
    if result["claim_hits"]:
        lines.extend(["## 主张命中", ""])
        for claim in result["claim_hits"][:5]:
            lines.append(f"- {claim['text']} [{claim['claim_type']}]")
        lines.append("")
    if result["references"]:
        lines.extend(["## 证据引用", ""])
        for ref in result["references"]:
            section_text = f" | 小节 {ref['section_title']}" if ref.get("section_title") else ""
            role = f" | 角色 {ref['role']}" if ref.get("role") else ""
            page_text = f" | 印刷页 {ref['printed_page']} | PDF 页 {ref['pdf_page']}" if ref.get("printed_page") or ref.get("pdf_page") else ""
            lines.append(f"- {ref['title']} | 页段 {ref['page_span']} | 图片 {ref['image_span']}{page_text} | chunk {ref['chunk_id']}{section_text}{role}")
        lines.append("")
    if result["fallback_hits"]:
        lines.extend(["## 章节回退", ""])
        for item in result["fallback_hits"]:
            lines.append(f"- {item['chapter_title']} | {item['chapter_overview']}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    args = parse_args()
    page_error = explicit_page_subject_error(subject=args.subject, query=args.query or "", printed_page=args.printed_page)
    if page_error:
        raise SystemExit(page_error)
    if not args.subject:
        raise SystemExit("[ERROR] --subject is required")
    subject, _ = resolve_subject(args.subject)
    batch = query_exercise_batch(
        Path(args.vault_root),
        subject,
        args.chapter,
        args.query or "",
        args.topk,
        args.book_title,
        args.printed_page,
        view="query",
    )
    if batch is not None:
        if args.format == "json":
            print(json.dumps(batch, ensure_ascii=False, indent=2))
        else:
            print(render_exercise_batch_text(batch), end="")
        return 0
    result = query_knowledge(Path(args.vault_root), subject, args.chapter, args.query or "", args.topk, args.printed_page, args.book_title, args.exercise_label)
    if args.format == "json":
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(render_text(result), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
