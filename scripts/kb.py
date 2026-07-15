#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import locale
import subprocess
import sys
from pathlib import Path
from typing import Any

from common import INDEX_DIRNAME, ensure_kb_layout, learner_file_map, load_json_or_default, now_iso, run_utf8_subprocess, save_json
from config import load_runtime_config
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
  book            Paper-book intake, OCR, PDF source, and classify handoff
  snapshot/run    Recovery checkpoints and resumable run manifests

Common examples:
  kb.py doctor
  kb.py sync --subject 数学 --format json
  kb.py query --subject 408 --query "栈和队列的区别" --format json
  kb.py review evidence queue --subject 数学 --format json
  kb.py learner daily-card --plan-date 2026-07-07 --format json
  kb.py book inspect --book-root <book-root> --format json
"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=ROOT_DESCRIPTION,
        epilog=ROOT_EPILOG,
        formatter_class=HelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="command", required=True, title="commands")

    add_core_commands(subparsers, formatter_class=HelpFormatter)

    add_review_commands(subparsers, formatter_class=HelpFormatter)

    add_learner_commands(subparsers, formatter_class=HelpFormatter)

    add_run_maintain_commands(subparsers, formatter_class=HelpFormatter)

    add_book_commands(subparsers, formatter_class=HelpFormatter)
    '''book = subparsers.add_parser(
        "book",
        help="paper-book intake, OCR, PDF source registration, and classify handoff",
        description="Paper-book intake, OCR, PDF source registration, and classify handoff.",
        formatter_class=HelpFormatter,
    )
    book_sub = book.add_subparsers(dest="book_command", required=True)
    inspect = book_sub.add_parser("inspect")
    inspect.add_argument("--book-root", required=True)
    inspect.add_argument("--dry-run", action="store_true")
    inspect.add_argument("--min-width", type=int)
    inspect.add_argument("--min-height", type=int)
    inspect.add_argument("--blur-threshold", type=float)
    inspect.add_argument("--phash-distance", type=int)
    inspect.add_argument("--format", choices=("json", "quiet"), default="json")
    map_pages = book_sub.add_parser("map-pages")
    map_pages.add_argument("--book-root", required=True)
    map_pages.add_argument("--dry-run", action="store_true")
    map_pages.add_argument("--format", choices=("json", "quiet"), default="json")
    classify = book_sub.add_parser("classify")
    classify.add_argument("--book-root", required=True)
    classify.add_argument("--format", choices=("json", "quiet"), default="json")
    generate_chapters = book_sub.add_parser("generate-chapters")
    generate_chapters.add_argument("--book-root", required=True)
    generate_chapters.add_argument("--context-json", required=True)
    generate_chapters.add_argument("--plan-json", required=True)
    generate_chapters.add_argument("--format", choices=("json", "quiet"), default="json")
    register_pdf = book_sub.add_parser("register-pdf-source")
    register_pdf.add_argument("--subject", required=True)
    register_pdf.add_argument("--book-title", required=True)
    register_pdf.add_argument("--pdf-path", required=True)
    register_pdf.add_argument("--edition", default="")
    register_pdf.add_argument("--format", choices=("json", "quiet"), default="json")
    link_parallel = book_sub.add_parser("link-parallel-sources")
    link_parallel.add_argument("--subject", required=True)
    link_parallel.add_argument("--book-title", required=True)
    link_parallel.add_argument("--image-book-root", required=True)
    link_parallel.add_argument("--pdf-source-id", default="")
    link_parallel.add_argument("--context-root", default="")
    link_parallel.add_argument("--format", choices=("json", "quiet"), default="json")
    repair_parallel = book_sub.add_parser("repair-parallel-provenance")
    repair_parallel.add_argument("--subject", required=True)
    repair_parallel.add_argument("--book-title", required=True)
    repair_parallel.add_argument("--chapter-number", type=int, required=True)
    repair_parallel.add_argument("--format", choices=("json", "quiet"), default="json")
    pdf_acceptance = book_sub.add_parser("pdf-acceptance-checklist")
    pdf_acceptance.add_argument("--subject", required=True)
    pdf_acceptance.add_argument("--book-title", action="append", default=[])
    pdf_acceptance.add_argument("--format", choices=("json", "quiet"), default="json")
    pdf_anchor_quality = book_sub.add_parser("pdf-anchor-quality")
    pdf_anchor_quality.add_argument("--subject", required=True)
    pdf_anchor_quality.add_argument("--book-title", action="append", default=[])
    pdf_anchor_quality.add_argument("--format", choices=("json", "quiet"), default="json")
    parallel_source_guard = book_sub.add_parser("parallel-source-guard")
    parallel_source_guard.add_argument("--subject", required=True)
    parallel_source_guard.add_argument("--book-title", action="append", default=[])
    parallel_source_guard.add_argument("--format", choices=("json", "quiet"), default="json")
    ocr = book_sub.add_parser("ocr")
    ocr.add_argument("--book-root", required=True)
    ocr.add_argument("--provider")
    ocr.add_argument("--model")
    ocr.add_argument("--fixture-json")
    ocr.add_argument("--allow-remote", action="store_true")
    ocr.add_argument("--yes", action="store_true")
    ocr.add_argument("--max-retries", type=int, default=2)
    ocr.add_argument("--require-quality-gate", action="store_true")
    ocr.add_argument("--quality-report")
    ocr.add_argument("--format", choices=("json", "quiet"), default="json")
    ocr_pdf = book_sub.add_parser("ocr-pdf-source")
    ocr_pdf.add_argument("--subject", required=True)
    ocr_pdf.add_argument("--book-title", required=True)
    ocr_pdf.add_argument("--pdf-source-id", default="")
    ocr_pdf.add_argument("--chapter-number", action="append", type=int, default=[])
    ocr_pdf.add_argument("--provider")
    ocr_pdf.add_argument("--model")
    ocr_pdf.add_argument("--fixture-json")
    ocr_pdf.add_argument("--allow-remote", action="store_true")
    ocr_pdf.add_argument("--yes", action="store_true")
    ocr_pdf.add_argument("--dpi", type=int, default=200)
    ocr_pdf.add_argument("--format", choices=("json", "quiet"), default="json")
    pdf_ocr_review_status = book_sub.add_parser("pdf-ocr-review-status")
    pdf_ocr_review_status.add_argument("--subject", required=True)
    pdf_ocr_review_status.add_argument("--book-title", required=True)
    pdf_ocr_review_status.add_argument("--pdf-source-id", default="")
    pdf_ocr_review_status.add_argument("--report-path", default="")
    pdf_ocr_review_status.add_argument("--format", choices=("json", "quiet"), default="json")
    pdf_ocr_review_artifact = book_sub.add_parser("pdf-ocr-review-artifact")
    pdf_ocr_review_artifact.add_argument("--subject", required=True)
    pdf_ocr_review_artifact.add_argument("--book-title", required=True)
    pdf_ocr_review_artifact.add_argument("--pdf-source-id", default="")
    pdf_ocr_review_artifact.add_argument("--bridge-report-path", default="")
    pdf_ocr_review_artifact.add_argument("--format", choices=("json", "quiet"), default="json")
    ocr_review = book_sub.add_parser("ocr-review")
    ocr_review_sub = ocr_review.add_subparsers(dest="ocr_review_command", required=True)
    ocr_review_queue = ocr_review_sub.add_parser("queue")
    ocr_review_queue.add_argument("--book-root", required=True)
    ocr_review_queue.add_argument("--review-type", choices=("table", "equation", "low-confidence"))
    ocr_review_queue.add_argument("--format", choices=("json", "quiet"), default="json")
    ocr_review_apply = ocr_review_sub.add_parser("apply")
    ocr_review_apply.add_argument("--request-key", required=True)
    ocr_review_apply.add_argument("--block-id", required=True)
    ocr_review_apply.add_argument("--review-status", choices=("pending", "accepted", "rejected", "ignored"), required=True)
    ocr_review_apply.add_argument("--corrected-text", default="")
    ocr_review_apply.add_argument("--note", default="")
    ocr_review_apply.add_argument("--format", choices=("json", "quiet"), default="json")'''

    add_snapshot_migrate_commands(subparsers, formatter_class=HelpFormatter)
    return parser


def run_script(name: str, *args: str) -> str:
    try:
        completed = run_utf8_subprocess(
            [sys.executable, str(SCRIPT_DIR / name), *args],
            command_label=f"python:{name}",
            check=True,
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
        f"- schema_version_exists: {bool_status(bool(payload['schema_version_exists']))}",
        f"- schema_dir_exists: {bool_status(bool(payload['schema_dir_exists']))}",
        f"- ocr_package_manager: {payload['ocr_env'].get('package_manager', '')}",
        f"- fixture_ocr_ready: {bool_status(bool(payload['ocr_acceptance'].get('fixture_ocr_ready')))}",
        f"- live_smoke_ready: {bool_status(bool(payload['ocr_acceptance'].get('live_smoke_ready')))}",
        f"- live_smoke_manual_only: {'yes' if payload['ocr_acceptance'].get('manual_only') else 'no'}",
        f"- live_smoke_command: {payload['ocr_acceptance'].get('live_smoke_command', '')}",
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


def _learner_exercise_output(args: argparse.Namespace) -> str:
    from learner_events import append_event, rebuild_views

    subject = args.subject or _subject_from_node_id(args.node)
    payload = {
        "node_id": args.node,
        "result": args.result,
        "tags": _dedupe_strings(list(args.tag)),
        "note": args.note,
    }
    event = append_event(
        subject=subject,
        chapter_title=args.chapter or "",
        event_type="exercise_logged",
        payload=payload,
    )
    rebuild_views()
    result = {
        "event_id": event["event_id"],
        "event_type": event["event_type"],
        "subject": subject,
        "chapter_title": args.chapter or "",
        "node_id": args.node,
        "result": args.result,
        "cli_write_scope": "learner_events_and_derived_views",
        "learner_layer_only": True,
    }
    return json.dumps(result, ensure_ascii=False, indent=2) + "\n" if args.format == "json" else ""


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = build_parser()
    args = parser.parse_args()

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

    if False and args.command == "learner":
        if args.learner_command == "orchestration-context":
            forwarded: list[str] = ["--freshness-days", str(args.freshness_days), "--format", args.format]
            if args.vault_root:
                forwarded.extend(["--vault-root", args.vault_root])
            if args.as_of:
                forwarded.extend(["--as-of", args.as_of])
            print(run_script("build_study_orchestration_context.py", *forwarded), end="")
            return 0
        if args.learner_command == "daily-card":
            forwarded = ["--plan-date", args.plan_date, "--format", args.format]
            if args.vault_root:
                forwarded.extend(["--vault-root", args.vault_root])
            print(run_script("build_daily_study_card.py", *forwarded), end="")
            return 0
        if args.learner_command == "review-followups":
            forwarded = ["--plan-date", args.plan_date, "--format", args.format]
            if args.vault_root:
                forwarded.extend(["--vault-root", args.vault_root])
            print(run_script("build_review_followups.py", *forwarded), end="")
            return 0
        if args.learner_command == "weekly-orchestration":
            forwarded = ["--plan-date", args.plan_date, "--format", args.format]
            if args.vault_root:
                forwarded.extend(["--vault-root", args.vault_root])
            if args.override_json:
                forwarded.extend(["--override-json", args.override_json])
            print(run_script("build_weekly_orchestration.py", *forwarded), end="")
            return 0
        if args.learner_command == "teacher-loop-artifact":
            forwarded = ["--plan-date", args.plan_date, "--format", args.format]
            if args.vault_root:
                forwarded.extend(["--vault-root", args.vault_root])
            print(run_script("build_r17_teacher_loop_artifact.py", *forwarded), end="")
            return 0
        if args.learner_command == "adaptive-coaching-context":
            forwarded = ["--plan-date", args.plan_date, "--stale-signal-days", str(args.stale_signal_days), "--format", args.format]
            if args.vault_root:
                forwarded.extend(["--vault-root", args.vault_root])
            print(run_script("build_adaptive_coaching_context.py", *forwarded), end="")
            return 0
        if args.learner_command == "adaptive-coaching-packet":
            forwarded = ["--plan-date", args.plan_date, "--format", args.format]
            if args.vault_root:
                forwarded.extend(["--vault-root", args.vault_root])
            print(run_script("build_adaptive_coaching_packet.py", *forwarded), end="")
            return 0
        if args.learner_command == "coaching-feedback-loop":
            forwarded = ["--plan-date", args.plan_date, "--format", args.format]
            if args.vault_root:
                forwarded.extend(["--vault-root", args.vault_root])
            print(run_script("build_coaching_feedback_loop.py", *forwarded), end="")
            return 0
        if args.learner_command == "closed-loop-operations":
            forwarded = ["--plan-date", args.plan_date, "--format", args.format]
            if args.vault_root:
                forwarded.extend(["--vault-root", args.vault_root])
            if args.override_json:
                forwarded.extend(["--override-json", args.override_json])
            print(run_script("build_closed_loop_operations.py", *forwarded), end="")
            return 0
        if args.learner_command == "adaptive-coaching-artifact":
            forwarded = ["--plan-date", args.plan_date, "--format", args.format]
            if args.vault_root:
                forwarded.extend(["--vault-root", args.vault_root])
            print(run_script("build_r18_adaptive_coaching_artifact.py", *forwarded), end="")
            return 0
        if args.learner_command == "longitudinal-tutoring-context":
            forwarded = ["--plan-date", args.plan_date, "--stale-cycle-days", str(args.stale_cycle_days), "--format", args.format]
            if args.vault_root:
                forwarded.extend(["--vault-root", args.vault_root])
            print(run_script("build_longitudinal_tutoring_context.py", *forwarded), end="")
            return 0
        if args.learner_command == "tutoring-strategy-packet":
            forwarded = ["--plan-date", args.plan_date, "--format", args.format]
            if args.vault_root:
                forwarded.extend(["--vault-root", args.vault_root])
            print(run_script("build_tutoring_strategy_packet.py", *forwarded), end="")
            return 0
        if args.learner_command == "tutoring-feedback-loop":
            forwarded = ["--plan-date", args.plan_date, "--format", args.format]
            if args.vault_root:
                forwarded.extend(["--vault-root", args.vault_root])
            print(run_script("build_tutoring_feedback_loop.py", *forwarded), end="")
            return 0
        if args.learner_command == "long-horizon-operations":
            forwarded = ["--plan-date", args.plan_date, "--format", args.format]
            if args.vault_root:
                forwarded.extend(["--vault-root", args.vault_root])
            if args.override_json:
                forwarded.extend(["--override-json", args.override_json])
            print(run_script("build_long_horizon_operations.py", *forwarded), end="")
            return 0
        if args.learner_command == "longitudinal-tutoring-artifact":
            forwarded = ["--plan-date", args.plan_date, "--format", args.format]
            if args.vault_root:
                forwarded.extend(["--vault-root", args.vault_root])
            print(run_script("build_r19_longitudinal_tutoring_artifact.py", *forwarded), end="")
            return 0
        if args.learner_command == "autonomous-trigger-contract":
            forwarded = ["--plan-date", args.plan_date, "--format", args.format]
            if args.vault_root:
                forwarded.extend(["--vault-root", args.vault_root])
            print(run_script("build_autonomous_trigger_contract.py", *forwarded), end="")
            return 0
        if args.learner_command == "autonomous-action-plan":
            forwarded = ["--plan-date", args.plan_date, "--format", args.format]
            if args.vault_root:
                forwarded.extend(["--vault-root", args.vault_root])
            print(run_script("build_autonomous_action_plan.py", *forwarded), end="")
            return 0
        if args.learner_command == "autonomous-governance-ledger":
            forwarded = ["--plan-date", args.plan_date, "--format", args.format]
            if args.vault_root:
                forwarded.extend(["--vault-root", args.vault_root])
            if args.governance_json:
                forwarded.extend(["--governance-json", args.governance_json])
            print(run_script("build_autonomous_governance_ledger.py", *forwarded), end="")
            return 0
        if args.learner_command == "autonomous-tutoring-artifact":
            forwarded = ["--plan-date", args.plan_date, "--format", args.format]
            if args.vault_root:
                forwarded.extend(["--vault-root", args.vault_root])
            print(run_script("build_r20_autonomous_tutoring_artifact.py", *forwarded), end="")
            return 0
        if args.learner_command == "exercise":
            from learner_events import append_event, rebuild_views

            subject = args.subject or _subject_from_node_id(args.node)
            payload = {
                "node_id": args.node,
                "result": args.result,
                "tags": _dedupe_strings(list(args.tag)),
                "note": args.note,
            }
            event = append_event(
                subject=subject,
                chapter_title=args.chapter or "",
                event_type="exercise_logged",
                payload=payload,
            )
            rebuild_views()
            result = {
                "event_id": event["event_id"],
                "event_type": event["event_type"],
                "subject": subject,
                "chapter_title": args.chapter or "",
                "node_id": args.node,
                "result": args.result,
                "cli_write_scope": "learner_events_and_derived_views",
                "learner_layer_only": True,
            }
            if args.format == "json":
                print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0

    book_output = dispatch_book(args, run_script)
    if book_output is not None:
        print(book_output, end="")
        return 0

    if False and args.command == "book":
        if args.book_command == "inspect":
            forwarded = ["--book-root", args.book_root, "--format", args.format]
            if args.dry_run:
                forwarded.append("--dry-run")
            if args.min_width is not None:
                forwarded.extend(["--min-width", str(args.min_width)])
            if args.min_height is not None:
                forwarded.extend(["--min-height", str(args.min_height)])
            if args.blur_threshold is not None:
                forwarded.extend(["--blur-threshold", str(args.blur_threshold)])
            if args.phash_distance is not None:
                forwarded.extend(["--phash-distance", str(args.phash_distance)])
            print(run_script("ingest_paper_book.py", *forwarded), end="")
            return 0
        if args.book_command == "map-pages":
            forwarded = ["--book-root", args.book_root, "--format", args.format]
            if args.dry_run:
                forwarded.append("--dry-run")
            print(run_script("map_book_pages.py", *forwarded), end="")
            return 0
        if args.book_command == "classify":
            forwarded = ["--book-root", args.book_root, "--format", args.format]
            print(run_script("classify_book_pages.py", *forwarded), end="")
            return 0
        if args.book_command == "generate-chapters":
            forwarded = [
                "--book-root",
                args.book_root,
                "--context-json",
                args.context_json,
                "--plan-json",
                args.plan_json,
                "--format",
                args.format,
            ]
            print(run_script("generate_book_chapters.py", *forwarded), end="")
            return 0
        if args.book_command == "register-pdf-source":
            forwarded = [
                "--subject",
                args.subject,
                "--book-title",
                args.book_title,
                "--pdf-path",
                args.pdf_path,
                "--edition",
                args.edition,
                "--format",
                args.format,
            ]
            print(run_script("register_pdf_book_source.py", *forwarded), end="")
            return 0
        if args.book_command == "link-parallel-sources":
            forwarded = [
                "--subject",
                args.subject,
                "--book-title",
                args.book_title,
                "--image-book-root",
                args.image_book_root,
                "--format",
                args.format,
            ]
            if args.pdf_source_id:
                forwarded.extend(["--pdf-source-id", args.pdf_source_id])
            if args.context_root:
                forwarded.extend(["--context-root", args.context_root])
            print(run_script("link_parallel_book_sources.py", *forwarded), end="")
            return 0
        if args.book_command == "repair-parallel-provenance":
            forwarded = [
                "--subject",
                args.subject,
                "--book-title",
                args.book_title,
                "--chapter-number",
                str(args.chapter_number),
                "--format",
                args.format,
            ]
            print(run_script("repair_parallel_book_provenance.py", *forwarded), end="")
            return 0
        if args.book_command == "pdf-acceptance-checklist":
            forwarded = ["--subject", args.subject, "--format", args.format]
            for title in args.book_title:
                forwarded.extend(["--book-title", title])
            print(run_script("build_pdf_acceptance_checklist.py", *forwarded), end="")
            return 0
        if args.book_command == "pdf-anchor-quality":
            forwarded = ["--subject", args.subject, "--format", args.format]
            for title in args.book_title:
                forwarded.extend(["--book-title", title])
            print(run_script("build_pdf_anchor_quality_report.py", *forwarded), end="")
            return 0
        if args.book_command == "parallel-source-guard":
            forwarded = ["--subject", args.subject, "--format", args.format]
            for title in args.book_title:
                forwarded.extend(["--book-title", title])
            print(run_script("build_parallel_source_guard_report.py", *forwarded), end="")
            return 0
        if args.book_command == "ocr":
            forwarded = ["--book-root", args.book_root, "--max-retries", str(args.max_retries), "--format", args.format]
            if args.provider:
                forwarded.extend(["--provider", args.provider])
            if args.model:
                forwarded.extend(["--model", args.model])
            if args.fixture_json:
                forwarded.extend(["--fixture-json", args.fixture_json])
            if args.allow_remote:
                forwarded.append("--allow-remote")
            if args.yes:
                forwarded.append("--yes")
            if args.require_quality_gate:
                forwarded.append("--require-quality-gate")
            if args.quality_report:
                forwarded.extend(["--quality-report", args.quality_report])
            print(run_script("ocr_book_pages.py", *forwarded), end="")
            return 0
        if args.book_command == "ocr-pdf-source":
            forwarded = [
                "--subject",
                args.subject,
                "--book-title",
                args.book_title,
                "--pdf-source-id",
                args.pdf_source_id,
                "--dpi",
                str(args.dpi),
                "--format",
                args.format,
            ]
            for item in args.chapter_number:
                forwarded.extend(["--chapter-number", str(item)])
            if args.provider:
                forwarded.extend(["--provider", args.provider])
            if args.model:
                forwarded.extend(["--model", args.model])
            if args.fixture_json:
                forwarded.extend(["--fixture-json", args.fixture_json])
            if args.allow_remote:
                forwarded.append("--allow-remote")
            if args.yes:
                forwarded.append("--yes")
            print(run_script("ocr_pdf_book_source.py", *forwarded), end="")
            return 0
        if args.book_command == "pdf-ocr-review-status":
            forwarded = [
                "--subject",
                args.subject,
                "--book-title",
                args.book_title,
                "--pdf-source-id",
                args.pdf_source_id,
                "--format",
                args.format,
            ]
            if args.report_path:
                forwarded.extend(["--report-path", args.report_path])
            print(run_script("build_pdf_ocr_review_status.py", *forwarded), end="")
            return 0
        if args.book_command == "pdf-ocr-review-artifact":
            forwarded = [
                "--subject",
                args.subject,
                "--book-title",
                args.book_title,
                "--pdf-source-id",
                args.pdf_source_id,
                "--format",
                args.format,
            ]
            if args.bridge_report_path:
                forwarded.extend(["--bridge-report-path", args.bridge_report_path])
            print(run_script("build_pdf_ocr_review_artifact.py", *forwarded), end="")
            return 0
        if args.book_command == "ocr-review":
            if args.ocr_review_command == "queue":
                forwarded = ["queue", "--book-root", args.book_root, "--format", args.format]
                if args.review_type:
                    forwarded.extend(["--review-type", args.review_type])
                print(run_script("ocr\\review.py", *forwarded), end="")
                return 0
            if args.ocr_review_command == "apply":
                forwarded = [
                    "apply",
                    "--request-key",
                    args.request_key,
                    "--block-id",
                    args.block_id,
                    "--review-status",
                    args.review_status,
                    "--corrected-text",
                    args.corrected_text,
                    "--note",
                    args.note,
                    "--format",
                    args.format,
                ]
                print(run_script("ocr\\review.py", *forwarded), end="")
                return 0

    snapshot_or_migration_output = dispatch_snapshot_migrate(args, run_script)
    if snapshot_or_migration_output is not None:
        print(snapshot_or_migration_output, end="")
        return 0

    parser.error("unsupported command")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
