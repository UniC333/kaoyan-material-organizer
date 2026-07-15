from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def _filesystem_path(path: str | Path) -> str:
    raw = os.fspath(path)
    if os.name != "nt" or not raw or raw.startswith("\\\\?\\"):
        return raw
    candidate = raw if os.path.isabs(raw) else os.path.abspath(raw)
    if candidate.startswith("\\\\"):
        return "\\\\?\\UNC\\" + candidate.lstrip("\\")
    return "\\\\?\\" + candidate


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def load_json_or_default(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return default


def _strip_ignored_keys(payload: Any, ignored_keys: set[str]) -> Any:
    if isinstance(payload, dict):
        return {
            key: _strip_ignored_keys(value, ignored_keys)
            for key, value in payload.items()
            if key not in ignored_keys
        }
    if isinstance(payload, list):
        return [_strip_ignored_keys(item, ignored_keys) for item in payload]
    return payload


def _atomic_write_text(path: Path, content: str) -> None:
    os.makedirs(_filesystem_path(path.parent), exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    try:
        with open(_filesystem_path(temp_path), "w", encoding="utf-8", newline="") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(_filesystem_path(temp_path), _filesystem_path(path))
    finally:
        if Path(_filesystem_path(temp_path)).exists():
            try:
                os.unlink(_filesystem_path(temp_path))
            except OSError:
                pass


def save_text(path: Path, content: str) -> bool:
    existing = path.read_text(encoding="utf-8") if path.exists() else None
    if existing == content:
        return False
    _atomic_write_text(path, content)
    return True


def save_json(
    path: Path,
    payload: dict[str, Any] | list[Any],
    *,
    ignored_compare_keys: tuple[str, ...] = ("updated_at",),
) -> bool:
    if path.exists():
        try:
            existing_payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            existing_payload = None
        ignored_keys = set(ignored_compare_keys)
        if existing_payload is not None and _strip_ignored_keys(existing_payload, ignored_keys) == _strip_ignored_keys(
            payload, ignored_keys
        ):
            return False
    _atomic_write_text(path, json.dumps(payload, ensure_ascii=False, indent=2))
    return True
