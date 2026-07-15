#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from build_long_horizon_operations import ARTIFACT_JSON as LONG_HORIZON_OPERATIONS_JSON
from build_r19_longitudinal_tutoring_artifact import ARTIFACT_JSON as R19_ACCEPTANCE_JSON
from build_tutoring_feedback_loop import ARTIFACT_JSON as TUTORING_FEEDBACK_JSON
from build_tutoring_strategy_packet import ARTIFACT_JSON as STRATEGY_PACKET_JSON
from common import INDEX_DIRNAME, default_vault_root_arg, load_json_or_default, save_json, save_text

ARTIFACT_JSON = "41_r20_autonomous_trigger_contract.json"
ARTIFACT_MD = "41_r20_autonomous_trigger_contract.md"
ARTIFACT_ID = "r20-autonomous-trigger-contract"
ARTIFACT_CONTRACT_VERSION = "r20.autonomous-trigger-contract.v1"
POST_R20_T02_SUCCESSOR = {
    "track_id": "R20-T03",
    "title": "autonomous action planning and approval boundary",
    "scope": "initiative eligibility -> autonomous action planning -> approval boundary",
    "machine_readable_entry_point": "R20-T03 -> M11-T03",
    "status": "defined_not_started",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--vault-root", default=default_vault_root_arg())
    parser.add_argument("--plan-date", required=True)
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


def _strategy_map(strategy_packet: dict[str, Any]) -> dict[str, dict[str, Any]]:
    items: dict[str, dict[str, Any]] = {}
    for key in (
        "recommended_strategies",
        "review_needed_strategies",
        "blocked_strategies",
        "out_of_scope_strategies",
    ):
        for item in list(strategy_packet.get(key, [])):
            question = str(item.get("question", "")).strip()
            if question:
                items[question] = dict(item)
    return items


def _feedback_map(feedback_loop: dict[str, Any]) -> dict[str, dict[str, Any]]:
    items: dict[str, dict[str, Any]] = {}
    for key in (
        "formal_cycle_feedback",
        "review_only_feedback",
        "out_of_scope_feedback",
        "blocked_follow_ups",
    ):
        for item in list(feedback_loop.get(key, [])):
            question = str(item.get("question", "")).strip()
            if question:
                items[question] = dict(item)
    return items


def _build_signal(
    item: dict[str, Any],
    *,
    initiative_eligibility: str,
    trigger_signal_source: str,
    trigger_reason: str,
    strategy_item: dict[str, Any] | None,
    feedback_item: dict[str, Any] | None,
) -> dict[str, Any]:
    return {
        "question": str(item.get("question", "")).strip(),
        "subject": item.get("subject", ""),
        "chapter_title": item.get("chapter_title", ""),
        "initiative_eligibility": initiative_eligibility,
        "trigger_signal_source": trigger_signal_source,
        "trigger_reason": trigger_reason,
        "goal_adjustment_action": dict(item.get("goal_adjustment", {})).get("action", ""),
        "target_cycle_offset": int(dict(item.get("goal_adjustment", {})).get("target_cycle_offset", 0) or 0),
        "source_refs": list(item.get("source_refs", [])),
        "strategy_refs": list(item.get("strategy_refs", [])),
        "cycle_feedback_status": str(item.get("cycle_feedback_status", "")).strip(),
        "strategy_type": str((strategy_item or {}).get("strategy_type", "")).strip(),
        "phase_priority": str((strategy_item or {}).get("phase_priority", "")).strip(),
        "feedback_result": str((feedback_item or {}).get("result", "")).strip(),
        "fact_writeback_allowed": False,
    }


def build_payload(index_root: Path, plan_date: str) -> dict[str, Any]:
    acceptance = _load_artifact(index_root, R19_ACCEPTANCE_JSON)
    strategy_packet = _load_artifact(index_root, STRATEGY_PACKET_JSON)
    feedback_loop = _load_artifact(index_root, TUTORING_FEEDBACK_JSON)
    operations = _load_artifact(index_root, LONG_HORIZON_OPERATIONS_JSON)

    strategy_by_question = _strategy_map(strategy_packet)
    feedback_by_question = _feedback_map(feedback_loop)

    initiative_signals: list[dict[str, Any]] = []
    summary = {
        "auto_executable_count": 0,
        "approval_required_count": 0,
        "review_only_count": 0,
        "blocked_count": 0,
    }

    for item in list(operations.get("long_horizon_operations", [])):
        question = str(item.get("question", "")).strip()
        signal = _build_signal(
            item,
            initiative_eligibility="auto_executable",
            trigger_signal_source="auto_goal_adjustment",
            trigger_reason=str(dict(item.get("goal_adjustment", {})).get("action", "")).strip() or "auto_goal_adjustment_ready",
            strategy_item=strategy_by_question.get(question),
            feedback_item=feedback_by_question.get(question),
        )
        initiative_signals.append(signal)
        summary["auto_executable_count"] += 1

    for item in list(operations.get("operator_overrides", [])):
        question = str(item.get("question", "")).strip()
        signal = _build_signal(
            item,
            initiative_eligibility="approval_required",
            trigger_signal_source="operator_override",
            trigger_reason=str(item.get("override_reason", "")).strip() or "operator_override_requires_human_approval",
            strategy_item=strategy_by_question.get(question),
            feedback_item=feedback_by_question.get(question),
        )
        initiative_signals.append(signal)
        summary["approval_required_count"] += 1

    for item in list(operations.get("locked_manual_edits", [])):
        question = str(item.get("question", "")).strip()
        signal = _build_signal(
            item,
            initiative_eligibility="review_only",
            trigger_signal_source="manual_lock",
            trigger_reason=str(item.get("override_reason", "")).strip() or "manual_lock_requires_review_only",
            strategy_item=strategy_by_question.get(question),
            feedback_item=feedback_by_question.get(question),
        )
        initiative_signals.append(signal)
        summary["review_only_count"] += 1

    for item in list(operations.get("scope_blocked_operations", [])):
        question = str(item.get("question", "")).strip()
        signal = _build_signal(
            item,
            initiative_eligibility="blocked",
            trigger_signal_source=str(item.get("cycle_feedback_status", "")).strip() or "scope_blocked_operation",
            trigger_reason=str(item.get("override_reason", "")).strip() or "blocked_autonomous_initiative_signal",
            strategy_item=strategy_by_question.get(question),
            feedback_item=feedback_by_question.get(question),
        )
        initiative_signals.append(signal)
        summary["blocked_count"] += 1

    tutoring_summary = dict(acceptance.get("tutoring_summary", {}))
    goal_adjustment_safety = dict(acceptance.get("goal_adjustment_safety_status", {}))
    source_scope = dict(tutoring_summary)
    remaining_gaps = _dedupe_strings([str(item) for item in list(acceptance.get("remaining_gaps", []))]) + [
        "R20-T02 only defines autonomous trigger eligibility and does not yet publish learner-side action plans or governance-ledger updates.",
    ]

    readiness_status = (
        "ready-for-r20-t03"
        if str(acceptance.get("readiness_status", "")).strip() == "ready-for-r20-t01"
        and (summary["auto_executable_count"] > 0 or summary["approval_required_count"] > 0)
        else "not-ready-for-r20-t03"
    )

    return {
        "artifact_contract_version": ARTIFACT_CONTRACT_VERSION,
        "artifact_id": ARTIFACT_ID,
        "plan_date": plan_date,
        "scope": "longitudinal-tutoring acceptance -> initiative eligibility -> autonomous trigger contract boundary",
        "input_contract_refs": [
            {"name": "r19_t06_longitudinal_tutoring_acceptance_artifact", "version": acceptance.get("artifact_contract_version", "")},
            {"name": "r19_t03_tutoring_strategy_packet", "version": strategy_packet.get("artifact_contract_version", "")},
            {"name": "r19_t04_tutoring_feedback_loop", "version": feedback_loop.get("artifact_contract_version", "")},
            {"name": "r19_t05_long_horizon_operations", "version": operations.get("artifact_contract_version", "")},
        ],
        "longitudinal_tutoring_refs": {
            "acceptance_artifact_id": acceptance.get("artifact_id", ""),
            "readiness_status": acceptance.get("readiness_status", ""),
            "tutoring_summary_status": tutoring_summary.get("status", ""),
            "goal_adjustment_safety_status": goal_adjustment_safety.get("status", ""),
        },
        "trigger_guardrails": {
            "preserve_human_owned_edits": bool(goal_adjustment_safety.get("preserve_human_owned_edits", False)),
            "fact_writeback_allowed": False,
            "in_scope_subjects": list(source_scope.get("in_scope_subjects", [])),
            "out_of_scope_subjects": list(source_scope.get("out_of_scope_subjects", [])),
            "human_override_priority": "operator_override_and_manual_lock_take_precedence",
        },
        "autonomous_trigger_contract": {
            "plan_date": plan_date,
            "initiative_signals": initiative_signals,
        },
        "initiative_eligibility_summary": summary,
        "remaining_gaps": remaining_gaps,
        "readiness_status": readiness_status,
        "post_r20_t02_successor": POST_R20_T02_SUCCESSOR,
    }


def render_markdown(payload: dict[str, Any]) -> str:
    summary = dict(payload.get("initiative_eligibility_summary", {}))
    successor = dict(payload.get("post_r20_t02_successor", {}))
    lines = [
        "# R20-T02 autonomous trigger contract",
        "",
        f"- artifact_id: {payload.get('artifact_id', '')}",
        f"- plan_date: {payload.get('plan_date', '')}",
        f"- readiness_status: {payload.get('readiness_status', '')}",
        "",
        "## Initiative eligibility summary",
        "",
        f"- auto_executable_count: {summary.get('auto_executable_count', 0)}",
        f"- approval_required_count: {summary.get('approval_required_count', 0)}",
        f"- review_only_count: {summary.get('review_only_count', 0)}",
        f"- blocked_count: {summary.get('blocked_count', 0)}",
        "",
        "## Post-R20-T02 successor",
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
    payload = build_payload(index_root, args.plan_date)
    save_json(index_root / ARTIFACT_JSON, payload)
    save_text(index_root / ARTIFACT_MD, render_markdown(payload))
    result = {
        "artifact_id": payload["artifact_id"],
        "plan_date": payload["plan_date"],
        "longitudinal_tutoring_refs": payload["longitudinal_tutoring_refs"],
        "trigger_guardrails": payload["trigger_guardrails"],
        "autonomous_trigger_contract": payload["autonomous_trigger_contract"],
        "initiative_eligibility_summary": payload["initiative_eligibility_summary"],
        "remaining_gaps": payload["remaining_gaps"],
        "readiness_status": payload["readiness_status"],
        "post_r20_t02_successor": payload["post_r20_t02_successor"],
    }
    if args.format == "json":
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
