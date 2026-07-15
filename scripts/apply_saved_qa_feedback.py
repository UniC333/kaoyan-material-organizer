#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from common import load_json, normalize_context
from learner_events import append_event, build_intake_decision, build_source_provenance, rebuild_views


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--context-json", required=True)
    parser.add_argument("--question")
    parser.add_argument("--answer-metadata")
    parser.add_argument("--saved-note")
    parser.add_argument("--format", choices=("json", "quiet"), default="json")
    return parser.parse_args()


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    args = parse_args()
    context = normalize_context(load_json(Path(args.context_json)))
    subject = context["subject"]
    chapter_title = context.get("chapter_title", "")

    metadata = json.loads(args.answer_metadata) if args.answer_metadata else {}
    updated = False
    if args.question:
        event_payload = {
            "question": args.question,
            "intent": metadata.get("intent", ""),
            "answer_mode": metadata.get("answer_mode", ""),
            "syllabus_route": metadata.get("syllabus_route", []),
            "references": metadata.get("references", []),
            "saved_note": args.saved_note or "",
            "source_kind": metadata.get("source_kind", "legacy_saved_answer"),
            "answer_contract_version": metadata.get("answer_contract_version", ""),
            "citation_coverage_ok": metadata.get("citation_coverage_ok"),
            "fact_write_intent": metadata.get("fact_write_intent", ""),
        }
        source_provenance = build_source_provenance(event_payload)
        intake_decision = build_intake_decision(event_payload, source_provenance)
        if intake_decision["status"] == "blocked":
            raise SystemExit(f"[ERROR] blocked learner write: {intake_decision['reason']}")
        append_event(
            subject=subject,
            chapter_title=chapter_title,
            event_type="question_saved",
            payload=event_payload,
        )
        rebuild_views()
        updated = True

    if args.format == "json":
        print(json.dumps({"updated": updated, "subject": subject, "chapter_title": chapter_title}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
