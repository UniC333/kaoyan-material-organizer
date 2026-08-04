from __future__ import annotations

import sys
from pathlib import Path

import pytest


SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import conversation_distillation as distillation
from kaoyan_kb.domain.teaching_context import build_bounded_teaching_context


def _bundle() -> dict:
    return {
        "session_id": "019fc65e-9174-77b0-b6c5-ee9fafee3a57",
        "source_digest": "a" * 64,
        "messages": [{"id": "message-1"}],
    }


def _payload() -> dict:
    return {
        "subject": "数学",
        "chapter_title": "第三章",
        "topic": "正割积分与三角换元",
        "accepted_core": ["根式消去后仍可能留下正割积分。"],
        "learning_items": [
            {
                "item_id": "p72-example",
                "kind": "original_problem",
                "source_type": "page_asset_only",
                "mastery_status": "guided_complete",
                "title": "P72 例题",
                "source_summary": "已精确定位原页，尚无结构化 OCR。",
                "handoff_summary": "从三角换元后的分部积分继续。",
                "self_check": "不看提示写出回代。",
            },
            {
                "item_id": "sec-generalization",
                "kind": "supplementary_derivation",
                "related_to": "p72-example",
                "source_type": "supplementary_derivation",
                "mastery_status": "pending_verification",
                "title": "正割积分的泛化推导",
            },
        ],
    }


def test_distillation_candidate_keeps_source_and_mastery_boundaries() -> None:
    candidate = distillation.build_distillation_candidate(_bundle(), _payload(), now="2026-08-04T10:00:00+08:00")

    assert candidate["fact_write_allowed"] is False
    assert candidate["learning_items"][0]["source_type"] == "page_asset_only"
    assert candidate["learning_items"][1]["related_to"] == "p72-example"
    rendered = distillation.render_candidate_note(candidate)
    assert "仅原页定位" in rendered
    assert "补充推导" in rendered
    assert "跟随完成" in rendered
    assert "待验证" in rendered


def test_supplementary_item_must_reference_original_problem() -> None:
    payload = _payload()
    payload["learning_items"][1]["related_to"] = "missing-item"

    with pytest.raises(distillation.DistillationError, match="related_to"):
        distillation.build_distillation_candidate(_bundle(), payload, now="2026-08-04T10:00:00+08:00")


def test_published_learning_items_create_a_topic_scoped_handoff() -> None:
    candidate = distillation.build_distillation_candidate(_bundle(), _payload(), now="2026-08-04T10:00:00+08:00")
    event = {
        "event_id": "event-1",
        "event_type": "understanding_distilled",
        "occurred_at": "2026-08-04T10:00:00+08:00",
        "subject": "数学",
        "chapter_title": "第三章",
        "intake_decision": {"learner_model_eligible": True},
        "payload": {
            "history_status": "active",
            "candidate_id": candidate["candidate_id"],
            "topic": candidate["topic"],
            "accepted_core": candidate["accepted_core"],
            "learning_items": candidate["learning_items"],
            "source_session_id": candidate["source"]["session_id"],
        },
    }

    context = build_bounded_teaching_context([event], subject="数学", chapter="第三章", query="正割积分怎么推导", as_of="2026-08-04")

    assert context["learning_handoff"]["original_problem"]["item_id"] == "p72-example"
    assert context["learning_handoff"]["supplementary_content"][0]["item_id"] == "sec-generalization"
