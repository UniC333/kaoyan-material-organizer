#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from kaoyan_kb.domain.learner_artifact_files import LONG_HORIZON_OPERATIONS as LONG_HORIZON_OPERATIONS_JSON
from kaoyan_kb.domain.learner_artifact_files import LONGITUDINAL_TUTORING_CONTEXT as LONGITUDINAL_CONTEXT_JSON
from kaoyan_kb.domain.learner_artifact_files import TUTORING_FEEDBACK_LOOP as TUTORING_FEEDBACK_JSON
from kaoyan_kb.domain.learner_artifact_files import TUTORING_STRATEGY_PACKET as STRATEGY_PACKET_JSON
from kaoyan_kb.cli.learner_artifact_runner import run_artifact
from common import default_vault_root_arg
from kaoyan_kb.domain.artifact_support import render_acceptance_markdown
from kaoyan_kb.domain.artifact_support import dedupe_strings as _dedupe_strings
from kaoyan_kb.domain.artifact_support import load_required_artifact as _load_artifact
from kaoyan_kb.domain.artifact_support import status_from_readiness as _status_from_readiness

from kaoyan_kb.domain.learner_artifact_files import R19_LONGITUDINAL_TUTORING_ARTIFACT as ARTIFACT_JSON
ARTIFACT_MD = "40_r19_longitudinal_tutoring_acceptance_artifact.md"
ARTIFACT_ID = "r19-longitudinal-tutoring-acceptance"
ARTIFACT_CONTRACT_VERSION = "r19.longitudinal-tutoring-acceptance.v1"
POST_R19_SUCCESSOR = {
    "track_id": "R20-T01",
    "title": "autonomous tutoring and self-evolving study governance reset",
    "scope": "longitudinal tutoring -> autonomous tutoring -> self-evolving study governance",
    "machine_readable_entry_point": "R20-T01 -> M11-T01",
    "status": "defined_not_started",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--vault-root", default=default_vault_root_arg())
    parser.add_argument("--plan-date", required=True)
    parser.add_argument("--format", choices=("json", "quiet"), default="json")
    return parser.parse_args()


def build_payload(index_root: Path, plan_date: str) -> dict[str, Any]:
    longitudinal_context = _load_artifact(index_root, LONGITUDINAL_CONTEXT_JSON)
    strategy_packet = _load_artifact(index_root, STRATEGY_PACKET_JSON)
    tutoring_feedback = _load_artifact(index_root, TUTORING_FEEDBACK_JSON)
    long_horizon_operations = _load_artifact(index_root, LONG_HORIZON_OPERATIONS_JSON)

    eligibility_summary = dict(longitudinal_context.get("goal_adaptation_eligibility_summary", {}))
    tutoring_summary = {
        "status": _status_from_readiness(
            str(longitudinal_context.get("readiness_status", "")).strip(),
            "ready-for-r19-t03",
        ),
        "eligible_long_horizon_signals": int(eligibility_summary.get("eligible_count", 0) or 0),
        "review_only_or_stale_signals": int(eligibility_summary.get("review_only_count", 0) or 0)
        + int(eligibility_summary.get("stale_cycle_count", 0) or 0),
        "blocked_or_out_of_scope_signals": int(eligibility_summary.get("blocked_count", 0) or 0)
        + int(eligibility_summary.get("out_of_scope_count", 0) or 0),
        "in_scope_subjects": list(longitudinal_context.get("source_scope", {}).get("in_scope_subjects", [])),
        "out_of_scope_subjects": list(longitudinal_context.get("source_scope", {}).get("out_of_scope_subjects", [])),
    }

    strategy_readiness = {
        "status": _status_from_readiness(
            str(strategy_packet.get("readiness_status", "")).strip(),
            "ready-for-r19-t04",
        ),
        "recommended_strategies": len(list(strategy_packet.get("recommended_strategies", []))),
        "review_needed_strategies": len(list(strategy_packet.get("review_needed_strategies", []))),
        "blocked_strategies": len(list(strategy_packet.get("blocked_strategies", []))),
        "out_of_scope_strategies": len(list(strategy_packet.get("out_of_scope_strategies", []))),
    }

    cycle_feedback_status = {
        "status": _status_from_readiness(
            str(tutoring_feedback.get("readiness_status", "")).strip(),
            "ready-for-r19-t05",
        ),
        "formal_cycle_feedback": len(list(tutoring_feedback.get("formal_cycle_feedback", []))),
        "review_only_feedback": len(list(tutoring_feedback.get("review_only_feedback", []))),
        "out_of_scope_feedback": len(list(tutoring_feedback.get("out_of_scope_feedback", []))),
        "blocked_follow_ups": len(list(tutoring_feedback.get("blocked_follow_ups", []))),
        "fact_writeback_allowed": bool(tutoring_feedback.get("fact_writeback_allowed", False)),
    }

    adjustment_policy = dict(long_horizon_operations.get("goal_adjustment_policy", {}))
    goal_adjustment_safety_status = {
        "status": (
            "accepted"
            if str(long_horizon_operations.get("readiness_status", "")).strip() == "ready-for-r19-t06"
            and bool(adjustment_policy.get("preserve_human_owned_edits", False))
            and not bool(adjustment_policy.get("fact_writeback_allowed", True))
            else "not-yet-accepted"
        ),
        "preserve_human_owned_edits": bool(adjustment_policy.get("preserve_human_owned_edits", False)),
        "fact_writeback_allowed": bool(adjustment_policy.get("fact_writeback_allowed", False)),
        "long_horizon_operations": len(list(long_horizon_operations.get("long_horizon_operations", []))),
        "operator_overrides": len(list(long_horizon_operations.get("operator_overrides", []))),
        "locked_manual_edits": len(list(long_horizon_operations.get("locked_manual_edits", []))),
        "scope_blocked_operations": len(list(long_horizon_operations.get("scope_blocked_operations", []))),
    }

    inherited_gaps = _dedupe_strings(
        [str(item) for item in list(longitudinal_context.get("remaining_gaps", []))]
        + [str(item) for item in list(strategy_packet.get("remaining_gaps", []))]
        + [str(item) for item in list(tutoring_feedback.get("remaining_gaps", []))]
        + [str(item) for item in list(long_horizon_operations.get("remaining_gaps", []))]
    )
    remaining_gaps = inherited_gaps + [
        "Longitudinal-tutoring acceptance still only covers the current formal scope and current intake chain, not a finished all-subject autonomous tutoring system.",
        "Post-R19 work still needs autonomous tutoring and self-evolving study governance implementation on top of the accepted longitudinal-tutoring loop.",
    ]

    readiness_status = (
        "ready-for-r20-t01"
        if tutoring_summary["status"] == "accepted"
        and strategy_readiness["status"] == "accepted"
        and cycle_feedback_status["status"] == "accepted"
        and goal_adjustment_safety_status["status"] == "accepted"
        else "not-ready-for-r20-t01"
    )

    return {
        "artifact_contract_version": ARTIFACT_CONTRACT_VERSION,
        "artifact_id": ARTIFACT_ID,
        "plan_date": plan_date,
        "scope": "long-horizon profile -> strategy packet -> cycle feedback -> goal-adjustment safety -> longitudinal tutoring acceptance",
        "input_contract_refs": [
            {"name": "r19_t02_longitudinal_tutoring_context", "version": longitudinal_context.get("artifact_contract_version", "")},
            {"name": "r19_t03_tutoring_strategy_packet", "version": strategy_packet.get("artifact_contract_version", "")},
            {"name": "r19_t04_tutoring_feedback_loop", "version": tutoring_feedback.get("artifact_contract_version", "")},
            {"name": "r19_t05_long_horizon_operations", "version": long_horizon_operations.get("artifact_contract_version", "")},
        ],
        "tutoring_summary": tutoring_summary,
        "strategy_readiness": strategy_readiness,
        "cycle_feedback_status": cycle_feedback_status,
        "goal_adjustment_safety_status": goal_adjustment_safety_status,
        "remaining_gaps": remaining_gaps,
        "readiness_status": readiness_status,
        "post_r19_successor": POST_R19_SUCCESSOR,
    }


def render_markdown(payload: dict[str, Any]) -> str:
    return render_acceptance_markdown(
        payload,
        title='# R19-T06 longitudinal tutoring acceptance artifact', summary_title='## Acceptance summary',
        summary_fields=(
            ('tutoring_summary_status', 'tutoring_summary', 'status'),
            ('strategy_readiness_status', 'strategy_readiness', 'status'),
            ('cycle_feedback_status', 'cycle_feedback_status', 'status'),
            ('goal_adjustment_safety_status', 'goal_adjustment_safety_status', 'status'),
        ),
        successor_key='post_r19_successor', successor_title='## Post-R19 successor',
    )


def main() -> int:
    return run_artifact(
        parse_args,
        lambda index_root, args: build_payload(index_root, args.plan_date),
        render_markdown, ARTIFACT_JSON, ARTIFACT_MD,
        (
            'artifact_id', 'plan_date', 'tutoring_summary', 'strategy_readiness',
            'cycle_feedback_status', 'goal_adjustment_safety_status', 'remaining_gaps',
            'readiness_status', 'post_r19_successor',
        ),
    )


if __name__ == "__main__":
    raise SystemExit(main())
