#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path, PurePosixPath

from kaoyan_kb.storage.snapshot_restore import RestoreTransaction, is_redirected, path_stat, preflight, regular_files_under, reject_redirected_destination, resolve_path
from common import ensure_parent_dir, filesystem_path, load_json_or_default, load_runtime_config, save_json, sha256_for_file


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
        "workspace": resolve_path(runtime.workspace_root),
        "vault": resolve_path(runtime.vault_root),
        "kb": resolve_path(runtime.kb_root),
    }


def snapshot_file_set(manifest: dict) -> dict[str, set[str]]:
    files_by_root: dict[str, set[str]] = {"workspace": set(), "vault": set(), "kb": set()}
    for item in manifest.get("files", []):
        root_label = str(item.get("root", "")).strip()
        relative_path = PurePosixPath(str(item.get("relative_path", "")).replace("\\", "/")).as_posix()
        if root_label in files_by_root and relative_path:
            files_by_root[root_label].add(relative_path)
    return files_by_root


def is_machine_owned_cleanup_candidate(relative_path: str) -> bool:
    normalized = str(relative_path or "").replace("\\", "/")
    return normalized.startswith("runs/")


def prune_machine_only_files(kb_root: Path, expected_relative_paths: set[str], *, candidate_paths=None) -> dict[str, object]:
    pruned_paths: list[str] = []
    protected_paths: list[str] = []
    if path_stat(kb_root) is None:
        return {
            "machine_owned_pruned_count": 0,
            "human_owned_protected_count": 0,
            "pruned_relative_paths": pruned_paths,
            "protected_relative_paths": protected_paths,
        }
    candidates = candidate_paths if candidate_paths is not None else regular_files_under(kb_root)
    for path in sorted(candidates):
        relative = str(path.relative_to(kb_root)).replace("\\", "/")
        if relative in expected_relative_paths:
            continue
        state = path_stat(path, follow_symlinks=False)
        if state is None:
            continue
        if not is_redirected(state) and is_machine_owned_cleanup_candidate(relative):
            os.unlink(filesystem_path(path))
            pruned_paths.append(relative)
            continue
        protected_paths.append(relative)
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
    os.makedirs(filesystem_path(report_dir), exist_ok=True)
    return report_dir / "latest_restore_summary.json"


def restore_snapshot(snapshot_id: str) -> dict:
    root = snapshot_root()
    if not snapshot_id or snapshot_id in {".", ".."} or any(c in snapshot_id for c in "/\\:"):
        return {"restored": False, "snapshot_id": snapshot_id, "reason": "snapshot_id_invalid"}
    snapshot_dir = root / snapshot_id
    try:
        resolve_path(snapshot_dir).relative_to(resolve_path(root))
        manifest = load_json_or_default(Path(filesystem_path(snapshot_dir / "manifest.json")), {})
    except (OSError, ValueError) as exc:
        return {"restored": False, "snapshot_id": snapshot_id, "reason": "snapshot_unreadable", "error": str(exc)}
    if not manifest:
        return {"restored": False, "snapshot_id": snapshot_id, "reason": "snapshot_not_found"}

    roots = destination_roots()
    plan, errors = preflight(snapshot_dir, manifest, roots)
    if errors:
        return {"restored": False, "snapshot_id": snapshot_id, "reason": "snapshot_preflight_failed",
                "restored_files": 0, "pruned_files": 0, "checksum_failures": len(errors), "errors": errors}
    expected_files = snapshot_file_set(manifest)
    for _, destination, _ in plan:
        try:
            expected_files["kb"].add(destination.relative_to(roots["kb"]).as_posix())
        except ValueError:
            pass
    transaction = None
    try:
        report_path = root / "recovery" / "latest_restore_summary.json"
        reject_redirected_destination(report_path, root)
        # Freeze the cleanup set before copying; never prune files created later.
        candidates = regular_files_under(roots["kb"])
        cleanup_paths = [path for path in candidates
                         if not is_redirected(path_stat(path, follow_symlinks=False))
                         and is_machine_owned_cleanup_candidate(path.relative_to(roots["kb"]).as_posix())
                         and path.relative_to(roots["kb"]).as_posix() not in expected_files["kb"]]
        transaction = RestoreTransaction([destination for _, destination, _ in plan] + cleanup_paths + [report_path],
                                         backup_root=root)
        for source, destination, expected in plan:
            ensure_parent_dir(destination)
            descriptor, name = tempfile.mkstemp(prefix=".snapshot-restore-", dir=filesystem_path(destination.parent))
            os.close(descriptor)
            temporary = Path(name)
            try:
                shutil.copy2(filesystem_path(source), filesystem_path(temporary))
                if sha256_for_file(temporary) != expected:
                    raise OSError(f"restored checksum mismatch: {destination}")
                os.replace(filesystem_path(temporary), filesystem_path(destination))
            finally:
                try:
                    os.unlink(filesystem_path(temporary))
                except FileNotFoundError:
                    pass
        cleanup_summary = prune_machine_only_files(roots["kb"], expected_files["kb"], candidate_paths=candidates)
        payload = {
            "restored": True,
            "recovery_status": "restored_with_cleanup_boundary",
            "snapshot_id": snapshot_id,
            "restored_files": len(plan),
            "pruned_files": int(cleanup_summary["machine_owned_pruned_count"]),
            "checksum_failures": 0,
            "snapshot_dir": str(snapshot_dir),
            "snapshot_boundary": {"snapshot_status": "restore-ready-snapshot", "file_count": len(plan)},
            "resume_boundary": build_resume_boundary(expected_files["kb"]),
            "cleanup_summary": cleanup_summary,
        }
        save_json(recovery_report_path(), payload)
    except Exception as exc:
        payload = {"restored": False, "snapshot_id": snapshot_id, "reason": "restore_failed",
                   "error": str(exc), "restored_files": 0, "pruned_files": 0,
                   "rolled_back": False}
        if transaction is not None:
            try:
                transaction.rollback()
                payload["rolled_back"] = True
            except Exception as rollback_error:
                payload.update(rolled_back=False, rollback_error=str(rollback_error),
                               rollback_backup_dir=str(transaction.backup_dir))
        return payload
    transaction.commit()
    return payload


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    args = parse_args()
    payload = restore_snapshot(args.snapshot_id)
    if args.format == "json":
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload["restored"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
