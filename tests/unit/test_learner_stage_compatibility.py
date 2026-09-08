from __future__ import annotations

import argparse
import importlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / 'scripts'
sys.path.insert(0, str(SCRIPTS))
from kaoyan_kb.cli import learner_artifact_runner
from kaoyan_kb.domain import learner_artifact_files

FIXTURE = json.loads((Path(__file__).resolve().parents[1] / 'fixtures' / 'learner_stage_compatibility.json').read_text(encoding='utf-8'))


@pytest.mark.parametrize('stem', list(FIXTURE['stages']))
def test_stage_payload_and_output_compatibility(stem, monkeypatch, tmp_path, capsys):
    expected = FIXTURE['stages'][stem]
    module = importlib.import_module(stem)
    args = argparse.Namespace(vault_root=str(tmp_path), format='json', **expected['kwargs'])
    index_root = tmp_path / learner_artifact_runner.INDEX_DIRNAME
    index_root.mkdir()
    for name in vars(learner_artifact_files).values():
        if isinstance(name, str) and name.endswith('.json'):
            (index_root / name).write_text(json.dumps(FIXTURE['input']), encoding='utf-8')
    monkeypatch.setattr(module, 'parse_args', lambda: args)
    if hasattr(module, 'load_events'):
        monkeypatch.setattr(module, 'load_events', lambda: [])
    before = {p.name: p.read_bytes() for p in index_root.iterdir()}
    assert module.main() == 0
    assert capsys.readouterr().out == expected['stdout']
    assert json.loads((index_root / expected['json_filename']).read_text(encoding='utf-8')) == expected['payload']
    assert (index_root / expected['markdown_filename']).read_text(encoding='utf-8') == expected['markdown']
    changed = {p.name for p in index_root.iterdir() if before.get(p.name) != p.read_bytes()}
    assert changed == {expected['json_filename'], expected['markdown_filename']}
    after = {p.name: p.read_bytes() for p in index_root.iterdir()}
    args.format = 'quiet'
    assert module.main() == 0
    assert capsys.readouterr().out == ''
    assert after == {p.name: p.read_bytes() for p in index_root.iterdir()}


def test_runner_propagates_build_failure_without_artifacts(tmp_path):
    args = argparse.Namespace(vault_root=str(tmp_path), format='json')
    def fail(root, args):
        raise SystemExit('missing required artifact: upstream.json')
    with pytest.raises(SystemExit, match='missing required artifact'):
        learner_artifact_runner.run_artifact(lambda: args, fail, lambda payload: '', 'out.json', 'out.md', ())
    assert list(tmp_path.rglob('*.json')) == []
    assert list(tmp_path.rglob('*.md')) == []


def test_runner_propagates_write_failure(monkeypatch, tmp_path):
    args = argparse.Namespace(vault_root=str(tmp_path), format='json')
    def fail(*args):
        raise OSError('disk unavailable')
    monkeypatch.setattr(learner_artifact_runner, 'save_json', fail)
    with pytest.raises(OSError, match='disk unavailable'):
        learner_artifact_runner.run_artifact(lambda: args, lambda root,args: {'id':'x'}, lambda payload: 'text', 'out.json', 'out.md', ('id',))
    assert list(tmp_path.rglob('out.md')) == []


def test_importing_stage_does_not_import_upstream_builders():
    code = "import build_r20_autonomous_tutoring_artifact, sys, json; print(json.dumps(sorted(n for n in sys.modules if n.startswith('build_'))))"
    result = subprocess.run([sys.executable, '-c', code], cwd=SCRIPTS, capture_output=True, text=True, encoding='utf-8', check=True)
    assert json.loads(result.stdout) == ['build_r20_autonomous_tutoring_artifact']


@pytest.mark.parametrize('stem,ready_values,policy_key,next_status', [
    ('build_r17_teacher_loop_artifact', ('ready-for-r17-t03','ready-for-r17-t04','ready-for-r17-t05','ready-for-r17-t06'), 'operator_override_policy', 'ready-for-r18-t01'),
    ('build_r18_adaptive_coaching_artifact', ('ready-for-r18-t03','ready-for-r18-t04','ready-for-r18-t05','ready-for-r18-t06'), 'operator_override_policy', 'ready-for-r19-t01'),
    ('build_r19_longitudinal_tutoring_artifact', ('ready-for-r19-t03','ready-for-r19-t04','ready-for-r19-t05','ready-for-r19-t06'), 'goal_adjustment_policy', 'ready-for-r20-t01'),
])
@pytest.mark.parametrize('allow_fact_writeback', [False, True])
def test_acceptance_keeps_human_edit_and_fact_writeback_gate(stem, ready_values, policy_key, next_status, allow_fact_writeback, monkeypatch, tmp_path):
    module = importlib.import_module(stem)
    inputs = [dict(readiness_status=status) for status in ready_values]
    inputs[-1][policy_key] = {'preserve_human_owned_edits': True, 'fact_writeback_allowed': allow_fact_writeback}
    iterator = iter(inputs)
    monkeypatch.setattr(module, '_load_artifact', lambda root, filename: next(iterator))
    payload = module.build_payload(tmp_path, '2026-09-08')
    assert payload['readiness_status'] == ('not-' + next_status if allow_fact_writeback else next_status)


@pytest.mark.parametrize('authority', ['human_operator_only', 'agent', ''])
def test_autonomous_acceptance_preserves_human_rollback_authority(authority, monkeypatch, tmp_path):
    module = importlib.import_module('build_r20_autonomous_tutoring_artifact')
    iterator = iter([
        {'readiness_status':'ready-for-r20-t03'},
        {'readiness_status':'ready-for-r20-t04'},
        {'readiness_status':'ready-for-r20-t05','policy_adjustment_policy':{'rollback_authority':authority}},
    ])
    monkeypatch.setattr(module, '_load_artifact', lambda root, filename: next(iterator))
    payload = module.build_payload(tmp_path, '2026-09-08')
    assert payload['readiness_status'] == ('ready-for-r21-t00' if authority == 'human_operator_only' else 'not-ready-for-r21-t00')
