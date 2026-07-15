#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

from common import (
    default_vault_root_arg,
    ensure_kb_layout,
    is_owned_generated_markdown,
    load_all_json,
    load_json,
    preferred_python_executable,
    resolve_subject,
    run_utf8_subprocess,
    runtime_subprocess_env,
    sanitize_name,
    save_json,
    save_text,
    validate_entity_contract,
    wrap_generated_markdown,
)

HIGH_RISK_TYPES = {"missing_condition", "true_conflict"}
CARD_DIRNAME = "20_考点主卡"
GENERATED_TYPE = "canonical_card"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--subject", action="append", default=[])
    parser.add_argument("--vault-root", default=default_vault_root_arg())
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--yes", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--no-backup", action="store_true")
    parser.add_argument("--format", choices=("json", "quiet"), default="json")
    return parser.parse_args()


def script_path(name: str) -> Path:
    return Path(__file__).with_name(name)


def create_backup() -> str:
    completed = run_utf8_subprocess(
        [preferred_python_executable(), str(script_path("create_snapshot.py")), "--format", "json"],
        command_label="python:create_snapshot.py",
        check=True,
        env=runtime_subprocess_env(),
    )
    payload = json.loads(completed.stdout)
    return str(payload.get("snapshot_id", ""))


def dedupe_texts(items: list[str]) -> list[str]:
    seen: list[str] = []
    for item in items:
        text = str(item or "").strip()
        if text and text not in seen:
            seen.append(text)
    return seen


def cluster_registry(layout: dict[str, Path]) -> dict[str, Any]:
    path = layout["indexes"] / "claim_registry.json"
    if not path.exists():
        return {"claims": [], "clusters": []}
    return load_json(path)


def grouped_card_materials(
    layout: dict[str, Path], subjects: set[str]
) -> tuple[dict[tuple[str, str], dict[str, Any]], dict[str, dict[str, Any]]]:
    evidence_map = {item["evidence_id"]: item for item in load_all_json(layout["evidence"])}
    claim_map = {item["claim_id"]: item for item in load_all_json(layout["claims"])}
    relation_map = {
        str(item.get("relation_id") or item.get("conflict_id") or ""): item
        for item in load_all_json(layout["conflicts"])
    }
    registry = cluster_registry(layout)
    clusters = registry.get("clusters", [])

    grouped: dict[tuple[str, str], dict[str, Any]] = defaultdict(lambda: {"ready_clusters": [], "blocked_clusters": []})
    for cluster in clusters:
        subject = str(cluster.get("subject", ""))
        if subjects and subject not in subjects:
            continue
        node_id = str(cluster.get("syllabus_node_id", ""))
        claim_ids = [str(item).strip() for item in cluster.get("claim_ids", []) if str(item).strip()]
        claims = [claim_map[claim_id] for claim_id in claim_ids if claim_id in claim_map]
        if not claims:
            continue
        refs = []
        for claim in claims:
            refs.extend(evidence_map[eid] for eid in claim.get("evidence_ids", []) if eid in evidence_map)
        if not refs or not all(ref.get("source_grounded") for ref in refs):
            continue
        cluster_copy = dict(cluster)
        cluster_copy["claims"] = claims
        cluster_copy["references"] = refs
        cluster_copy["source_differences"] = [
            relation_map[relation_id]
            for relation_id in cluster.get("low_risk_relation_ids", [])
            if relation_id in relation_map
        ]
        cluster_copy["high_risk_relations"] = [
            relation_map[relation_id]
            for relation_id in cluster.get("high_risk_relation_ids", [])
            if relation_id in relation_map
        ]
        bucket = grouped[(subject, node_id)]
        if cluster.get("cluster_status") == "ready":
            bucket["ready_clusters"].append(cluster_copy)
        else:
            bucket["blocked_clusters"].append(cluster_copy)

    # Fallback for older states without clusters
    if not clusters:
        conflict_map: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for relation in load_all_json(layout["conflicts"]):
            for claim_id in relation.get("claim_ids", []):
                conflict_map[str(claim_id)].append(relation)
        for claim in load_all_json(layout["claims"]):
            subject = str(claim.get("subject", ""))
            if subjects and subject not in subjects:
                continue
            if claim.get("status") != "accepted" or not claim.get("evidence_ids"):
                continue
            refs = [evidence_map[eid] for eid in claim.get("evidence_ids", []) if eid in evidence_map]
            if not refs or not all(ref.get("source_grounded") for ref in refs):
                continue
            risks = [
                item
                for item in conflict_map.get(str(claim["claim_id"]), [])
                if item.get("relation_type") in HIGH_RISK_TYPES and item.get("status") != "resolved"
            ]
            bucket = grouped[(subject, str(claim.get("syllabus_node_id", "")))]
            cluster_copy = {
                "cluster_id": f"fallback-{claim['claim_id']}",
                "canonical_claim_id": claim["claim_id"],
                "canonical_text": claim.get("canonical_text") or claim.get("text") or "",
                "claim_ids": [claim["claim_id"]],
                "claims": [claim],
                "references": refs,
                "source_differences": [
                    item
                    for item in conflict_map.get(str(claim["claim_id"]), [])
                    if item.get("relation_type") not in HIGH_RISK_TYPES
                ],
                "high_risk_relations": risks,
            }
            if risks:
                bucket["blocked_clusters"].append(cluster_copy)
            else:
                bucket["ready_clusters"].append(cluster_copy)
    return grouped, evidence_map


def render_card(subject: str, node: dict[str, Any], card_material: dict[str, Any]) -> str:
    ready_clusters = list(card_material.get("ready_clusters", []))
    blocked_clusters = list(card_material.get("blocked_clusters", []))
    claims = [claim for cluster in ready_clusters for claim in cluster.get("claims", [])]
    evidence_map = {
        ref.get("evidence_id", ""): ref
        for cluster in ready_clusters
        for ref in cluster.get("references", [])
        if ref.get("evidence_id")
    }
    direct_conclusions = dedupe_texts(
        [cluster.get("canonical_text", "") for cluster in ready_clusters if cluster.get("canonical_text")]
        or [claim.get("text", "") for claim in claims if claim.get("claim_type") in {"definition", "rule", "comparison"}]
        or [claim.get("text", "") for claim in claims]
    )
    confusion_texts = dedupe_texts([claim.get("text", "") for claim in claims if claim.get("claim_type") == "confusion"])
    example_texts = dedupe_texts([claim.get("text", "") for claim in claims if claim.get("claim_type") == "example_type"])
    source_differences = dedupe_texts(
        [
            f"{relation.get('relation_type', '')}: {relation.get('reason', '')}".strip(": ")
            for cluster in ready_clusters
            for relation in cluster.get("source_differences", [])
        ]
    )
    blocked_summaries = dedupe_texts(
        [
            cluster.get("canonical_text", "")
            for cluster in blocked_clusters
            if cluster.get("canonical_text")
        ]
    )

    lines = [
        f"# {node['title']}",
        "",
        f"- subject: {subject}",
        f"- syllabus_node: `{node['node_id']}`",
        f"- aliases: {', '.join(node.get('aliases', [])) or 'n/a'}",
        f"- published_clusters: {len(ready_clusters)}",
        f"- blocked_clusters: {len(blocked_clusters)}",
        "",
        "## One-line Conclusion",
        "",
        f"- {direct_conclusions[0]}" if direct_conclusions else "- n/a",
        "",
        "## Strict Notes",
        "",
    ]
    for cluster in ready_clusters:
        lines.extend([f"### {cluster.get('claim_type', 'claim')}", "", f"- {cluster.get('canonical_text', '')}"])
        claim_refs = [
            evidence_map[eid]
            for claim in cluster.get("claims", [])
            for eid in claim.get("evidence_ids", [])
            if eid in evidence_map
        ]
        if claim_refs:
            lines.append("- references:")
            for ref in claim_refs[:3]:
                locator = ref.get("locator", {})
                lines.append(
                    f"  - {ref.get('title', '')} | pages {locator.get('page_start', '')}-{locator.get('page_end', '')} | images {locator.get('image_start', '')}-{locator.get('image_end', '')} | chunk {ref.get('chunk_id', '')}"
                )
        lines.append("")
    lines.extend(["## Confusions", ""])
    lines.extend([f"- {text}" for text in confusion_texts] or ["- n/a"])
    lines.extend(["", "## Example Types", ""])
    lines.extend([f"- {text}" for text in example_texts] or ["- n/a"])
    lines.extend(["", "## Source Differences", ""])
    lines.extend([f"- {text}" for text in source_differences] or ["- n/a"])
    lines.extend(["", "## Needs Review", ""])
    if blocked_summaries:
        lines.extend([f"- {text}" for text in blocked_summaries])
    else:
        lines.append("- n/a")
    return "\n".join(lines).rstrip() + "\n"


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    args = parse_args()
    execute = args.yes or args.force
    layout = ensure_kb_layout()
    vault_root = Path(args.vault_root)
    selected_subjects = {resolve_subject(item)[0] for item in args.subject} if args.subject else set()
    grouped_materials, _ = grouped_card_materials(layout, selected_subjects)

    syllabus_cache: dict[str, dict[str, Any]] = {}
    published_current: list[dict[str, Any]] = []
    planned_writes: list[dict[str, Any]] = []
    for (subject, node_id), material in sorted(grouped_materials.items()):
        ready_clusters = material.get("ready_clusters", [])
        if not ready_clusters:
            continue
        _, config = resolve_subject(subject)
        if subject not in syllabus_cache:
            syllabus_cache[subject] = load_json(layout["syllabus"] / f"{subject}.json")
        node = next((item for item in syllabus_cache[subject].get("nodes", []) if item["node_id"] == node_id), None)
        if not node:
            continue
        card_dir = vault_root / config["dir"] / CARD_DIRNAME
        card_path = card_dir / f"{sanitize_name(node['title'])}.md"
        rendered = wrap_generated_markdown(render_card(subject, node, material), GENERATED_TYPE)
        published_claim_ids = [claim["claim_id"] for cluster in ready_clusters for claim in cluster.get("claims", [])]
        published_cluster_ids = [cluster.get("cluster_id", "") for cluster in ready_clusters if cluster.get("cluster_id")]
        blocked_cluster_ids = [cluster.get("cluster_id", "") for cluster in material.get("blocked_clusters", []) if cluster.get("cluster_id")]
        planned_writes.append({"path": str(card_path), "subject": subject, "syllabus_node_id": node_id})
        published_current.append(
            {
                "subject": subject,
                "syllabus_node_id": node_id,
                "title": node["title"],
                "card_path": str(card_path),
                "claim_ids": published_claim_ids,
                "published_cluster_ids": published_cluster_ids,
                "blocked_cluster_ids": blocked_cluster_ids,
                "_rendered": rendered,
            }
        )

    for item in published_current:
        validate_entity_contract("canonical_card", item)

    index_path = layout["indexes"] / "canonical_cards.json"
    existing = load_json(index_path) if index_path.exists() else {"count": 0, "items": []}
    preserved: list[dict[str, Any]] = []
    current_keys = {(item["subject"], item["syllabus_node_id"]) for item in published_current}
    for item in existing.get("items", []):
        key = (item.get("subject", ""), item.get("syllabus_node_id", ""))
        if selected_subjects and item.get("subject", "") in selected_subjects:
            continue
        if key in current_keys:
            continue
        preserved.append(item)
    merged = preserved + [{k: v for k, v in item.items() if not k.startswith("_")} for item in published_current]
    merged.sort(key=lambda item: (item.get("subject", ""), item.get("syllabus_node_id", "")))

    planned_deletes: list[str] = []
    if selected_subjects:
        for subject in selected_subjects:
            _, config = resolve_subject(subject)
            card_dir = vault_root / config["dir"] / CARD_DIRNAME
            wanted_paths = {Path(item["card_path"]) for item in merged if item.get("subject") == subject}
            for path in sorted(card_dir.glob("*.md")) if card_dir.exists() else []:
                if path not in wanted_paths and is_owned_generated_markdown(path, GENERATED_TYPE):
                    planned_deletes.append(str(path))

    backup_snapshot_id = ""
    if execute and not args.no_backup:
        backup_snapshot_id = create_backup()
    if execute:
        for item in published_current:
            card_path = Path(item["card_path"])
            card_path.parent.mkdir(parents=True, exist_ok=True)
            save_text(card_path, str(item["_rendered"]))
        save_json(index_path, {"count": len(merged), "items": merged})
        for path in planned_deletes:
            Path(path).unlink(missing_ok=True)

    if args.format == "json":
        print(
            json.dumps(
                {
                    "executed": execute,
                    "mode": "execute" if execute else "dry-run",
                    "backup_snapshot_id": backup_snapshot_id,
                    "count": len(merged),
                    "items": merged,
                    "planned_writes": planned_writes,
                    "planned_deletes": planned_deletes,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
