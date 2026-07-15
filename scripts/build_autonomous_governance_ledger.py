#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from build_autonomous_action_plan import ARTIFACT_JSON as ACTION_PLAN_JSON
from common import INDEX_DIRNAME, default_vault_root_arg, load_json_or_default, save_json, save_text

ARTIFACT_JSON = "43_r20_autonomous_governance_ledger.json"
ARTIFACT_MD = "43_r20_autonomous_governance_ledger.md"
ARTIFACT_ID = "r20-autonomous-governance-ledger"
ARTIFACT_CONTRACT_VERSION = "r20.autonomous-governance-ledger.v1"
POST_R20_T04_SUCCESSOR = {
    "track_id": "R20-T05",
    "title": "autonomous tutoring acceptance artifact and post-R20 successor preparation",
    "scope": "governance ledger -> policy adjustment boundary -> autonomous tutoring acceptance",
    "machine_readable_entry_point": "R20-T05 -> M11-T05",
    "status": "defined_not_started",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--vault-root", default=default_vault_root_arg())
    parser.add_argument("--plan-date", required=True)
    parser.add_argument("--governance-json")
    parser.add_argument("--format", choices=("json", "quiet"), default="json")
    return parser.parse_args()


def _load_action_plan(index_root: Path) -> dict[str, Any]:
    payload = load_json_or_default(index_root / ACTION_PLAN_JSON, {})
    if not payload:
        raise SystemExit("missing autonomous action plan artifact; run build_autonomous_action_plan.py first")
    return payload


def _load_governance(path: str | None) -> dict[str, Any]:
    if not path:
        return {
            "strategy_drifts": [],
            "false_positive_flags": [],
            "over_interventions": [],
            "human_rollbacks": [],
        }
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return {
        "strategy_drifts": list(payload.get("strategy_drifts", [])),
        "false_positive_flags": list(payload.get("false_positive_flags", [])),
        "over_interventions": list(payload.get("over_interventions", [])),
        "human_rollbacks": list(payload.get("human_rollbacks", [])),
    }


def _action_map(action_plan: dict[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
    items: dict[tuple[str, str], dict[str, Any]] = {}
    for key in (
        "auto_executable_actions",
        "approval_required_actions",
        "review_only_actions",
        "blocked_actions",
    ):
        for item in list(action_plan.get(key, [])):
            question = str(item.get("question", "")).strip()
            trigger_signal_source = str(item.get("trigger_signal_source", "")).strip()
            map_key = (question, trigger_signal_source)
            if question and trigger_signal_source and map_key not in items:
                items[map_key] = dict(item)
    return items


def _base_entry(action_item: dict[str, Any], governance_item: dict[str, Any], event_type: str) -> dict[str, Any]:
    return {
        "question": action_item.get("question", ""),
        "subject": action_item.get("subject", ""),
        "chapter_title": action_item.get("chapter_title", ""),
        "ledger_event_type": event_type,
        "governance_reason": str(governance_item.get("governance_reason", "")).strip(),
        "planned_action": action_item.get("planned_action", ""),
        "approval_boundary": action_item.get("approval_boundary", ""),
        "initiative_eligibility": action_item.get("initiative_eligibility", ""),
        "trigger_signal_source": action_item.get("trigger_signal_source", ""),
        "target_learner_effect": dict(action_item.get("target_learner_effect", {})),
        "source_refs": list(action_item.get("source_refs", [])),
        "strategy_refs": list(action_item.get("strategy_refs", [])),
        "cycle_feedback_status": str(action_item.get("cycle_feedback_status", "")).strip(),
        "fact_writeback_allowed": False,
    }


def _with_policy_adjustment(base: dict[str, Any], governance_item: dict[str, Any], *, rollback_required: bool) -> dict[str, Any]:
    adjustment = dict(governance_item.get("policy_adjustment", {}))
    rollback = dict(governance_item.get("rollback_action", {}))
    selected = rollback if rollback_required else adjustment
    return {
        **base,
        "rollback_required": rollback_required,
        "policy_adjustment": {
            "action": str(selected.get("action", "")).strip(),
            "target_cycle_offset": int(selected.get("target_cycle_offset", 0) or 0),
        },
    }


def _collect_entries(
    governance_items: list[dict[str, Any]],
    actions_by_question: dict[tuple[str, str], dict[str, Any]],
    *,
    event_type: str,
    rollback_required: bool,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for item in governance_items:
        question = str(item.get("question", "")).strip()
        trigger_signal_source = str(item.get("trigger_signal_source", "")).strip()
        map_key = (question, trigger_signal_source)
        if not question or not trigger_signal_source or map_key not in actions_by_question:
            continue
        base = _base_entry(actions_by_question[map_key], item, event_type)
        results.append(_with_policy_adjustment(base, item, rollback_required=rollback_required))
    return results


def _policy_trace(*entry_groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    trace: list[dict[str, Any]] = []
    for group in entry_groups:
        for item in group:
            adjustment = dict(item.get("policy_adjustment", {}))
            trace.append(
                {
                    "question": item.get("question", ""),
                    "ledger_event_type": item.get("ledger_event_type", ""),
                    "adjustment_action": str(adjustment.get("action", "")).strip(),
                    "target_cycle_offset": int(adjustment.get("target_cycle_offset", 0) or 0),
                    "approval_boundary": item.get("approval_boundary", ""),
                    "rollback_required": bool(item.get("rollback_required", False)),
                }
            )
    return trace


def build_payload(index_root: Path, plan_date: str, governance_json: str | None) -> dict[str, Any]:
    action_plan = _load_action_plan(index_root)
    governance = _load_governance(governance_json)
    actions_by_question = _action_map(action_plan)

    strategy_drift_entries = _collect_entries(
        governance.get("strategy_drifts", []),
        actions_by_question,
        event_type="strategy_drift",
        rollback_required=False,
    )
    false_positive_entries = _collect_entries(
        governance.get("false_positive_flags", []),
        actions_by_question,
        event_type="false_positive",
        rollback_required=False,
    )
    over_intervention_entries = _collect_entries(
        governance.get("over_interventions", []),
        actions_by_question,
        event_type="over_intervention",
        rollback_required=False,
    )
    human_rollback_entries = _collect_entries(
        governance.get("human_rollbacks", []),
        actions_by_question,
        event_type="human_rollback",
        rollback_required=True,
    )

    remaining_gaps = list(action_plan.get("remaining_gaps", []))
    remaining_gaps.append(
        "R20-T04 only records governance-ledger drift and policy-adjustment boundaries; final autonomous-tutoring acceptance still belongs to R20-T05."
    )

    readiness_status = (
        "ready-for-r20-t05"
        if str(action_plan.get("readiness_status", "")).strip() == "ready-for-r20-t04"
        and (
            strategy_drift_entries
            or false_positive_entries
            or over_intervention_entries
            or human_rollback_entries
        )
        else "not-ready-for-r20-t05"
    )

    return {
        "artifact_contract_version": ARTIFACT_CONTRACT_VERSION,
        "artifact_id": ARTIFACT_ID,
        "governance_ledger_id": f"r20-governance-ledger-{plan_date}",
        "plan_date": plan_date,
        "scope": "autonomous action plan -> governance ledger -> policy adjustment and rollback boundary",
        "input_contract_refs": [
            {
                "name": "r20_t03_autonomous_action_plan",
                "version": action_plan.get("artifact_contract_version", ""),
            }
        ],
        "policy_adjustment_policy": {
            "preserve_human_owned_edits": bool(
                dict(action_plan.get("action_planning_policy", {})).get("preserve_human_owned_edits", False)
            ),
            "fact_writeback_allowed": False,
            "human_override_priority": str(
                dict(action_plan.get("action_planning_policy", {})).get("human_override_priority", "")
            ).strip(),
            "rollback_authority": "human_operator_only",
        },
        "strategy_drift_entries": strategy_drift_entries,
        "false_positive_entries": false_positive_entries,
        "over_intervention_entries": over_intervention_entries,
        "human_rollback_entries": human_rollback_entries,
        "policy_adjustment_trace": _policy_trace(
            strategy_drift_entries,
            false_positive_entries,
            over_intervention_entries,
            human_rollback_entries,
        ),
        "remaining_gaps": remaining_gaps,
        "readiness_status": readiness_status,
        "post_r20_t04_successor": POST_R20_T04_SUCCESSOR,
    }


def render_markdown(payload: dict[str, Any]) -> str:
    successor = dict(payload.get("post_r20_t04_successor", {}))
    lines = [
        "# R20-T04 autonomous governance ledger",
        "",
        f"- artifact_id: {payload.get('artifact_id', '')}",
        f"- governance_ledger_id: {payload.get('governance_ledger_id', '')}",
        f"- plan_date: {payload.get('plan_date', '')}",
        f"- readiness_status: {payload.get('readiness_status', '')}",
        "",
        "## Governance summary",
        "",
        f"- strategy_drift_entries: {len(list(payload.get('strategy_drift_entries', [])))}",
        f"- false_positive_entries: {len(list(payload.get('false_positive_entries', [])))}",
        f"- over_intervention_entries: {len(list(payload.get('over_intervention_entries', [])))}",
        f"- human_rollback_entries: {len(list(payload.get('human_rollback_entries', [])))}",
        f"- policy_adjustment_trace: {len(list(payload.get('policy_adjustment_trace', [])))}",
        "",
        "## Post-R20-T04 successor",
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
    payload = build_payload(index_root, args.plan_date, args.governance_json)
    save_json(index_root / ARTIFACT_JSON, payload)
    save_text(index_root / ARTIFACT_MD, render_markdown(payload))
    result = {
        "artifact_id": payload["artifact_id"],
        "governance_ledger_id": payload["governance_ledger_id"],
        "plan_date": payload["plan_date"],
        "policy_adjustment_policy": payload["policy_adjustment_policy"],
        "strategy_drift_entries": payload["strategy_drift_entries"],
        "false_positive_entries": payload["false_positive_entries"],
        "over_intervention_entries": payload["over_intervention_entries"],
        "human_rollback_entries": payload["human_rollback_entries"],
        "policy_adjustment_trace": payload["policy_adjustment_trace"],
        "readiness_status": payload["readiness_status"],
        "post_r20_t04_successor": payload["post_r20_t04_successor"],
    }
    if args.format == "json":
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
