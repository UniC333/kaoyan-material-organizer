"""Read-only JSON snapshots scoped to one query (including its batch items)."""
from __future__ import annotations

import json
from contextvars import ContextVar
from contextlib import contextmanager
from copy import deepcopy
from functools import wraps
from pathlib import Path
from typing import Any, Callable, ParamSpec, TypeVar

_CACHE: ContextVar[dict[Path, Any] | None] = ContextVar('query_json_cache', default=None)
_DERIVED: ContextVar[dict[tuple[Path, Callable], Any] | None] = ContextVar('query_derived_cache', default=None)
P = ParamSpec('P')
R = TypeVar('R')


@contextmanager
def read_scope():
    if _CACHE.get() is not None:
        yield
        return
    token = _CACHE.set({})
    derived_token = _DERIVED.set({})
    try:
        yield
    finally:
        _DERIVED.reset(derived_token)
        _CACHE.reset(token)


def with_read_scope(function: Callable[P, R]) -> Callable[P, R]:
    @wraps(function)
    def wrapped(*args: P.args, **kwargs: P.kwargs) -> R:
        with read_scope():
            return function(*args, **kwargs)
    return wrapped


def read_derived(path: Path, build: Callable[[Any], R]) -> R:
    """Reuse a read-only projection within a request; builders must return immutable data."""
    cache = _DERIVED.get()
    if cache is None:
        return build(read_json(path))
    key = (path.resolve(), build)
    if key not in cache:
        cache[key] = build(read_json(path))
    return cache[key]


def read_json(path: Path) -> Any:
    cache = _CACHE.get()
    key = path.resolve()
    if cache is None:
        return json.loads(path.read_text(encoding='utf-8'))
    if key not in cache:
        cache[key] = json.loads(path.read_text(encoding='utf-8'))
    # Callers may augment evidence records. Do not let those mutations reach later gates.
    return deepcopy(cache[key])


def read_all_json(directory: Path) -> list[dict[str, Any]]:
    records = []
    for path in sorted(directory.glob('*.json')):
        try:
            records.append(read_json(path))
        except Exception:
            continue
    return records
