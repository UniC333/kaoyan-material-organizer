"""Conservative discovery and pruning of this tool's abandoned write files."""
from __future__ import annotations

import os
import re
import stat
from pathlib import Path
from typing import Any

from common import kb_layout
from kaoyan_kb.storage.atomic_io import _filesystem_path
from kaoyan_kb.storage.file_lock import FileLockTimeout, file_lock


# Keep these tied to atomic_io._atomic_write_text and FileTransaction.restore.
ATOMIC_TMP = re.compile(r"\.(?P<target>.+)\.(?P<pid>[1-9][0-9]*)\.[0-9a-f]{32}\.tmp")
RESTORE_TMP = re.compile(r"\.(?P<target>.+)\.restore\.tmp")


def _pid_running(pid: int) -> bool:
    """False only when the OS confirms absence; PID reuse conservatively skips."""
    if os.name == "nt":
        # os.kill(pid, 0) is unsafe on Windows: use a read-only process handle.
        import ctypes
        from ctypes import wintypes

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel.WaitForSingleObject.restype = wintypes.DWORD
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.CloseHandle.restype = wintypes.BOOL
        if pid > 0xFFFFFFFF:
            raise ValueError("PID outside Windows DWORD range")
        handle = kernel.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE only
        if not handle:
            error = ctypes.get_last_error()
            if error == 87:  # ERROR_INVALID_PARAMETER: no such PID
                return False
            raise ctypes.WinError(error)
        try:
            result = kernel.WaitForSingleObject(handle, 0)
            if result not in (0, 258):  # signalled (exited) / WAIT_TIMEOUT
                raise ctypes.WinError(ctypes.get_last_error())
            return result == 258
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def _safe_path(path: Path, root: Path) -> bool:
    """Reject links (including parent links/junctions), even within the KB."""
    try:
        resolved = path.resolve()
        return resolved.is_relative_to(root) and resolved == path and not path.is_symlink()
    except (OSError, RuntimeError):
        return False


def prune_tmp(kb_root: Path, *, ocr_cache_root: Path | None = None) -> dict[str, Any]:
    root = Path(kb_root).resolve()
    items: list[dict[str, str]] = []

    def report(path: Path, status: str, reason: str) -> None:
        items.append({"path": str(path), "status": status, "reason": reason})

    def inspect(path: Path) -> None:
        atomic = ATOMIC_TMP.fullmatch(path.name)
        restore = RESTORE_TMP.fullmatch(path.name)
        if not atomic and not restore:
            report(path, "skipped", "unrecognized-temp-name")
            return
        match = atomic or restore
        if match["target"] in {".", ".."}:
            report(path, "skipped", "invalid-target-name")
            return
        target = path.with_name(match["target"])
        sidecar = target.with_name(target.name + ".lock")
        deleted = False
        try:
            if not all(_safe_path(p, root) for p in (path, target, sidecar)):
                report(path, "skipped", "symlink-or-outside-kb")
                return
            # Hold the same stable sidecar throughout checks AND deletion.
            with file_lock(target, timeout=0):
                if not all(_safe_path(p, root) for p in (path, target, sidecar)):
                    report(path, "skipped", "symlink-or-outside-kb")
                    return
                if not stat.S_ISREG(path.lstat().st_mode):
                    report(path, "skipped", "not-a-regular-file")
                    return
                if restore:
                    # restore() neither records a PID nor takes this lock. A
                    # free target lock alone cannot prove its tmp is abandoned.
                    report(path, "skipped", "restore-writer-uncoordinated; orphan-status-unknown")
                    return
                if _pid_running(int(atomic["pid"])):
                    report(path, "skipped", "creator-pid-running; may-be-in-use-or-pid-reused")
                    return
                os.unlink(_filesystem_path(path))
                deleted = True
                report(path, "deleted", "target-lock-acquired; creator-pid-exited")
        except (OSError, ValueError, OverflowError) as exc:
            if deleted:
                # Lock cleanup cannot undo an already successful unlink.
                items[-1]["reason"] += f"; lock-release-failed: {exc}"
            elif isinstance(exc, FileLockTimeout):
                report(path, "skipped", f"target-lock-held: {sidecar}")
            else:
                report(path, "skipped", f"cannot-prove-or-remove-orphan: {exc}")

    def scan_error(exc: OSError) -> None:
        report(Path(exc.filename) if exc.filename else root, "skipped", f"scan-error: {exc}")

    # The root itself is NOT recursively scanned: only kb_layout directories
    # and an explicitly configured in-KB OCR cache are managed scan roots.
    roots = {p for key, p in kb_layout(root=root).items() if key != "root"}
    if ocr_cache_root is not None:
        cache = Path(ocr_cache_root).absolute()
        if cache.is_relative_to(root) and cache != root:
            roots.add(cache)
    roots = {p for p in roots if not any(parent in roots for parent in p.parents)}
    try:
        for path in sorted(root.iterdir()):
            if path.name.endswith(".tmp") and not path.is_dir():
                inspect(path)
    except OSError as exc:
        scan_error(exc)
    for scan_root in sorted(roots):
        if not _safe_path(scan_root, root):
            report(scan_root, "skipped", "symlink-or-outside-kb; directory-not-followed")
            continue
        if not scan_root.exists():
            continue
        for current, dirs, files in os.walk(scan_root, followlinks=False, onerror=scan_error):
            directory = Path(current)
            for name in sorted(dirs.copy()):
                child = directory / name
                if not _safe_path(child, root):
                    dirs.remove(name)
                    report(child, "skipped", "symlink-or-outside-kb; directory-not-followed")
            dirs.sort()
            for name in sorted(files):
                if name.endswith(".tmp"):
                    inspect(directory / name)
    return {
        "kb_root": str(root),
        "deleted_count": sum(item["status"] == "deleted" for item in items),
        "skipped_count": sum(item["status"] == "skipped" for item in items),
        "items": items,
    }


def render_prune_text(payload: dict[str, Any]) -> str:
    return "\n".join([
        f"kb_root: {payload['kb_root']}",
        f"deleted_count: {payload['deleted_count']}",
        f"skipped_count: {payload['skipped_count']}",
        *[f"{item['status']}: {item['path']} ({item['reason']})" for item in payload["items"]],
    ])
