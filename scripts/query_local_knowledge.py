#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from pathlib import Path
from typing import Any

from common import INDEX_DIRNAME, default_vault_root_arg, kb_layout, is_publishable_claim, is_publishable_source_evidence, learner_file_map, resolve_subject, runtime_context_payload, validate_entity_contract
from kaoyan_kb.domain.page_locator import evidence_matches_locator, load_page_locator_index, parse_exercise_label, resolve_page_locator
from kaoyan_kb.domain.exercise_locator import assemble_exact_relation, container_path_from_query, find_exact_relation, find_exact_worked_example_relation, find_unique_relation_for_scope, list_exact_relations_for_question_page, list_exact_worked_example_relations, normalize_exercise_category, normalize_exercise_label, resolve_section_anchor
from kaoyan_kb.domain.book_series import classify_source_request, has_exercise_request_signal, parse_exercise_request, resolve_answer_grounding, resolve_book_route, resolve_exercise_route
from kaoyan_kb.domain.exercise_batch import BATCH_CONTRACT_VERSION, parse_exercise_batch_request, resolve_exercise_batch_targets
from kaoyan_kb.domain.teaching_context import build_bounded_teaching_context
from kaoyan_kb.domain.query.request import (
    normalize_text, resolve_current_task_book, chapter_matches, chapter_ordinal,
    chinese_number_to_int, detect_intent, extract_explicit_concepts, parse_page_anchor,
    parse_requested_option, needs_exercise_identity, resolve_request, build_page_crosscheck,
    explicit_page_subject_error, build_page_verification_summary, build_textbook_location,
    CURRENT_TASK_PATH, CONCEPT_SUFFIXES,
)
from kaoyan_kb.domain.query.topics import (
    tokenize, compare_parts, generic_topic_terms, topic_coverage, is_definition_request,
    evidence_has_formal_topic_statement, _claim_topic_text, score_text, title_match_score,
    _normalized_book_title, _book_title_matches, evidence_matches_book, _claim_support_evidence,
    _retrieval_hit_matches_book, _generic_candidate_records, build_generic_gate,
    GENERIC_QUERY_NOISE, GENERIC_ANSWER_MODES,
)
# Compatibility exports for existing Python callers; implementation lives in domain.
from kaoyan_kb.domain.query.grounding import (
    build_answer_grounding, build_teaching_bundle, build_page_content_bundle,
    _replace_relative_image_links, _promote_exact_pair_page_anchor,
    _empty_page_content_bundle, _reviewed_page_evidence,
)
from kaoyan_kb.domain.query.views import (
    render_text, render_exercise_batch_text,
)
from kaoyan_kb.domain.query.batch import (
    _batch_failure_text, _blocked_batch_item, _project_batch_item,
    query_exercise_batch as _query_exercise_batch,
)
from kaoyan_kb.domain.query.read_session import read_json as load_json, read_all_json as load_all_json, with_read_scope, read_scope
from learner_events import load_events_readonly as load_events
from retrieve_knowledge import retrieve as retrieve_index
from kaoyan_kb.domain.query.permission import finalized
from kaoyan_kb.domain.query.source_targets import query_source_targets, is_multi, render_targets, catalogue


PRIMARY_CALCULUS_BOOK_TITLE = "高等数学辅导讲义基础篇"
SUPPLEMENTAL_CALCULUS_BOOK_TITLE = "李正元数一"


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
    parser.add_argument("--auto-sync", action="store_true", help="refresh missing/stale query indexes before querying (writes indexes)")
    parser.add_argument("--format", choices=("text", "json"), default="text")
    return parser.parse_args()


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
    layout = kb_layout()
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
        layout = kb_layout()
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
    layout = kb_layout()
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
    layout = kb_layout()
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


def attach_exercise_index_runtime(result: dict[str, Any]) -> None:
    availability = dict((result.get("exercise_anchor") or {}).get("_availability") or {})
    if not availability:
        return
    runtime = result.setdefault("runtime_context", {})
    runtime["exercise_locator_index_available"] = bool(availability.get("available", False))
    runtime["exercise_locator_index_path"] = str(availability.get("path") or "")
    runtime["exercise_locator_unavailable_reason"] = str(availability.get("reason") or "")


def load_syllabus(subject: str) -> tuple[dict, dict]:
    layout = kb_layout()
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
    files = learner_file_map(readonly=True)
    model = load_json(files["learner_model"]) if files["learner_model"].exists() else {}
    return model.get("subjects", {}).get(subject, {})


def learner_compare_candidates(subject: str, chapter: str | None) -> list[dict]:
    files = learner_file_map(readonly=True)
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
    formal_only: bool = False,
) -> list[dict]:
    layout = kb_layout()
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
        support_evidence = _claim_support_evidence(claim, book_title)
        if not support_evidence:
            continue
        if formal_only and not all(evidence_has_formal_topic_statement(item, topic_terms) for item in support_evidence):
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
    formal_only: bool = False,
) -> list[dict]:
    layout = kb_layout()
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
            if formal_only and not evidence_has_formal_topic_statement(evidence, topic_terms):
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
    formal_only: bool = False,
) -> list[dict]:
    layout = kb_layout()
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
        support_evidence = _claim_support_evidence(claim, book_title)
        if not support_evidence:
            continue
        if formal_only and not all(evidence_has_formal_topic_statement(item, topic_terms) for item in support_evidence):
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
    formal_only: bool = False,
) -> list[dict]:
    layout = kb_layout()
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
        if formal_only and not evidence_has_formal_topic_statement(evidence, topic_terms):
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
    layout = kb_layout()
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
        payload = retrieve_index(kb_layout(), subject=subject, query=query, topk=max(topk * 5, 20))
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
    formal_only: bool = False,
) -> tuple[list[dict], list[dict], list[dict], list[dict], bool]:
    retrieval_topk = max(topk, 10) if intent == "compare" and topic_terms else topk
    if intent == "compare" and topic_terms:
        merged_hits: dict[tuple[str, str], dict] = {}
        for retrieval_query in [query, *topic_terms]:
            for hit in retrieve_hits(subject, retrieval_query, retrieval_topk, chapter, book_title):
                key = (str(hit.get("doc_type") or ""), str(hit.get("entity_id") or hit.get("doc_id") or ""))
                previous = merged_hits.get(key)
                if previous is None or float(hit.get("score", 0.0) or 0.0) > float(previous.get("score", 0.0) or 0.0):
                    merged_hits[key] = hit
        retrieval_hits = sorted(
            merged_hits.values(),
            key=lambda item: (-float(item.get("score", 0.0) or 0.0), str(item.get("entity_id") or "")),
        )
    else:
        retrieval_hits = retrieve_hits(subject, query, retrieval_topk, chapter, book_title)
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
            formal_only,
        )[:8]
        evidence_candidates = evidence_hits_from_retrieval(
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
            formal_only,
        )
        if intent == "compare" and topic_terms:
            evidences = evidence_candidates[: max(5, len(topic_terms) * 4)]
            selected_ids = {str(item.get("evidence_id") or "") for item in evidences}
            for term in topic_terms:
                formal_match = next(
                    (
                        item
                        for item in evidence_candidates
                        if evidence_has_formal_topic_statement(item, [term])
                    ),
                    None,
                )
                evidence_id = str((formal_match or {}).get("evidence_id") or "")
                if formal_match is not None and evidence_id and evidence_id not in selected_ids:
                    evidences.append(formal_match)
                    selected_ids.add(evidence_id)
        else:
            evidences = evidence_candidates[:5]
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
            formal_only,
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
            formal_only,
        )[: max(5, len(topic_terms or []) * 4) if intent == "compare" else 5]
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
            True,
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
            True,
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


@with_read_scope
@finalized
def _query_single(
    vault_root: Path,
    subject: str,
    chapter: str | None,
    query: str,
    topk: int,
    printed_page: int | None = None,
    book_title: str | None = None,
    exercise_label: str | None = None,
    *, confirmed_book_title: str | None = None,
) -> dict:
    intent = detect_intent(query)
    book_resolution = resolve_current_task_book(vault_root=vault_root, subject=subject, explicit_book_title=book_title, query=query, confirmed_book_title=confirmed_book_title)
    effective_book_title = str(book_resolution.get("book_title") or "")
    request_resolution = resolve_request(query=query, book_title=effective_book_title, chapter=chapter, printed_page=printed_page, exercise_label=exercise_label)
    pair_request = request_resolution.get('exercise_scope') or parse_exercise_request(query)
    book_route = resolve_book_route(query=query, book_title=effective_book_title, request=pair_request)
    exercise_route = resolve_exercise_route(query=query, book_route=book_route, request=pair_request)
    if exercise_route.get('match_status') == 'exact_exercise' and exercise_route.get('pair_status') == 'exact_pair':
        book_id = (exercise_route.get('question') or {}).get('book_id')
        volumes = [v for s in catalogue() for v in s.get('volumes', []) if v.get('book_id') == book_id]
        # A unique formal relation may refine a series alias, never replace an
        # explicitly selected different volume.
        candidates = book_route.get('candidates', [])
        if len(volumes) == 1 and any(v.get('book_id') == book_id for v in candidates):
            explicit_volumes = [v for s in catalogue() for v in s.get('volumes', []) if _normalized_book_title(v.get('title', '')) == _normalized_book_title(effective_book_title)]
            if not explicit_volumes or explicit_volumes[0].get('book_id') == book_id:
                effective_book_title = volumes[0]['title']
                book_resolution.update(status='exact', book_title=effective_book_title, book_id=book_id, match_basis='unique_formal_exercise_relation')
                book_route.update(match_status='exact_series')
                request_resolution['book_title'] = effective_book_title
    request_resolution.setdefault('field_sources', {})['book_title'] = book_resolution.get('source', '')
    page_anchor_request = {
        "requested_page": request_resolution["page"]["number"],
        "requested_position": request_resolution.get("requested_position"),
        "requested_exercise_label": request_resolution["exercise_label"],
        "requested_container_path": list(request_resolution.get("container_path") or []),
    }
    effective_chapter = str(chapter or request_resolution.get("section_root") or "")
    resolved_label = str(request_resolution.get("exercise_label") or "")
    page_semantics = str(request_resolution["page"]["semantics"])
    generic_request = str(request_resolution.get("source_request_kind") or "generic") == "generic"
    topic_terms = generic_topic_terms(query, intent) if generic_request else None
    formal_only = generic_request and (is_definition_request(query, intent) or intent == "compare")
    require_topic_formal_coverage = generic_request and intent == "compare"
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
        formal_only=formal_only,
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
            exercise_request = pair_request
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
                pair_request['exercise_label'] = resolved_label if resolved_label.startswith('例') else ''
                pair_request['exercise_number'] = int(resolved_label) if resolved_label.isdigit() else None
                request_resolution['exercise_scope'] = pair_request
                exercise_route = resolve_exercise_route(query=query, book_route=book_route, request=pair_request)
                page_anchor_request["requested_exercise_label"] = resolved_label
                page_anchor["requested_exercise_label"] = resolved_label
        if resolved_label:
            exercise_anchor, evidences = apply_exercise_relation(page_anchor, evidences)
            if exercise_anchor.get('status') == 'exact_answer_evidence':
                page_anchor['exercise_match_status'] = 'matched'
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
        "formal_statement_required": False,
        "formal_statement_ok": True,
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
            formal_only=formal_only,
            require_topic_formal_coverage=require_topic_formal_coverage,
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
            request=pair_request,
        )
        if exercise_route.get("match_status") == "exact_exercise" and exercise_route.get("pair_status") == "exact_pair":
            # An exact series exercise relation is the formal exercise gate,
            # even when the request identifies the item by chapter and number
            # without an explicit printed-page anchor.
            page_anchor["exercise_match_status"] = "matched"
            # The page lookup above already used the resolved volume identity.
            # Never promote an unavailable/conflicting page from answer evidence.
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


@with_read_scope
def query_knowledge(vault_root: Path, subject: str, chapter: str | None, query: str, topk: int,
                    printed_page: int | None = None, book_title: str | None = None,
                    exercise_label: str | None = None, *, confirmed_book_title: str | None = None) -> dict:
    multiple = query_source_targets(query_one=_query_single, vault_root=vault_root, subject=subject,
                                    chapter=chapter, query=query, topk=topk, book_title=book_title,
                                    printed_page=printed_page, exercise_label=exercise_label,
                                    confirmed_book_title=confirmed_book_title)
    if multiple is not None:
        return multiple
    return _query_single(vault_root, subject, chapter, query, topk, printed_page, book_title, exercise_label, confirmed_book_title=confirmed_book_title)


@with_read_scope
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
    return _query_exercise_batch(
        vault_root, subject, chapter, query, topk, book_title, printed_page,
        view=view, query_one=query_knowledge, resolve_book=resolve_current_task_book,
        runtime_context=runtime_context_payload, resolve_targets=resolve_exercise_batch_targets,
    )


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
    if getattr(args, "auto_sync", False):
        from kaoyan_kb.cli.query_auto_sync import ensure_query_indexes
        ensure_query_indexes()
    with read_scope():
        multiple = query_source_targets(query_one=query_knowledge, vault_root=Path(args.vault_root), subject=subject, chapter=args.chapter, query=args.query or '', topk=args.topk, book_title=args.book_title, printed_page=args.printed_page, exercise_label=args.exercise_label)
        if multiple is not None:
            print(json.dumps(multiple, ensure_ascii=False, indent=2) if args.format == 'json' else render_targets(multiple))
            return 0
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
