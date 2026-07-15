#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from build_tutoring_strategy_packet import ARTIFACT_JSON as STRATEGY_PACKET_JSON
from common import INDEX_DIRNAME, default_vault_root_arg, save_json, save_text
from learner_events import load_events

ARTIFACT_JSON = "38_r19_tutoring_feedback_loop.json"
ARTIFACT_MD = "38_r19_tutoring_feedback_loop.md"
ARTIFACT_ID = "r19-tutoring-feedback-loop"
ARTIFACT_CONTRACT_VERSION = "r19.tutoring-feedback-loop.v1"
POST_R19_T04_SUCCESSOR = {
    "track_id": "R19-T05",
    "title": "long-horizon study operations, goal-adjustment governance, and human override boundary",
    "scope": "cycle feedback -> goal-adjustment governance -> long-horizon operations",
    "machine_readable_entry_point": "R19-T05 -> M10-T05",
    "status": "defined_not_started",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--vault-root", default=default_vault_root_arg())
    parser.add_argument("--plan-date", required=True)
    parser.add_argument("--format", choices=("json", "quiet"), default="json")
    return parser.parse_args()


def _load_packet(index_root: Path) -> dict[str, Any]:
    path = index_root / STRATEGY_PACKET_JSON
    if not path.exists():
        raise SystemExit("missing tutoring strategy packet artifact; run build_tutoring_strategy_packet.py first")
    return json.loads(path.read_text(encoding="utf-8"))


def _packet_map(packet: dict[str, Any]) -> dict[str, dict[str, Any]]:
    items: dict[str, dict[str, Any]] = {}
    for key in (
        "recommended_strategies",
        "review_needed_strategies",
        "blocked_strategies",
        "out_of_scope_strategies",
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
        linked_question = str(payload.get("linked_strategy_question", "")).strip()
        packet_item = packet_items.get(linked_question)
        if not packet_item:
            continue
        feedback_status = str(payload.get("cycle_feedback_status", "")).strip() or "formal_cycle_feedback"
        results.append(
            {
                "event_id": str(event.get("event_id", "")).strip(),
                "question": linked_question,
                "subject": str(event.get("subject", "")).strip(),
                "chapter_title": str(event.get("chapter_title", "")).strip(),
                "cycle_feedback_status": feedback_status,
                "follow_up_reason": str(payload.get("follow_up_reason", "")).strip(),
                "result": str(payload.get("result", "")).strip(),
                "goal_progress_log": {
                    "result": str(payload.get("result", "")).strip(),
                    "progress_signal": str(payload.get("progress_signal", "")).strip(),
                    "cycle_index": int(payload.get("cycle_index", 1) or 1),
                },
                "strategy_refs": [
                    {
                        "question": packet_item.get("question", ""),
                        "strategy_type": packet_item.get("strategy_type", ""),
                        "phase_priority": packet_item.get("phase_priority", ""),
                        "phase_priority_reason": packet_item.get("phase_priority_reason", ""),
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

    formal_cycle_feedback: list[dict[str, Any]] = []
    review_only_feedback: list[dict[str, Any]] = []
    out_of_scope_feedback: list[dict[str, Any]] = []
    blocked_follow_ups: list[dict[str, Any]] = []
    goal_progress_log: list[dict[str, Any]] = []

    seen_questions: set[str] = set()
    for item in feedback_items:
        goal_progress_log.append(dict(item))
        seen_questions.add(item["question"])
        status = item["cycle_feedback_status"]
        if status == "review_only_feedback":
            review_only_feedback.append(item)
        elif status == "out_of_scope_feedback":
            out_of_scope_feedback.append(item)
        else:
            formal_cycle_feedback.append(item)

    for blocked in list(packet.get("blocked_strategies", [])):
        question = str(blocked.get("question", "")).strip()
        if not question or question in seen_questions:
            continue
        item = {
            "question": question,
            "subject": blocked.get("subject", ""),
            "chapter_title": blocked.get("chapter_title", ""),
            "cycle_feedback_status": "blocked_follow_up",
            "follow_up_reason": ",".join(list(blocked.get("blocked_reasons", []))),
            "goal_progress_log": {
                "result": "blocked",
                "progress_signal": "blocked_strategy",
                "cycle_index": 0,
            },
            "strategy_refs": [
                {
                    "question": blocked.get("question", ""),
                    "strategy_type": blocked.get("strategy_type", ""),
                    "phase_priority": blocked.get("phase_priority", ""),
                    "phase_priority_reason": blocked.get("phase_priority_reason", ""),
                }
            ],
            "source_refs": list(blocked.get("source_refs", [])),
            "fact_writeback_allowed": False,
        }
        blocked_follow_ups.append(item)
        goal_progress_log.append(dict(item))

    remaining_gaps = list(packet.get("remaining_gaps", []))
    if not formal_cycle_feedback:
        remaining_gaps.append("No formal multi-cycle feedback is currently linked to the tutoring strategy packet.")
    readiness_status = "ready-for-r19-t05" if formal_cycle_feedback else "not-ready-for-r19-t05"

    return {
        "artifact_contract_version": ARTIFACT_CONTRACT_VERSION,
        "artifact_id": ARTIFACT_ID,
        "plan_date": plan_date,
        "scope": "tutoring strategy packet -> multi-cycle feedback intake -> tutoring traceability boundary",
        "input_contract_refs": [
            {
                "name": "r19_t03_tutoring_strategy_packet",
                "version": packet.get("artifact_contract_version", ""),
            }
        ],
        "formal_cycle_feedback": formal_cycle_feedback,
        "review_only_feedback": review_only_feedback,
        "out_of_scope_feedback": out_of_scope_feedback,
        "blocked_follow_ups": blocked_follow_ups,
        "goal_progress_log": goal_progress_log,
        "fact_writeback_allowed": False,
        "remaining_gaps": remaining_gaps,
        "readiness_status": readiness_status,
        "post_r19_t04_successor": POST_R19_T04_SUCCESSOR,
    }


def render_markdown(payload: dict[str, Any]) -> str:
    successor = dict(payload.get("post_r19_t04_successor", {}))
    lines = [
        "# R19-T04 tutoring feedback loop",
        "",
        f"- artifact_id: {payload.get('artifact_id', '')}",
        f"- plan_date: {payload.get('plan_date', '')}",
        f"- readiness_status: {payload.get('readiness_status', '')}",
        "",
        "## Feedback summary",
        "",
        f"- formal_cycle_feedback: {len(list(payload.get('formal_cycle_feedback', [])))}",
        f"- review_only_feedback: {len(list(payload.get('review_only_feedback', [])))}",
        f"- out_of_scope_feedback: {len(list(payload.get('out_of_scope_feedback', [])))}",
        f"- blocked_follow_ups: {len(list(payload.get('blocked_follow_ups', [])))}",
        "",
        "## Post-R19-T04 successor",
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
        "formal_cycle_feedback": payload["formal_cycle_feedback"],
        "review_only_feedback": payload["review_only_feedback"],
        "out_of_scope_feedback": payload["out_of_scope_feedback"],
        "blocked_follow_ups": payload["blocked_follow_ups"],
        "goal_progress_log": payload["goal_progress_log"],
        "fact_writeback_allowed": payload["fact_writeback_allowed"],
        "readiness_status": payload["readiness_status"],
        "post_r19_t04_successor": payload["post_r19_t04_successor"],
    }
    if args.format == "json":
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
