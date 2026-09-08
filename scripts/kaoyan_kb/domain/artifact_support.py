from __future__ import annotations

import json
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


def load_overrides(path: str | None) -> dict[str, Any]:
    if not path:
        return {"manual_locks": [], "operator_overrides": []}
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return {
        "manual_locks": list(payload.get("manual_locks", [])),
        "operator_overrides": list(payload.get("operator_overrides", [])),
    }


def render_acceptance_markdown(
    payload: dict[str, Any], *, title: str, summary_title: str,
    summary_fields: tuple[tuple[str, str, str], ...],
    successor_key: str, successor_title: str,
) -> str:
    summaries = [(label, dict(payload.get(section, {})), key) for label, section, key in summary_fields]
    successor = dict(payload.get(successor_key, {}))
    lines = [
        title, "",
        f"- artifact_id: {payload.get('artifact_id', '')}",
        f"- plan_date: {payload.get('plan_date', '')}",
        f"- readiness_status: {payload.get('readiness_status', '')}",
        "", summary_title, "",
    ]
    lines.extend(f"- {label}: {summary.get(key, '')}" for label, summary, key in summaries)
    lines.extend([
        "", successor_title, "",
        f"- track_id: {successor.get('track_id', '')}",
        f"- machine_readable_entry_point: {successor.get('machine_readable_entry_point', '')}",
        "",
    ])
    return "\n".join(lines)
