#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from build_study_orchestration_context import ARTIFACT_JSON as ORCHESTRATION_CONTEXT_JSON
from common import INDEX_DIRNAME, default_vault_root_arg, learner_file_map, load_json_or_default, save_json, save_text
from learner_events import load_events

ARTIFACT_JSON = "27_r17_daily_study_card.json"
ARTIFACT_MD = "27_r17_daily_study_card.md"
ARTIFACT_ID = "r17-daily-study-card"
ARTIFACT_CONTRACT_VERSION = "r17.daily-study-card.v1"
POST_R17_T03_SUCCESSOR = {
    "track_id": "R17-T04",
    "title": "review loop, weak-point follow-up, and explanation boundary",
    "scope": "daily study card -> review loop -> explanation refs",
    "machine_readable_entry_point": "R17-T04 -> M8-T04",
    "status": "defined_not_started",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--vault-root", default=default_vault_root_arg())
    parser.add_argument("--plan-date", required=True)
    parser.add_argument("--format", choices=("json", "quiet"), default="json")
    return parser.parse_args()


def _load_context(index_root: Path) -> dict[str, Any]:
    return load_json_or_default(index_root / ORCHESTRATION_CONTEXT_JSON, {})


def _event_map() -> dict[str, dict[str, Any]]:
    return {
        str(event.get("event_id", "")).strip(): event
        for event in load_events()
        if str(event.get("event_id", "")).strip()
    }


def _weak_point_map(learner_model: dict[str, Any]) -> dict[str, dict[str, Any]]:
    weak_points: dict[str, dict[str, Any]] = {}
    for subject, subject_model in dict(learner_model.get("subjects", {})).items():
        for node_id, node_model in dict(subject_model.get("node_mastery", {})).items():
            weak_points[node_id] = {
                "subject": subject,
                "node_id": node_id,
                "title": node_model.get("title", ""),
                "mastery_band": node_model.get("mastery_band", ""),
                "mastery_score": float(node_model.get("mastery_score", 0.0)),
            }
    return weak_points


def _source_refs(event_payload: dict[str, Any], item: dict[str, Any]) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = [{"ref_type": "learner_event", "event_id": item.get("event_id", "")}]
    for reference in list(event_payload.get("references", [])):
        evidence_id = str(reference.get("evidence_id", "")).strip()
        if evidence_id:
            refs.append({"ref_type": "evidence", "evidence_id": evidence_id})
    for node_id in list(item.get("syllabus_node_ids", [])):
        text = str(node_id).strip()
        if text:
            refs.append({"ref_type": "syllabus_node", "node_id": text})
    deduped: list[dict[str, Any]] = []
    seen: set[str] = set()
    for ref in refs:
        marker = json.dumps(ref, ensure_ascii=False, sort_keys=True)
        if marker in seen:
            continue
        seen.add(marker)
        deduped.append(ref)
    return deduped


def _weak_point_refs(item: dict[str, Any], weak_points: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    for node_id in list(item.get("syllabus_node_ids", [])):
        payload = weak_points.get(str(node_id).strip())
        if payload:
            refs.append(payload)
    return refs


def _scope_reason(item: dict[str, Any]) -> str:
    if item.get("recommendation_eligibility") == "out_of_scope":
        return "subject_not_in_current_formal_scope"
    return "subject_in_current_formal_scope"


def _blocked_reasons(item: dict[str, Any]) -> list[str]:
    if item.get("recommendation_eligibility") == "blocked":
        return [str(item.get("eligibility_reason", "")).strip()]
    return []


def _action_type(eligibility: str) -> str:
    mapping = {
        "eligible": "recommended",
        "stale": "review_needed",
        "review_only": "review_needed",
        "blocked": "blocked",
        "out_of_scope": "out_of_scope",
    }
    return mapping.get(eligibility, "blocked")


def _build_action(item: dict[str, Any], event_payload: dict[str, Any], weak_points: dict[str, dict[str, Any]]) -> dict[str, Any]:
    eligibility = str(item.get("recommendation_eligibility", "")).strip()
    question = str(item.get("question", "")).strip()
    return {
        "event_id": item.get("event_id", ""),
        "question": question,
        "subject": item.get("subject", ""),
        "chapter_title": item.get("chapter_title", ""),
        "action_type": _action_type(eligibility),
        "recommendation_eligibility": eligibility,
        "source_refs": _source_refs(event_payload, item),
        "weak_point_refs": _weak_point_refs(item, weak_points),
        "scope_reason": _scope_reason(item),
        "blocked_reasons": _blocked_reasons(item),
        "why_this_action": str(item.get("eligibility_reason", "")).strip(),
    }


def build_payload(index_root: Path, plan_date: str) -> dict[str, Any]:
    orchestration_context = _load_context(index_root)
    learner_model = load_json_or_default(learner_file_map()["learner_model"], {"subjects": {}})
    weak_points = _weak_point_map(learner_model)
    events = _event_map()
    recommended_actions: list[dict[str, Any]] = []
    review_needed_actions: list[dict[str, Any]] = []
    blocked_actions: list[dict[str, Any]] = []
    out_of_scope_actions: list[dict[str, Any]] = []

    for item in list(orchestration_context.get("learner_day_context", {}).get("recommendation_inputs", [])):
        event = events.get(str(item.get("event_id", "")).strip(), {})
        event_payload = dict(event.get("payload", {}))
        action = _build_action(item, event_payload, weak_points)
        if action["action_type"] == "recommended":
            recommended_actions.append(action)
        elif action["action_type"] == "review_needed":
            review_needed_actions.append(action)
        elif action["action_type"] == "out_of_scope":
            out_of_scope_actions.append(action)
        else:
            blocked_actions.append(action)

    remaining_gaps = list(orchestration_context.get("remaining_gaps", []))
    if not recommended_actions:
        remaining_gaps.append("No recommended action is currently available for the plan date.")
    readiness_status = "ready-for-r17-t04" if recommended_actions else "not-ready-for-r17-t04"
    return {
        "artifact_contract_version": ARTIFACT_CONTRACT_VERSION,
        "artifact_id": ARTIFACT_ID,
        "plan_date": plan_date,
        "scope": "learner-day context -> daily recommendation -> study card packaging boundary",
        "input_contract_refs": [
            {
                "name": "r17_t02_orchestration_context",
                "version": orchestration_context.get("artifact_contract_version", ""),
            }
        ],
        "recommended_actions": recommended_actions,
        "review_needed_actions": review_needed_actions,
        "blocked_actions": blocked_actions,
        "out_of_scope_actions": out_of_scope_actions,
        "remaining_gaps": remaining_gaps,
        "readiness_status": readiness_status,
        "post_r17_t03_successor": POST_R17_T03_SUCCESSOR,
    }


def render_markdown(payload: dict[str, Any]) -> str:
    successor = dict(payload.get("post_r17_t03_successor", {}))
    lines = [
        "# R17-T03 daily study card",
        "",
        f"- artifact_id: {payload.get('artifact_id', '')}",
        f"- plan_date: {payload.get('plan_date', '')}",
        f"- readiness_status: {payload.get('readiness_status', '')}",
        "",
        "## Action summary",
        "",
        f"- recommended_actions: {len(list(payload.get('recommended_actions', [])))}",
        f"- review_needed_actions: {len(list(payload.get('review_needed_actions', [])))}",
        f"- blocked_actions: {len(list(payload.get('blocked_actions', [])))}",
        f"- out_of_scope_actions: {len(list(payload.get('out_of_scope_actions', [])))}",
        "",
        "## Post-R17-T03 successor",
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
        "recommended_actions": payload["recommended_actions"],
        "review_needed_actions": payload["review_needed_actions"],
        "blocked_actions": payload["blocked_actions"],
        "out_of_scope_actions": payload["out_of_scope_actions"],
        "readiness_status": payload["readiness_status"],
        "post_r17_t03_successor": payload["post_r17_t03_successor"],
    }
    if args.format == "json":
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
