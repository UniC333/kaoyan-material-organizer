#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from build_autonomous_action_plan import ARTIFACT_JSON as ACTION_PLAN_JSON
from build_autonomous_governance_ledger import ARTIFACT_JSON as GOVERNANCE_LEDGER_JSON
from build_autonomous_trigger_contract import ARTIFACT_JSON as TRIGGER_CONTRACT_JSON
from common import INDEX_DIRNAME, default_vault_root_arg, load_json_or_default, save_json, save_text
from kaoyan_kb.domain.artifact_support import dedupe_strings as _dedupe_strings
from kaoyan_kb.domain.artifact_support import load_required_artifact as _load_artifact
from kaoyan_kb.domain.artifact_support import status_from_readiness as _status_from_readiness

ARTIFACT_JSON = "44_r20_autonomous_tutoring_acceptance_artifact.json"
ARTIFACT_MD = "44_r20_autonomous_tutoring_acceptance_artifact.md"
ARTIFACT_ID = "r20-autonomous-tutoring-acceptance"
ARTIFACT_CONTRACT_VERSION = "r20.autonomous-tutoring-acceptance.v1"
POST_R20_SUCCESSOR = {
    "track_id": "R21-T00",
    "title": "storage/index scalability acceptance planning gate",
    "scope": "autonomous tutoring acceptance -> storage/index scalability acceptance -> operating range expansion",
    "machine_readable_entry_point": "R21-T00 -> M12-T00",
    "status": "defined_not_started",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--vault-root", default=default_vault_root_arg())
    parser.add_argument("--plan-date", required=True)
    parser.add_argument("--format", choices=("json", "quiet"), default="json")
    return parser.parse_args()


def build_payload(index_root: Path, plan_date: str) -> dict[str, Any]:
    trigger_contract = _load_artifact(index_root, TRIGGER_CONTRACT_JSON)
    action_plan = _load_artifact(index_root, ACTION_PLAN_JSON)
    governance_ledger = _load_artifact(index_root, GOVERNANCE_LEDGER_JSON)

    initiative_summary = dict(trigger_contract.get("initiative_eligibility_summary", {}))
    initiative_readiness = {
        "status": _status_from_readiness(
            str(trigger_contract.get("readiness_status", "")).strip(),
            "ready-for-r20-t03",
        ),
        "auto_executable_count": int(initiative_summary.get("auto_executable_count", 0) or 0),
        "approval_required_count": int(initiative_summary.get("approval_required_count", 0) or 0),
        "review_only_count": int(initiative_summary.get("review_only_count", 0) or 0),
        "blocked_count": int(initiative_summary.get("blocked_count", 0) or 0),
    }

    action_plan_readiness = {
        "status": _status_from_readiness(
            str(action_plan.get("readiness_status", "")).strip(),
            "ready-for-r20-t04",
        ),
        "auto_executable_actions": len(list(action_plan.get("auto_executable_actions", []))),
        "approval_required_actions": len(list(action_plan.get("approval_required_actions", []))),
        "review_only_actions": len(list(action_plan.get("review_only_actions", []))),
        "blocked_actions": len(list(action_plan.get("blocked_actions", []))),
        "preserve_human_owned_edits": bool(
            dict(action_plan.get("action_planning_policy", {})).get("preserve_human_owned_edits", False)
        ),
        "fact_writeback_allowed": bool(
            dict(action_plan.get("action_planning_policy", {})).get("fact_writeback_allowed", False)
        ),
    }

    policy_adjustment_policy = dict(governance_ledger.get("policy_adjustment_policy", {}))
    governance_readiness = {
        "status": _status_from_readiness(
            str(governance_ledger.get("readiness_status", "")).strip(),
            "ready-for-r20-t05",
        ),
        "strategy_drift_entries": len(list(governance_ledger.get("strategy_drift_entries", []))),
        "false_positive_entries": len(list(governance_ledger.get("false_positive_entries", []))),
        "over_intervention_entries": len(list(governance_ledger.get("over_intervention_entries", []))),
        "human_rollback_entries": len(list(governance_ledger.get("human_rollback_entries", []))),
        "policy_adjustment_trace": len(list(governance_ledger.get("policy_adjustment_trace", []))),
        "rollback_authority": str(policy_adjustment_policy.get("rollback_authority", "")).strip(),
        "preserve_human_owned_edits": bool(policy_adjustment_policy.get("preserve_human_owned_edits", False)),
        "fact_writeback_allowed": bool(policy_adjustment_policy.get("fact_writeback_allowed", False)),
    }

    remaining_gaps = _dedupe_strings(
        [str(item) for item in list(trigger_contract.get("remaining_gaps", []))]
        + [str(item) for item in list(action_plan.get("remaining_gaps", []))]
        + [str(item) for item in list(governance_ledger.get("remaining_gaps", []))]
    ) + [
        "Autonomous tutoring acceptance still only covers the current formal scope and current autonomous-governance chain, not a finished all-subject autonomous tutoring system.",
        "Post-R20 work still needs storage/index scalability acceptance before operating-range expansion can be treated as formally ready.",
    ]

    readiness_status = (
        "ready-for-r21-t00"
        if initiative_readiness["status"] == "accepted"
        and action_plan_readiness["status"] == "accepted"
        and governance_readiness["status"] == "accepted"
        and governance_readiness["rollback_authority"] == "human_operator_only"
        else "not-ready-for-r21-t00"
    )

    return {
        "artifact_contract_version": ARTIFACT_CONTRACT_VERSION,
        "artifact_id": ARTIFACT_ID,
        "plan_date": plan_date,
        "scope": "initiative eligibility -> autonomous action planning -> governance ledger -> autonomous tutoring acceptance",
        "input_contract_refs": [
            {"name": "r20_t02_autonomous_trigger_contract", "version": trigger_contract.get("artifact_contract_version", "")},
            {"name": "r20_t03_autonomous_action_plan", "version": action_plan.get("artifact_contract_version", "")},
            {"name": "r20_t04_autonomous_governance_ledger", "version": governance_ledger.get("artifact_contract_version", "")},
        ],
        "initiative_readiness": initiative_readiness,
        "action_plan_readiness": action_plan_readiness,
        "governance_readiness": governance_readiness,
        "remaining_gaps": remaining_gaps,
        "readiness_status": readiness_status,
        "post_r20_successor": POST_R20_SUCCESSOR,
    }


def render_markdown(payload: dict[str, Any]) -> str:
    initiative_readiness = dict(payload.get("initiative_readiness", {}))
    action_plan_readiness = dict(payload.get("action_plan_readiness", {}))
    governance_readiness = dict(payload.get("governance_readiness", {}))
    successor = dict(payload.get("post_r20_successor", {}))
    lines = [
        "# R20-T05 autonomous tutoring acceptance artifact",
        "",
        f"- artifact_id: {payload.get('artifact_id', '')}",
        f"- plan_date: {payload.get('plan_date', '')}",
        f"- readiness_status: {payload.get('readiness_status', '')}",
        "",
        "## Acceptance summary",
        "",
        f"- initiative_readiness_status: {initiative_readiness.get('status', '')}",
        f"- action_plan_readiness_status: {action_plan_readiness.get('status', '')}",
        f"- governance_readiness_status: {governance_readiness.get('status', '')}",
        "",
        "## Post-R20 successor",
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
        "initiative_readiness": payload["initiative_readiness"],
        "action_plan_readiness": payload["action_plan_readiness"],
        "governance_readiness": payload["governance_readiness"],
        "remaining_gaps": payload["remaining_gaps"],
        "readiness_status": payload["readiness_status"],
        "post_r20_successor": payload["post_r20_successor"],
    }
    if args.format == "json":
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
