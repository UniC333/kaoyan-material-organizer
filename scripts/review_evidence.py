#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from common import ensure_kb_layout, load_all_json, load_json, now_iso, save_json, resolve_subject

RESOLVED_REVIEW_STATUSES = {"accepted", "rejected", "acknowledged"}
PENDING_VERIFICATION_STATUSES = {"needs_review", "stale"}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)

    queue = subparsers.add_parser("queue")
    queue.add_argument("--subject")
    queue.add_argument("--chapter-id")
    queue.add_argument("--format", choices=("json", "quiet"), default="json")

    decide = subparsers.add_parser("decide")
    decide.add_argument("--evidence-id", required=True)
    decide.add_argument("--decision", choices=("accept", "reject", "acknowledge-stale"), required=True)
    decide.add_argument("--note", default="")
    decide.add_argument("--format", choices=("json", "quiet"), default="json")
    return parser


def evidence_review_root() -> Path:
    return ensure_kb_layout()["review_queues"] / "evidence-review"


def normalize_subject(subject: str | None) -> str | None:
    if not subject:
        return None
    return resolve_subject(subject)[0]


def is_pending_review(evidence: dict[str, Any]) -> bool:
    review_status = str(evidence.get("review_status", "")).strip()
    verification_status = str(evidence.get("verification_status", "")).strip()
    if review_status in RESOLVED_REVIEW_STATUSES:
        return False
    return verification_status in PENDING_VERIFICATION_STATUSES


def queue_item_for(evidence: dict[str, Any]) -> dict[str, Any]:
    overlay_refs = [item for item in evidence.get("ocr_overlay_refs", []) if isinstance(item, dict)]
    return {
        "evidence_id": evidence.get("evidence_id", ""),
        "subject": evidence.get("subject", ""),
        "chapter_id": evidence.get("chapter_id", ""),
        "chunk_id": evidence.get("chunk_id", ""),
        "title": evidence.get("title", ""),
        "verification_status": evidence.get("verification_status", ""),
        "source_grounded": bool(evidence.get("source_grounded")),
        "stale_reasons": list(evidence.get("stale_reasons", [])),
        "ocr_overlay_ref_count": len(overlay_refs),
        "ocr_overlay_texts": [str(item.get("text", "")).strip() for item in overlay_refs if str(item.get("text", "")).strip()][:3],
        "updated_at": evidence.get("updated_at", ""),
    }


def queue_payload(subject: str | None = None, chapter_id: str | None = None) -> dict[str, Any]:
    layout = ensure_kb_layout()
    items: list[dict[str, Any]] = []
    for evidence in load_all_json(layout["evidence"]):
        if subject and evidence.get("subject") != subject:
            continue
        if chapter_id and evidence.get("chapter_id") != chapter_id:
            continue
        if not is_pending_review(evidence):
            continue
        items.append(queue_item_for(evidence))
    items.sort(key=lambda item: (item["subject"], item["chapter_id"], item["evidence_id"]))
    return {
        "subject": subject or "all",
        "chapter_id": chapter_id or "",
        "updated_at": now_iso(),
        "count": len(items),
        "items": items,
    }


def queue_path(subject: str | None) -> Path:
    root = evidence_review_root()
    root.mkdir(parents=True, exist_ok=True)
    return root / f"{subject or 'all'}.json"


def write_queue(subject: str | None, chapter_id: str | None = None) -> dict[str, Any]:
    payload = queue_payload(subject, chapter_id)
    save_json(queue_path(subject), payload)
    return payload


def update_evidence_decision(evidence: dict[str, Any], decision: str, note: str) -> dict[str, Any]:
    payload = dict(evidence)
    history = list(payload.get("review_history", []))
    entry = {
        "decision": decision,
        "note": note,
        "previous_verification_status": payload.get("verification_status", ""),
        "reviewed_at": now_iso(),
    }
    history.append(entry)
    payload["review_history"] = history
    payload["review_status"] = (
        "acknowledged" if decision == "acknowledge-stale" else "accepted" if decision == "accept" else "rejected"
    )
    payload["review_decision"] = decision
    payload["review_note"] = note
    payload["reviewed_at"] = entry["reviewed_at"]
    provenance = dict(payload.get("provenance") or {})

    if decision == "reject":
        payload["verification_status"] = "rejected"
        payload["source_grounded"] = False
        provenance["verification_status"] = "rejected"
        provenance["source_grounded"] = False
    elif decision == "accept":
        if payload.get("verification_status") == "needs_review":
            payload["verification_status"] = "reviewed"
        provenance["verification_status"] = payload.get("verification_status", "")
        provenance["source_grounded"] = bool(payload.get("source_grounded"))
    else:
        payload["verification_status"] = "stale"
        payload["source_grounded"] = False
        provenance["verification_status"] = "stale"
        provenance["source_grounded"] = False

    payload["provenance"] = provenance
    payload["updated_at"] = now_iso()
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

    evidence_path = layout["evidence"] / f"{args.evidence_id}.json"
    if not evidence_path.exists():
        raise SystemExit(f"[ERROR] missing evidence: {args.evidence_id}")
    evidence = load_json(evidence_path)
    payload = update_evidence_decision(evidence, args.decision, args.note)
    save_json(evidence_path, payload)
    refreshed_queue = write_queue(payload.get("subject", ""))
    result = {
        "evidence_id": payload.get("evidence_id", ""),
        "decision": args.decision,
        "review_status": payload.get("review_status", ""),
        "queue_count": refreshed_queue["count"],
    }
    if args.format == "json":
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
