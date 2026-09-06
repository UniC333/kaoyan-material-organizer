from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts'))
from kaoyan_kb.domain import artifact_support, chapter_text, pdf_sources
from kaoyan_kb.storage import snapshot_support
from kaoyan_kb.cli import review_handlers, maintain_handlers
from kaoyan_kb.cli.learner_commands import add_learner_commands, dispatch_learner


def test_artifact_values_and_failures(tmp_path):
    assert artifact_support.dedupe_strings([' a ', '', 'a', None, 0, 'b']) == ['a', 'None', '0', 'b']
    assert artifact_support.load_overrides(None) == {'manual_locks': [], 'operator_overrides': []}
    p = tmp_path / 'input.json'
    p.write_text('{"manual_locks": ["a"], "ignored": true}', encoding='utf-8')
    assert artifact_support.load_overrides(str(p)) == {'manual_locks': ['a'], 'operator_overrides': []}
    assert artifact_support.load_required_artifact(tmp_path, p.name)['ignored'] is True
    p.write_text('{', encoding='utf-8')
    with pytest.raises(json.JSONDecodeError):
        artifact_support.load_overrides(str(p))
    with pytest.raises(SystemExit, match='missing required artifact'):
        artifact_support.load_required_artifact(tmp_path, p.name)
    with pytest.raises(FileNotFoundError):
        artifact_support.load_overrides(str(tmp_path / 'missing'))


@pytest.mark.parametrize('section,chapter,expected', [('', '第一章图片批次验收', '第一章'), (' 待细化 题目段 +', '', '题型训练'), ('解析段 图片批次', '', '题解与解析'), (None, '', '本章')])
def test_chapter_text(section, chapter, expected):
    assert chapter_text.clean_section_name(section, chapter) == expected


def test_pdf_source_selection(monkeypatch):
    records = [dict(subject='数学', material_type='book-pdf', source_name='书', source_id='old', updated_at='1'), dict(subject='数学', material_type='book-pdf', source_name='书', source_id='new', updated_at='2')]
    monkeypatch.setattr(pdf_sources, 'load_all_json', lambda path: records)
    assert pdf_sources.resolve_pdf_source_id('数学', '书', {}, 'explicit') == 'explicit'
    assert pdf_sources.resolve_pdf_source_id('数学', '书', {'sources': Path('.')}, '') == 'new'
    with pytest.raises(SystemExit, match='no registered book-pdf source'):
        pdf_sources.resolve_pdf_source_id('408', '书', {'sources': Path('.')}, '')


def test_snapshot_environment_and_failure(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(snapshot_support, 'preferred_python_executable', lambda: 'python-chosen')
    monkeypatch.setattr(snapshot_support, 'runtime_subprocess_env', lambda: {'KAOYAN_CONFIG_FILE': 'chosen'})
    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return SimpleNamespace(stdout='{"snapshot_id":"snap"}')
    monkeypatch.setattr(snapshot_support, 'run_utf8_subprocess', run)
    script = tmp_path / 'create_snapshot.py'
    assert snapshot_support.create_backup(script) == 'snap'
    assert calls == [(['python-chosen', str(script), '--format', 'json'], dict(command_label='python:create_snapshot.py', check=True, env={'KAOYAN_CONFIG_FILE': 'chosen'}))]
    def fail(*args, **kwargs):
        raise subprocess.CalledProcessError(2, 'snapshot')
    monkeypatch.setattr(snapshot_support, 'run_utf8_subprocess', fail)
    with pytest.raises(subprocess.CalledProcessError):
        snapshot_support.create_backup(script)


def test_refinement_transition_is_scoped(monkeypatch, tmp_path):
    queue = tmp_path / 'queue.json'
    queue.write_text(json.dumps({'items': [{'refinement_id': 'r', 'status': 'open'}]}), encoding='utf-8')
    monkeypatch.setattr(review_handlers, 'learner_file_map', lambda: {'refinement_queue': queue})
    monkeypatch.setattr(review_handlers, 'now_iso', lambda: 'fixed')
    result = review_handlers._review_refinement_decide('r', 'accepted', 'ok')
    assert result['cli_write_scope'] == 'refinement_queue'
    before = queue.read_bytes()
    assert json.loads(before)['items'][0]['review_history'] == [{'status': 'accepted', 'note': 'ok', 'at': 'fixed'}]
    with pytest.raises(SystemExit, match='invalid refinement lifecycle'):
        review_handlers._review_refinement_decide('r', 'verified', '')
    assert queue.read_bytes() == before
    with pytest.raises(SystemExit, match='refinement not found'):
        review_handlers._review_refinement_decide('missing', 'accepted', '')
    assert queue.read_bytes() == before


def test_weekly_fallback_forwards_scope(tmp_path):
    calls = []
    def run(name, *args):
        calls.append((name, args))
        return json.dumps({'batches': [1, 2], 'items': ['a', 'b']})
    args = argparse.Namespace(vault_root=str(tmp_path), subject='数学', chapter='第一章', topn=1)
    result = maintain_handlers._build_weekly_refresh_fallback(args, 'original failure', run_script=run)
    assert result['maintenance_error'] == 'original failure'
    assert result['top_actions'] == [1]
    assert result['top_refinement_queue'] == ['a']
    assert result['fact_writeback_allowed'] is False
    assert len(calls) == 6
    assert all(call[1][:2] == ('--vault-root', str(tmp_path)) for call in calls)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize('command,flags,script,expected', [
    ('review-followups', ['--time-budget-minutes', '12', '--max-items', '2'], 'build_review_followups.py', ['--plan-date','2026-09-06','--time-budget-minutes','12','--max-items','2','--format','json','--vault-root','vault']),
    ('weekly-orchestration', ['--override-json','overrides'], 'build_weekly_orchestration.py', ['--plan-date','2026-09-06','--format','json','--vault-root','vault','--override-json','overrides']),
])
def test_learner_forwarding(command, flags, script, expected):
    parser = argparse.ArgumentParser()
    add_learner_commands(parser.add_subparsers(dest='command'))
    args = parser.parse_args(['learner',command,'--plan-date','2026-09-06','--vault-root','vault',*flags])
    calls = []
    def run(name, *args):
        calls.append((name,list(args)))
        return 'result'
    assert dispatch_learner(args, run, lambda args: '') == 'result'
    assert calls == [(script,expected)]
