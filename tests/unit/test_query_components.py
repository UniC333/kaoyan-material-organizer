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


def test_source_bound_generic_gate_remains_blocked_without_evidence():
    assert not topics.evidence_has_formal_topic_statement({}, ['导数'])
    assert topics.topic_coverage('其他概念', ['导数'])['matched_terms'] == []
