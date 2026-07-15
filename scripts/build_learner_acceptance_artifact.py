#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from build_learning_dashboard import FEEDBACK_CONTRACT_VERSION, FEEDBACK_SUMMARY_JSON
from build_learner_model import LEARNER_MODEL_CONTRACT_VERSION
from build_refinement_queue import REFINEMENT_CONTRACT_VERSION
from common import INDEX_DIRNAME, default_vault_root_arg, learner_file_map, load_json_or_default, save_json, save_text
from learner_events import EVENT_SCHEMA_VERSION, load_events

ARTIFACT_JSON = "20_learner_layer_acceptance_artifact.json"
ARTIFACT_MD = "20_learner_layer_acceptance_artifact.md"
ARTIFACT_ID = "r15-learner-layer-acceptance"
ARTIFACT_CONTRACT_VERSION = "r15.acceptance.v1"
POST_R15_SUCCESSOR = {
    "track_id": "R16-T01",
    "title": "run-manifest and operational durability reset",
    "scope": "learner-layer acceptance -> run-manifest -> operational durability intake",
    "machine_readable_entry_point": "R16-T01 -> M7-T01",
    "status": "defined_not_started",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--vault-root", default=default_vault_root_arg())
    parser.add_argument("--format", choices=("json", "quiet"), default="json")
    return parser.parse_args()


def _dedupe_strings(items: list[str]) -> list[str]:
    seen: list[str] = []
    for item in items:
        text = str(item or "").strip()
        if text and text not in seen:
            seen.append(text)
    return seen


def build_learner_event_summary(events: list[dict[str, Any]]) -> tuple[dict[str, Any], list[str]]:
    accepted = 0
    review_only = 0
    blocked = 0
    eligible_event_ids: list[str] = []
    for event in events:
        if event.get("event_type") != "question_saved":
            continue
        intake = dict(event.get("intake_decision") or {})
        status = str(intake.get("status", "")).strip()
        if status == "accepted":
            accepted += 1
        elif status == "review_only":
            review_only += 1
        elif status == "blocked":
            blocked += 1
        if bool(intake.get("learner_model_eligible", False)):
            event_id = str(event.get("event_id", "")).strip()
            if event_id:
                eligible_event_ids.append(event_id)
    return (
        {
            "event_schema_version": EVENT_SCHEMA_VERSION,
            "total_question_saved_events": accepted + review_only + blocked,
            "accepted_count": accepted,
            "review_only_count": review_only,
            "blocked_count": blocked,
            "learner_model_eligible_event_count": len(eligible_event_ids),
            "learner_model_eligible_event_ids": eligible_event_ids,
        },
        eligible_event_ids,
    )


def build_rebuildability_status(
    learner_model: dict[str, Any],
    question_history: dict[str, Any],
    refinement_payload: dict[str, Any],
    eligible_event_ids: list[str],
) -> dict[str, Any]:
    learner_model_event_ids = [str(item).strip() for item in learner_model.get("derived_from_event_ids", []) if str(item).strip()]
    question_history_items = list(question_history.get("items", []))
    refinement_source_count = int(refinement_payload.get("derived_from_question_history_count", 0))
    expected_count = len(eligible_event_ids)
    counts_match = (
        int(learner_model.get("derived_from_event_count", 0)) == expected_count
        and len(learner_model_event_ids) == expected_count
        and len(question_history_items) == expected_count
        and refinement_source_count == expected_count
    )
    ids_match = sorted(learner_model_event_ids) == sorted(eligible_event_ids)
    status = "rebuildable_from_append_only_events" if counts_match and ids_match else "drift_detected"
    return {
        "status": status,
        "learner_model_contract_version": str(
            learner_model.get("derivation_contract_version", LEARNER_MODEL_CONTRACT_VERSION)
        ).strip(),
        "refinement_contract_version": str(
            refinement_payload.get("refinement_contract_version", REFINEMENT_CONTRACT_VERSION)
        ).strip(),
        "learner_model_event_count": int(learner_model.get("derived_from_event_count", 0)),
        "question_history_count": len(question_history_items),
        "refinement_source_count": refinement_source_count,
        "refinement_queue_count": len(list(refinement_payload.get("items", []))),
        "eligible_event_count": expected_count,
        "derived_from_event_ids_match": ids_match,
    }


def build_fact_safety_status(events: list[dict[str, Any]], feedback_summary: dict[str, Any]) -> dict[str, Any]:
    event_fact_write_allowed_count = 0
    for event in events:
        intake = dict(event.get("intake_decision") or {})
        if bool(intake.get("fact_write_allowed", False)):
            event_fact_write_allowed_count += 1
    feedback_fact_write_allowed = bool(feedback_summary.get("fact_writeback_allowed", False))
    status = "fact_layer_protected" if event_fact_write_allowed_count == 0 and not feedback_fact_write_allowed else "unsafe_writeback_detected"
    return {
        "status": status,
        "event_fact_write_allowed_count": event_fact_write_allowed_count,
        "feedback_fact_writeback_allowed": feedback_fact_write_allowed,
    }


def build_remaining_gaps(
    *,
    learner_event_summary: dict[str, Any],
    rebuildability_status: dict[str, Any],
    feedback_summary: dict[str, Any],
    refinement_payload: dict[str, Any],
) -> list[str]:
    gaps: list[str] = []
    if int(learner_event_summary.get("total_question_saved_events", 0)) <= 0:
        gaps.append("No learner events have been captured yet.")
    if rebuildability_status.get("status") != "rebuildable_from_append_only_events":
        gaps.append("Learner model or refinement queue drifted from append-only event rebuildability.")
    if not list(feedback_summary.get("learner_facing_summary", [])):
        gaps.append("Learner-facing feedback summary is still empty.")
    open_refinement_items = [item for item in refinement_payload.get("items", []) if str(item.get("status", "")).strip() in {"open", "accepted"}]
    if open_refinement_items:
        gaps.append("Review-only refinement items still require learner-layer follow-up before successor stages consume them.")
    return gaps


def build_payload(
    *,
    events: list[dict[str, Any]],
    learner_model: dict[str, Any],
    question_history: dict[str, Any],
    refinement_payload: dict[str, Any],
    feedback_summary: dict[str, Any],
) -> dict[str, Any]:
    learner_event_summary, eligible_event_ids = build_learner_event_summary(events)
    rebuildability_status = build_rebuildability_status(
        learner_model,
        question_history,
        refinement_payload,
        eligible_event_ids,
    )
    fact_safety_status = build_fact_safety_status(events, feedback_summary)
    remaining_gaps = build_remaining_gaps(
        learner_event_summary=learner_event_summary,
        rebuildability_status=rebuildability_status,
        feedback_summary=feedback_summary,
        refinement_payload=refinement_payload,
    )
    readiness_status = (
        "ready-for-post-r15-planning"
        if learner_event_summary["total_question_saved_events"] > 0
        and rebuildability_status["status"] == "rebuildable_from_append_only_events"
        and fact_safety_status["status"] == "fact_layer_protected"
        else "not-ready-for-post-r15-planning"
    )
    return {
        "artifact_contract_version": ARTIFACT_CONTRACT_VERSION,
        "artifact_id": ARTIFACT_ID,
        "scope": "learner-event intake -> learner derivation -> refinement governance -> learner-facing feedback boundary",
        "input_contract_refs": [
            {"name": "learner_event_schema", "version": EVENT_SCHEMA_VERSION},
            {"name": "learner_model_derivation", "version": learner_model.get("derivation_contract_version", LEARNER_MODEL_CONTRACT_VERSION)},
            {"name": "refinement_queue", "version": refinement_payload.get("refinement_contract_version", REFINEMENT_CONTRACT_VERSION)},
            {"name": "learner_feedback_summary", "version": feedback_summary.get("feedback_contract_version", FEEDBACK_CONTRACT_VERSION)},
        ],
        "learner_event_summary": learner_event_summary,
        "rebuildability_status": rebuildability_status,
        "fact_safety_status": fact_safety_status,
        "remaining_gaps": remaining_gaps,
        "readiness_status": readiness_status,
        "post_r15_successor": POST_R15_SUCCESSOR,
    }


def render_markdown(payload: dict[str, Any]) -> str:
    learner_event_summary = dict(payload.get("learner_event_summary", {}))
    rebuildability_status = dict(payload.get("rebuildability_status", {}))
    fact_safety_status = dict(payload.get("fact_safety_status", {}))
    successor = dict(payload.get("post_r15_successor", {}))
    lines = [
        "# R15 learner-layer acceptance artifact",
        "",
        f"- artifact_id: {payload.get('artifact_id', '')}",
        f"- readiness_status: {payload.get('readiness_status', '')}",
        f"- scope: {payload.get('scope', '')}",
        "",
        "## Input contract refs",
        "",
    ]
    for item in payload.get("input_contract_refs", []):
        lines.append(f"- {item.get('name', '')}: {item.get('version', '')}")
    lines.extend(
        [
            "",
            "## Learner event summary",
            "",
            f"- total_question_saved_events: {learner_event_summary.get('total_question_saved_events', 0)}",
            f"- accepted_count: {learner_event_summary.get('accepted_count', 0)}",
            f"- review_only_count: {learner_event_summary.get('review_only_count', 0)}",
            f"- blocked_count: {learner_event_summary.get('blocked_count', 0)}",
            "",
            "## Rebuildability status",
            "",
            f"- status: {rebuildability_status.get('status', '')}",
            f"- eligible_event_count: {rebuildability_status.get('eligible_event_count', 0)}",
            f"- learner_model_event_count: {rebuildability_status.get('learner_model_event_count', 0)}",
            f"- question_history_count: {rebuildability_status.get('question_history_count', 0)}",
            f"- refinement_source_count: {rebuildability_status.get('refinement_source_count', 0)}",
            "",
            "## Fact safety status",
            "",
            f"- status: {fact_safety_status.get('status', '')}",
            f"- event_fact_write_allowed_count: {fact_safety_status.get('event_fact_write_allowed_count', 0)}",
            f"- feedback_fact_writeback_allowed: {str(fact_safety_status.get('feedback_fact_writeback_allowed', False)).lower()}",
            "",
            "## Remaining gaps",
            "",
        ]
    )
    if payload.get("remaining_gaps"):
        for item in payload["remaining_gaps"]:
            lines.append(f"- {item}")
    else:
        lines.append("- none")
    lines.extend(
        [
            "",
            "## Post-R15 successor",
            "",
            f"- track_id: {successor.get('track_id', '')}",
            f"- title: {successor.get('title', '')}",
            f"- scope: {successor.get('scope', '')}",
            f"- machine_readable_entry_point: {successor.get('machine_readable_entry_point', '')}",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    args = parse_args()
    files = learner_file_map()
    index_root = Path(args.vault_root) / INDEX_DIRNAME
    index_root.mkdir(parents=True, exist_ok=True)

    events = load_events()
    learner_model = load_json_or_default(files["learner_model"], {})
    question_history = load_json_or_default(files["question_history"], {"items": []})
    refinement_payload = load_json_or_default(files["refinement_queue"], {"items": []})
    feedback_summary = load_json_or_default(index_root / FEEDBACK_SUMMARY_JSON, {})

    payload = build_payload(
        events=events,
        learner_model=learner_model,
        question_history=question_history,
        refinement_payload=refinement_payload,
        feedback_summary=feedback_summary,
    )
    save_json(index_root / ARTIFACT_JSON, payload)
    save_text(index_root / ARTIFACT_MD, render_markdown(payload))

    result = {
        "artifact_id": payload["artifact_id"],
        "readiness_status": payload["readiness_status"],
        "remaining_gaps": payload["remaining_gaps"],
        "post_r15_successor": payload["post_r15_successor"],
    }
    if args.format == "json":
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
