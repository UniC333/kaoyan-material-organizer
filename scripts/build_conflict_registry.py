#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys

from common import ensure_kb_layout, load_all_json, save_json


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--format", choices=("json", "quiet"), default="json")
    return parser.parse_args()


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    args = parse_args()
    layout = ensure_kb_layout()
    conflicts = sorted(load_all_json(layout["conflicts"]), key=lambda item: (item.get("subject", ""), item.get("syllabus_node_id", ""), item.get("conflict_id", "")))
    payload = {"count": len(conflicts), "conflicts": conflicts}
    save_json(layout["indexes"] / "conflict_registry.json", payload)
    if args.format == "json":
        print(json.dumps({"count": len(conflicts), "path": str(layout["indexes"] / "conflict_registry.json")}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
