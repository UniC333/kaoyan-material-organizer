#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from build_longitudinal_tutoring_context import ARTIFACT_JSON as LONGITUDINAL_CONTEXT_JSON
from common import INDEX_DIRNAME, default_vault_root_arg, save_json, save_text

ARTIFACT_JSON = "37_r19_tutoring_strategy_packet.json"
ARTIFACT_MD = "37_r19_tutoring_strategy_packet.md"
ARTIFACT_ID = "r19-tutoring-strategy-packet"
ARTIFACT_CONTRACT_VERSION = "r19.tutoring-strategy-packet.v1"
POST_R19_T03_SUCCESSOR = {
    "track_id": "R19-T04",
    "title": "multi-cycle feedback intake and tutoring traceability boundary",
    "scope": "tutoring strategy packet -> cycle feedback -> tutoring traceability",
    "machine_readable_entry_point": "R19-T04 -> M10-T04",
    "status": "defined_not_started",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--vault-root", default=default_vault_root_arg())
    parser.add_argument("--plan-date", required=True)
    parser.add_argument("--format", choices=("json", "quiet"), default="json")
    return parser.parse_args()


def _load_context(index_root: Path) -> dict[str, Any]:
    path = index_root / LONGITUDINAL_CONTEXT_JSON
    if not path.exists():
        raise SystemExit("missing longitudinal tutoring context artifact; run build_longitudinal_tutoring_context.py first")
    return json.loads(path.read_text(encoding="utf-8"))


def _strategy_type(eligibility: str) -> str:
    mapping = {
        "eligible": "recommended",
        "stale_cycle": "review_needed",
        "review_only": "review_needed",
        "blocked": "blocked",
        "out_of_scope": "out_of_scope",
    }
    return mapping.get(eligibility, "blocked")


def _phase_priority(eligibility: str, drift_signal_type: str) -> str:
    if eligibility == "eligible":
        if drift_signal_type == "needs_retry_after_formal_feedback":
            return "stabilize_current_stage_first"
        return "advance_current_stage"
    if eligibility == "stale_cycle":
        return "refresh_previous_cycle_first"
    if eligibility == "review_only":
        return "human_review_before_reprioritization"
    if eligibility == "out_of_scope":
        return "keep_outside_current_goal_scope"
    return "block_until_input_repaired"


def _phase_priority_reason(eligibility: str) -> str:
    mapping = {
        "eligible": "eligible_long_horizon_strategy_inside_current_scope",
        "stale_cycle": "stale_cycle_requires_refresh_before_strategy_escalation",
        "review_only": "review_only_signal_requires_human_goal_interpretation",
        "blocked": "blocked_signal_cannot_form_goal_strategy",
        "out_of_scope": "subject_not_in_current_formal_scope",
    }
    return mapping.get(eligibility, "blocked_signal_cannot_form_goal_strategy")


def _goal_scope_reason(item: dict[str, Any]) -> str:
    if str(item.get("goal_adaptation_eligibility", "")).strip() == "out_of_scope":
        return "subject_not_in_current_formal_scope"
    return "subject_in_current_formal_scope"


def _blocked_reasons(item: dict[str, Any]) -> list[str]:
    eligibility = str(item.get("goal_adaptation_eligibility", "")).strip()
    if eligibility in {"blocked", "out_of_scope"}:
        reason = str(item.get("eligibility_reason", "")).strip()
        return [reason] if reason else []
    return []


def _milestone_rationale(item: dict[str, Any], profile: dict[str, Any]) -> str:
    current_stage = str(profile.get("current_stage", "")).strip() or "longitudinal_tutoring"
    reason = str(item.get("priority_reason", "")).strip()
    if reason:
        return f"{current_stage}:{reason}"
    return f"{current_stage}:{item.get('drift_signal_type', '')}"


def _build_strategy(item: dict[str, Any], profile: dict[str, Any]) -> dict[str, Any]:
    eligibility = str(item.get("goal_adaptation_eligibility", "")).strip()
    drift_signal_type = str(item.get("drift_signal_type", "")).strip()
    return {
        "question": str(item.get("question", "")).strip(),
        "subject": item.get("subject", ""),
        "chapter_title": item.get("chapter_title", ""),
        "strategy_type": _strategy_type(eligibility),
        "goal_adaptation_eligibility": eligibility,
        "phase_priority": _phase_priority(eligibility, drift_signal_type),
        "phase_priority_reason": _phase_priority_reason(eligibility),
        "milestone_rationale": _milestone_rationale(item, profile),
        "source_refs": list(item.get("source_refs", [])),
        "goal_scope_reason": _goal_scope_reason(item),
        "blocked_reasons": _blocked_reasons(item),
        "strategy_rationale": str(item.get("priority_reason", "")).strip(),
        "drift_signal_type": drift_signal_type,
        "feedback_intake_status": str(item.get("feedback_intake_status", "")).strip(),
    }


def build_payload(index_root: Path, plan_date: str) -> dict[str, Any]:
    context = _load_context(index_root)
    profile = dict(context.get("long_horizon_profile", {}))
    recommended_strategies: list[dict[str, Any]] = []
    review_needed_strategies: list[dict[str, Any]] = []
    blocked_strategies: list[dict[str, Any]] = []
    out_of_scope_strategies: list[dict[str, Any]] = []

    for item in list(context.get("longitudinal_context", {}).get("goal_adjustment_inputs", [])):
        strategy = _build_strategy(item, profile)
        if strategy["strategy_type"] == "recommended":
            recommended_strategies.append(strategy)
        elif strategy["strategy_type"] == "review_needed":
            review_needed_strategies.append(strategy)
        elif strategy["strategy_type"] == "out_of_scope":
            out_of_scope_strategies.append(strategy)
        else:
            blocked_strategies.append(strategy)

    remaining_gaps = list(context.get("remaining_gaps", []))
    if not recommended_strategies:
        remaining_gaps.append("No recommended long-horizon strategy is currently available for the plan date.")
    readiness_status = "ready-for-r19-t04" if recommended_strategies else "not-ready-for-r19-t04"

    return {
        "artifact_contract_version": ARTIFACT_CONTRACT_VERSION,
        "artifact_id": ARTIFACT_ID,
        "strategy_packet_id": f"r19-strategy-packet-{plan_date}",
        "plan_date": plan_date,
        "scope": "longitudinal tutoring context -> tutoring strategy packaging -> phase-priority boundary",
        "input_contract_refs": [
            {
                "name": "r19_t02_longitudinal_tutoring_context",
                "version": context.get("artifact_contract_version", ""),
            }
        ],
        "recommended_strategies": recommended_strategies,
        "review_needed_strategies": review_needed_strategies,
        "blocked_strategies": blocked_strategies,
        "out_of_scope_strategies": out_of_scope_strategies,
        "remaining_gaps": remaining_gaps,
        "readiness_status": readiness_status,
        "post_r19_t03_successor": POST_R19_T03_SUCCESSOR,
    }


def render_markdown(payload: dict[str, Any]) -> str:
    successor = dict(payload.get("post_r19_t03_successor", {}))
    lines = [
        "# R19-T03 tutoring strategy packet",
        "",
        f"- artifact_id: {payload.get('artifact_id', '')}",
        f"- strategy_packet_id: {payload.get('strategy_packet_id', '')}",
        f"- plan_date: {payload.get('plan_date', '')}",
        f"- readiness_status: {payload.get('readiness_status', '')}",
        "",
        "## Strategy summary",
        "",
        f"- recommended_strategies: {len(list(payload.get('recommended_strategies', [])))}",
        f"- review_needed_strategies: {len(list(payload.get('review_needed_strategies', [])))}",
        f"- blocked_strategies: {len(list(payload.get('blocked_strategies', [])))}",
        f"- out_of_scope_strategies: {len(list(payload.get('out_of_scope_strategies', [])))}",
        "",
        "## Post-R19-T03 successor",
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
        "strategy_packet_id": payload["strategy_packet_id"],
        "plan_date": payload["plan_date"],
        "recommended_strategies": payload["recommended_strategies"],
        "review_needed_strategies": payload["review_needed_strategies"],
        "blocked_strategies": payload["blocked_strategies"],
        "out_of_scope_strategies": payload["out_of_scope_strategies"],
        "readiness_status": payload["readiness_status"],
        "post_r19_t03_successor": payload["post_r19_t03_successor"],
    }
    if args.format == "json":
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
