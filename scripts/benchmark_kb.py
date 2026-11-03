#!/usr/bin/env python3
"""Thin CLI for the isolated query/ask/sync baseline."""
from __future__ import annotations

import argparse
import json
import sys

from kaoyan_kb.diagnostics.command_baseline import run_baseline


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples", type=int, default=20)
    parser.add_argument("--warmups", type=int, default=1)
    parser.add_argument("--query-threshold", type=float, default=2.0, help="query P95 threshold in seconds")
    args = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    try:
        report = run_baseline(args.samples, args.warmups, args.query_threshold)
    except (ValueError, RuntimeError) as exc:
        parser.exit(1, f"{exc}\n")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
