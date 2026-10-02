"""Preflight and disk-backed rollback for explicitly enumerated snapshot files."""
from __future__ import annotations

import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import shutil
import tempfile

from common import filesystem_path, sha256_for_file


def preflight(snapshot_dir: Path, manifest: dict, roots: dict[str, Path]):
    """Reject the entire restore before any target write if a file is invalid."""
    errors, plan, destinations = [], [], set()
    files = manifest.get("files") if isinstance(manifest, dict) else None
    if not isinstance(files, list) or not files:
        return [], [{"reason": "snapshot_file_list_missing_or_empty"}]
    if "file_count" in manifest and manifest["file_count"] != len(files):
        errors.append({"reason": "snapshot_file_count_mismatch"})
    for item in files:
        try:
            if not isinstance(item, dict):
                raise ValueError("snapshot_entry_invalid")
            label = item.get("root")
            relative = item.get("relative_path")
            expected = item.get("sha256")
            if label not in roots:
                raise ValueError("snapshot_root_invalid")
            if not isinstance(relative, str) or not relative:
                raise ValueError("snapshot_path_invalid")
            normalized = relative.replace("\\", "/")
            path = PurePosixPath(normalized)
            if (path.is_absolute() or PureWindowsPath(normalized).drive
                    or ".." in path.parts or path == PurePosixPath(".")):
                raise ValueError("snapshot_path_outside_root")
            if not isinstance(expected, str) or not re.fullmatch(r"[0-9a-f]{64}", expected):
                raise ValueError("snapshot_checksum_invalid")
            source = (snapshot_dir / "files" / label / path).resolve()
            source.relative_to(snapshot_dir.resolve())
            destination = (roots[label] / path).resolve()
            destination.relative_to(roots[label].resolve())
            # Restoring workspace files must never overwrite the recovery source.
            try:
                destination.relative_to(snapshot_dir.parent.resolve())
            except ValueError:
                pass
            else:
                raise ValueError("snapshot_destination_overlaps_backup")
            if destination in destinations:
                raise ValueError("snapshot_destination_duplicate")
            destinations.add(destination)
            if not Path(filesystem_path(source)).is_file():
                raise ValueError("snapshot_file_missing")
            if sha256_for_file(source) != expected:
                raise ValueError("snapshot_checksum_mismatch")
            if destination.exists() and not destination.is_file():
                raise ValueError("restore_destination_not_file")
            plan.append((source, destination, expected))
        except (OSError, ValueError, TypeError) as exc:
            errors.append({"reason": str(exc), "entry": item})
    return plan, errors


class RestoreTransaction:
    """Back up only files the restore may overwrite or prune, on disk."""

    def __init__(self, paths, *, backup_root: Path):
        self.paths = list(dict.fromkeys(Path(path) for path in paths))
        self.before = {}
        self.missing_dirs = set()
        for path in self.paths:
            parent = path.parent
            while not parent.exists():
                self.missing_dirs.add(parent)
                parent = parent.parent
        self.backup_dir = Path(tempfile.mkdtemp(prefix=".restore-rollback-", dir=backup_root))
        try:
            for index, path in enumerate(self.paths):
                if path.is_symlink():
                    raise OSError(f"restore transaction target is a symlink: {path}")
                if path.exists():
                    if not path.is_file():
                        raise OSError(f"restore target is not a file: {path}")
                    backup = self.backup_dir / str(index)
                    shutil.copy2(filesystem_path(path), filesystem_path(backup))
                    self.before[path] = backup
                else:
                    self.before[path] = None
            (self.backup_dir / "manifest.json").write_text(
                json.dumps({str(path): str(backup) if backup else None
                            for path, backup in self.before.items()}, ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception:
            shutil.rmtree(self.backup_dir, ignore_errors=True)
            raise

    def rollback(self):
        failures = []
        for path, backup in self.before.items():
            temporary = None
            try:
                if backup is None:
                    if path.exists():
                        path.unlink()
                    continue
                path.parent.mkdir(parents=True, exist_ok=True)
                descriptor, name = tempfile.mkstemp(prefix=".snapshot-rollback-", dir=path.parent)
                os.close(descriptor)
                temporary = Path(name)
                # Deliberately independent of the restore's copy2 operation.
                shutil.copyfile(filesystem_path(backup), filesystem_path(temporary))
                shutil.copystat(filesystem_path(backup), filesystem_path(temporary))
                os.replace(filesystem_path(temporary), filesystem_path(path))
            except OSError as exc:
                failures.append(f"{path}: {exc}")
            finally:
                if temporary is not None and temporary.exists():
                    temporary.unlink()
        for directory in sorted(self.missing_dirs, key=lambda path: len(path.parts), reverse=True):
            try:
                directory.rmdir()
            except FileNotFoundError:
                pass
            except OSError as exc:
                failures.append(f"{directory}: {exc}")
        if failures:
            # Keep the disk backups and manifest when rollback itself fails.
            raise OSError("; ".join(failures))
        self.commit()

    def commit(self):
        shutil.rmtree(self.backup_dir, ignore_errors=True)
