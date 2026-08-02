#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from answer_local_question import build_answer_contract, render_text
from common import default_vault_root_arg, resolve_subject
from query_local_knowledge import query_knowledge
from save_local_answer import save_answer_contract, saved_at_label


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--vault-root")
    parser.add_argument("--subject", required=True)
    parser.add_argument("--chapter")
    parser.add_argument("--book-title")
    parser.add_argument("--question", required=True)
    parser.add_argument("--topk", type=int, default=3)
    parser.add_argument("--printed-page", type=int)
    parser.add_argument("--format", choices=("text", "json"), default="text")
    parser.add_argument("--save", action="store_true")
    parser.add_argument("--saved-at")
    return parser.parse_args()


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    args = parse_args()
    vault_root = Path(args.vault_root or default_vault_root_arg())
    subject, _ = resolve_subject(args.subject)
    result = query_knowledge(vault_root, subject, args.chapter, args.question, args.topk, args.printed_page, args.book_title)
    contract = build_answer_contract(result)

    if args.save:
        try:
            save_answer_contract(
                contract=contract,
                vault_root=vault_root,
                subject=subject,
                chapter=args.chapter,
                question=args.question,
                saved_at=args.saved_at,
            )
        except ValueError as exc:
            raise SystemExit(f"[ERROR] no saved-QA write was made: {exc}") from exc

    if args.format == "json":
        payload = {
            "saved": args.save,
            "saved_at": saved_at_label(args.saved_at) if args.save else "",
            "answer": contract,
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(render_text(contract), end="")
        if args.save:
            print(f"\n已沉淀到本地问答入口，记录日期：{saved_at_label(args.saved_at)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
