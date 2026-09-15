from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import build_exercise_coverage_report as coverage_module
from kaoyan_kb.domain import book_series
from ocr_book_pages import _selected_pages
import publish_book_ocr_evidence
from publish_book_ocr_evidence import collect_publication_items
from publish_book_exercises import _effective_text


def _publication_ready_evidences(evidences: list[dict]) -> list[dict]:
    ready = []
    for original in evidences:
        evidence = dict(original)
        evidence_id = str(evidence.get("evidence_id") or "TEST-EVIDENCE")
        refs = [item for item in evidence.get("page_classification_refs", []) or [] if isinstance(item, dict)]
        source_id = str(evidence.get("source_id") or f"SRC-{evidence_id}")
        chapter_id = str(evidence.get("chapter_id") or (refs[0].get("chapter_id") if refs else "CH-TEST"))
        span = {
            "source_id": source_id,
            "file_id": f"FILE-{evidence_id}",
            "source_file_sha256": "test-source-sha",
            "locator": {"page_start": 1, "page_end": 1, "image_start": 1, "image_end": 1},
        }
        evidence.update({
            "evidence_key": evidence.get("evidence_key") or f"{evidence_id}-key",
            "source_id": source_id,
            "chapter_id": chapter_id,
            "chunk_id": evidence.get("chunk_id") or f"CHUNK-{evidence_id}",
            "origin_type": evidence.get("origin_type") or "paper_book_reviewed_ocr",
            "review_status": evidence.get("review_status") or "accepted",
            "source_spans": evidence.get("source_spans") or [span],
            "provenance": evidence.get("provenance") or {
                "origin_type": evidence.get("origin_type") or "paper_book_reviewed_ocr",
                "verification_status": evidence.get("verification_status") or "source_grounded",
                "source_grounded": True,
                "source_spans": [span],
            },
        })
        ready.append(evidence)
    return ready


SERIES_INDEX = {
    "series": [
        {
            "series_id": "JIELI1800-M1",
            "canonical_title": "考研数学接力题典1800（数学一）",
            "aliases": ["接力", "1800", "接力题典1800"],
            "normalized_aliases": ["接力", "1800", "接力题典1800", "考研数学接力题典1800数学一"],
            "volumes": [
                {"book_id": "Q", "title": "题目册", "role": "question_book", "stages": ["basic", "advanced"], "status": "active"},
                {"book_id": "SB", "title": "基础篇题解", "role": "solution_book", "stages": ["basic"], "status": "active"},
                {"book_id": "SA", "title": "强化篇题解", "role": "solution_book", "stages": ["advanced"], "status": "pending"},
            ],
        }
    ]
}


def test_photo_pair_record_keeps_ordered_printed_pages_and_source_images() -> None:
    record = book_series._exercise_pair_record(
        {
            "evidence_id": "EV-PHOTO",
            "page_classification_refs": [
                {"printed_page": 7, "source_image_path": "P7.jpg"},
                {"printed_page": 5, "source_image_path": "P5.jpg"},
            ],
            "content": "答案",
            "title": "题解",
        },
        book_id="SB",
    )

    assert record["printed_pages"] == [5, 7]
    assert record["source_image_paths"] == ["P5.jpg", "P7.jpg"]


def _verified_query_result(*, source_id: str, printed: bool) -> dict:
    page_key = "printed_pages" if printed else "pdf_pages"
    page_anchor = {"source_id": source_id, "requested_page": 30} if printed else {"match_status": "not_requested"}
    return {
        "request_resolution": {"source_request_kind": "exercise"},
        "answer_grounding": {
            "status": "exact_answer",
            "can_conclude": True,
            "problem": {"evidence_ids": ["EV-Q"], page_key: [30 if printed else 55]},
            "solution": {"evidence_ids": ["EV-A"], page_key: [31 if printed else 61]},
        },
        "teaching_bundle": {"status": "exact"},
        "page_anchor": page_anchor,
    }


def _mock_coverage_answer(monkeypatch, result: dict) -> None:
    monkeypatch.setattr(coverage_module, "query_knowledge", lambda *args, **kwargs: result)
    monkeypatch.setattr(coverage_module, "build_answer_contract", lambda query_result: {"query_result": query_result})
    monkeypatch.setattr(
        coverage_module,
        "build_teaching_answer_view",
        lambda contract: {
            "teaching_view_version": coverage_module.TEACHING_VIEW_VERSION,
            "answer_grounding": dict(result["answer_grounding"]),
            "citation_coverage_ok": True,
        },
    )


def test_photo_exercise_coverage_verifies_nested_source_pages_and_both_entry_gates(monkeypatch, tmp_path: Path) -> None:
    relation = {
        "relation_id": "PHOTO-1",
        "relation_kind": "same-book-worked-example",
        "relation_status": "exact",
        "book_title": "照片教材",
        "chapter_id": "CH-02",
        "exercise_label": "例1",
        "question": {"source_id": "SRC-PHOTO", "evidence_ids": ["EV-Q"], "printed_pages": [30]},
        "answer": {"source_id": "SRC-PHOTO", "evidence_ids": ["EV-A"], "printed_pages": [31]},
    }
    result = _verified_query_result(source_id="SRC-PHOTO", printed=True)
    _mock_coverage_answer(monkeypatch, result)

    verification = coverage_module.verify_relation(
        relation=relation,
        subject="数学",
        book_title="照片教材",
        source_id="SRC-PHOTO",
        vault_root=tmp_path,
        evidence_by_id={
            "EV-Q": {"source_id": "SRC-PHOTO"},
            "EV-A": {"source_id": "SRC-PHOTO"},
        },
    )

    assert coverage_module.relation_source_id(relation) == "SRC-PHOTO"
    assert coverage_module.relation_chapter_number(relation) == 2
    assert verification["verification_status"] == "passed"
    assert verification["query_ok"] is True
    assert verification["ask_ok"] is True
    assert verification["page_source_consistent"] is True
    assert verification["answer_gate_ok"] is True


def test_pdf_exercise_coverage_keeps_pdf_pages_and_fails_on_source_mismatch(monkeypatch, tmp_path: Path) -> None:
    relation = {
        "relation_id": "PDF-1",
        "relation_status": "exact",
        "source_id": "SRC-PDF",
        "section_root": "2.3",
        "category": "comprehensive",
        "exercise_label": "01",
        "question_evidence_ids": ["EV-Q"],
        "question_pdf_pages": [55],
        "answer_evidence_ids": ["EV-A"],
        "answer_pdf_pages": [61],
    }
    result = _verified_query_result(source_id="SRC-PDF", printed=False)
    _mock_coverage_answer(monkeypatch, result)

    verification = coverage_module.verify_relation(
        relation=relation,
        subject="408",
        book_title="王道数据结构",
        source_id="SRC-PDF",
        vault_root=tmp_path,
        evidence_by_id={
            "EV-Q": {"source_id": "SRC-PDF"},
            "EV-A": {"source_id": "WRONG-SOURCE"},
        },
    )

    assert coverage_module.relation_chapter_number(relation) == 2
    assert verification["query_ok"] is True
    assert verification["ask_ok"] is True
    assert verification["page_source_consistent"] is False
    assert "evidence_source_mismatch" in verification["failure_reasons"]


def test_exercise_coverage_require_complete_returns_nonzero(monkeypatch) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "build_exercise_coverage_report.py",
            "--subject",
            "数学",
            "--book-title",
            "教材",
            "--require-complete",
            "--format",
            "quiet",
        ],
    )
    monkeypatch.setattr(coverage_module, "build_report", lambda args: ({"summary": {"complete": False}}, False))

    assert coverage_module.main() == 2


def test_alias_resolves_series_but_alias_only_is_ambiguous(monkeypatch) -> None:
    monkeypatch.setattr(book_series, "load_book_series_index", lambda: SERIES_INDEX)
    ambiguous = book_series.resolve_book_route(query="1800第7题")
    assert ambiguous["match_status"] == "book_ambiguous"
    assert ambiguous["series_id"] == "JIELI1800-M1"

    exact = book_series.resolve_book_route(query="接力基础篇高数第一章选择题第7题怎么做")
    assert exact["match_status"] == "exact_series"
    assert exact["stage"] == "basic"
    assert exact["volume_ids"] == ["Q"]
    assert book_series.parse_exercise_request("基础篇高数第一章选择题第7题")["chapter_number"] == 1


def test_context_can_fill_stage_and_role(monkeypatch) -> None:
    monkeypatch.setattr(book_series, "load_book_series_index", lambda: SERIES_INDEX)
    route = book_series.resolve_book_route(query="1800第7题", context={"stage": "basic", "role_intent": "question"})
    assert route["match_status"] == "exact_series"
    assert route["volume_ids"] == ["Q"]


def test_exercise_request_and_pair_route(monkeypatch) -> None:
    request = book_series.parse_exercise_request("接力 P4 第7题按书解讲")
    assert request["printed_page"] == 4
    assert request["exercise_number"] == 7
    assert request["role_intent"] == "paired_answer"

    monkeypatch.setattr(
        book_series,
        "load_exercise_pair_index",
        lambda: {
            "items": [
                {
                    "series_id": "JIELI1800-M1",
                    "stage": "basic",
                    "part": "CALC",
                    "chapter_number": 1,
                    "exercise_type": "choice",
                    "exercise_number": 7,
                    "exercise_key": "JIELI1800-M1|basic|CALC-01|choice|7",
                    "pair_status": "exact_pair",
                    "question": {"printed_pages": [4]},
                    "solution": {"printed_pages": [1, 2]},
                }
            ]
        },
    )
    result = book_series.resolve_exercise_route(
        query="1800基础篇高数第一章选择题第7题怎么做",
        book_route={"series_id": "JIELI1800-M1", "stage": "basic", "match_status": "exact_series"},
    )
    assert result["match_status"] == "exact_exercise"
    assert result["pair_status"] == "exact_pair"


def test_plain_worked_example_route_uses_page_and_integer_label(monkeypatch) -> None:
    monkeypatch.setattr(
        book_series,
        "load_exercise_pair_index",
        lambda: {
            "items": [
                {
                    "series_id": "TANG2027-M1",
                    "stage": "basic",
                    "exercise_label": label,
                    "exercise_key": f"worked:tang|CH1|p25|{label}",
                    "pair_status": "exact_pair",
                    "question": {"printed_pages": [25]},
                    "solution": {"printed_pages": [25]},
                }
                for label in ("例1", "例2")
            ]
        },
    )

    result = book_series.resolve_exercise_route(
        query="第25页例1的答案",
        book_route={"series_id": "TANG2027-M1", "stage": "basic", "match_status": "exact_series"},
    )

    assert result["match_status"] == "exact_exercise"
    assert result["exercise_key"] == "worked:tang|CH1|p25|例1"


def test_page_local_worked_label_overrides_base_query_label(monkeypatch) -> None:
    monkeypatch.setattr(
        book_series,
        "load_exercise_pair_index",
        lambda: {
            "items": [
                {
                    "pair_kind": "same_book_worked_example",
                    "book_id": "tang",
                    "book_title": "汤家凤高数基础篇",
                    "exercise_label": "例1（P18页内1）",
                    "pair_status": "exact_pair",
                    "question": {"printed_pages": [18], "content": "原题"},
                    "solution": {"printed_pages": [18], "content": "原书答案"},
                }
            ]
        },
    )

    result = book_series.resolve_answer_grounding(
        query="P18 例1 问答验收",
        book_title="汤家凤高数基础篇",
        page_anchor={
            "requested_page": 18,
            "requested_exercise_label": "例1（P18页内1）",
            "book_id": "tang",
            "match_status": "exact_evidence",
        },
        book_route={"series_id": "TANG2027-M1"},
        exercise_route={},
    )

    assert result["status"] == "exact_answer"
    assert result["can_conclude"] is True


def test_ocr_chapter_filter_handles_question_and_solution_ids(tmp_path: Path) -> None:
    chapters = {
        "chapters": [
            {"chapter_id": "CH-JL1800-B-CALC-01", "page_start": 3, "page_end": 9},
            {"chapter_id": "CH-JL1800-SB-CALC-01", "page_start": 1, "page_end": 12},
            {"chapter_id": "CH-JL1800-A-CALC-01", "page_start": 133, "page_end": 139},
        ]
    }
    mappings = {"items": [{"page_id": f"P{page}", "printed_page": page} for page in range(1, 140)]}
    chapters_path = tmp_path / "chapters.yaml"
    mappings_path = tmp_path / "page_mappings.json"
    chapters_path.write_text(json.dumps(chapters), encoding="utf-8")
    mappings_path.write_text(json.dumps(mappings), encoding="utf-8")
    items = [{"page_id": f"P{page}"} for page in range(1, 140)]

    question = _selected_pages(items=items, paths={"chapters": chapters_path, "page_mappings": mappings_path}, stage="basic", chapter_ids=["CH-JL1800-B-CALC-01"])
    solution = _selected_pages(items=items, paths={"chapters": chapters_path, "page_mappings": mappings_path}, stage="basic", chapter_ids=["CH-JL1800-SB-CALC-01"])
    assert len(question) == 7
    assert len(solution) == 12


def test_ocr_publish_chapter_scope_excludes_out_of_scope_incomplete_page(monkeypatch, tmp_path: Path) -> None:
    def fake_load(path: Path, default: object) -> dict:
        if str(path).endswith("normalized.json"):
            return {"chunk_candidates": [{"block_id": "TEXT-1", "block_type": "text", "text": "正文"}]}
        return {"items": []}

    monkeypatch.setattr(publish_book_ocr_evidence, "load_json_or_default", fake_load)
    monkeypatch.setattr(
        publish_book_ocr_evidence,
        "cache_paths_for_request",
        lambda *_: {"overlay": tmp_path / "overlay.json"},
    )
    status = {
        "items": [
            {"page_id": "PAGE-CH3", "status": "completed", "normalized_path": tmp_path / "normalized.json", "request_key": "ch3"},
            {"page_id": "PAGE-CH4", "status": "pending", "normalized_path": tmp_path / "normalized.json", "request_key": "ch4"},
        ]
    }
    assets = {"items": [{"page_id": "PAGE-CH3"}, {"page_id": "PAGE-CH4"}]}
    classes = {
        "PAGE-CH3": {"chapter_id": "CH-3", "classification_status": "confirmed"},
        "PAGE-CH4": {"chapter_id": "CH-4", "classification_status": "confirmed"},
    }
    locator = {"PAGE-CH3": {"printed_page": 42}, "PAGE-CH4": {"printed_page": 73}}

    items, blocked = collect_publication_items(
        runtime=SimpleNamespace(ocr_cache_root=tmp_path),
        root=tmp_path,
        assets=assets,
        status=status,
        classes=classes,
        locator_by_page=locator,
        chapter_ids={"CH-3"},
    )

    assert [item["page_id"] for item in items] == ["PAGE-CH3"]
    assert blocked == []


def test_effective_text_requires_review_for_formula() -> None:
    normalized = {"chunk_candidates": [{"block_id": "b1", "block_type": "formula", "confidence": 0.9, "text": "x=1"}]}
    text, blockers = _effective_text(normalized, {})
    assert text == ""
    assert blockers == ["b1"]
    text, blockers = _effective_text(normalized, {"b1": {"review_status": "accepted", "corrected_text": "x=2"}})
    assert text == "x=2"
    assert blockers == []


def test_same_book_worked_example_pairs_question_page_with_next_page_solution() -> None:
    evidences = [
        {
            "evidence_id": "EV-MATH-000097",
            "verification_status": "source_grounded",
            "source_grounded": True,
            "content": "【例 3.8】计算下列定积分：（I）错误 OCR 的原题；（II）另一问。",
            "page_classification_refs": [
                {"book_id": "li-math1", "book_title": "李正元数一", "chapter_id": "CH3", "printed_page": 64, "source_image_path": "P64.jpg"}
            ],
        },
        {
            "evidence_id": "EV-MATH-000098",
            "verification_status": "source_grounded",
            "source_grounded": True,
            "content": "【解】（I）2\\left(\\frac12\\cdot\\frac{\\pi}{3}+\\left.\\sin x\\right|_{\\pi/3}^{\\pi/2}\\right)=\\frac{\\pi}{3}+2-\\sqrt3。\n【例 3.9】下一题。",
            "page_classification_refs": [
                {"book_id": "li-math1", "book_title": "李正元数一", "chapter_id": "CH3", "printed_page": 65, "source_image_path": "P65.jpg"}
            ],
        },
    ]

    pairs = book_series.worked_example_pairs_from_evidence(_publication_ready_evidences(evidences))

    pair = next(item for item in pairs if item["exercise_label"] == "例3.8")
    assert pair["pair_status"] == "exact_pair"
    assert pair["question"]["evidence_ids"] == ["EV-MATH-000097"]
    assert pair["solution"]["evidence_ids"] == ["EV-MATH-000098"]
    assert pair["question"]["printed_pages"] == [64]
    assert pair["solution"]["printed_pages"] == [65]
    assert "\\frac{\\pi}{3}+2-\\sqrt3" in pair["solution"]["content"]


def test_plain_handout_examples_pair_by_page_and_accept_proof_marker() -> None:
    evidences = [
        {
            "evidence_id": "EV-MATH-PLAIN-25",
            "verification_status": "source_grounded",
            "source_grounded": True,
            "content": "例1 求极限。\n解 书中解答。\n例2 证明命题。\n证明 书中证明。",
            "page_classification_refs": [
                {
                    "book_id": "tang-math1",
                    "book_title": "汤家凤高数基础篇",
                    "chapter_id": "CH1",
                    "printed_page": 25,
                    "source_image_path": "P25.jpg",
                }
            ],
        }
    ]

    pairs = book_series.worked_example_pairs_from_evidence(_publication_ready_evidences(evidences))

    assert [item["exercise_label"] for item in pairs] == ["例1", "例2"]
    assert all(item["pair_status"] == "exact_pair" for item in pairs)
    assert {item["exercise_key"] for item in pairs} == {
        "worked:tang-math1|CH1|p25|例1",
        "worked:tang-math1|CH1|p25|例2",
    }
    assert book_series.parse_exercise_request("第25页例1的答案")["exercise_label"] == "例1"


def test_restarted_plain_example_number_is_distinguished_by_question_page() -> None:
    evidences = []
    for page in (25, 30):
        evidences.append(
            {
                "evidence_id": f"EV-MATH-PLAIN-{page}",
                "verification_status": "source_grounded",
                "source_grounded": True,
                "content": "例1 题目。\n解 答案。",
                "page_classification_refs": [
                    {
                        "book_id": "tang-math1",
                        "book_title": "汤家凤高数基础篇",
                        "chapter_id": "CH1",
                        "printed_page": page,
                        "source_image_path": f"P{page}.jpg",
                    }
                ],
            }
        )

    pairs = book_series.worked_example_pairs_from_evidence(_publication_ready_evidences(evidences))

    assert len(pairs) == 2
    assert {item["exercise_key"] for item in pairs} == {
        "worked:tang-math1|CH1|p25|例1",
        "worked:tang-math1|CH1|p30|例1",
    }
    assert all(item["pair_status"] == "exact_pair" for item in pairs)


def test_worked_example_pairs_do_not_cross_missing_printed_pages() -> None:
    evidences = [
        {
            "evidence_id": "EV-P54",
            "verification_status": "source_grounded",
            "source_grounded": True,
            "content": "例1 只有题目。",
            "page_classification_refs": [
                {"book_id": "tang-math1", "book_title": "汤家凤高数基础篇", "chapter_id": "CH3", "printed_page": 54}
            ],
        },
        {
            "evidence_id": "EV-P64",
            "verification_status": "source_grounded",
            "source_grounded": True,
            "content": "解 不应配给P54。\n例2 P64题目。\n解 P64答案。",
            "page_classification_refs": [
                {"book_id": "tang-math1", "book_title": "汤家凤高数基础篇", "chapter_id": "CH3", "printed_page": 64}
            ],
        },
    ]

    pairs = book_series.worked_example_pairs_from_evidence(_publication_ready_evidences(evidences))

    p54 = next(item for item in pairs if item["exercise_label"] == "例1")
    p64 = next(item for item in pairs if item["exercise_label"] == "例2")
    assert p54["pair_status"] == "question_only"
    assert not p54["solution"]
    assert p64["pair_status"] == "exact_pair"


def test_worked_example_answer_stops_before_following_theory() -> None:
    evidences = [
        {
            "evidence_id": "EV-P30",
            "verification_status": "source_grounded",
            "source_grounded": True,
            "content": "例2 求常数。\n解 答案。\n(3) 若函数可导，则连续。\n证明 理论证明。",
            "page_classification_refs": [
                {"book_id": "tang-math1", "book_title": "汤家凤高数基础篇", "chapter_id": "CH2", "printed_page": 30}
            ],
        },
        {
            "evidence_id": "EV-P44",
            "verification_status": "source_grounded",
            "source_grounded": True,
            "content": "例1 求证。\n证明 例题证明。\n定理2 某定理。\n证明 定理证明。",
            "page_classification_refs": [
                {"book_id": "tang-math1", "book_title": "汤家凤高数基础篇", "chapter_id": "CH3", "printed_page": 44}
            ],
        },
    ]

    pairs = book_series.worked_example_pairs_from_evidence(_publication_ready_evidences(evidences))

    assert all(item["pair_status"] == "exact_pair" for item in pairs)
    assert "理论证明" not in next(item for item in pairs if item["exercise_label"] == "例2")["solution"]["content"]
    assert "定理证明" not in next(item for item in pairs if item["exercise_label"] == "例1")["solution"]["content"]


def test_resolve_answer_grounding_requires_exact_pair(monkeypatch) -> None:
    monkeypatch.setattr(
        book_series,
        "load_exercise_pair_index",
        lambda: {
            "items": [
                {
                    "pair_kind": "same_book_worked_example",
                    "book_id": "li-math1",
                    "book_title": "李正元数一",
                    "exercise_label": "例3.8",
                    "pair_status": "exact_pair",
                    "question": {"evidence_ids": ["EV-Q"], "printed_pages": [64]},
                    "solution": {"evidence_ids": ["EV-A"], "printed_pages": [65], "content": "正确答案"},
                }
            ]
        },
    )
    grounding = book_series.resolve_answer_grounding(
        query="P64 例3.8 第一问检查过程",
        book_title="李正元数一",
        page_anchor={"requested_page": 64, "requested_exercise_label": "例3.8", "book_id": "li-math1", "match_status": "exact_evidence"},
        book_route={},
        exercise_route={},
    )
    assert grounding["status"] == "exact_answer"
    assert grounding["can_conclude"] is True

    monkeypatch.setattr(book_series, "load_exercise_pair_index", lambda: {"items": []})
    blocked = book_series.resolve_answer_grounding(
        query="P64 例3.8 第一问检查过程",
        book_title="李正元数一",
        page_anchor={"requested_page": 64, "requested_exercise_label": "例3.8", "book_id": "li-math1", "match_status": "exact_evidence"},
        book_route={},
        exercise_route={},
    )
    assert blocked["status"] == "answer_not_found"
    assert blocked["can_conclude"] is False


def test_source_photo_without_book_identity_still_fails_closed(monkeypatch) -> None:
    monkeypatch.setattr(book_series, "load_exercise_pair_index", lambda: {"items": []})
    grounding = book_series.resolve_answer_grounding(
        query="看这张题目照片，帮我检查这道题的过程",
        book_title=None,
        page_anchor={},
        book_route={},
        exercise_route={},
    )
    assert grounding["required"] is True
    assert grounding["status"] == "answer_not_found"
    assert grounding["can_conclude"] is False


def test_explicitly_self_authored_problem_does_not_require_source_answer(monkeypatch) -> None:
    monkeypatch.setattr(book_series, "load_exercise_pair_index", lambda: {"items": []})
    grounding = book_series.resolve_answer_grounding(
        query="这是我自拟的一道题，帮我推导",
        book_title=None,
        page_anchor={},
        book_route={},
        exercise_route={},
    )
    assert grounding["status"] == "not_applicable"
    assert grounding["can_conclude"] is True
