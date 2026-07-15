#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from build_daily_study_card import ARTIFACT_JSON as DAILY_CARD_JSON
from build_r17_teacher_loop_artifact import ARTIFACT_JSON as R17_TEACHER_LOOP_JSON
from build_review_followups import ARTIFACT_JSON as REVIEW_FOLLOWUPS_JSON
from build_study_orchestration_context import ARTIFACT_JSON as ORCHESTRATION_CONTEXT_JSON
from build_weekly_orchestration import ARTIFACT_JSON as WEEKLY_ORCHESTRATION_JSON
from common import INDEX_DIRNAME, default_vault_root_arg, load_json_or_default, save_json, save_text

ARTIFACT_JSON = "31_r18_adaptive_coaching_context.json"
ARTIFACT_MD = "31_r18_adaptive_coaching_context.md"
ARTIFACT_ID = "r18-adaptive-coaching-context"
ARTIFACT_CONTRACT_VERSION = "r18.adaptive-coaching-context.v1"
POST_R18_T02_SUCCESSOR = {
    "track_id": "R18-T03",
    "title": "adaptive coaching packet and action-priority packaging boundary",
    "scope": "coach-input -> adaptive context -> intervention packet packaging",
    "machine_readable_entry_point": "R18-T03 -> M9-T03",
    "status": "defined_not_started",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--vault-root", default=default_vault_root_arg())
    parser.add_argument("--plan-date", required=True)
    parser.add_argument("--stale-signal-days", type=int, default=21)
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


def _source_refs(item: dict[str, Any], review_item: dict[str, Any] | None) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    for ref in list(item.get("source_refs", [])):
        refs.append(dict(ref))
    if review_item:
        for ref in list(review_item.get("explanation_refs", [])):
            refs.append(dict(ref))
    deduped: list[dict[str, Any]] = []
    seen: set[str] = set()
    for ref in refs:
        marker = json.dumps(ref, ensure_ascii=False, sort_keys=True)
        if marker in seen:
            continue
        seen.add(marker)
        deduped.append(ref)
    return deduped


def _build_eligible(action: dict[str, Any], review_item: dict[str, Any] | None) -> dict[str, Any]:
    question = str(action.get("question", "")).strip()
    return {
        "question": question,
        "subject": action.get("subject", ""),
        "chapter_title": action.get("chapter_title", ""),
        "intervention_eligibility": "eligible",
        "eligibility_reason": "teacher_loop_ready_and_recommended",
        "priority_reason": "recommended_action_inside_current_formal_scope",
        "source_refs": _source_refs(action, review_item),
        "teacher_loop_signal": "recommended_action",
    }


def _build_stale(action: dict[str, Any], review_item: dict[str, Any]) -> dict[str, Any]:
    question = str(action.get("question", "")).strip()
    return {
        "question": question,
        "subject": action.get("subject", ""),
        "chapter_title": action.get("chapter_title", ""),
        "intervention_eligibility": "stale",
        "eligibility_reason": str(review_item.get("follow_up_reason", "")).strip() or "event_outside_freshness_window",
        "priority_reason": "refresh_review_before_adaptive_intervention",
        "source_refs": _source_refs(action, review_item),
        "teacher_loop_signal": "needs_refresh_review",
    }


def _build_review_only(action: dict[str, Any], review_item: dict[str, Any]) -> dict[str, Any]:
    question = str(action.get("question", "")).strip()
    return {
        "question": question,
        "subject": action.get("subject", ""),
        "chapter_title": action.get("chapter_title", ""),
        "intervention_eligibility": "review_only",
        "eligibility_reason": str(review_item.get("follow_up_reason", "")).strip() or "review_only_context_requires_followup",
        "priority_reason": "review_only_signal_requires_human_interpretation",
        "source_refs": _source_refs(action, review_item),
        "teacher_loop_signal": "review_only_insight",
    }


def _build_blocked(
    action: dict[str, Any],
    review_item: dict[str, Any],
    *,
    eligibility: str,
    priority_reason: str,
) -> dict[str, Any]:
    question = str(action.get("question", "")).strip()
    return {
        "question": question,
        "subject": action.get("subject", ""),
        "chapter_title": action.get("chapter_title", ""),
        "intervention_eligibility": eligibility,
        "eligibility_reason": str(review_item.get("follow_up_reason", "")).strip() or str(action.get("scope_reason", "")).strip(),
        "priority_reason": priority_reason,
        "source_refs": _source_refs(action, review_item),
        "teacher_loop_signal": "blocked_follow_up",
    }


def build_payload(index_root: Path, *, plan_date: str, stale_signal_days: int) -> dict[str, Any]:
    teacher_loop = _load_artifact(index_root, R17_TEACHER_LOOP_JSON)
    orchestration = _load_artifact(index_root, ORCHESTRATION_CONTEXT_JSON)
    daily_card = _load_artifact(index_root, DAILY_CARD_JSON)
    review_followups = _load_artifact(index_root, REVIEW_FOLLOWUPS_JSON)
    weekly = _load_artifact(index_root, WEEKLY_ORCHESTRATION_JSON)

    scope_filter = dict(orchestration.get("scope_filter", {}))
    formal_inputs = dict(orchestration.get("formal_inputs", {}))
    review_by_question = _question_map(list(review_followups.get("formal_follow_ups", [])))
    review_by_question.update(_question_map(list(review_followups.get("review_only_insights", []))))
    review_by_question.update(_question_map(list(review_followups.get("blocked_follow_ups", []))))

    intervention_inputs: list[dict[str, Any]] = []
    summary = {
        "eligible_count": 0,
        "stale_count": 0,
        "review_only_count": 0,
        "blocked_count": 0,
        "out_of_scope_count": 0,
    }

    for action in list(daily_card.get("recommended_actions", [])):
        question = str(action.get("question", "")).strip()
        item = _build_eligible(action, review_by_question.get(question))
        intervention_inputs.append(item)
        summary["eligible_count"] += 1

    for action in list(daily_card.get("review_needed_actions", [])):
        question = str(action.get("question", "")).strip()
        review_item = review_by_question.get(question, {})
        if str(action.get("recommendation_eligibility", "")).strip() == "stale":
            item = _build_stale(action, review_item)
            summary["stale_count"] += 1
        else:
            item = _build_review_only(action, review_item)
            summary["review_only_count"] += 1
        intervention_inputs.append(item)

    for action in list(daily_card.get("blocked_actions", [])):
        question = str(action.get("question", "")).strip()
        item = _build_blocked(
            action,
            review_by_question.get(question, {}),
            eligibility="blocked",
            priority_reason="blocked_signal_cannot_enter_adaptive_intervention",
        )
        intervention_inputs.append(item)
        summary["blocked_count"] += 1

    for action in list(daily_card.get("out_of_scope_actions", [])):
        question = str(action.get("question", "")).strip()
        item = _build_blocked(
            action,
            review_by_question.get(question, {}),
            eligibility="out_of_scope",
            priority_reason="subject_outside_current_formal_scope",
        )
        intervention_inputs.append(item)
        summary["out_of_scope_count"] += 1

    priority_budget = {
        "recommended_budget": len(list(daily_card.get("recommended_actions", []))),
        "review_budget": len(list(daily_card.get("review_needed_actions", []))),
        "blocked_budget": len(list(daily_card.get("blocked_actions", []))) + len(list(daily_card.get("out_of_scope_actions", []))),
        "operator_override_count": len(list(weekly.get("operator_overrides", []))),
        "manual_lock_count": len(list(weekly.get("locked_manual_edits", []))),
    }

    inherited_gaps = _dedupe_strings(
        [str(item) for item in list(orchestration.get("remaining_gaps", []))]
        + [str(item) for item in list(daily_card.get("remaining_gaps", []))]
        + [str(item) for item in list(review_followups.get("remaining_gaps", []))]
        + [str(item) for item in list(weekly.get("remaining_gaps", []))]
        + [str(item) for item in list(teacher_loop.get("remaining_gaps", []))]
    )
    remaining_gaps = inherited_gaps + [
        "Adaptive coaching context is limited to current teacher-loop outputs and does not yet package intervention actions.",
    ]

    teacher_loop_refs = {
        "teacher_loop_artifact_id": teacher_loop.get("artifact_id", ""),
        "teacher_loop_readiness_status": teacher_loop.get("readiness_status", ""),
        "orchestration_summary_status": dict(teacher_loop.get("orchestration_summary", {})).get("input_status", ""),
        "override_safety_status": dict(teacher_loop.get("override_safety_status", {})).get("status", ""),
    }

    readiness_status = (
        "ready-for-r18-t03"
        if str(teacher_loop.get("readiness_status", "")).strip() == "ready-for-r18-t01"
        and summary["eligible_count"] > 0
        else "not-ready-for-r18-t03"
    )

    return {
        "artifact_contract_version": ARTIFACT_CONTRACT_VERSION,
        "artifact_id": ARTIFACT_ID,
        "plan_date": plan_date,
        "scope": "teacher-loop intake -> adaptive context -> intervention eligibility boundary",
        "input_contract_refs": [
            {"name": "r17_t06_teacher_loop_acceptance_artifact", "version": teacher_loop.get("artifact_contract_version", "")},
            {"name": "r17_t02_orchestration_context", "version": orchestration.get("artifact_contract_version", "")},
            {"name": "r17_t03_daily_study_card", "version": daily_card.get("artifact_contract_version", "")},
            {"name": "r17_t04_review_followups", "version": review_followups.get("artifact_contract_version", "")},
            {"name": "r17_t05_weekly_orchestration", "version": weekly.get("artifact_contract_version", "")},
        ],
        "coach_context_contract_version": ARTIFACT_CONTRACT_VERSION,
        "source_scope": {
            "in_scope_subjects": list(scope_filter.get("in_scope_subjects", [])),
            "out_of_scope_subjects": list(scope_filter.get("out_of_scope_subjects", [])),
        },
        "teacher_loop_refs": teacher_loop_refs,
        "priority_budget": priority_budget,
        "stale_signal_window": {
            "window_days": max(1, stale_signal_days),
            "stale_signal_count": summary["stale_count"],
        },
        "formal_inputs": formal_inputs,
        "adaptive_context": {
            "plan_date": plan_date,
            "intervention_inputs": intervention_inputs,
        },
        "intervention_eligibility_summary": summary,
        "remaining_gaps": remaining_gaps,
        "readiness_status": readiness_status,
        "post_r18_t02_successor": POST_R18_T02_SUCCESSOR,
    }


def render_markdown(payload: dict[str, Any]) -> str:
    teacher_loop_refs = dict(payload.get("teacher_loop_refs", {}))
    summary = dict(payload.get("intervention_eligibility_summary", {}))
    successor = dict(payload.get("post_r18_t02_successor", {}))
    lines = [
        "# R18-T02 adaptive coaching context",
        "",
        f"- artifact_id: {payload.get('artifact_id', '')}",
        f"- plan_date: {payload.get('plan_date', '')}",
        f"- readiness_status: {payload.get('readiness_status', '')}",
        "",
        "## Teacher-loop refs",
        "",
        f"- teacher_loop_artifact_id: {teacher_loop_refs.get('teacher_loop_artifact_id', '')}",
        f"- override_safety_status: {teacher_loop_refs.get('override_safety_status', '')}",
        "",
        "## Intervention eligibility summary",
        "",
        f"- eligible_count: {summary.get('eligible_count', 0)}",
        f"- stale_count: {summary.get('stale_count', 0)}",
        f"- review_only_count: {summary.get('review_only_count', 0)}",
        f"- blocked_count: {summary.get('blocked_count', 0)}",
        f"- out_of_scope_count: {summary.get('out_of_scope_count', 0)}",
        "",
        "## Post-R18-T02 successor",
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
    payload = build_payload(index_root, plan_date=args.plan_date, stale_signal_days=max(1, args.stale_signal_days))
    save_json(index_root / ARTIFACT_JSON, payload)
    save_text(index_root / ARTIFACT_MD, render_markdown(payload))
    result = {
        "artifact_id": payload["artifact_id"],
        "plan_date": payload["plan_date"],
        "priority_budget": payload["priority_budget"],
        "intervention_eligibility_summary": payload["intervention_eligibility_summary"],
        "readiness_status": payload["readiness_status"],
        "post_r18_t02_successor": payload["post_r18_t02_successor"],
    }
    if args.format == "json":
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
