from __future__ import annotations

import argparse
import json
from collections.abc import Callable
from typing import Any


def add_core_commands(subparsers: argparse._SubParsersAction, *, formatter_class: type[argparse.HelpFormatter]) -> None:
    doctor = subparsers.add_parser("doctor", help="inspect runtime paths, OCR readiness, and terminal encoding guidance", description="Inspect runtime paths, OCR readiness, and terminal encoding guidance.", formatter_class=formatter_class)
    doctor.add_argument("--format", choices=("json", "text"), default="text")

    ask = subparsers.add_parser("ask", help="answer a saved/local question with optional handoff into learner history", description="Answer a local question and optionally save the result into learner history.", formatter_class=formatter_class)
    ask.add_argument("--vault-root")
    ask.add_argument("--subject", required=True)
    ask.add_argument("--chapter")
    ask.add_argument("--book-title")
    ask.add_argument("--question", required=True)
    ask.add_argument("--topk", type=int, default=3)
    ask.add_argument("--printed-page", type=int)
    ask.add_argument("--exercise-label")
    ask.add_argument("--format", choices=("text", "json"), default="json")
    ask.add_argument("--save", action="store_true")
    ask.add_argument("--saved-at")

    sync = subparsers.add_parser("sync", help="run the wrapped sync flow for fact sync, publish, and learner refresh", description="Run the wrapped sync flow for fact sync, publish, and learner refresh.", formatter_class=formatter_class)
    sync.add_argument("--subject")
    sync.add_argument("--context-json")
    sync.add_argument("--rebuild-kb", action="store_true")
    sync.add_argument("--publish-canonical", action="store_true")
    sync.add_argument("--refresh-learning", action="store_true")
    sync.add_argument("--indexes-only", action="store_true")
    sync.add_argument("--yes", action="store_true")
    sync.add_argument("--force", action="store_true")
    sync.add_argument("--no-backup", action="store_true")
    sync.add_argument("--format", choices=("json", "quiet"), default="json")

    query = subparsers.add_parser("query", help="search local knowledge and return a retrieval-grounded answer", description="Search local knowledge and return a retrieval-grounded answer.", formatter_class=formatter_class)
    query.add_argument("--vault-root")
    query.add_argument("--subject")
    query.add_argument("--chapter")
    query.add_argument("--book-title")
    query.add_argument("--query", required=True)
    query.add_argument("--topk", type=int, default=3)
    query.add_argument("--printed-page", type=int)
    query.add_argument("--exercise-label")
    query.add_argument("--format", choices=("text", "json"), default="json")


def dispatch_core(
    args: argparse.Namespace,
    run_script: Callable[..., str],
    doctor_payload: Callable[[], dict[str, Any]],
    render_doctor_text: Callable[[dict[str, Any]], str],
) -> str | None:
    if args.command == "doctor":
        payload = doctor_payload()
        return json.dumps(payload, ensure_ascii=False, indent=2) + "\n" if args.format == "json" else render_doctor_text(payload)

    if args.command == "ask":
        forwarded = ["--subject", args.subject, "--question", args.question, "--topk", str(args.topk), "--format", args.format]
        for key in ("vault_root", "chapter", "book_title", "saved_at", "printed_page", "exercise_label"):
            value = getattr(args, key)
            if value:
                forwarded.extend([f"--{key.replace('_', '-')}", str(value)])
        if args.save:
            forwarded.append("--save")
        return run_script("ask_local_knowledge.py", *forwarded)

    if args.command == "sync":
        forwarded: list[str] = []
        for key in ("subject", "context_json"):
            value = getattr(args, key)
            if value:
                forwarded.extend([f"--{key.replace('_', '-')}", str(value)])
        for flag in ("rebuild_kb", "publish_canonical", "refresh_learning", "indexes_only", "yes", "force", "no_backup"):
            if getattr(args, flag):
                forwarded.append(f"--{flag.replace('_', '-')}")
        return run_script("sync_exam_kb.py", *forwarded, "--format", args.format)

    if args.command == "query":
        forwarded = []
        for key in ("vault_root", "subject", "chapter", "book_title", "query", "printed_page", "exercise_label"):
            value = getattr(args, key)
            if value:
                forwarded.extend([f"--{key.replace('_', '-')}", str(value)])
        return run_script("query_local_knowledge.py", *forwarded, "--topk", str(args.topk), "--format", args.format)
    return None
