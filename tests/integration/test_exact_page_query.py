from __future__ import annotations

import sys
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import query_local_knowledge as query_module


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
