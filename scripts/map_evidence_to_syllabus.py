#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from common import ensure_kb_layout, load_all_json, load_json, normalize_context, now_iso, resolve_subject, save_json
from subject_adapters import get_subject_adapter

ACCEPT_THRESHOLD = 0.90
REVIEW_THRESHOLD = 0.70


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--subject", required=True)
    parser.add_argument("--context-json")
    parser.add_argument("--format", choices=("json", "quiet"), default="json")
    return parser.parse_args()


def norm(text: str) -> str:
    return str(text or "").strip().lower()


def score_candidate(evidence: dict, node: dict, alias_map: dict[str, list[str]], adapter) -> float:
    title = norm(evidence.get("title", ""))
    content = norm(evidence.get("content", ""))
    evidence_terms = [norm(term) for term in adapter.evidence_terms(evidence)]
    haystack = " ".join([title, content, *[term for term in evidence_terms if term]])
    score = 0.0

    title_text = norm(node.get("title", ""))
    if title_text and title_text in haystack:
        score += 0.56
        if title_text in title:
            score += 0.14

    aliases = [*alias_map.get(node["node_id"], []), *adapter.node_aliases(node)]
    alias_hits = 0
    for alias in aliases:
        alias_text = norm(alias)
        if alias_text and alias_text in haystack:
            alias_hits += 1
    if alias_hits:
        score += min(0.24, alias_hits * 0.08)

    keyword_hits = 0
    for keyword in [*node.get("keywords", []), *adapter.node_keywords(node)]:
        keyword_text = norm(keyword)
        if keyword_text and keyword_text in haystack:
            keyword_hits += 1
    if keyword_hits:
        score += min(0.24, keyword_hits * 0.06)

    if evidence.get("evidence_type") == "rule" and ("定理" in title or "公式" in title):
        score += 0.04
    if evidence.get("evidence_type") == "example" and ("题" in content or "例" in title):
        score += 0.03
    if score > 0:
        score = min(score * 1.8, 0.99)
    score = min(score + float(adapter.score_bonus(evidence, node)), 0.99)
    return round(score, 2)


def build_node_index(syllabus: dict) -> dict[str, dict]:
    return {str(node.get("node_id", "")).strip(): node for node in syllabus.get("nodes", []) if node.get("node_id")}


def node_payload(node: dict, *, confidence: float, status: str) -> dict:
    return {
        "node_id": node["node_id"],
        "title": node.get("title", ""),
        "confidence": confidence,
        "status": status,
    }


def haystack_for(evidence: dict) -> str:
    return f"{norm(evidence.get('title', ''))} {norm(evidence.get('content', ''))}"


def apply_mapping_override(evidence: dict, overrides: list[dict], node_index: dict[str, dict]) -> tuple[dict, dict] | None:
    haystack = haystack_for(evidence)
    for override in overrides:
        pattern = norm(override.get("pattern", ""))
        node_id = str(override.get("node_id", "")).strip()
        if not pattern or not node_id:
            continue
        node = node_index.get(node_id)
        if not node or pattern not in haystack:
            continue
        candidate = node_payload(node, confidence=1.0, status="accepted")
        matched_override = {
            "pattern": override.get("pattern", ""),
            "node_id": node_id,
            "reason": override.get("reason", ""),
        }
        return candidate, matched_override
    return None


def preserve_manual_review(evidence: dict) -> dict | None:
    review_status = str(evidence.get("mapping_review_status", "")).strip()
    if review_status not in {"accepted", "rejected", "unmapped"}:
        return None
    payload = {
        "syllabus_candidates": list(evidence.get("syllabus_candidates", [])),
        "accepted_syllabus_nodes": list(evidence.get("accepted_syllabus_nodes", [])),
        "mapping_status": "accepted" if review_status == "accepted" else "unmapped",
        "mapping_decision": "manual-review",
    }
    if review_status != "accepted":
        payload["accepted_syllabus_nodes"] = []
    return payload


def is_stale_evidence(evidence: dict) -> bool:
    return evidence.get("verification_status") == "stale" or evidence.get("mapping_status") == "stale"


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    args = parse_args()
    subject, _ = resolve_subject(args.subject)
    adapter = get_subject_adapter(subject)
    layout = ensure_kb_layout()
    syllabus_path = layout["syllabus"] / f"{subject}.json"
    aliases_path = layout["syllabus"] / f"{subject}.aliases.json"
    if not syllabus_path.exists():
        raise SystemExit(f"[ERROR] missing syllabus registry: {syllabus_path}")
    syllabus = load_json(syllabus_path)
    alias_payload = load_json(aliases_path) if aliases_path.exists() else {"aliases": {}}
    alias_map = alias_payload.get("aliases", {})
    overrides = alias_payload.get("mapping_overrides", syllabus.get("mapping_overrides", []))
    node_index = build_node_index(syllabus)
    context = normalize_context(load_json(Path(args.context_json))) if args.context_json else None

    evidences = []
    for evidence in load_all_json(layout["evidence"]):
        if evidence.get("subject") != subject:
            continue
        if is_stale_evidence(evidence):
            continue
        if context and evidence.get("chapter_id") != context.get("chapter_id"):
            continue
        evidences.append(evidence)

    review_items: list[dict] = []
    updated: list[dict] = []
    for evidence in evidences:
        preserved = preserve_manual_review(evidence)
        if preserved is not None:
            evidence["syllabus_candidates"] = preserved["syllabus_candidates"]
            evidence["accepted_syllabus_nodes"] = preserved["accepted_syllabus_nodes"]
            evidence["mapping_status"] = preserved["mapping_status"]
            evidence["mapping_decision"] = preserved["mapping_decision"]
            evidence["updated_at"] = now_iso()
            save_json(layout["evidence"] / f"{evidence['evidence_id']}.json", evidence)
            updated.append(
                {
                    "evidence_id": evidence["evidence_id"],
                    "mapping_status": evidence["mapping_status"],
                    "top_candidate": evidence["accepted_syllabus_nodes"][0] if evidence["accepted_syllabus_nodes"] else None,
                }
            )
            continue

        override_match = apply_mapping_override(evidence, overrides, node_index)
        if override_match is not None:
            accepted_candidate, matched_override = override_match
            evidence["syllabus_candidates"] = [accepted_candidate]
            evidence["accepted_syllabus_nodes"] = [accepted_candidate]
            evidence["mapping_status"] = "accepted"
            evidence["mapping_decision"] = "override"
            evidence["mapping_override"] = matched_override
            evidence["updated_at"] = now_iso()
            save_json(layout["evidence"] / f"{evidence['evidence_id']}.json", evidence)
            updated.append(
                {
                    "evidence_id": evidence["evidence_id"],
                    "mapping_status": evidence["mapping_status"],
                    "top_candidate": accepted_candidate,
                }
            )
            continue

        candidates = []
        for node in syllabus.get("nodes", []):
            confidence = score_candidate(evidence, node, alias_map, adapter)
            if confidence < 0.40:
                continue
            if confidence >= ACCEPT_THRESHOLD:
                status = "accepted"
            elif confidence >= REVIEW_THRESHOLD:
                status = "review"
            else:
                status = "unmapped"
            candidates.append(
                {
                    "node_id": node["node_id"],
                    "title": node["title"],
                    "confidence": confidence,
                    "status": status,
                }
            )
        candidates.sort(key=lambda item: (-item["confidence"], item["node_id"]))
        accepted = [item for item in candidates if item["status"] == "accepted"]
        review = [item for item in candidates if item["status"] == "review"]
        evidence["syllabus_candidates"] = candidates[:5]
        evidence["accepted_syllabus_nodes"] = accepted
        evidence["mapping_status"] = "accepted" if accepted else "review" if review else "unmapped"
        evidence["mapping_decision"] = "auto"
        evidence.pop("mapping_override", None)
        evidence["updated_at"] = now_iso()
        save_json(layout["evidence"] / f"{evidence['evidence_id']}.json", evidence)
        updated.append(
            {
                "evidence_id": evidence["evidence_id"],
                "mapping_status": evidence["mapping_status"],
                "top_candidate": candidates[0] if candidates else None,
            }
        )
        if review:
            review_items.append(
                {
                    "evidence_id": evidence["evidence_id"],
                    "title": evidence.get("title", ""),
                    "chapter_id": evidence.get("chapter_id", ""),
                    "top_candidates": review[:3],
                    "updated_at": now_iso(),
                }
            )
    review_payload = {
        "subject": subject,
        "updated_at": now_iso(),
        "count": len(review_items),
        "items": review_items,
    }
    save_json(layout["review_syllabus_mapping"] / f"{subject}.json", review_payload)
    if args.format == "json":
        print(json.dumps({"count": len(updated), "items": updated, "review_queue": review_payload}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
