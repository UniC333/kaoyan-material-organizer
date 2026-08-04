from __future__ import annotations

import sys
from pathlib import Path

import pytest


SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import answer_local_question as answer_module
import ask_local_knowledge as ask_module
from save_local_answer import save_answer_contract, save_eligibility


def _result(*, intent: str = "define", answer_mode: str = "chapter_fallback", page_anchor: dict | None = None) -> dict:
    return {
        "subject": "数学",
        "chapter": "第三章",
        "query": "书上有类似推导吗？",
        "intent": intent,
        "answer_mode": answer_mode,
        "fallback_note": "当前未命中正式主张，仅基于章节层回退。",
        "syllabus_route": [],
        "references": [],
        "retrieval_hits": [],
        "claim_hits": [],
        "evidence_hits": [],
        "fallback_hits": [],
        "page_anchor": page_anchor or {},
        "teaching_context": {},
        "compare_bundle": None,
        "refinement_candidates": [],
        "learner_snapshot": {},
    }


def test_source_verify_without_structured_evidence_stops_at_unconfirmed(monkeypatch) -> None:
    monkeypatch.setattr(answer_module, "build_citations", lambda result: [])
    contract = answer_module.build_answer_contract(_result(intent="source_verify"))

    assert contract["evidence_assessment"]["level"] == "structured_unconfirmed"
    assert "不能把章节摘要" in contract["evidence_assessment"]["cannot_confirm"]
    assert contract["sections"]["direct_conclusion"] == contract["evidence_assessment"]["can_confirm"]


def test_exact_asset_is_never_presented_as_structured_textbook_evidence(monkeypatch) -> None:
    monkeypatch.setattr(answer_module, "build_citations", lambda result: [])
    contract = answer_module.build_answer_contract(
        _result(
            intent="source_verify",
            answer_mode="page_asset",
            page_anchor={"match_status": "exact_asset", "source_image_path": "P63.jpg"},
        )
    )

    assert contract["evidence_assessment"]["level"] == "page_asset_only"
    assert "不能仅据页码映射确认" in contract["evidence_assessment"]["cannot_confirm"]
    assert not contract["citation_coverage_ok"] or not contract["citations"]
    assert contract["content_provenance"][0]["source_type"] == "page_asset_only"
    assert contract["content_provenance"][0]["textbook_assertion_allowed"] is False
    assert "教材正文未确认" in answer_module.render_text(contract)


def test_supplemental_derivation_has_its_own_identity(monkeypatch) -> None:
    monkeypatch.setattr(answer_module, "build_citations", lambda result: [])
    result = _result(
        answer_mode="page_asset",
        page_anchor={"match_status": "exact_asset", "requested_page": 72, "exercise_label": "例3.15"},
    )
    result["supplementary_content"] = [
        {"title": "根式的泛化换元", "explanation": "这是补充同类结构。"}
    ]
    contract = answer_module.build_answer_contract(result)

    primary, supplement = contract["content_provenance"]
    assert primary["printed_page"] == 72
    assert primary["exercise_label"] == "例3.15"
    assert supplement["source_type"] == "supplementary_derivation"
    assert supplement["printed_page"] is None
    assert supplement["exercise_label"] == ""
    assert supplement["related_to"] == "primary-answer"


def test_blocked_page_asset_save_has_zero_writes(tmp_path: Path) -> None:
    contract = {
        "answer_mode": "page_asset",
        "citation_coverage_ok": False,
        "evidence_assessment": {"level": "page_asset_only"},
        "query_result": _result(answer_mode="page_asset", page_anchor={"match_status": "exact_asset"}),
    }

    with pytest.raises(ValueError, match="没有可保存的结构化证据"):
        save_answer_contract(
            contract=contract,
            vault_root=tmp_path,
            subject="数学",
            chapter="第三章",
            question="书上有吗",
            saved_at="2026-08-02",
        )
    assert list(tmp_path.iterdir()) == []


def test_save_eligibility_requires_citations_for_fact_answers() -> None:
    allowed, reason = save_eligibility(
        {
            "answer_mode": "accepted_evidence",
            "citation_coverage_ok": False,
            "evidence_assessment": {"level": "structured_evidence"},
        }
    )
    assert not allowed
    assert "缺少完整引用" in reason


def test_ask_queries_once_and_passes_the_same_contract_to_save(monkeypatch, tmp_path: Path, capsys) -> None:
    calls: list[object] = []
    result = _result(answer_mode="accepted_evidence")
    result["references"] = [{"evidence_id": "EV-1"}]
    contract = {"answer_contract_version": "test", "answer_mode": "accepted_evidence", "citation_coverage_ok": True, "evidence_assessment": {"level": "structured_evidence", "can_confirm": "ok", "cannot_confirm": "", "next_action": ""}, "query_result": result, "intent": "define", "syllabus_route": [], "references": result["references"], "sections": {}}
    monkeypatch.setattr(ask_module, "query_knowledge", lambda *args, **kwargs: calls.append("query") or result)
    monkeypatch.setattr(ask_module, "build_answer_contract", lambda value: calls.append(value) or contract)
    monkeypatch.setattr(ask_module, "save_answer_contract", lambda **kwargs: calls.append(kwargs["contract"]) or tmp_path / "index.md")
    monkeypatch.setattr(sys, "argv", ["ask_local_knowledge.py", "--subject", "数学", "--question", "定义是什么", "--save", "--format", "json"])

    assert ask_module.main() == 0
    assert calls.count("query") == 1
    assert calls[-1] is contract
    assert capsys.readouterr().out
