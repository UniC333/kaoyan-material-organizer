#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from common import display_path, ensure_parent_dir, filesystem_path, load_runtime_config, now_iso, save_json, sha256_for_file


STAGING_MARKER = ".kaoyan-snapshot-machine-owned.json"
MIN_CAPACITY_RESERVE_BYTES = 1024 * 1024


class SnapshotCapacityError(RuntimeError):
    pass


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--format", choices=("json", "quiet"), default="json")
    return parser.parse_args()


def snapshot_root() -> Path:
    return load_runtime_config().backup_root / "snapshots"


def staging_root(root: Path) -> Path:
    return root / ".staging"


def allocate_snapshot_id(root: Path) -> str:
    stamp = datetime.now().strftime("%Y%m%d")
    root.mkdir(parents=True, exist_ok=True)
    existing = sorted(root.glob(f"SNAP-{stamp}-*"))
    return f"SNAP-{stamp}-{len(existing) + 1:03d}"


def relative_files(base: Path, *, exclude: set[Path] | None = None) -> list[Path]:
    skipped = {path.resolve() for path in (exclude or set())}
    items: list[Path] = []
    if not base.exists():
        return items
    for root, _, filenames in os.walk(filesystem_path(base)):
        root_path = Path(display_path(root))
        for filename in sorted(filenames):
            path = root_path / filename
            resolved = path.resolve()
            if any(parent == resolved or parent in resolved.parents for parent in skipped):
                continue
            items.append(path.relative_to(base))
    return items


def copy_root(snapshot_files_root: Path, label: str, source_root: Path, rel_paths: list[Path]) -> list[dict[str, Any]]:
    copied: list[dict[str, Any]] = []
    for rel_path in rel_paths:
        source_path = source_root / rel_path
        target_path = snapshot_files_root / label / rel_path
        ensure_parent_dir(target_path)
        shutil.copy2(filesystem_path(source_path), filesystem_path(target_path))
        copied.append(
            {
                "root": label,
                "relative_path": rel_path.as_posix(),
                "sha256": sha256_for_file(source_path),
                "size_bytes": os.stat(filesystem_path(source_path)).st_size,
            }
        )
    return copied


def payload_size_bytes(roots: list[tuple[Path, list[Path]]]) -> int:
    return sum(os.stat(filesystem_path(root / relative)).st_size for root, paths in roots for relative in paths)


def ensure_snapshot_capacity(target_root: Path, *, payload_bytes: int) -> dict[str, int]:
    target_root.mkdir(parents=True, exist_ok=True)
    reserve_bytes = max(MIN_CAPACITY_RESERVE_BYTES, payload_bytes // 100)
    required_bytes = payload_bytes + reserve_bytes
    free_bytes = shutil.disk_usage(filesystem_path(target_root)).free
    if free_bytes < required_bytes:
        raise SnapshotCapacityError(
            f"insufficient snapshot capacity: free={free_bytes} required={required_bytes} payload={payload_bytes} reserve={reserve_bytes}"
        )
    return {"free_bytes": free_bytes, "payload_bytes": payload_bytes, "reserve_bytes": reserve_bytes, "required_bytes": required_bytes}


def write_staging_marker(staging_dir: Path, snapshot_id: str) -> None:
    staging_dir.mkdir(parents=True, exist_ok=True)
    save_json(
        staging_dir / STAGING_MARKER,
        {"machine_owned_snapshot": True, "snapshot_id": snapshot_id, "created_at": now_iso(), "purpose": "snapshot-create-staging"},
        ignored_compare_keys=(),
    )


def verify_snapshot(snapshot_dir: Path, files: list[dict[str, Any]]) -> dict[str, Any]:
    checksum_failures: list[str] = []
    for item in files:
        path = snapshot_dir / "files" / str(item["root"]) / str(item["relative_path"])
        if not os.path.exists(filesystem_path(path)) or sha256_for_file(path) != item["sha256"]:
            checksum_failures.append(str(item["relative_path"]))
    return {"verified": not checksum_failures, "checksum_failures": checksum_failures, "recoverable": not checksum_failures and bool(files)}


def cleanup_verified_staging(staging_dir: Path, *, verified: bool) -> dict[str, Any]:
    if not verified:
        return {"removed": False, "reason": "verification_failed", "staging_dir": str(staging_dir)}
    marker = staging_dir / STAGING_MARKER
    if not marker.exists():
        return {"removed": False, "reason": "ownership_marker_missing", "staging_dir": str(staging_dir)}
    # The caller promotes the verified directory atomically; this only records the approved lifecycle boundary.
    return {"removed": True, "reason": "promoted_to_snapshot", "staging_dir": str(staging_dir)}


def cleanup_empty_staging_root(candidate: Path, snapshots_dir: Path) -> dict[str, Any]:
    expected = staging_root(snapshots_dir).resolve()
    if candidate.resolve() != expected:
        return {"removed": False, "reason": "staging_root_boundary_rejected", "staging_root": str(candidate)}
    try:
        os.rmdir(filesystem_path(candidate))
    except FileNotFoundError:
        return {"removed": True, "reason": "already_absent", "staging_root": str(candidate)}
    except OSError as exc:
        return {"removed": False, "reason": "cleanup_failed", "staging_root": str(candidate), "error": str(exc)}
    return {"removed": True, "reason": "empty_staging_root_removed", "staging_root": str(candidate)}


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    args = parse_args()
    runtime = load_runtime_config()
    snapshots_dir = snapshot_root()
    snapshot_id = allocate_snapshot_id(snapshots_dir)
    snapshot_dir = snapshots_dir / snapshot_id

    workspace_root = runtime.workspace_root.resolve()
    vault_root = runtime.vault_root.resolve()
    kb_root = runtime.kb_root.resolve()
    backup_root = runtime.backup_root.resolve()

    workspace_files = relative_files(workspace_root, exclude={backup_root, kb_root, vault_root})
    vault_files = relative_files(vault_root)
    kb_files = relative_files(kb_root, exclude={backup_root})

    capacity = ensure_snapshot_capacity(
        snapshots_dir,
        payload_bytes=payload_size_bytes([(workspace_root, workspace_files), (vault_root, vault_files), (kb_root, kb_files)]),
    )
    staging_dir = staging_root(snapshots_dir) / snapshot_id
    write_staging_marker(staging_dir, snapshot_id)
    snapshot_files_root = staging_dir / "files"

    try:
        files = []
        files.extend(copy_root(snapshot_files_root, "workspace", workspace_root, workspace_files))
        files.extend(copy_root(snapshot_files_root, "vault", vault_root, vault_files))
        files.extend(copy_root(snapshot_files_root, "kb", kb_root, kb_files))

        manifest = {
            "snapshot_id": snapshot_id,
            "created_at": now_iso(),
            "workspace_root": str(workspace_root),
            "vault_root": str(vault_root),
            "kb_root": str(kb_root),
            "backup_root": str(backup_root),
            "snapshot_dir": str(snapshot_dir),
            "files": files,
            "file_count": len(files),
            "machine_owned_snapshot": True,
            "ownership_marker": STAGING_MARKER,
            "capacity": capacity,
        }
        save_json(staging_dir / "manifest.json", manifest, ignored_compare_keys=())
        verification = verify_snapshot(staging_dir, files)
        cleanup = cleanup_verified_staging(staging_dir, verified=bool(verification["verified"] and verification["recoverable"]))
        if not cleanup["removed"]:
            raise RuntimeError(f"snapshot verification failed: {verification}")
        os.replace(filesystem_path(staging_dir), filesystem_path(snapshot_dir))
        cleanup["post_promotion"] = cleanup_empty_staging_root(staging_dir.parent, snapshots_dir)
    except Exception:
        # Marker-proven staging remains for diagnosis; never erase an unverified copy.
        raise

    if args.format == "json":
        print(
            json.dumps(
                {
                    "snapshot_id": snapshot_id,
                    "backup_root": str(backup_root),
                    "snapshot_dir": str(snapshot_dir),
                    "file_count": len(files),
                    "capacity": capacity,
                    "verification": verification,
                    "staging_cleanup": cleanup,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
