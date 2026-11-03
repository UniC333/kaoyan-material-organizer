from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, Iterable

from config import load_runtime_config
from kaoyan_kb.domain.review_config import REVIEW_SUBJECT_TOKENS


FLUENCY_LEVELS = (
    "very_fluent",
    "fairly_fluent",
    "not_fluent",
    "needs_remediation",
)
REVIEW_CONTEXTS = {"chapter_review", "scheduled_review"}
PRIORITY = {
    "needs_remediation": 0,
    "not_fluent": 1,
    "fairly_fluent": 2,
    "very_fluent": 3,
}


def _as_date(value: str) -> date:
    return date.fromisoformat(value[:10])


def is_supported_review_subject(subject: str) -> bool:
    normalized = subject.strip().lower()
    enabled = load_runtime_config().review_subjects
    matches = [
        name for name, tokens in REVIEW_SUBJECT_TOKENS.items()
        if any(token in normalized for token in tokens)
    ]
    # Preserve classification of legacy mixed labels, but let config enable them.
    for name in ("英语", "洛谷", "算法竞赛"):
        if name in matches:
            return name in enabled
    return any(name in enabled for name in matches)


def effective_fluency(result: str, fluency: str) -> str:
    if fluency not in FLUENCY_LEVELS:
        raise ValueError(f"unsupported fluency: {fluency}")
    if result == "wrong":
        return "needs_remediation"
    if result == "partial" and PRIORITY[fluency] > PRIORITY["not_fluent"]:
        return "not_fluent"
    return fluency


def next_interval_days(fluency: str, previous_interval: int | None = None) -> int:
    if fluency not in FLUENCY_LEVELS:
        raise ValueError(f"unsupported fluency: {fluency}")
    settings = load_runtime_config().adaptive_review
    rule = settings["intervals"][fluency]
    if previous_interval is None or previous_interval <= 0 or rule["multiplier"] == 0:
        return rule["initial_days"]
    return min(
        rule["max_days"],
        max(settings["min_interval_days"], round(previous_interval * rule["multiplier"])),
    )


def review_identity(subject: str, payload: dict[str, Any]) -> tuple[str, str]:
    return (
        subject.strip(),
        str(payload.get("node_id", "")).strip(),
    )


def latest_review_state(events: Iterable[dict[str, Any]]) -> dict[tuple[str, str], dict[str, Any]]:
    states: dict[tuple[str, str], dict[str, Any]] = {}
    ordered = sorted(events, key=lambda item: str(item.get("occurred_at", "")))
    for event in ordered:
        if event.get("event_type") != "exercise_logged":
            continue
        payload = dict(event.get("payload") or {})
        if payload.get("context") not in REVIEW_CONTEXTS or payload.get("fluency") not in FLUENCY_LEVELS:
            continue
        subject = str(event.get("subject", "")).strip()
        if not is_supported_review_subject(subject):
            continue
        key = review_identity(subject, payload)
        if not key[1]:
            continue
        states[key] = {
            "event_id": str(event.get("event_id", "")),
            "subject": key[0],
            "chapter_title": str(event.get("chapter_title", "")).strip(),
            "node_id": key[1],
            "review_id": str(payload.get("review_id", "")),
            "question_id": str(payload.get("question_id", "")),
            "result": str(payload.get("result", "")),
            "fluency": str(payload.get("fluency", "")),
            "effective_fluency": str(payload.get("effective_fluency", payload.get("fluency", ""))),
            "bottleneck": str(payload.get("note", "")).strip(),
            "hint_used": bool(payload.get("hint_used", False)),
            "interval_days": int(payload.get("interval_days", 0) or 0),
            "due_date": str(payload.get("due_date", "")),
            "estimated_minutes": int(payload.get("estimated_minutes", 0) or 0),
            "tags": [str(item) for item in list(payload.get("tags", []))],
            "source_task_id": str(payload.get("source_task_id", "")).strip(),
            "source_message_ids": [
                str(item).strip() for item in payload.get("source_message_ids", []) if str(item).strip()
            ],
            "last_reviewed_at": str(event.get("occurred_at", "")),
        }
    return states


def previous_interval_for(
    events: Iterable[dict[str, Any]],
    subject: str,
    chapter_title: str,
    node_id: str,
    *,
    before_or_at: str | None = None,
) -> int | None:
    del chapter_title
    candidates = list(events)
    if before_or_at:
        cutoff = datetime.fromisoformat(before_or_at.replace("Z", "+00:00"))
        candidates = [
            event
            for event in candidates
            if str(event.get("occurred_at", "")).strip()
            and datetime.fromisoformat(str(event["occurred_at"]).replace("Z", "+00:00")) <= cutoff
        ]
    state = latest_review_state(candidates).get((subject.strip(), node_id.strip()))
    if not state:
        return None
    value = int(state.get("interval_days", 0) or 0)
    return value or None


def schedule_fields(
    *,
    subject: str,
    result: str,
    fluency: str,
    occurred_at: str,
    previous_interval: int | None,
    duration_minutes: int | None,
) -> dict[str, Any]:
    effective = effective_fluency(result, fluency)
    interval = next_interval_days(effective, previous_interval)
    reviewed_on = _as_date(occurred_at)
    settings = load_runtime_config().adaptive_review
    default_minutes = settings["math_default_minutes"] if "数学" in subject else settings["default_minutes"]
    estimate = duration_minutes if duration_minutes and duration_minutes > 0 else default_minutes
    return {
        "effective_fluency": effective,
        "interval_days": interval,
        "due_date": (reviewed_on + timedelta(days=interval)).isoformat(),
        "estimated_minutes": min(settings["max_estimated_minutes"], max(settings["min_estimated_minutes"], int(estimate))),
    }


def build_due_queue(
    events: Iterable[dict[str, Any]],
    *,
    plan_date: str,
    time_budget_minutes: int | None = None,
    max_items: int | None = None,
) -> dict[str, Any]:
    settings = load_runtime_config().adaptive_review
    time_budget_minutes = settings["time_budget_minutes"] if time_budget_minutes is None else time_budget_minutes
    max_items = settings["max_items"] if max_items is None else max_items
    for name, value in (("time_budget_minutes", time_budget_minutes), ("max_items", max_items)):
        if type(value) is not int or value < 0:
            raise ValueError(f"{name} must be a non-negative integer")
    today = _as_date(plan_date)
    states = list(latest_review_state(events).values())
    due: list[dict[str, Any]] = []
    for state in states:
        due_text = str(state.get("due_date", ""))
        if not due_text:
            continue
        due_on = _as_date(due_text)
        if due_on > today:
            continue
        item = dict(state)
        item["overdue_days"] = (today - due_on).days
        due.append(item)
    due.sort(
        key=lambda item: (
            PRIORITY.get(str(item.get("effective_fluency", "")), 99),
            -int(item.get("overdue_days", 0)),
            str(item.get("due_date", "")),
            str(item.get("node_id", "")),
        )
    )
    selected: list[dict[str, Any]] = []
    used = 0
    full_math_selected = 0
    for item in due:
        estimate = item.get("estimated_minutes")
        estimate = int(settings["default_minutes"] if estimate is None else estimate)
        is_full_math = "数学" in str(item.get("subject", "")) and "full_calculation" in item.get("tags", [])
        if len(selected) >= max(0, max_items) or time_budget_minutes == 0:
            break
        if is_full_math and full_math_selected >= settings["max_full_math_items"]:
            continue
        if selected and used + estimate > max(0, time_budget_minutes):
            continue
        if not selected and estimate > max(0, time_budget_minutes):
            continue
        selected.append(item)
        used += estimate
        full_math_selected += int(is_full_math)
    selected_ids = {item["event_id"] for item in selected}
    deferred = [item for item in due if item["event_id"] not in selected_ids]
    future_dates = sorted(
        str(item.get("due_date", ""))
        for item in states
        if str(item.get("due_date", "")) and _as_date(str(item.get("due_date", ""))) > today
    )
    fluency_counts = {level: 0 for level in FLUENCY_LEVELS}
    for item in states:
        level = str(item.get("effective_fluency", ""))
        if level in fluency_counts:
            fluency_counts[level] += 1
    return {
        "plan_date": today.isoformat(),
        "time_budget_minutes": time_budget_minutes,
        "max_items": max_items,
        "status": "nothing_due" if not due else ("ready" if selected else "deferred_by_budget"),
        "estimated_minutes": used,
        "selected": selected,
        "deferred": deferred,
        "due_count": len(due),
        "next_due_date": future_dates[0] if future_dates else "",
        "fluency_counts": fluency_counts,
    }


def build_checkin_summary(events: Iterable[dict[str, Any]], *, plan_date: str) -> dict[str, Any]:
    today = _as_date(plan_date)
    window_start = today - timedelta(days=6)
    completed_days: set[str] = set()
    completed_today = 0
    completed_items = 0
    for event in events:
        if event.get("event_type") != "exercise_logged":
            continue
        payload = dict(event.get("payload") or {})
        if payload.get("context") not in REVIEW_CONTEXTS:
            continue
        subject = str(event.get("subject", ""))
        if not is_supported_review_subject(subject):
            continue
        occurred_at = str(event.get("occurred_at", ""))
        if not occurred_at:
            continue
        completed_on = _as_date(occurred_at)
        if window_start <= completed_on <= today:
            completed_days.add(completed_on.isoformat())
            completed_items += 1
        if completed_on == today:
            completed_today += 1
    return {
        "plan_date": today.isoformat(),
        "completed_today": completed_today,
        "completed_items_last_7_days": completed_items,
        "completed_days_last_7_days": len(completed_days),
        "recent_completion_dates": sorted(completed_days),
        "streak_is_mastery_evidence": False,
        "missed_day_penalty": False,
    }


def is_duplicate_review_event(
    events: Iterable[dict[str, Any]],
    *,
    subject: str,
    chapter_title: str,
    review_id: str,
    question_id: str,
    node_id: str,
) -> dict[str, Any] | None:
    marker = (subject.strip(), chapter_title.strip(), review_id.strip(), question_id.strip(), node_id.strip())
    for event in reversed(list(events)):
        if event.get("event_type") != "exercise_logged":
            continue
        payload = dict(event.get("payload") or {})
        candidate = (
            str(event.get("subject", "")).strip(),
            str(event.get("chapter_title", "")).strip(),
            str(payload.get("review_id", "")).strip(),
            str(payload.get("question_id", "")).strip(),
            str(payload.get("node_id", "")).strip(),
        )
        if marker == candidate:
            return event
    return None
