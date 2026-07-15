#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

from common import ensure_kb_layout, ensure_parent_dir, filesystem_path, load_json_or_default, load_runtime_config, save_json, sha256_for_file


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshot-id", required=True)
    parser.add_argument("--format", choices=("json", "quiet"), default="json")
    return parser.parse_args()


def snapshot_root() -> Path:
    return load_runtime_config().backup_root / "snapshots"


def destination_roots() -> dict[str, Path]:
    runtime = load_runtime_config()
    return {
        "workspace": runtime.workspace_root.resolve(),
        "vault": runtime.vault_root.resolve(),
        "kb": runtime.kb_root.resolve(),
    }


def snapshot_file_set(manifest: dict) -> dict[str, set[str]]:
    files_by_root: dict[str, set[str]] = {"workspace": set(), "vault": set(), "kb": set()}
    for item in manifest.get("files", []):
        root_label = str(item.get("root", "")).strip()
        relative_path = str(item.get("relative_path", "")).replace("\\", "/").strip()
        if root_label in files_by_root and relative_path:
            files_by_root[root_label].add(relative_path)
    return files_by_root


def is_machine_owned_cleanup_candidate(relative_path: str) -> bool:
    normalized = str(relative_path or "").replace("\\", "/").strip()
    return normalized.startswith("runs/")


def prune_machine_only_files(kb_root: Path, expected_relative_paths: set[str]) -> dict[str, object]:
    pruned_paths: list[str] = []
    protected_paths: list[str] = []
    if not kb_root.exists():
        return {
            "machine_owned_pruned_count": 0,
            "human_owned_protected_count": 0,
            "pruned_relative_paths": pruned_paths,
            "protected_relative_paths": protected_paths,
        }
    for path in sorted(item for item in kb_root.rglob("*") if item.is_file()):
        relative = str(path.relative_to(kb_root)).replace("\\", "/")
        if relative in expected_relative_paths:
            continue
        if is_machine_owned_cleanup_candidate(relative):
            path.unlink()
            pruned_paths.append(relative)
            continue
        protected_paths.append(relative)
    for path in sorted((item for item in kb_root.rglob("*") if item.is_dir()), key=lambda item: len(item.parts), reverse=True):
        try:
            path.rmdir()
        except OSError:
            continue
    return {
        "machine_owned_pruned_count": len(pruned_paths),
        "human_owned_protected_count": len(protected_paths),
        "pruned_relative_paths": pruned_paths,
        "protected_relative_paths": protected_paths,
    }


def build_resume_boundary(expected_kb_files: set[str]) -> dict[str, object]:
    run_paths = sorted(path for path in expected_kb_files if path.startswith("runs/"))
    checkpoint_paths = [path for path in run_paths if path == "runs/resume_index.json" or path.startswith("runs/RUN-")]
    return {
        "resume_only_checkpoint_available": bool(checkpoint_paths),
        "checkpoint_relative_paths": checkpoint_paths,
    }


def recovery_report_path() -> Path:
    report_dir = snapshot_root() / "recovery"
    report_dir.mkdir(parents=True, exist_ok=True)
    return report_dir / "latest_restore_summary.json"


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    args = parse_args()
    root = snapshot_root()
    snapshot_dir = root / args.snapshot_id
    manifest = load_json_or_default(snapshot_dir / "manifest.json", {})
    if not manifest:
        payload = {"restored": False, "snapshot_id": args.snapshot_id, "reason": "snapshot_not_found"}
        if args.format == "json":
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    roots = destination_roots()
    expected_files = snapshot_file_set(manifest)
    restored_files = 0
    checksum_failures = 0
    for item in manifest.get("files", []):
        root_label = str(item.get("root", ""))
        relative_path = Path(str(item.get("relative_path", "")))
        if root_label not in roots or not str(relative_path):
            continue
        source_path = snapshot_dir / "files" / root_label / relative_path
        if not Path(filesystem_path(source_path)).exists():
            checksum_failures += 1
            continue
        destination = roots[root_label] / relative_path
        ensure_parent_dir(destination)
        shutil.copy2(filesystem_path(source_path), filesystem_path(destination))
        restored_files += 1
        if sha256_for_file(destination) != item.get("sha256", ""):
            checksum_failures += 1
    cleanup_summary = prune_machine_only_files(roots["kb"], expected_files.get("kb", set()))
    pruned_files = int(cleanup_summary["machine_owned_pruned_count"])
    recovery_status = "restored_with_cleanup_boundary" if checksum_failures == 0 else "restore_failed_with_recoverable_boundary"

    payload = {
        "restored": checksum_failures == 0,
        "recovery_status": recovery_status,
        "snapshot_id": args.snapshot_id,
        "restored_files": restored_files,
        "pruned_files": pruned_files,
        "checksum_failures": checksum_failures,
        "snapshot_dir": str(snapshot_dir),
        "snapshot_boundary": {
            "snapshot_status": "restore-ready-snapshot",
            "file_count": int(manifest.get("file_count", 0)),
        },
        "resume_boundary": build_resume_boundary(expected_files.get("kb", set())),
        "cleanup_summary": cleanup_summary,
    }
    save_json(recovery_report_path(), payload)
    if args.format == "json":
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
