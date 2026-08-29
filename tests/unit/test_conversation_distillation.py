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


def test_skill_contract_keeps_state_first_recording_boundaries() -> None:
    skill = (Path(__file__).resolve().parents[2] / "SKILL.md").read_text(encoding="utf-8")

    assert "状态优先分流" in skill
    assert "普通进度陈述默认零写入" in skill
    assert "纯时长、背词、章节推进等进度不生成问答" in skill
    assert "当天多主题且无明确主次时跳过蒸馏" in skill


def test_skill_contract_requires_complete_cross_task_daily_closure() -> None:
    skill = (Path(__file__).resolve().parents[2] / "SKILL.md").read_text(encoding="utf-8")

    assert "任务列表按更新时间发现候选" in skill
    assert "标题和摘要只用于发现" in skill
    assert "分页读取直到已经越过该日期" in skill
    assert "对候选任务按任务 ID 去重" in skill
    assert "扫描、纳入、跳过" in skill
    assert "不得声称已经汇总全天" in skill
    assert "工作目录位于配置 `vault_root`" in skill


def test_publish_without_confirmation_has_zero_writes(tmp_path: Path) -> None:
    with pytest.raises(distillation.DistillationError, match="explicit --yes confirmation is required"):
        distillation.publish_candidate("missing-candidate", vault_root=tmp_path, confirmed=False)

    assert list(tmp_path.iterdir()) == []


def test_publish_candidate_is_idempotent(monkeypatch, tmp_path: Path) -> None:
    candidate = distillation.build_distillation_candidate(
        _bundle(),
        _payload(),
        now="2026-08-04T10:00:00+08:00",
    )
    store_path = tmp_path / "distillation_candidates.json"
    store = {"contract_version": candidate["contract_version"], "items": [candidate]}
    events: list[dict] = []

    monkeypatch.setattr(distillation, "_candidate_store", lambda: (store_path, store))
    monkeypatch.setattr(distillation, "resolve_subject", lambda subject: (subject, {"dir": "10_数学"}))
    monkeypatch.setattr(distillation, "_rebuild_saved_qa", lambda vault_root: None)
    monkeypatch.setattr(distillation, "load_events", lambda: events)
    monkeypatch.setattr(distillation, "append_event", lambda **kwargs: events.append(kwargs))
    monkeypatch.setattr(distillation, "rebuild_views", lambda: None)

    first = distillation.publish_candidate(candidate["candidate_id"], vault_root=tmp_path, confirmed=True)
    second = distillation.publish_candidate(candidate["candidate_id"], vault_root=tmp_path, confirmed=True)

    assert first["already_published"] is False
    assert second == {
        "candidate_id": candidate["candidate_id"],
        "note_path": first["note_path"],
        "already_published": True,
    }
    assert Path(first["note_path"]).is_file()
    assert len(events) == 1
