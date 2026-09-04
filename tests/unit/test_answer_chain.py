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
import save_local_answer as save_module
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

    assert contract["answer_contract_version"] == "m6.answer.v4"
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


def _exact_calculus_result(query: str, *, book_title: str = "考研数学高等数学辅导讲义 基础篇") -> dict:
    return {
        "subject": "数学",
        "book_title": book_title,
        "book_resolution": {"status": "explicit", "source": "explicit", "book_title": book_title},
        "query": query,
        "request_resolution": {"source_request_kind": "exercise"},
        "answer_grounding": {
            "required": True,
            "status": "exact_answer",
            "can_conclude": True,
            "problem": {"evidence_ids": ["EV-Q"]},
            "solution": {"evidence_ids": ["EV-A"], "content": "原书答案"},
            "failure_reason": "",
            "next_action": "",
        },
    }


def _concept_evidence(status: str, book_title: str, *, content: str = "") -> dict:
    return {
        "status": status,
        "book_title": book_title,
        "source_id": "SRC-1" if status == "exact" else "",
        "evidence_ids": ["EV-C"] if status == "exact" else [],
        "printed_pages": [42] if status == "exact" else [],
        "pdf_pages": [48] if status == "exact" else [],
        "content": content if status == "exact" else "",
        "failure_reason": "" if status == "exact" else "未命中独立正文。",
    }


def test_explicit_concept_extraction_does_not_expand_implicit_dependencies() -> None:
    assert query_module.extract_explicit_concepts("讲一下高数63页例5第一问和最值定理结合的应用方式") == ["最值定理"]
    assert query_module.extract_explicit_concepts("比较罗尔定理与拉格朗日中值定理") == ["罗尔定理", "拉格朗日中值定理"]
    assert query_module.extract_explicit_concepts("讲一下高数63页例5第一问") == []


def test_exact_primary_concept_evidence_skips_li_zhengyuan(monkeypatch) -> None:
    calls: list[str] = []

    def find(**kwargs):
        calls.append(kwargs["book_title"])
        return _concept_evidence("exact", kwargs["book_title"], content="最值定理的正式正文。")

    monkeypatch.setattr(query_module, "_find_formal_concept_evidence", find)
    routes = query_module.build_concept_routes(_exact_calculus_result("P63例5与最值定理怎么结合"), topk=3)

    assert calls == ["考研数学高等数学辅导讲义 基础篇"]
    assert routes[0]["primary"]["status"] == "exact"
    assert routes[0]["supplement"]["status"] == "not_needed"
    assert routes[0]["supplement"]["attempted"] is False


def test_primary_concept_gap_can_add_exact_li_zhengyuan_supplement(monkeypatch) -> None:
    calls: list[tuple[str, set[str]]] = []

    def find(**kwargs):
        calls.append((kwargs["book_title"], kwargs["excluded_evidence_ids"]))
        if kwargs["book_title"] == query_module.SUPPLEMENTAL_CALCULUS_BOOK_TITLE:
            return _concept_evidence("exact", kwargs["book_title"], content="李正元中的最值定理正文。")
        return _concept_evidence("not_found", kwargs["book_title"])

    monkeypatch.setattr(query_module, "_find_formal_concept_evidence", find)
    routes = query_module.build_concept_routes(_exact_calculus_result("P63例5与最值定理怎么结合"), topk=3)

    assert calls[0][1] == {"EV-Q", "EV-A"}
    assert calls[1][0] == "李正元数一"
    assert routes[0]["supplement"]["status"] == "exact"
    assert routes[0]["supplement"]["attempted"] is True
    assert routes[0]["supplement"]["trigger"] == "primary_concept_evidence_not_found"


@pytest.mark.parametrize(
    "result",
    [
        {**_exact_calculus_result("P63例5与最值定理怎么结合"), "answer_grounding": {"status": "answer_not_found", "can_conclude": False}},
        _exact_calculus_result("例题与最值定理怎么结合", book_title="李正元数一"),
    ],
)
def test_concept_supplement_never_bypasses_primary_answer_or_explicit_li_route(monkeypatch, result: dict) -> None:
    monkeypatch.setattr(query_module, "_find_formal_concept_evidence", lambda **kwargs: pytest.fail("concept lookup must not run"))
    assert query_module.build_concept_routes(result, topk=3) == []


def test_render_and_teaching_view_keep_cross_book_supplement_separate(monkeypatch) -> None:
    citation = lambda evidence_id: {"evidence_id": evidence_id, "title": evidence_id, "page_span": "", "image_span": "", "chunk_id": "", "section_title": "", "section_view_path": ""}
    monkeypatch.setattr(answer_module, "build_citations", lambda result: [citation("EV-Q"), citation("EV-A")])
    result = _result(answer_mode="accepted_evidence")
    result.update(_exact_calculus_result("P63例5与最值定理怎么结合"))
    result.update(
        teaching_bundle={"status": "exact", "problem_text": "原题", "source_answer_text": "原书答案", "requested_option": "", "exercise_label": "例5", "citations": {"problem_evidence_ids": ["EV-Q"], "solution_evidence_ids": ["EV-A"]}, "failure_reason": ""},
        page_content_bundle={"status": "not_applicable", "source_id": "", "book_title": "", "evidence_ids": [], "printed_pages": [], "pdf_pages": [], "content": "", "failure_reason": "", "next_action": ""},
        page_verification={"textbook_explanation_allowed": True},
        concept_routes=[
            {
                "concept": "最值定理",
                "requested_by": "user_explicit",
                "primary": _concept_evidence("not_found", "考研数学高等数学辅导讲义 基础篇"),
                "supplement": {**_concept_evidence("exact", "李正元数一", content="李正元中的最值定理正文。"), "attempted": True, "trigger": "primary_concept_evidence_not_found"},
            }
        ],
    )

    contract = answer_module.build_answer_contract(result)
    rendered = answer_module.render_text(contract)
    view = answer_module.build_teaching_answer_view(contract)

    headings = [rendered.index(title) for title in ("## 原书答案", "## 主书定理依据", "## 李正元补充（非当前主线）", "## 过程核对")]
    assert headings == sorted(headings)
    assert "仅作跨书补充，不改变本题原书答案或当前学习主线" in rendered
    assert view["teaching_view_version"] == "m6.teaching.v3"
    assert view["concept_routes"][0]["supplement"]["status"] == "exact"


def test_unconfirmed_li_zhengyuan_result_is_not_rendered(monkeypatch) -> None:
    citation = lambda evidence_id: {"evidence_id": evidence_id, "title": evidence_id, "page_span": "", "image_span": "", "chunk_id": "", "section_title": "", "section_view_path": ""}
    monkeypatch.setattr(answer_module, "build_citations", lambda result: [citation("EV-Q"), citation("EV-A")])
    result = _result(answer_mode="accepted_evidence")
    result.update(_exact_calculus_result("P63例5与最值定理怎么结合"))
    result.update(
        teaching_bundle={"status": "exact", "problem_text": "原题", "source_answer_text": "原书答案", "requested_option": "", "exercise_label": "例5", "citations": {"problem_evidence_ids": ["EV-Q"], "solution_evidence_ids": ["EV-A"]}, "failure_reason": ""},
        page_content_bundle={"status": "not_applicable", "source_id": "", "book_title": "", "evidence_ids": [], "printed_pages": [], "pdf_pages": [], "content": "", "failure_reason": "", "next_action": ""},
        page_verification={"textbook_explanation_allowed": True},
        concept_routes=[
            {
                "concept": "最值定理",
                "requested_by": "user_explicit",
                "primary": _concept_evidence("not_found", "考研数学高等数学辅导讲义 基础篇"),
                "supplement": {**_concept_evidence("not_found", "李正元数一"), "attempted": True, "trigger": "primary_concept_evidence_not_found"},
            }
        ],
    )

    rendered = answer_module.render_text(answer_module.build_answer_contract(result))

    assert "## 主书定理依据" in rendered
    assert "本书未单独定位到" in rendered
    assert "## 李正元补充（非当前主线）" not in rendered


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


def test_teaching_contract_rejects_blocked_page_content_with_explanation_permission() -> None:
    with pytest.raises(ValueError, match="page-content explanation permission"):
        answer_module.validate_teaching_contract_invariants(
            {
                "answer_grounding": {"required": False, "status": "not_applicable", "can_conclude": True},
                "teaching_bundle": {"status": "not_applicable", "problem_text": "", "source_answer_text": ""},
                "request_resolution": {"source_request_kind": "page_content"},
                "page_anchor": {"match_status": "exact_evidence"},
                "page_content_bundle": {
                    "status": "blocked",
                    "content": "",
                    "failure_reason": "正文未通过审核。",
                    "next_action": "请先审核正文。",
                },
                "page_verification": {"textbook_explanation_allowed": True},
            }
        )


def test_teaching_contract_rejects_blocked_exercise_with_explanation_permission() -> None:
    with pytest.raises(ValueError, match="exercise explanation permission"):
        answer_module.validate_teaching_contract_invariants(
            {
                "answer_grounding": {
                    "required": True,
                    "status": "answer_not_found",
                    "can_conclude": False,
                    "failure_reason": "原书答案未确认。",
                    "next_action": "请先定位原书答案。",
                },
                "teaching_bundle": {
                    "status": "blocked",
                    "problem_text": "",
                    "source_answer_text": "",
                    "citations": {},
                },
                "request_resolution": {"source_request_kind": "exercise"},
                "page_content_bundle": {"status": "not_applicable", "content": ""},
                "page_verification": {"textbook_explanation_allowed": True},
            }
        )


def test_teaching_contract_rejects_generic_textbook_explanation_permission() -> None:
    with pytest.raises(ValueError, match="generic requests cannot permit"):
        answer_module.validate_teaching_contract_invariants(
            {
                "answer_grounding": {"required": False, "status": "not_applicable", "can_conclude": True},
                "teaching_bundle": {"status": "not_applicable", "problem_text": "", "source_answer_text": ""},
                "request_resolution": {"source_request_kind": "generic"},
                "page_content_bundle": {"status": "not_applicable", "content": ""},
                "page_verification": {"textbook_explanation_allowed": True},
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


def test_page_content_bundle_accepts_formal_source_grounded_evidence_without_review_overlay() -> None:
    evidence = _reviewed_page_content_evidence(verification_status="source_grounded")
    evidence.pop("review_status")

    bundle = query_module.build_page_content_bundle(
        request_resolution=_page_content_request(),
        page_anchor=_page_content_anchor(),
        evidences=[evidence],
    )

    assert bundle["status"] == "exact"
    assert bundle["evidence_ids"] == ["EV-408-000068"]


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
        {"review_status": "pending", "verification_status": "source_grounded"},
        {"review_status": "rejected", "verification_status": "source_grounded"},
        {"review_status": "", "verification_status": "needs_review"},
        {"mapping_status": "stale"},
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


def test_teaching_v3_exposes_compact_page_anchor_and_reviewed_page_text(monkeypatch) -> None:
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

    assert view["teaching_view_version"] == "m6.teaching.v3"
    assert view["page_anchor"]["printed_page"] == 117
    assert view["page_anchor"]["pdf_page"] == 129
    assert view["page_content_bundle"]["status"] == "exact"
    assert "nextval[i]" in view["page_content_bundle"]["content"]
    assert view["answer_grounding"]["status"] == "not_applicable"
    assert view["teaching_bundle"]["status"] == "not_applicable"


def test_generic_answer_bundle_requires_same_book_relevance_and_dependency_citations(monkeypatch) -> None:
    result = _result(answer_mode="accepted_evidence")
    result.update(
        book_title="王道数据结构",
        book_resolution={"status": "explicit", "source": "explicit", "book_title": "王道数据结构"},
        query="栈和队列有什么区别？",
        request_resolution={"source_request_kind": "generic"},
        generic_gate={
            "status": "exact",
            "book_title": "王道数据结构",
            "topic_terms": ["栈", "队列"],
            "matched_terms": ["栈", "队列"],
            "dependency_evidence_ids": ["EV-STACK-QUEUE"],
            "candidate_count": 1,
            "relevance_ok": True,
            "same_book_ok": True,
            "structured_answer_ok": True,
            "failure_reason": "",
            "next_action": "",
        },
        claim_hits=[{"claim_id": "CL-1", "claim_type": "comparison", "text": "栈和队列的操作端不同。", "evidence_ids": ["EV-STACK-QUEUE"]}],
    )
    monkeypatch.setattr(
        answer_module,
        "build_citations",
        lambda result: [{"evidence_id": "EV-STACK-QUEUE", "title": "栈和队列", "book_title": "王道数据结构"}],
    )

    contract = answer_module.build_answer_contract(result)

    assert contract["generic_answer_bundle"]["status"] == "exact"
    assert contract["generic_answer_bundle"]["evidence_ids"] == ["EV-STACK-QUEUE"]
    assert contract["citation_coverage_ok"] is True
    assert save_eligibility(contract) == (True, "")


def test_blocked_generic_answer_is_content_free_and_unsaveable(monkeypatch) -> None:
    result = _result(answer_mode="unconfirmed")
    result.update(
        book_title="不存在的教材",
        book_resolution={"status": "explicit", "source": "explicit", "book_title": "不存在的教材"},
        query="香蕉和苹果有什么区别？",
        request_resolution={"source_request_kind": "generic"},
        fallback_note="当前教材范围内没有找到与问题主题匹配的可发布证据。",
        generic_gate={
            "status": "blocked",
            "book_title": "不存在的教材",
            "topic_terms": ["香蕉", "苹果"],
            "matched_terms": [],
            "dependency_evidence_ids": [],
            "candidate_count": 0,
            "relevance_ok": False,
            "same_book_ok": False,
            "structured_answer_ok": False,
            "failure_reason": "当前教材范围内没有找到与问题主题匹配的可发布证据。",
            "next_action": "请补充当前教材的正式证据。",
        },
    )
    monkeypatch.setattr(answer_module, "build_citations", lambda result: [])

    contract = answer_module.build_answer_contract(result)

    bundle = contract["generic_answer_bundle"]
    assert bundle["status"] == "blocked"
    assert bundle["conclusion"] == ""
    assert bundle["explanation"] == ""
    assert bundle["citations"] == []
    allowed, reason = save_eligibility(contract)
    assert not allowed
    assert "门禁" in reason


def _transaction_contract(question: str = "事务测试") -> dict:
    result = _result(answer_mode="accepted_evidence")
    result.update(
        query=question,
        references=[
            {
                "evidence_id": "EV-FIXTURE",
                "title": "fixture evidence",
                "page_span": "P1",
                "image_span": "1",
                "chunk_id": "CHUNK-FIXTURE",
            }
        ],
        evidence_hits=[],
    )
    return {
        "answer_contract_version": "test.answer.v1",
        "answer_mode": "accepted_evidence",
        "citation_coverage_ok": True,
        "evidence_assessment": {
            "level": "structured_evidence",
            "can_confirm": "fixture confirms",
            "cannot_confirm": "",
            "next_action": "",
        },
        "query_result": result,
        "intent": "define",
        "syllabus_route": [],
        "references": result["references"],
        "content_provenance": [{"source_label": "fixture evidence"}],
    }


def _transaction_fixture(tmp_path: Path) -> tuple[Path, Path, tuple[Path, ...]]:
    vault_root = tmp_path / "vault"
    context_path = vault_root / "fixture" / "00_批次上下文.json"
    context_path.parent.mkdir(parents=True)
    context_path.write_text(
        json.dumps({"subject": "数学", "chapter_title": "第三章"}, ensure_ascii=False),
        encoding="utf-8",
    )
    learner_root = tmp_path / "kb" / "learner"
    feedback_paths = tuple(learner_root / name for name in save_module.LEARNER_FEEDBACK_FILENAMES)
    return vault_root, context_path, feedback_paths


def _transaction_paths(vault_root: Path, question: str, feedback_paths: tuple[Path, ...]) -> tuple[Path, ...]:
    chapter_dir = vault_root / "10_数学" / "00_课程入口" / "10_问答沉淀" / "第三章"
    note_path = chapter_dir / f"2026-09-05_{question}.md"
    return save_module.transaction_paths(
        note_path,
        chapter_dir / "00_本章问答入口.md",
        chapter_dir.parent / "00_知识问答入口.md",
        *feedback_paths,
    )


def _stub_feedback_scripts(monkeypatch, feedback_paths: tuple[Path, ...], *, fail_second: bool = False) -> list[str]:
    calls: list[str] = []

    def fake_run_script(name: str, *args: str) -> None:
        calls.append(name)
        if name == "apply_saved_qa_feedback.py":
            for path in feedback_paths:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(f"new:{path.name}".encode("utf-8"))
        elif name == "review_refinement_candidates.py":
            refinement_path = next(path for path in feedback_paths if path.name == "refinement_queue.json")
            refinement_path.write_bytes(b"new:second-feedback")
            if fail_second:
                raise RuntimeError("第二个反馈步骤失败")

    monkeypatch.setattr(save_module, "learner_feedback_paths", lambda: feedback_paths)
    monkeypatch.setattr(save_module, "run_script", fake_run_script)
    return calls


def test_save_transaction_success_keeps_note_indexes_and_feedback(tmp_path: Path, monkeypatch) -> None:
    vault_root, _context_path, feedback_paths = _transaction_fixture(tmp_path)
    calls = _stub_feedback_scripts(monkeypatch, feedback_paths)
    contract = _transaction_contract()

    subject_index = save_answer_contract(
        contract=contract,
        vault_root=vault_root,
        subject="数学",
        chapter="第三章",
        question="事务测试",
        saved_at="2026-09-05",
    )

    paths = _transaction_paths(vault_root, "事务测试", feedback_paths)
    assert subject_index == paths[2]
    assert calls == ["apply_saved_qa_feedback.py", "review_refinement_candidates.py"]
    assert all(path.is_file() for path in paths)
    assert paths[0].read_text(encoding="utf-8").startswith("# 事务测试")
    assert paths[1].is_file()
    assert paths[2].is_file()
    assert paths[3].read_bytes() == b"new:learner_events.jsonl"
    assert paths[-2].read_bytes() == b"new:second-feedback"


def test_save_transaction_second_feedback_failure_removes_new_files(tmp_path: Path, monkeypatch) -> None:
    vault_root, _context_path, feedback_paths = _transaction_fixture(tmp_path)
    _stub_feedback_scripts(monkeypatch, feedback_paths, fail_second=True)

    with pytest.raises(RuntimeError, match="第二个反馈步骤失败"):
        save_answer_contract(
            contract=_transaction_contract(),
            vault_root=vault_root,
            subject="数学",
            chapter="第三章",
            question="事务测试",
            saved_at="2026-09-05",
        )

    paths = _transaction_paths(vault_root, "事务测试", feedback_paths)
    assert all(not path.exists() for path in paths)
    assert not (vault_root / "10_数学").exists()
    assert not (tmp_path / "kb").exists()


def test_save_transaction_failure_restores_overwritten_note_indexes_and_feedback(tmp_path: Path, monkeypatch) -> None:
    vault_root, _context_path, feedback_paths = _transaction_fixture(tmp_path)
    paths = _transaction_paths(vault_root, "事务测试", feedback_paths)
    for path in paths:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(f"old:{path.name}".encode("utf-8"))
    _stub_feedback_scripts(monkeypatch, feedback_paths, fail_second=True)

    with pytest.raises(RuntimeError, match="第二个反馈步骤失败"):
        save_answer_contract(
            contract=_transaction_contract(),
            vault_root=vault_root,
            subject="数学",
            chapter="第三章",
            question="事务测试",
            saved_at="2026-09-05",
        )

    assert [path.read_bytes() for path in paths] == [f"old:{path.name}".encode("utf-8") for path in paths]


def test_rejected_save_does_not_enumerate_or_write_transaction_files(tmp_path: Path, monkeypatch) -> None:
    contract = {
        "answer_mode": "page_asset",
        "citation_coverage_ok": False,
        "evidence_assessment": {"level": "page_asset_only"},
        "query_result": _result(answer_mode="page_asset", page_anchor={"match_status": "exact_asset"}),
    }
    monkeypatch.setattr(save_module, "learner_feedback_paths", lambda: pytest.fail("拒绝保存不应枚举反馈路径"))
    monkeypatch.setattr(save_module, "run_script", lambda *args: pytest.fail("拒绝保存不应执行反馈脚本"))

    with pytest.raises(ValueError, match="没有可保存的结构化证据"):
        save_answer_contract(
            contract=contract,
            vault_root=tmp_path,
            subject="数学",
            chapter="第三章",
            question="书上有吗",
            saved_at="2026-09-05",
        )

    assert list(tmp_path.iterdir()) == []
