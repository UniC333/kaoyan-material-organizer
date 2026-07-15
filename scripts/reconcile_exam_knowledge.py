#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import defaultdict
from itertools import combinations
from typing import Any

from claim_relations import classify_claim_relation
from common import (
    build_claim_key,
    build_relation_key,
    ensure_kb_layout,
    load_all_json,
    merge_manual_resolution,
    now_iso,
    save_json,
    stable_fingerprint,
    subject_id_code,
    validate_entity_contract,
)

HIGH_RISK_TYPES = {"missing_condition", "true_conflict"}
EXPLICIT_PREFIX_TO_TYPE = {
    "概念": "definition",
    "规则": "rule",
    "题型": "example_type",
    "易混点": "confusion",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--subject", action="append", default=[])
    parser.add_argument("--format", choices=("json", "quiet"), default="json")
    return parser.parse_args()


def extract_lines(evidence: dict[str, Any]) -> list[str]:
    return [line.strip() for line in str(evidence.get("content", "")).splitlines() if line.strip()]


def parse_explicit_entries(evidence: dict[str, Any]) -> list[dict[str, str]]:
    entries: list[dict[str, str]] = []
    for line in extract_lines(evidence):
        match = re.match(r"^(概念|规则|题型|易混点)\s*[：:]\s*(.+)$", line)
        if not match:
            continue
        prefix = match.group(1)
        text = match.group(2).strip()
        if not text:
            continue
        entries.append({"claim_type": EXPLICIT_PREFIX_TO_TYPE[prefix], "text": text})
    return entries


def split_named_text(text: str) -> tuple[str, str]:
    parts = str(text or "").split(" ", 1)
    if len(parts) == 2:
        return parts[0].strip(), parts[1].strip()
    return str(text or "").strip(), str(text or "").strip()


def should_keep_comparison(left_name: str, right_name: str, left_body: str, right_body: str) -> bool:
    if not left_name or not right_name or left_name == right_name:
        return False
    if len(left_name) > 18 or len(right_name) > 18:
        return False
    if left_body == right_body:
        return False
    return True


def build_comparison_claims(explicit_entries: list[dict[str, str]]) -> list[str]:
    comparison_claims: list[str] = []
    definition_entries = [item["text"] for item in explicit_entries if item["claim_type"] == "definition"]
    if len(definition_entries) < 2:
        return comparison_claims
    left_name, left_body = split_named_text(definition_entries[0])
    right_name, right_body = split_named_text(definition_entries[1])
    if not should_keep_comparison(left_name, right_name, left_body, right_body):
        return comparison_claims
    comparison_claims.append(
        f"{left_name}侧重{left_body}；{right_name}侧重{right_body}。两者回答的不是同一层面的问法。"
    )
    return comparison_claims


def deterministic_kb_id(kind: str, subject: str, payload: dict[str, Any]) -> str:
    code = subject_id_code(subject)
    digest = stable_fingerprint(payload)
    number = int(hashlib.sha256(digest.encode("utf-8")).hexdigest()[:12], 16) % 1_000_000
    if kind == "claim":
        return f"CLAIM-{code}-{number:06d}"
    if kind == "conflict":
        return f"CONFLICT-{code}-{number:06d}"
    raise ValueError(f"unsupported deterministic id kind: {kind}")


def claim_identity_payload(subject: str, syllabus_node_id: str, claim_type: str, text: str) -> dict[str, Any]:
    return {
        "subject": subject,
        "syllabus_node_id": syllabus_node_id,
        "claim_type": claim_type,
        "canonical_text": text,
    }


def conflict_identity_payload(subject: str, syllabus_node_id: str, relation_type: str, claim_ids: list[str]) -> dict[str, Any]:
    return {
        "subject": subject,
        "syllabus_node_id": syllabus_node_id,
        "relation_type": relation_type,
        "claim_ids": sorted(claim_ids),
    }


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    args = parse_args()
    layout = ensure_kb_layout()
    subjects = list(dict.fromkeys(args.subject or sorted({item.get("subject", "") for item in load_all_json(layout["evidence"])})))
    evidence_items = load_all_json(layout["evidence"])

    existing_claims: dict[str, dict[str, Any]] = {}
    existing_conflicts: dict[str, dict[str, Any]] = {}
    for path in sorted(layout["claims"].glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("subject") not in subjects:
            continue
        payload["_path"] = path
        claim_key = str(payload.get("claim_key") or build_claim_key(payload))
        payload["claim_key"] = claim_key
        existing_claims[claim_key] = payload
    for path in sorted(layout["conflicts"].glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("subject") not in subjects:
            continue
        payload["_path"] = path
        relation_key = str(payload.get("relation_key") or build_relation_key(payload))
        payload["relation_key"] = relation_key
        existing_conflicts[relation_key] = payload

    grouped: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for evidence in evidence_items:
        subject = evidence.get("subject", "")
        if subject not in subjects:
            continue
        if evidence.get("mapping_status") != "accepted":
            continue
        if not evidence.get("source_grounded"):
            continue
        explicit_entries = parse_explicit_entries(evidence)
        if not explicit_entries:
            continue
        accepted_nodes = evidence.get("accepted_syllabus_nodes", [])
        for node in accepted_nodes:
            node_id = node["node_id"]
            for entry in explicit_entries:
                grouped[(subject, node_id, entry["claim_type"])].append(
                    {
                        "text": entry["text"],
                        "evidence_id": evidence["evidence_id"],
                        "origin": "explicit_entry",
                    }
                )
            for comparison_text in build_comparison_claims(explicit_entries):
                grouped[(subject, node_id, "comparison")].append(
                    {
                        "text": comparison_text,
                        "evidence_id": evidence["evidence_id"],
                        "origin": "comparison_synthesis",
                    }
                )

    written_claims: list[dict[str, Any]] = []
    written_conflicts: list[dict[str, Any]] = []
    desired_claim_keys: set[str] = set()
    desired_relation_keys: set[str] = set()

    for (subject, syllabus_node_id, claim_type), items in grouped.items():
        buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for item in items:
            claim_key = build_claim_key(claim_identity_payload(subject, syllabus_node_id, claim_type, item["text"]))
            buckets[claim_key].append(item)

        node_claims: list[dict[str, Any]] = []
        for claim_key, bucket in buckets.items():
            existing_claim = existing_claims.get(claim_key, {})
            claim_id = str(existing_claim.get("claim_id") or "").strip() or deterministic_kb_id(
                "claim",
                subject,
                {"claim_key": claim_key},
            )
            evidence_ids = sorted({item["evidence_id"] for item in bucket})
            canonical_text = bucket[0]["text"]
            claim = {
                "claim_id": claim_id,
                "claim_key": claim_key,
                "concept_id": syllabus_node_id,
                "subject": subject,
                "syllabus_node_id": syllabus_node_id,
                "claim_type": claim_type,
                "text": canonical_text,
                "canonical_text": canonical_text,
                "variants": sorted({item["text"] for item in bucket}),
                "status": "accepted",
                "evidence_ids": evidence_ids,
                "support_count": len(evidence_ids),
                "contradiction_ids": [],
                "origin": bucket[0]["origin"],
                "created_at": existing_claim.get("created_at") or now_iso(),
                "updated_at": now_iso(),
            }
            node_claims.append(claim)
            desired_claim_keys.add(claim_key)

        claim_relation_ids: dict[str, list[str]] = defaultdict(list)
        for left_claim, right_claim in combinations(sorted(node_claims, key=lambda item: item["claim_id"]), 2):
            relation_meta = classify_claim_relation(left_claim, right_claim)
            relation_type = relation_meta["relation_type"]
            claim_ids = sorted([left_claim["claim_id"], right_claim["claim_id"]])
            relation_key = build_relation_key(
                conflict_identity_payload(subject, syllabus_node_id, relation_type, claim_ids)
            )
            existing_conflict = existing_conflicts.get(relation_key, {})
            conflict_id = str(existing_conflict.get("conflict_id") or existing_conflict.get("relation_id") or "").strip() or deterministic_kb_id(
                "conflict",
                subject,
                {"relation_key": relation_key},
            )
            conflict = {
                "conflict_id": conflict_id,
                "relation_id": conflict_id,
                "relation_key": relation_key,
                "subject": subject,
                "syllabus_node_id": syllabus_node_id,
                "conflict_type": relation_type,
                "relation_type": relation_type,
                "claim_ids": claim_ids,
                "left_claim_id": claim_ids[0],
                "right_claim_id": claim_ids[1],
                "status": "open" if relation_type in HIGH_RISK_TYPES else "review",
                "resolution": {},
                "risk_level": relation_meta["risk_level"],
                "reason": relation_meta["reason"],
                "overlap_ratio": relation_meta["overlap_ratio"],
                "created_at": existing_conflict.get("created_at") or now_iso(),
                "updated_at": now_iso(),
            }
            conflict = merge_manual_resolution(existing_conflict, conflict)
            save_json(layout["conflicts"] / f"{conflict_id}.json", conflict)
            written_conflicts.append(conflict)
            desired_relation_keys.add(relation_key)
            claim_relation_ids[left_claim["claim_id"]].append(conflict_id)
            claim_relation_ids[right_claim["claim_id"]].append(conflict_id)

        for claim in node_claims:
            claim["contradiction_ids"] = sorted(claim_relation_ids.get(claim["claim_id"], []))
            validate_entity_contract("claim", claim)
            save_json(layout["claims"] / f"{claim['claim_id']}.json", claim)
            written_claims.append(claim)

    for claim_key, existing_claim in existing_claims.items():
        claim_id = str(existing_claim.get("claim_id") or "").strip()
        if not claim_id or claim_key in desired_claim_keys:
            continue
        payload = dict(existing_claim)
        payload.pop("_path", None)
        payload["status"] = "superseded"
        payload["updated_at"] = now_iso()
        validate_entity_contract("claim", payload)
        save_json(layout["claims"] / f"{claim_id}.json", payload)

    for relation_key, existing_conflict in existing_conflicts.items():
        conflict_id = str(existing_conflict.get("conflict_id") or existing_conflict.get("relation_id") or "").strip()
        if not conflict_id or relation_key in desired_relation_keys:
            continue
        payload = dict(existing_conflict)
        payload.pop("_path", None)
        if payload.get("status") != "resolved":
            payload["status"] = "superseded"
        payload["updated_at"] = now_iso()
        save_json(layout["conflicts"] / f"{conflict_id}.json", payload)

    if args.format == "json":
        print(
            json.dumps(
                {
                    "subjects": subjects,
                    "claim_count": len(written_claims),
                    "conflict_count": len(written_conflicts),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
