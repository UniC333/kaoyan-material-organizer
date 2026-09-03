from __future__ import annotations

import sys
from pathlib import Path

import pytest


SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import query_local_knowledge as query_module


def test_compact_p_page_anchor_next_to_chinese_text() -> None:
    assert query_module.parse_page_anchor("高数p64例3.8第一问")["requested_page"] == 64


def test_missing_exercise_number_is_inferred_from_exact_page_problem_text(monkeypatch, tmp_path: Path) -> None:
    original_query = "讲下数据结构94页C选项，我的问题是读取*后，读到C，C会进入操作数栈吗？这个问题直接干扰了我在B和C选项的判断"
    problem = {"evidence_id": "EV-408-000050", "content": "04. 利用栈求表达式的值时，设立运算数栈 OPEN。"}
    solution = {"evidence_id": "EV-408-000053", "content": "04. B。选项 A、C、D 的栈深依次为 4、3、3。"}
    monkeypatch.setattr(query_module, "resolve_book_route", lambda **kwargs: {})
    monkeypatch.setattr(query_module, "resolve_exercise_route", lambda **kwargs: {"match_status": "not_requested", "request": {}})
    monkeypatch.setattr(query_module, "_resolve_query_hits", lambda *args, **kwargs: ([], [], [], [], True))
    monkeypatch.setattr(
        query_module,
        "apply_hard_page_route",
        lambda **kwargs: (
            {
                "requested_page": 94,
                "match_status": "exact_evidence",
                "exercise_match_status": "not_requested",
                "source_id": "SRC-408-0004",
                "book_title": "王道数据结构",
                "pdf_page": 106,
                "locator_available": True,
            },
            [],
            [],
            [problem],
        ),
    )
    monkeypatch.setattr(
        query_module,
        "list_exact_relations_for_question_page",
        lambda **kwargs: {
            "status": "exact",
            "relations": [
                {"exercise_label": "02", "question_content": "表达式 a*(b+c)-d 的后缀表达式是（ ）。"},
                {"exercise_label": "04", "question_content": problem["content"]},
                {"exercise_label": "05", "question_content": "执行下列递归语句段后，i 的值为（ ）。"},
            ],
        },
    )
    monkeypatch.setattr(
        query_module,
        "apply_exercise_relation",
        lambda locator, evidences: (
            {
                "status": "exact_answer_evidence",
                "exercise_label": "04",
                "question_evidence_ids": ["EV-408-000050"],
                "answer_evidence_ids": ["EV-408-000053"],
                "question_printed_pages": [94],
                "answer_printed_pages": [97],
                "question_pdf_pages": [106],
                "answer_pdf_pages": [109],
                "question_content": problem["content"],
                "answer_content": solution["content"],
            },
            [problem, solution],
        ),
    )
    monkeypatch.setattr(query_module, "learner_compare_candidates", lambda *args, **kwargs: [])
    monkeypatch.setattr(query_module, "learner_snapshot", lambda *args, **kwargs: {})
    monkeypatch.setattr(query_module, "load_events", lambda: [])
    monkeypatch.setattr(query_module, "runtime_context_payload", lambda **kwargs: {})

    result = query_module.query_knowledge(tmp_path, "408", None, original_query, 3, book_title="数据结构")

    assert result["request_resolution"]["exercise_label"] == "04"
    assert result["request_resolution"]["requested_option"] == "C"
    assert result["request_resolution"]["exercise_resolution"]["status"] == "inferred_unique"
    assert result["answer_grounding"]["status"] == "exact_answer"
    assert result["answer_grounding"]["can_conclude"] is True
    assert result["answer_grounding"]["problem"]["evidence_ids"] == ["EV-408-000050"]
    assert result["answer_grounding"]["solution"]["evidence_ids"] == ["EV-408-000053"]
    assert result["teaching_bundle"]["requested_option"] == "C"
    assert result["teaching_bundle"]["exercise_label"] == "04"


def test_generic_exact_page_explanation_does_not_force_exercise_inference(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(query_module, "resolve_book_route", lambda **kwargs: {})
    monkeypatch.setattr(query_module, "resolve_exercise_route", lambda **kwargs: {"match_status": "not_requested", "request": {}})
    monkeypatch.setattr(query_module, "_resolve_query_hits", lambda *args, **kwargs: ([], [], [], [], True))
    monkeypatch.setattr(
        query_module,
        "apply_hard_page_route",
        lambda **kwargs: (
            {"requested_page": 94, "match_status": "exact_evidence", "exercise_match_status": "not_requested", "source_id": "SRC", "pdf_page": 106},
            [],
            [],
            [{"evidence_id": "EV-PAGE", "content": "整页教材正文"}],
        ),
    )
    monkeypatch.setattr(query_module, "learner_compare_candidates", lambda *args, **kwargs: [])
    monkeypatch.setattr(query_module, "learner_snapshot", lambda *args, **kwargs: {})
    monkeypatch.setattr(query_module, "load_events", lambda: [])
    monkeypatch.setattr(query_module, "runtime_context_payload", lambda **kwargs: {})
    result = query_module.query_knowledge(tmp_path, "408", None, "讲一下数据结构第94页的内容", 3, book_title="数据结构")
    assert result["request_resolution"]["exercise_resolution"]["status"] == "not_requested"
    assert result["exercise_anchor"]["status"] == "not_requested"
    assert result["page_verification"]["exercise_verification_status"] == "not_requested"


def test_source_photo_without_book_identity_still_requires_answer_grounding() -> None:
    grounding = query_module.build_answer_grounding(query="看这张题目照片，帮我检查这道题的过程", book_title=None, page_anchor={}, exercise_anchor={}, evidences=[])
    assert grounding["required"] is True
    assert grounding["status"] == "answer_not_found"
    assert grounding["can_conclude"] is False


def test_explicitly_self_authored_problem_skips_source_answer_gate() -> None:
    grounding = query_module.build_answer_grounding(query="这是我自拟的一道题，帮我推导", book_title=None, page_anchor={}, exercise_anchor={}, evidences=[])
    assert grounding["status"] == "not_applicable"
    assert grounding["can_conclude"] is True

    request_resolution = query_module.resolve_request(
        query="这是我自拟的 P94 题，帮我推导",
        book_title="王道数据结构",
        chapter=None,
        printed_page=94,
        exercise_label=None,
    )
    verification = query_module.build_page_verification_summary(
        {"requested_page": 94, "match_status": "exact_evidence", "exercise_match_status": "not_requested"},
        "accepted_evidence",
        request_resolution=request_resolution,
        answer_grounding=grounding,
        teaching_bundle={"status": "not_applicable"},
        page_content_bundle={"status": "not_applicable"},
    )
    assert request_resolution["source_request_kind"] == "generic"
    assert verification["textbook_explanation_allowed"] is False


def test_exact_exercise_anchor_builds_source_answer_grounding() -> None:
    grounding = query_module.build_answer_grounding(
        query="例3.8 第一问，检查我的过程",
        book_title="李正元数一",
        page_anchor={"requested_page": 64, "book_title": "李正元数一", "match_status": "exact_evidence"},
        exercise_anchor={"status": "exact_answer_evidence", "exercise_label": "例3.8", "question_evidence_ids": ["EV-Q"], "answer_evidence_ids": ["EV-A"], "question_printed_pages": [64], "answer_printed_pages": [65]},
        evidences=[{"evidence_id": "EV-Q", "content": "原题"}, {"evidence_id": "EV-A", "content": "原书答案"}],
    )
    assert grounding["status"] == "exact_answer"
    assert grounding["can_conclude"] is True
    assert grounding["problem"]["evidence_ids"] == ["EV-Q"]
    assert grounding["solution"]["evidence_ids"] == ["EV-A"]
    assert grounding["solution"]["content"] == "原书答案"


def test_section_start_page_conflict_blocks_exact_exercise_answer() -> None:
    grounding = query_module.build_answer_grounding(
        query="95页起的3.3.6试题第4题C项",
        book_title="王道数据结构",
        page_anchor={"requested_page": 95, "book_title": "王道数据结构", "match_status": "exact_evidence"},
        exercise_anchor={"status": "exact_answer_evidence", "exercise_label": "04", "question_evidence_ids": ["EV-Q"], "answer_evidence_ids": ["EV-A"]},
        evidences=[{"evidence_id": "EV-Q", "content": "原题"}, {"evidence_id": "EV-A", "content": "原书答案"}],
        page_crosscheck={"required": True, "status": "conflict", "reason": "页码线索与正式小节标题锚点冲突。"},
    )
    assert grounding["status"] == "answer_ambiguous"
    assert grounding["can_conclude"] is False
    assert "冲突" in grounding["failure_reason"]


@pytest.mark.parametrize(
    ("page_number", "semantics", "resolved_pdf_page", "crosscheck_status", "grounding_status", "allowed"),
    [
        (None, "not_requested", 0, "not_requested", "exact_answer", True),
        (94, "section_start", 106, "confirmed", "exact_answer", True),
        (94, "approximate_page", 108, "confirmed", "exact_answer", True),
        (95, "section_start", 107, "conflict", "answer_ambiguous", False),
    ],
)
def test_scoped_exercise_relation_keeps_all_teaching_gates_consistent(
    monkeypatch,
    tmp_path: Path,
    page_number: int | None,
    semantics: str,
    resolved_pdf_page: int,
    crosscheck_status: str,
    grounding_status: str,
    allowed: bool,
) -> None:
    request_resolution = {
        "source_request_kind": "exercise",
        "page": {"number": page_number, "semantics": semantics, "explicit_cli": False},
        "exercise_label": "04",
        "exercise_category": "single-choice",
        "requested_option": "C",
        "section_root": "3.3",
        "section_anchor": {"status": "exact", "section_root": "3.3", "pdf_page": 106},
    }
    exercise_anchor = {
        "status": "exact_answer_evidence",
        "exercise_label": "04",
        "question_evidence_ids": ["EV-Q"],
        "answer_evidence_ids": ["EV-A"],
        "question_pdf_pages": [106],
        "answer_pdf_pages": [132],
        "question_content": "第4题原题",
        "answer_content": "04. C 原书解析",
    }
    evidences = [
        {"evidence_id": "EV-Q", "content": "第4题原题"},
        {"evidence_id": "EV-A", "content": "04. C 原书解析"},
    ]
    monkeypatch.setattr(query_module, "resolve_book_route", lambda **kwargs: {})
    monkeypatch.setattr(query_module, "resolve_request", lambda **kwargs: request_resolution)
    monkeypatch.setattr(query_module, "apply_scoped_exercise_relation", lambda **kwargs: (dict(exercise_anchor), evidences))
    monkeypatch.setattr(
        query_module,
        "apply_hard_page_route",
        lambda **kwargs: (
            {
                "requested_page": page_number,
                "match_status": "exact_evidence",
                "pdf_page": resolved_pdf_page,
                "exercise_match_status": "unverified",
            },
            [],
            [],
            [],
        ),
    )
    monkeypatch.setattr(query_module, "load_events", lambda: [])
    monkeypatch.setattr(query_module, "runtime_context_payload", lambda **kwargs: {})

    result = query_module.query_knowledge(
        tmp_path,
        "408",
        None,
        "王道数据结构 3.3.6 试题第4题 C项",
        3,
        book_title="王道数据结构",
    )

    assert result["page_anchor"]["exercise_match_status"] == "matched"
    assert result["page_crosscheck"]["status"] == crosscheck_status
    assert result["answer_grounding"]["status"] == grounding_status
    assert result["answer_grounding"]["can_conclude"] is allowed
    assert result["page_verification"]["exercise_verification_status"] == "matched"
    assert result["page_verification"]["textbook_explanation_allowed"] is allowed
    assert result["teaching_bundle"]["status"] == ("exact" if allowed else "blocked")


def test_stale_exercise_index_propagates_to_grounding_and_runtime(monkeypatch, tmp_path: Path) -> None:
    request_resolution = {
        "page": {"number": None, "semantics": "none", "explicit_cli": False},
        "exercise_label": "04",
        "exercise_category": "single-choice",
        "requested_option": "C",
        "section_root": "3.3",
        "section_anchor": {"status": "exact", "section_root": "3.3", "pdf_page": 106},
    }
    availability = {
        "available": False,
        "path": "C:/kb/indexes/exercise_locator_index.json",
        "reason": "exercise_locator_index_stale",
        "detail": "inputs changed",
    }
    monkeypatch.setattr(query_module, "resolve_book_route", lambda **kwargs: {})
    monkeypatch.setattr(query_module, "resolve_request", lambda **kwargs: request_resolution)
    monkeypatch.setattr(
        query_module,
        "apply_scoped_exercise_relation",
        lambda **kwargs: (
            {
                "status": "unavailable",
                "exercise_label": "04",
                "reason": "exercise_locator_index_stale",
                "detail": "inputs changed",
                "_availability": availability,
            },
            [],
        ),
    )
    monkeypatch.setattr(query_module, "load_events", lambda: [])
    monkeypatch.setattr(query_module, "runtime_context_payload", lambda **kwargs: {})

    result = query_module.query_knowledge(
        tmp_path,
        "408",
        None,
        "王道数据结构 3.3.6 试题第4题 C项",
        3,
        book_title="王道数据结构",
    )

    assert result["answer_grounding"]["status"] == "answer_unavailable"
    assert result["answer_grounding"]["can_conclude"] is False
    assert result["teaching_bundle"]["status"] == "blocked"
    assert result["runtime_context"]["exercise_locator_index_available"] is False
    assert result["runtime_context"]["exercise_locator_unavailable_reason"] == "exercise_locator_index_stale"


def test_exact_asset_problem_reports_answer_asset_only() -> None:
    grounding = query_module.build_answer_grounding(query="P64 例3.8 怎么做", book_title="李正元数一", page_anchor={"requested_page": 64, "requested_exercise_label": "例3.8", "match_status": "exact_asset"}, exercise_anchor={}, evidences=[])
    assert grounding["status"] == "answer_asset_only"
    assert grounding["can_conclude"] is False


def test_ambiguous_exercise_relation_reports_answer_ambiguous() -> None:
    grounding = query_module.build_answer_grounding(query="P64 例3.8 怎么做", book_title="李正元数一", page_anchor={"requested_page": 64, "requested_exercise_label": "例3.8", "match_status": "exact_evidence"}, exercise_anchor={"status": "ambiguous", "exercise_label": "例3.8"}, evidences=[])
    assert grounding["status"] == "answer_ambiguous"
    assert grounding["can_conclude"] is False


def test_missing_exercise_number_requests_only_the_number() -> None:
    grounding = query_module.build_answer_grounding(
        query="讲第94页C选项",
        book_title="王道数据结构",
        page_anchor={"requested_page": 94, "match_status": "exact_evidence"},
        exercise_anchor={"status": "ambiguous", "reason": "exercise-label-missing", "candidate_labels": ["01", "02", "04"]},
        evidences=[],
    )
    assert grounding["status"] == "answer_ambiguous"
    assert grounding["can_conclude"] is False
    assert grounding["next_action"] == "目前只缺题号，请告诉我是第几题。"
    assert "01、02、04" in grounding["failure_reason"]


def test_compact_p_page_anchor_before_chinese_text() -> None:
    assert query_module.parse_page_anchor("P4第7题")["requested_page"] == 4


def test_explicit_page_clears_unrelated_semantic_hits(monkeypatch, tmp_path: Path) -> None:
    wrong_evidence = {"evidence_id": "EV-WRONG", "subject": "数学", "title": "另一页"}
    wrong_claim = {"claim_id": "CL-WRONG", "evidence_ids": ["EV-WRONG"], "claim_type": "definition", "text": "错误页"}
    wrong_retrieval = {"doc_type": "evidence", "entity_id": "EV-WRONG", "references": ["EV-WRONG"]}
    monkeypatch.setattr(
        query_module,
        "_resolve_query_hits",
        lambda *args, **kwargs: ([wrong_retrieval], [], [wrong_claim], [wrong_evidence], True),
    )
    monkeypatch.setattr(
        query_module,
        "resolve_page_locator",
        lambda **kwargs: {
            "requested_page": 49,
            "requested_position": None,
            "requested_book_title": "李正元数一",
            "requested_exercise_label": "例2.29",
            "match_status": "exact_asset",
            "exercise_match_status": "unverified",
            "book_id": "li-zhengyuan-math1-ch2",
            "book_title": "李正元数一",
            "source_id": "SRC-MATH-0002",
            "page_id": "PAGE-li-zhengyuan-math1-ch2-0020",
            "source_image_path": "P49.jpg",
            "source_image_sha256": "p49-sha",
            "match_basis": "formal_page_locator_index",
            "candidates": [],
            "matched_evidence_id": "",
            "matched_chunk_id": "",
            "snippets": [],
        },
    )
    monkeypatch.setattr(query_module, "exact_evidence_hits_for_locator", lambda *args, **kwargs: [])
    monkeypatch.setattr(query_module, "learner_compare_candidates", lambda *args, **kwargs: [])
    monkeypatch.setattr(query_module, "learner_snapshot", lambda *args, **kwargs: {})
    monkeypatch.setattr(query_module, "load_events", lambda: [])

    result = query_module.query_knowledge(
        tmp_path,
        "数学",
        None,
        "P49 例2.29 隐函数微分",
        3,
        book_title="李正元数一",
    )

    assert result["answer_mode"] == "page_asset"
    assert result["page_anchor"]["match_status"] == "exact_asset"
    assert result["page_verification"] == {
        "page_location_status": "exact_asset",
        "exercise_verification_status": "unverified",
        "page_crosscheck_status": "not_requested",
        "answer_mode": "page_asset",
        "textbook_explanation_allowed": False,
        "summary": "教材原页已定位；教材正文未确认，不能按书上原题讲解。",
    }
    assert result["retrieval_hits"] == []
    assert result["claim_hits"] == []
    assert result["evidence_hits"] == []
    assert result["fallback_hits"] == []


def test_query_without_page_keeps_normal_semantic_hits(monkeypatch, tmp_path: Path) -> None:
    evidence = {"evidence_id": "EV-OK", "subject": "数学", "title": "导数定义", "content": "导数定义"}
    monkeypatch.setattr(query_module, "_resolve_query_hits", lambda *args, **kwargs: ([], [], [], [evidence], False))
    monkeypatch.setattr(query_module, "learner_compare_candidates", lambda *args, **kwargs: [])
    monkeypatch.setattr(query_module, "learner_snapshot", lambda *args, **kwargs: {})
    monkeypatch.setattr(query_module, "load_events", lambda: [])
    result = query_module.query_knowledge(tmp_path, "数学", None, "什么是导数", 3)
    assert result["evidence_hits"] == [evidence]
    assert not result["query_path"]["hard_page_filter_applied"]


def test_recognized_series_exercise_never_falls_back_to_another_book(monkeypatch, tmp_path: Path) -> None:
    wrong_evidence = {"evidence_id": "EV-OTHER-BOOK", "subject": "数学", "title": "另一教材"}
    wrong_retrieval = {"doc_type": "evidence", "entity_id": "EV-OTHER-BOOK", "references": ["EV-OTHER-BOOK"]}
    monkeypatch.setattr(query_module, "_resolve_query_hits", lambda *args, **kwargs: ([wrong_retrieval], [], [], [wrong_evidence], True))
    monkeypatch.setattr(query_module, "resolve_book_route", lambda **kwargs: {"match_status": "exact_series", "series_id": "JIELI1800-M1", "stage": "basic"})
    monkeypatch.setattr(query_module, "resolve_exercise_route", lambda **kwargs: {"match_status": "not_found", "request": {"exercise_number": 7}})
    monkeypatch.setattr(query_module, "learner_compare_candidates", lambda *args, **kwargs: [])
    monkeypatch.setattr(query_module, "learner_snapshot", lambda *args, **kwargs: {})
    monkeypatch.setattr(query_module, "load_events", lambda: [])

    result = query_module.query_knowledge(tmp_path, "数学", None, "1800基础篇高数第一章选择题第7题怎么做", 3)

    assert result["answer_mode"] == "exercise_not_found"
    assert result["retrieval_hits"] == []
    assert result["evidence_hits"] == []
    assert result["references"] == []


def test_unavailable_page_locator_clears_hits_and_does_not_become_not_found(monkeypatch, tmp_path: Path) -> None:
    wrong_evidence = {"evidence_id": "EV-WRONG", "subject": "数学", "title": "另一页"}
    monkeypatch.setattr(
        query_module,
        "_resolve_query_hits",
        lambda *args, **kwargs: ([], [], [], [wrong_evidence], False),
    )
    monkeypatch.setattr(
        query_module,
        "resolve_page_locator",
        lambda **kwargs: {
            "requested_page": 62,
            "requested_position": None,
            "requested_book_title": "李正元数一",
            "requested_exercise_label": "",
            "match_status": "unavailable",
            "exercise_match_status": "not_requested",
            "locator_available": False,
            "locator_index_path": "C:/kb/indexes/page_locator_index.json",
            "unavailable_reason": "page_locator_index_missing",
            "unavailable_detail": "missing",
            "evidence_ids": [],
            "candidates": [],
            "matched_evidence_id": "",
            "matched_chunk_id": "",
            "snippets": [],
        },
    )
    monkeypatch.setattr(query_module, "learner_compare_candidates", lambda *args, **kwargs: [])
    monkeypatch.setattr(query_module, "learner_snapshot", lambda *args, **kwargs: {})
    monkeypatch.setattr(query_module, "load_events", lambda: [])

    result = query_module.query_knowledge(
        tmp_path,
        "数学",
        None,
        "第62页定理3.5",
        3,
        book_title="李正元数一",
    )

    assert result["answer_mode"] == "page_unavailable"
    assert result["page_anchor"]["match_status"] == "unavailable"
    assert result["evidence_hits"] == []
    assert result["fallback_hits"] == []
    assert result["query_path"]["page_locator_index_available"] is False
    assert result["runtime_context"]["page_locator_unavailable_reason"] == "page_locator_index_missing"


def test_exact_page_evidence_retains_only_claims_supported_by_that_page(monkeypatch) -> None:
    locator = {
        "requested_page": 49,
        "requested_position": None,
        "requested_book_title": "李正元数一",
        "requested_exercise_label": "例2.29",
        "match_status": "exact_asset",
        "exercise_match_status": "unverified",
        "book_id": "li-zhengyuan-math1-ch2",
        "book_title": "李正元数一",
        "source_id": "SRC-MATH-0002",
        "page_id": "PAGE-li-zhengyuan-math1-ch2-0020",
        "source_image_path": "P49.jpg",
        "source_image_sha256": "p49-sha",
        "evidence_ids": ["EV-P49"],
        "match_basis": "formal_page_locator_index",
        "candidates": [],
        "matched_evidence_id": "",
        "matched_chunk_id": "",
        "snippets": [],
    }
    evidence = {
        "evidence_id": "EV-P49",
        "chunk_id": "chunk-p49",
        "chunk_extract_path": "",
        "page_classification_refs": [{"printed_page": 49, "source_file_sha256": "p49-sha"}],
    }
    claims = [
        {"claim_id": "CL-P49", "evidence_ids": ["EV-P49"]},
        {"claim_id": "CL-OTHER", "evidence_ids": ["EV-OTHER"]},
    ]
    retrieval = [
        {"entity_id": "EV-P49", "references": ["EV-P49"]},
        {"entity_id": "EV-OTHER", "references": ["EV-OTHER"]},
    ]
    monkeypatch.setattr(query_module, "resolve_page_locator", lambda **kwargs: dict(locator))
    monkeypatch.setattr(query_module, "exact_evidence_hits_for_locator", lambda *args, **kwargs: [evidence])
    anchor, exact_retrieval, exact_claims, exact_evidence = query_module.apply_hard_page_route(
        subject="数学",
        chapter=None,
        book_title="李正元数一",
        request={"requested_page": 49, "requested_position": None, "requested_exercise_label": "例2.29"},
        retrieval_hits=retrieval,
        claims=claims,
    )
    assert anchor["match_status"] == "exact_evidence"
    assert [item["claim_id"] for item in exact_claims] == ["CL-P49"]
    assert [item["entity_id"] for item in exact_retrieval] == ["EV-P49"]
    assert exact_evidence == [evidence]


def test_exact_asset_page_summary_is_not_rendered_as_not_found(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        query_module,
        "_resolve_query_hits",
        lambda *args, **kwargs: ([], [], [], [], True),
    )
    monkeypatch.setattr(
        query_module,
        "resolve_page_locator",
        lambda **kwargs: {
            "requested_page": 76,
            "requested_position": None,
            "requested_book_title": "李正元数一",
            "requested_exercise_label": "例3.19",
            "match_status": "exact_asset",
            "exercise_match_status": "unverified",
            "book_title": "李正元数一",
            "source_image_path": "P76.jpg",
            "source_image_sha256": "p76-sha",
        },
    )
    monkeypatch.setattr(query_module, "exact_evidence_hits_for_locator", lambda *args, **kwargs: [])
    monkeypatch.setattr(query_module, "learner_compare_candidates", lambda *args, **kwargs: [])
    monkeypatch.setattr(query_module, "learner_snapshot", lambda *args, **kwargs: {})
    monkeypatch.setattr(query_module, "load_events", lambda: [])

    result = query_module.query_knowledge(tmp_path, "数学", None, "P76 例3.19 第一问", 3, book_title="李正元数一")

    rendered = query_module.render_text(result)
    assert result["page_anchor"]["match_status"] == "exact_asset"
    assert result["page_anchor"]["exercise_match_status"] == "unverified"
    assert result["answer_mode"] == "page_asset"
    assert result["page_verification"]["textbook_explanation_allowed"] is False
    assert "教材原页已定位；教材正文未确认" in rendered
    assert "页面定位：exact_asset" in rendered
    assert "not_found" not in rendered
