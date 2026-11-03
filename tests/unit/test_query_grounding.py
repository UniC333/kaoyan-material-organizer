from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import query_local_knowledge as entry
from kaoyan_kb.domain.query import grounding


@pytest.mark.parametrize("name", [
    "build_answer_grounding", "build_teaching_bundle", "build_page_content_bundle",
    "_replace_relative_image_links", "_promote_exact_pair_page_anchor",
    "_empty_page_content_bundle", "_reviewed_page_evidence",
])
def test_legacy_exports_share_the_domain_implementation(name):
    assert getattr(entry, name) is getattr(grounding, name)


def _answer_inputs():
    return {
        "query": "第1题原书答案",
        "book_title": "教材",
        "page_anchor": {"match_status": "exact_evidence"},
        "exercise_anchor": {
            "status": "exact_answer_evidence",
            "exercise_label": "1",
            "question_evidence_ids": ["EV-Q"],
            "answer_evidence_ids": ["EV-A"],
            "question_printed_pages": [6],
            "answer_printed_pages": [8],
            "question_pdf_pages": [16],
            "answer_pdf_pages": [18],
        },
        "evidences": [
            {"evidence_id": "EV-Q", "content": "原题"},
            {"evidence_id": "EV-A", "content": "原书答案"},
        ],
    }


def test_exact_answer_payload_retains_evidence_and_independent_page_numbers():
    inputs = _answer_inputs()
    original = deepcopy(inputs)

    answer = grounding.build_answer_grounding(**inputs)
    teaching = grounding.build_teaching_bundle(answer, {"exercise_label": "1"})

    assert answer["status"] == "exact_answer"
    assert answer["can_conclude"] is True
    assert teaching["problem_text"] == "原题"
    assert teaching["source_answer_text"] == "原书答案"
    assert teaching["citations"]["problem_evidence_ids"] == ["EV-Q"]
    assert teaching["citations"]["solution_evidence_ids"] == ["EV-A"]
    assert teaching["citations"]["problem_printed_pages"] == [6]
    assert teaching["citations"]["solution_printed_pages"] == [8]
    assert teaching["citations"]["problem_pdf_pages"] == [16]
    assert teaching["citations"]["solution_pdf_pages"] == [18]
    assert inputs == original


@pytest.mark.parametrize("missing", ["question_evidence_ids", "answer_evidence_ids"])
def test_incomplete_pair_never_exposes_teaching_text(missing):
    inputs = _answer_inputs()
    inputs["exercise_anchor"][missing] = []

    answer = grounding.build_answer_grounding(**inputs)
    teaching = grounding.build_teaching_bundle(answer, {})

    assert answer["can_conclude"] is False
    assert teaching["status"] == "blocked"
    assert teaching["problem_text"] == ""
    assert teaching["source_answer_text"] == ""


@pytest.mark.parametrize("status", ["conflict", "ambiguous", "unverified"])
def test_page_crosscheck_blocks_both_answer_and_page_content(status):
    inputs = _answer_inputs()
    crosscheck = {"required": True, "status": status, "reason": "页码尚未确认"}

    answer = grounding.build_answer_grounding(**inputs, page_crosscheck=crosscheck)
    page = grounding.build_page_content_bundle(
        request_resolution={"source_request_kind": "page_content"},
        page_anchor={"match_status": "exact_evidence", "requested_page": 6, "pdf_page": 16},
        evidences=inputs["evidences"],
        page_crosscheck=crosscheck,
    )

    assert answer["can_conclude"] is False
    assert answer["failure_reason"] == "页码尚未确认"
    assert page["status"] == "blocked"
    assert page["content"] == ""
    assert page["printed_pages"] == [6]
    assert page["pdf_pages"] == [16]
