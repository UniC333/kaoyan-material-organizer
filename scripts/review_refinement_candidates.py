#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys

from build_refinement_queue import build_refinement_queue


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--subject")
    parser.add_argument("--chapter")
    parser.add_argument("--topn", type=int, default=20)
    parser.add_argument("--format", choices=("json", "quiet"), default="json")
    return parser.parse_args()


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    args = parse_args()
    payload = build_refinement_queue(subject=args.subject, chapter=args.chapter, limit=args.topn)
    result = {
        "refinement_contract_version": payload.get("refinement_contract_version", ""),
        "derived_from_question_history_count": payload.get("derived_from_question_history_count", 0),
        "count": len(payload.get("items", [])),
        "items": payload.get("items", []),
    }
    if args.format == "json":
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
