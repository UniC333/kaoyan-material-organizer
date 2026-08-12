from __future__ import annotations

import json
from pathlib import Path

from kaoyan_kb.domain import book_series
from ocr_book_pages import _selected_pages
from publish_book_exercises import _effective_text


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

    pairs = book_series.worked_example_pairs_from_evidence(evidences)

    pair = next(item for item in pairs if item["exercise_label"] == "例3.8")
    assert pair["pair_status"] == "exact_pair"
    assert pair["question"]["evidence_ids"] == ["EV-MATH-000097"]
    assert pair["solution"]["evidence_ids"] == ["EV-MATH-000098"]
    assert pair["question"]["printed_pages"] == [64]
    assert pair["solution"]["printed_pages"] == [65]
    assert "\\frac{\\pi}{3}+2-\\sqrt3" in pair["solution"]["content"]


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
