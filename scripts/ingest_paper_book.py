#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from config import load_runtime_config
from paper_book_assets import inspect_book_images


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--book-root", required=True)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--min-width", type=int)
    parser.add_argument("--min-height", type=int)
    parser.add_argument("--blur-threshold", type=float)
    parser.add_argument("--phash-distance", type=int)
    parser.add_argument("--format", choices=("json", "quiet"), default="json")
    return parser


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    args = build_parser().parse_args()
    runtime = load_runtime_config()
    payload = inspect_book_images(
        book_root=Path(args.book_root),
        runtime=runtime,
        dry_run=args.dry_run,
        min_width=args.min_width,
        min_height=args.min_height,
        blur_threshold=args.blur_threshold,
        phash_distance=args.phash_distance,
    )
    if args.format == "json":
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
