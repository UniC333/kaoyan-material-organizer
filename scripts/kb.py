#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import locale
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from common import INDEX_DIRNAME, ensure_kb_layout, learner_file_map, load_json_or_default, now_iso, run_utf8_subprocess, runtime_subprocess_env, save_json
from config import CONFIG_SOURCE_ENV, RuntimeConfigError, load_runtime_config, reset_runtime_config_cache
from kaoyan_kb.cli.core_commands import add_core_commands, dispatch_core
from kaoyan_kb.cli.book_commands import add_book_commands, dispatch_book
from kaoyan_kb.cli.learner_commands import add_learner_commands, dispatch_learner
from kaoyan_kb.cli.review_commands import add_review_commands, dispatch_review
from kaoyan_kb.cli.run_maintain_commands import add_run_maintain_commands, dispatch_run_maintain
from kaoyan_kb.cli.snapshot_migrate_commands import add_snapshot_migrate_commands, dispatch_snapshot_migrate


SCRIPT_DIR = Path(__file__).resolve().parent


class HelpFormatter(argparse.RawDescriptionHelpFormatter, argparse.ArgumentDefaultsHelpFormatter):
    pass


ROOT_DESCRIPTION = "Wrapper-first CLI for the maintained kaoyan material organizer surface."

ROOT_EPILOG = """Stable entry groups:
  doctor          Check runtime paths, OCR readiness, and terminal encoding hints
  sync/query/ask  Daily sync plus local knowledge retrieval and QA
  review          Evidence, conflicts, and refinement review queues
  learner         Learner-layer artifacts and tutoring packets
  book            Paper-book/PDF: inspect -> map-pages -> OCR -> review -> classify -> publish -> query/ask
  snapshot/run    Recovery checkpoints and resumable run manifests

Common examples:
  kb.py doctor
  kb.py sync --subject 数学 --format json
  kb.py query --subject 408 --query "栈和队列的区别" --format json
  kb.py review evidence queue --subject 数学 --format json
  kb.py learner daily-card --plan-date 2026-07-07 --format json
  kb.py learner closure validate --manifest-json <path> --vault-root <path> --format json
  kb.py book inspect --book-root <book-root> --format json
"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=ROOT_DESCRIPTION,
        epilog=ROOT_EPILOG,
        formatter_class=HelpFormatter,
    )
    parser.add_argument(
        "--config",
        help="use this kaoyan.config.json for the entire command and every wrapped child process",
    )
    subparsers = parser.add_subparsers(dest="command", required=True, title="commands")

    add_core_commands(subparsers, formatter_class=HelpFormatter)

    add_review_commands(subparsers, formatter_class=HelpFormatter)

    add_learner_commands(subparsers, formatter_class=HelpFormatter)

    add_run_maintain_commands(subparsers, formatter_class=HelpFormatter)

    add_book_commands(subparsers, formatter_class=HelpFormatter)

    add_snapshot_migrate_commands(subparsers, formatter_class=HelpFormatter)
    return parser


def run_script(name: str, *args: str) -> str:
    try:
        completed = run_utf8_subprocess(
            [sys.executable, str(SCRIPT_DIR / name), *args],
            command_label=f"python:{name}",
            check=True,
            env=runtime_subprocess_env(),
        )
    except subprocess.CalledProcessError as exc:
        message = (exc.stderr or exc.stdout or "").strip()
        raise SystemExit(message or f"{name} failed with exit code {exc.returncode}") from exc
    return completed.stdout


def bool_status(value: bool) -> str:
    return "ok" if value else "missing"


def terminal_encoding_payload() -> dict[str, str | bool]:
    stdout_encoding = (sys.stdout.encoding or "").strip()
    preferred_encoding = (locale.getpreferredencoding(False) or "").strip()
    filesystem_encoding = (sys.getfilesystemencoding() or "").strip()
    utf8_aliases = {"utf-8", "utf8", "cp65001"}
    stdout_utf8 = stdout_encoding.lower() in utf8_aliases if stdout_encoding else False
    preferred_utf8 = preferred_encoding.lower() in utf8_aliases if preferred_encoding else False
    utf8_ready = stdout_utf8 and preferred_utf8
    note = (
        "UTF-8 looks consistent for both stdout and the preferred locale."
        if utf8_ready
        else "Non-UTF-8 locale settings can still garble Chinese when opening docs or running PowerShell helpers."
    )
    fix_hint = (
        "PowerShell: chcp 65001, then set "
        "$OutputEncoding = [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()"
    )
    return {
        "terminal_stdout_encoding": stdout_encoding or "unknown",
        "terminal_preferred_encoding": preferred_encoding or "unknown",
        "terminal_filesystem_encoding": filesystem_encoding or "unknown",
        "terminal_utf8_ready": utf8_ready,
        "terminal_note": note,
        "terminal_fix_hint": fix_hint,
    }


def doctor_payload() -> dict[str, Any]:
    runtime = load_runtime_config()
    layout = ensure_kb_layout()
    ocr_env = json.loads(run_script("doctor_ocr_env.py", "--format", "json"))
    return {
        "workspace_root": str(runtime.workspace_root),
        "vault_root": str(runtime.vault_root),
        "kb_root": str(runtime.kb_root),
        "backup_root": str(runtime.backup_root),
        "python_executable": str(runtime.python_executable),
        "config_path": str(runtime.config_path) if runtime.config_path else "",
        "config_source": runtime.config_source,
        "runtime_configured": runtime.configured,
        "schema_version_exists": (layout["root"] / "schema-version.json").exists(),
        "schema_dir_exists": layout["schemas"].exists(),
        "ocr_env": ocr_env,
        "ocr_acceptance": ocr_env.get("ocr_acceptance", {}),
        **terminal_encoding_payload(),
    }


def render_doctor_text(payload: dict[str, Any]) -> str:
    lines = [
        "# kb doctor",
        "",
        f"- workspace_root: {payload['workspace_root']}",
        f"- vault_root: {payload['vault_root']}",
        f"- kb_root: {payload['kb_root']}",
        f"- backup_root: {payload['backup_root']}",
        f"- python_executable: {payload['python_executable']}",
        f"- config_path: {payload['config_path'] or 'n/a'}",
        f"- config_source: {payload['config_source']}",
        f"- runtime_configured: {'yes' if payload['runtime_configured'] else 'no'}",
        f"- schema_version_exists: {bool_status(bool(payload['schema_version_exists']))}",
        f"- schema_dir_exists: {bool_status(bool(payload['schema_dir_exists']))}",
        f"- ocr_package_manager: {payload['ocr_env'].get('package_manager', '')}",
        f"- ocr_remote_authorization: {payload['ocr_env'].get('ocr_runtime', {}).get('remote_authorization', 'disabled')}",
        f"- ocr_monthly_page_budget: {payload['ocr_env'].get('ocr_runtime', {}).get('monthly_page_budget', '')}",
        f"- fixture_ocr_ready: {bool_status(bool(payload['ocr_acceptance'].get('fixture_ocr_ready')))}",
        f"- live_smoke_ready: {bool_status(bool(payload['ocr_acceptance'].get('live_smoke_ready')))}",
        f"- live_smoke_manual_only: {'yes' if payload['ocr_acceptance'].get('manual_only') else 'no'}",
        f"- live_smoke_command: {payload['ocr_acceptance'].get('live_smoke_command', '')}",
        *[f"- warning: {warning}" for warning in payload["ocr_acceptance"].get("warnings", [])],
        f"- terminal_stdout_encoding: {payload['terminal_stdout_encoding']}",
        f"- terminal_preferred_encoding: {payload['terminal_preferred_encoding']}",
        f"- terminal_filesystem_encoding: {payload['terminal_filesystem_encoding']}",
        f"- terminal_utf8_ready: {bool_status(bool(payload['terminal_utf8_ready']))}",
        f"- terminal_note: {payload['terminal_note']}",
        f"- terminal_fix_hint: {payload['terminal_fix_hint']}",
    ]
    return "\n".join(lines) + "\n"


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


def _review_refinement_decide(refinement_id: str, status: str, note: str) -> dict[str, Any]:
    files = learner_file_map()
    payload = load_json_or_default(files["refinement_queue"], {"updated_at": "", "items": []})
    updated = False
    review_at = now_iso()
    allowed_transitions = {
        "open": {"accepted", "rejected"},
        "accepted": {"implemented", "rejected"},
        "implemented": {"verified", "rejected"},
        "verified": {"verified"},
        "rejected": {"rejected"},
    }
    for item in payload.get("items", []):
        if item.get("refinement_id") != refinement_id:
            continue
        current_status = str(item.get("status", "open")).strip() or "open"
        if status != current_status and status not in allowed_transitions.get(current_status, set()):
            raise SystemExit(f"invalid refinement lifecycle transition: {current_status} -> {status}")
        history = list(item.get("review_history", []))
        history.append({"status": status, "note": note, "at": review_at})
        item["status"] = status
        item["updated_at"] = review_at
        item["review_history"] = history
        updated = True
        break
    if not updated:
        raise SystemExit(f"refinement not found: {refinement_id}")
    payload["updated_at"] = review_at
    save_json(files["refinement_queue"], payload)
    return {
        "updated": True,
        "refinement_id": refinement_id,
        "status": status,
        "reviewed_at": review_at,
        "cli_write_scope": "refinement_queue",
        "learner_layer_only": True,
    }


def _build_weekly_refresh_fallback(args: argparse.Namespace, failure_message: str = "") -> dict[str, Any]:
    common_args: list[str] = []
    if args.vault_root:
        common_args.extend(["--vault-root", args.vault_root])

    run_script("build_global_knowledge_registry.py", *common_args)
    run_script("build_subject_course_index.py", *common_args)
    audit_args = [*common_args, "--write-report", "--format", "json"]
    if args.subject:
        audit_args.extend(["--subject", args.subject])
    if args.chapter:
        audit_args.extend(["--chapter", args.chapter])
    audit_payload = json.loads(run_script("audit_knowledge_batches.py", *audit_args))
    run_script("build_saved_qa_registry.py", *common_args)
    refinement_args = [*common_args, "--topn", str(args.topn), "--format", "json"]
    if args.subject:
        refinement_args.extend(["--subject", args.subject])
    if args.chapter:
        refinement_args.extend(["--chapter", args.chapter])
    refinement_payload = json.loads(run_script("build_refinement_queue.py", *refinement_args))
    run_script("build_learning_dashboard.py", *common_args)
    feedback_summary_path = Path(args.vault_root or load_runtime_config().vault_root) / INDEX_DIRNAME / "19_learner_feedback_summary.json"
    feedback_summary = load_json_or_default(
        feedback_summary_path,
        {
            "feedback_contract_version": "",
            "fact_writeback_allowed": False,
            "learner_facing_summary": [],
            "review_only_insights": [],
        },
    )
    return {
        "sync_mode": "fallback-no-sync",
        "sync": {"count": 0, "chapters": []},
        "audit": audit_payload,
        "top_actions": audit_payload.get("batches", [])[: max(1, args.topn)],
        "top_reuse_candidates": [],
        "top_refinement_queue": refinement_payload.get("items", [])[: max(1, args.topn)],
        "top_master_card_candidates": [],
        "promoted_master_cards": [],
        "steps": [
            "build_global_knowledge_registry",
            "build_subject_course_index",
            "audit_knowledge_batches",
            "build_saved_qa_registry",
            "build_refinement_queue",
            "build_learning_dashboard",
        ],
        "fallback_reason": "sync_chapter_knowledge_prerequisites_missing",
        "maintenance_error": failure_message,
        "feedback_contract_version": feedback_summary.get("feedback_contract_version", ""),
        "fact_writeback_allowed": bool(feedback_summary.get("fact_writeback_allowed", False)),
        "learner_facing_summary": feedback_summary.get("learner_facing_summary", []),
        "review_only_insights": feedback_summary.get("review_only_insights", []),
    }


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


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = build_parser()
    args = parser.parse_args()

    if args.config:
        os.environ["KAOYAN_CONFIG_FILE"] = str(Path(args.config).expanduser())
        os.environ[CONFIG_SOURCE_ENV] = "command-line"
        reset_runtime_config_cache()
    try:
        load_runtime_config()
    except RuntimeConfigError as exc:
        parser.error(str(exc))

    core_output = dispatch_core(args, run_script, doctor_payload, render_doctor_text)
    if core_output is not None:
        print(core_output, end="")
        return 0

    run_output = dispatch_run_maintain(args, run_script, _build_weekly_refresh_fallback)
    if run_output is not None:
        print(run_output, end="")
        return 0

    review_output = dispatch_review(args, run_script, _review_refinement_decide)
    if review_output is not None:
        print(review_output, end="")
        return 0

    learner_output = dispatch_learner(args, run_script, _learner_exercise_output)
    if learner_output is not None:
        print(learner_output, end="")
        return 0

    book_output = dispatch_book(args, run_script)
    if book_output is not None:
        print(book_output, end="")
        return 0

    snapshot_or_migration_output = dispatch_snapshot_migrate(args, run_script)
    if snapshot_or_migration_output is not None:
        print(snapshot_or_migration_output, end="")
        return 0

    parser.error("unsupported command")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
