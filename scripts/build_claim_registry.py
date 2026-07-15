#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict, deque
from typing import Any

from common import ensure_kb_layout, load_all_json, save_json, stable_fingerprint

HIGH_RISK_TYPES = {"missing_condition", "true_conflict"}
ACTIVE_CLAIM_STATUSES = {"accepted", "review"}
ACTIVE_RELATION_STATUSES = {"review", "resolved", "open"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--format", choices=("json", "quiet"), default="json")
    return parser.parse_args()


def active_claims(layout: dict[str, Any]) -> list[dict[str, Any]]:
    claims = []
    for item in load_all_json(layout["claims"]):
        if str(item.get("status", "")).strip() in {"superseded", "rejected"}:
            continue
        claims.append(item)
    claims.sort(key=lambda item: (item.get("subject", ""), item.get("syllabus_node_id", ""), item.get("claim_id", "")))
    return claims


def active_relations(layout: dict[str, Any]) -> list[dict[str, Any]]:
    relations = []
    for item in load_all_json(layout["conflicts"]):
        if str(item.get("status", "")).strip() == "superseded":
            continue
        relations.append(item)
    relations.sort(key=lambda item: (item.get("subject", ""), item.get("syllabus_node_id", ""), item.get("relation_id", item.get("conflict_id", ""))))
    return relations


def choose_canonical_claim(claims: list[dict[str, Any]]) -> dict[str, Any]:
    ranked = sorted(
        claims,
        key=lambda item: (
            -int(item.get("support_count", 0)),
            -len(item.get("variants", []) or []),
            str(item.get("claim_id", "")),
        ),
    )
    return ranked[0]


def build_cluster_id(subject: str, syllabus_node_id: str, claim_type: str, claim_ids: list[str]) -> str:
    return stable_fingerprint(
        {
            "subject": subject,
            "syllabus_node_id": syllabus_node_id,
            "claim_type": claim_type,
            "claim_ids": sorted(claim_ids),
        }
    )


def cluster_claims(claims: list[dict[str, Any]], relations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    claims_by_id = {str(item.get("claim_id", "")): item for item in claims}
    grouped_claims: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for claim in claims:
        grouped_claims[(str(claim.get("subject", "")), str(claim.get("syllabus_node_id", "")), str(claim.get("claim_type", "")))].append(claim)

    low_risk_by_group: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    high_risk_by_claim: dict[str, set[str]] = defaultdict(set)
    for relation in relations:
        relation_type = str(relation.get("relation_type") or relation.get("conflict_type") or "").strip()
        if str(relation.get("status", "")).strip() not in ACTIVE_RELATION_STATUSES:
            continue
        claim_ids = [str(item).strip() for item in relation.get("claim_ids", []) if str(item).strip()]
        if len(claim_ids) < 2:
            continue
        left_claim = claims_by_id.get(claim_ids[0])
        if not left_claim:
            continue
        group_key = (
            str(left_claim.get("subject", "")),
            str(left_claim.get("syllabus_node_id", "")),
            str(left_claim.get("claim_type", "")),
        )
        if relation_type in HIGH_RISK_TYPES:
            relation_id = str(relation.get("relation_id") or relation.get("conflict_id") or "").strip()
            for claim_id in claim_ids:
                high_risk_by_claim[claim_id].add(relation_id)
        else:
            low_risk_by_group[group_key].append(relation)

    clusters: list[dict[str, Any]] = []
    for group_key, group_claims in grouped_claims.items():
        adjacency: dict[str, set[str]] = defaultdict(set)
        for claim in group_claims:
            adjacency[str(claim["claim_id"])]
        for relation in low_risk_by_group.get(group_key, []):
            claim_ids = [str(item).strip() for item in relation.get("claim_ids", []) if str(item).strip()]
            if len(claim_ids) < 2:
                continue
            left, right = claim_ids[0], claim_ids[1]
            adjacency[left].add(right)
            adjacency[right].add(left)

        visited: set[str] = set()
        for claim in sorted(group_claims, key=lambda item: str(item.get("claim_id", ""))):
            claim_id = str(claim["claim_id"])
            if claim_id in visited:
                continue
            component: list[str] = []
            queue: deque[str] = deque([claim_id])
            visited.add(claim_id)
            while queue:
                current = queue.popleft()
                component.append(current)
                for neighbor in sorted(adjacency[current]):
                    if neighbor in visited:
                        continue
                    visited.add(neighbor)
                    queue.append(neighbor)

            component_claims = [claims_by_id[item] for item in sorted(component)]
            canonical_claim = choose_canonical_claim(component_claims)
            low_risk_relation_ids = sorted(
                {
                    str(relation.get("relation_id") or relation.get("conflict_id") or "").strip()
                    for relation in low_risk_by_group.get(group_key, [])
                    if set(str(item).strip() for item in relation.get("claim_ids", [])) <= set(component)
                }
            )
            high_risk_relation_ids = sorted(
                {
                    relation_id
                    for claim_id_member in component
                    for relation_id in high_risk_by_claim.get(claim_id_member, set())
                }
            )
            variants = sorted(
                {
                    str(variant).strip()
                    for item in component_claims
                    for variant in list(item.get("variants") or [item.get("canonical_text") or item.get("text") or ""])
                    if str(variant).strip()
                }
            )
            subject, syllabus_node_id, claim_type = group_key
            clusters.append(
                {
                    "cluster_id": build_cluster_id(subject, syllabus_node_id, claim_type, component),
                    "subject": subject,
                    "syllabus_node_id": syllabus_node_id,
                    "claim_type": claim_type,
                    "claim_ids": sorted(component),
                    "canonical_claim_id": canonical_claim["claim_id"],
                    "canonical_text": canonical_claim.get("canonical_text") or canonical_claim.get("text") or "",
                    "variants": variants,
                    "support_count": sum(int(item.get("support_count", 0)) for item in component_claims),
                    "low_risk_relation_ids": low_risk_relation_ids,
                    "high_risk_relation_ids": high_risk_relation_ids,
                    "cluster_status": "needs_review" if high_risk_relation_ids else "ready",
                }
            )

    clusters.sort(key=lambda item: (item.get("subject", ""), item.get("syllabus_node_id", ""), item.get("claim_type", ""), item.get("canonical_claim_id", "")))
    return clusters


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    args = parse_args()
    layout = ensure_kb_layout()
    claims = active_claims(layout)
    relations = active_relations(layout)
    clusters = cluster_claims(claims, relations)
    payload = {
        "count": len(claims),
        "cluster_count": len(clusters),
        "claims": claims,
        "clusters": clusters,
    }
    save_json(layout["indexes"] / "claim_registry.json", payload)
    if args.format == "json":
        print(
            json.dumps(
                {"count": len(claims), "cluster_count": len(clusters), "path": str(layout["indexes"] / "claim_registry.json")},
                ensure_ascii=False,
                indent=2,
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
