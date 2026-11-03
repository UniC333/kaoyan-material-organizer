from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from kaoyan_kb.domain.review_scheduler import (
    build_checkin_summary,
    build_due_queue,
    effective_fluency,
    is_duplicate_review_event,
    is_supported_review_subject,
    next_interval_days,
    previous_interval_for,
    schedule_fields,
)
import config


@pytest.fixture(autouse=True)
def review_config(monkeypatch, tmp_path):
    for key in tuple(os.environ):
        if key.startswith("KAOYAN_"):
            monkeypatch.delenv(key)
    path = tmp_path / "kaoyan.config.json"
    monkeypatch.setenv("KAOYAN_CONFIG_FILE", str(path))

    def configure(**values):
        path.write_text(json.dumps(values, ensure_ascii=False), encoding="utf-8")
        config.reset_runtime_config_cache()

    configure()
    yield configure
    config.reset_runtime_config_cache()


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


def test_historical_batch_ignores_review_events_that_happened_later() -> None:
    older = _event(
        event_id="older",
        node_id="MATH-1",
        fluency="not_fluent",
        due_date="2026-08-04",
        occurred_at="2026-08-01T10:00:00+08:00",
    )
    future = _event(
        event_id="future",
        node_id="MATH-1",
        fluency="very_fluent",
        due_date="2026-09-20",
        occurred_at="2026-09-01T10:00:00+08:00",
    )

    interval = previous_interval_for(
        [older, future],
        "数学",
        "第三章",
        "MATH-1",
        before_or_at="2026-08-27T10:00:00+08:00",
    )

    assert interval == 3


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


@pytest.mark.parametrize("subject, supported", [
    ("数学", True), ("高等数学", True), ("10_数学", True),
    ("408", True), ("408 数据结构", True), ("数据结构", True),
    ("计算机组成原理", True), ("操作系统", True), ("计算机网络", True),
    ("英语", False), ("ENGLISH", False), ("政治", False),
    ("洛谷", False), ("算法竞赛", False), ("", False), ("math", False),
    ("英语/数学", False), ("english408", False), ("洛谷数据结构", False),
    ("算法竞赛数学", False), ("政治数学", True),
])
def test_default_subject_pool_preserves_legacy_labels(subject, supported):
    assert is_supported_review_subject(subject) is supported


def test_subject_changes_apply_to_queue_history_and_checkins(review_config):
    events = [
        _event(event_id=subject, subject=subject, node_id=subject, fluency="very_fluent",
               due_date="2026-08-12", occurred_at="2026-08-12T09:00:00+08:00")
        for subject in ("数学", "408", "英语", "政治", "洛谷", "算法竞赛")
    ]
    for subject in ("英语", "政治", "洛谷", "算法竞赛", "408"):
        review_config(review_subjects=[subject])
        queue = build_due_queue(events, plan_date="2026-08-12")
        assert [item["subject"] for item in queue["selected"]] == [subject]
        assert queue["due_count"] == 1
        assert previous_interval_for(events, subject, "第三章", subject) == 3
        assert build_checkin_summary(events, plan_date="2026-08-12")["completed_today"] == 1


def test_legacy_exclusions_can_be_enabled_without_code_changes(review_config):
    for subject, label in [("英语", "English/数学"), ("洛谷", "洛谷数据结构"),
                           ("算法竞赛", "算法竞赛数学")]:
        review_config(review_subjects=[subject])
        assert is_supported_review_subject(label) is True
    review_config(review_subjects=["数学"])
    assert is_supported_review_subject("数据结构") is False
    review_config(review_subjects=["408"])
    assert is_supported_review_subject("高等数学") is False


def test_custom_intervals_and_estimates(review_config):
    review_config(adaptive_review={
        "intervals": {
            "very_fluent": {"initial_days": 4, "multiplier": 3, "max_days": 30},
            "fairly_fluent": {"initial_days": 2, "multiplier": 2.5, "max_days": 40},
            "not_fluent": {"initial_days": 6},
            "needs_remediation": {"initial_days": 0},
        },
        "min_interval_days": 5,
        "default_minutes": 7, "math_default_minutes": 15,
        "min_estimated_minutes": 2, "max_estimated_minutes": 18,
    })
    assert next_interval_days("very_fluent") == 4
    assert next_interval_days("very_fluent", 4) == 12
    assert next_interval_days("very_fluent", 20) == 30
    assert next_interval_days("fairly_fluent", 1) == 5
    assert next_interval_days("fairly_fluent", 4) == 10
    assert next_interval_days("not_fluent", 20) == 6
    assert next_interval_days("needs_remediation", 20) == 0
    for subject, duration, expected in [("数学", None, 15), ("英语", None, 7),
                                         ("英语", 1, 2), ("数学", 30, 18)]:
        fields = schedule_fields(subject=subject, result="right", fluency="very_fluent",
                                 occurred_at="2026-08-12", previous_interval=None,
                                 duration_minutes=duration)
        assert fields["estimated_minutes"] == expected
        assert fields["due_date"] == "2026-08-16"


def test_configured_daily_caps_and_explicit_overrides(review_config):
    events = [
        _event(event_id=str(i), node_id=f"MATH-{i}", fluency="not_fluent",
               due_date="2026-08-12", minutes=8, tags=["full_calculation"])
        for i in range(4)
    ]
    review_config(adaptive_review={"time_budget_minutes": 32, "max_items": 4,
                                   "max_full_math_items": 2})
    assert len(build_due_queue(events, plan_date="2026-08-12")["selected"]) == 2
    review_config(adaptive_review={"time_budget_minutes": 8, "max_items": 4,
                                   "max_full_math_items": 4})
    assert len(build_due_queue(events, plan_date="2026-08-12")["selected"]) == 1
    assert len(build_due_queue(events, plan_date="2026-08-12", time_budget_minutes=32)["selected"]) == 4
    review_config(adaptive_review={"time_budget_minutes": 32, "max_items": 1,
                                   "max_full_math_items": 4})
    assert len(build_due_queue(events, plan_date="2026-08-12")["selected"]) == 1
    assert len(build_due_queue(events, plan_date="2026-08-12", max_items=4)["selected"]) == 4
    overridden = build_due_queue(events, plan_date="2026-08-12", time_budget_minutes=32, max_items=4)
    assert len(overridden["selected"]) == 4
    assert build_due_queue(events, plan_date="2026-08-12", max_items=0)["selected"] == []
    assert build_due_queue(events, plan_date="2026-08-12", time_budget_minutes=0)["selected"] == []
    review_config(adaptive_review={"max_full_math_items": 0})
    assert build_due_queue(events, plan_date="2026-08-12")["selected"] == []



def test_due_queue_honors_zero_estimate_with_positive_budget(review_config):
    review_config(adaptive_review={"min_estimated_minutes": 0, "max_estimated_minutes": 0})
    event = _event(event_id="zero", node_id="MATH-ZERO", fluency="not_fluent",
                   due_date="2026-08-12", minutes=0)

    queue = build_due_queue([event], plan_date="2026-08-12", time_budget_minutes=4)

    assert queue["status"] == "ready"
    assert [item["node_id"] for item in queue["selected"]] == ["MATH-ZERO"]
    assert queue["selected"][0]["estimated_minutes"] == 0
    assert queue["estimated_minutes"] == 0
    assert queue["deferred"] == []


def test_due_queue_zero_budget_defers_zero_estimate(review_config):
    review_config(adaptive_review={
        "default_minutes": 0, "math_default_minutes": 0,
        "min_estimated_minutes": 0, "max_estimated_minutes": 0,
    })
    event = _event(event_id="zero", node_id="MATH-ZERO", fluency="not_fluent",
                   due_date="2026-08-12", minutes=0)

    queue = build_due_queue([event], plan_date="2026-08-12", time_budget_minutes=0)

    assert queue["status"] == "deferred_by_budget"
    assert queue["selected"] == []
    assert queue["estimated_minutes"] == 0
    assert queue["due_count"] == 1
    assert [item["node_id"] for item in queue["deferred"]] == ["MATH-ZERO"]
