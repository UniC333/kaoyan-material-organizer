#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from kaoyan_kb.domain.learner_artifact_files import ADAPTIVE_COACHING_CONTEXT as ADAPTIVE_CONTEXT_JSON
from kaoyan_kb.domain.learner_artifact_files import ADAPTIVE_COACHING_PACKET as ADAPTIVE_PACKET_JSON
from kaoyan_kb.domain.learner_artifact_files import CLOSED_LOOP_OPERATIONS as CLOSED_LOOP_JSON
from kaoyan_kb.domain.learner_artifact_files import COACHING_FEEDBACK_LOOP as FEEDBACK_LOOP_JSON
from kaoyan_kb.cli.learner_artifact_runner import run_artifact
from common import default_vault_root_arg
from kaoyan_kb.domain.artifact_support import render_acceptance_markdown
from kaoyan_kb.domain.artifact_support import dedupe_strings as _dedupe_strings
from kaoyan_kb.domain.artifact_support import load_required_artifact as _load_artifact
from kaoyan_kb.domain.artifact_support import status_from_readiness as _status_from_readiness

from kaoyan_kb.domain.learner_artifact_files import R18_ADAPTIVE_COACHING_ARTIFACT as ARTIFACT_JSON
ARTIFACT_MD = "35_r18_adaptive_coaching_acceptance_artifact.md"
ARTIFACT_ID = "r18-adaptive-coaching-acceptance"
ARTIFACT_CONTRACT_VERSION = "r18.adaptive-coaching-acceptance.v1"
POST_R18_SUCCESSOR = {
    "track_id": "R19-T01",
    "title": "longitudinal tutoring and goal-adaptation reset",
    "scope": "adaptive coaching -> longitudinal tutoring -> goal-adaptation",
    "machine_readable_entry_point": "R19-T01 -> M10-T01",
    "status": "defined_not_started",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--vault-root", default=default_vault_root_arg())
    parser.add_argument("--plan-date", required=True)
    parser.add_argument("--format", choices=("json", "quiet"), default="json")
    return parser.parse_args()


def build_payload(index_root: Path, plan_date: str) -> dict[str, Any]:
    adaptive_context = _load_artifact(index_root, ADAPTIVE_CONTEXT_JSON)
    adaptive_packet = _load_artifact(index_root, ADAPTIVE_PACKET_JSON)
    feedback_loop = _load_artifact(index_root, FEEDBACK_LOOP_JSON)
    closed_loop = _load_artifact(index_root, CLOSED_LOOP_JSON)

    context_summary = dict(adaptive_context.get("intervention_eligibility_summary", {}))
    coaching_summary = {
        "status": _status_from_readiness(
            str(adaptive_context.get("readiness_status", "")).strip(),
            "ready-for-r18-t03",
        ),
        "eligible_interventions": int(context_summary.get("eligible_count", 0) or 0),
        "review_only_or_stale_signals": int(context_summary.get("stale_count", 0) or 0)
        + int(context_summary.get("review_only_count", 0) or 0),
        "blocked_or_out_of_scope_signals": int(context_summary.get("blocked_count", 0) or 0)
        + int(context_summary.get("out_of_scope_count", 0) or 0),
        "in_scope_subjects": list(adaptive_context.get("source_scope", {}).get("in_scope_subjects", [])),
        "out_of_scope_subjects": list(adaptive_context.get("source_scope", {}).get("out_of_scope_subjects", [])),
    }

    packet_readiness = {
        "status": _status_from_readiness(
            str(adaptive_packet.get("readiness_status", "")).strip(),
            "ready-for-r18-t04",
        ),
        "recommended_interventions": len(list(adaptive_packet.get("recommended_interventions", []))),
        "review_needed_interventions": len(list(adaptive_packet.get("review_needed_interventions", []))),
        "blocked_interventions": len(list(adaptive_packet.get("blocked_interventions", []))),
        "out_of_scope_interventions": len(list(adaptive_packet.get("out_of_scope_interventions", []))),
    }

    feedback_loop_status = {
        "status": _status_from_readiness(
            str(feedback_loop.get("readiness_status", "")).strip(),
            "ready-for-r18-t05",
        ),
        "formal_feedback_intake": len(list(feedback_loop.get("formal_feedback_intake", []))),
        "review_only_feedback": len(list(feedback_loop.get("review_only_feedback", []))),
        "out_of_scope_feedback": len(list(feedback_loop.get("out_of_scope_feedback", []))),
        "blocked_follow_ups": len(list(feedback_loop.get("blocked_follow_ups", []))),
        "fact_writeback_allowed": bool(feedback_loop.get("fact_writeback_allowed", False)),
    }

    override_policy = dict(closed_loop.get("operator_override_policy", {}))
    cadence_safety_status = {
        "status": (
            "accepted"
            if str(closed_loop.get("readiness_status", "")).strip() == "ready-for-r18-t06"
            and bool(override_policy.get("preserve_human_owned_edits", False))
            and not bool(override_policy.get("fact_writeback_allowed", True))
            else "not-yet-accepted"
        ),
        "preserve_human_owned_edits": bool(override_policy.get("preserve_human_owned_edits", False)),
        "fact_writeback_allowed": bool(override_policy.get("fact_writeback_allowed", False)),
        "auto_adjustable_operations": len(list(closed_loop.get("auto_adjustable_operations", []))),
        "operator_overrides": len(list(closed_loop.get("operator_overrides", []))),
        "locked_manual_edits": len(list(closed_loop.get("locked_manual_edits", []))),
        "scope_blocked_operations": len(list(closed_loop.get("scope_blocked_operations", []))),
    }

    inherited_gaps = _dedupe_strings(
        [str(item) for item in list(adaptive_context.get("remaining_gaps", []))]
        + [str(item) for item in list(adaptive_packet.get("remaining_gaps", []))]
        + [str(item) for item in list(feedback_loop.get("remaining_gaps", []))]
        + [str(item) for item in list(closed_loop.get("remaining_gaps", []))]
    )
    remaining_gaps = inherited_gaps + [
        "Adaptive-coaching ready only covers the current formal scope and current intake chain, not a finished all-subject longitudinal tutoring system.",
        "Post-R18 work still needs longitudinal tutoring and goal-adaptation implementation on top of the accepted adaptive-coaching loop.",
    ]

    readiness_status = (
        "ready-for-r19-t01"
        if coaching_summary["status"] == "accepted"
        and packet_readiness["status"] == "accepted"
        and feedback_loop_status["status"] == "accepted"
        and cadence_safety_status["status"] == "accepted"
        else "not-ready-for-r19-t01"
    )

    return {
        "artifact_contract_version": ARTIFACT_CONTRACT_VERSION,
        "artifact_id": ARTIFACT_ID,
        "plan_date": plan_date,
        "scope": "coach-input -> coaching packet -> feedback absorption -> cadence safety -> adaptive-coaching acceptance",
        "input_contract_refs": [
            {"name": "r18_t02_adaptive_coaching_context", "version": adaptive_context.get("artifact_contract_version", "")},
            {"name": "r18_t03_adaptive_coaching_packet", "version": adaptive_packet.get("artifact_contract_version", "")},
            {"name": "r18_t04_coaching_feedback_loop", "version": feedback_loop.get("artifact_contract_version", "")},
            {"name": "r18_t05_closed_loop_operations", "version": closed_loop.get("artifact_contract_version", "")},
        ],
        "coaching_summary": coaching_summary,
        "packet_readiness": packet_readiness,
        "feedback_loop_status": feedback_loop_status,
        "cadence_safety_status": cadence_safety_status,
        "remaining_gaps": remaining_gaps,
        "readiness_status": readiness_status,
        "post_r18_successor": POST_R18_SUCCESSOR,
    }


def render_markdown(payload: dict[str, Any]) -> str:
    return render_acceptance_markdown(
        payload,
        title='# R18-T06 adaptive coaching acceptance artifact', summary_title='## Acceptance summary',
        summary_fields=(
            ('coaching_summary_status', 'coaching_summary', 'status'),
            ('packet_readiness_status', 'packet_readiness', 'status'),
            ('feedback_loop_status', 'feedback_loop_status', 'status'),
            ('cadence_safety_status', 'cadence_safety_status', 'status'),
        ),
        successor_key='post_r18_successor', successor_title='## Post-R18 successor',
    )


def main() -> int:
    return run_artifact(
        parse_args,
        lambda index_root, args: build_payload(index_root, args.plan_date),
        render_markdown, ARTIFACT_JSON, ARTIFACT_MD,
        (
            'artifact_id', 'plan_date', 'coaching_summary', 'packet_readiness',
            'feedback_loop_status', 'cadence_safety_status', 'remaining_gaps', 'readiness_status',
            'post_r18_successor',
        ),
    )


if __name__ == "__main__":
    raise SystemExit(main())
