from __future__ import annotations

from kaoyan_kb.domain.book_series import (
    classify_source_request, has_exercise_request_signal,
)
from kaoyan_kb.domain.exercise_locator import (
    container_path_from_query, normalize_exercise_category, normalize_exercise_label,
    resolve_section_anchor,
)
from kaoyan_kb.domain.page_locator import parse_exercise_label
from pathlib import Path
from typing import Any
import re


CURRENT_TASK_PATH = Path("01_任务") / "当前任务.md"

CONCEPT_SUFFIXES = ("定理", "法则", "公式", "定义")

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
