#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from kaoyan_kb.domain.learner_artifact_files import AUTONOMOUS_ACTION_PLAN as ACTION_PLAN_JSON
from kaoyan_kb.domain.learner_artifact_files import AUTONOMOUS_GOVERNANCE_LEDGER as GOVERNANCE_LEDGER_JSON
from kaoyan_kb.domain.learner_artifact_files import AUTONOMOUS_TRIGGER_CONTRACT as TRIGGER_CONTRACT_JSON
from kaoyan_kb.cli.learner_artifact_runner import run_artifact
from common import default_vault_root_arg
from kaoyan_kb.domain.artifact_support import render_acceptance_markdown
from kaoyan_kb.domain.artifact_support import dedupe_strings as _dedupe_strings
from kaoyan_kb.domain.artifact_support import load_required_artifact as _load_artifact
from kaoyan_kb.domain.artifact_support import status_from_readiness as _status_from_readiness

from kaoyan_kb.domain.learner_artifact_files import R20_AUTONOMOUS_TUTORING_ARTIFACT as ARTIFACT_JSON
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
    return render_acceptance_markdown(
        payload,
        title='# R20-T05 autonomous tutoring acceptance artifact', summary_title='## Acceptance summary',
        summary_fields=(
            ('initiative_readiness_status', 'initiative_readiness', 'status'),
            ('action_plan_readiness_status', 'action_plan_readiness', 'status'),
            ('governance_readiness_status', 'governance_readiness', 'status'),
        ),
        successor_key='post_r20_successor', successor_title='## Post-R20 successor',
    )


def main() -> int:
    return run_artifact(
        parse_args,
        lambda index_root, args: build_payload(index_root, args.plan_date),
        render_markdown, ARTIFACT_JSON, ARTIFACT_MD,
        (
            'artifact_id', 'plan_date', 'initiative_readiness', 'action_plan_readiness',
            'governance_readiness', 'remaining_gaps', 'readiness_status', 'post_r20_successor',
        ),
    )


if __name__ == "__main__":
    raise SystemExit(main())
