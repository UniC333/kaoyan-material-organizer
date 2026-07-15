#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from build_r16_changed_only_artifact import ARTIFACT_JSON as R16_T02_ARTIFACT_JSON
from common import INDEX_DIRNAME, default_vault_root_arg, ensure_kb_layout, load_json_or_default, save_json, save_text
from config import load_runtime_config

ARTIFACT_JSON = "23_r16_recovery_boundary_artifact.json"
ARTIFACT_MD = "23_r16_recovery_boundary_artifact.md"
ARTIFACT_ID = "r16-recovery-boundary"
ARTIFACT_CONTRACT_VERSION = "r16.recovery.v1"
POST_R16_T03_SUCCESSOR = {
    "track_id": "R16-T04",
    "title": "in-scope real-material gray release and release-scope boundary",
    "scope": "recovery boundary -> gray release -> release-scope reporting",
    "machine_readable_entry_point": "R16-T04 -> M7-T04",
    "status": "defined_not_started",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--vault-root", default=default_vault_root_arg())
    parser.add_argument("--format", choices=("json", "quiet"), default="json")
    return parser.parse_args()


def _load_r16_t02_artifact(index_root: Path) -> dict[str, Any]:
    return load_json_or_default(
        index_root / R16_T02_ARTIFACT_JSON,
        {
            "artifact_id": "",
            "readiness_status": "missing-r16-t02-artifact",
        },
    )


def _load_latest_restore_summary() -> dict[str, Any]:
    path = load_runtime_config().backup_root / "snapshots" / "recovery" / "latest_restore_summary.json"
    return load_json_or_default(
        path,
        {
            "restored": False,
            "recovery_status": "missing-restore-summary",
            "cleanup_summary": {
                "machine_owned_pruned_count": 0,
                "human_owned_protected_count": 0,
                "pruned_relative_paths": [],
                "protected_relative_paths": [],
            },
            "resume_boundary": {"resume_only_checkpoint_available": False, "checkpoint_relative_paths": []},
        },
    )


def build_payload(index_root: Path) -> dict[str, Any]:
    t02_artifact = _load_r16_t02_artifact(index_root)
    restore_summary = _load_latest_restore_summary()
    cleanup_summary = dict(restore_summary.get("cleanup_summary", {}))
    resume_boundary = dict(restore_summary.get("resume_boundary", {}))
    remaining_gaps: list[str] = []
    t02_ready = t02_artifact.get("readiness_status") == "ready-for-r16-t03"
    if not t02_ready:
        remaining_gaps.append("R16-T02 changed-only artifact is not ready, so recovery rehearsal is not grounded.")
    if not bool(restore_summary.get("restored", False)):
        remaining_gaps.append("Latest restore summary does not prove a successful restore path.")
    if not bool(resume_boundary.get("resume_only_checkpoint_available", False)):
        remaining_gaps.append("Resume-only checkpoint boundary has not been exercised yet.")
    if int(cleanup_summary.get("machine_owned_pruned_count", 0)) <= 0:
        remaining_gaps.append("No machine-owned cleanup candidate has been pruned yet.")
    readiness_status = "ready-for-r16-t04" if not remaining_gaps else "not-ready-for-r16-t04"
    return {
        "artifact_contract_version": ARTIFACT_CONTRACT_VERSION,
        "artifact_id": ARTIFACT_ID,
        "scope": "snapshot restore -> checkpoint resume -> machine-owned cleanup -> human-owned protection boundary",
        "input_contract_refs": [
            {"name": "r16_t02_changed_only_artifact", "version": t02_artifact.get("artifact_contract_version", "")},
            {"name": "latest_restore_summary", "version": "snapshot-restore.v1"},
        ],
        "changed_only_input": {
            "artifact_id": t02_artifact.get("artifact_id", ""),
            "readiness_status": t02_artifact.get("readiness_status", ""),
        },
        "recovery_boundary_status": {
            "latest_restore_status": restore_summary.get("recovery_status", ""),
            "restored": bool(restore_summary.get("restored", False)),
            "resume_only_checkpoint_available": bool(resume_boundary.get("resume_only_checkpoint_available", False)),
            "checkpoint_relative_paths": list(resume_boundary.get("checkpoint_relative_paths", [])),
            "machine_owned_pruned_count": int(cleanup_summary.get("machine_owned_pruned_count", 0)),
            "human_owned_protected_count": int(cleanup_summary.get("human_owned_protected_count", 0)),
            "pruned_relative_paths": list(cleanup_summary.get("pruned_relative_paths", [])),
            "protected_relative_paths": list(cleanup_summary.get("protected_relative_paths", [])),
        },
        "remaining_gaps": remaining_gaps,
        "readiness_status": readiness_status,
        "post_r16_t03_successor": POST_R16_T03_SUCCESSOR,
    }


def render_markdown(payload: dict[str, Any]) -> str:
    changed_only = dict(payload.get("changed_only_input", {}))
    recovery = dict(payload.get("recovery_boundary_status", {}))
    successor = dict(payload.get("post_r16_t03_successor", {}))
    lines = [
        "# R16-T03 recovery boundary artifact",
        "",
        f"- artifact_id: {payload.get('artifact_id', '')}",
        f"- readiness_status: {payload.get('readiness_status', '')}",
        f"- scope: {payload.get('scope', '')}",
        "",
        "## Changed-only input",
        "",
        f"- artifact_id: {changed_only.get('artifact_id', '')}",
        f"- readiness_status: {changed_only.get('readiness_status', '')}",
        "",
        "## Recovery boundary status",
        "",
        f"- latest_restore_status: {recovery.get('latest_restore_status', '')}",
        f"- resume_only_checkpoint_available: {str(recovery.get('resume_only_checkpoint_available', False)).lower()}",
        f"- machine_owned_pruned_count: {recovery.get('machine_owned_pruned_count', 0)}",
        f"- human_owned_protected_count: {recovery.get('human_owned_protected_count', 0)}",
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
            "## Post-R16-T03 successor",
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
        "post_r16_t03_successor": payload["post_r16_t03_successor"],
    }
    if args.format == "json":
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
