#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from common import learner_file_map, load_json_or_default, now_iso, save_json, stable_fingerprint
from kaoyan_kb.domain.learner_model import build_learner_model_payload

EVENT_SCHEMA_VERSION = "0.3.0"
ALLOWED_SOURCE_KINDS = {"learner_safe_query_answer", "query_answer", "legacy_saved_answer", "codex_conversation_distillation"}
ALLOWED_ANSWER_MODES = {"canonical_claim", "accepted_evidence", "chapter_fallback", "learner_understanding"}
REQUIRES_CITATIONS = {"canonical_claim", "accepted_evidence"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--subject")
    parser.add_argument("--chapter-title")
    parser.add_argument("--event-type")
    parser.add_argument("--payload-json")
    parser.add_argument("--format", choices=("json", "quiet"), default="json")
    return parser.parse_args()


def load_events(default: Path | None = None) -> list[dict[str, Any]]:
    events_path = learner_file_map(default)["events"]
    if not events_path.exists():
        return []
    events: list[dict[str, Any]] = []
    for line in events_path.read_text(encoding="utf-8").splitlines():
        text = line.strip()
        if not text:
            continue
        events.append(json.loads(text))
    return events


def build_source_provenance(payload: dict[str, Any]) -> dict[str, Any]:
    references = [dict(item) for item in payload.get("references", []) if isinstance(item, dict)]
    syllabus_route = [dict(item) for item in payload.get("syllabus_route", []) if isinstance(item, dict)]
    answer_mode = str(payload.get("answer_mode", "")).strip()
    citation_coverage_ok = payload.get("citation_coverage_ok")
    if citation_coverage_ok is None:
        citation_coverage_ok = bool(references) if answer_mode in REQUIRES_CITATIONS else True
    return {
        "source_kind": str(payload.get("source_kind", "legacy_saved_answer")).strip() or "legacy_saved_answer",
        "answer_contract_version": str(payload.get("answer_contract_version", "")).strip(),
        "answer_mode": answer_mode,
        "citation_coverage_ok": bool(citation_coverage_ok),
        "reference_count": len(references),
        "syllabus_node_ids": [str(item.get("node_id", "")).strip() for item in syllabus_route if str(item.get("node_id", "")).strip()],
    }


def build_intake_decision(payload: dict[str, Any], source_provenance: dict[str, Any]) -> dict[str, Any]:
    source_kind = source_provenance["source_kind"]
    answer_mode = source_provenance["answer_mode"]
    citation_coverage_ok = bool(source_provenance["citation_coverage_ok"])
    reference_count = int(source_provenance["reference_count"])
    fact_write_intent = str(payload.get("fact_write_intent", "")).strip()

    if fact_write_intent:
        status = "blocked"
        reason = "fact_layer_mutation_requested"
    elif source_kind not in ALLOWED_SOURCE_KINDS:
        status = "blocked"
        reason = "non_learner_safe_source"
    elif answer_mode not in ALLOWED_ANSWER_MODES:
        status = "blocked"
        reason = "unsupported_answer_mode"
    elif (source_kind == "codex_conversation_distillation") != (answer_mode == "learner_understanding"):
        status = "blocked"
        reason = "distillation_contract_mismatch"
    elif answer_mode in REQUIRES_CITATIONS and (not citation_coverage_ok or reference_count <= 0):
        status = "blocked"
        reason = "missing_citation_coverage"
    elif answer_mode == "chapter_fallback":
        status = "review_only"
        reason = "chapter_fallback_requires_followup"
    elif answer_mode == "learner_understanding":
        status = "accepted"
        reason = "confirmed_learner_understanding"
    else:
        status = "accepted"
        reason = "grounded_query_answer"

    return {
        "status": status,
        "reason": reason,
        "learner_model_eligible": status in {"accepted", "review_only"},
        "refinement_eligible": status == "review_only",
        "fact_write_allowed": False,
    }


def append_event(
    *,
    subject: str,
    chapter_title: str,
    event_type: str,
    payload: dict[str, Any],
    default: Path | None = None,
) -> dict[str, Any]:
    files = learner_file_map(default)
    occurred_at = now_iso()
    source_provenance = build_source_provenance(payload)
    intake_decision = build_intake_decision(payload, source_provenance)
    event = {
        "schema_version": EVENT_SCHEMA_VERSION,
        "event_id": stable_fingerprint(
            {
                "occurred_at": occurred_at,
                "subject": subject,
                "chapter_title": chapter_title,
                "event_type": event_type,
                "payload": payload,
            }
        ),
        "event_type": event_type,
        "occurred_at": occurred_at,
        "subject": subject,
        "chapter_title": chapter_title,
        "source_provenance": source_provenance,
        "intake_decision": intake_decision,
        "payload": payload,
    }
    files["root"].mkdir(parents=True, exist_ok=True)
    with files["events"].open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(event, ensure_ascii=False) + "\n")
    return event


def rebuild_views(default: Path | None = None) -> dict[str, dict[str, Any]]:
    files = learner_file_map(default)
    events = load_events(default)
    learner_model = build_learner_model_payload(events)
    question_history: dict[str, Any] = {"items": []}
    error_log: dict[str, Any] = {"items": []}
    review_history: dict[str, Any] = {"items": []}
    refinement_queue = load_json_or_default(files["refinement_queue"], {"items": []})
    distillation_candidates = load_json_or_default(
        files["distillation_candidates"], {"contract_version": "r54.conversation-distillation.v1", "items": []}
    )

    for event in events:
        if event.get("event_type") != "question_saved":
            continue
        intake = dict(event.get("intake_decision") or {})
        status = str(intake.get("status", "accepted")).strip() or "accepted"
        if status == "blocked":
            continue
        payload = dict(event.get("payload") or {})
        subject = str(event.get("subject", "")).strip()
        chapter_title = str(event.get("chapter_title", "")).strip()
        source_provenance = dict(event.get("source_provenance") or {})
        question_item = {
            "event_id": event.get("event_id", ""),
            "saved_at": event.get("occurred_at", ""),
            "subject": subject,
            "chapter_title": chapter_title,
            "question": payload.get("question", ""),
            "intent": payload.get("intent", ""),
            "answer_mode": payload.get("answer_mode", ""),
            "syllabus_route": payload.get("syllabus_route", []),
            "references": payload.get("references", []),
            "saved_note": payload.get("saved_note", ""),
            "intake_status": status,
            "intake_reason": intake.get("reason", ""),
            "source_provenance": source_provenance,
        }
        question_history["items"].append(question_item)

        review_history["items"].append(
            {
                "saved_at": question_item["saved_at"],
                "subject": subject,
                "chapter_title": chapter_title,
                "event": "question_saved",
                "question": question_item["question"],
            }
        )
        if question_item["answer_mode"] == "chapter_fallback":
            error_log["items"].append(
                {
                    "saved_at": question_item["saved_at"],
                    "subject": subject,
                    "chapter_title": chapter_title,
                    "issue_type": "fallback_only",
                    "question": question_item["question"],
                }
            )

    payloads = {
        "learner_model": learner_model,
        "question_history": question_history,
        "error_log": error_log,
        "review_history": review_history,
        "refinement_queue": refinement_queue,
        "distillation_candidates": distillation_candidates,
    }
    for key, path in files.items():
        if key in {"root", "events"}:
            continue
        save_json(path, payloads[key])
    return payloads


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    args = parse_args()
    result: dict[str, Any] = {}
    if args.subject and args.chapter_title and args.event_type and args.payload_json:
        payload = json.loads(args.payload_json)
        event = append_event(
            subject=args.subject,
            chapter_title=args.chapter_title,
            event_type=args.event_type,
            payload=payload,
        )
        rebuild_views()
        result = {"appended": True, "event_id": event["event_id"], "event_type": event["event_type"]}
    else:
        events = load_events()
        result = {"count": len(events), "items": events[-10:]}
    if args.format == "json":
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
