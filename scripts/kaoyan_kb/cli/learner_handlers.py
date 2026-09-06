from __future__ import annotations

import argparse
import json
from typing import Any
from common import now_iso


def _subject_from_node_id(node_id: str) -> str:
    token = str(node_id or "").strip().upper().split("-")
    code = token[1] if len(token) >= 2 else ""
    mapping = {
        "MATH": "数学",
        "408": "408",
        "ENG": "英语",
        "POL": "政治",
    }
    subject = mapping.get(code, "")
    if not subject:
        raise SystemExit(f"cannot infer subject from node id: {node_id}")
    return subject


def _dedupe_strings(values: list[str]) -> list[str]:
    items: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if text and text not in items:
            items.append(text)
    return items


def _review_event_result(event: dict[str, Any], *, idempotent_reuse: bool) -> dict[str, Any]:
    payload = dict(event.get("payload") or {})
    return {
        "event_id": event.get("event_id", ""),
        "node_id": payload.get("node_id", ""),
        "question_id": payload.get("question_id", ""),
        "result": payload.get("result", ""),
        "fluency": payload.get("fluency", ""),
        "effective_fluency": payload.get("effective_fluency", ""),
        "interval_days": payload.get("interval_days", 0),
        "due_date": payload.get("due_date", ""),
        "hint_used": bool(payload.get("hint_used", False)),
        "source_task_id": payload.get("source_task_id", ""),
        "source_message_ids": list(payload.get("source_message_ids", [])),
        "idempotent_reuse": idempotent_reuse,
    }


def _learner_exercise_batch_output(args: argparse.Namespace) -> str:
    from learner_events import append_events, build_event, load_events_readonly, rebuild_views
    from kaoyan_kb.domain.chapter_review_batch import (
        load_chapter_review_batch,
        validate_chapter_review_batch,
    )
    from kaoyan_kb.domain.review_scheduler import (
        is_duplicate_review_event,
        previous_interval_for,
        schedule_fields,
    )

    if args.node or args.result:
        raise SystemExit("[ERROR] --batch-json cannot be combined with --node or --result; no learner write was made")
    try:
        batch = validate_chapter_review_batch(load_chapter_review_batch(args.batch_json))
    except ValueError as exc:
        raise SystemExit(f"[ERROR] {exc}; no learner write was made") from exc
    if args.subject and args.subject.strip() != batch["subject"]:
        raise SystemExit("[ERROR] --subject conflicts with batch subject; no learner write was made")
    if args.chapter and args.chapter.strip() != batch["chapter_title"]:
        raise SystemExit("[ERROR] --chapter conflicts with batch chapter_title; no learner write was made")

    events = load_events_readonly()
    working_events = list(events)
    planned_events: list[dict[str, Any]] = []
    item_results: list[dict[str, Any]] = []
    source = dict(batch["source"])
    for item in batch["items"]:
        duplicate = is_duplicate_review_event(
            working_events,
            subject=batch["subject"],
            chapter_title=batch["chapter_title"],
            review_id=batch["review_id"],
            question_id=item["question_id"],
            node_id=item["node_id"],
        )
        if duplicate is not None:
            item_results.append(_review_event_result(duplicate, idempotent_reuse=True))
            continue
        payload = {
            "node_id": item["node_id"],
            "result": item["result"],
            "tags": list(item["tags"]),
            "note": item["note"],
            "context": "chapter_review",
            "review_id": batch["review_id"],
            "question_id": item["question_id"],
            "fluency": item["fluency"],
            "duration_minutes": item["duration_minutes"],
            "hint_used": item["hint_used"],
            "batch_contract_version": batch["contract_version"],
            "source_kind": "codex_chapter_review",
            "answer_mode": "learner_review_result",
            "source_task_id": source["task_id"],
            "source_message_ids": list(source["message_ids"]),
        }
        payload.update(
            schedule_fields(
                subject=batch["subject"],
                result=item["result"],
                fluency=item["fluency"],
                occurred_at=batch["reviewed_at"],
                previous_interval=previous_interval_for(
                    working_events,
                    batch["subject"],
                    batch["chapter_title"],
                    item["node_id"],
                    before_or_at=batch["reviewed_at"],
                ),
                duration_minutes=item["duration_minutes"],
            )
        )
        event = build_event(
            subject=batch["subject"],
            chapter_title=batch["chapter_title"],
            event_type="exercise_logged",
            payload=payload,
            occurred_at=batch["reviewed_at"],
        )
        planned_events.append(event)
        working_events.append(event)
        item_results.append(_review_event_result(event, idempotent_reuse=False))

    views_rebuilt = False
    if args.yes and planned_events:
        append_events(planned_events)
        rebuild_views()
        views_rebuilt = True
    result = {
        "contract_version": batch["contract_version"],
        "mode": "committed" if args.yes else "preview",
        "valid": True,
        "subject": batch["subject"],
        "chapter_title": batch["chapter_title"],
        "review_id": batch["review_id"],
        "reviewed_at": batch["reviewed_at"],
        "source": source,
        "item_count": len(batch["items"]),
        "would_write_count": len(planned_events),
        "written_count": len(planned_events) if args.yes else 0,
        "reused_count": len(batch["items"]) - len(planned_events),
        "idempotent_reuse": not planned_events,
        "derived_views_rebuilt": views_rebuilt,
        "cli_write_scope": (
            "none_preview"
            if not args.yes
            else ("none_existing_events_reused" if not planned_events else "learner_events_and_derived_views")
        ),
        "learner_layer_only": True,
        "items": item_results,
    }
    return json.dumps(result, ensure_ascii=False, indent=2) + "\n" if args.format == "json" else ""


def _learner_exercise_output(args: argparse.Namespace) -> str:
    if args.batch_json:
        return _learner_exercise_batch_output(args)
    if not args.node or not args.result:
        raise SystemExit("[ERROR] single exercise requires --node and --result; no learner write was made")
    if args.yes:
        raise SystemExit("[ERROR] --yes is only valid with --batch-json; no learner write was made")

    from learner_events import append_event, load_events, rebuild_views
    from kaoyan_kb.domain.review_scheduler import (
        is_duplicate_review_event,
        is_supported_review_subject,
        previous_interval_for,
        schedule_fields,
    )

    subject = args.subject or _subject_from_node_id(args.node)
    chapter_title = args.chapter or ""
    events = load_events()
    payload = {
        "node_id": args.node,
        "result": args.result,
        "tags": _dedupe_strings(list(args.tag)),
        "note": args.note,
    }
    is_review = args.context in {"chapter_review", "scheduled_review"}
    if is_review:
        if not args.review_id or not args.question_id or not args.fluency:
            raise SystemExit("review exercise requires --review-id, --question-id, and --fluency")
        if not is_supported_review_subject(subject):
            raise SystemExit("adaptive review currently supports mathematics and 408 subjects only")
        duplicate = is_duplicate_review_event(
            events,
            subject=subject,
            chapter_title=chapter_title,
            review_id=args.review_id,
            question_id=args.question_id,
            node_id=args.node,
        )
        if duplicate is not None:
            duplicate_payload = dict(duplicate.get("payload") or {})
            result = {
                "event_id": duplicate.get("event_id", ""),
                "event_type": duplicate.get("event_type", ""),
                "subject": subject,
                "chapter_title": chapter_title,
                "node_id": args.node,
                "result": duplicate_payload.get("result", ""),
                "fluency": duplicate_payload.get("fluency", ""),
                "effective_fluency": duplicate_payload.get("effective_fluency", ""),
                "interval_days": duplicate_payload.get("interval_days", 0),
                "due_date": duplicate_payload.get("due_date", ""),
                "idempotent_reuse": True,
                "cli_write_scope": "none_existing_event_reused",
                "learner_layer_only": True,
            }
            return json.dumps(result, ensure_ascii=False, indent=2) + "\n" if args.format == "json" else ""
        payload.update(
            {
                "context": args.context,
                "review_id": args.review_id,
                "question_id": args.question_id,
                "fluency": args.fluency,
                "duration_minutes": args.duration_minutes,
                "hint_used": bool(args.hint_used),
            }
        )
    occurred_at = now_iso()
    if is_review:
        payload.update(
            schedule_fields(
                subject=subject,
                result=args.result,
                fluency=args.fluency,
                occurred_at=occurred_at,
                previous_interval=previous_interval_for(events, subject, chapter_title, args.node),
                duration_minutes=args.duration_minutes,
            )
        )
    event = append_event(
        subject=subject,
        chapter_title=chapter_title,
        event_type="exercise_logged",
        payload=payload,
        occurred_at=occurred_at,
    )
    rebuild_views()
    result = {
        "event_id": event["event_id"],
        "event_type": event["event_type"],
        "subject": subject,
        "chapter_title": chapter_title,
        "node_id": args.node,
        "result": args.result,
        "fluency": payload.get("fluency", ""),
        "effective_fluency": payload.get("effective_fluency", ""),
        "interval_days": payload.get("interval_days", 0),
        "due_date": payload.get("due_date", ""),
        "idempotent_reuse": False,
        "cli_write_scope": "learner_events_and_derived_views",
        "learner_layer_only": True,
    }
    return json.dumps(result, ensure_ascii=False, indent=2) + "\n" if args.format == "json" else ""
