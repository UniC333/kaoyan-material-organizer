from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Iterable


FLUENCY_LEVELS = (
    "very_fluent",
    "fairly_fluent",
    "not_fluent",
    "needs_remediation",
)
REVIEW_CONTEXTS = {"chapter_review", "scheduled_review"}
INITIAL_INTERVALS = {
    "very_fluent": 14,
    "fairly_fluent": 7,
    "not_fluent": 3,
    "needs_remediation": 1,
}
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
    excluded = ("英语", "english", "洛谷", "算法竞赛")
    if any(token in normalized for token in excluded):
        return False
    allowed = ("数学", "408", "数据结构", "计算机组成", "操作系统", "计算机网络")
    return any(token in normalized for token in allowed)


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
    if previous_interval is None or previous_interval <= 0:
        return INITIAL_INTERVALS[fluency]
    if fluency == "very_fluent":
        return min(45, max(1, previous_interval * 2))
    if fluency == "fairly_fluent":
        return min(21, max(1, round(previous_interval * 1.5)))
    return INITIAL_INTERVALS[fluency]


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
            "last_reviewed_at": str(event.get("occurred_at", "")),
        }
    return states


def previous_interval_for(
    events: Iterable[dict[str, Any]], subject: str, chapter_title: str, node_id: str
) -> int | None:
    del chapter_title
    state = latest_review_state(events).get((subject.strip(), node_id.strip()))
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
    default_minutes = 10 if "数学" in subject else 5
    estimate = duration_minutes if duration_minutes and duration_minutes > 0 else default_minutes
    return {
        "effective_fluency": effective,
        "interval_days": interval,
        "due_date": (reviewed_on + timedelta(days=interval)).isoformat(),
        "estimated_minutes": min(12, max(3, int(estimate))),
    }


def build_due_queue(
    events: Iterable[dict[str, Any]],
    *,
    plan_date: str,
    time_budget_minutes: int = 20,
    max_items: int = 3,
) -> dict[str, Any]:
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
    full_math_selected = False
    for item in due:
        estimate = int(item.get("estimated_minutes", 0) or 5)
        is_full_math = "数学" in str(item.get("subject", "")) and "full_calculation" in item.get("tags", [])
        if len(selected) >= max(0, max_items):
            break
        if is_full_math and full_math_selected:
            continue
        if selected and used + estimate > max(0, time_budget_minutes):
            continue
        if not selected and estimate > max(0, time_budget_minutes):
            continue
        selected.append(item)
        used += estimate
        full_math_selected = full_math_selected or is_full_math
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
