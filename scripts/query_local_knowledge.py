#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

from common import INDEX_DIRNAME, default_vault_root_arg, ensure_kb_layout, learner_file_map, load_all_json, load_json, resolve_subject
from kaoyan_kb.domain.page_locator import evidence_matches_locator, parse_exercise_label, resolve_page_locator
from kaoyan_kb.domain.teaching_context import build_bounded_teaching_context
from learner_events import load_events
from retrieve_knowledge import retrieve as retrieve_index


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--vault-root", default=default_vault_root_arg())
    parser.add_argument("--subject")
    parser.add_argument("--chapter")
    parser.add_argument("--book-title")
    parser.add_argument("--query")
    parser.add_argument("--printed-page", type=int)
    parser.add_argument("--topk", type=int, default=3)
    parser.add_argument("--format", choices=("text", "json"), default="text")
    return parser.parse_args()


def normalize_text(value: Any) -> str:
    text = str(value or "").strip().lower()
    text = re.sub(r"\s+", " ", text)
    return text


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


def parse_page_anchor(query: str) -> dict[str, Any]:
    text = str(query or "")
    match = re.search(r"(?:第?\s*([0-9]+)\s*页|\b[Pp]\s*[.．]?\s*([0-9]+)\b)", text)
    requested_page = int(match.group(1) or match.group(2)) if match else None
    requested_position = None
    if any(token in text for token in ("最下方", "最下面", "页底", "底部", "最底下", "下方")):
        requested_position = "bottom"
    elif any(token in text for token in ("最上方", "最上面", "页首", "顶部", "上方")):
        requested_position = "top"
    elif any(token in text for token in ("中间", "中部")):
        requested_position = "middle"
    return {
        "requested_page": requested_page,
        "requested_position": requested_position,
        "requested_exercise_label": parse_exercise_label(text),
    }


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


def build_page_verification_summary(page_anchor: dict[str, Any], answer_mode: str) -> dict[str, Any]:
    """Expose page location, exercise verification, and teaching permission separately."""
    status = str(page_anchor.get("match_status") or "not_requested")
    exercise_status = str(page_anchor.get("exercise_match_status") or "not_requested")
    textbook_explanation_allowed = status == "exact_evidence" and exercise_status in {"not_requested", "matched"}
    if status == "exact_asset":
        summary = "教材原页已定位；教材正文未确认，不能按书上原题讲解。"
    elif status == "exact_evidence" and not textbook_explanation_allowed:
        summary = "教材页正文已有证据，但请求的题号尚未在正文中核验。"
    elif textbook_explanation_allowed:
        summary = "教材原页与所需结构化证据已核验，可在证据范围内按教材讲解。"
    elif status == "not_requested":
        summary = "本次未请求按页核验。"
    else:
        summary = "教材原页尚未完成可用于按书讲解的核验。"
    return {
        "page_location_status": status,
        "exercise_verification_status": exercise_status,
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
        if is_stale_evidence(evidence) or evidence.get("subject") != subject:
            continue
        if chapter and not evidence_matches_chapter(evidence, chapter):
            continue
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
        locator["match_status"] = "exact_evidence"
        label = str(locator.get("requested_exercise_label") or "")
        if label:
            normalized_label = normalize_text(label).replace(" ", "")
            haystack = normalize_text("\n".join(locator.get("snippets", []))).replace(" ", "")
            locator["exercise_match_status"] = "matched" if normalized_label in haystack else "unverified"
    return locator, exact_retrieval, exact_claims, exact_evidences


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
    normalized = [normalize_text(part) for part in parts if normalize_text(part)]
    compact = [part for part in normalized if len(part) <= 12]
    return compact[:3]


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
) -> list[dict]:
    layout = ensure_kb_layout()
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
        if claim.get("status") != "accepted":
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
) -> list[dict]:
    layout = ensure_kb_layout()
    claim_evidence_ids = {eid for claim in claim_list for eid in claim.get("evidence_ids", [])}
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
            seen.add(evidence_id)
            evidence = load_json(path)
            if is_stale_evidence(evidence):
                continue
            if evidence.get("subject") != subject:
                continue
            if chapter and not evidence_matches_chapter(evidence, chapter):
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
    subject: str, chapter: str | None, node_ids: list[str], tokens: list[str], full_query: str, intent: str
) -> list[dict]:
    layout = ensure_kb_layout()
    type_weights = {
        "compare": {"comparison": 1.8, "confusion": 1.5, "definition": 0.8, "rule": 0.6},
        "diagnose": {"confusion": 1.8, "comparison": 1.0, "rule": 0.7, "definition": 0.5},
        "plan": {"example_type": 1.3, "rule": 0.8, "definition": 0.6},
        "define": {"definition": 1.4, "rule": 1.0, "comparison": 0.5, "confusion": 0.4},
    }.get(intent, {})
    results: list[dict] = []
    for claim in load_all_json(layout["claims"]):
        if claim.get("subject") != subject or claim.get("status") != "accepted":
            continue
        if chapter and not chapter_matches(chapter, claim.get("chapter_title", ""), claim.get("chapter_hint", "")):
            continue
        if node_ids and claim.get("syllabus_node_id") not in node_ids:
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
    subject: str, chapter: str | None, node_ids: list[str], claim_list: list[dict], tokens: list[str], full_query: str, page_anchor: dict[str, Any]
) -> list[dict]:
    layout = ensure_kb_layout()
    claim_evidence_ids = {eid for claim in claim_list for eid in claim.get("evidence_ids", [])}
    results: list[dict] = []
    for evidence in load_all_json(layout["evidence"]):
        if is_stale_evidence(evidence) or evidence.get("subject") != subject:
            continue
        if chapter and not evidence_matches_chapter(evidence, chapter):
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


def fallback_chapter_hits(vault_root: Path, subject: str, chapter: str | None, tokens: list[str], full_query: str) -> list[dict]:
    path = vault_root / INDEX_DIRNAME / "chapter_knowledge_registry.json"
    if not path.exists():
        return []
    payload = load_json(path)
    results = []
    for item in payload.get("chapters", []):
        if item.get("subject") != subject:
            continue
        if chapter and chapter not in str(item.get("chapter_title", "")):
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
    for evidence in evidences[:3]:
        locator = evidence.get("locator", {})
        page_refs = list(evidence.get("page_classification_refs", []) or [])
        primary_ref = preferred_page_ref(evidence, page_refs)
        use_evidence_chapter = should_prefer_evidence_chapter(evidence, primary_ref)
        fallback_book_title = evidence.get("book_title", "")
        fallback_chapter_title = evidence.get("chapter_title", "")
        display_chapter_title = chapter or fallback_chapter_title
        use_requested_chapter = bool(chapter and not primary_ref)
        references.append(
            {
                "evidence_id": evidence.get("evidence_id", ""),
                "title": evidence.get("title", ""),
                "chunk_id": evidence.get("chunk_id", ""),
                "page_span": f"{locator.get('page_start', '')}-{locator.get('page_end', '')}",
                "image_span": f"{locator.get('image_start', '')}-{locator.get('image_end', '')}",
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


def retrieve_hits(subject: str, query: str, topk: int) -> list[dict]:
    try:
        payload = retrieve_index(ensure_kb_layout(), subject=subject, query=query, topk=max(topk, 5))
    except SystemExit:
        return []
    return list(payload.get("results", []))


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
) -> tuple[list[dict], list[dict], list[dict], list[dict], bool]:
    retrieval_hits = retrieve_hits(subject, query, topk)
    routed = route_syllabus_nodes(subject, query, max(topk, 3), intent)
    if not routed:
        routed = route_from_retrieval_hits(subject, retrieval_hits, topk)
    node_ids = [item["node_id"] for item in routed]
    index_routed = bool(retrieval_hits)
    if index_routed:
        claims = claim_hits_from_retrieval(subject, chapter, node_ids, retrieval_hits, intent)[:8]
        evidences = evidence_hits_from_retrieval(
            subject, chapter, node_ids, claims, retrieval_hits, tokens, full_query, page_anchor_request
        )[:5]
    else:
        claims = fallback_claim_hits(subject, chapter, node_ids, tokens, full_query, intent)[:8]
        evidences = fallback_evidence_hits(subject, chapter, node_ids, claims, tokens, full_query, page_anchor_request)[:5]
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
) -> tuple[str, str, list[dict]]:
    answer_mode = "canonical_claim" if claims else "accepted_evidence"
    fallback_note = ""
    fallback: list[dict] = []
    if not claims and not evidences:
        fallback = fallback_chapter_hits(vault_root, subject, chapter, tokens, full_query)[: max(topk, 2)]
        answer_mode = "chapter_fallback"
        fallback_note = "当前未命中正式主张，仅基于章节层回退。"
    elif intent in {"compare", "diagnose"} and not compare_bundle and not any(item.get("claim_type") in {"comparison", "confusion"} for item in claims):
        fallback_note = "当前缺少专门的比较/诊断主张，回答会更多依赖定义类主张和证据拼装。"
    return answer_mode, fallback_note, fallback


def query_knowledge(
    vault_root: Path,
    subject: str,
    chapter: str | None,
    query: str,
    topk: int,
    printed_page: int | None = None,
    book_title: str | None = None,
) -> dict:
    intent = detect_intent(query)
    page_anchor_request = parse_page_anchor(query)
    if printed_page is not None:
        page_anchor_request["requested_page"] = printed_page
    tokens = tokenize(query)
    full_query = normalize_text(query)
    parts = compare_parts(query) if intent == "compare" else []
    retrieval_hits, routed, claims, evidences, index_routed = _resolve_query_hits(
        subject, chapter, query, topk, intent, tokens, full_query, page_anchor_request
    )
    hard_page_route = page_anchor_request.get("requested_page") is not None
    if hard_page_route:
        page_anchor, retrieval_hits, claims, evidences = apply_hard_page_route(
            subject=subject,
            chapter=chapter,
            book_title=book_title,
            request=page_anchor_request,
            retrieval_hits=retrieval_hits,
            claims=claims,
        )
    else:
        page_anchor = build_page_anchor(evidences, page_anchor_request)
    compare_bundle = build_compare_bundle(routed, claims, evidences, parts) if intent == "compare" else None
    refine_candidates = learner_compare_candidates(subject, chapter)
    answer_mode, fallback_note, fallback = _resolve_answer_fallback(
        vault_root, subject, chapter, tokens, full_query, topk, intent, claims, evidences, compare_bundle
    )
    if hard_page_route:
        fallback = []
        status = page_anchor.get("match_status")
        if status == "exact_evidence":
            answer_mode = "accepted_evidence"
            fallback_note = ""
        elif status == "exact_asset":
            answer_mode = "page_asset"
            fallback_note = "已精确定位教材原页，但该页尚无可用的结构化 OCR 证据；请基于原图核对，不应声称逐字引用。"
        elif status == "ambiguous":
            answer_mode = "page_ambiguous"
            fallback_note = "多本教材包含该印刷页，需要先确认教材名称。"
        elif status == "unmapped":
            answer_mode = "page_unmapped"
            fallback_note = "已识别教材，但该印刷页尚未建立正式页码映射。"
        else:
            answer_mode = "page_not_found"
            fallback_note = "正式页定位索引中没有找到该印刷页。"
    page_verification = build_page_verification_summary(page_anchor, answer_mode)
    query_path = {
        "retrieval_candidate_set_used": index_routed,
        "retrieval_hit_count": len(retrieval_hits),
        "normal_path": "retrieval/search index -> candidate doc ids -> targeted json reads",
        "full_json_scan_used_for_answer": not index_routed,
        "full_json_scan_policy": "index-miss fallback only; normal indexed path is targeted reads",
        "page_locator_index_used": hard_page_route,
        "hard_page_filter_applied": hard_page_route,
    }
    teaching_context = build_bounded_teaching_context(
        load_events(),
        subject=subject,
        chapter=chapter,
        query=query,
    )

    return {
        "subject": subject,
        "chapter": chapter or "",
        "book_title": book_title or "",
        "query": query,
        "intent": intent,
        "answer_mode": answer_mode,
        "fallback_note": fallback_note,
        "syllabus_route": routed[: max(topk, 3)],
        "retrieval_hits": retrieval_hits[: max(topk, 5)],
        "claim_hits": claims,
        "evidence_hits": evidences,
        "fallback_hits": fallback,
        "references": build_reference_items(evidences, chapter),
        "learner_snapshot": learner_snapshot(subject),
        "teaching_context": teaching_context,
        "compare_bundle": compare_bundle,
        "refinement_candidates": refine_candidates[:3],
        "query_path": query_path,
        "page_anchor": page_anchor,
        "page_verification": page_verification,
    }


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
    page_anchor = dict(result.get("page_anchor") or {})
    if page_anchor.get("requested_page") is not None:
        verification = dict(result.get("page_verification") or build_page_verification_summary(page_anchor, result["answer_mode"]))
        lines.extend(
            [
                "## 页码核验摘要",
                "",
                f"- 页面定位：{verification['page_location_status']}",
                f"- 题号正文核验：{verification['exercise_verification_status']}",
                f"- 命中层：{verification['answer_mode']}",
                f"- 可否按教材正文讲解：{'可以' if verification['textbook_explanation_allowed'] else '不可以'}",
                f"- 结论：{verification['summary']}",
                f"- 教材：{page_anchor.get('book_title') or page_anchor.get('requested_book_title') or '未确定'}",
                f"- 印刷页：{page_anchor.get('requested_page')}",
            ]
        )
        if page_anchor.get("source_image_path"):
            lines.append(f"- 原图：{page_anchor['source_image_path']}")
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
            lines.append(f"- {ref['title']} | 页段 {ref['page_span']} | 图片 {ref['image_span']} | chunk {ref['chunk_id']}{section_text}")
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
    result = query_knowledge(Path(args.vault_root), subject, args.chapter, args.query or "", args.topk, args.printed_page, args.book_title)
    if args.format == "json":
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(render_text(result), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
