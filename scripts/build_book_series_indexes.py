#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys

from kaoyan_kb.domain.book_series import build_book_series_index, build_exercise_pair_index


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser()
    parser.add_argument("--format", choices=("json", "quiet"), default="json")
    args = parser.parse_args()
    series = build_book_series_index()
    pairs = build_exercise_pair_index()
    if args.format == "json":
        print(json.dumps({"series": series["summary"], "exercise_pairs": pairs["summary"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
