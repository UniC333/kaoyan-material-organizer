from __future__ import annotations

import argparse
from contextlib import contextmanager
import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import ask_local_knowledge as ask
import kb
import query_local_knowledge as query
import sync_exam_kb as sync
from kaoyan_kb.cli import query_auto_sync as auto
from kaoyan_kb.storage.file_lock import FileLockTimeout, file_lock


def availability(reason=""):
    return {"_availability": {"available": not reason, "reason": reason}}


@pytest.mark.parametrize("command,flag", [("query", "--query"), ("ask", "--question")])
@pytest.mark.parametrize("enabled", [False, True])
def test_wrapper_parses_and_forwards_only_explicit_switch(command, flag, enabled):
    args = kb.build_parser().parse_args([
        command, "--subject", "数学", flag, "导数",
        *(["--auto-sync"] if enabled else []),
    ])
    calls = []
    kb.dispatch_core(args, lambda *a: calls.append(a) or "", lambda: {}, lambda p: "")
    assert args.auto_sync is enabled
    assert ("--auto-sync" in calls[0]) is enabled
    assert calls[0][0] == f"{command}_local_knowledge.py"


@pytest.mark.parametrize("module", [query, ask], ids=["query", "ask"])
@pytest.mark.parametrize("reason", sorted(auto.REPAIRABLE_REASONS))
def test_stale_missing_or_old_indexes_sync_before_query_and_answer(monkeypatch, tmp_path, capsys, module, reason):
    calls = []
    state = {"reason": reason}
    monkeypatch.setattr(auto, "load_page_locator_index", lambda: availability(state["reason"] if reason.startswith("page_") else ""))
    monkeypatch.setattr(auto, "load_exercise_locator_index", lambda: availability(state["reason"] if reason.startswith("exercise_") else ""))
    def refresh():
        calls.append("sync")
        state["reason"] = ""
    monkeypatch.setattr(auto, "refresh_query_indexes", refresh)
    _stub_cli(monkeypatch, module, tmp_path, enabled=True, calls=calls, state=state)
    assert module.main() == 0
    payload = json.loads(capsys.readouterr().out)
    assert (payload if module is query else payload["answer"])["status"] == "exact"
    assert calls == ["sync", "query", *(["answer"] if module is ask else [])]


def _stub_cli(monkeypatch, module, tmp_path, *, enabled, calls, state, save=False):
    args = argparse.Namespace(vault_root=str(tmp_path), subject="数学", chapter=None, book_title=None,
                              query="导数", question="导数", topk=3, printed_page=None,
                              exercise_label=None, format="json",
                              auto_sync=enabled, save=save, saved_at=None, confirmed_topic=None)
    monkeypatch.setattr(module, "parse_args", lambda: args)
    monkeypatch.setattr(module, "query_source_targets", lambda **kw: None)
    monkeypatch.setattr(module, "query_exercise_batch", lambda *a, **kw: None)
    def query_one(*a, **kw):
        calls.append("query")
        return {"status": "blocked" if state["reason"] else "exact", "reason": state["reason"]}
    monkeypatch.setattr(module, "query_knowledge", query_one)
    if module is ask:
        def answer(result):
            calls.append("answer")
            return result
        monkeypatch.setattr(ask, "build_answer_contract", answer)
        monkeypatch.setattr(ask, "save_answer_contract", lambda **kw: calls.append("save"))


@pytest.mark.parametrize("module", [query, ask], ids=["query", "ask"])
def test_no_switch_preserves_blocked_result_without_preflight_or_writes(monkeypatch, tmp_path, capsys, module):
    calls = []
    monkeypatch.setattr(auto, "_index_problems", lambda: pytest.fail("default must not check or sync indexes"))
    _stub_cli(monkeypatch, module, tmp_path, enabled=False, calls=calls, state={"reason": "page_locator_index_stale"})
    assert module.main() == 0
    payload = json.loads(capsys.readouterr().out)
    result = payload if module is query else payload["answer"]
    assert result == {"status": "blocked", "reason": "page_locator_index_stale"}
    assert calls == ["query", *(["answer"] if module is ask else [])]


@pytest.mark.parametrize("module,save", [(query, False), (ask, False), (ask, True)], ids=["query", "ask", "ask-save"])
@pytest.mark.parametrize("failure", ["child", "lock", "still-stale"])
def test_sync_failure_never_queries_answers_or_saves(monkeypatch, tmp_path, capsys, module, save, failure):
    calls = []
    monkeypatch.setattr(auto, "_index_problems", lambda: ["exercise_locator_index_stale"])
    def refresh():
        calls.append("sync")
        if failure == "child":
            raise subprocess.CalledProcessError(1, "builder", stderr="builder failed")
        if failure == "lock":
            raise FileLockTimeout("query-indexes.lock busy")
    monkeypatch.setattr(auto, "refresh_query_indexes", refresh)
    _stub_cli(monkeypatch, module, tmp_path, enabled=True, calls=calls, state={"reason": "stale"}, save=save)
    with pytest.raises(SystemExit, match="auto-sync.*aborted"):
        module.main()
    assert calls == ["sync"]
    assert capsys.readouterr().out == ""


def test_ask_save_runs_only_after_successful_sync_and_answer(monkeypatch, tmp_path, capsys):
    calls = []
    state = {"reason": "page_locator_index_stale"}
    monkeypatch.setattr(auto, "_index_problems", lambda: [state["reason"]] if state["reason"] else [])
    def refresh():
        calls.append("sync")
        state["reason"] = ""
    monkeypatch.setattr(auto, "refresh_query_indexes", refresh)
    _stub_cli(monkeypatch, ask, tmp_path, enabled=True, calls=calls, state=state, save=True)
    assert ask.main() == 0
    assert json.loads(capsys.readouterr().out)["saved"] is True
    assert calls == ["sync", "query", "answer", "save"]


def test_fresh_indexes_do_not_sync(monkeypatch):
    monkeypatch.setattr(auto, "load_page_locator_index", availability)
    monkeypatch.setattr(auto, "load_exercise_locator_index", availability)
    monkeypatch.setattr(auto, "refresh_query_indexes", lambda: pytest.fail("fresh indexes must not be written"))
    auto.ensure_query_indexes()


@pytest.mark.parametrize("reason", ["runtime_config_missing", "page_locator_index_invalid", "exercise_locator_index_invalid"])
def test_other_unavailable_reasons_fail_without_sync(monkeypatch, reason):
    monkeypatch.setattr(auto, "_index_problems", lambda: [reason])
    monkeypatch.setattr(auto, "refresh_query_indexes", lambda: pytest.fail("must not sync"))
    with pytest.raises(SystemExit, match=reason):
        auto.ensure_query_indexes()


def test_shared_sync_entry_holds_existing_lock_protocol_for_all_steps(monkeypatch, tmp_path):
    events = []
    target = tmp_path / "query-indexes"
    @contextmanager
    def tracked_lock(path):
        assert path == target
        with file_lock(path):
            events.append("locked")
            yield
        events.append("released")
    monkeypatch.setattr(sync, "kb_layout", lambda: {"indexes": tmp_path})
    monkeypatch.setattr(sync, "file_lock", tracked_lock)
    def run(name, *args):
        with pytest.raises(FileLockTimeout):
            with file_lock(target, timeout=0):
                pytest.fail("builder must run under the shared lock")
        events.append(name)
    monkeypatch.setattr(sync, "run_script", run)
    assert auto.refresh_query_indexes is sync.refresh_query_indexes
    steps = sync.refresh_query_indexes()
    assert events == ["locked", *(f"{step}.py" for step in steps), "released"]
    assert target.with_name("query-indexes.lock").read_bytes() == b""


def test_shared_sync_stops_and_releases_lock_on_builder_failure(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(sync, "kb_layout", lambda: {"indexes": tmp_path})
    def fail(name, *args):
        calls.append(name)
        raise subprocess.CalledProcessError(1, name)
    monkeypatch.setattr(sync, "run_script", fail)
    with pytest.raises(subprocess.CalledProcessError):
        sync.refresh_query_indexes()
    assert calls == ["build_page_locator_index.py"]
    with file_lock(tmp_path / "query-indexes", timeout=0):
        pass


@pytest.mark.parametrize("arguments", [
    ["--question", "导数"],
    ["--subject", "数学", "--question", "导数", "--confirmed-topic", "导数", "--save"],
    ["--subject", "408", "--question", "第1、8题没懂，重点讲 sin^2x + 3y", "--save"],
])
def test_ask_rejects_invalid_requests_before_sync(monkeypatch, arguments):
    monkeypatch.setattr(auto, "ensure_query_indexes", lambda: pytest.fail("invalid request must not sync"))
    monkeypatch.setattr(sys, "argv", ["ask_local_knowledge.py", *arguments, "--auto-sync"])
    with pytest.raises(SystemExit):
        ask.main()
