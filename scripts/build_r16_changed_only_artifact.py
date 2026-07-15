#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from build_r16_run_manifest_artifact import ARTIFACT_JSON as R16_T01_ARTIFACT_JSON
from common import INDEX_DIRNAME, default_vault_root_arg, ensure_kb_layout, load_json_or_default, save_json, save_text

ARTIFACT_JSON = "22_r16_changed_only_boundary_artifact.json"
ARTIFACT_MD = "22_r16_changed_only_boundary_artifact.md"
ARTIFACT_ID = "r16-changed-only-boundary"
ARTIFACT_CONTRACT_VERSION = "r16.changed-only.v1"
POST_R16_T02_SUCCESSOR = {
    "track_id": "R16-T03",
    "title": "disaster recovery, snapshot restore, and machine-owned cleanup boundary",
    "scope": "changed-only rebuild -> recovery rehearsal -> machine-owned cleanup boundary",
    "machine_readable_entry_point": "R16-T03 -> M7-T03",
    "status": "defined_not_started",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--vault-root", default=default_vault_root_arg())
    parser.add_argument("--format", choices=("json", "quiet"), default="json")
    return parser.parse_args()


def _load_r16_t01_artifact(index_root: Path) -> dict[str, Any]:
    return load_json_or_default(
        index_root / R16_T01_ARTIFACT_JSON,
        {
            "artifact_id": "",
            "durability_intake_status": {"status": "missing-r16-t01-artifact"},
        },
    )


def _load_search_manifest() -> dict[str, Any]:
    manifest_path = ensure_kb_layout()["indexes"] / "search_manifest.json"
    return load_json_or_default(
        manifest_path,
        {
            "doc_count": 0,
            "changed_count": 0,
            "rebuild_mode": "missing-search-manifest",
            "manifest_delta": {
                "added_count": 0,
                "updated_count": 0,
                "removed_count": 0,
                "unchanged_count": 0,
                "removed_doc_ids": [],
            },
            "performance_boundary": {"status": "hard_failure", "reasons": ["missing-search-manifest"]},
        },
    )


def build_payload(index_root: Path) -> dict[str, Any]:
    t01_artifact = _load_r16_t01_artifact(index_root)
    search_manifest = _load_search_manifest()
    performance_boundary = dict(search_manifest.get("performance_boundary", {}))
    remaining_gaps: list[str] = []
    t01_ready = dict(t01_artifact.get("durability_intake_status", {})).get("status") == "ready-for-r16-t02"
    if not t01_ready:
        remaining_gaps.append("R16-T01 run-entry artifact is not ready, so changed-only boundary is not grounded.")
    rebuild_mode = str(search_manifest.get("rebuild_mode", "")).strip()
    if rebuild_mode in {"", "missing-search-manifest"}:
        remaining_gaps.append("Search-index changed-only rebuild evidence is missing.")
    perf_status = str(performance_boundary.get("status", "")).strip()
    if perf_status == "hard_failure":
        remaining_gaps.append("Search-index performance boundary has crossed the hard-failure threshold.")
    readiness_status = "ready-for-r16-t03" if not remaining_gaps else "not-ready-for-r16-t03"
    return {
        "artifact_contract_version": ARTIFACT_CONTRACT_VERSION,
        "artifact_id": ARTIFACT_ID,
        "scope": "run-manifest -> changed-only rebuild -> manifest delta -> performance warning boundary",
        "input_contract_refs": [
            {"name": "r16_t01_run_manifest_artifact", "version": t01_artifact.get("artifact_contract_version", "")},
            {"name": "search_manifest", "version": "search-index.v1"},
        ],
        "run_entry_input": {
            "artifact_id": t01_artifact.get("artifact_id", ""),
            "durability_intake_status": dict(t01_artifact.get("durability_intake_status", {})).get("status", ""),
        },
        "changed_only_rebuild_status": {
            "rebuild_mode": rebuild_mode,
            "doc_count": int(search_manifest.get("doc_count", 0)),
            "changed_count": int(search_manifest.get("changed_count", 0)),
            "manifest_delta": dict(search_manifest.get("manifest_delta", {})),
            "performance_boundary": performance_boundary,
        },
        "remaining_gaps": remaining_gaps,
        "readiness_status": readiness_status,
        "post_r16_t02_successor": POST_R16_T02_SUCCESSOR,
    }


def render_markdown(payload: dict[str, Any]) -> str:
    run_entry = dict(payload.get("run_entry_input", {}))
    rebuild = dict(payload.get("changed_only_rebuild_status", {}))
    delta = dict(rebuild.get("manifest_delta", {}))
    perf = dict(rebuild.get("performance_boundary", {}))
    successor = dict(payload.get("post_r16_t02_successor", {}))
    lines = [
        "# R16-T02 changed-only boundary artifact",
        "",
        f"- artifact_id: {payload.get('artifact_id', '')}",
        f"- readiness_status: {payload.get('readiness_status', '')}",
        f"- scope: {payload.get('scope', '')}",
        "",
        "## Run-entry input",
        "",
        f"- artifact_id: {run_entry.get('artifact_id', '')}",
        f"- durability_intake_status: {run_entry.get('durability_intake_status', '')}",
        "",
        "## Changed-only rebuild status",
        "",
        f"- rebuild_mode: {rebuild.get('rebuild_mode', '')}",
        f"- doc_count: {rebuild.get('doc_count', 0)}",
        f"- changed_count: {rebuild.get('changed_count', 0)}",
        f"- added_count: {delta.get('added_count', 0)}",
        f"- updated_count: {delta.get('updated_count', 0)}",
        f"- removed_count: {delta.get('removed_count', 0)}",
        f"- performance_status: {perf.get('status', '')}",
        "",
        "## Remaining gaps",
        "",
    ]
    if payload.get("remaining_gaps"):
        for item in payload["remaining_gaps"]:
            lines.append(f"- {item}")
    else:
        lines.append("- none")
    lines.extend(
        [
            "",
            "## Post-R16-T02 successor",
            "",
            f"- track_id: {successor.get('track_id', '')}",
            f"- title: {successor.get('title', '')}",
            f"- machine_readable_entry_point: {successor.get('machine_readable_entry_point', '')}",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    args = parse_args()
    index_root = Path(args.vault_root) / INDEX_DIRNAME
    index_root.mkdir(parents=True, exist_ok=True)
    payload = build_payload(index_root)
    save_json(index_root / ARTIFACT_JSON, payload)
    save_text(index_root / ARTIFACT_MD, render_markdown(payload))
    result = {
        "artifact_id": payload["artifact_id"],
        "readiness_status": payload["readiness_status"],
        "remaining_gaps": payload["remaining_gaps"],
        "post_r16_t02_successor": payload["post_r16_t02_successor"],
    }
    if args.format == "json":
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
