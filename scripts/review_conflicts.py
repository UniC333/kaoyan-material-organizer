#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from common import ensure_kb_layout, load_all_json, load_json, now_iso, save_json, resolve_subject


PENDING_STATUSES = {"open", "review"}
RESOLVED_STATUSES = {"resolved", "accepted", "rejected"}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)

    queue = subparsers.add_parser("queue")
    queue.add_argument("--subject")
    queue.add_argument("--chapter-id")
    queue.add_argument("--format", choices=("json", "quiet"), default="json")

    decide = subparsers.add_parser("decide")
    decide.add_argument("--relation-id", required=True)
    decide.add_argument(
        "--decision",
        choices=("keep-both", "prefer-left", "prefer-right", "mark-uncertain"),
        required=True,
    )
    decide.add_argument("--note", default="")
    decide.add_argument("--format", choices=("json", "quiet"), default="json")
    return parser


def conflict_review_root() -> Path:
    return ensure_kb_layout()["review_queues"] / "conflict-review"


def normalize_subject(subject: str | None) -> str | None:
    if not subject:
        return None
    return resolve_subject(subject)[0]


def is_pending_review(conflict: dict[str, Any]) -> bool:
    status = str(conflict.get("status", "")).strip()
    if status in RESOLVED_STATUSES:
        return False
    resolution = conflict.get("resolution") or {}
    if isinstance(resolution, dict) and any(str(value).strip() for value in resolution.values() if value is not None):
        return False
    return status in PENDING_STATUSES or not status


def queue_item_for(conflict: dict[str, Any]) -> dict[str, Any]:
    return {
        "relation_id": conflict.get("relation_id") or conflict.get("conflict_id", ""),
        "conflict_id": conflict.get("conflict_id") or conflict.get("relation_id", ""),
        "relation_key": conflict.get("relation_key", ""),
        "subject": conflict.get("subject", ""),
        "chapter_id": conflict.get("chapter_id", ""),
        "syllabus_node_id": conflict.get("syllabus_node_id", ""),
        "relation_type": conflict.get("relation_type") or conflict.get("conflict_type", ""),
        "risk_level": conflict.get("risk_level", ""),
        "claim_ids": list(conflict.get("claim_ids", [])),
        "left_claim_id": conflict.get("left_claim_id", ""),
        "right_claim_id": conflict.get("right_claim_id", ""),
        "reason": conflict.get("reason", ""),
        "updated_at": conflict.get("updated_at", ""),
    }


def queue_payload(subject: str | None = None, chapter_id: str | None = None) -> dict[str, Any]:
    layout = ensure_kb_layout()
    items: list[dict[str, Any]] = []
    for conflict in load_all_json(layout["conflicts"]):
        if subject and conflict.get("subject") != subject:
            continue
        if chapter_id and conflict.get("chapter_id") != chapter_id:
            continue
        if not is_pending_review(conflict):
            continue
        items.append(queue_item_for(conflict))
    items.sort(key=lambda item: (item["subject"], item["chapter_id"], item["relation_id"]))
    return {
        "subject": subject or "all",
        "chapter_id": chapter_id or "",
        "updated_at": now_iso(),
        "count": len(items),
        "items": items,
    }


def queue_path(subject: str | None) -> Path:
    root = conflict_review_root()
    root.mkdir(parents=True, exist_ok=True)
    return root / f"{subject or 'all'}.json"


def write_queue(subject: str | None, chapter_id: str | None = None) -> dict[str, Any]:
    payload = queue_payload(subject, chapter_id)
    save_json(queue_path(subject), payload)
    return payload


def update_conflict_decision(conflict: dict[str, Any], decision: str, note: str) -> dict[str, Any]:
    payload = dict(conflict)
    reviewed_at = now_iso()
    entry = {
        "decision": decision,
        "note": note,
        "previous_status": payload.get("status", ""),
        "reviewed_at": reviewed_at,
    }
    history = list(payload.get("review_history", []))
    history.append(entry)
    payload["review_history"] = history
    payload["status"] = "resolved"
    payload["resolution"] = {
        "decision": decision,
        "note": note,
        "resolved_by": "human",
        "resolved_at": reviewed_at,
    }
    payload["resolved_by"] = "human"
    payload["resolved_at"] = reviewed_at
    payload["updated_at"] = reviewed_at
    return payload


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    args = build_parser().parse_args()
    layout = ensure_kb_layout()

    if args.command == "queue":
        subject = normalize_subject(args.subject)
        payload = write_queue(subject, args.chapter_id)
        if args.format == "json":
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    conflict_path = layout["conflicts"] / f"{args.relation_id}.json"
    if not conflict_path.exists():
        raise SystemExit(f"[ERROR] missing conflict: {args.relation_id}")
    conflict = load_json(conflict_path)
    payload = update_conflict_decision(conflict, args.decision, args.note)
    save_json(conflict_path, payload, ignored_compare_keys=())
    refreshed_queue = write_queue(payload.get("subject", ""))
    result = {
        "relation_id": payload.get("relation_id") or payload.get("conflict_id", ""),
        "decision": args.decision,
        "status": payload.get("status", ""),
        "queue_count": refreshed_queue["count"],
    }
    if args.format == "json":
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
