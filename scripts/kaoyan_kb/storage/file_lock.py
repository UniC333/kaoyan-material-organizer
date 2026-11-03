"""Cooperating writers lock a stable sidecar, never the replaceable data file."""
from __future__ import annotations

import errno
import math
import os
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from kaoyan_kb.storage.atomic_io import _filesystem_path

if os.name == "nt":
    import msvcrt
else:
    import fcntl


class FileLockTimeout(TimeoutError):
    """A cooperating writer still holds the requested lock at the deadline."""


def _try_lock(fd: int) -> None:
    if os.name == "nt":
        # Windows permits byte-range locks beyond EOF, so the sidecar stays empty.
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
    else:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock(fd: int) -> None:
    if os.name == "nt":
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
    else:
        fcntl.flock(fd, fcntl.LOCK_UN)


@contextmanager
def file_lock(
    path: str | Path, *, timeout: float = 30.0, poll_interval: float = 0.05
) -> Iterator[None]:
    """Hold an exclusive, non-reentrant lock across the entire read/modify/write.

    POSIX flock / Windows byte-range locks are released by the kernel on close
    or process death, including forced termination. No age/PID heuristic is
    needed to recover stale ownership. The empty ``<target>.lock`` sidecar must
    NEVER be removed/replaced: waiters may already have its inode open. There is
    one zero-byte sidecar per target, reused indefinitely; it contains no facts.
    All writers must use this protocol. This is not a multi-file transaction.
    Use spawn/exec for children; do not fork while holding a lock, since fork
    can inherit a descriptor and prolong ownership on POSIX.
    """
    if not math.isfinite(timeout) or timeout < 0:
        raise ValueError("lock timeout must be finite and non-negative")
    if not math.isfinite(poll_interval) or poll_interval <= 0:
        raise ValueError("lock poll_interval must be finite and positive")
    target = Path(path).resolve()
    lock_path = target.with_name(target.name + ".lock")
    os.makedirs(_filesystem_path(lock_path.parent), exist_ok=True)
    fd = os.open(_filesystem_path(lock_path), os.O_CREAT | os.O_RDWR, 0o666)
    acquired = False
    deadline = time.monotonic() + timeout
    try:
        while True:
            try:
                _try_lock(fd)
                acquired = True
                break
            except OSError as exc:
                if exc.errno not in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
                    raise
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise FileLockTimeout(
                        f"Timed out after {timeout:g}s waiting for cross-process lock: "
                        f"{target} (lock file: {lock_path})"
                    ) from exc
                time.sleep(min(poll_interval, remaining))
        yield
    finally:
        try:
            if acquired:
                _unlock(fd)
        finally:
            os.close(fd)
