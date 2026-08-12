from __future__ import annotations

import sys
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from kaoyan_kb.domain.review_scheduler import (
    build_checkin_summary,
    build_due_queue,
    effective_fluency,
    is_duplicate_review_event,
    next_interval_days,
    schedule_fields,
)


def _event(
    *,
    event_id: str,
    subject: str = "数学",
    node_id: str,
    fluency: str,
    due_date: str,
    minutes: int = 5,
    occurred_at: str = "2026-08-01T10:00:00+08:00",
    tags: list[str] | None = None,
) -> dict:
    return {
        "event_id": event_id,
        "event_type": "exercise_logged",
        "occurred_at": occurred_at,
        "subject": subject,
        "chapter_title": "第三章",
        "payload": {
            "context": "chapter_review",
            "review_id": "review-1",
            "question_id": f"question-{event_id}",
            "node_id": node_id,
            "result": "right",
            "fluency": fluency,
            "effective_fluency": fluency,
            "interval_days": 3,
            "due_date": due_date,
            "estimated_minutes": minutes,
            "tags": tags or [],
        },
    }


def test_objective_result_caps_subjective_fluency() -> None:
    assert effective_fluency("wrong", "very_fluent") == "needs_remediation"
    assert effective_fluency("partial", "very_fluent") == "not_fluent"
    assert effective_fluency("right", "not_fluent") == "not_fluent"


def test_intervals_seed_expand_and_reset() -> None:
    assert next_interval_days("very_fluent") == 14
    assert next_interval_days("fairly_fluent") == 7
    assert next_interval_days("not_fluent") == 3
    assert next_interval_days("needs_remediation") == 1
    assert next_interval_days("very_fluent", 14) == 28
    assert next_interval_days("very_fluent", 30) == 45
    assert next_interval_days("fairly_fluent", 14) == 21
    assert next_interval_days("not_fluent", 14) == 3


def test_schedule_fields_keep_right_but_not_fluent_separate() -> None:
    fields = schedule_fields(
        subject="数学",
        result="right",
        fluency="not_fluent",
        occurred_at="2026-08-12T10:00:00+08:00",
        previous_interval=None,
        duration_minutes=9,
    )

    assert fields["effective_fluency"] == "not_fluent"
    assert fields["interval_days"] == 3
    assert fields["due_date"] == "2026-08-15"
    assert fields["estimated_minutes"] == 9


def test_due_queue_is_priority_ordered_and_bounded() -> None:
    events = [
        _event(event_id="easy", node_id="MATH-EASY", fluency="very_fluent", due_date="2026-08-10", minutes=5),
        _event(event_id="hard", node_id="MATH-HARD", fluency="needs_remediation", due_date="2026-08-12", minutes=12),
        _event(event_id="middle", node_id="MATH-MIDDLE", fluency="not_fluent", due_date="2026-08-11", minutes=8),
        _event(event_id="english", subject="英语", node_id="WORD", fluency="needs_remediation", due_date="2026-08-01"),
    ]

    queue = build_due_queue(events, plan_date="2026-08-12", time_budget_minutes=20, max_items=3)

    assert [item["node_id"] for item in queue["selected"]] == ["MATH-HARD", "MATH-MIDDLE"]
    assert queue["estimated_minutes"] == 20
    assert [item["node_id"] for item in queue["deferred"]] == ["MATH-EASY"]


def test_latest_event_replaces_older_state_for_same_knowledge_point() -> None:
    older = _event(event_id="old", node_id="MATH-1", fluency="needs_remediation", due_date="2026-08-02")
    newer = _event(
        event_id="new",
        node_id="MATH-1",
        fluency="very_fluent",
        due_date="2026-08-20",
        occurred_at="2026-08-12T10:00:00+08:00",
    )

    queue = build_due_queue([older, newer], plan_date="2026-08-12")

    assert queue["status"] == "nothing_due"


def test_review_identity_is_idempotent_per_question_and_knowledge_point() -> None:
    event = _event(event_id="one", node_id="MATH-1", fluency="fairly_fluent", due_date="2026-08-19")

    duplicate = is_duplicate_review_event(
        [event],
        subject="数学",
        chapter_title="第三章",
        review_id="review-1",
        question_id="question-one",
        node_id="MATH-1",
    )

    assert duplicate is event


def test_checkin_summary_counts_real_review_events_only() -> None:
    events = [
        _event(event_id="today", node_id="MATH-1", fluency="fairly_fluent", due_date="2026-08-19", occurred_at="2026-08-12T09:00:00+08:00"),
        _event(event_id="older", node_id="MATH-2", fluency="fairly_fluent", due_date="2026-08-12", occurred_at="2026-08-07T09:00:00+08:00"),
        _event(event_id="too-old", node_id="MATH-3", fluency="fairly_fluent", due_date="2026-08-01", occurred_at="2026-08-01T09:00:00+08:00"),
    ]

    summary = build_checkin_summary(events, plan_date="2026-08-12")

    assert summary["completed_today"] == 1
    assert summary["completed_items_last_7_days"] == 2
    assert summary["completed_days_last_7_days"] == 2
    assert summary["missed_day_penalty"] is False


def test_due_queue_selects_at_most_one_full_math_calculation() -> None:
    events = [
        _event(event_id="math-1", node_id="MATH-1", fluency="not_fluent", due_date="2026-08-12", minutes=8, tags=["full_calculation"]),
        _event(event_id="math-2", node_id="MATH-2", fluency="not_fluent", due_date="2026-08-12", minutes=8, tags=["full_calculation"]),
        _event(event_id="ds", subject="408", node_id="DS-1", fluency="fairly_fluent", due_date="2026-08-12", minutes=5),
    ]

    queue = build_due_queue(events, plan_date="2026-08-12", time_budget_minutes=20, max_items=3)

    assert [item["node_id"] for item in queue["selected"]] == ["MATH-1", "DS-1"]
    assert [item["node_id"] for item in queue["deferred"]] == ["MATH-2"]
