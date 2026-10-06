from __future__ import annotations

import json
import sys
import pytest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from kaoyan_kb.domain import exercise_batch, exercise_locator
import query_local_knowledge as query_module

ORIGINAL_WRONG_ANSWERS = (
    '刚整理了一下错题，数据结构146页起前20题的错误：1D,7C19A。'
    '19题我没问题，这个题只花两个结点的二叉树就没问题了'
)


def test_compact_wrong_answers_keep_quantity_and_page_start_separate():
    parsed = exercise_batch.parse_exercise_batch_request(ORIGINAL_WRONG_ANSWERS)
    assert parsed['is_batch'] is True
    assert [(item['exercise_label'], item['requested_option']) for item in parsed['items']] == [
        ('01', 'D'), ('07', 'C'), ('19', 'A'),
    ]
    assert not any(item['emphasis'] for item in parsed['items'])
    assert parsed['exercise_range'] == {'start': 1, 'end': 20}
    assert parsed['page_start'] == {'number': 146, 'semantics': 'section_start'}
    assert parsed['page_range'] == {}


@pytest.mark.parametrize('text', [
    '第1题没懂，表达式为7C19A',
    '选项里出现2A3B + 4C',
    '错题：2A3B + 4C',
    '错题：2A3B^2',
    '错题：2A3Bx',
])
def test_compact_formula_is_not_a_wrong_answer_list(text):
    assert exercise_batch.parse_exercise_batch_request(text)['is_batch'] is False


def test_quantity_is_not_added_as_a_focus_question():
    parsed = exercise_batch.parse_exercise_batch_request('前20题没懂，错题：1D7C，重点讲第7题')
    assert [item['exercise_label'] for item in parsed['items']] == ['01', '07']
    assert [item['exercise_label'] for item in parsed['items'] if item['emphasis']] == ['07']


@pytest.mark.parametrize('query', [
    '王道数据结构 8.4 前20题都做对了，错题：21A,28D',
    '王道数据结构 8.4 错题：21A,28D。前20题已经做完了',
    '王道数据结构 8.4 前20题没懂，错题：21A,28D，重点讲第28题',
    '王道数据结构 8.4 前20题，错题：21A,28D',
    '王道数据结构8.4，只讲前20题以外的错题：21A,28D',
    '王道数据结构8.4，仅讲前20题之外的错题：21A,28D',
    '王道数据结构8.4，限定在前20题外的错题：21A,28D',
    '王道数据结构8.4，范围为前 20 道题 以外的错题：21A,28D',
])
def test_feedback_quantity_does_not_block_explicit_wrong_answers(monkeypatch, tmp_path, query):
    monkeypatch.setattr(exercise_batch, '_active_pdf_sources', lambda _: {'SRC': {}})
    monkeypatch.setattr(exercise_batch, 'load_exercise_locator_index', lambda: {'relations': [
        {'source_id': 'SRC', 'section_root': '8.4', 'category': 'single-choice',
         'exercise_label': label, 'relation_status': 'exact', 'question_printed_pages': [80]}
        for label in ('21', '28')
    ]})
    parsed = exercise_batch.parse_exercise_batch_request(query)
    assert parsed['exercise_range'] == {}
    assert [item['exercise_label'] for item in parsed['items']] == ['21', '28']
    resolved = exercise_batch.resolve_exercise_batch_targets(
        subject='408', book_title='王道数据结构', chapter=None, query=query, parsed=parsed,
    )
    assert [item['status'] for item in resolved['targets']] == ['exact', 'exact']
    monkeypatch.setattr(query_module, 'resolve_current_task_book', lambda **_: {
        'status': 'exact', 'source': 'query', 'book_title': '王道数据结构',
    })
    monkeypatch.setattr(query_module, 'query_knowledge', lambda *args: _exact_query_result(args[-1]))
    monkeypatch.setattr(query_module, 'runtime_context_payload', lambda **_: {})
    payload = query_module.query_exercise_batch(tmp_path, '408', None, query, 3, view='teaching')
    assert payload['batch_status'] == 'exact'
    assert payload['request_resolution']['exercise_range'] == {}
    assert payload['request_resolution']['original_query'] == query


@pytest.mark.parametrize('scope', [
    '前30题的错误', '前30题的错题', '前30题的选错的选项',
    '只讲前30题', '仅讲前30题', '只讲解前30题',
    '限定在前30题', '限于前30题', '范围为前30题',
    '前 30 道题的错误',
])
def test_explicit_quantity_is_selected_after_progress_feedback(scope):
    parsed = exercise_batch.parse_exercise_batch_request(
        f'王道数据结构 8.4 前20题都做对了，{scope}：21A,28D',
    )
    assert parsed['exercise_range'] == {'start': 1, 'end': 30}
    assert [item['exercise_label'] for item in parsed['items']] == ['21', '28']


@pytest.mark.parametrize('query', [
    '前20题的错误有这些：1D,7C',
    '前20题的错题 1D,7C',
    '前20题的错误如下：1D,7C',
    '昨天前20题的错误：1D,7C',
])
def test_explicit_wrong_answer_list_keeps_its_own_quantity(query):
    parsed = exercise_batch.parse_exercise_batch_request(query)
    assert parsed['exercise_range'] == {'start': 1, 'end': 20}
    assert [item['exercise_label'] for item in parsed['items']] == ['01', '07']


@pytest.mark.parametrize('query,expected', [
    ('只讲前30题，错题：21A,28D。前20题已做完', {'start': 1, 'end': 30}),
    ('只讲前30题，前30题的错题：21A,28D', {'start': 1, 'end': 30}),
    ('只讲前20题，仅讲前30题，错题：21A,28D', {}),
    ('前20题的错误已经订正完了，错题：21A,28D', {}),
    ('前20题的错题我没问题，错题：21A,28D', {}),
    ('之前只讲前20题，现在错题：21A,28D', {}),
    ('这次不只讲前20题，错题：21A,28D', {}),
    ('不限于前20题，错题：21A,28D', {}),
    ('前20题的错误：已经订正完了，错题：21A,28D', {}),
])
def test_quantity_scope_ignores_feedback_history_and_conflicting_limits(query, expected):
    parsed = exercise_batch.parse_exercise_batch_request(query)
    assert parsed['exercise_range'] == expected
    assert [item['exercise_label'] for item in parsed['items']] == ['21', '28']


def _start_page_fixture(monkeypatch, tmp_path):
    anchors = tmp_path / 'pdf_book_anchors'
    anchors.mkdir()
    (anchors / 'SRC.json').write_text(json.dumps({'anchors': [
        {'title': '8.4.2 本节试题精选', 'page_start': 93, 'anchor_type': 'section'},
    ]}, ensure_ascii=False), encoding='utf-8')
    monkeypatch.setattr(exercise_batch, 'kb_layout', lambda: {'indexes': tmp_path})
    monkeypatch.setattr(exercise_batch, '_active_pdf_sources', lambda _: {'SRC': {}})
    monkeypatch.setattr(exercise_batch, 'resolve_section_anchor', lambda **_: {'status': 'not_requested', 'section_root': ''})
    monkeypatch.setattr(exercise_batch, 'load_page_locator_index', lambda: {'entries': [
        {'source_asset_kind': 'pdf', 'subject': '408', 'book_title': '测试教材', 'source_id': 'SRC', 'printed_page': 80, 'pdf_page': 93},
    ]})
    monkeypatch.setattr(exercise_batch, 'load_exercise_locator_index', lambda: {'relations': [
        {'source_id': 'SRC', 'section_root': section, 'category': 'single-choice', 'exercise_label': label,
         'relation_status': 'exact', 'question_printed_pages': [page], 'question_pdf_pages': [page + 13]}
        for section, label, page in [('8.4', '01', 80), ('8.4', '07', 81), ('8.4', '19', 82), ('8.5', '07', 85)]
    ]})
    query = '测试教材80页起前20题的错误：1D,7C19A'
    return query, exercise_batch.parse_exercise_batch_request(query), anchors / 'SRC.json'


def test_page_start_uses_formal_heading_and_allows_later_question_pages(monkeypatch, tmp_path):
    query, parsed, _ = _start_page_fixture(monkeypatch, tmp_path)
    resolved = exercise_batch.resolve_exercise_batch_targets(
        subject='408', book_title='测试教材', chapter=None, query=query, parsed=parsed,
    )
    assert resolved['section_root'] == '8.4'
    assert resolved['page_start_resolution']['status'] == 'exact'
    assert [item['status'] for item in resolved['targets']] == ['exact'] * 3
    assert [item['printed_page'] for item in resolved['targets']] == [80, 81, 82]


@pytest.mark.parametrize('query,expected_page', [
    ('昨天80页起的前20题都做对了，今天90页起前20题的错误：1D,7C', 90),
    ('昨天90页起的前20题都做对了，今天80页起前20题的错误：1D,7C', 80),
    ('今天90页起前20题的错误：1D,7C。昨天80页起的前20题都做对了', 90),
    ('昨天80页起的前20题都做对了今天90页起前20题的错误：1D,7C', 90),
    ('昨天90页起前20题的错误：1D,7C', 90),
    ('昨天90页起，前20题的错误：1D,7C', 90),
    ('昨天90页起前20题的错误有这些：1D,7C', 90),
    ('昨天90页起前20题的错误如下：1D,7C', 90),
    ('昨天90页起，重点讲第1、7题', 90),
    ('90页起，90页开始，错题：1D,7C', 90),
    ('80页起或90页起，错题：1D,7C', None),
    ('80页起，90页起，错题：1D,7C', None),
])
def test_page_start_belongs_to_current_request_or_blocks_ambiguity(monkeypatch, tmp_path, query, expected_page):
    _, _, anchors = _start_page_fixture(monkeypatch, tmp_path)
    anchors.write_text(json.dumps({'anchors': [
        {'title': f'{section}.2 本节试题精选', 'page_start': page + 13}
        for section, page in [('8.4', 80), ('8.5', 90)]
    ]}, ensure_ascii=False), encoding='utf-8')
    monkeypatch.setattr(exercise_batch, 'load_page_locator_index', lambda: {'entries': [
        {'source_asset_kind': 'pdf', 'subject': '408', 'book_title': '测试教材',
         'source_id': 'SRC', 'printed_page': page, 'pdf_page': page + 13}
        for page in (80, 90)
    ]})
    monkeypatch.setattr(exercise_batch, 'load_exercise_locator_index', lambda: {'relations': [
        {'source_id': 'SRC', 'section_root': section, 'category': 'single-choice',
         'exercise_label': label, 'relation_status': 'exact',
         'question_printed_pages': [page + offset], 'question_pdf_pages': [page + offset + 13]}
        for section, page in [('8.4', 80), ('8.5', 90)]
        for label, offset in [('01', 0), ('07', 1)]
    ]})
    parsed = exercise_batch.parse_exercise_batch_request(query)
    if expected_page is not None:
        assert parsed['page_start'] == {'number': expected_page, 'semantics': 'section_start'}
    else:
        assert parsed['page_start']['status'] == 'ambiguous'
    resolved = exercise_batch.resolve_exercise_batch_targets(
        subject='408', book_title='测试教材', chapter=None, query=query, parsed=parsed,
    )
    calls = []
    monkeypatch.setattr(query_module, 'resolve_current_task_book', lambda **_: {
        'status': 'exact', 'source': 'query', 'book_title': '测试教材',
    })
    def query_one(*args):
        calls.append(args)
        return _exact_query_result(args[-1])
    monkeypatch.setattr(query_module, 'query_knowledge', query_one)
    monkeypatch.setattr(query_module, 'runtime_context_payload', lambda **_: {})
    payload = query_module.query_exercise_batch(tmp_path, '408', None, query, 3, view='teaching')
    assert payload['request_resolution']['original_query'] == query
    if expected_page is not None:
        section = '8.5' if expected_page == 90 else '8.4'
        assert resolved['section_root'] == section
        assert [item['status'] for item in resolved['targets']] == ['exact', 'exact']
        assert payload['batch_status'] == 'exact'
        assert all(args[2] == section for args in calls)
        for item, offset in zip(payload['items'], (0, 1)):
            assert item['textbook_location']['question_printed_pages'] == [expected_page + offset]
            assert item['textbook_location']['question_pdf_pages'] == [expected_page + offset + 13]
    else:
        assert resolved['reason'] == 'page-start-ambiguous'
        assert payload['batch_status'] == 'blocked'
        assert calls == []
        assert all(not item['answer_grounding']['can_conclude'] for item in payload['items'])
        assert all('起始页' in item['answer_grounding']['failure_reason'] for item in payload['items'])


@pytest.mark.parametrize('requested,status', [
    ('第8章', 'exact'), ('8.4', 'exact'),
    ('8.5', 'blocked'), ('第9章', 'blocked'), ('第80章', 'blocked'),
])
@pytest.mark.parametrize('via_query', [False, True])
def test_page_start_refines_only_compatible_real_section_scope(monkeypatch, tmp_path, requested, status, via_query):
    query, _, _ = _start_page_fixture(monkeypatch, tmp_path)
    monkeypatch.setattr(exercise_batch, 'resolve_section_anchor', exercise_locator.resolve_section_anchor)
    if via_query:
        query = f'{requested} {query}'
    resolved = exercise_batch.resolve_exercise_batch_targets(
        subject='408', book_title='测试教材', chapter=None if via_query else requested,
        query=query, parsed=exercise_batch.parse_exercise_batch_request(query),
    )
    assert [item['status'] for item in resolved['targets']] == [status] * 3
    if status == 'exact':
        assert resolved['section_root'] == '8.4'
        assert resolved['page_start_resolution']['section_root'] == '8.4'
        assert [item['printed_page'] for item in resolved['targets']] == [80, 81, 82]
    else:
        assert [item['reason'] for item in resolved['targets']] == ['section-start-anchor-conflict'] * 3


def test_page_start_does_not_treat_chapter_as_string_prefix(monkeypatch, tmp_path):
    query, parsed, path = _start_page_fixture(monkeypatch, tmp_path)
    path.write_text(json.dumps({'anchors': [
        {'title': '80.4.2 本节试题精选', 'page_start': 93, 'anchor_type': 'section'},
    ]}, ensure_ascii=False), encoding='utf-8')
    monkeypatch.setattr(exercise_batch, 'resolve_section_anchor', exercise_locator.resolve_section_anchor)
    resolved = exercise_batch.resolve_exercise_batch_targets(
        subject='408', book_title='测试教材', chapter='第8章', query=query, parsed=parsed,
    )
    assert [item['reason'] for item in resolved['targets']] == ['section-start-anchor-conflict'] * 3


@pytest.mark.parametrize('anchors,reason', [
    ([], 'section-start-anchor-not-found'),
    ([{'title': '8.4.3 答案与解析', 'page_start': 93}], 'section-start-anchor-not-found'),
    ([{'title': '8.4.2 本节试题精选', 'page_start': 92}], 'section-start-anchor-not-found'),
    ([{'title': '8.4.2 本节试题精选', 'page_start': 93},
      {'title': '8.5.2 本节试题精选', 'page_start': 93}], 'section-anchor-ambiguous'),
])
def test_page_start_missing_or_ambiguous_heading_blocks_batch(monkeypatch, tmp_path, anchors, reason):
    query, parsed, path = _start_page_fixture(monkeypatch, tmp_path)
    path.write_text(json.dumps({'anchors': anchors}, ensure_ascii=False), encoding='utf-8')
    resolved = exercise_batch.resolve_exercise_batch_targets(
        subject='408', book_title='测试教材', chapter=None, query=query, parsed=parsed,
    )
    assert [item['reason'] for item in resolved['targets']] == [reason] * 3


def test_page_start_conflict_with_explicit_section_blocks_batch(monkeypatch, tmp_path):
    query, parsed, _ = _start_page_fixture(monkeypatch, tmp_path)
    monkeypatch.setattr(exercise_batch, 'resolve_section_anchor', lambda **_: {'status': 'not_applicable', 'section_root': '8.5'})
    resolved = exercise_batch.resolve_exercise_batch_targets(
        subject='408', book_title='测试教材', chapter='8.5', query=query, parsed=parsed,
    )
    assert [item['reason'] for item in resolved['targets']] == ['section-start-anchor-conflict'] * 3


def test_question_outside_quantity_range_does_not_expand_scope(monkeypatch, tmp_path):
    query, _, _ = _start_page_fixture(monkeypatch, tmp_path)
    query = query.replace('前20题', '前10题')
    resolved = exercise_batch.resolve_exercise_batch_targets(
        subject='408', book_title='测试教材', chapter=None, query=query,
        parsed=exercise_batch.parse_exercise_batch_request(query),
    )
    assert [item['status'] for item in resolved['targets']] == ['exact', 'exact', 'blocked']
    assert resolved['targets'][2]['reason'] == 'exercise-outside-requested-range'


@pytest.mark.parametrize('entries,reason', [
    ([], 'printed-page-range-not-uniquely-mapped'),
    ([{'source_asset_kind': 'pdf', 'subject': '408', 'book_title': '测试教材',
       'source_id': source, 'printed_page': 80, 'pdf_page': 93} for source in ('SRC', 'OTHER')],
     'printed-page-range-not-uniquely-mapped'),
])
def test_page_start_missing_or_conflicting_mapping_cannot_fall_back(monkeypatch, tmp_path, entries, reason):
    query, parsed, _ = _start_page_fixture(monkeypatch, tmp_path)
    monkeypatch.setattr(exercise_batch, '_active_pdf_sources', lambda _: {'SRC': {}, 'OTHER': {}})
    monkeypatch.setattr(exercise_batch, 'load_page_locator_index', lambda: {'entries': entries})
    resolved = exercise_batch.resolve_exercise_batch_targets(
        subject='408', book_title='测试教材', chapter=None, query=query, parsed=parsed,
    )
    assert [item['reason'] for item in resolved['targets']] == [reason] * 3


def test_page_start_stale_relation_index_remains_blocked(monkeypatch, tmp_path):
    query, parsed, _ = _start_page_fixture(monkeypatch, tmp_path)
    monkeypatch.setattr(exercise_batch, 'load_exercise_locator_index', lambda: {
        '_availability': {'available': False, 'reason': 'exercise_locator_index_stale'},
    })
    resolved = exercise_batch.resolve_exercise_batch_targets(
        subject='408', book_title='测试教材', chapter=None, query=query, parsed=parsed,
    )
    assert [item['reason'] for item in resolved['targets']] == ['exercise_locator_index_stale'] * 3


@pytest.mark.parametrize('quantity,status', [(20, 'exact'), (10, 'partial')])
def test_batch_preserves_original_feedback_and_range_blockers(monkeypatch, tmp_path, quantity, status):
    query, _, _ = _start_page_fixture(monkeypatch, tmp_path)
    query = query.replace('前20题', f'前{quantity}题') + '。19题我没问题，只需画两个结点。'
    book_queries, calls = [], []
    def resolve_book(**kwargs):
        book_queries.append(kwargs['query'])
        return {'status': 'exact', 'source': 'query', 'book_title': '测试教材'}
    def query_one(*args):
        calls.append(args)
        return _exact_query_result(args[-1])
    monkeypatch.setattr(query_module, 'resolve_current_task_book', resolve_book)
    monkeypatch.setattr(query_module, 'query_knowledge', query_one)
    monkeypatch.setattr(query_module, 'runtime_context_payload', lambda **_: {})
    payload = query_module.query_exercise_batch(tmp_path, '408', None, query, 3, view='teaching')
    assert payload['batch_status'] == status
    assert payload['request_resolution']['original_query'] == query
    assert payload['request_resolution']['page_start']['number'] == 80
    assert payload['request_resolution']['exercise_range'] == {'start': 1, 'end': quantity}
    assert book_queries == [query]
    assert all(args[2] == '8.4' and args[5] is None for args in calls)
    assert [item['exercise_label'] for item in payload['items']] == ['01', '07', '19']
    assert not any(item['emphasis'] for item in payload['items'])
    if status == 'partial':
        assert len(calls) == 2
        assert payload['items'][2]['teaching_bundle']['problem_text'] == ''
        assert payload['items'][2]['answer_grounding']['can_conclude'] is False


@pytest.mark.parametrize('text', [
    '第10页第1题第（1）问：lim_{x→0}(1/sin^2x - cos^2x/x^2)。',
    '第10页第一问，没理解 lim_{x→0}(1/sin^2x - cos^2x/x^2)',
    '第10页第1题没理解，式子是 2x + 3y',
    '第10页第1题，式子为 sin^ 2X 和 cos^2X',
    '第10页第1题，没懂 2A + 3B',
    '第10页第一问没理解，1/sin²x - cos²x/x²',
    '选择题第1题，题干 sin(2x) 和 cos(3y)',
])
def test_formula_and_page_numbers_do_not_create_exercise_batches(text):
    parsed = exercise_batch.parse_exercise_batch_request(text)
    assert parsed['is_batch'] is False
    assert all(item['exercise_label'] == '01' and not item['requested_option'] for item in parsed['items'])


def test_explicit_batch_survives_formula_and_focus_text():
    parsed = exercise_batch.parse_exercise_batch_request('第1、8题没懂，重点讲 sin^2x + 3y')
    assert parsed['is_batch'] is True
    assert [item['exercise_label'] for item in parsed['items']] == ['01', '08']


def test_lowercase_choice_options_with_explicit_context():
    parsed = exercise_batch.parse_exercise_batch_request('选项 1b,8d')
    assert [item['requested_option'] for item in parsed['items']] == ['B', 'D']


def _published_pdf_evidence(evidence_id: str, pdf_page: int, printed_page: int) -> dict:
    span = {
        "source_id": "SRC",
        "file_id": "FILE-PDF",
        "source_file_sha256": "pdf-sha",
        "locator": {"page_start": pdf_page, "page_end": pdf_page, "image_start": pdf_page, "image_end": pdf_page},
    }
    return {
        "evidence_id": evidence_id,
        "evidence_key": f"{evidence_id}-key",
        "source_id": "SRC",
        "chapter_id": "CH-PDF",
        "chunk_id": f"CHUNK-{evidence_id}",
        "origin_type": "pdf_page_ocr",
        "verification_status": "reviewed",
        "review_status": "accepted",
        "source_grounded": True,
        "mapping_status": "mapped",
        "pdf_page": pdf_page,
        "printed_page": printed_page,
        "source_spans": [span],
        "provenance": {"origin_type": "pdf_page_ocr", "verification_status": "reviewed", "source_grounded": True, "source_spans": [span]},
    }


def test_parse_original_wrong_answer_batch_preserves_order_options_and_focus() -> None:
    parsed = exercise_batch.parse_exercise_batch_request(
        "刚刚做完了数据结构133～135页的选择，选错的选项有这些：1B,8D,10A,16A,18A,19C,25A,28B，另外，12和28两个题没理解透题意，着重多一点细节"
    )

    assert parsed["is_batch"] is True
    assert parsed["page_range"] == {"start": 133, "end": 135, "semantics": "question_scope"}
    assert parsed["exercise_category"] == "single-choice"
    assert [item["exercise_label"] for item in parsed["items"]] == ["01", "08", "10", "16", "18", "19", "25", "28", "12"]
    assert {item["exercise_label"]: item["requested_option"] for item in parsed["items"] if item["requested_option"]} == {
        "01": "B", "08": "D", "10": "A", "16": "A", "18": "A", "19": "C", "25": "A", "28": "B"
    }
    assert {item["exercise_label"] for item in parsed["items"] if item["emphasis"]} == {"12", "28"}


def test_parse_section_batch_does_not_treat_section_number_as_exercise() -> None:
    parsed = exercise_batch.parse_exercise_batch_request("王道数据结构 5.2 单选第1、8、28题")

    assert parsed["is_batch"] is True
    assert parsed["page_range"] == {}
    assert [item["exercise_label"] for item in parsed["items"]] == ["01", "08", "28"]


def test_parse_invalid_option_is_retained_as_a_blocked_item() -> None:
    parsed = exercise_batch.parse_exercise_batch_request("王道数据结构 5.2 单选第1E、8D题")

    assert [item["exercise_label"] for item in parsed["items"]] == ["08", "01"]
    invalid = next(item for item in parsed["items"] if item["exercise_label"] == "01")
    assert invalid == {"exercise_label": "01", "requested_option": "E", "emphasis": False, "parse_status": "invalid_option"}


def test_page_range_requires_contiguous_formal_pdf_mapping(monkeypatch) -> None:
    monkeypatch.setattr(
        exercise_batch,
        "load_page_locator_index",
        lambda: {
            "_availability": {"available": True},
            "entries": [
                {"source_asset_kind": "pdf", "subject": "408", "book_title": "王道数据结构", "source_id": "SRC", "printed_page": 133, "pdf_page": 145},
                {"source_asset_kind": "pdf", "subject": "408", "book_title": "王道数据结构", "source_id": "SRC", "printed_page": 134, "pdf_page": 147},
            ],
        },
    )

    resolved = exercise_batch._resolve_range_source(
        subject="408",
        book_title="王道数据结构",
        page_range={"start": 133, "end": 134},
        allowed_source_ids={"SRC"},
    )

    assert resolved["status"] == "unavailable"
    assert resolved["reason"] == "printed-page-range-not-contiguous"


def test_stale_relation_index_blocks_each_item(monkeypatch) -> None:
    monkeypatch.setattr(exercise_batch, "_active_pdf_sources", lambda _: {"SRC": {"source_id": "SRC"}})
    monkeypatch.setattr(exercise_batch, "resolve_section_anchor", lambda **_: {"status": "exact", "section_root": "5.2"})
    monkeypatch.setattr(
        exercise_batch,
        "load_exercise_locator_index",
        lambda: {"_availability": {"available": False, "reason": "exercise_locator_index_stale"}},
    )
    parsed = exercise_batch.parse_exercise_batch_request("王道数据结构 5.2 单选第1、8题")

    resolved = exercise_batch.resolve_exercise_batch_targets(
        subject="408", book_title="王道数据结构", chapter=None, query="5.2 单选第1、8题", parsed=parsed
    )

    assert [item["reason"] for item in resolved["targets"]] == ["exercise_locator_index_stale"] * 2


def test_duplicate_label_across_sections_is_ambiguous_without_section(monkeypatch) -> None:
    monkeypatch.setattr(exercise_batch, "_active_pdf_sources", lambda _: {"SRC": {"source_id": "SRC"}})
    monkeypatch.setattr(exercise_batch, "resolve_section_anchor", lambda **_: {"status": "not_found", "section_root": ""})
    monkeypatch.setattr(
        exercise_batch,
        "load_exercise_locator_index",
        lambda: {
            "_availability": {"available": True},
            "relations": [
                {"source_id": "SRC", "section_root": section, "category": "single-choice", "exercise_label": label, "relation_status": "exact", "question_printed_pages": [page]}
                for section, label, page in [("5.2", "01", 133), ("5.3", "01", 139), ("5.2", "08", 133)]
            ],
        },
    )
    parsed = exercise_batch.parse_exercise_batch_request("王道数据结构单选第1、8题")

    resolved = exercise_batch.resolve_exercise_batch_targets(
        subject="408", book_title="王道数据结构", chapter=None, query="王道数据结构单选第1、8题", parsed=parsed
    )

    assert resolved["targets"][0]["reason"] == "exercise-relation-ambiguous"
    assert resolved["targets"][1]["status"] == "exact"


def test_same_title_source_conflict_blocks_cross_source_batch(monkeypatch) -> None:
    monkeypatch.setattr(exercise_batch, "_active_pdf_sources", lambda _: {"SRC-A": {}, "SRC-B": {}})
    monkeypatch.setattr(exercise_batch, "resolve_section_anchor", lambda **_: {"status": "exact", "section_root": "5.2"})
    monkeypatch.setattr(
        exercise_batch,
        "load_exercise_locator_index",
        lambda: {
            "_availability": {"available": True},
            "relations": [
                {"source_id": "SRC-A", "section_root": "5.2", "category": "single-choice", "exercise_label": "01", "relation_status": "exact", "question_printed_pages": [133]},
                {"source_id": "SRC-B", "section_root": "5.2", "category": "single-choice", "exercise_label": "08", "relation_status": "exact", "question_printed_pages": [133]},
            ],
        },
    )
    parsed = exercise_batch.parse_exercise_batch_request("王道数据结构 5.2 单选第1、8题")

    resolved = exercise_batch.resolve_exercise_batch_targets(
        subject="408", book_title="王道数据结构", chapter=None, query="王道数据结构 5.2 单选第1、8题", parsed=parsed
    )

    assert [item["reason"] for item in resolved["targets"]] == ["book-source-ambiguous"] * 2


def test_relation_enrichment_requires_independent_printed_pages() -> None:
    relation = {
        "relation_id": "EXR-SRC-5.2-single-choice-01",
        "relation_status": "exact",
        "source_id": "SRC",
        "question_evidence_ids": ["EV-Q"],
        "question_pdf_pages": [145],
        "answer_evidence_ids": ["EV-A"],
        "answer_pdf_pages": [147],
    }
    evidences = {
        "EV-Q": _published_pdf_evidence("EV-Q", 145, 133),
        "EV-A": _published_pdf_evidence("EV-A", 147, 135),
    }

    enriched = exercise_locator._with_relation_printed_pages(relation, evidences)
    assert enriched["relation_status"] == "exact"
    assert enriched["question_printed_pages"] == [133]
    assert enriched["answer_printed_pages"] == [135]

    del evidences["EV-A"]["printed_page"]
    blocked = exercise_locator._with_relation_printed_pages(relation, evidences)
    assert blocked["relation_status"] == "needs_review"
    assert blocked["mapping_failure_reason"] == "formal-question-or-answer-page-mapping-missing"


def test_shared_relation_assembler_returns_both_page_systems(monkeypatch, tmp_path: Path) -> None:
    evidence_root = tmp_path / "evidence"
    evidence_root.mkdir()
    (evidence_root / "EV-Q.json").write_text(json.dumps({"evidence_id": "EV-Q", "content": "# 一、单项选择题\n01. 题干\n02. 下一题"}), encoding="utf-8")
    (evidence_root / "EV-A.json").write_text(json.dumps({"evidence_id": "EV-A", "content": "# 一、单项选择题\n01. C\n解析\n02. D"}), encoding="utf-8")
    monkeypatch.setattr(exercise_locator, "ensure_kb_layout", lambda: {"evidence": evidence_root})
    monkeypatch.setattr(exercise_locator, "kb_layout", lambda: {"evidence": evidence_root})
    relation = {
        "relation_id": "EXR-SRC-5.2-single-choice-01",
        "relation_status": "exact",
        "source_id": "SRC",
        "section_root": "5.2",
        "category": "single-choice",
        "exercise_label": "01",
        "question_evidence_ids": ["EV-Q"],
        "question_pdf_pages": [145],
        "question_printed_pages": [133],
        "answer_evidence_ids": ["EV-A"],
        "answer_pdf_pages": [147],
        "answer_printed_pages": [135],
    }

    anchor, evidences = exercise_locator.assemble_exact_relation(relation)

    assert anchor["status"] == "exact_answer_evidence"
    assert anchor["question_printed_pages"] == [133]
    assert anchor["question_pdf_pages"] == [145]
    assert anchor["answer_printed_pages"] == [135]
    assert anchor["answer_pdf_pages"] == [147]
    assert anchor["question_content"] == "01. 题干"
    assert anchor["answer_content"] == "01. C\n解析"
    assert {item["evidence_id"] for item in evidences} == {"EV-Q", "EV-A"}


def _exact_query_result(label: str) -> dict:
    grounding = {
        "required": True,
        "status": "exact_answer",
        "can_conclude": True,
        "problem": {"evidence_ids": [f"EV-Q-{label}"], "printed_pages": [133], "pdf_pages": [145], "content": "题干"},
        "solution": {"evidence_ids": [f"EV-A-{label}"], "printed_pages": [135], "pdf_pages": [147], "content": "答案"},
        "failure_reason": "",
        "next_action": "",
    }
    return {
        "book_title": "王道数据结构",
        "request_resolution": {"source_request_kind": "exercise"},
        "answer_grounding": grounding,
        "teaching_bundle": {
            "status": "exact", "problem_text": "题干", "source_answer_text": "答案", "requested_option": "", "exercise_label": label,
            "citations": {"problem_evidence_ids": [f"EV-Q-{label}"], "solution_evidence_ids": [f"EV-A-{label}"], "problem_pdf_pages": [145], "solution_pdf_pages": [147]},
            "failure_reason": "",
        },
        "references": [{"evidence_id": f"EV-Q-{label}"}, {"evidence_id": f"EV-A-{label}"}],
        "textbook_location": {"status": "exact", "exercise_label": label, "printed_pages": [133]},
        "page_verification": {},
    }


def test_batch_query_keeps_exact_items_when_one_relation_is_blocked(monkeypatch, tmp_path: Path) -> None:
    relation = {"relation_id": "EXR-01", "question_printed_pages": [160]}
    monkeypatch.setattr(
        query_module,
        "resolve_exercise_batch_targets",
        lambda **_: {
            "section_root": "5.3", "source_id": "SRC", "targets": [
                {"exercise_label": "01", "requested_option": "", "emphasis": False, "status": "exact", "printed_page": 160, "relation": relation},
                {"exercise_label": "11", "requested_option": "", "emphasis": False, "status": "blocked", "reason": "exercise-relation-needs-review", "relation": {}},
            ],
        },
    )
    monkeypatch.setattr(query_module, "query_knowledge", lambda *args, **kwargs: _exact_query_result("01"))
    monkeypatch.setattr(query_module, "runtime_context_payload", lambda **_: {})

    payload = query_module.query_exercise_batch(
        tmp_path,
        "408",
        "5.3",
        "王道数据结构 5.3 综合题第1、11题",
        3,
        "王道数据结构",
        view="teaching",
    )

    assert payload is not None
    assert payload["batch_contract_version"] == "m6.exercise-batch.v1"
    assert payload["batch_status"] == "partial"
    assert payload["summary"] == {"requested_count": 2, "exact_count": 1, "blocked_count": 1}
    assert payload["items"][0]["teaching_bundle"]["status"] == "exact"
    assert payload["items"][0]["textbook_location"]["question_printed_pages"] == [160]
    assert payload["items"][1]["teaching_bundle"]["status"] == "blocked"
    assert payload["items"][1]["textbook_location"]["question_pdf_pages"] == []
    assert payload["items"][1]["teaching_bundle"]["problem_text"] == ""
    assert payload["items"][1]["teaching_bundle"]["source_answer_text"] == ""
