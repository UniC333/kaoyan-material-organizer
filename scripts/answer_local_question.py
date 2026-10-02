#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

from common import default_vault_root_arg, kb_layout, is_publishable_source_evidence, resolve_subject, validate_entity_contract
from kaoyan_kb.domain.query.read_session import read_json as load_json
from kaoyan_kb.domain.query.source_revision import capture
from query_local_knowledge import build_page_content_bundle, build_teaching_bundle, detect_intent, evidence_has_formal_topic_statement, evidence_matches_book, generic_topic_terms, preferred_page_ref, query_knowledge, should_prefer_evidence_chapter


ANSWER_CONTRACT_VERSION = "m6.answer.v4"
TEACHING_VIEW_VERSION = "m6.teaching.v3"
STRUCTURED_ANSWER_MODES = {"canonical_claim", "accepted_evidence", "exercise_pair"}
CONTENT_SOURCE_LABELS = {
    "textbook_structured_evidence": "教材结构化证据",
    "textbook_problem_evidence": "原题结构化证据",
    "textbook_solution_evidence": "原书答案结构化证据",
    "page_asset_only": "仅原页定位",
    "runtime_unavailable": "本地证据链不可用",
    "supplementary_derivation": "补充推导",
    "learner_feedback": "学习者反馈",
}


def normalized_answer_grounding(result: dict) -> dict[str, Any]:
    grounding = dict(result.get("answer_grounding") or {})
    required = bool(grounding.get("required"))
    grounding.setdefault("required", required)
    grounding.setdefault("status", "answer_not_found" if required else "not_applicable")
    grounding.setdefault("can_conclude", not required)
    grounding.setdefault("problem", {})
    grounding.setdefault("solution", {})
    grounding.setdefault("failure_reason", "尚未定位原书答案。" if required else "")
    grounding.setdefault("next_action", "请先定位原书答案。" if required else "")
    consistency = dict(result.get("answer_consistency") or {})
    if required and str(consistency.get("status") or "") == "conflict":
        grounding.update(
            status="answer_ambiguous",
            can_conclude=False,
            failure_reason=str(consistency.get("reason") or "AI 辅助推导与结构化原书答案冲突。"),
            next_action="请核对原书答案原图；在冲突解除前不得判断用户过程或采用 AI 推导。",
        )
    return grounding


def request_kind_for_payload(payload: dict[str, Any]) -> str:
    request = dict(payload.get("request_resolution") or {})
    kind = str(request.get("source_request_kind") or "").strip()
    if kind in {"generic", "page_content", "exercise"}:
        return kind
    return "generic"


def generic_book_title(payload: dict[str, Any]) -> str:
    return str(
        payload.get("book_title")
        or dict(payload.get("book_resolution") or {}).get("book_title")
        or ""
    ).strip()


def generic_dependency_ids(result: dict[str, Any]) -> list[str]:
    """Collect every evidence ID used by an ordinary-answer conclusion."""
    gate = dict(result.get("generic_gate") or {})
    ordered: list[str] = []

    def add(value: Any) -> None:
        text = str(value or "").strip()
        if text and text not in ordered:
            ordered.append(text)

    for evidence_id in gate.get("dependency_evidence_ids", []) or []:
        add(evidence_id)
    bundle = dict(result.get("compare_bundle") or {})
    for key in ("primary_claim", "left_claim", "right_claim"):
        claim = bundle.get(key)
        if isinstance(claim, dict):
            for evidence_id in claim.get("evidence_ids", []) or []:
                add(evidence_id)
    for key in ("primary_evidence", "left_evidence", "right_evidence"):
        evidence = bundle.get(key)
        if isinstance(evidence, dict):
            add(evidence.get("evidence_id"))
    for claim in result.get("claim_hits", []) or []:
        for evidence_id in claim.get("evidence_ids", []) or []:
            add(evidence_id)
    for evidence in result.get("evidence_hits", []) or []:
        add(evidence.get("evidence_id"))
    return ordered


def _empty_generic_answer_bundle(status: str = "not_applicable", *, failure_reason: str = "", next_action: str = "") -> dict[str, Any]:
    return {
        "status": status,
        "book_title": "",
        "topic_terms": [],
        "conclusion": "",
        "explanation": "",
        "evidence_ids": [],
        "citations": [],
        "relevance_ok": False,
        "same_book_ok": False,
        "citation_coverage_ok": False,
        "failure_reason": failure_reason,
        "next_action": next_action,
    }


def build_generic_answer_bundle(
    result: dict[str, Any],
    *,
    direct: str,
    explanation: list[str],
    citations: list[dict[str, Any]],
) -> dict[str, Any]:
    """Project the ordinary-answer gate into a compact teaching payload."""
    if request_kind_for_payload(result) != "generic":
        return _empty_generic_answer_bundle()
    gate = dict(result.get("generic_gate") or {})
    evidence_ids = generic_dependency_ids(result)
    citation_ids = {str(item.get("evidence_id") or "").strip() for item in citations if str(item.get("evidence_id") or "").strip()}
    coverage_ok = bool(evidence_ids) and set(evidence_ids).issubset(citation_ids)
    relevance_ok = bool(gate.get("relevance_ok"))
    same_book_ok = bool(gate.get("same_book_ok"))
    topic_terms = [str(item) for item in gate.get("topic_terms", []) or [] if str(item).strip()]
    book_title = str(gate.get("book_title") or generic_book_title(result)).strip()
    exact = (
        str(result.get("answer_mode") or "") in {"canonical_claim", "accepted_evidence"}
        and str(gate.get("status") or "") == "exact"
        and relevance_ok
        and same_book_ok
        and coverage_ok
        and bool(str(direct).strip())
        and bool(evidence_ids)
    )
    if exact:
        compact_citations = [
            {
                "evidence_id": str(item.get("evidence_id") or ""),
                "title": str(item.get("title") or ""),
                "book_title": str(item.get("book_title") or ""),
                "page_span": str(item.get("page_span") or ""),
            }
            for item in citations
            if str(item.get("evidence_id") or "") in evidence_ids
        ]
        return {
            "status": "exact",
            "book_title": book_title,
            "topic_terms": topic_terms,
            "conclusion": str(direct).strip(),
            "explanation": "；".join(line.strip() for line in explanation if line.strip()),
            "evidence_ids": evidence_ids,
            "citations": compact_citations,
            "relevance_ok": True,
            "same_book_ok": True,
            "citation_coverage_ok": True,
            "failure_reason": "",
            "next_action": "",
        }
    failure_reason = str(gate.get("failure_reason") or "当前回答未通过主题、同书和引用覆盖门禁，已停止不确定回答。")
    next_action = str(gate.get("next_action") or "请补充同书审核证据后重新查询。")
    if not coverage_ok and evidence_ids:
        failure_reason = "结论依赖的全部证据 ID 尚未进入 citations，不能形成可保存回答。"
        next_action = "请补齐结论依赖证据的引用覆盖后重新查询。"
    return {
        "status": "blocked",
        "book_title": book_title,
        "topic_terms": topic_terms,
        "conclusion": "",
        "explanation": "",
        "evidence_ids": [],
        "citations": [],
        "relevance_ok": relevance_ok,
        "same_book_ok": same_book_ok,
        "citation_coverage_ok": coverage_ok,
        "failure_reason": failure_reason,
        "next_action": next_action,
    }


def validate_teaching_contract_invariants(contract: dict[str, Any]) -> None:
    """Reject contradictory teaching gates before a contract reaches a caller."""
    grounding = dict(contract.get("answer_grounding") or {})
    teaching = dict(contract.get("teaching_bundle") or {})
    required = bool(grounding.get("required"))
    exact = str(grounding.get("status") or "") == "exact_answer"
    can_conclude = bool(grounding.get("can_conclude"))
    teaching_status = str(teaching.get("status") or "")
    problem_text = str(teaching.get("problem_text") or "").strip()
    answer_text = str(teaching.get("source_answer_text") or "").strip()
    citations = dict(teaching.get("citations") or {})

    option = str(teaching.get("requested_option") or "")
    if option not in {"", "A", "B", "C", "D"}:
        raise ValueError(f"teaching contract has invalid requested_option: {option}")

    if required:
        if exact != can_conclude:
            raise ValueError("teaching contract has contradictory exact-answer conclusion state")
        if exact:
            if teaching_status != "exact" or not problem_text or not answer_text:
                raise ValueError("exact answer must expose an exact teaching bundle with problem and source answer text")
            if not list(citations.get("problem_evidence_ids") or []) or not list(citations.get("solution_evidence_ids") or []):
                raise ValueError("exact teaching bundle must retain both problem and solution evidence ids")
        else:
            if teaching_status != "blocked" or problem_text or answer_text:
                raise ValueError("unconfirmed source answer must expose a blocked, content-free teaching bundle")
            if not str(grounding.get("failure_reason") or "").strip() or not str(grounding.get("next_action") or "").strip():
                raise ValueError("blocked source answer must explain the failure and next action")
    elif teaching_status != "not_applicable" or problem_text or answer_text:
        raise ValueError("ordinary queries must expose a content-free not_applicable teaching bundle")

    request_kind = request_kind_for_payload(contract)
    if "generic_answer_bundle" in contract:
        generic = dict(contract.get("generic_answer_bundle") or {})
        generic_status = str(generic.get("status") or "")
        generic_conclusion = str(generic.get("conclusion") or "").strip()
        generic_explanation = str(generic.get("explanation") or "").strip()
        generic_evidence_ids = list(generic.get("evidence_ids") or [])
        generic_citations = list(generic.get("citations") or [])
        if request_kind == "generic":
            if generic_status == "exact":
                if (
                    not generic_conclusion
                    or not generic_explanation
                    or not generic_evidence_ids
                    or not generic_citations
                    or not bool(generic.get("relevance_ok"))
                    or not bool(generic.get("same_book_ok"))
                    or not bool(generic.get("citation_coverage_ok"))
                ):
                    raise ValueError("exact generic answer must retain conclusion, explanation, same-book evidence and citations")
            elif generic_status == "blocked":
                if generic_conclusion or generic_explanation or generic_evidence_ids or generic_citations:
                    raise ValueError("blocked generic answer must remain content-free")
                if not str(generic.get("failure_reason") or "").strip() or not str(generic.get("next_action") or "").strip():
                    raise ValueError("blocked generic answer must explain the failure and next action")
            elif generic_status != "not_applicable":
                raise ValueError("generic answer must expose exact, blocked, or not_applicable state")
        elif generic_status != "not_applicable" or generic_conclusion or generic_explanation or generic_evidence_ids or generic_citations:
            raise ValueError("non-generic requests must expose a content-free not_applicable generic bundle")
    page_content = dict(contract.get("page_content_bundle") or {})
    page_content_status = str(page_content.get("status") or "not_applicable")
    page_content_text = str(page_content.get("content") or "").strip()
    page_anchor = dict(contract.get("page_anchor") or {})
    if request_kind == "page_content":
        if page_content_status == "exact":
            if str(page_anchor.get("match_status") or "") != "exact_evidence":
                raise ValueError("exact page content requires an exact-evidence page anchor")
            if not page_content_text or not list(page_content.get("evidence_ids") or []):
                raise ValueError("exact page content must retain reviewed text and evidence ids")
        elif page_content_status in {"asset_only", "blocked"}:
            if page_content_text:
                raise ValueError("unconfirmed page content must remain content-free")
            if not str(page_content.get("failure_reason") or "").strip() or not str(page_content.get("next_action") or "").strip():
                raise ValueError("blocked page content must explain the failure and next action")
        else:
            raise ValueError("page-content requests must expose exact, asset_only, or blocked state")
    elif page_content_status != "not_applicable" or page_content_text:
        raise ValueError("non-page requests must expose a content-free not_applicable page bundle")

    verification = dict(contract.get("page_verification") or {})
    explanation_allowed = bool(verification.get("textbook_explanation_allowed"))
    from kaoyan_kb.domain.query.permission import teaching_permission
    if request_kind == "page_content":
        expected_allowed = teaching_permission(contract)
        if explanation_allowed != expected_allowed:
            raise ValueError("page-content explanation permission contradicts page content bundle")
    elif request_kind == "exercise":
        expected_allowed = teaching_permission(contract)
        if explanation_allowed != expected_allowed:
            raise ValueError("exercise explanation permission contradicts exact-answer teaching gate")
        if exact and not expected_allowed:
            raise ValueError("exact answer contradicts source identity, page or citation permission")
    elif explanation_allowed:
        raise ValueError("generic requests cannot permit textbook explanation")

    concept_routes = list(contract.get("concept_routes") or [])
    if concept_routes and not (request_kind == "exercise" and exact and can_conclude and teaching_status == "exact"):
        raise ValueError("concept routes require an exact primary textbook answer")
    for route in concept_routes:
        primary = dict(route.get("primary") or {})
        supplement = dict(route.get("supplement") or {})
        if primary.get("status") == "exact":
            if not str(primary.get("content") or "").strip() or not list(primary.get("evidence_ids") or []):
                raise ValueError("exact primary concept evidence must retain reviewed text and evidence ids")
            if supplement.get("attempted") or supplement.get("status") != "not_needed":
                raise ValueError("an exact primary concept cannot trigger cross-book supplementation")
        elif str(primary.get("content") or "").strip():
            raise ValueError("unconfirmed primary concept evidence must remain content-free")
        if supplement.get("status") == "exact":
            if not supplement.get("attempted") or primary.get("status") == "exact":
                raise ValueError("exact supplemental concept evidence requires a failed primary concept lookup")
            if not str(supplement.get("content") or "").strip() or not list(supplement.get("evidence_ids") or []):
                raise ValueError("exact supplemental concept evidence must retain reviewed text and evidence ids")
        elif str(supplement.get("content") or "").strip():
            raise ValueError("unconfirmed supplemental concept evidence must remain content-free")
        if supplement.get("status") == "not_needed" and supplement.get("attempted"):
            raise ValueError("a not-needed supplement cannot be marked attempted")

    crosscheck = dict(contract.get("page_crosscheck") or {})
    if crosscheck.get("required") and str(crosscheck.get("status") or "") != "confirmed":
        if (required and (can_conclude or teaching_status == "exact")) or page_content_status == "exact":
            raise ValueError("unconfirmed page crosscheck cannot permit an exact teaching answer")
        if verification.get("textbook_explanation_allowed"):
            raise ValueError("unconfirmed page crosscheck cannot permit textbook explanation")


def _compact_grounding_side(side: dict[str, Any]) -> dict[str, Any]:
    if not side:
        return {}
    return {
        key: side.get(key)
        for key in ("book_title", "exercise_label", "evidence_ids", "printed_pages", "pdf_pages")
        if side.get(key) not in (None, "", [])
    }


def _compact_page_anchor(anchor: dict[str, Any]) -> dict[str, Any]:
    if not anchor:
        return {}
    printed_page = anchor.get("requested_page") if anchor.get("requested_page") is not None else anchor.get("printed_page")
    compact = {
        "match_status": str(anchor.get("match_status") or ""),
        "book_title": str(anchor.get("book_title") or anchor.get("requested_book_title") or ""),
        "source_id": str(anchor.get("source_id") or anchor.get("book_id") or ""),
        "requested_page": anchor.get("requested_page"),
        "printed_page": printed_page,
        "pdf_page": anchor.get("pdf_page"),
        "evidence_ids": list(anchor.get("evidence_ids") or []),
        "match_basis": str(anchor.get("match_basis") or ""),
    }
    return {key: value for key, value in compact.items() if value not in (None, "", [])}


def build_teaching_answer_view(
    contract: dict[str, Any],
    *,
    saved: bool = False,
    saved_at: str = "",
) -> dict[str, Any]:
    """Project the full answer contract into a small, model-facing teaching view."""
    validate_teaching_contract_invariants(contract)
    grounding = dict(contract.get("answer_grounding") or {})
    compact_grounding = {
        "required": bool(grounding.get("required")),
        "status": str(grounding.get("status") or "not_applicable"),
        "can_conclude": bool(grounding.get("can_conclude")),
        "problem": _compact_grounding_side(dict(grounding.get("problem") or {})),
        "solution": _compact_grounding_side(dict(grounding.get("solution") or {})),
        "failure_reason": str(grounding.get("failure_reason") or ""),
        "next_action": str(grounding.get("next_action") or ""),
    }
    view = {
        "teaching_view_version": TEACHING_VIEW_VERSION,
        "answer_contract_version": str(contract.get("answer_contract_version") or ""),
        "view": "teaching",
        "saved": bool(saved),
        "saved_at": str(saved_at or ""),
        "subject": str(contract.get("subject") or ""),
        "chapter": str(contract.get("chapter") or ""),
        "question": str(contract.get("question") or ""),
        "answer_mode": str(contract.get("answer_mode") or ""),
        "book_resolution": dict(contract.get("book_resolution") or {}),
        "runtime_context": dict(contract.get("runtime_context") or {}),
        "request_resolution": dict(contract.get("request_resolution") or {}),
        "page_crosscheck": dict(contract.get("page_crosscheck") or {}),
        "page_verification": dict(contract.get("page_verification") or {}),
        "page_anchor": _compact_page_anchor(dict(contract.get("page_anchor") or {})),
        "textbook_location": dict(contract.get("textbook_location") or {}),
        "answer_grounding": compact_grounding,
        "citation_coverage_ok": bool(contract.get("citation_coverage_ok")),
        "generic_answer_bundle": dict(contract.get("generic_answer_bundle") or _empty_generic_answer_bundle()),
        "teaching_bundle": dict(contract.get("teaching_bundle") or {}),
        "page_content_bundle": dict(contract.get("page_content_bundle") or {}),
        "concept_routes": list(contract.get("concept_routes") or []),
    }
    validate_entity_contract("teaching_answer_view", view)
    return view


def _side_evidence_ids(side: dict[str, Any]) -> list[str]:
    values = list(side.get("evidence_ids") or [])
    if side.get("evidence_id"):
        values.insert(0, side["evidence_id"])
    return dedupe([str(item) for item in values])


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--vault-root", default=default_vault_root_arg())
    parser.add_argument("--subject", required=True)
    parser.add_argument("--chapter")
    parser.add_argument("--book-title")
    parser.add_argument("--question", required=True)
    parser.add_argument("--topk", type=int, default=3)
    parser.add_argument("--printed-page", type=int)
    parser.add_argument("--format", choices=("text", "json", "teaching-json"), default="text")
    return parser.parse_args()


def dedupe(items: list[str]) -> list[str]:
    seen: list[str] = []
    for item in items:
        text = str(item or "").strip()
        if text and text not in seen:
            seen.append(text)
    return seen


def page_anchor_snippets(result: dict) -> list[str]:
    anchor = result.get("page_anchor", {}) or {}
    snippets = anchor.get("snippets", []) or []
    return dedupe([str(item).strip() for item in snippets])


def evidence_excerpt(evidence: dict, question: str) -> tuple[int, str]:
    """Return a short, question-relevant textbook sentence from one page."""
    raw_lines = [re.sub(r"\s+", " ", str(line)).strip() for line in str(evidence.get("content", "")).splitlines()]
    topic_terms = generic_topic_terms(question, detect_intent(question))
    lines = [
        line
        for line in raw_lines
        if not line.startswith(("#", "!["))
        and len(line) >= 12
    ]
    normalized_question = re.sub(r"[？?。！，、：:\s]", "", question)
    definition_topic = normalized_question.split("的定义", 1)[0] if "的定义" in normalized_question else ""
    for index, raw_heading in enumerate(raw_lines):
        heading = raw_heading.lstrip("#").strip()
        normalized_heading = re.sub(r"\s+", "", heading).lower()
        heading_matches_topic = any(
            re.sub(r"\s+", "", term).lower() in normalized_heading for term in topic_terms
        )
        if not raw_heading.startswith("#") and not (len(raw_heading) < 12 and heading_matches_topic):
            continue
        definition_heading = bool(definition_topic and f"{definition_topic}的定义" in heading)
        if not heading_matches_topic and not definition_heading:
            continue
        following = next(
            (
                candidate
                for candidate in raw_lines[index + 1 :]
                if len(candidate) >= 12 and not candidate.startswith(("#", "![", "考点追踪"))
            ),
            "",
        )
        if following:
            lines.append(f"{heading}：{following}")
        else:
            lines.append(heading)
    bigrams = {normalized_question[index : index + 2] for index in range(max(0, len(normalized_question) - 1))}
    ranked = sorted(
        (
            (
                sum(line.count(token) for token in bigrams if token)
                + sum(
                    20 + len(term)
                    for term in topic_terms
                    if re.sub(r"\s+", "", term).lower() in re.sub(r"\s+", "", line).lower()
                )
                + (10 if definition_topic and f"{definition_topic}的定义" in line else 0),
                line,
            )
            for line in lines
        ),
        key=lambda item: (-item[0], len(item[1])),
    )
    if not ranked:
        return 0, ""
    score, line = ranked[0]
    return score, line[:320]


def formal_topic_excerpt(evidence: dict, topic: str) -> tuple[int, str]:
    """Choose a formal, topic-specific passage instead of a page-level mention."""
    normalized_topic = re.sub(r"\s+", "", str(topic or "")).lower()
    if not normalized_topic or not evidence_has_formal_topic_statement(evidence, [normalized_topic]):
        return 0, ""
    raw_lines = [re.sub(r"\s+", " ", str(line)).strip() for line in str(evidence.get("content") or "").splitlines()]
    candidates: list[tuple[int, str]] = []
    rejected = ("例", "例如", "解", "证明", "证法", "由", "利用", "根据", "题型", "方法", "考点追踪")

    def compact_line(value: str) -> str:
        return re.sub(r"\s+", "", value.lstrip("#>*- 【[（(")).lower()

    def is_rejected(compact: str) -> bool:
        return (
            compact.startswith(rejected)
            or "应用" in compact
            or "用法" in compact
            or any(marker in compact for marker in ("（ ）", "()", "选项", "真题", "选择题", "试编写", "下列"))
        )

    def is_formal(compact: str, *, require_topic: bool = False) -> bool:
        if not compact or (require_topic and normalized_topic not in compact) or is_rejected(compact):
            return False
        direct = re.search(
            re.escape(normalized_topic)
            + r"(?:[（(][^）)]{0,12}[）)]|的(?:定义|概念|特点|特性|核心规则))?(?:是|为|指|叫做|称为|定义为)",
            compact,
        )
        condition = (
            ("设" in compact and "则" in compact)
            or ("若" in compact and "则" in compact)
            or ("如果" in compact and "则" in compact)
            or ("对任意" in compact and ("存在" in compact or "则" in compact))
            or ("对于" in compact and ("存在" in compact or "则" in compact or "满足" in compact))
        )
        return bool(direct or condition or ("定义" in compact and ("是" in compact or "为" in compact)))

    def formula_line(compact: str) -> bool:
        return not is_rejected(compact) and any(marker in compact for marker in ("=", "\\in", "\\le", "\\ge", "\\forall", "\\exists"))

    for index, line in enumerate(raw_lines):
        compact = re.sub(r"\s+", "", line.lstrip("#>*- 【[（(")).lower()
        if normalized_topic not in compact or line.startswith("![") or is_rejected(compact):
            continue

        heading_like = line.startswith("#") or len(compact) <= 16
        if heading_like:
            for following_index in range(index + 1, min(len(raw_lines), index + 6)):
                following = raw_lines[following_index]
                following_compact = compact_line(following)
                if not (is_formal(following_compact) or formula_line(following_compact)):
                    continue
                passage = [line, following]
                for formula in raw_lines[following_index + 1 : following_index + 3]:
                    formula_compact = compact_line(formula)
                    if formula.startswith("#") or is_rejected(formula_compact):
                        break
                    if formula_line(formula_compact):
                        passage.append(formula)
                candidates.append((170 if len(passage) > 2 else 150, "：".join(passage[:2]) + ("\n" + "\n".join(passage[2:]) if len(passage) > 2 else "")))
                break

        if not is_formal(compact, require_topic=True):
            continue
        score = 10
        if re.search(
            re.escape(normalized_topic)
            + r"(?:[（(][^）)]{0,12}[）)]|的(?:定义|概念|特点|特性|核心规则))?(?:是|为|指|叫做|称为|定义为)",
            compact,
        ):
            score += 100
        if any(marker in compact for marker in ("先进后出", "先进先出", "插入和删除", "入队", "出队", "入栈", "出栈")):
            score += 40
        if compact.startswith(normalized_topic):
            score += 10
        candidates.append((score, line))
    if not candidates:
        return 0, ""
    candidates.sort(key=lambda item: (-item[0], len(item[1])))
    score, line = candidates[0]
    return score, line[:320]


def generic_compare_conclusion(result: dict) -> str:
    """Build one deduplicated conclusion per formally covered comparison topic."""
    if request_kind_for_payload(result) != "generic" or str(result.get("intent") or "") != "compare":
        return ""
    terms = [str(item).strip() for item in (dict(result.get("generic_gate") or {}).get("topic_terms") or []) if str(item).strip()]
    chunks: list[str] = []
    for term in terms:
        candidates = []
        for evidence in result.get("evidence_hits", []) or []:
            score, text = formal_topic_excerpt(evidence, term)
            if text:
                candidates.append((score, text, str(evidence.get("title") or "")))
        if not candidates:
            return ""
        candidates.sort(key=lambda item: (-item[0], len(item[1]), item[2]))
        _, text, title = candidates[0]
        chunks.append(f"{text}（来源：{title}）" if title else text)
    return "；".join(dedupe(chunks))


def generic_definition_conclusion(result: dict) -> str:
    """Build a definition conclusion from the formal passage, not page mentions."""
    if request_kind_for_payload(result) != "generic" or str(result.get("intent") or "") != "define":
        return ""
    terms = [str(item).strip() for item in (dict(result.get("generic_gate") or {}).get("topic_terms") or []) if str(item).strip()]
    chunks: list[str] = []
    for term in terms:
        candidates = []
        for evidence in result.get("evidence_hits", []) or []:
            score, text = formal_topic_excerpt(evidence, term)
            if text:
                candidates.append((score, text, str(evidence.get("title") or "")))
        if not candidates:
            return ""
        candidates.sort(key=lambda item: (-item[0], len(item[1]), item[2]))
        _, text, title = candidates[0]
        chunks.append(f"{text}（来源：{title}）" if title else text)
    return "；".join(dedupe(chunks))


def direct_conclusion(result: dict) -> str:
    exercise = dict(result.get("exercise_route") or {})
    if exercise.get("match_status") == "exact_exercise":
        question = dict(exercise.get("question") or {})
        solution = dict(exercise.get("solution") or {})
        if exercise.get("pair_status") == "exact_pair":
            return f"已定位原题（题目册 P{','.join(map(str, question.get('printed_pages', []))) or '未标页'}）并配对书中题解（题解册 P{','.join(map(str, solution.get('printed_pages', []))) or '未标页'}）。"
        if exercise.get("pair_status") == "solution_pending":
            return "原题已经定位，但强化篇题解尚未接入；当前不能把独立推导称为书中解析。"
    compare_conclusion = generic_compare_conclusion(result)
    if compare_conclusion:
        return compare_conclusion
    definition_conclusion = generic_definition_conclusion(result)
    if definition_conclusion:
        return definition_conclusion
    anchor_snippets = page_anchor_snippets(result)
    if anchor_snippets:
        return anchor_snippets[0]
    anchor = result.get("page_anchor", {}) or {}
    if anchor.get("match_status") == "exact_asset":
        return f"已精确定位教材原页：{anchor.get('source_image_path', '')}；该页尚无结构化 OCR，教材正文未确认。"
    if anchor.get("match_status") in {"ambiguous", "unmapped", "not_found", "unavailable"}:
        return result.get("fallback_note") or "当前无法唯一定位教材原页。"
    if result.get("answer_mode") == "unconfirmed":
        return result.get("fallback_note") or "当前教材范围内没有足够稳定的可发布证据，已停止不确定回答。"
    bundle = result.get("compare_bundle")
    if bundle:
        return bundle["summary"]
    if result["claim_hits"]:
        texts = dedupe([claim["text"] for claim in result["claim_hits"]])
        return "；".join(texts[:3])
    if result["evidence_hits"]:
        if request_kind_for_payload(result) != "generic":
            excerpts = [
                (score, text, evidence.get("title", ""))
                for evidence in result["evidence_hits"][:5]
                for score, text in [evidence_excerpt(evidence, str(result.get("query", "")))]
            ]
            excerpts.sort(key=lambda item: (-item[0], len(item[1])))
            if excerpts and excerpts[0][1]:
                _, text, title = excerpts[0]
                return f"{text}（来源：{title}）"
            return "；".join(item.get("title", "") for item in result["evidence_hits"][:3])
        evidence = result["evidence_hits"][0]
        _, text = evidence_excerpt(evidence, str(result.get("query", "")))
        if text:
            title = evidence.get("title", "")
            return f"{text}（来源：{title}）"
        return "；".join(item.get("title", "") for item in result["evidence_hits"][:3])
    if result["fallback_hits"]:
        return result["fallback_hits"][0].get("chapter_overview", "") or "当前只能回退到章节级概览。"
    return "当前本地知识库里还没有足够稳定的命中。"


def intuitive_explanation(result: dict) -> str:
    grounding = normalized_answer_grounding(result)
    if grounding["required"] and not grounding["can_conclude"]:
        return "原书答案尚未确认，本次不能以 AI 独立推导补位，也不能输出数值、选项或证明结论。"
    if result.get("answer_mode") == "page_unavailable":
        return "配置或正式页码索引不可用，本次不能判断教材是否包含该内容，也不能回退到其他知识库或通用推导冒充原文。"
    if result.get("answer_mode") == "page_asset":
        return "页码和原图已经确认，但正文尚未进入正式证据层；可以查看原图讲解，不能把未核对的语义检索结果当作书上原文。"
    if result["answer_mode"] == "chapter_fallback":
        return "当前未命中正式主张，这次回答只基于章节层回退，不应把它当作正式知识结论。"
    bundle = result.get("compare_bundle")
    if bundle:
        if bundle.get("mode") == "single_node":
            return f"这次问题直接命中 {bundle['node']['title']}，说明教材里已经把这组概念放在同一考点下处理。"
        if bundle.get("mode") == "single_node_evidence":
            return f"这次问题直接命中 {bundle['node']['title']}，但当前正式 comparison claim 还不稳定，所以解释先基于该节点证据组织。"
        return f"这次问题同时命中了 {bundle['left_node']['title']} 和 {bundle['right_node']['title']}，回答会先分清两边各自在说什么。"
    if result["syllabus_route"]:
        return f"这次问题优先路由到了 {result['syllabus_route'][0]['title']}，所以回答以该考纲节点下的正式主张和证据为主。"
    return "这次没有稳定命中考纲节点，只能退回到证据层或章节概览。"


def strict_explanation(result: dict) -> list[str]:
    grounding = normalized_answer_grounding(result)
    if grounding["required"] and not grounding["can_conclude"]:
        return [str(grounding.get("failure_reason") or "原书答案未确认，已停止解题。")]
    exercise = dict(result.get("exercise_route") or {})
    if exercise.get("match_status") == "exact_exercise":
        question = dict(exercise.get("question") or {})
        solution = dict(exercise.get("solution") or {})
        lines = []
        if question.get("content"):
            lines.append(f"原题：{question['content']}")
        if solution.get("content"):
            lines.append(f"书中题解：{solution['content']}")
        if exercise.get("pair_status") == "solution_pending":
            lines.append("强化篇题解状态：待接入。")
        return lines or ["习题记录已定位，但正文尚不可用。"]
    bundle = result.get("compare_bundle")
    if bundle:
        if bundle.get("mode") == "single_node":
            return [f"{bundle['node']['title']}: {bundle['primary_claim']['text']}"]
        if bundle.get("mode") == "single_node_evidence":
            evidence = bundle["primary_evidence"]
            first_lines = [line.strip() for line in str(evidence.get("content", "")).splitlines() if line.strip()]
            return [f"{bundle['node']['title']}: {first_lines[1] if len(first_lines) > 1 else evidence.get('title', '')}"]
        return [
            f"{bundle['left_node']['title']}: {bundle['left_claim']['text']}",
            f"{bundle['right_node']['title']}: {bundle['right_claim']['text']}",
        ]
    lines: list[str] = []
    anchor_snippets = page_anchor_snippets(result)
    for snippet in anchor_snippets:
        if snippet not in lines:
            lines.append(snippet)
    for claim in result["claim_hits"][:4]:
        line = f"{claim['claim_type']}: {claim['text']}"
        if line not in lines:
            lines.append(line)
    if not lines:
        for evidence in result["evidence_hits"][:3]:
            if request_kind_for_payload(result) == "generic":
                _, first_line = evidence_excerpt(evidence, str(result.get("query", "")))
            else:
                first_line = evidence.get("content", "").splitlines()[0] if evidence.get("content") else evidence.get("title", "")
            lines.append(f"{evidence.get('evidence_type', '')}: {first_line}")
    return lines or ["当前没有稳定主张，只能继续补证据。"]


def example_lines(result: dict) -> list[str]:
    examples = [claim["text"] for claim in result["claim_hits"] if claim.get("claim_type") == "example_type"]
    examples = dedupe(examples)
    if examples:
        return examples[:3]
    derived = [item.get("title", "") for item in result["evidence_hits"][:3] if item.get("evidence_type") in {"example", "exercise"}]
    return dedupe(derived)[:3]


def personalized_reminder(result: dict) -> str:
    teaching_context = dict(result.get("teaching_context") or {})
    guidance = personalized_teaching_guidance(result)
    self_check = str(teaching_context.get("self_check", "")).strip()
    if self_check:
        guidance.append(f"讲完后自测：{self_check}")
    if guidance:
        return "；".join(guidance)
    snapshot = result.get("learner_snapshot", {})
    question_count = int(snapshot.get("question_count", 0))
    if question_count >= 5:
        return "你在这个学科已经有连续提问记录，优先补高频卡点，不要再回到全章泛看。"
    if question_count >= 1:
        return "这个学科已经开始形成个人提问轨迹，建议继续围绕当前命中的考纲节点追问。"
    return "当前还没有形成个人历史，先把这个节点的定义、规则和易混点问扎实。"


def personalized_teaching_guidance(result: dict) -> list[str]:
    context = dict(result.get("teaching_context") or {})
    guidance: list[str] = []
    anchor = str(context.get("accepted_anchor", "")).strip()
    if anchor:
        guidance.append(f"先从已确认的核心抓手开始：{anchor}")
    routes = [str(item).strip() for item in context.get("preferred_routes", []) if str(item).strip()]
    if routes:
        guidance.append(f"优先按这条路线展开：{' -> '.join(routes)}")
    handoff = dict(context.get("learning_handoff") or {})
    original = dict(handoff.get("original_problem") or {})
    if original.get("title"):
        guidance.append(f"续接上次原题：{original['title']}（{original.get('mastery_status') or '待验证'}）")
    if handoff.get("handoff_summary"):
        guidance.append(f"真实停点：{handoff['handoff_summary']}")
    for item in context.get("avoid_as_first_explanation", []):
        if not isinstance(item, dict):
            continue
        method = str(item.get("method", "")).strip()
        reason = str(item.get("reason", "")).strip()
        if method:
            guidance.append(f"本次不先采用“{method}”；{reason or '它并非错误方法，只是当前不作为首选。'}")
    return guidance


def source_differences(result: dict) -> list[str]:
    titles = dedupe([str(evidence.get("title", "")).strip() for evidence in result["evidence_hits"][:5]])
    if result.get("fallback_note"):
        return [result["fallback_note"]]
    return titles[:3] or ["当前只有单一证据视角。"]


def next_steps(result: dict) -> list[str]:
    if result["answer_mode"] == "chapter_fallback" and result.get("refinement_candidates"):
        return [f"先处理 refinement：{item.get('candidate_type', '')}" for item in result["refinement_candidates"][:3]]
    if result["intent"] == "plan" and result["syllabus_route"]:
        return [f"继续追问 {item['title']} 的定义、条件和典型题型。" for item in result["syllabus_route"][:3]]
    bundle = result.get("compare_bundle")
    if bundle:
        if bundle.get("mode") == "single_node":
            return [f"继续追问 {bundle['node']['title']} 的判定口径、反例和易错点。"]
        if bundle.get("mode") == "single_node_evidence":
            return [f"继续把 {bundle['node']['title']} 补成正式 comparison/confusion claim。"]
        return [
            f"继续追问 {bundle['left_node']['title']} 的定义边界。",
            f"继续追问 {bundle['right_node']['title']} 的判断口径。",
        ]
    if result["claim_hits"]:
        return [f"把“{claim['text'][:24]}”继续追问成条件、反例或题型。" for claim in result["claim_hits"][:3]]
    if result["references"]:
        return [f"回到 {ref['title']}，按页段 {ref['page_span']} 继续补充。" for ref in result["references"][:3]]
    return ["先补当前章节的 chunk 提取，再重新同步知识库。"]


def _span_text(start: Any, end: Any) -> str:
    left = str(start or "").strip()
    right = str(end or "").strip()
    if left and right:
        return f"{left}-{right}"
    return left or right


def _ranking_by_evidence(result: dict) -> dict[str, dict[str, Any]]:
    ranking: dict[str, dict[str, Any]] = {}
    for hit in result.get("retrieval_hits", []):
        factors = dict(hit.get("ranking_factors", {}))
        factors.setdefault("final_score", hit.get("score", 0))
        factors["retrieval_doc_id"] = hit.get("doc_id", "")
        factors["retrieval_doc_type"] = hit.get("doc_type", "")
        for evidence_id in hit.get("references", []):
            if evidence_id and evidence_id not in ranking:
                ranking[evidence_id] = factors
    return ranking


def _evidence_from_id(layout: dict[str, Path], evidence_id: str) -> dict[str, Any]:
    path = layout["evidence"] / f"{evidence_id}.json"
    return load_json(path) if evidence_id and path.exists() else {}


def build_citations(result: dict, *, limit: int = 3) -> list[dict[str, Any]]:
    layout = kb_layout()
    ranking = _ranking_by_evidence(result)
    ordered_ids: list[str] = []
    grounding = normalized_answer_grounding(result)
    request_kind = request_kind_for_payload(result)
    for side_name in ("problem", "solution"):
        for evidence_id in _side_evidence_ids(dict(grounding.get(side_name) or {})):
            if evidence_id not in ordered_ids:
                ordered_ids.append(evidence_id)

    grounding = normalized_answer_grounding(result)
    for side_name in ("problem", "solution"):
        for evidence_id in _side_evidence_ids(dict(grounding.get(side_name) or {})):
            if evidence_id and evidence_id not in ordered_ids:
                ordered_ids.append(evidence_id)

    exercise = dict(result.get("exercise_route") or {})
    for side in ("question", "solution"):
        evidence_id = str((exercise.get(side) or {}).get("evidence_id") or "").strip()
        if evidence_id and evidence_id not in ordered_ids:
            ordered_ids.append(evidence_id)

    for ref in result.get("references", []):
        evidence_id = str(ref.get("evidence_id", "")).strip()
        if evidence_id and evidence_id not in ordered_ids:
            ordered_ids.append(evidence_id)
    for hit in result.get("retrieval_hits", []):
        for evidence_id in hit.get("references", []):
            evidence_id = str(evidence_id).strip()
            if evidence_id and evidence_id not in ordered_ids:
                ordered_ids.append(evidence_id)
    for evidence in result.get("evidence_hits", []):
        evidence_id = str(evidence.get("evidence_id", "")).strip()
        if evidence_id and evidence_id not in ordered_ids:
            ordered_ids.append(evidence_id)

    if request_kind == "generic":
        dependency_ids = generic_dependency_ids(result)
        ordered_ids = dependency_ids + [item for item in ordered_ids if item not in dependency_ids]

    grounding_id_count = len(
        {
            evidence_id
            for side_name in ("problem", "solution")
            for evidence_id in _side_evidence_ids(dict(grounding.get(side_name) or {}))
        }
    )
    citation_limit = max(limit, grounding_id_count, len(generic_dependency_ids(result)) if request_kind == "generic" else 0)
    citations: list[dict[str, Any]] = []
    fallback_refs = {ref.get("evidence_id", ""): ref for ref in result.get("references", [])}
    for evidence_id in ordered_ids[:citation_limit]:
        evidence = _evidence_from_id(layout, evidence_id)
        if request_kind == "generic":
            requested_book = generic_book_title(result)
            if (
                not requested_book
                or not evidence
                or not is_publishable_source_evidence(evidence)
                or not evidence_matches_book(evidence, requested_book)
            ):
                continue
        ref = fallback_refs.get(evidence_id, {})
        locator = evidence.get("locator", {}) if evidence else {}
        page_refs = list(evidence.get("page_classification_refs", []) or ref.get("page_classification_refs", []) or [])
        primary_ref = preferred_page_ref(evidence, page_refs)
        use_evidence_chapter = should_prefer_evidence_chapter(evidence, primary_ref)
        citation = {
            "evidence_id": evidence_id,
            "title": evidence.get("title") or ref.get("title", ""),
            "source_id": evidence.get("source_id", ""),
            "chapter_id": evidence.get("chapter_id", ""),
            "chunk_id": evidence.get("chunk_id") or ref.get("chunk_id", ""),
            "chunk_kb_id": evidence.get("chunk_kb_id", ""),
            "page_span": _span_text(locator.get("page_start"), locator.get("page_end")) or ref.get("page_span", ""),
            "image_span": _span_text(locator.get("image_start"), locator.get("image_end")) or ref.get("image_span", ""),
            "page_classification_refs": page_refs,
            "book_id": primary_ref.get("book_id", ref.get("book_id", "")),
            "book_title": evidence.get("book_title", "") or primary_ref.get("book_title", ref.get("book_title", "")),
            "book_chapter_title": evidence.get("chapter_title", "") if use_evidence_chapter else (primary_ref.get("chapter_title", ref.get("book_chapter_title", "")) or evidence.get("chapter_title", "")),
            "section_title": primary_ref.get("section_title", ref.get("section_title", "")),
            "chapter_view_path": primary_ref.get("chapter_view_path", ref.get("chapter_view_path", "")),
            "section_view_path": primary_ref.get("section_view_path", ref.get("section_view_path", "")),
            "source_grounded": bool(evidence.get("source_grounded")),
            "verification_status": evidence.get("verification_status", ""),
            "confidence": evidence.get("confidence", 0),
            "ranking_factors": ranking.get(evidence_id, {}),
        }
        citations.append(citation)
    return citations


def build_evidence_assessment(result: dict, citations: list[dict[str, Any]]) -> dict[str, str]:
    """Describe what the local structured layer can prove without reading an image."""
    answer_mode = str(result.get("answer_mode", ""))
    source_verify = result.get("intent") == "source_verify"
    anchor = dict(result.get("page_anchor") or {})
    page_status = str(anchor.get("match_status", ""))
    grounding = normalized_answer_grounding(result)
    if grounding["required"] and not grounding["can_conclude"]:
        return {
            "level": str(grounding.get("status") or "answer_not_found"),
            "can_confirm": "当前只能确认原题或资料定位状态，原书答案尚未形成可核验锚点。",
            "cannot_confirm": "不能输出解题结论，也不能以 AI 独立推导代替原书答案。",
            "next_action": str(grounding.get("next_action") or "请先定位原书答案。"),
        }

    if page_status == "unavailable":
        return {
            "level": "page_unavailable",
            "can_confirm": "本地证据链当前不可用。",
            "cannot_confirm": "当前不能判断教材是否包含该页、公式、推导或原文。",
            "next_action": "请先修复配置或正式页码索引，再重新执行结构化查询。",
        }
    if request_kind_for_payload(result) == "generic" and "generic_gate" in result:
        gate = dict(result.get("generic_gate") or {})
        dependency_ids = set(generic_dependency_ids(result))
        citation_ids = {str(item.get("evidence_id") or "").strip() for item in citations if str(item.get("evidence_id") or "").strip()}
        gate_ok = (
            str(gate.get("status") or "") == "exact"
            and bool(gate.get("relevance_ok"))
            and bool(gate.get("same_book_ok"))
            and bool(dependency_ids)
            and dependency_ids.issubset(citation_ids)
            and str(answer_mode) in {"canonical_claim", "accepted_evidence"}
        )
        if gate_ok:
            return {
                "level": "structured_evidence",
                "can_confirm": "当前教材范围内的同书审核证据支持本次结论。",
                "cannot_confirm": "当前结论仅覆盖已列出的同书引用范围。",
                "next_action": "可沿引用继续核对条件、例题或原始上下文。",
            }
        return {
            "level": "unconfirmed",
            "can_confirm": "当前只能确认检索边界，未形成通过主题、同书和引用覆盖门禁的结论。",
            "cannot_confirm": "不能用其他教材、无关主题或未列入 citations 的证据补足本次回答。",
            "next_action": str(gate.get("next_action") or "请补充同书审核证据后重新查询。"),
        }
    if answer_mode in STRUCTURED_ANSWER_MODES and citations:
        return {
            "level": "structured_evidence",
            "can_confirm": "本地已整理的主张或证据支持本次结论。",
            "cannot_confirm": "当前结论仅覆盖已列出的引用范围。",
            "next_action": "可沿引用继续核对条件、例题或原始上下文。",
        }
    if page_status == "exact_asset":
        return {
            "level": "page_asset_only",
            "can_confirm": "已确认教材原页的位置，但该页尚无结构化 OCR 或正式证据，教材正文未确认。",
            "cannot_confirm": "不能仅据页码映射确认书上是否出现了某个公式、推导或原文表述。",
            "next_action": "如仍需核对，请明确要求人工阅图；也可先补该页 OCR 后重新查询。",
        }
    if page_status in {"ambiguous", "unmapped", "not_found"}:
        labels = {
            "ambiguous": "存在多个可能教材页",
            "unmapped": "该教材页尚未建立正式映射",
            "not_found": "正式页码索引未找到该页",
        }
        return {
            "level": f"page_{page_status}",
            "can_confirm": labels[page_status] + "。",
            "cannot_confirm": "当前无法确认书中具体内容。",
            "next_action": "请补充教材名或页码；映射完成后再进行结构化核验。",
        }
    if source_verify:
        return {
            "level": "structured_unconfirmed",
            "can_confirm": "当前本地结构化资料没有给出可核验的教材结论。",
            "cannot_confirm": "不能把章节摘要、检索不到的结果或补充讲解说成书上原文。",
            "next_action": "可补充书名、章节或页码后重查；如已定位原页，可再明确要求人工阅图。",
        }
    if answer_mode == "chapter_fallback":
        return {
            "level": "chapter_summary",
            "can_confirm": "当前只命中章节级概览，可用于确定大致主题。",
            "cannot_confirm": "章节概览不能证明具体公式、原文或例题就在书中出现。",
            "next_action": "先补充章节提取或 OCR，再回到本地检索。",
        }
    return {
        "level": "unconfirmed",
        "can_confirm": "当前没有足够稳定的本地证据。",
        "cannot_confirm": "不能据此给出教材事实结论。",
        "next_action": "补充检索条件或资料证据后再回答。",
    }


def content_provenance(result: dict, assessment: dict[str, str]) -> list[dict[str, Any]]:
    """Keep textbook assertions and supplemental teaching material separately labelled."""
    grounding = normalized_answer_grounding(result)
    if grounding["required"]:
        items: list[dict[str, Any]] = []
        for content_id, side_name, source_type in (
            ("original-problem", "problem", "textbook_problem_evidence"),
            ("source-answer", "solution", "textbook_solution_evidence"),
        ):
            side = dict(grounding.get(side_name) or {})
            if not side:
                continue
            pages = list(side.get("printed_pages") or [])
            items.append(
                {
                    "content_id": content_id,
                    "content_type": "original_problem" if side_name == "problem" else "source_answer",
                    "source_type": source_type,
                    "source_label": CONTENT_SOURCE_LABELS[source_type],
                    "textbook_assertion_allowed": bool(grounding["can_conclude"]),
                    "printed_page": pages[0] if pages else None,
                    "printed_pages": pages,
                    "exercise_label": str((result.get("page_anchor") or {}).get("requested_exercise_label") or ""),
                    "evidence_ids": _side_evidence_ids(side),
                }
            )
        for index, raw in enumerate(result.get("supplementary_content", []) or [], start=1):
            if not grounding["can_conclude"] or not isinstance(raw, dict):
                continue
            title = str(raw.get("title", "")).strip()
            explanation = str(raw.get("explanation", "")).strip()
            if not title and not explanation:
                continue
            items.append(
                {
                    "content_id": str(raw.get("content_id", "")).strip() or f"supplement-{index}",
                    "content_type": "supplementary_derivation",
                    "source_type": "supplementary_derivation",
                    "source_label": CONTENT_SOURCE_LABELS["supplementary_derivation"],
                    "textbook_assertion_allowed": False,
                    "title": title,
                    "explanation": explanation,
                    "related_to": "source-answer",
                    "printed_page": None,
                    "exercise_label": "",
                }
            )
        return items

    level = str(assessment.get("level", ""))
    anchor = dict(result.get("page_anchor") or {})
    if level == "structured_evidence":
        source_type = "textbook_structured_evidence"
    elif level == "page_asset_only":
        source_type = "page_asset_only"
    elif level == "page_unavailable":
        source_type = "runtime_unavailable"
    else:
        source_type = "learner_feedback" if result.get("intent") == "learner_feedback" else "supplementary_derivation"
    items: list[dict[str, Any]] = [
        {
            "content_id": "primary-answer",
            "content_type": "original_problem" if anchor.get("requested_page") is not None else "answer",
            "source_type": source_type,
            "source_label": CONTENT_SOURCE_LABELS[source_type],
            "textbook_assertion_allowed": source_type == "textbook_structured_evidence",
            "printed_page": anchor.get("requested_page"),
            "exercise_label": anchor.get("exercise_label", ""),
        }
    ]
    for index, raw in enumerate(result.get("supplementary_content", []) or [], start=1):
        if not isinstance(raw, dict):
            continue
        title = str(raw.get("title", "")).strip()
        explanation = str(raw.get("explanation", "")).strip()
        if not title and not explanation:
            continue
        items.append(
            {
                "content_id": str(raw.get("content_id", "")).strip() or f"supplement-{index}",
                "content_type": "supplementary_derivation",
                "source_type": "supplementary_derivation",
                "source_label": CONTENT_SOURCE_LABELS["supplementary_derivation"],
                "textbook_assertion_allowed": False,
                "title": title,
                "explanation": explanation,
                "related_to": str(raw.get("related_to", "primary-answer")).strip() or "primary-answer",
                # A generic derivation must never inherit the original problem's identity.
                "printed_page": None,
                "exercise_label": "",
            }
        )
    return items


def build_answer_contract(result: dict) -> dict[str, Any]:
    citations = build_citations(result)
    grounding = normalized_answer_grounding(result)
    request_resolution = dict(result.get("request_resolution") or {})
    teaching = dict(result.get("teaching_bundle") or {}) or build_teaching_bundle(grounding, request_resolution)
    page_content = dict(result.get("page_content_bundle") or {}) or build_page_content_bundle(
        request_resolution=request_resolution,
        page_anchor=dict(result.get("page_anchor") or {}),
        evidences=list(result.get("evidence_hits") or []),
        page_crosscheck=dict(result.get("page_crosscheck") or {}),
    )
    evidence_assessment = build_evidence_assessment(result, citations)
    provenance = content_provenance(result, evidence_assessment)
    direct = direct_conclusion(result)
    solution = dict(grounding.get("solution") or {})
    if grounding["required"]:
        direct = str(solution.get("content") or "").strip() if grounding["can_conclude"] else str(grounding.get("failure_reason") or "原书答案未确认，已停止解题。")
    if result.get("intent") == "source_verify" and evidence_assessment["level"] != "structured_evidence":
        direct = direct if grounding["required"] else evidence_assessment["can_confirm"]
    supplemental = [
        str(item.get("explanation") or item.get("title") or "").strip()
        for item in result.get("supplementary_content", []) or []
        if isinstance(item, dict) and str(item.get("explanation") or item.get("title") or "").strip()
    ] if grounding["can_conclude"] else []
    concept_routes = list(result.get("concept_routes") or []) if grounding["can_conclude"] else []
    sections = {
        "answer_grounding_status": str(grounding["status"]),
        "source_answer": str(solution.get("content") or "").strip() if grounding["can_conclude"] else "",
        "first_incorrect_equality": "原书答案已锚定后，逐行核对用户过程并只指出第一个错误等号。" if grounding["can_conclude"] else "",
        "supplementary_derivation": supplemental,
        "concept_routes": concept_routes,
        "syllabus_position": [
            {"node_id": item.get("node_id", ""), "title": item.get("title", ""), "score": item.get("score", 0)}
            for item in result.get("syllabus_route", [])
        ],
        "direct_conclusion": direct,
        "intuitive_explanation": intuitive_explanation(result),
        "strict_explanation": strict_explanation(result),
        "typical_examples": example_lines(result),
        "personalized_reminder": personalized_reminder(result),
        "source_differences": source_differences(result),
        "next_steps": next_steps(result),
        "content_boundaries": provenance,
    }
    if grounding["required"] and not grounding["can_conclude"]:
        sections["strict_explanation"] = [str(grounding.get("failure_reason") or "原书答案未确认，已停止解题。")]
        sections["typical_examples"] = []
        sections["next_steps"] = [str(grounding.get("next_action") or "请先定位原书答案。")]
    citation_required = result.get("answer_mode") in {"canonical_claim", "accepted_evidence", "exercise_pair"}
    citation_ids = {str(item.get("evidence_id") or "") for item in citations}
    problem_ids = set(_side_evidence_ids(dict(grounding.get("problem") or {})))
    solution_ids = set(_side_evidence_ids(dict(grounding.get("solution") or {})))
    request_kind = request_kind_for_payload(result)
    generic_ids = set(generic_dependency_ids(result))
    if grounding["required"]:
        coverage_ok = bool(grounding["can_conclude"] and problem_ids and solution_ids and problem_ids <= citation_ids and solution_ids <= citation_ids)
    elif request_kind == "generic":
        coverage_ok = bool(generic_ids) and generic_ids.issubset(citation_ids)
    else:
        coverage_ok = (not citation_required) or bool(citations)
    generic_bundle = build_generic_answer_bundle(
        result,
        direct=direct,
        explanation=list(sections.get("strict_explanation") or []),
        citations=citations,
    )
    if request_kind == "generic" and "generic_gate" in result and generic_bundle.get("status") == "blocked":
        blocked_text = str(generic_bundle.get("failure_reason") or "当前回答未通过通用证据门禁，已停止不确定回答。")
        sections["direct_conclusion"] = blocked_text
        sections["intuitive_explanation"] = str(generic_bundle.get("next_action") or "请先补充同书审核证据。")
        sections["strict_explanation"] = [blocked_text]
        sections["typical_examples"] = []
        sections["supplementary_derivation"] = []
        sections["next_steps"] = [str(generic_bundle.get("next_action") or "请先补充同书审核证据。")]
    contract = {
        "answer_contract_version": ANSWER_CONTRACT_VERSION,
        "subject": result.get("subject", ""),
        "chapter": result.get("chapter", ""),
        "question": result.get("query", ""),
        "query": result.get("query", ""),
        "intent": result.get("intent", ""),
        "answer_mode": result.get("answer_mode", ""),
        "fallback_note": result.get("fallback_note", ""),
        "book_route": dict(result.get("book_route") or {}),
        "book_resolution": dict(result.get("book_resolution") or {}),
        "exercise_route": dict(result.get("exercise_route") or {}),
        "answer_grounding": grounding,
        "citation_coverage_ok": coverage_ok,
        "evidence_assessment": evidence_assessment,
        "content_provenance": provenance,
        "sections": sections,
        "citations": citations,
        "teaching_context": dict(result.get("teaching_context") or {}),
        "request_resolution": request_resolution,
        "page_crosscheck": dict(result.get("page_crosscheck") or {}),
        "generic_answer_bundle": generic_bundle,
        "teaching_bundle": teaching,
        "page_content_bundle": page_content,
        "concept_routes": concept_routes,
        # Compatibility fields for older callers that consumed raw query output.
        "syllabus_route": result.get("syllabus_route", []),
        "references": result.get("references", []),
        "retrieval_hits": result.get("retrieval_hits", []),
        "claim_hits": result.get("claim_hits", []),
        "evidence_hits": result.get("evidence_hits", []),
        "fallback_hits": result.get("fallback_hits", []),
        "page_anchor": result.get("page_anchor", {}),
        "page_verification": result.get("page_verification", {}),
        "textbook_location": dict(result.get("textbook_location") or {}),
        "runtime_context": dict(result.get("runtime_context") or {}),
        "query_result": result,
    }
    # Capture the same records used to build this full contract, before output projection.
    contract["source_revisions"] = capture(contract)
    validate_teaching_contract_invariants(contract)
    validate_entity_contract("query_artifact", contract)
    return contract


def render_text(contract: dict) -> str:
    sections = contract["sections"]
    lines = [
        "# 本地问答草稿",
        "",
        f"- 问题：{contract['question']}",
        f"- 学科：{contract['subject']}",
        f"- 依据级别：{contract['evidence_assessment']['level']}",
        f"- 契约版本：{contract['answer_contract_version']}",
    ]
    grounding = dict(contract.get("answer_grounding") or {})
    if grounding.get("required"):
        lines.extend(
            [
                "",
                "## 答案定位状态",
                "",
                f"- 状态：{grounding.get('status', 'answer_not_found')}",
                f"- 可输出结论：{'是' if grounding.get('can_conclude') else '否'}",
                f"- 原因：{grounding.get('failure_reason') or '原题与原书答案均已确认。'}",
                f"- 下一步：{grounding.get('next_action') or '按原书答案核对用户过程。'}",
                "",
                "## 原书答案",
                "",
                sections.get("source_answer") or "- 原书答案未确认，本次不输出解题结论。",
            ]
        )
        concept_routes = list(sections.get("concept_routes") or [])
        if concept_routes:
            lines.extend(["", "## 主书定理依据", ""])
            for route in concept_routes:
                concept = str(route.get("concept") or "")
                primary = dict(route.get("primary") or {})
                if primary.get("status") == "exact":
                    pages = "、".join(str(item) for item in primary.get("printed_pages", []) or []) or "未标注"
                    evidence_ids = "、".join(str(item) for item in primary.get("evidence_ids", []) or [])
                    lines.extend(
                        [
                            f"### {concept}",
                            "",
                            str(primary.get("content") or ""),
                            "",
                            f"- 来源：{primary.get('book_title', '')}；印刷页：{pages}；证据：{evidence_ids}",
                        ]
                    )
                else:
                    lines.append(f"- {concept}：本书未单独定位到已审核的定理或定义正文。")
            exact_supplements = [route for route in concept_routes if (route.get("supplement") or {}).get("status") == "exact"]
            if exact_supplements:
                lines.extend(["", "## 李正元补充（非当前主线）", ""])
                for route in exact_supplements:
                    supplement = dict(route.get("supplement") or {})
                    pages = "、".join(str(item) for item in supplement.get("printed_pages", []) or []) or "未标注"
                    evidence_ids = "、".join(str(item) for item in supplement.get("evidence_ids", []) or [])
                    lines.extend(
                        [
                            f"### {route.get('concept', '')}",
                            "",
                            str(supplement.get("content") or ""),
                            "",
                            f"- 来源：{supplement.get('book_title', '')}；印刷页：{pages}；证据：{evidence_ids}",
                            "- 边界：仅作跨书补充，不改变本题原书答案或当前学习主线。",
                        ]
                    )
        lines.extend(
            [
                "",
                "## 过程核对",
                "",
                sections.get("first_incorrect_equality") or "- 等待原书答案确认后再核对用户过程。",
                "",
                "## AI 辅助推导",
                "",
            ]
        )
        supplements = sections.get("supplementary_derivation", [])
        if supplements:
            lines.extend(f"- {item}" for item in supplements)
        else:
            lines.append("- 当前没有允许输出的补充推导。")
    lines.extend(["", "## 考纲定位", ""])
    if sections["syllabus_position"]:
        for item in sections["syllabus_position"]:
            lines.append(f"- {item['title']} (`{item['node_id']}`)")
    else:
        lines.append("- 当前没有稳定命中正式考纲节点。")
    if contract.get("fallback_note"):
        lines.extend(["", "## 回退说明", "", f"- {contract['fallback_note']}"])
    assessment = contract["evidence_assessment"]
    lines.extend(
        [
            "",
            "## 证据边界",
            "",
            f"- 能确认：{assessment['can_confirm']}",
            f"- 不能确认：{assessment['cannot_confirm']}",
            f"- 下一步：{assessment['next_action']}",
        ]
    )
    verification = dict(contract.get("page_verification") or {})
    if verification:
        lines.extend(
            [
                "",
                "## 页码核验摘要",
                "",
                f"- 页面定位：{verification.get('page_location_status', '未确认')}",
                f"- 题号正文核验：{verification.get('exercise_verification_status', '未确认')}",
                f"- 命中层：{verification.get('answer_mode', contract.get('answer_mode', ''))}",
                f"- 可否按教材正文讲解：{'可以' if verification.get('textbook_explanation_allowed') else '不可以'}",
                f"- 结论：{verification.get('summary', '')}",
            ]
        )
    lines.extend(["", "## 内容来源", ""])
    for item in contract.get("content_provenance", []):
        identity: list[str] = []
        if item.get("printed_page") is not None:
            identity.append(f"印刷页 {item['printed_page']}")
        if item.get("exercise_label"):
            identity.append(f"题号 {item['exercise_label']}")
        relation = f"；关联 {item['related_to']}" if item.get("related_to") else ""
        suffix = f"（{'，'.join(identity)}）" if identity else ""
        lines.append(f"- {item['source_label']}{suffix}{relation}")
    lines.extend(["", "## 直接结论", "", str(sections["direct_conclusion"])])
    lines.extend(["", "## 直观解释", "", str(sections["intuitive_explanation"])])
    lines.extend(["", "## 严格说明", ""])
    for line in sections["strict_explanation"]:
        lines.append(f"- {line}")
    lines.extend(["", "## 典型题型", ""])
    example_items = sections["typical_examples"]
    if example_items:
        for line in example_items:
            lines.append(f"- {line}")
    else:
        lines.append("- 当前还没有稳定题型卡。")
    lines.extend(["", "## 个性化提醒", "", str(sections["personalized_reminder"])])
    lines.extend(["", "## 来源差异", ""])
    for line in sections["source_differences"]:
        lines.append(f"- {line}")
    lines.extend(["", "## 下一步建议", ""])
    for line in sections["next_steps"]:
        lines.append(f"- {line}")
    lines.extend(["", "## 来源引用", ""])
    if contract["citations"]:
        for ref in contract["citations"]:
            section_text = f" | 小节 {ref['section_title']}" if ref.get("section_title") else ""
            view_text = f" | 视图 {ref['section_view_path']}" if ref.get("section_view_path") else ""
            lines.append(
                f"- {ref['evidence_id']} | {ref['title']} | 页段 {ref['page_span']} | 图片 {ref['image_span']} | chunk {ref['chunk_id']}{section_text}{view_text}"
            )
    else:
        lines.append("- 当前没有稳定证据引用。")
    return "\n".join(lines).rstrip() + "\n"


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    args = parse_args()
    subject, _ = resolve_subject(args.subject)
    result = query_knowledge(Path(args.vault_root), subject, args.chapter, args.question, args.topk, args.printed_page, args.book_title)
    contract = build_answer_contract(result)
    if args.format == "json":
        print(json.dumps(contract, ensure_ascii=False, indent=2))
    elif args.format == "teaching-json":
        print(json.dumps(build_teaching_answer_view(contract), ensure_ascii=False, indent=2))
    else:
        print(render_text(contract), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
