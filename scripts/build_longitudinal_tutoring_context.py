#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from build_adaptive_coaching_context import ARTIFACT_JSON as ADAPTIVE_CONTEXT_JSON
from build_closed_loop_operations import ARTIFACT_JSON as CLOSED_LOOP_JSON
from build_coaching_feedback_loop import ARTIFACT_JSON as FEEDBACK_LOOP_JSON
from build_r17_teacher_loop_artifact import ARTIFACT_JSON as TEACHER_LOOP_JSON
from build_r18_adaptive_coaching_artifact import ARTIFACT_JSON as ADAPTIVE_ACCEPTANCE_JSON
from common import INDEX_DIRNAME, default_vault_root_arg, load_json_or_default, save_json, save_text

ARTIFACT_JSON = "36_r19_longitudinal_tutoring_context.json"
ARTIFACT_MD = "36_r19_longitudinal_tutoring_context.md"
ARTIFACT_ID = "r19-longitudinal-tutoring-context"
ARTIFACT_CONTRACT_VERSION = "r19.longitudinal-tutoring-context.v1"
POST_R19_T02_SUCCESSOR = {
    "track_id": "R19-T03",
    "title": "tutoring strategy packet and phase-priority packaging boundary",
    "scope": "longitudinal tutoring context -> tutoring strategy packet -> phase priority",
    "machine_readable_entry_point": "R19-T03 -> M10-T03",
    "status": "defined_not_started",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--vault-root", default=default_vault_root_arg())
    parser.add_argument("--plan-date", required=True)
    parser.add_argument("--stale-cycle-days", type=int, default=14)
    parser.add_argument("--format", choices=("json", "quiet"), default="json")
    return parser.parse_args()


def _load_artifact(index_root: Path, filename: str) -> dict[str, Any]:
    payload = load_json_or_default(index_root / filename, {})
    if not payload:
        raise SystemExit(f"missing required artifact: {filename}")
    return payload


def _dedupe_strings(items: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for item in items:
        text = str(item).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def _question_map(items: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for item in items:
        question = str(item.get("question", "")).strip()
        if question:
            result[question] = dict(item)
    return result


def _goal_adaptation_status(intervention_eligibility: str) -> str:
    mapping = {
        "eligible": "eligible",
        "stale": "stale_cycle",
        "review_only": "review_only",
        "blocked": "blocked",
        "out_of_scope": "out_of_scope",
    }
    return mapping.get(intervention_eligibility, "blocked")


def _drift_signal_type(goal_status: str, feedback_item: dict[str, Any] | None, closed_loop_item: dict[str, Any] | None) -> str:
    if goal_status == "eligible":
        result = str((feedback_item or {}).get("result", "")).strip()
        if result == "wrong":
            return "needs_retry_after_formal_feedback"
        if result == "partial":
            return "partial_mastery_requires_goal_followup"
        if closed_loop_item:
            return "auto_adjustable_progress_signal"
        return "eligible_long_horizon_signal"
    if goal_status == "stale_cycle":
        return "stale_cycle_guard"
    if goal_status == "review_only":
        return "review_only_guard"
    if goal_status == "out_of_scope":
        return "scope_filter_guard"
    return "blocked_goal_adjustment_guard"


def _source_refs(
    intervention_item: dict[str, Any],
    feedback_item: dict[str, Any] | None,
    closed_loop_item: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = [dict(ref) for ref in list(intervention_item.get("source_refs", []))]
    if feedback_item:
        refs.extend(dict(ref) for ref in list(feedback_item.get("source_refs", [])))
    if closed_loop_item:
        refs.extend(dict(ref) for ref in list(closed_loop_item.get("source_refs", [])))

    deduped: list[dict[str, Any]] = []
    seen: set[str] = set()
    for ref in refs:
        marker = json.dumps(ref, ensure_ascii=False, sort_keys=True)
        if marker in seen:
            continue
        seen.add(marker)
        deduped.append(ref)
    return deduped


def build_payload(index_root: Path, *, plan_date: str, stale_cycle_days: int) -> dict[str, Any]:
    teacher_loop = _load_artifact(index_root, TEACHER_LOOP_JSON)
    adaptive_context = _load_artifact(index_root, ADAPTIVE_CONTEXT_JSON)
    feedback_loop = _load_artifact(index_root, FEEDBACK_LOOP_JSON)
    closed_loop = _load_artifact(index_root, CLOSED_LOOP_JSON)
    adaptive_acceptance = _load_artifact(index_root, ADAPTIVE_ACCEPTANCE_JSON)

    feedback_by_question = _question_map(
        list(feedback_loop.get("formal_feedback_intake", []))
        + list(feedback_loop.get("review_only_feedback", []))
        + list(feedback_loop.get("out_of_scope_feedback", []))
        + list(feedback_loop.get("blocked_follow_ups", []))
    )
    closed_loop_by_question = _question_map(
        list(closed_loop.get("auto_adjustable_operations", []))
        + list(closed_loop.get("operator_overrides", []))
        + list(closed_loop.get("locked_manual_edits", []))
        + list(closed_loop.get("scope_blocked_operations", []))
    )

    goal_adjustment_inputs: list[dict[str, Any]] = []
    summary = {
        "eligible_count": 0,
        "stale_cycle_count": 0,
        "review_only_count": 0,
        "blocked_count": 0,
        "out_of_scope_count": 0,
    }

    for item in list(adaptive_context.get("adaptive_context", {}).get("intervention_inputs", [])):
        question = str(item.get("question", "")).strip()
        intervention_eligibility = str(item.get("intervention_eligibility", "")).strip()
        goal_status = _goal_adaptation_status(intervention_eligibility)
        feedback_item = feedback_by_question.get(question)
        closed_loop_item = closed_loop_by_question.get(question)
        entry = {
            "question": question,
            "subject": item.get("subject", ""),
            "chapter_title": item.get("chapter_title", ""),
            "goal_adaptation_eligibility": goal_status,
            "eligibility_reason": str(item.get("eligibility_reason", "")).strip(),
            "priority_reason": str(item.get("priority_reason", "")).strip(),
            "drift_signal_type": _drift_signal_type(goal_status, feedback_item, closed_loop_item),
            "feedback_intake_status": str((feedback_item or {}).get("feedback_intake_status", "")).strip(),
            "closed_loop_adjustment": dict((closed_loop_item or {}).get("cadence_adjustment", {})),
            "source_refs": _source_refs(item, feedback_item, closed_loop_item),
            "intervention_refs": list((feedback_item or {}).get("intervention_refs", [])),
        }
        goal_adjustment_inputs.append(entry)
        summary[f"{goal_status}_count"] += 1

    inherited_gaps = _dedupe_strings(
        [str(item) for item in list(teacher_loop.get("remaining_gaps", []))]
        + [str(item) for item in list(adaptive_context.get("remaining_gaps", []))]
        + [str(item) for item in list(feedback_loop.get("remaining_gaps", []))]
        + [str(item) for item in list(closed_loop.get("remaining_gaps", []))]
        + [str(item) for item in list(adaptive_acceptance.get("remaining_gaps", []))]
    )
    remaining_gaps = inherited_gaps + [
        "Longitudinal tutoring context only defines long-horizon inputs and eligibility; strategy packaging, multi-cycle feedback, and goal-adjustment governance still belong to later R19 tasks.",
    ]

    long_horizon_profile = {
        "current_stage": "longitudinal_tutoring",
        "long_horizon_window_days": max(1, stale_cycle_days),
        "teacher_loop_readiness_status": teacher_loop.get("readiness_status", ""),
        "adaptive_readiness_status": adaptive_acceptance.get("readiness_status", ""),
        "eligible_goal_adjustment_count": summary["eligible_count"],
        "stale_cycle_count": summary["stale_cycle_count"],
        "review_only_count": summary["review_only_count"],
        "blocked_count": summary["blocked_count"],
        "out_of_scope_count": summary["out_of_scope_count"],
    }

    readiness_status = (
        "ready-for-r19-t03"
        if str(adaptive_acceptance.get("readiness_status", "")).strip() == "ready-for-r19-t01"
        and summary["eligible_count"] > 0
        else "not-ready-for-r19-t03"
    )

    return {
        "artifact_contract_version": ARTIFACT_CONTRACT_VERSION,
        "artifact_id": ARTIFACT_ID,
        "plan_date": plan_date,
        "scope": "adaptive-coaching acceptance -> long-horizon learner profile -> goal-adaptation eligibility boundary",
        "input_contract_refs": [
            {"name": "r17_t06_teacher_loop_acceptance_artifact", "version": teacher_loop.get("artifact_contract_version", "")},
            {"name": "r18_t02_adaptive_coaching_context", "version": adaptive_context.get("artifact_contract_version", "")},
            {"name": "r18_t04_coaching_feedback_loop", "version": feedback_loop.get("artifact_contract_version", "")},
            {"name": "r18_t05_closed_loop_operations", "version": closed_loop.get("artifact_contract_version", "")},
            {"name": "r18_t06_adaptive_coaching_acceptance_artifact", "version": adaptive_acceptance.get("artifact_contract_version", "")},
        ],
        "longitudinal_profile_contract_version": ARTIFACT_CONTRACT_VERSION,
        "source_scope": dict(adaptive_context.get("source_scope", {})),
        "adaptive_coaching_refs": {
            "teacher_loop_artifact_id": teacher_loop.get("artifact_id", ""),
            "adaptive_acceptance_artifact_id": adaptive_acceptance.get("artifact_id", ""),
            "adaptive_readiness_status": adaptive_acceptance.get("readiness_status", ""),
            "feedback_loop_artifact_id": feedback_loop.get("artifact_id", ""),
            "closed_loop_artifact_id": closed_loop.get("artifact_id", ""),
        },
        "stale_cycle_window": {
            "window_days": max(1, stale_cycle_days),
            "stale_cycle_count": summary["stale_cycle_count"],
        },
        "milestone_drift_signals": [
            {
                "question": item["question"],
                "goal_adaptation_eligibility": item["goal_adaptation_eligibility"],
                "drift_signal_type": item["drift_signal_type"],
                "feedback_intake_status": item["feedback_intake_status"],
            }
            for item in goal_adjustment_inputs
        ],
        "long_horizon_profile": long_horizon_profile,
        "longitudinal_context": {
            "plan_date": plan_date,
            "goal_adjustment_inputs": goal_adjustment_inputs,
        },
        "goal_adaptation_eligibility_summary": summary,
        "remaining_gaps": remaining_gaps,
        "readiness_status": readiness_status,
        "post_r19_t02_successor": POST_R19_T02_SUCCESSOR,
    }


def render_markdown(payload: dict[str, Any]) -> str:
    profile = dict(payload.get("long_horizon_profile", {}))
    summary = dict(payload.get("goal_adaptation_eligibility_summary", {}))
    successor = dict(payload.get("post_r19_t02_successor", {}))
    lines = [
        "# R19-T02 longitudinal tutoring context",
        "",
        f"- artifact_id: {payload.get('artifact_id', '')}",
        f"- plan_date: {payload.get('plan_date', '')}",
        f"- readiness_status: {payload.get('readiness_status', '')}",
        "",
        "## Long-horizon profile",
        "",
        f"- current_stage: {profile.get('current_stage', '')}",
        f"- long_horizon_window_days: {profile.get('long_horizon_window_days', 0)}",
        f"- eligible_goal_adjustment_count: {profile.get('eligible_goal_adjustment_count', 0)}",
        f"- stale_cycle_count: {summary.get('stale_cycle_count', 0)}",
        f"- review_only_count: {summary.get('review_only_count', 0)}",
        f"- blocked_count: {summary.get('blocked_count', 0)}",
        f"- out_of_scope_count: {summary.get('out_of_scope_count', 0)}",
        "",
        "## Post-R19-T02 successor",
        "",
        f"- track_id: {successor.get('track_id', '')}",
        f"- machine_readable_entry_point: {successor.get('machine_readable_entry_point', '')}",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    args = parse_args()
    index_root = Path(args.vault_root) / INDEX_DIRNAME
    index_root.mkdir(parents=True, exist_ok=True)
    payload = build_payload(index_root, plan_date=args.plan_date, stale_cycle_days=max(1, args.stale_cycle_days))
    save_json(index_root / ARTIFACT_JSON, payload)
    save_text(index_root / ARTIFACT_MD, render_markdown(payload))
    result = {
        "artifact_id": payload["artifact_id"],
        "plan_date": payload["plan_date"],
        "long_horizon_profile": payload["long_horizon_profile"],
        "goal_adaptation_eligibility_summary": payload["goal_adaptation_eligibility_summary"],
        "readiness_status": payload["readiness_status"],
        "post_r19_t02_successor": payload["post_r19_t02_successor"],
    }
    if args.format == "json":
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
