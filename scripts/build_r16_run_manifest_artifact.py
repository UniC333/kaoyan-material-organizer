#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from build_learner_acceptance_artifact import ARTIFACT_JSON as LEARNER_ACCEPTANCE_JSON
from common import INDEX_DIRNAME, default_vault_root_arg, ensure_kb_layout, load_json_or_default, save_json, save_text
from run_manager import RESUMABLE_STATUSES, RUN_INDEX_JSON, RUN_SCHEMA_VERSION

ARTIFACT_JSON = "21_r16_run_manifest_artifact.json"
ARTIFACT_MD = "21_r16_run_manifest_artifact.md"
ARTIFACT_ID = "r16-run-manifest-reset"
ARTIFACT_CONTRACT_VERSION = "r16.run-manifest.v1"
POST_R16_T01_SUCCESSOR = {
    "track_id": "R16-T02",
    "title": "changed-only rebuild and operational performance boundary",
    "scope": "run-manifest -> changed-only rebuild -> performance-warning boundary",
    "machine_readable_entry_point": "R16-T02 -> M7-T02",
    "status": "defined_not_started",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--vault-root", default=default_vault_root_arg())
    parser.add_argument("--format", choices=("json", "quiet"), default="json")
    return parser.parse_args()


def _load_learner_acceptance(index_root: Path) -> dict[str, Any]:
    path = index_root / LEARNER_ACCEPTANCE_JSON
    return load_json_or_default(
        path,
        {
            "artifact_id": "",
            "artifact_contract_version": "",
            "readiness_status": "missing_learner_acceptance_artifact",
            "fact_safety_status": {"status": "unknown"},
            "remaining_gaps": ["Learner acceptance artifact is missing."],
        },
    )


def _load_run_manifests() -> list[dict[str, Any]]:
    runs_root = ensure_kb_layout()["runs"]
    manifests: list[dict[str, Any]] = []
    for path in sorted(runs_root.glob("RUN-*.json")):
        payload = load_json_or_default(path, {})
        if str(payload.get("run_id", "")).strip():
            manifests.append(payload)
    return manifests


def _summarize_run_manifests(manifests: list[dict[str, Any]]) -> dict[str, Any]:
    resumable_runs = [item for item in manifests if str(item.get("status", "")).strip() in RESUMABLE_STATUSES]
    terminal_runs = [item for item in manifests if item not in resumable_runs]
    checkpointed_step_count = 0
    run_types: list[str] = []
    active_resume_keys: list[str] = []
    for manifest in manifests:
        run_type = str(manifest.get("run_type", "")).strip()
        if run_type and run_type not in run_types:
            run_types.append(run_type)
        resume_key = str(manifest.get("resume_key", "")).strip()
        if resume_key and str(manifest.get("status", "")).strip() in RESUMABLE_STATUSES and resume_key not in active_resume_keys:
            active_resume_keys.append(resume_key)
        for step_payload in dict(manifest.get("steps", {})).values():
            if isinstance(step_payload, dict) and step_payload.get("checkpoint") is not None:
                checkpointed_step_count += 1
    return {
        "run_schema_version": RUN_SCHEMA_VERSION,
        "total_runs": len(manifests),
        "resumable_run_count": len(resumable_runs),
        "terminal_run_count": len(terminal_runs),
        "checkpointed_step_count": checkpointed_step_count,
        "run_types": run_types,
        "active_resume_keys": active_resume_keys,
    }


def _durability_intake_status(
    *,
    learner_acceptance: dict[str, Any],
    run_summary: dict[str, Any],
    resume_index: dict[str, str],
) -> tuple[dict[str, Any], list[str]]:
    remaining_gaps: list[str] = []
    learner_ready = learner_acceptance.get("readiness_status") == "ready-for-post-r15-planning"
    fact_safe = dict(learner_acceptance.get("fact_safety_status", {})).get("status") == "fact_layer_protected"
    if not learner_ready:
        remaining_gaps.append("Learner-layer acceptance is not ready, so R16 should not treat run intake as grounded.")
    if not fact_safe:
        remaining_gaps.append("Learner-layer fact safety is not yet proven.")
    if int(run_summary.get("total_runs", 0)) <= 0:
        remaining_gaps.append("No run manifest has been exercised yet.")
    if int(run_summary.get("checkpointed_step_count", 0)) <= 0:
        remaining_gaps.append("No checkpointed run step has been captured yet.")
    if not resume_index:
        remaining_gaps.append("Resume index is empty, so resume lookup has not been exercised yet.")

    status = "ready-for-r16-t02" if not remaining_gaps else "intake-gaps-remain"
    return (
        {
            "status": status,
            "learner_layer_ready": learner_ready,
            "fact_layer_safe": fact_safe,
            "resume_index_entry_count": len(resume_index),
            "checkpoint_resume_exercised": int(run_summary.get("checkpointed_step_count", 0)) > 0,
        },
        remaining_gaps,
    )


def build_payload(index_root: Path) -> dict[str, Any]:
    learner_acceptance = _load_learner_acceptance(index_root)
    manifests = _load_run_manifests()
    run_summary = _summarize_run_manifests(manifests)
    resume_index = load_json_or_default(ensure_kb_layout()["runs"] / RUN_INDEX_JSON, {})
    durability_status, remaining_gaps = _durability_intake_status(
        learner_acceptance=learner_acceptance,
        run_summary=run_summary,
        resume_index=resume_index,
    )
    return {
        "artifact_contract_version": ARTIFACT_CONTRACT_VERSION,
        "artifact_id": ARTIFACT_ID,
        "scope": "learner-layer acceptance -> run-manifest -> checkpoint/resume -> operational durability intake",
        "input_contract_refs": [
            {"name": "r15_learner_acceptance_artifact", "version": learner_acceptance.get("artifact_contract_version", "")},
            {"name": "run_manifest_schema", "version": RUN_SCHEMA_VERSION},
        ],
        "learner_layer_input": {
            "artifact_id": learner_acceptance.get("artifact_id", ""),
            "readiness_status": learner_acceptance.get("readiness_status", ""),
            "fact_safety_status": dict(learner_acceptance.get("fact_safety_status", {})).get("status", ""),
        },
        "run_manifest_summary": run_summary,
        "durability_intake_status": durability_status,
        "remaining_gaps": remaining_gaps,
        "post_r16_t01_successor": POST_R16_T01_SUCCESSOR,
    }


def render_markdown(payload: dict[str, Any]) -> str:
    learner_input = dict(payload.get("learner_layer_input", {}))
    run_summary = dict(payload.get("run_manifest_summary", {}))
    durability = dict(payload.get("durability_intake_status", {}))
    successor = dict(payload.get("post_r16_t01_successor", {}))
    lines = [
        "# R16-T01 run-manifest artifact",
        "",
        f"- artifact_id: {payload.get('artifact_id', '')}",
        f"- scope: {payload.get('scope', '')}",
        f"- durability_status: {durability.get('status', '')}",
        "",
        "## Learner-layer input",
        "",
        f"- artifact_id: {learner_input.get('artifact_id', '')}",
        f"- readiness_status: {learner_input.get('readiness_status', '')}",
        f"- fact_safety_status: {learner_input.get('fact_safety_status', '')}",
        "",
        "## Run manifest summary",
        "",
        f"- total_runs: {run_summary.get('total_runs', 0)}",
        f"- resumable_run_count: {run_summary.get('resumable_run_count', 0)}",
        f"- terminal_run_count: {run_summary.get('terminal_run_count', 0)}",
        f"- checkpointed_step_count: {run_summary.get('checkpointed_step_count', 0)}",
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
            "## Post-R16-T01 successor",
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
        "durability_intake_status": payload["durability_intake_status"],
        "remaining_gaps": payload["remaining_gaps"],
        "post_r16_t01_successor": payload["post_r16_t01_successor"],
    }
    if args.format == "json":
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
