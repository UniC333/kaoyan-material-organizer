from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / 'scripts'
sys.path.insert(0, str(SCRIPTS))
from kaoyan_kb.domain.query import book_identity, read_session, topics
from kaoyan_kb.cli import learner_artifact_runner as runner


def test_query_cache_is_request_local_and_returns_independent_records(tmp_path, monkeypatch):
    path = tmp_path / 'evidence.json'
    path.write_text('{"value":1}', encoding='utf-8')
    reads = []
    original = Path.read_text
    def tracked(self, *args, **kwargs):
        reads.append(self)
        return original(self, *args, **kwargs)
    monkeypatch.setattr(Path, 'read_text', tracked)
    @read_session.with_read_scope
    def nested():
        return read_session.read_json(path)
    @read_session.with_read_scope
    def query():
        first = read_session.read_json(path)
        first['value'] = 99
        return nested()
    assert query() == {'value': 1}
    assert reads == [path]
    path.write_text('{"value":2}', encoding='utf-8')
    assert query() == {'value': 2}
    assert reads == [path, path]


def test_query_cache_resets_on_error(tmp_path):
    path = tmp_path / 'item.json'
    path.write_text('{"value":1}', encoding='utf-8')
    @read_session.with_read_scope
    def fail():
        read_session.read_json(path)
        raise ValueError('stop')
    with pytest.raises(ValueError):
        fail()
    path.write_text('{"value":2}', encoding='utf-8')
    assert read_session.read_json(path)['value'] == 2


def test_book_identity_projection_is_built_once_per_request(tmp_path, monkeypatch):
    monkeypatch.setattr(book_identity, 'kb_layout', lambda: {'indexes': tmp_path})
    path = tmp_path / 'book_series_index.json'
    path.write_text(json.dumps({'series': [{'volumes': [{'book_id': 'A', 'title': '教材'}]}]}), encoding='utf-8')
    builds = []
    original = book_identity._identity_map
    def tracked(index):
        builds.append(index)
        return original(index)
    monkeypatch.setattr(book_identity, '_identity_map', tracked)
    with read_session.read_scope():
        for _ in range(100):
            ids = book_identity.registered_identities('教材')
            assert ids == {'A'}
            ids.clear()
    assert len(builds) == 1
    path.write_text(json.dumps({'series': [{'volumes': [{'book_id': 'B', 'title': '教材'}]}]}), encoding='utf-8')
    with read_session.read_scope():
        assert book_identity.registered_identities('教材') == {'B'}
    assert len(builds) == 2


def test_ask_shares_query_and_citation_reads_but_save_reads_fresh(tmp_path, monkeypatch, capsys):
    import ask_local_knowledge as ask
    import answer_local_question as answer
    path = tmp_path / 'EV.json'
    path.write_text('{"value":1}', encoding='utf-8')
    reads = []
    original = Path.read_text
    def tracked(self, *args, **kwargs):
        if self == path:
            reads.append(self)
        return original(self, *args, **kwargs)
    monkeypatch.setattr(Path, 'read_text', tracked)
    args = argparse.Namespace(vault_root=str(tmp_path), subject='数学', chapter=None, question='导数', topk=3, book_title=None, printed_page=None, exercise_label=None, format='json', save=True, saved_at=None)
    monkeypatch.setattr(ask, 'parse_args', lambda: args)
    monkeypatch.setattr(ask, 'query_exercise_batch', lambda *a, **kw: None)
    monkeypatch.setattr(ask, 'query_knowledge', lambda *a: read_session.read_json(path))
    def contract(result):
        assert result == answer._evidence_from_id({'evidence': tmp_path}, 'EV')
        path.write_text('{"value":2}', encoding='utf-8')
        return result
    monkeypatch.setattr(ask, 'build_answer_contract', contract)
    def save(**kwargs):
        assert read_session.read_json(path) == {'value': 2}
    monkeypatch.setattr(ask, 'save_answer_contract', save)
    assert ask.main() == 0
    assert len(reads) == 2
    assert json.loads(capsys.readouterr().out)['answer'] == {'value': 1}


def test_titles_require_exact_or_unique_registered_alias(tmp_path, monkeypatch):
    monkeypatch.setattr(book_identity, 'kb_layout', lambda: {'indexes': tmp_path})
    assert book_identity.titles_match('教材（2027）', '教材(2027)')
    assert not book_identity.titles_match('教材', '教材2027')
    assert not book_identity.titles_match('高等数学辅导讲义基础篇', '高等数学辅导讲义')
    index = {'series': [
        {'canonical_title':'数学2027', 'aliases':['数学', '高数基础'], 'volumes':[
            {'book_id':'CALC-2027','title':'高数基础2027'},
            {'book_id':'ALG-2027','title':'线代基础2027'},
        ]},
    ]}
    path = tmp_path / 'book_series_index.json'
    path.write_text(json.dumps(index), encoding='utf-8')
    assert book_identity.titles_match('高数基础2027', '高数基础')
    assert not book_identity.titles_match('线代基础2027', '高数基础')
    assert not book_identity.titles_match('高数基础2027', '数学')
    assert topics.evidence_matches_book({'book_id':'CALC-2027'}, '高数基础')
    assert not topics.evidence_matches_book({'book_id':'ALG-2027','book_title':'高数基础2027'}, '高数基础')
    index['series'].append({'canonical_title':'数学2026','aliases':['高数基础'], 'volumes':[{'book_id':'CALC-2026','title':'高数基础2026'}]})
    path.write_text(json.dumps(index), encoding='utf-8')
    assert not book_identity.titles_match('高数基础2027', '高数基础')
    assert not book_identity.titles_match('高数基础2027', '高数基础2026')


@pytest.mark.parametrize('existing', [False, True])
def test_second_artifact_write_failure_restores_exact_pair(tmp_path, monkeypatch, existing):
    vault = tmp_path / 'vault'
    index = vault / runner.INDEX_DIRNAME
    if existing:
        index.mkdir(parents=True)
        (index / 'out.json').write_bytes(b'old json\r\n')
        (index / 'out.md').write_bytes(b'old markdown\r\n')
    before = {str(p.relative_to(tmp_path)): p.read_bytes() if p.is_file() else None for p in tmp_path.rglob('*')}
    args = argparse.Namespace(vault_root=str(vault), format='json')
    def fail(path, content):
        path.write_text('partial markdown', encoding='utf-8')
        raise OSError('second write failed')
    monkeypatch.setattr(runner, 'save_text', fail)
    with pytest.raises(OSError, match='second write failed'):
        runner.run_artifact(lambda: args, lambda root,args: {'id':'new'}, lambda p: 'markdown', 'out.json','out.md',('id',))
    after = {str(p.relative_to(tmp_path)): p.read_bytes() if p.is_file() else None for p in tmp_path.rglob('*')}
    assert after == before


@pytest.mark.parametrize('failure', ['render', 'projection'])
def test_artifact_validation_happens_before_any_write(tmp_path, failure):
    args = argparse.Namespace(vault_root=str(tmp_path / 'vault'), format='json')
    def render(payload):
        if failure == 'render':
            raise ValueError('render failed')
        return 'markdown'
    with pytest.raises((ValueError, KeyError)):
        runner.run_artifact(lambda: args, lambda root,args: {}, render, 'out.json','out.md',('id',))
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize('question', ['什么是导数', 'P63例5', '王道数据结构5.3综合应用题1和11题'])
@pytest.mark.parametrize('existing', [False, True])
def test_query_and_ask_do_not_initialize_storage(tmp_path, question, existing):
    if existing:
        schemas = tmp_path / 'kb' / 'schemas'
        schemas.mkdir(parents=True)
        (schemas / 'evidence.schema.json').write_text('{"description":"keep existing schema"}', encoding='utf-8')
        (tmp_path / 'vault').mkdir()
        (tmp_path / 'vault' / '学习记录.md').write_bytes(b'keep existing notes\r\n')
    config = tmp_path / 'config.json'
    config.write_text(json.dumps({'workspace_root':str(tmp_path),'vault_root':str(tmp_path/'vault'),'kb_root':str(tmp_path/'kb'),'backup_root':str(tmp_path/'backups'),'migration_root':str(tmp_path/'migration'),'python_executable':sys.executable,'ocr_allow_remote':False}), encoding='utf-8')
    env = {k:v for k,v in os.environ.items() if not k.startswith('KAOYAN_')}
    env['PYTHONUTF8'] = '1'
    before = {str(p.relative_to(tmp_path)):p.read_bytes() if p.is_file() else None for p in tmp_path.rglob('*')}
    for command, flag in [('query','--query'),('ask','--question')]:
        subject, book = ('408','王道数据结构') if '王道' in question else ('数学','高等数学辅导讲义基础篇')
        result = subprocess.run([sys.executable,str(SCRIPTS/'kb.py'),'--config',str(config),command,'--subject',subject,'--book-title',book,flag,question,'--format','json'],cwd=tmp_path,env=env,text=True,capture_output=True,encoding='utf-8')
        assert result.returncode == 0, result.stderr
        json.loads(result.stdout)
        assert {str(p.relative_to(tmp_path)):p.read_bytes() if p.is_file() else None for p in tmp_path.rglob('*')} == before, command


def test_retrieval_reuses_records_and_keeps_output(tmp_path, monkeypatch):
    import retrieve_knowledge as retrieval
    import common
    layout = {name: tmp_path / name for name in ("indexes", "evidence", "claims")}
    for path in layout.values():
        path.mkdir()
    docs = [{"doc_id": "CL", "doc_type": "claim", "entity_id": "CL", "tokens": ["derivative"]}]
    for path, payload in [
        (layout["indexes"] / "search_documents.json", {"documents": docs}),
        (layout["indexes"] / "inverted_index.json", {"terms": {"derivative": ["CL"]}}),
        (layout["claims"] / "CL.json", {"evidence_ids": ["EV"]}),
        (layout["evidence"] / "EV.json", {"content": "definition"}),
    ]:
        path.write_text(json.dumps(payload), encoding="utf-8")
    original = Path.read_text
    counts = []
    def tracked(path, *args, **kwargs):
        counts.append(path)
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, "read_text", tracked)
    cached = retrieval.load_json
    monkeypatch.setattr(retrieval, "load_json", common.load_json)
    baseline = retrieval.retrieve(layout, subject=None, query="derivative", topk=3)
    assert len(counts) == 5
    counts.clear()
    monkeypatch.setattr(retrieval, "load_json", cached)
    with read_session.read_scope():
        actual = retrieval.retrieve(layout, subject=None, query="derivative", topk=3)
        assert retrieval.retrieve(layout, subject=None, query="derivative", topk=3) == actual
    assert actual == baseline
    assert len(counts) == 4
