#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

from common import display_path, filesystem_path, load_json_or_default, load_runtime_config


OWNERSHIP_MARKER = ".kaoyan-snapshot-machine-owned.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--keep-last", type=int, default=3)
    parser.add_argument("--keep-daily", type=int, default=0)
    parser.add_argument("--yes", action="store_true")
    parser.add_argument("--format", choices=("json", "quiet"), default="json")
    return parser.parse_args()


def snapshot_root() -> Path:
    return load_runtime_config().backup_root / "snapshots"


def dir_size_bytes(path: Path) -> int:
    total = 0
    if not path.exists():
        return total
    for child in path.rglob("*"):
        if child.is_file():
            total += child.stat().st_size
    return total


def _daily_bucket(created_at: str) -> str:
    if "T" not in created_at:
        return ""
    return created_at.split("T", 1)[0]


def load_snapshot_items(root: Path) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    if not root.exists():
        return items
    for snapshot_dir in sorted(path for path in root.iterdir() if path.is_dir() and not path.name.startswith(".")):
        manifest_path = snapshot_dir / "manifest.json"
        manifest = load_json_or_default(manifest_path, {})
        marker = load_json_or_default(snapshot_dir / OWNERSHIP_MARKER, {})
        machine_owned = bool(
            marker.get("machine_owned_snapshot") is True
            and marker.get("snapshot_id") == snapshot_dir.name
        )
        items.append(
            {
                "snapshot_id": str(manifest.get("snapshot_id") or snapshot_dir.name),
                "snapshot_dir": snapshot_dir,
                "created_at": str(manifest.get("created_at") or ""),
                "file_count": int(manifest.get("file_count", 0) or 0),
                "size_bytes": dir_size_bytes(snapshot_dir),
                "has_manifest": bool(manifest),
                "machine_owned": machine_owned,
            }
        )
    return items


def select_snapshots(items: list[dict[str, Any]], keep_last: int, keep_daily: int) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    complete = [item for item in items if item["has_manifest"] and item["machine_owned"]]
    marker_owned_incomplete = [item for item in items if not item["has_manifest"] and item["machine_owned"]]
    complete.sort(key=lambda item: (item["created_at"], item["snapshot_id"]))

    keep_ids: set[str] = set()
    if keep_last > 0:
        keep_ids.update(item["snapshot_id"] for item in complete[-keep_last:])

    if keep_daily > 0:
        by_day: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for item in complete:
            bucket = _daily_bucket(item["created_at"])
            if bucket:
                by_day[bucket].append(item)
        recent_days = sorted(by_day, reverse=True)[:keep_daily]
        for day in recent_days:
            keep_ids.add(by_day[day][-1]["snapshot_id"])

    kept = [item for item in items if item["snapshot_id"] in keep_ids]
    removable = [item for item in complete if item["snapshot_id"] not in keep_ids] + marker_owned_incomplete
    removable.sort(key=lambda item: (item["created_at"], item["snapshot_id"]))
    kept.sort(key=lambda item: (item["created_at"], item["snapshot_id"]))
    return kept, removable


def delete_snapshot_dir(snapshot_dir: Path, root: Path) -> None:
    resolved_root = Path(filesystem_path(root)).resolve()
    resolved_target = Path(filesystem_path(snapshot_dir)).resolve()
    if resolved_target == resolved_root or resolved_root not in resolved_target.parents:
        raise SystemExit(f"refusing to delete outside snapshot root: {snapshot_dir}")

    marker = load_json_or_default(snapshot_dir / OWNERSHIP_MARKER, {})
    if not (marker.get("machine_owned_snapshot") is True and marker.get("snapshot_id") == snapshot_dir.name):
        raise SystemExit(f"refusing to delete snapshot without machine ownership marker: {snapshot_dir}")

    for current_root, dirnames, filenames in os.walk(filesystem_path(snapshot_dir), topdown=False):
        current_path = Path(display_path(current_root))
        for filename in filenames:
            file_path = current_path / filename
            os.unlink(filesystem_path(file_path))
        for dirname in dirnames:
            dir_path = current_path / dirname
            os.rmdir(filesystem_path(dir_path))
    os.rmdir(filesystem_path(snapshot_dir))


def render_payload(
    *,
    root: Path,
    keep_last: int,
    keep_daily: int,
    execute: bool,
    kept: list[dict[str, Any]],
    removable: list[dict[str, Any]],
    removed_snapshot_ids: list[str],
    protected_snapshot_ids: list[str],
) -> dict[str, Any]:
    return {
        "mode": "execute" if execute else "dry-run",
        "snapshot_root": str(root),
        "keep_last": keep_last,
        "keep_daily": keep_daily,
        "kept_snapshot_ids": [item["snapshot_id"] for item in kept],
        "removable_count": len(removable),
        "removable_snapshot_ids": [item["snapshot_id"] for item in removable],
        "removed_snapshot_ids": removed_snapshot_ids,
        "protected_snapshot_ids": protected_snapshot_ids,
        "freed_bytes": sum(item["size_bytes"] for item in removable if item["snapshot_id"] in removed_snapshot_ids),
        "candidates": [
            {
                "snapshot_id": item["snapshot_id"],
                "created_at": item["created_at"],
                "file_count": item["file_count"],
                "size_bytes": item["size_bytes"],
                "state": "machine-owned" if item["machine_owned"] else "protected",
                "action": "remove" if item in removable else "keep",
            }
            for item in sorted(kept + removable, key=lambda entry: (entry["created_at"], entry["snapshot_id"]))
        ],
    }


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    args = parse_args()
    if args.keep_last < 0 or args.keep_daily < 0:
        raise SystemExit("keep-last and keep-daily must be >= 0")

    root = snapshot_root()
    items = load_snapshot_items(root)
    kept, removable = select_snapshots(items, args.keep_last, args.keep_daily)

    removed_snapshot_ids: list[str] = []
    if args.yes:
        for item in removable:
            delete_snapshot_dir(Path(item["snapshot_dir"]), root)
            removed_snapshot_ids.append(item["snapshot_id"])

    protected_snapshot_ids = [item["snapshot_id"] for item in items if not item["machine_owned"]]

    payload = render_payload(
        root=root,
        keep_last=args.keep_last,
        keep_daily=args.keep_daily,
        execute=args.yes,
        kept=kept,
        removable=removable,
        removed_snapshot_ids=removed_snapshot_ids,
        protected_snapshot_ids=protected_snapshot_ids,
    )
    if args.format == "json":
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
