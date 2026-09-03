from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest


SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import answer_local_question as answer_module
import ask_local_knowledge as ask_module
import build_pdf_ocr_review_artifact as pdf_review_artifact_module
import kb as kb_module
import lint_kb_entities as lint_module
import publish_pdf_ocr_evidence as pdf_publish_module
import query_local_knowledge as query_module
from save_local_answer import save_answer_contract, save_eligibility


def test_lint_never_treats_retained_stale_evidence_as_publishable(monkeypatch, tmp_path: Path, capsys) -> None:
    layout = {name: tmp_path / name for name in ("evidence", "claims", "conflicts")}
    stale = {"evidence_id": "EV-STALE", "verification_status": "stale", "mapping_status": "stale"}
    claim = {
        "claim_id": "CL-STALE",
        "syllabus_node_id": "NODE-1",
        "canonical_text": "旧结论",
        "evidence_ids": ["EV-STALE"],
    }
    payloads = {layout["evidence"]: [stale], layout["claims"]: [claim], layout["conflicts"]: []}
    monkeypatch.setattr(lint_module, "parse_args", lambda: type("Args", (), {"format": "json"})())
    monkeypatch.setattr(lint_module, "ensure_kb_layout", lambda: layout)
    monkeypatch.setattr(lint_module, "load_all_json", lambda path: payloads[path])

    assert lint_module.main() == 1
    result = json.loads(capsys.readouterr().out)
    assert any(item["message"] == "claim references non-publishable evidence: EV-STALE" for item in result["errors"])


def test_ask_query_alias_is_normalized_by_direct_and_wrapper_parsers(monkeypatch) -> None:
    monkeypatch.setattr(sys, "argv", ["ask_local_knowledge.py", "--subject", "数学", "--query", "同一个问题"])
    direct = ask_module.parse_args()
    wrapper = kb_module.build_parser().parse_args(["ask", "--subject", "数学", "--query", "同一个问题"])

    assert direct.question == "同一个问题"
    assert wrapper.question == "同一个问题"


def test_ask_question_and_query_alias_are_mutually_exclusive(monkeypatch) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        ["ask_local_knowledge.py", "--subject", "数学", "--question", "一", "--query", "二"],
    )
    with pytest.raises(SystemExit):
        ask_module.parse_args()
    with pytest.raises(SystemExit):
        kb_module.build_parser().parse_args(
            ["ask", "--subject", "数学", "--question", "一", "--query", "二"]
        )


def test_pdf_ocr_publication_uses_accepted_review_overlay() -> None:
    normalized = {
        "chunk_candidates": [
            {"block_id": "b1", "text": "next[1]=l"},
            {"block_id": "b2", "text": "unchanged"},
        ]
    }
    overlay = {
        "b1": {"review_status": "accepted", "corrected_text": "next[1]=1"},
        "b2": {"review_status": "pending", "corrected_text": "wrong"},
    }

    assert pdf_publish_module._reviewed_page_text(normalized, overlay) == "next[1]=1\nunchanged"


def test_pdf_ocr_publication_keeps_printed_page_separate_from_pdf_page() -> None:
    chapter = {"printed_page": 121}
    handoff = {"printed_page": 109}

    assert pdf_publish_module._resolved_printed_page(chapter, handoff, 121) == 109


def test_pdf_review_artifact_uses_confirmed_printed_page() -> None:
    page = {"printed_page": 121}
    decision = {"printed_page": 109, "page_header_verified": True}

    assert pdf_review_artifact_module._reviewed_printed_page(page, decision, 121) == 109


def test_pdf_mapping_approval_cannot_close_a_pending_sensitive_block() -> None:
    decision = {"review_status": "accepted"}
    assert pdf_review_artifact_module._resolved_page_review_status(decision, 1) == "pending"
    assert pdf_review_artifact_module._resolved_page_review_status(decision, 0) == "accepted"


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


def test_direct_conclusion_extracts_the_relevant_definition_from_evidence() -> None:
    result = _result(answer_mode="accepted_evidence")
    result["query"] = "栈的定义是什么？"
    result["evidence_hits"] = [
        {
            "title": "第3章 栈、队列和数组（PDF 第76页）",
            "content": "# 栈的定义\n栈是只允许在一端进行插入或删除操作的线性表。\n# 队列的定义\n队列是只允许在一端插入、另一端删除的线性表。",
        }
    ]

    conclusion = answer_module.direct_conclusion(result)

    assert "栈是只允许在一端进行插入或删除操作的线性表" in conclusion
    assert "来源：第3章 栈、队列和数组（PDF 第76页）" in conclusion


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


def test_answer_render_includes_page_verification_summary(monkeypatch) -> None:
    monkeypatch.setattr(answer_module, "build_citations", lambda result: [])
    result = _result(
        answer_mode="page_asset",
        page_anchor={"match_status": "exact_asset", "requested_page": 76, "exercise_match_status": "unverified"},
    )
    result["page_verification"] = {
        "page_location_status": "exact_asset",
        "exercise_verification_status": "unverified",
        "answer_mode": "page_asset",
        "textbook_explanation_allowed": False,
        "summary": "教材原页已定位；教材正文未确认，不能按书上原题讲解。",
    }

    rendered = answer_module.render_text(answer_module.build_answer_contract(result))

    assert "## 页码核验摘要" in rendered
    assert "页面定位：exact_asset" in rendered
    assert "题号正文核验：unverified" in rendered
    assert "教材原页已定位；教材正文未确认" in rendered


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


def test_sourced_problem_without_source_answer_blocks_conclusion_and_save(monkeypatch) -> None:
    monkeypatch.setattr(answer_module, "build_citations", lambda result: [])
    result = _result(answer_mode="accepted_evidence", page_anchor={"match_status": "exact_evidence", "requested_page": 64})
    result["answer_grounding"] = {"required": True, "status": "answer_not_found", "can_conclude": False, "problem": {"evidence_ids": ["EV-Q"]}, "solution": {}, "failure_reason": "原书答案未确认。", "next_action": "先定位题解。"}
    result["supplementary_content"] = [{"explanation": "错误的 AI 独立推导"}]

    contract = answer_module.build_answer_contract(result)

    assert contract["answer_contract_version"] == "m6.answer.v3"
    assert contract["sections"]["direct_conclusion"] == "原书答案未确认。"
    assert contract["sections"]["supplementary_derivation"] == []
    assert "错误的 AI 独立推导" not in answer_module.render_text(contract)
    allowed, reason = save_eligibility(contract)
    assert not allowed
    assert "原书答案" in reason


def test_exact_answer_requires_problem_and_solution_citations(monkeypatch) -> None:
    result = _result(answer_mode="accepted_evidence")
    result["answer_grounding"] = {"required": True, "status": "exact_answer", "can_conclude": True, "problem": {"evidence_ids": ["EV-Q"], "printed_pages": [64], "exercise_label": "例3.8", "content": "原题"}, "solution": {"evidence_ids": ["EV-A"], "printed_pages": [65], "exercise_label": "例3.8", "content": "\\frac{\\pi}{3}+2-\\sqrt3"}, "failure_reason": "", "next_action": ""}
    result["supplementary_content"] = [{"explanation": "由偶函数折半后保留整体系数 2。"}]
    monkeypatch.setattr(answer_module, "build_citations", lambda result: [{"evidence_id": "EV-Q"}, {"evidence_id": "EV-A"}])

    contract = answer_module.build_answer_contract(result)

    assert contract["citation_coverage_ok"] is True
    assert contract["sections"]["source_answer"] == "\\frac{\\pi}{3}+2-\\sqrt3"
    assert contract["content_provenance"][0]["source_type"] == "textbook_problem_evidence"
    assert contract["content_provenance"][1]["source_type"] == "textbook_solution_evidence"
    assert save_eligibility(contract) == (True, "")


def test_supplementary_derivation_conflict_stops_the_answer(monkeypatch) -> None:
    monkeypatch.setattr(answer_module, "build_citations", lambda result: [{"evidence_id": "EV-Q"}, {"evidence_id": "EV-A"}])
    result = _result(answer_mode="accepted_evidence")
    result["answer_grounding"] = {"required": True, "status": "exact_answer", "can_conclude": True, "problem": {"evidence_ids": ["EV-Q"]}, "solution": {"evidence_ids": ["EV-A"], "content": "原书答案"}, "failure_reason": "", "next_action": ""}
    result["answer_consistency"] = {"status": "conflict", "reason": "补充推导得到另一结果。"}
    result["supplementary_content"] = [{"explanation": "冲突推导"}]

    contract = answer_module.build_answer_contract(result)

    assert contract["answer_grounding"]["status"] == "answer_ambiguous"
    assert contract["answer_grounding"]["can_conclude"] is False
    assert contract["sections"]["supplementary_derivation"] == []
    assert contract["citation_coverage_ok"] is False


@pytest.mark.parametrize("status", ["answer_asset_only", "answer_ambiguous", "answer_not_found", "answer_unavailable"])
def test_all_unconfirmed_source_answer_states_fail_closed(monkeypatch, status: str) -> None:
    monkeypatch.setattr(answer_module, "build_citations", lambda result: [])
    result = _result(answer_mode="accepted_evidence")
    result["answer_grounding"] = {"required": True, "status": status, "can_conclude": False, "problem": {}, "solution": {}, "failure_reason": "不能确认答案。", "next_action": "补齐答案证据。"}
    contract = answer_module.build_answer_contract(result)
    assert contract["sections"]["direct_conclusion"] == "不能确认答案。"
    assert contract["sections"]["supplementary_derivation"] == []
    assert contract["citation_coverage_ok"] is False


def test_sourced_answer_render_order_is_fixed(monkeypatch) -> None:
    citation = lambda evidence_id: {"evidence_id": evidence_id, "title": evidence_id, "page_span": "", "image_span": "", "chunk_id": "", "section_title": "", "section_view_path": ""}
    monkeypatch.setattr(answer_module, "build_citations", lambda result: [citation("EV-Q"), citation("EV-A")])
    result = _result(answer_mode="accepted_evidence")
    result["answer_grounding"] = {"required": True, "status": "exact_answer", "can_conclude": True, "problem": {"evidence_ids": ["EV-Q"], "content": "原题"}, "solution": {"evidence_ids": ["EV-A"], "content": "原书答案"}, "failure_reason": "", "next_action": ""}
    rendered = answer_module.render_text(answer_module.build_answer_contract(result))
    headings = [rendered.index(title) for title in ("## 答案定位状态", "## 原书答案", "## 过程核对", "## AI 辅助推导")]
    assert headings == sorted(headings)


def test_teaching_view_excludes_diagnostic_and_save_only_fields(monkeypatch) -> None:
    monkeypatch.setattr(answer_module, "build_citations", lambda result: [])
    contract = answer_module.build_answer_contract(_result())

    view = answer_module.build_teaching_answer_view(contract)

    assert view["view"] == "teaching"
    assert view["teaching_bundle"]["status"] == "not_applicable"
    assert "sections" not in view
    assert "query_result" not in view
    assert "references" not in view
    assert "evidence_hits" not in view
    assert "sections" in contract
    assert "query_result" in contract


def test_ask_teaching_json_saves_full_contract_before_projecting(monkeypatch, tmp_path: Path, capsys) -> None:
    result = _result()
    contract = answer_module.build_answer_contract(result)
    saved_contracts: list[dict] = []
    monkeypatch.setattr(ask_module, "query_knowledge", lambda *args, **kwargs: result)
    monkeypatch.setattr(ask_module, "build_answer_contract", lambda value: contract)
    monkeypatch.setattr(
        ask_module,
        "save_answer_contract",
        lambda **kwargs: saved_contracts.append(kwargs["contract"]) or tmp_path / "index.md",
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "ask_local_knowledge.py",
            "--subject",
            "数学",
            "--question",
            "定义是什么",
            "--save",
            "--saved-at",
            "2026-08-13",
            "--format",
            "teaching-json",
        ],
    )

    assert ask_module.main() == 0
    payload = json.loads(capsys.readouterr().out)
    assert saved_contracts == [contract]
    assert payload["saved"] is True
    assert payload["saved_at"] == "2026-08-13"
    assert payload["view"] == "teaching"
    assert "query_result" not in payload


def test_teaching_contract_rejects_conflicting_exact_gate() -> None:
    with pytest.raises(ValueError, match="contradictory exact-answer"):
        answer_module.validate_teaching_contract_invariants(
            {
                "answer_grounding": {
                    "required": True,
                    "status": "exact_answer",
                    "can_conclude": False,
                },
                "teaching_bundle": {"status": "blocked"},
            }
        )


def _page_content_request(kind: str = "page_content") -> dict:
    return {
        "original_query": "解释 P117 的 nextval 代码",
        "source_request_kind": kind,
        "page": {"number": 117, "semantics": "exact_page", "explicit_cli": True},
        "exercise_resolution": {
            "status": "not_requested",
            "source": "",
            "exercise_label": "",
            "requested_option": "",
            "candidate_labels": [],
            "matched_terms": [],
        },
    }


def _page_content_anchor(status: str = "exact_evidence") -> dict:
    return {
        "requested_page": 117,
        "match_status": status,
        "book_id": "SRC-408-0004",
        "book_title": "王道数据结构",
        "source_id": "SRC-408-0004",
        "pdf_page": 129,
        "evidence_ids": ["EV-408-000068"],
        "matched_evidence_id": "EV-408-000068",
        "match_basis": "formal_page_locator_index",
    }


def _reviewed_page_content_evidence(**overrides: object) -> dict:
    result = {
        "evidence_id": "EV-408-000068",
        "source_id": "SRC-408-0004",
        "book_title": "王道数据结构",
        "printed_page": 117,
        "pdf_page": 129,
        "content": "if (T.ch[i]!=T.ch[j]) nextval[i]=j; else nextval[i]=nextval[j];",
        "source_grounded": True,
        "verification_status": "reviewed",
        "review_status": "accepted",
    }
    result.update(overrides)
    return result


def test_exact_page_content_bundle_keeps_reviewed_source_and_both_page_numbers() -> None:
    bundle = query_module.build_page_content_bundle(
        request_resolution=_page_content_request(),
        page_anchor=_page_content_anchor(),
        evidences=[_reviewed_page_content_evidence()],
    )

    assert bundle["status"] == "exact"
    assert bundle["source_id"] == "SRC-408-0004"
    assert bundle["evidence_ids"] == ["EV-408-000068"]
    assert bundle["printed_pages"] == [117]
    assert bundle["pdf_pages"] == [129]
    assert "nextval[i]" in bundle["content"]


@pytest.mark.parametrize(
    ("page_status", "bundle_status"),
    [
        ("exact_asset", "asset_only"),
        ("ambiguous", "blocked"),
        ("unmapped", "blocked"),
        ("not_found", "blocked"),
        ("unavailable", "blocked"),
    ],
)
def test_unconfirmed_page_content_states_never_expose_text(page_status: str, bundle_status: str) -> None:
    bundle = query_module.build_page_content_bundle(
        request_resolution=_page_content_request(),
        page_anchor=_page_content_anchor(page_status),
        evidences=[_reviewed_page_content_evidence()],
    )

    assert bundle["status"] == bundle_status
    assert bundle["content"] == ""
    assert bundle["failure_reason"]
    assert bundle["next_action"]


@pytest.mark.parametrize(
    "overrides",
    [
        {"review_status": "pending", "verification_status": "candidate"},
        {"source_grounded": False},
        {"source_id": "SRC-OTHER"},
        {"printed_page": 118},
        {"pdf_page": 130},
    ],
)
def test_page_content_bundle_rejects_unreviewed_or_mismatched_evidence(overrides: dict) -> None:
    bundle = query_module.build_page_content_bundle(
        request_resolution=_page_content_request(),
        page_anchor=_page_content_anchor(),
        evidences=[_reviewed_page_content_evidence(**overrides)],
    )

    assert bundle["status"] == "blocked"
    assert bundle["content"] == ""


def test_unconfirmed_page_crosscheck_blocks_reviewed_page_content() -> None:
    bundle = query_module.build_page_content_bundle(
        request_resolution=_page_content_request(),
        page_anchor=_page_content_anchor(),
        evidences=[_reviewed_page_content_evidence()],
        page_crosscheck={"required": True, "status": "conflict", "reason": "页码线索与正式小节标题锚点冲突。"},
    )

    assert bundle["status"] == "blocked"
    assert bundle["content"] == ""
    assert "冲突" in bundle["failure_reason"]


def test_teaching_v2_exposes_compact_page_anchor_and_reviewed_page_text(monkeypatch) -> None:
    evidence = _reviewed_page_content_evidence()
    monkeypatch.setattr(answer_module, "build_citations", lambda result: [{"evidence_id": "EV-408-000068"}])
    result = _result(answer_mode="accepted_evidence", page_anchor=_page_content_anchor())
    result.update(
        subject="408",
        chapter="第4章 串",
        query="解释 P117 的 nextval 代码",
        fallback_note="",
        book_resolution={"status": "exact", "source": "explicit", "book_title": "王道数据结构"},
        request_resolution=_page_content_request(),
        page_crosscheck={"required": False, "status": "not_requested"},
        page_verification={"page_location_status": "exact_evidence", "textbook_explanation_allowed": True},
        evidence_hits=[evidence],
        runtime_context={},
    )

    view = answer_module.build_teaching_answer_view(answer_module.build_answer_contract(result))

    assert view["teaching_view_version"] == "m6.teaching.v2"
    assert view["page_anchor"]["printed_page"] == 117
    assert view["page_anchor"]["pdf_page"] == 129
    assert view["page_content_bundle"]["status"] == "exact"
    assert "nextval[i]" in view["page_content_bundle"]["content"]
    assert view["answer_grounding"]["status"] == "not_applicable"
    assert view["teaching_bundle"]["status"] == "not_applicable"
