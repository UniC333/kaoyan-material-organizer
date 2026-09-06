#!/usr/bin/env python3
from __future__ import annotations

from functools import partial
import argparse
import json
import locale
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from common import ensure_kb_layout, run_utf8_subprocess, runtime_subprocess_env
from config import CONFIG_SOURCE_ENV, RuntimeConfigError, load_runtime_config, reset_runtime_config_cache
from kaoyan_kb.cli.learner_handlers import _learner_exercise_output
from kaoyan_kb.cli.review_handlers import _review_refinement_decide
from kaoyan_kb.cli.maintain_handlers import _build_weekly_refresh_fallback
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

    run_output = dispatch_run_maintain(args, run_script, partial(_build_weekly_refresh_fallback, run_script=run_script))
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
