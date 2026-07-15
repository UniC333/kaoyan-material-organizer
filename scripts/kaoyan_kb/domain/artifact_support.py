from __future__ import annotations

from pathlib import Path
from typing import Any

from kaoyan_kb.storage.atomic_io import load_json_or_default


def load_required_artifact(index_root: Path, filename: str) -> dict[str, Any]:
    payload = load_json_or_default(index_root / filename, {})
    if not payload:
        raise SystemExit(f"missing required artifact: {filename}")
    return payload


def status_from_readiness(readiness_status: str, ready_value: str) -> str:
    return "accepted" if readiness_status == ready_value else "not-yet-accepted"


def dedupe_strings(items: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for item in items:
        text = str(item).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result
