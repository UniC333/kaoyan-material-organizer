#!/usr/bin/env python3
from __future__ import annotations

import argparse
import io
import json
import sys
from contextlib import redirect_stdout

from answer_local_question import main as answer_main
from save_local_answer import main as save_main, saved_at_label


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--vault-root")
    parser.add_argument("--subject", required=True)
    parser.add_argument("--chapter")
    parser.add_argument("--question", required=True)
    parser.add_argument("--topk", type=int, default=3)
    parser.add_argument("--printed-page", type=int)
    parser.add_argument("--format", choices=("text", "json"), default="text")
    parser.add_argument("--save", action="store_true")
    parser.add_argument("--saved-at")
    return parser.parse_args()


def run_embedded(func, argv: list[str]) -> str:
    previous = sys.argv[:]
    capture = io.StringIO()
    try:
        sys.argv = argv
        with redirect_stdout(capture):
            func()
    finally:
        sys.argv = previous
    return capture.getvalue()


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    args = parse_args()
    answer_argv = [
        "answer_local_question.py",
        "--subject",
        args.subject,
        "--question",
        args.question,
        "--topk",
        str(args.topk),
        "--format",
        args.format,
    ]
    if args.vault_root:
        answer_argv.extend(["--vault-root", args.vault_root])
    if args.chapter:
        answer_argv.extend(["--chapter", args.chapter])
    if args.printed_page is not None:
        answer_argv.extend(["--printed-page", str(args.printed_page)])
    answer_output = run_embedded(answer_main, answer_argv)

    if args.save:
        save_argv = [
            "save_local_answer.py",
            "--subject",
            args.subject,
            "--question",
            args.question,
            "--topk",
            str(args.topk),
        ]
        if args.vault_root:
            save_argv.extend(["--vault-root", args.vault_root])
        if args.chapter:
            save_argv.extend(["--chapter", args.chapter])
        if args.printed_page is not None:
            save_argv.extend(["--printed-page", str(args.printed_page)])
        if args.saved_at:
            save_argv.extend(["--saved-at", args.saved_at])
        run_embedded(save_main, save_argv)

    if args.format == "json":
        payload = {
            "saved": args.save,
            "saved_at": saved_at_label(args.saved_at) if args.save else "",
            "answer": json.loads(answer_output or "{}"),
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(answer_output, end="")
        if args.save:
            print(f"\n已沉淀到本地问答入口，记录日期：{saved_at_label(args.saved_at)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
