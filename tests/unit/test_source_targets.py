from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts'))
import common
from kaoyan_kb.domain.query import source_targets as targets, request
from kaoyan_kb.domain.query.permission import teaching_permission, finalize_result
from kaoyan_kb.domain import book_series
from save_local_answer import save_eligibility


@pytest.fixture
def books(monkeypatch):
    data = [dict(series_id='EX', canonical_title='练习1800', aliases=['练习', '1800'], volumes=[
        dict(book_id='EX-Q', title='练习1800题目册', status='active', role='question_book'),
        dict(book_id='EX-A', title='练习1800基础篇题解', status='active', role='solution_book'),
    ]), dict(series_id='TEXT', canonical_title='基础讲义', aliases=['基础讲义'], volumes=[
        dict(book_id='TEXT-CALC', title='高等数学辅导讲义 基础篇', status='active'),
    ])]
    monkeypatch.setattr(targets, 'catalogue', lambda: data)
    return data


@pytest.mark.parametrize('token', ['1800p17', '1800 P17', '1800p.17', '1800p．17', '1800第17页', '1800 17页'])
def test_registered_numeric_book_page_boundary(books, token):
    text = f'练习{token}第3题和高数基础篇56页'
    result = targets.split_targets(text)
    assert [t['printed_page'] for t in result] == [17, 56]
    assert request.parse_page_anchor(result[0]['fragment'])['requested_exercise_label'] == '03'
    assert request.parse_page_anchor(result[1]['fragment'])['requested_exercise_label'] == ''
    assert '高数' not in result[0]['fragment']
    assert all(text[t['span'][0]:t['span'][1]] == t['fragment'] for t in result)


def test_arbitrary_identifier_is_not_page(books):
    assert targets.page_mentions('abc1800p17') == []
    assert targets.page_mentions('codep17') == []


@pytest.mark.parametrize('text', ['P17-P20', '第17页至第20页', 'P17 ～ P20'])
def test_range_is_not_multi_target(books, text):
    assert targets.split_targets(text) == []


def test_book_precedence_and_unknown_book_no_default(books, tmp_path):
    task = tmp_path / '01_任务' / '当前任务.md'
    task.parent.mkdir()
    task.write_text('数学唯一当前主教材：《高等数学辅导讲义 基础篇》', encoding='utf8')
    inferred = request.resolve_current_task_book(vault_root=tmp_path, subject='数学', explicit_book_title=None, query='练习1800第17页第3题')
    assert inferred['source'] == 'query'
    assert inferred['book_title'] != '高等数学辅导讲义 基础篇'
    unknown = request.resolve_current_task_book(vault_root=tmp_path, subject='数学', explicit_book_title=None, query='《未知教材》P17第3题')
    assert unknown['status'] == 'not_found' and unknown['source'] == 'query'
    default = request.resolve_current_task_book(vault_root=tmp_path, subject='数学', explicit_book_title=None, query='P71例3')
    assert default['source'] == 'current_task_default'
    context = request.resolve_current_task_book(vault_root=tmp_path, subject='数学', explicit_book_title=None, query='P71例3', confirmed_book_title='已确认教材')
    assert context['source'] == 'confirmed_context' and context['book_title'] == '已确认教材'
    explicit = request.resolve_current_task_book(vault_root=tmp_path, subject='数学', explicit_book_title='指定书', query='练习1800P17')
    assert explicit['book_title'] == '指定书'


def test_descriptive_title_requires_unique_volume(books):
    assert targets.resolve_mentioned_book('高数基础篇P56')['book_id'] == 'TEXT-CALC'
    books.append(dict(series_id='OTHER', volumes=[dict(book_id='OTHER-CALC', title='另一版高等数学基础篇', status='active')]))
    binding = targets.resolve_mentioned_book('高数基础篇P56')
    assert binding['status'] == 'ambiguous' and not binding['book_title']


def exact_result(page=17):
    return dict(
        query='', book_resolution=dict(status='exact', source='explicit', book_title='练习1800题目册'),
        request_resolution=dict(source_request_kind='exercise', page=dict(number=page), exercise_label='03'),
        page_anchor=dict(requested_page=page, match_status='exact_evidence', exercise_match_status='matched'),
        page_crosscheck=dict(required=False), page_verification=dict(textbook_explanation_allowed=True),
        answer_mode='exercise_pair',
        answer_grounding=dict(required=True, status='exact_answer', can_conclude=True, problem=dict(evidence_ids=['Q'], printed_pages=[page], content='题干'), solution=dict(evidence_ids=['A'], content='答案'), failure_reason='', next_action=''),
        teaching_bundle=dict(status='exact', problem_text='题干', source_answer_text='答案', citations=dict(problem_evidence_ids=['Q'], solution_evidence_ids=['A'])),
        exercise_route=dict(question=dict(content='题干'), solution=dict(content='答案')),
    )


@pytest.mark.parametrize('subquestion', ['第一问', '第1问', '第（1）问', '第(一)问', '第十二问'])
def test_subquestion_is_exercise_intent_without_invented_parent(books, monkeypatch, subquestion):
    monkeypatch.setattr(request, 'resolve_section_anchor', lambda **_: {'status': 'not_requested'})
    text = f'1800第10页{subquestion}刚刚做错了，帮我讲一下这个等价的问题'
    resolved = request.resolve_request(query=text, book_title='1800', chapter=None, printed_page=None, exercise_label=None)
    assert resolved['source_request_kind'] == 'exercise'
    assert resolved['exercise_label'] == ''
    assert resolved['exercise_scope']['exercise_number'] is None
    assert resolved['page']['number'] == 10
    binding = targets.resolve_mentioned_book(text)
    assert binding['status'] == 'ambiguous'
    assert {v['book_id'] for v in binding['candidates']} == {'EX-Q', 'EX-A'}


def test_explicit_volume_keeps_its_page_and_identity(books, monkeypatch):
    monkeypatch.setattr(request, 'resolve_section_anchor', lambda **_: {'status': 'not_requested'})
    for title, book_id in [('练习1800题目册', 'EX-Q'), ('练习1800基础篇题解', 'EX-A')]:
        text = f'{title}第10页第一问'
        binding = targets.resolve_mentioned_book(text)
        assert binding['book_id'] == book_id
        resolved = request.resolve_request(query=text, book_title=binding['book_title'], chapter=None, printed_page=None, exercise_label=None)
        assert resolved['book_title'] == title
        assert resolved['page']['number'] == 10


@pytest.mark.parametrize('status', ['unavailable', 'unmapped', 'stale', 'ambiguous', 'not_found'])
def test_precise_answer_cannot_bypass_page_gate(status):
    result = exact_result()
    result['page_anchor']['match_status'] = status
    result['page_anchor']['snippets'] = ['秘密答案正文']
    finalize_result(result)
    assert not teaching_permission(result)
    assert not result['answer_grounding']['can_conclude']
    assert result['teaching_bundle']['status'] == 'blocked'
    assert not result['teaching_bundle']['source_answer_text']
    assert '秘密答案正文' not in json.dumps(result, ensure_ascii=False)
    assert 'content' not in result['exercise_route']['solution']
    assert not save_eligibility(result)[0]


@pytest.mark.parametrize('fault', ['crosscheck', 'book', 'citations', 'body', 'wrong_page', 'wrong_volume', 'stale_index'])
def test_all_consumers_share_teaching_denial(fault):
    result = exact_result()
    if fault == 'crosscheck':
        result['page_crosscheck'] = dict(required=True, status='conflict')
    elif fault == 'book':
        result['book_resolution']['status'] = 'ambiguous'
    elif fault == 'citations':
        result['teaching_bundle']['citations']['solution_evidence_ids'] = []
    elif fault == 'body':
        result['teaching_bundle']['source_answer_text'] = ''
    elif fault == 'wrong_page':
        result['answer_grounding']['problem']['printed_pages'] = [18]
    elif fault == 'wrong_volume':
        result['book_resolution']['book_id'] = 'ONE'
        result['answer_grounding']['problem']['book_id'] = 'TWO'
    else:
        result['runtime_context'] = {'exercise_locator_index_available': False}
    finalize_result(result)
    assert result['answer_grounding']['status'] != 'exact_answer'
    assert result['page_verification']['textbook_explanation_allowed'] is False
    assert save_eligibility(result)[0] is False


def test_explicit_page_and_label_reach_pairing(books, monkeypatch):
    resolved = request.resolve_request(query='练习1800p17第3题', book_title='练习1800', chapter=None, printed_page=18, exercise_label='4')
    monkeypatch.setattr(book_series, 'load_exercise_pair_index', lambda: {'items': [
        dict(series_id='EX', exercise_number=3, question=dict(printed_pages=[17]), exercise_key='wrong'),
        dict(series_id='EX', exercise_number=4, question=dict(printed_pages=[18]), exercise_key='right', pair_status='exact_pair')
    ]})
    route = book_series.resolve_exercise_route(query='练习1800p17第3题', book_route=dict(series_id='EX', match_status='exact_series'), request=resolved['exercise_scope'])
    assert route['exercise_key'] == 'right'
    assert route['request']['printed_page'] == 18


def test_multi_partial_and_repeat_cache(books, tmp_path, monkeypatch):
    monkeypatch.setattr(common, 'runtime_context_payload', lambda **kwargs: {})
    calls = []
    def query_one(*args):
        calls.append(args)
        result = exact_result(args[5])
        if args[5] == 56:
            result['page_anchor']['match_status'] = 'unmapped'
            finalize_result(result)
        return result
    payload = targets.query_source_targets(query_one=query_one, vault_root=tmp_path, subject='数学', chapter=None, query='练习1800P17第3题和P17第3题，以及高数基础篇P56', topk=3)
    assert len(calls) == 2
    assert payload['status'] == 'partial'
    assert not payload['comparison_allowed']
    assert [x['status'] for x in payload['items']] == ['exact', 'exact', 'blocked']
    payload['items'][0]['result']['teaching_bundle']['source_answer_text'] = 'changed'
    assert payload['items'][1]['result']['teaching_bundle']['source_answer_text'] == '答案'
    assert payload['items'][0]['binding']['status'] == 'exact'


def test_multi_global_override_is_blocked_before_query(books, tmp_path, monkeypatch):
    monkeypatch.setattr(common, 'runtime_context_payload', lambda **kwargs: {})
    payload = targets.query_source_targets(query_one=lambda *a: pytest.fail('must not query'), vault_root=tmp_path, subject='数学', chapter=None, query='练习1800P17第3题和高数基础篇P56', topk=3, printed_page=17)
    assert payload['status'] == 'blocked'
    assert all(x['failure_reason'] == 'global-target-override-ambiguous' for x in payload['items'])


def test_multi_save_rejected_before_any_query(books, monkeypatch):
    import ask_local_knowledge as ask
    monkeypatch.setattr(sys, 'argv', ['ask', '--subject', 'math', '--question', '练习1800P17第3题和高数基础篇P56', '--save'])
    monkeypatch.setattr(ask, 'query_source_targets', lambda **kw: pytest.fail('must not query'))
    with pytest.raises(SystemExit, match='multi-target answers cannot be saved'):
        ask.main()


@pytest.mark.parametrize('alias', ['高等数学', '高数', 'math', '数学'])
def test_math_subject_alias(alias):
    assert common.resolve_subject(alias)[0] == '数学'
