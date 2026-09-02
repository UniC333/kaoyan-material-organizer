from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


MANIFEST_VERSION = "conversation-scan-manifest.v1"
DECISION_VERSION = "conversation-closure-decision.v1"
LOCAL_INDEX_VERSION = "codex-local-index.v1"
SOURCE_KINDS = {"local_codex", "remote_codex", "chatgpt"}
FACT_TYPES = {"progress", "stop_position", "learning_gap", "accepted_understanding", "duration", "course"}
CONFIRMATION_TYPES = {"explicit", "accepted_explanation"}
STABLE_STATUSES = {"idle", "completed", "failed", "notloaded"}
MAX_MANIFEST_BYTES = 5_000_000
MAX_TASKS = 1_000
MAX_TURNS = 4_000
MAX_TEXT_CHARS = 300_000

STATE_TABLE_COLUMNS = {
    "threads": {
        "id",
        "rollout_path",
        "updated_at",
        "updated_at_ms",
        "archived",
        "agent_path",
        "thread_source",
    }
}
HISTORY_TABLE_COLUMNS = {
    "thread_turns": {"thread_id", "turn_id", "status", "started_at"},
    "thread_items": {"thread_id", "turn_id", "item_id", "created_at_ms", "item_json"},
}


class ClosureValidationError(ValueError):
    pass


def _object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ClosureValidationError(f"{label} must be an object")
    return value


def _items(value: Any, label: str, *, limit: int = MAX_TASKS) -> list[Any]:
    if not isinstance(value, list):
        raise ClosureValidationError(f"{label} must be a list")
    if len(value) > limit:
        raise ClosureValidationError(f"{label} exceeds item limit {limit}")
    return value


def _text(value: Any, label: str, *, limit: int = 300, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise ClosureValidationError(f"{label} must be a string")
    result = value.strip()
    if not result and not allow_empty:
        raise ClosureValidationError(f"{label} is required")
    if len(result) > limit:
        raise ClosureValidationError(f"{label} exceeds length limit {limit}")
    return result


def _boolean(value: Any, label: str) -> bool:
    if not isinstance(value, bool):
        raise ClosureValidationError(f"{label} must be a boolean")
    return value


def parse_timestamp(value: Any, label: str) -> datetime:
    if isinstance(value, bool):
        raise ClosureValidationError(f"{label} must be an ISO timestamp or Unix epoch")
    if isinstance(value, (int, float)):
        seconds = float(value) / 1000 if abs(float(value)) >= 100_000_000_000 else float(value)
        try:
            return datetime.fromtimestamp(seconds, tz=timezone.utc)
        except (OverflowError, OSError, ValueError) as exc:
            raise ClosureValidationError(f"{label} is outside the supported timestamp range") from exc
    if not isinstance(value, str) or not value.strip():
        raise ClosureValidationError(f"{label} must be an ISO timestamp or Unix epoch")
    raw = value.strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        result = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise ClosureValidationError(f"{label} must be an ISO timestamp with timezone") from exc
    if result.tzinfo is None or result.utcoffset() is None:
        raise ClosureValidationError(f"{label} must include a timezone")
    return result.astimezone(timezone.utc)


def canonical_timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def load_manifest(path: Path) -> dict[str, Any]:
    try:
        if path.stat().st_size > MAX_MANIFEST_BYTES:
            raise ClosureValidationError("manifest exceeds the 5 MB safety limit")
        payload = json.loads(path.read_text(encoding="utf-8", errors="strict"))
    except FileNotFoundError as exc:
        raise ClosureValidationError(f"manifest does not exist: {path}") from exc
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ClosureValidationError("manifest JSON cannot be read") from exc
    return _object(payload, "manifest")


def read_vault_watermarks(vault_root: Path) -> dict[str, str]:
    current_task = vault_root.resolve() / "01_任务" / "当前任务.md"
    try:
        text = current_task.read_text(encoding="utf-8", errors="strict")
    except (OSError, UnicodeDecodeError) as exc:
        raise ClosureValidationError(f"current task cannot be read: {current_task}") from exc
    labels = {
        "local": "本地 Codex 已核验至",
        "global": "全局会话已核验至",
        "local_audit": "本地覆盖审计截至",
    }
    found: dict[str, str] = {}
    for line in text.splitlines():
        stripped = line.strip()
        for key, label in labels.items():
            prefix = f"- {label}："
            if stripped.startswith(prefix):
                if key in found:
                    raise ClosureValidationError(f"duplicate watermark in current task: {label}")
                found[key] = stripped[len(prefix) :].strip()
    missing = [label for key, label in labels.items() if key not in found]
    if missing:
        raise ClosureValidationError(f"current task is missing watermarks: {', '.join(missing)}")
    for key, value in found.items():
        parse_timestamp(value, f"current task {labels[key]}")
    return found


def _normalize_discovery_item(value: Any, label: str) -> dict[str, Any]:
    item = _object(value, label)
    source_kind = _text(item.get("source_kind"), f"{label}.source_kind", limit=40)
    if source_kind not in SOURCE_KINDS:
        raise ClosureValidationError(f"{label}.source_kind is unsupported")
    return {
        "task_id": _text(item.get("task_id"), f"{label}.task_id", limit=120),
        "source_kind": source_kind,
        "host_id": _text(item.get("host_id"), f"{label}.host_id", limit=120),
        "updated_at": parse_timestamp(item.get("updated_at"), f"{label}.updated_at"),
    }


def _merge_discovery(
    target: dict[str, dict[str, Any]],
    value: Any,
    label: str,
    origin: str,
) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for index, raw in enumerate(_items(value, label)):
        item = _normalize_discovery_item(raw, f"{label}[{index}]")
        task_id = item["task_id"]
        existing = target.get(task_id)
        if existing is not None:
            identity = (existing["source_kind"], existing["host_id"], existing["updated_at"])
            incoming = (item["source_kind"], item["host_id"], item["updated_at"])
            if identity != incoming:
                raise ClosureValidationError(f"conflicting discovery metadata for task {task_id}")
            existing["origins"].add(origin)
        else:
            target[task_id] = {**item, "origins": {origin}}
        normalized.append(item)
    return normalized


def _listing_reaches_watermark(active: dict[str, Any], watermark: datetime) -> bool:
    returned_count = active["returned_count"]
    if returned_count < active["limit"]:
        return True
    items = active["items"]
    return bool(items) and min(item["updated_at"] for item in items) <= watermark


def _scope_for_source(source_kind: str) -> str:
    return "local" if source_kind == "local_codex" else "global"


def _gap(target: list[str], code: str) -> None:
    if code not in target:
        target.append(code)


def _normalize_task_body(value: Any, index: int) -> dict[str, Any]:
    label = f"tasks[{index}]"
    item = _object(value, label)
    source_kind = _text(item.get("source_kind"), f"{label}.source_kind", limit=40)
    if source_kind not in SOURCE_KINDS:
        raise ClosureValidationError(f"{label}.source_kind is unsupported")
    body_source = _text(item.get("body_source"), f"{label}.body_source", limit=40)
    if body_source not in {"official", "local_history", "local_rollout"}:
        raise ClosureValidationError(f"{label}.body_source is unsupported")
    if source_kind != "local_codex" and body_source != "official":
        raise ClosureValidationError(f"{label} cannot use a local body fallback")
    turns: list[dict[str, Any]] = []
    for turn_index, raw_turn in enumerate(_items(item.get("turns"), f"{label}.turns", limit=MAX_TURNS)):
        turn_label = f"{label}.turns[{turn_index}]"
        turn = _object(raw_turn, turn_label)
        messages: list[dict[str, str]] = []
        for message_index, raw_message in enumerate(
            _items(turn.get("user_messages"), f"{turn_label}.user_messages", limit=100)
        ):
            message_label = f"{turn_label}.user_messages[{message_index}]"
            message = _object(raw_message, message_label)
            messages.append(
                {
                    "message_id": _text(message.get("message_id"), f"{message_label}.message_id", limit=200),
                    "text": _text(message.get("text"), f"{message_label}.text", limit=12_000),
                }
            )
        turns.append(
            {
                "turn_id": _text(turn.get("turn_id"), f"{turn_label}.turn_id", limit=200),
                "started_at": parse_timestamp(turn.get("started_at"), f"{turn_label}.started_at"),
                "user_messages": messages,
            }
        )
    return {
        "task_id": _text(item.get("task_id"), f"{label}.task_id", limit=120),
        "source_kind": source_kind,
        "host_id": _text(item.get("host_id"), f"{label}.host_id", limit=120),
        "status": _text(item.get("status"), f"{label}.status", limit=40).lower(),
        "updated_at_before": parse_timestamp(item.get("updated_at_before"), f"{label}.updated_at_before"),
        "updated_at_after": parse_timestamp(item.get("updated_at_after"), f"{label}.updated_at_after"),
        "history_exhausted": _boolean(item.get("history_exhausted"), f"{label}.history_exhausted"),
        "next_cursor": _text(item.get("next_cursor", ""), f"{label}.next_cursor", limit=500, allow_empty=True),
        "read_error": _text(item.get("read_error", ""), f"{label}.read_error", limit=1_000, allow_empty=True),
        "body_source": body_source,
        "turns": turns,
    }


def _task_scope_gaps(
    task_id: str,
    discovery: dict[str, Any],
    body: dict[str, Any] | None,
    *,
    watermark: datetime,
    scan_upper: datetime,
) -> list[str]:
    prefix = f"task:{task_id}"
    if body is None:
        return [f"{prefix}:body_missing"]
    gaps: list[str] = []
    if (body["source_kind"], body["host_id"]) != (discovery["source_kind"], discovery["host_id"]):
        _gap(gaps, f"{prefix}:identity_changed")
    if body["updated_at_before"] != discovery["updated_at"]:
        _gap(gaps, f"{prefix}:discovery_timestamp_mismatch")
    if body["updated_at_before"] != body["updated_at_after"]:
        _gap(gaps, f"{prefix}:updated_during_read")
    if body["updated_at_after"] > scan_upper:
        _gap(gaps, f"{prefix}:updated_after_scan_upper")
    if body["status"] not in STABLE_STATUSES:
        _gap(gaps, f"{prefix}:unstable_status:{body['status']}")
    if body["read_error"]:
        _gap(gaps, f"{prefix}:body_read_failed")
    if body["history_exhausted"] and body["next_cursor"]:
        _gap(gaps, f"{prefix}:exhausted_with_cursor")
    timestamps = [turn["started_at"] for turn in body["turns"]]
    if any(timestamp > scan_upper for timestamp in timestamps):
        _gap(gaps, f"{prefix}:turn_after_scan_upper")
    crossed = body["history_exhausted"] or (bool(timestamps) and min(timestamps) <= watermark)
    if not crossed:
        _gap(gaps, f"{prefix}:watermark_not_crossed")
    return gaps


def _fact_key(task_id: str, turn_id: str, message_id: str, fact_type: str, subject: str) -> str:
    raw = "\x00".join((task_id, turn_id, message_id, fact_type, subject))
    return "closure-fact-v1:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()


def validate_manifest(
    payload: dict[str, Any],
    *,
    vault_root: Path | None = None,
    local_index_probe: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if payload.get("schema_version") != MANIFEST_VERSION:
        raise ClosureValidationError(f"schema_version must be {MANIFEST_VERSION}")
    mode = _text(payload.get("mode"), "mode", limit=40)
    if mode not in {"lightweight", "detailed_audit"}:
        raise ClosureValidationError("mode must be lightweight or detailed_audit")
    budget = _object(payload.get("budget"), "budget")
    page_count = budget.get("page_count")
    ambiguity_task_count = budget.get("ambiguity_task_count")
    if not isinstance(page_count, int) or isinstance(page_count, bool) or page_count < 0:
        raise ClosureValidationError("budget.page_count must be a non-negative integer")
    if not isinstance(ambiguity_task_count, int) or isinstance(ambiguity_task_count, bool) or ambiguity_task_count < 0:
        raise ClosureValidationError("budget.ambiguity_task_count must be a non-negative integer")
    limit_triggered = _boolean(budget.get("limit_triggered"), "budget.limit_triggered")
    if mode == "lightweight" and (page_count > 2 or ambiguity_task_count > 3):
        raise ClosureValidationError("lightweight budget exceeds two pages or three ambiguity tasks")
    write_authorized = _boolean(payload.get("write_authorized"), "write_authorized")
    old_local = parse_timestamp(payload.get("old_local_watermark"), "old_local_watermark")
    old_global = parse_timestamp(payload.get("old_global_watermark"), "old_global_watermark")
    scan_upper = parse_timestamp(payload.get("scan_upper"), "scan_upper")
    if old_local > scan_upper or old_global > scan_upper:
        raise ClosureValidationError("watermarks cannot be later than scan_upper")
    if vault_root is not None:
        state = read_vault_watermarks(vault_root)
        if parse_timestamp(state["local"], "vault local watermark") != old_local:
            raise ClosureValidationError("manifest old_local_watermark does not match current task")
        if parse_timestamp(state["global"], "vault global watermark") != old_global:
            raise ClosureValidationError("manifest old_global_watermark does not match current task")

    discovery_raw = _object(payload.get("discovery"), "discovery")
    discovered: dict[str, dict[str, Any]] = {}
    active_raw = _object(discovery_raw.get("active"), "discovery.active")
    active_limit = active_raw.get("limit")
    active_count = active_raw.get("returned_count")
    if not isinstance(active_limit, int) or isinstance(active_limit, bool) or not 1 <= active_limit <= 50:
        raise ClosureValidationError("discovery.active.limit must be between 1 and 50")
    if not isinstance(active_count, int) or isinstance(active_count, bool) or active_count < 0:
        raise ClosureValidationError("discovery.active.returned_count must be a non-negative integer")
    active_items = _merge_discovery(discovered, active_raw.get("items"), "discovery.active.items", "active")
    if active_count != len(active_items):
        raise ClosureValidationError("discovery.active.returned_count must match active items")
    if active_count > active_limit:
        raise ClosureValidationError("discovery.active.returned_count exceeds active limit")
    active = {"limit": active_limit, "returned_count": active_count, "items": active_items}

    pinned_raw = _object(discovery_raw.get("pinned"), "discovery.pinned")
    pinned_complete = _boolean(pinned_raw.get("complete"), "discovery.pinned.complete")
    _merge_discovery(discovered, pinned_raw.get("items"), "discovery.pinned.items", "pinned")

    archived_raw = _object(discovery_raw.get("archived"), "discovery.archived")
    archived_local_complete = _boolean(
        archived_raw.get("local_complete"), "discovery.archived.local_complete"
    )
    archived_global_complete = _boolean(
        archived_raw.get("global_complete"), "discovery.archived.global_complete"
    )
    _merge_discovery(discovered, archived_raw.get("items"), "discovery.archived.items", "archived")

    fallback_raw = _object(discovery_raw.get("local_fallback"), "discovery.local_fallback")
    fallback_status = _text(
        fallback_raw.get("schema_status"), "discovery.local_fallback.schema_status", limit=40
    )
    if fallback_status not in {"not_needed", "supported", "missing", "unsupported", "incomplete"}:
        raise ClosureValidationError("discovery.local_fallback.schema_status is unsupported")
    fallback_version = _text(
        fallback_raw.get("schema_version", ""),
        "discovery.local_fallback.schema_version",
        limit=100,
        allow_empty=True,
    )
    _merge_discovery(
        discovered,
        fallback_raw.get("items"),
        "discovery.local_fallback.items",
        "local_fallback",
    )
    probe_supported = bool(
        local_index_probe
        and local_index_probe.get("schema_status") == "supported"
        and local_index_probe.get("compatible_schema_version") == LOCAL_INDEX_VERSION
    )
    fallback_supported = (
        fallback_status == "supported"
        and fallback_version == LOCAL_INDEX_VERSION
        and probe_supported
    )

    unavailable_hosts = _items(discovery_raw.get("unavailable_hosts"), "discovery.unavailable_hosts", limit=100)
    unavailable_sources = _items(
        discovery_raw.get("unavailable_sources"), "discovery.unavailable_sources", limit=100
    )
    unavailable: list[dict[str, str]] = []
    for group, values in (("host", unavailable_hosts), ("source", unavailable_sources)):
        for index, raw in enumerate(values):
            item = _object(raw, f"discovery.unavailable_{group}s[{index}]")
            scope = _text(item.get("scope"), f"unavailable {group} scope", limit=40)
            if scope not in {"local_codex", "global_other"}:
                raise ClosureValidationError(f"unavailable {group} scope is unsupported")
            unavailable.append(
                {
                    "kind": group,
                    "id": _text(item.get("id"), f"unavailable {group} id", limit=200),
                    "scope": scope,
                }
            )

    task_bodies: dict[str, dict[str, Any]] = {}
    total_turns = 0
    total_text = 0
    for index, raw in enumerate(_items(payload.get("tasks"), "tasks", limit=MAX_TASKS)):
        body = _normalize_task_body(raw, index)
        if body["task_id"] in task_bodies:
            raise ClosureValidationError(f"duplicate task body: {body['task_id']}")
        task_bodies[body["task_id"]] = body
        total_turns += len(body["turns"])
        total_text += sum(len(message["text"]) for turn in body["turns"] for message in turn["user_messages"])
    if total_turns > MAX_TURNS:
        raise ClosureValidationError(f"manifest exceeds total turn limit {MAX_TURNS}")
    if total_text > MAX_TEXT_CHARS:
        raise ClosureValidationError("manifest exceeds total user-text limit")
    if mode == "lightweight" and total_text > 24_000:
        raise ClosureValidationError("lightweight manifest exceeds 24,000 user-text characters")

    local_gaps: list[str] = []
    global_gaps: list[str] = []
    if limit_triggered:
        _gap(local_gaps, "lightweight_limit_triggered")
        _gap(global_gaps, "lightweight_limit_triggered")
    active_reaches_local = _listing_reaches_watermark(active, old_local)
    active_reaches_global = _listing_reaches_watermark(active, old_global)
    if not active_reaches_local and not fallback_supported:
        _gap(local_gaps, "active_listing_truncated_without_supported_local_fallback")
    if not active_reaches_global:
        _gap(global_gaps, "active_listing_does_not_reach_global_watermark")
    if not pinned_complete:
        _gap(local_gaps, "pinned_listing_incomplete")
        _gap(global_gaps, "pinned_listing_incomplete")
    if not archived_local_complete:
        _gap(local_gaps, "local_archived_listing_incomplete")
        _gap(global_gaps, "local_archived_listing_incomplete")
    if not archived_global_complete:
        _gap(global_gaps, "global_archived_listing_incomplete")
    for item in unavailable:
        code = f"unavailable_{item['kind']}:{item['id']}"
        if item["scope"] == "local_codex":
            _gap(local_gaps, code)
        _gap(global_gaps, code)

    local_candidates: set[str] = set()
    global_candidates: set[str] = set()
    for task_id, item in discovered.items():
        if item["updated_at"] > scan_upper:
            if item["source_kind"] == "local_codex":
                _gap(local_gaps, f"task:{task_id}:discovered_after_scan_upper")
            _gap(global_gaps, f"task:{task_id}:discovered_after_scan_upper")
            continue
        if item["source_kind"] == "local_codex" and old_local < item["updated_at"]:
            local_candidates.add(task_id)
        if old_global < item["updated_at"]:
            global_candidates.add(task_id)

    for task_id in sorted(local_candidates):
        for code in _task_scope_gaps(
            task_id,
            discovered[task_id],
            task_bodies.get(task_id),
            watermark=old_local,
            scan_upper=scan_upper,
        ):
            _gap(local_gaps, code)
    for task_id in sorted(global_candidates):
        for code in _task_scope_gaps(
            task_id,
            discovered[task_id],
            task_bodies.get(task_id),
            watermark=old_global,
            scan_upper=scan_upper,
        ):
            _gap(global_gaps, code)

    eligible_turns: list[dict[str, Any]] = []
    message_lookup: dict[tuple[str, str, str], dict[str, Any]] = {}
    for task_id in sorted(local_candidates | global_candidates):
        body = task_bodies.get(task_id)
        if body is None:
            continue
        for turn in body["turns"]:
            local_eligible = task_id in local_candidates and old_local < turn["started_at"] <= scan_upper
            global_eligible = task_id in global_candidates and old_global < turn["started_at"] <= scan_upper
            if not local_eligible and not global_eligible:
                continue
            normalized_messages = []
            for message in turn["user_messages"]:
                key = (task_id, turn["turn_id"], message["message_id"])
                if key in message_lookup:
                    raise ClosureValidationError(f"duplicate user message identity in task {task_id}")
                normalized = {**message, "started_at": turn["started_at"]}
                message_lookup[key] = normalized
                normalized_messages.append(message)
            eligible_turns.append(
                {
                    "task_id": task_id,
                    "turn_id": turn["turn_id"],
                    "started_at": canonical_timestamp(turn["started_at"]),
                    "local_window": local_eligible,
                    "global_window": global_eligible,
                    "user_messages": normalized_messages,
                }
            )

    normalized_facts: list[dict[str, Any]] = []
    facts_by_key: dict[str, dict[str, Any]] = {}
    for index, raw in enumerate(_items(payload.get("facts"), "facts", limit=500)):
        label = f"facts[{index}]"
        item = _object(raw, label)
        task_id = _text(item.get("task_id"), f"{label}.task_id", limit=120)
        turn_id = _text(item.get("turn_id"), f"{label}.turn_id", limit=200)
        message_id = _text(item.get("user_message_id"), f"{label}.user_message_id", limit=200)
        fact_type = _text(item.get("fact_type"), f"{label}.fact_type", limit=80)
        if fact_type not in FACT_TYPES:
            raise ClosureValidationError(f"{label}.fact_type is unsupported")
        subject = _text(item.get("subject"), f"{label}.subject", limit=80)
        fact_text = _text(item.get("text"), f"{label}.text", limit=1_000)
        confirmation = _text(item.get("confirmation"), f"{label}.confirmation", limit=80)
        if confirmation not in CONFIRMATION_TYPES:
            raise ClosureValidationError(f"{label}.confirmation is unsupported")
        if item.get("user_confirmed") is not True:
            raise ClosureValidationError(f"{label}.user_confirmed must be true")
        explicit_reference = _boolean(item.get("explicit_reference"), f"{label}.explicit_reference")
        source_message = message_lookup.get((task_id, turn_id, message_id))
        if source_message is None:
            raise ClosureValidationError(f"{label} does not reference an eligible user message")
        source_kind = discovered[task_id]["source_kind"]
        fact_key = _fact_key(task_id, turn_id, message_id, fact_type, subject)
        task_gaps = (
            _task_scope_gaps(
                task_id,
                discovered[task_id],
                task_bodies.get(task_id),
                watermark=old_local if source_kind == "local_codex" else old_global,
                scan_upper=scan_upper,
            )
        )
        scope_complete = not task_gaps and (not local_gaps if source_kind == "local_codex" else not global_gaps)
        write_eligible = write_authorized and (
            scope_complete or (source_kind != "local_codex" and explicit_reference and not task_gaps)
        )
        normalized = {
            "fact_key": fact_key,
            "task_id": task_id,
            "turn_id": turn_id,
            "user_message_id": message_id,
            "fact_type": fact_type,
            "subject": subject,
            "text": fact_text,
            "confirmation": confirmation,
            "source_kind": source_kind,
            "explicit_reference": explicit_reference,
            "write_eligible": write_eligible,
        }
        existing = facts_by_key.get(fact_key)
        if existing is not None and existing != normalized:
            raise ClosureValidationError(f"conflicting facts share stable key {fact_key}")
        if existing is None:
            facts_by_key[fact_key] = normalized
            normalized_facts.append(normalized)

    local_complete = not local_gaps
    global_complete = not global_gaps
    return {
        "schema_version": DECISION_VERSION,
        "manifest_schema_version": MANIFEST_VERSION,
        "mode": mode,
        "watermarks": {
            "old_local": canonical_timestamp(old_local),
            "old_global": canonical_timestamp(old_global),
            "scan_upper": canonical_timestamp(scan_upper),
        },
        "coverage": {
            "local": {
                "complete": local_complete,
                "candidate_task_ids": sorted(local_candidates),
                "gaps": local_gaps,
            },
            "global": {
                "complete": global_complete,
                "candidate_task_ids": sorted(global_candidates),
                "gaps": global_gaps,
            },
            "active_listing": {
                "returned_count": active_count,
                "limit": active_limit,
                "reaches_local_watermark": active_reaches_local,
                "reaches_global_watermark": active_reaches_global,
            },
            "local_fallback": {
                "schema_status": fallback_status,
                "schema_version": fallback_version,
                "probe_verified": probe_supported,
                "accepted": fallback_supported,
            },
        },
        "eligible_turns": eligible_turns,
        "facts": normalized_facts,
        "actions": {
            "write_authorized": write_authorized,
            "any_fact_write_eligible": any(item["write_eligible"] for item in normalized_facts),
            "local_watermark_advance_eligible_after_successful_writes": write_authorized and local_complete,
            "global_watermark_advance_eligible_after_successful_writes": write_authorized and global_complete,
        },
        "report": {
            "discovered_task_count": len(discovered),
            "eligible_turn_count": len(eligible_turns),
            "fact_count": len(normalized_facts),
            "local_status": "complete" if local_complete else "partial",
            "global_status": "complete" if global_complete else "partial",
            "budget": {
                "page_count": page_count,
                "ambiguity_task_count": ambiguity_task_count,
                "limit_triggered": limit_triggered,
            },
        },
    }


def _sqlite_table_columns(connection: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in connection.execute(f'PRAGMA table_info("{table}")')}


def _probe_database(path: Path, required: dict[str, set[str]]) -> dict[str, Any]:
    if not path.is_file():
        return {"path": str(path), "status": "missing", "tables": {}}
    tables: dict[str, Any] = {}
    try:
        connection = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True, timeout=1)
        try:
            for table, columns in required.items():
                actual = _sqlite_table_columns(connection, table)
                missing = sorted(columns - actual)
                tables[table] = {"status": "supported" if not missing else "unsupported", "missing_columns": missing}
        finally:
            connection.close()
    except sqlite3.Error as exc:
        return {"path": str(path), "status": "unreadable", "error": str(exc), "tables": {}}
    supported = all(item["status"] == "supported" for item in tables.values())
    return {"path": str(path), "status": "supported" if supported else "unsupported", "tables": tables}


def probe_local_indexes(codex_home: Path | None = None) -> dict[str, Any]:
    root = (codex_home or Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))).resolve()
    state = _probe_database(root / "state_5.sqlite", STATE_TABLE_COLUMNS)
    history = _probe_database(root / "thread_history_1.sqlite", HISTORY_TABLE_COLUMNS)
    session_path = root / "session_index.jsonl"
    session: dict[str, Any] = {"path": str(session_path), "status": "missing"}
    if session_path.is_file():
        try:
            first: dict[str, Any] | None = None
            with session_path.open("r", encoding="utf-8", errors="strict") as handle:
                for line_number, line in enumerate(handle, start=1):
                    if line_number > 100:
                        break
                    if not line.strip():
                        continue
                    candidate = json.loads(line)
                    if not isinstance(candidate, dict):
                        raise ValueError("entry is not an object")
                    first = candidate
                    break
            missing = sorted({"id", "updated_at"} - set(first or {}))
            session = {
                "path": str(session_path),
                "status": "supported" if first is not None and not missing else "unsupported",
                "missing_fields": missing,
            }
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            session = {"path": str(session_path), "status": "unreadable", "error": str(exc)}
    supported = all(item.get("status") == "supported" for item in (state, history, session))
    return {
        "schema_version": "local-index-probe.v1",
        "codex_home": str(root),
        "schema_status": "supported" if supported else "unsupported",
        "compatible_schema_version": LOCAL_INDEX_VERSION if supported else "",
        "sources": {"state": state, "history": history, "session_index": session},
    }


def discover_local_tasks(
    *,
    after: Any,
    through: Any,
    codex_home: Path | None = None,
) -> dict[str, Any]:
    lower = parse_timestamp(after, "after")
    upper = parse_timestamp(through, "through")
    if lower > upper:
        raise ClosureValidationError("after cannot be later than through")
    probe = probe_local_indexes(codex_home)
    if probe["schema_status"] != "supported":
        raise ClosureValidationError("local Codex indexes are not compatible; discovery stopped")
    root = Path(probe["codex_home"])
    state_path = root / "state_5.sqlite"
    lower_ms = int(lower.timestamp() * 1000)
    upper_ms = int(upper.timestamp() * 1000)
    try:
        connection = sqlite3.connect(f"{state_path.resolve().as_uri()}?mode=ro", uri=True, timeout=1)
        try:
            rows = connection.execute(
                """
                SELECT id,
                       CASE WHEN updated_at_ms > 0 THEN updated_at_ms ELSE updated_at * 1000 END AS effective_updated_ms,
                       archived,
                       rollout_path
                FROM threads
                WHERE (agent_path IS NULL OR agent_path = '')
                  AND COALESCE(thread_source, '') != 'subagent'
                  AND CASE WHEN updated_at_ms > 0 THEN updated_at_ms ELSE updated_at * 1000 END > ?
                  AND CASE WHEN updated_at_ms > 0 THEN updated_at_ms ELSE updated_at * 1000 END <= ?
                ORDER BY effective_updated_ms DESC, id
                """,
                (lower_ms, upper_ms),
            ).fetchall()
        finally:
            connection.close()
    except sqlite3.Error as exc:
        raise ClosureValidationError("local task discovery failed in read-only mode") from exc
    tasks: list[dict[str, Any]] = []
    normalized_root = os.path.normcase(os.path.normpath(str(root)))
    for row in rows:
        raw_rollout = str(row[3])
        normalized_rollout = raw_rollout[4:] if raw_rollout.startswith("\\\\?\\") else raw_rollout
        normalized_rollout = os.path.normcase(os.path.normpath(str(Path(normalized_rollout).resolve())))
        try:
            inside = os.path.commonpath((normalized_root, normalized_rollout)) == normalized_root
        except ValueError:
            inside = False
        if not inside or not Path(normalized_rollout).is_file():
            raise ClosureValidationError(f"local rollout path cannot be safely resolved for task {row[0]}")
        tasks.append(
            {
                "task_id": str(row[0]),
                "source_kind": "local_codex",
                "host_id": "local",
                "updated_at": canonical_timestamp(datetime.fromtimestamp(int(row[1]) / 1000, tz=timezone.utc)),
                "archived": bool(row[2]),
                "rollout_path": normalized_rollout,
            }
        )
    return {
        "schema_version": "local-conversation-discovery.v1",
        "compatible_schema_version": LOCAL_INDEX_VERSION,
        "after": canonical_timestamp(lower),
        "through": canonical_timestamp(upper),
        "task_count": len(tasks),
        "tasks": tasks,
    }
