#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from build_adaptive_coaching_packet import ARTIFACT_JSON as ADAPTIVE_PACKET_JSON
from common import INDEX_DIRNAME, default_vault_root_arg, save_json, save_text
from learner_events import load_events

ARTIFACT_JSON = "33_r18_coaching_feedback_loop.json"
ARTIFACT_MD = "33_r18_coaching_feedback_loop.md"
ARTIFACT_ID = "r18-coaching-feedback-loop"
ARTIFACT_CONTRACT_VERSION = "r18.coaching-feedback-loop.v1"
POST_R18_T04_SUCCESSOR = {
    "track_id": "R18-T05",
    "title": "closed-loop study operations, cadence adjustment, and human override boundary",
    "scope": "feedback intake -> cadence adjustment -> closed-loop study operations",
    "machine_readable_entry_point": "R18-T05 -> M9-T05",
    "status": "defined_not_started",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--vault-root", default=default_vault_root_arg())
    parser.add_argument("--plan-date", required=True)
    parser.add_argument("--format", choices=("json", "quiet"), default="json")
    return parser.parse_args()


def _load_packet(index_root: Path) -> dict[str, Any]:
    path = index_root / ADAPTIVE_PACKET_JSON
    if not path.exists():
        raise SystemExit("missing adaptive coaching packet artifact; run build_adaptive_coaching_packet.py first")
    return json.loads(path.read_text(encoding="utf-8"))


def _packet_map(packet: dict[str, Any]) -> dict[str, dict[str, Any]]:
    items: dict[str, dict[str, Any]] = {}
    for key in (
        "recommended_interventions",
        "review_needed_interventions",
        "blocked_interventions",
        "out_of_scope_interventions",
    ):
        for item in list(packet.get(key, [])):
            question = str(item.get("question", "")).strip()
            if question:
                items[question] = dict(item)
    return items


def _event_feedback_items(packet_items: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for event in load_events():
        if str(event.get("event_type", "")).strip() != "exercise_logged":
            continue
        payload = dict(event.get("payload", {}))
        linked_question = str(payload.get("linked_intervention_question", "")).strip()
        packet_item = packet_items.get(linked_question)
        if not packet_item:
            continue
        feedback_status = str(payload.get("feedback_status", "")).strip() or "formal_feedback"
        results.append(
            {
                "event_id": str(event.get("event_id", "")).strip(),
                "question": linked_question,
                "subject": str(event.get("subject", "")).strip(),
                "chapter_title": str(event.get("chapter_title", "")).strip(),
                "feedback_intake_status": feedback_status,
                "follow_up_reason": str(payload.get("follow_up_reason", "")).strip(),
                "result": str(payload.get("result", "")).strip(),
                "intervention_refs": [
                    {
                        "question": packet_item.get("question", ""),
                        "intervention_type": packet_item.get("intervention_type", ""),
                        "priority_reason": packet_item.get("priority_reason", ""),
                    }
                ],
                "source_refs": list(packet_item.get("source_refs", [])),
                "fact_writeback_allowed": False,
            }
        )
    return results


def build_payload(index_root: Path, plan_date: str) -> dict[str, Any]:
    packet = _load_packet(index_root)
    packet_items = _packet_map(packet)
    feedback_items = _event_feedback_items(packet_items)

    formal_feedback_intake: list[dict[str, Any]] = []
    review_only_feedback: list[dict[str, Any]] = []
    out_of_scope_feedback: list[dict[str, Any]] = []
    blocked_follow_ups: list[dict[str, Any]] = []
    coach_outcome_log: list[dict[str, Any]] = []

    seen_questions: set[str] = set()
    for item in feedback_items:
        coach_outcome_log.append(dict(item))
        seen_questions.add(item["question"])
        status = item["feedback_intake_status"]
        if status == "review_only_feedback":
            review_only_feedback.append(item)
        elif status == "out_of_scope_feedback":
            out_of_scope_feedback.append(item)
        else:
            formal_feedback_intake.append(item)

    for blocked in list(packet.get("blocked_interventions", [])):
        question = str(blocked.get("question", "")).strip()
        if not question or question in seen_questions:
            continue
        item = {
            "question": question,
            "subject": blocked.get("subject", ""),
            "chapter_title": blocked.get("chapter_title", ""),
            "feedback_intake_status": "blocked_follow_up",
            "follow_up_reason": ",".join(list(blocked.get("blocked_reasons", []))),
            "intervention_refs": [
                {
                    "question": blocked.get("question", ""),
                    "intervention_type": blocked.get("intervention_type", ""),
                    "priority_reason": blocked.get("priority_reason", ""),
                }
            ],
            "source_refs": list(blocked.get("source_refs", [])),
            "fact_writeback_allowed": False,
        }
        blocked_follow_ups.append(item)
        coach_outcome_log.append(dict(item))

    remaining_gaps = list(packet.get("remaining_gaps", []))
    if not formal_feedback_intake:
        remaining_gaps.append("No formal feedback intake is currently linked to the adaptive coaching packet.")
    readiness_status = "ready-for-r18-t05" if formal_feedback_intake else "not-ready-for-r18-t05"

    return {
        "artifact_contract_version": ARTIFACT_CONTRACT_VERSION,
        "artifact_id": ARTIFACT_ID,
        "plan_date": plan_date,
        "scope": "adaptive coaching packet -> post-action feedback intake -> intervention traceability boundary",
        "input_contract_refs": [
            {
                "name": "r18_t03_adaptive_coaching_packet",
                "version": packet.get("artifact_contract_version", ""),
            }
        ],
        "formal_feedback_intake": formal_feedback_intake,
        "review_only_feedback": review_only_feedback,
        "out_of_scope_feedback": out_of_scope_feedback,
        "blocked_follow_ups": blocked_follow_ups,
        "coach_outcome_log": coach_outcome_log,
        "fact_writeback_allowed": False,
        "remaining_gaps": remaining_gaps,
        "readiness_status": readiness_status,
        "post_r18_t04_successor": POST_R18_T04_SUCCESSOR,
    }


def render_markdown(payload: dict[str, Any]) -> str:
    successor = dict(payload.get("post_r18_t04_successor", {}))
    lines = [
        "# R18-T04 coaching feedback loop",
        "",
        f"- artifact_id: {payload.get('artifact_id', '')}",
        f"- plan_date: {payload.get('plan_date', '')}",
        f"- readiness_status: {payload.get('readiness_status', '')}",
        "",
        "## Feedback summary",
        "",
        f"- formal_feedback_intake: {len(list(payload.get('formal_feedback_intake', [])))}",
        f"- review_only_feedback: {len(list(payload.get('review_only_feedback', [])))}",
        f"- out_of_scope_feedback: {len(list(payload.get('out_of_scope_feedback', [])))}",
        f"- blocked_follow_ups: {len(list(payload.get('blocked_follow_ups', [])))}",
        "",
        "## Post-R18-T04 successor",
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
        "formal_feedback_intake": payload["formal_feedback_intake"],
        "review_only_feedback": payload["review_only_feedback"],
        "out_of_scope_feedback": payload["out_of_scope_feedback"],
        "blocked_follow_ups": payload["blocked_follow_ups"],
        "coach_outcome_log": payload["coach_outcome_log"],
        "fact_writeback_allowed": payload["fact_writeback_allowed"],
        "readiness_status": payload["readiness_status"],
        "post_r18_t04_successor": payload["post_r18_t04_successor"],
    }
    if args.format == "json":
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
