from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from kaoyan_kb.domain.review_scheduler import FLUENCY_LEVELS, is_supported_review_subject


BATCH_CONTRACT_VERSION = "adaptive-review-batch.v1"
RESULTS = {"right", "partial", "wrong"}


def _nonempty(value: Any) -> str:
    return str(value or "").strip()


def _reviewed_at(value: Any, errors: list[str]) -> str:
    text = _nonempty(value)
    if not text:
        errors.append("reviewed_at is required")
        return ""
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        errors.append("reviewed_at must be an ISO-8601 timestamp")
        return text
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        errors.append("reviewed_at must include a timezone offset")
    return text


def load_chapter_review_batch(path: str | Path) -> dict[str, Any]:
    batch_path = Path(path).expanduser()
    try:
        payload = json.loads(batch_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"batch file does not exist: {batch_path}") from exc
    except (OSError, UnicodeError) as exc:
        raise ValueError(f"batch file cannot be read as UTF-8: {batch_path}") from exc
    except ValueError as exc:
        raise ValueError(f"batch file is not valid JSON: {batch_path}") from exc
    if not isinstance(payload, dict):
        raise ValueError("chapter review batch must be a JSON object")
    return payload


def validate_chapter_review_batch(payload: dict[str, Any]) -> dict[str, Any]:
    errors: list[str] = []
    contract_version = _nonempty(payload.get("contract_version"))
    if contract_version != BATCH_CONTRACT_VERSION:
        errors.append(f"contract_version must be {BATCH_CONTRACT_VERSION}")

    subject = _nonempty(payload.get("subject"))
    if not subject:
        errors.append("subject is required")
    elif not is_supported_review_subject(subject):
        errors.append("adaptive review currently supports mathematics and 408 subjects only")

    chapter_title = _nonempty(payload.get("chapter_title") or payload.get("chapter"))
    if not chapter_title:
        errors.append("chapter_title is required")
    review_id = _nonempty(payload.get("review_id"))
    if not review_id:
        errors.append("review_id is required")
    reviewed_at = _reviewed_at(payload.get("reviewed_at"), errors)

    source = payload.get("source")
    if not isinstance(source, dict):
        source = {}
        errors.append("source must be an object")
    source_task_id = _nonempty(source.get("task_id"))
    if not source_task_id:
        errors.append("source.task_id is required")
    raw_message_ids = source.get("message_ids")
    if not isinstance(raw_message_ids, list):
        raw_message_ids = []
        errors.append("source.message_ids must be an array")
    source_message_ids = []
    for value in raw_message_ids:
        item = _nonempty(value)
        if item and item not in source_message_ids:
            source_message_ids.append(item)
    if not source_message_ids:
        errors.append("source.message_ids must contain at least one id")

    raw_items = payload.get("items")
    if not isinstance(raw_items, list) or not raw_items:
        raw_items = []
        errors.append("items must be a non-empty array")

    normalized_items: list[dict[str, Any]] = []
    seen_markers: set[tuple[str, str]] = set()
    for index, raw in enumerate(raw_items, start=1):
        prefix = f"items[{index}]"
        if not isinstance(raw, dict):
            errors.append(f"{prefix} must be an object")
            continue
        node_id = _nonempty(raw.get("node_id"))
        question_id = _nonempty(raw.get("question_id"))
        result = _nonempty(raw.get("result"))
        fluency = _nonempty(raw.get("fluency"))
        if not node_id:
            errors.append(f"{prefix}.node_id is required")
        if not question_id:
            errors.append(f"{prefix}.question_id is required")
        if result not in RESULTS:
            errors.append(f"{prefix}.result must be one of {sorted(RESULTS)}")
        if fluency not in FLUENCY_LEVELS:
            errors.append(f"{prefix}.fluency must be one of {list(FLUENCY_LEVELS)}")
        marker = (question_id, node_id)
        if all(marker):
            if marker in seen_markers:
                errors.append(f"{prefix} duplicates question_id/node_id within the batch")
            seen_markers.add(marker)

        hint_used = raw.get("hint_used")
        if not isinstance(hint_used, bool):
            errors.append(f"{prefix}.hint_used must be true or false")
            hint_used = False
        duration = raw.get("duration_minutes")
        if duration is not None and (isinstance(duration, bool) or not isinstance(duration, int) or duration <= 0):
            errors.append(f"{prefix}.duration_minutes must be a positive integer or null")
            duration = None
        raw_tags = raw.get("tags", [])
        if not isinstance(raw_tags, list) or any(not isinstance(tag, str) for tag in raw_tags):
            errors.append(f"{prefix}.tags must be an array of strings")
            raw_tags = []
        tags: list[str] = []
        for tag in raw_tags:
            value = tag.strip()
            if value and value not in tags:
                tags.append(value)
        note = raw.get("note", "")
        if not isinstance(note, str):
            errors.append(f"{prefix}.note must be a string")
            note = ""
        normalized_items.append(
            {
                "node_id": node_id,
                "question_id": question_id,
                "result": result,
                "fluency": fluency,
                "hint_used": hint_used,
                "duration_minutes": duration,
                "tags": tags,
                "note": note.strip(),
            }
        )

    if errors:
        raise ValueError("invalid chapter review batch: " + "; ".join(errors))
    return {
        "contract_version": BATCH_CONTRACT_VERSION,
        "subject": subject,
        "chapter_title": chapter_title,
        "review_id": review_id,
        "reviewed_at": reviewed_at,
        "source": {
            "task_id": source_task_id,
            "message_ids": source_message_ids,
        },
        "items": normalized_items,
    }
