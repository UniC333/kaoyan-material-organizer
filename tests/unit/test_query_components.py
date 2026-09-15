from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts'))
import query_local_knowledge as entry
from kaoyan_kb.domain.query import request, topics, batch, views

CASES = json.loads((Path(__file__).resolve().parents[1] / 'fixtures' / 'query_parsing_compatibility.json').read_text(encoding='utf-8'))


@pytest.mark.parametrize('case', CASES, ids=[c['query'] for c in CASES])
def test_request_and_topic_compatibility(case):
    query = case['query']
    assert request.parse_page_anchor(query) == case['page']
    assert request.extract_explicit_concepts(query) == case['concepts']
    assert request.detect_intent(query) == case['intent']
    assert topics.generic_topic_terms(query) == case['topics']
    assert entry.parse_page_anchor(query) == case['page']


def test_batch_non_batch_does_not_resolve_or_query():
    def fail(*args, **kwargs):
        pytest.fail('ordinary question must not enter batch dependencies')
    assert batch.query_exercise_batch(Path('.'), '408', None, '什么是树', 3, query_one=fail, resolve_book=fail, runtime_context=fail, resolve_targets=fail) is None


def test_blocked_batch_keeps_no_answer_and_reports_reason():
    target = {'exercise_label': '11', 'reason': 'exercise-relation-needs-review'}
    item = batch._blocked_batch_item(target, book_title='教材')
    assert item['answer_grounding']['can_conclude'] is False
    assert item['teaching_bundle']['source_answer_text'] == ''
    assert item['teaching_bundle']['citations']['solution_evidence_ids'] == []
    text = views.render_exercise_batch_text({'batch_status':'blocked', 'items':[item], 'summary':{'requested_count':1,'exact_count':0,'blocked_count':1}})
    assert '仍需人工复核' in text


def test_photo_teaching_bundle_citations_keep_printed_pages_and_image_paths():
    grounding = {
        "required": True,
        "status": "exact_answer",
        "can_conclude": True,
        "problem": {
            "evidence_ids": ["EV-Q"],
            "printed_pages": [6, 7],
            "pdf_pages": [],
            "source_image_paths": ["Q-P6.jpg", "Q-P7.jpg"],
            "content": "题干",
        },
        "solution": {
            "evidence_ids": ["EV-A"],
            "printed_pages": [5, 6],
            "pdf_pages": [],
            "source_image_paths": ["A-P5.jpg", "A-P6.jpg"],
            "content": "答案",
        },
    }
    bundle = entry.build_teaching_bundle(grounding, {"exercise_label": "03"})

    assert bundle["status"] == "exact"
    assert bundle["citations"]["problem_printed_pages"] == [6, 7]
    assert bundle["citations"]["solution_printed_pages"] == [5, 6]
    assert bundle["citations"]["problem_source_image_paths"] == ["Q-P6.jpg", "Q-P7.jpg"]
    assert bundle["citations"]["solution_source_image_paths"] == ["A-P5.jpg", "A-P6.jpg"]
    assert bundle["citations"]["problem_pdf_pages"] == []
    assert bundle["citations"]["solution_pdf_pages"] == []


def test_natural_language_exact_pair_repairs_only_matching_alias_page_miss():
    resolved = request.resolve_request(
        query="P16选择题第1题按原书讲解",
        book_title="1800基础篇",
        chapter=None,
        printed_page=None,
        exercise_label=None,
    )
    assert resolved["source_request_kind"] == "exercise"
    assert resolved["page"]["number"] == 16

    grounding = {
        "required": True,
        "status": "exact_answer",
        "can_conclude": True,
        "problem": {
            "evidence_ids": ["EV-Q"],
            "printed_pages": [16],
            "source_image_paths": [r"E:\book\P16.jpg"],
            "content": "原题",
        },
        "solution": {"content": "原书答案", "evidence_ids": ["EV-A"]},
    }
    anchor = {
        "requested_page": 16,
        "match_status": "not_found",
        "exercise_match_status": "matched",
        "locator_available": True,
    }
    entry._promote_exact_pair_page_anchor(anchor, grounding, exact_pair=True, series_alias_match=True)
    teaching = entry.build_teaching_bundle(grounding, resolved)
    verification = request.build_page_verification_summary(
        anchor,
        "exercise_pair",
        request_resolution=resolved,
        answer_grounding=grounding,
        teaching_bundle=teaching,
    )

    assert anchor["match_status"] == "exact_evidence"
    assert anchor["match_basis"] == "exact_exercise_problem_evidence"
    assert verification["textbook_explanation_allowed"] is True


@pytest.mark.parametrize("status", ["unavailable", "unmapped", "stale"])
def test_exact_pair_does_not_override_unavailable_or_stale_page_mapping(status):
    anchor = {
        "requested_page": 16,
        "match_status": status,
        "locator_available": status != "unavailable",
    }
    grounding = {
        "status": "exact_answer",
        "can_conclude": True,
        "problem": {
            "evidence_ids": ["EV-Q"],
            "printed_pages": [16],
            "source_image_paths": [r"E:\book\P16.jpg"],
        },
    }
    entry._promote_exact_pair_page_anchor(anchor, grounding, exact_pair=True, series_alias_match=True)
    assert anchor["match_status"] == status


def test_exact_pair_does_not_promote_a_different_requested_page():
    anchor = {"requested_page": 17, "match_status": "not_found", "locator_available": True}
    grounding = {
        "status": "exact_answer",
        "can_conclude": True,
        "problem": {
            "evidence_ids": ["EV-Q"],
            "printed_pages": [16],
            "source_image_paths": [r"E:\book\P16.jpg"],
        },
    }
    entry._promote_exact_pair_page_anchor(anchor, grounding, exact_pair=True, series_alias_match=True)
    assert anchor["match_status"] == "not_found"


def test_page_miss_is_not_promoted_without_a_series_alias_match():
    anchor = {"requested_page": 16, "match_status": "not_found", "locator_available": True}
    grounding = {
        "status": "exact_answer",
        "can_conclude": True,
        "problem": {
            "evidence_ids": ["EV-Q"],
            "printed_pages": [16],
            "source_image_paths": [r"E:\book\P16.jpg"],
        },
    }
    entry._promote_exact_pair_page_anchor(anchor, grounding, exact_pair=True, series_alias_match=False)
    assert anchor["match_status"] == "not_found"


def test_teaching_bundle_replaces_unique_relative_figure_with_verified_full_page():
    grounding = {
        "required": True,
        "status": "exact_answer",
        "can_conclude": True,
        "problem": {
            "evidence_ids": ["EV-Q"],
            "printed_pages": [17],
            "source_image_paths": [r"E:\book pages\P17.jpg"],
            "content": "看图作答\n![img-0.jpeg](img-0.jpeg)",
        },
        "solution": {"evidence_ids": ["EV-A"], "content": "答案"},
    }
    bundle = entry.build_teaching_bundle(grounding, {"exercise_label": "07"})
    assert "img-0.jpeg](img-0.jpeg)" not in bundle["problem_text"]
    assert "![原题图（已核验整页原图）](<E:/book pages/P17.jpg>)" in bundle["problem_text"]


def test_teaching_bundle_lists_all_pages_when_image_to_page_mapping_is_ambiguous():
    grounding = {
        "required": True,
        "status": "exact_answer",
        "can_conclude": True,
        "problem": {
            "evidence_ids": ["EV-Q"],
            "printed_pages": [17, 18],
            "source_image_paths": [r"E:\book\P17.jpg", r"E:\book\P18.jpg"],
            "content": "图一 ![a](a.jpeg) 图二 ![b](b.jpeg)",
        },
        "solution": {"evidence_ids": ["EV-A"], "content": "答案"},
    }
    bundle = entry.build_teaching_bundle(grounding, {"exercise_label": "07"})
    text = bundle["problem_text"]
    assert "a.jpeg" not in text and "b.jpeg" not in text
    assert "原题图块：见下列已核验整页原图" in text
    assert "(<E:/book/P17.jpg>)" in text
    assert "(<E:/book/P18.jpg>)" in text


def test_source_bound_generic_gate_remains_blocked_without_evidence():
    assert not topics.evidence_has_formal_topic_statement({}, ['导数'])
    assert topics.topic_coverage('其他概念', ['导数'])['matched_terms'] == []
