"""Preflight and disk-backed rollback for explicitly enumerated snapshot files."""
from __future__ import annotations

import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import shutil
import stat
import tempfile

from common import display_path, filesystem_path, sha256_for_file


def path_stat(path: Path, *, follow_symlinks=True):
    """Use extended Windows paths; only a missing entry counts as absent."""
    try:
        return os.stat(filesystem_path(path), follow_symlinks=follow_symlinks)
    except FileNotFoundError:
        return None


def resolve_path(path: Path) -> Path:
    # Keep logical paths unprefixed for containment checks and manifest keys.
    return Path(display_path(Path(filesystem_path(path)).resolve()))


def regular_files_under(root: Path) -> list[Path]:
    if path_stat(root) is None:
        return []
    files = []
    for entry in Path(filesystem_path(root)).rglob("*"):
        state = path_stat(entry)
        if state is not None and stat.S_ISREG(state.st_mode):
            files.append(Path(display_path(entry)))
    return files


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
            source = resolve_path(snapshot_dir / "files" / label / path)
            source.relative_to(resolve_path(snapshot_dir))
            destination = resolve_path(roots[label] / path)
            destination.relative_to(resolve_path(roots[label]))
            # Restoring workspace files must never overwrite the recovery source.
            try:
                destination.relative_to(resolve_path(snapshot_dir.parent))
            except ValueError:
                pass
            else:
                raise ValueError("snapshot_destination_overlaps_backup")
            if destination in destinations:
                raise ValueError("snapshot_destination_duplicate")
            destinations.add(destination)
            source_state = path_stat(source)
            if source_state is None or not stat.S_ISREG(source_state.st_mode):
                raise ValueError("snapshot_file_missing")
            if sha256_for_file(source) != expected:
                raise ValueError("snapshot_checksum_mismatch")
            destination_state = path_stat(destination)
            if destination_state is not None and not stat.S_ISREG(destination_state.st_mode):
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
        self.checksums = {}
        self.missing_dirs = set()
        for path in self.paths:
            parent = path.parent
            while path_stat(parent) is None:
                self.missing_dirs.add(parent)
                parent = parent.parent
        self.backup_dir = Path(display_path(tempfile.mkdtemp(prefix=".restore-rollback-", dir=filesystem_path(backup_root))))
        try:
            for index, path in enumerate(self.paths):
                state = path_stat(path, follow_symlinks=False)
                if state is not None and stat.S_ISLNK(state.st_mode):
                    raise OSError(f"restore transaction target is a symlink: {path}")
                if state is not None:
                    if not stat.S_ISREG(state.st_mode):
                        raise OSError(f"restore target is not a file: {path}")
                    backup = self.backup_dir / str(index)
                    shutil.copy2(filesystem_path(path), filesystem_path(backup))
                    checksum = sha256_for_file(backup)
                    if sha256_for_file(path) != checksum:
                        raise OSError(f"restore backup checksum mismatch: {path}")
                    self.before[path] = backup
                    self.checksums[path] = checksum
                else:
                    self.before[path] = None
            Path(filesystem_path(self.backup_dir / "manifest.json")).write_text(
                json.dumps({str(path): str(backup) if backup else None
                            for path, backup in self.before.items()}, ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception:
            shutil.rmtree(filesystem_path(self.backup_dir), ignore_errors=True)
            raise

    def rollback(self):
        failures = []
        for path, backup in self.before.items():
            temporary = None
            try:
                if backup is None:
                    if path_stat(path, follow_symlinks=False) is not None:
                        os.unlink(filesystem_path(path))
                    if path_stat(path, follow_symlinks=False) is not None:
                        raise OSError(f"rollback did not remove new file: {path}")
                    continue
                os.makedirs(filesystem_path(path.parent), exist_ok=True)
                descriptor, name = tempfile.mkstemp(prefix=".snapshot-rollback-", dir=filesystem_path(path.parent))
                os.close(descriptor)
                temporary = Path(name)
                # Deliberately independent of the restore's copy2 operation.
                shutil.copyfile(filesystem_path(backup), filesystem_path(temporary))
                shutil.copystat(filesystem_path(backup), filesystem_path(temporary))
                os.replace(filesystem_path(temporary), filesystem_path(path))
                if sha256_for_file(path) != self.checksums[path]:
                    raise OSError(f"rollback checksum mismatch: {path}")
            except OSError as exc:
                failures.append(f"{path}: {exc}")
            finally:
                if temporary is not None:
                    try:
                        os.unlink(filesystem_path(temporary))
                    except FileNotFoundError:
                        pass
                    except OSError as exc:
                        failures.append(f"{temporary}: {exc}")
        for directory in sorted(self.missing_dirs, key=lambda path: len(path.parts), reverse=True):
            try:
                os.rmdir(filesystem_path(directory))
            except FileNotFoundError:
                pass
            except OSError as exc:
                failures.append(f"{directory}: {exc}")
        if failures:
            # Keep the disk backups and manifest when rollback itself fails.
            raise OSError("; ".join(failures))
        self.commit()

    def commit(self):
        shutil.rmtree(filesystem_path(self.backup_dir), ignore_errors=True)
