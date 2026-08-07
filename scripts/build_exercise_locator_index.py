#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys

from kaoyan_kb.domain.exercise_locator import build_exercise_locator_index


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--format", choices=("json", "quiet"), default="json")
    args = parser.parse_args()
    payload = build_exercise_locator_index()
    if args.format == "json":
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
