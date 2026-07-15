#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from common import load_json_or_default, load_runtime_config


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--format", choices=("json", "quiet"), default="json")
    return parser.parse_args()


def snapshot_root() -> Path:
    return load_runtime_config().backup_root / "snapshots"


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    args = parse_args()
    root = snapshot_root()
    items: list[dict[str, Any]] = []
    if root.exists():
        for snapshot_dir in sorted(path for path in root.iterdir() if path.is_dir()):
            manifest = load_json_or_default(snapshot_dir / "manifest.json", {})
            if not manifest:
                continue
            items.append(
                {
                    "snapshot_id": manifest.get("snapshot_id", snapshot_dir.name),
                    "created_at": manifest.get("created_at", ""),
                    "snapshot_dir": str(snapshot_dir),
                    "file_count": int(manifest.get("file_count", 0) or 0),
                }
            )
    if args.format == "json":
        print(json.dumps({"count": len(items), "items": items}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
