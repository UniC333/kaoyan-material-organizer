#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from build_daily_study_card import ARTIFACT_JSON as DAILY_CARD_JSON
from build_review_followups import ARTIFACT_JSON as REVIEW_FOLLOWUPS_JSON
from build_study_orchestration_context import ARTIFACT_JSON as ORCHESTRATION_CONTEXT_JSON
from build_weekly_orchestration import ARTIFACT_JSON as WEEKLY_ORCHESTRATION_JSON
from common import INDEX_DIRNAME, default_vault_root_arg, load_json_or_default, save_json, save_text
from kaoyan_kb.domain.artifact_support import dedupe_strings as _dedupe_strings
from kaoyan_kb.domain.artifact_support import load_required_artifact as _load_artifact
from kaoyan_kb.domain.artifact_support import status_from_readiness as _status_from_readiness

ARTIFACT_JSON = "30_r17_teacher_loop_acceptance_artifact.json"
ARTIFACT_MD = "30_r17_teacher_loop_acceptance_artifact.md"
ARTIFACT_ID = "r17-teacher-loop-intake-acceptance"
ARTIFACT_CONTRACT_VERSION = "r17.teacher-loop-intake.v1"
POST_R17_SUCCESSOR = {
    "track_id": "R18-T01",
    "title": "adaptive coaching and closed-loop study operations reset",
    "scope": "teacher-loop intake -> adaptive coaching -> closed-loop study operations",
    "machine_readable_entry_point": "R18-T01 -> M9-T01",
    "status": "defined_not_started",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--vault-root", default=default_vault_root_arg())
    parser.add_argument("--plan-date", required=True)
    parser.add_argument("--format", choices=("json", "quiet"), default="json")
    return parser.parse_args()


def build_payload(index_root: Path, plan_date: str) -> dict[str, Any]:
    orchestration = _load_artifact(index_root, ORCHESTRATION_CONTEXT_JSON)
    daily_card = _load_artifact(index_root, DAILY_CARD_JSON)
    review_followups = _load_artifact(index_root, REVIEW_FOLLOWUPS_JSON)
    weekly = _load_artifact(index_root, WEEKLY_ORCHESTRATION_JSON)

    recommendation_inputs = list(orchestration.get("learner_day_context", {}).get("recommendation_inputs", []))
    orchestration_summary = {
        "input_status": _status_from_readiness(
            str(orchestration.get("readiness_status", "")).strip(),
            "ready-for-r17-t03",
        ),
        "as_of": orchestration.get("as_of", ""),
        "freshness_window_days": orchestration.get("freshness_window_days", 0),
        "recommendation_input_count": len(recommendation_inputs),
        "recommended_action_candidates": sum(
            1 for item in recommendation_inputs if str(item.get("recommendation_eligibility", "")).strip() == "eligible"
        ),
        "review_needed_candidates": sum(
            1
            for item in recommendation_inputs
            if str(item.get("recommendation_eligibility", "")).strip() in {"stale", "review_only"}
        ),
        "blocked_action_candidates": sum(
            1
            for item in recommendation_inputs
            if str(item.get("recommendation_eligibility", "")).strip() in {"blocked", "out_of_scope"}
        ),
    }

    daily_card_readiness = {
        "status": _status_from_readiness(
            str(daily_card.get("readiness_status", "")).strip(),
            "ready-for-r17-t04",
        ),
        "recommended_actions": len(list(daily_card.get("recommended_actions", []))),
        "review_needed_actions": len(list(daily_card.get("review_needed_actions", []))),
        "blocked_actions": len(list(daily_card.get("blocked_actions", []))),
        "out_of_scope_actions": len(list(daily_card.get("out_of_scope_actions", []))),
    }

    review_loop_status = {
        "status": _status_from_readiness(
            str(review_followups.get("readiness_status", "")).strip(),
            "ready-for-r17-t05",
        ),
        "formal_follow_ups": len(list(review_followups.get("formal_follow_ups", []))),
        "review_only_insights": len(list(review_followups.get("review_only_insights", []))),
        "blocked_follow_ups": len(list(review_followups.get("blocked_follow_ups", []))),
        "fact_writeback_allowed": bool(review_followups.get("fact_writeback_allowed", False)),
    }

    override_policy = dict(weekly.get("operator_override_policy", {}))
    override_safety_status = {
        "status": (
            "accepted"
            if str(weekly.get("readiness_status", "")).strip() == "ready-for-r17-t06"
            and bool(override_policy.get("preserve_human_owned_edits", False))
            and not bool(override_policy.get("fact_writeback_allowed", True))
            else "not-yet-accepted"
        ),
        "preserve_human_owned_edits": bool(override_policy.get("preserve_human_owned_edits", False)),
        "fact_writeback_allowed": bool(override_policy.get("fact_writeback_allowed", False)),
        "auto_reschedulable_actions": len(list(weekly.get("auto_reschedulable_actions", []))),
        "operator_overrides": len(list(weekly.get("operator_overrides", []))),
        "locked_manual_edits": len(list(weekly.get("locked_manual_edits", []))),
        "scope_blocked_actions": len(list(weekly.get("scope_blocked_actions", []))),
    }

    inherited_gaps = _dedupe_strings(
        [str(item) for item in list(orchestration.get("remaining_gaps", []))]
        + [str(item) for item in list(daily_card.get("remaining_gaps", []))]
        + [str(item) for item in list(review_followups.get("remaining_gaps", []))]
        + [str(item) for item in list(weekly.get("remaining_gaps", []))]
    )
    remaining_gaps = inherited_gaps + [
        "teacher-loop intake readiness is limited to the current formal scope and current learner-input chain, not full adaptive coaching.",
        "Post-R17 work still needs adaptive coaching and closed-loop study operations instead of reopening orchestration intake.",
    ]

    readiness_status = (
        "ready-for-r18-t01"
        if orchestration_summary["input_status"] == "accepted"
        and daily_card_readiness["status"] == "accepted"
        and review_loop_status["status"] == "accepted"
        and override_safety_status["status"] == "accepted"
        else "not-ready-for-r18-t01"
    )

    return {
        "artifact_contract_version": ARTIFACT_CONTRACT_VERSION,
        "artifact_id": ARTIFACT_ID,
        "plan_date": plan_date,
        "scope": "orchestration input -> daily recommendation -> review loop -> override safety -> teacher-loop intake acceptance",
        "input_contract_refs": [
            {"name": "r17_t02_orchestration_context", "version": orchestration.get("artifact_contract_version", "")},
            {"name": "r17_t03_daily_study_card", "version": daily_card.get("artifact_contract_version", "")},
            {"name": "r17_t04_review_followups", "version": review_followups.get("artifact_contract_version", "")},
            {"name": "r17_t05_weekly_orchestration", "version": weekly.get("artifact_contract_version", "")},
        ],
        "orchestration_summary": orchestration_summary,
        "daily_card_readiness": daily_card_readiness,
        "review_loop_status": review_loop_status,
        "override_safety_status": override_safety_status,
        "remaining_gaps": remaining_gaps,
        "readiness_status": readiness_status,
        "post_r17_successor": POST_R17_SUCCESSOR,
    }


def render_markdown(payload: dict[str, Any]) -> str:
    orchestration_summary = dict(payload.get("orchestration_summary", {}))
    daily_card_readiness = dict(payload.get("daily_card_readiness", {}))
    review_loop_status = dict(payload.get("review_loop_status", {}))
    override_safety_status = dict(payload.get("override_safety_status", {}))
    successor = dict(payload.get("post_r17_successor", {}))
    lines = [
        "# R17-T06 teacher-loop intake acceptance artifact",
        "",
        f"- artifact_id: {payload.get('artifact_id', '')}",
        f"- plan_date: {payload.get('plan_date', '')}",
        f"- readiness_status: {payload.get('readiness_status', '')}",
        "",
        "## Intake summary",
        "",
        f"- orchestration_input_status: {orchestration_summary.get('input_status', '')}",
        f"- daily_card_status: {daily_card_readiness.get('status', '')}",
        f"- review_loop_status: {review_loop_status.get('status', '')}",
        f"- override_safety_status: {override_safety_status.get('status', '')}",
        "",
        "## Post-R17 successor",
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
        "orchestration_summary": payload["orchestration_summary"],
        "daily_card_readiness": payload["daily_card_readiness"],
        "review_loop_status": payload["review_loop_status"],
        "override_safety_status": payload["override_safety_status"],
        "remaining_gaps": payload["remaining_gaps"],
        "readiness_status": payload["readiness_status"],
        "post_r17_successor": payload["post_r17_successor"],
    }
    if args.format == "json":
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
