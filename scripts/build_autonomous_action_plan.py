#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from build_autonomous_trigger_contract import ARTIFACT_JSON as TRIGGER_CONTRACT_JSON
from common import INDEX_DIRNAME, default_vault_root_arg, load_json_or_default, save_json, save_text

ARTIFACT_JSON = "42_r20_autonomous_action_plan.json"
ARTIFACT_MD = "42_r20_autonomous_action_plan.md"
ARTIFACT_ID = "r20-autonomous-action-plan"
ARTIFACT_CONTRACT_VERSION = "r20.autonomous-action-plan.v1"
POST_R20_T03_SUCCESSOR = {
    "track_id": "R20-T04",
    "title": "self-evolving governance ledger and policy-adjustment boundary",
    "scope": "autonomous action planning -> governance ledger -> policy adjustment boundary",
    "machine_readable_entry_point": "R20-T04 -> M11-T04",
    "status": "defined_not_started",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--vault-root", default=default_vault_root_arg())
    parser.add_argument("--plan-date", required=True)
    parser.add_argument("--format", choices=("json", "quiet"), default="json")
    return parser.parse_args()


def _load_contract(index_root: Path) -> dict[str, Any]:
    payload = load_json_or_default(index_root / TRIGGER_CONTRACT_JSON, {})
    if not payload:
        raise SystemExit("missing autonomous trigger contract artifact; run build_autonomous_trigger_contract.py first")
    return payload


def _target_learner_effect(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "action": str(item.get("goal_adjustment_action", "")).strip() or "no_direct_action",
        "target_cycle_offset": int(item.get("target_cycle_offset", 0) or 0),
    }


def _auto_action(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "question": item.get("question", ""),
        "subject": item.get("subject", ""),
        "chapter_title": item.get("chapter_title", ""),
        "planned_action": "apply_goal_adjustment",
        "initiative_eligibility": item.get("initiative_eligibility", ""),
        "trigger_signal_source": item.get("trigger_signal_source", ""),
        "approval_boundary": "auto_execute_with_trace",
        "action_rationale": str(item.get("trigger_reason", "")).strip(),
        "target_learner_effect": _target_learner_effect(item),
        "source_refs": list(item.get("source_refs", [])),
        "strategy_refs": list(item.get("strategy_refs", [])),
        "cycle_feedback_status": str(item.get("cycle_feedback_status", "")).strip(),
        "fact_writeback_allowed": False,
    }


def _approval_action(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "question": item.get("question", ""),
        "subject": item.get("subject", ""),
        "chapter_title": item.get("chapter_title", ""),
        "planned_action": "request_operator_approval",
        "initiative_eligibility": item.get("initiative_eligibility", ""),
        "trigger_signal_source": item.get("trigger_signal_source", ""),
        "approval_boundary": "requires_human_approval",
        "action_rationale": str(item.get("trigger_reason", "")).strip(),
        "target_learner_effect": _target_learner_effect(item),
        "source_refs": list(item.get("source_refs", [])),
        "strategy_refs": list(item.get("strategy_refs", [])),
        "cycle_feedback_status": str(item.get("cycle_feedback_status", "")).strip(),
        "fact_writeback_allowed": False,
    }


def _review_only_action(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "question": item.get("question", ""),
        "subject": item.get("subject", ""),
        "chapter_title": item.get("chapter_title", ""),
        "planned_action": "emit_review_only_recommendation",
        "initiative_eligibility": item.get("initiative_eligibility", ""),
        "trigger_signal_source": item.get("trigger_signal_source", ""),
        "approval_boundary": "review_only_no_direct_execution",
        "action_rationale": str(item.get("trigger_reason", "")).strip(),
        "target_learner_effect": _target_learner_effect(item),
        "source_refs": list(item.get("source_refs", [])),
        "strategy_refs": list(item.get("strategy_refs", [])),
        "cycle_feedback_status": str(item.get("cycle_feedback_status", "")).strip(),
        "fact_writeback_allowed": False,
    }


def _blocked_action(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "question": item.get("question", ""),
        "subject": item.get("subject", ""),
        "chapter_title": item.get("chapter_title", ""),
        "planned_action": "blocked_no_action",
        "initiative_eligibility": item.get("initiative_eligibility", ""),
        "trigger_signal_source": item.get("trigger_signal_source", ""),
        "approval_boundary": "blocked_no_execution",
        "action_rationale": str(item.get("trigger_reason", "")).strip(),
        "target_learner_effect": _target_learner_effect(item),
        "source_refs": list(item.get("source_refs", [])),
        "strategy_refs": list(item.get("strategy_refs", [])),
        "cycle_feedback_status": str(item.get("cycle_feedback_status", "")).strip(),
        "fact_writeback_allowed": False,
    }


def build_payload(index_root: Path, plan_date: str) -> dict[str, Any]:
    trigger_contract = _load_contract(index_root)
    auto_executable_actions: list[dict[str, Any]] = []
    approval_required_actions: list[dict[str, Any]] = []
    review_only_actions: list[dict[str, Any]] = []
    blocked_actions: list[dict[str, Any]] = []

    for item in list(trigger_contract.get("autonomous_trigger_contract", {}).get("initiative_signals", [])):
        eligibility = str(item.get("initiative_eligibility", "")).strip()
        if eligibility == "auto_executable":
            auto_executable_actions.append(_auto_action(item))
        elif eligibility == "approval_required":
            approval_required_actions.append(_approval_action(item))
        elif eligibility == "review_only":
            review_only_actions.append(_review_only_action(item))
        else:
            blocked_actions.append(_blocked_action(item))

    policy = dict(trigger_contract.get("trigger_guardrails", {}))
    remaining_gaps = list(trigger_contract.get("remaining_gaps", []))
    remaining_gaps.append(
        "R20-T03 only defines learner-side action planning and approval boundaries; governance-ledger drift handling still belongs to R20-T04."
    )
    readiness_status = (
        "ready-for-r20-t04"
        if str(trigger_contract.get("readiness_status", "")).strip() == "ready-for-r20-t03"
        and (auto_executable_actions or approval_required_actions)
        else "not-ready-for-r20-t04"
    )

    return {
        "artifact_contract_version": ARTIFACT_CONTRACT_VERSION,
        "artifact_id": ARTIFACT_ID,
        "action_plan_id": f"r20-action-plan-{plan_date}",
        "plan_date": plan_date,
        "scope": "autonomous trigger contract -> learner-side action planning -> approval boundary",
        "input_contract_refs": [
            {
                "name": "r20_t02_autonomous_trigger_contract",
                "version": trigger_contract.get("artifact_contract_version", ""),
            }
        ],
        "action_planning_policy": {
            "preserve_human_owned_edits": bool(policy.get("preserve_human_owned_edits", False)),
            "fact_writeback_allowed": False,
            "human_override_priority": str(policy.get("human_override_priority", "")).strip(),
        },
        "auto_executable_actions": auto_executable_actions,
        "approval_required_actions": approval_required_actions,
        "review_only_actions": review_only_actions,
        "blocked_actions": blocked_actions,
        "remaining_gaps": remaining_gaps,
        "readiness_status": readiness_status,
        "post_r20_t03_successor": POST_R20_T03_SUCCESSOR,
    }


def render_markdown(payload: dict[str, Any]) -> str:
    successor = dict(payload.get("post_r20_t03_successor", {}))
    lines = [
        "# R20-T03 autonomous action plan",
        "",
        f"- artifact_id: {payload.get('artifact_id', '')}",
        f"- action_plan_id: {payload.get('action_plan_id', '')}",
        f"- plan_date: {payload.get('plan_date', '')}",
        f"- readiness_status: {payload.get('readiness_status', '')}",
        "",
        "## Action summary",
        "",
        f"- auto_executable_actions: {len(list(payload.get('auto_executable_actions', [])))}",
        f"- approval_required_actions: {len(list(payload.get('approval_required_actions', [])))}",
        f"- review_only_actions: {len(list(payload.get('review_only_actions', [])))}",
        f"- blocked_actions: {len(list(payload.get('blocked_actions', [])))}",
        "",
        "## Post-R20-T03 successor",
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
        "action_plan_id": payload["action_plan_id"],
        "plan_date": payload["plan_date"],
        "action_planning_policy": payload["action_planning_policy"],
        "auto_executable_actions": payload["auto_executable_actions"],
        "approval_required_actions": payload["approval_required_actions"],
        "review_only_actions": payload["review_only_actions"],
        "blocked_actions": payload["blocked_actions"],
        "readiness_status": payload["readiness_status"],
        "post_r20_t03_successor": payload["post_r20_t03_successor"],
    }
    if args.format == "json":
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
