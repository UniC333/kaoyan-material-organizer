from __future__ import annotations

import errno
import importlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from kaoyan_kb.storage import atomic_io
from kaoyan_kb.storage.file_lock import FileLockTimeout, file_lock

lock_module = importlib.import_module("kaoyan_kb.storage.file_lock")


def _wait_for(path: Path) -> None:
    deadline = time.monotonic() + 15
    while not path.exists():
        if time.monotonic() >= deadline:
            raise TimeoutError(f"worker signal did not arrive: {path}")
        time.sleep(0.01)


def _worker(mode: str, target: Path, signals: Path, writer: str) -> None:
    if mode == "hold":
        with file_lock(target):
            (signals / f"ready-{writer}").touch()
            _wait_for(signals / "release")
        return
    if mode == "atomic":
        replace = atomic_io.os.replace

        def paused_replace(source, destination):
            (signals / f"temp-{writer}").write_text(str(source), encoding="utf-8")
            _wait_for(signals / "release")
            replace(source, destination)

        atomic_io.os.replace = paused_replace
        atomic_io._atomic_write_text(target, json.dumps({"writer": writer, "text": "内容" * 1000}))
        return

    # Real helpers, with every KB path redirected to an isolated test directory.
    import common

    layout = common.kb_layout(root=target)
    common.kb_layout = lambda default=None: layout
    common.ensure_kb_layout()
    (signals / f"ready-{writer}").touch()
    _wait_for(signals / "go")
    result = []
    for number in range(20):
        if mode == "increment":
            with file_lock(target / "counter.json"):
                value = json.loads((target / "counter.json").read_text(encoding="utf-8"))
                time.sleep(0.002)  # Force a lost-update window without the lock.
                value["count"] += 1
                atomic_io.save_json(target / "counter.json", value)
        elif mode == "id":
            result.append(common.allocate_kb_id("evidence", "数学"))
        elif mode == "run":
            result.append(common.allocate_run_id())
        elif mode == "events":
            from learner_events import append_events

            append_events([{"event_id": f"{writer}-{number}-a"}, {"event_id": f"{writer}-{number}-b"}])
        elif mode == "cache":
            from ocr.cache import save_cached_payloads

            save_cached_payloads(
                target / "cache", f"{writer}-{number}", raw_payload={"raw": writer},
                normalized_payload={"text": writer}, source_file=target / "source.txt",
            )
        elif mode == "review":
            from kaoyan_kb.cli import review_handlers

            review_handlers.learner_file_map = lambda: {"refinement_queue": target / "queue.json"}
            review_handlers._review_refinement_decide("r", "accepted", f"{writer}-{number}")
        elif mode == "queue-review":
            from kaoyan_kb.cli.review_handlers import _review_refinement_decide

            _review_refinement_decide("r", "accepted", f"{writer}-{number}")
        elif mode == "queue":
            from build_refinement_queue import build_refinement_queue

            build_refinement_queue()
        elif mode == "views":
            from learner_events import rebuild_views

            rebuild_views()
        elif mode == "overlay":
            from ocr import review

            review.load_runtime_config = lambda: SimpleNamespace(ocr_cache_root=target / "cache")
            review.apply_overlay(request_key="request", block_id=f"{writer}-{number}",
                                 review_status="accepted", corrected_text=writer, note="review")
        else:
            raise AssertionError(mode)
    print(json.dumps(result), flush=True)


def _start(mode: str, target: Path, signals: Path, writer: str) -> subprocess.Popen:
    return subprocess.Popen(
        [sys.executable, str(Path(__file__).resolve()), mode, str(target), str(signals), writer],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8",
    )


def _finish(process: subprocess.Popen) -> str:
    stdout, stderr = process.communicate(timeout=30)
    assert process.returncode == 0, stderr
    return stdout


def _stop(processes) -> None:
    for process in processes:
        if process.poll() is None:
            process.kill()
        process.communicate(timeout=10)


def _pair(mode: str | tuple[str, str], target: Path, signals: Path) -> list[str]:
    signals.mkdir()
    modes = (mode, mode) if isinstance(mode, str) else mode
    processes = [_start(modes[writer], target, signals, str(writer)) for writer in range(2)]
    try:
        for writer in range(2):
            _wait_for(signals / f"ready-{writer}")
        (signals / "go").touch()
        return [_finish(process) for process in processes]
    finally:
        _stop(processes)


def test_two_process_read_modify_write_loses_no_updates(tmp_path):
    target = tmp_path / "kb"
    target.mkdir()
    (target / "counter.json").write_text('{"count": 0}', encoding="utf-8")
    _pair("increment", target, tmp_path / "signals")
    assert json.loads((target / "counter.json").read_text(encoding="utf-8")) == {"count": 40}


def test_concurrent_atomic_writers_have_distinct_temporary_files(tmp_path):
    target = tmp_path / "value.json"
    signals = tmp_path / "signals"
    signals.mkdir()
    processes = [_start("atomic", target, signals, str(writer)) for writer in range(2)]
    try:
        for writer in range(2):
            _wait_for(signals / f"temp-{writer}")
        paths = [Path((signals / f"temp-{writer}").read_text(encoding="utf-8")) for writer in range(2)]
        assert paths[0] != paths[1]
        for writer, path in enumerate(paths):
            assert path.parent == target.parent
            assert str(processes[writer].pid) in path.name
            assert json.loads(path.read_text(encoding="utf-8"))["writer"] == str(writer)
        (signals / "release").touch()
        for process in processes:
            _finish(process)
        assert json.loads(target.read_text(encoding="utf-8"))["writer"] in {"0", "1"}
        assert not list(tmp_path.glob("*.tmp"))
    finally:
        _stop(processes)


def test_live_owner_times_out_without_deleting_old_lock(tmp_path):
    target = tmp_path / "value.json"
    process = _start("hold", target, tmp_path, "owner")
    try:
        _wait_for(tmp_path / "ready-owner")
        sidecar = target.with_name(target.name + ".lock")
        identity = sidecar.stat().st_ino
        os.utime(sidecar, (1, 1))
        started = time.monotonic()
        with pytest.raises(FileLockTimeout, match="Timed out.*cross-process lock") as error:
            with file_lock(target, timeout=0.1, poll_interval=0.01):
                pytest.fail("entered while another process owns the lock")
        assert str(target) in str(error.value)
        assert str(sidecar) in str(error.value)
        assert 0.08 <= time.monotonic() - started < 5
        assert sidecar.stat().st_ino == identity
        assert process.poll() is None
        with pytest.raises(FileLockTimeout):
            with file_lock(target, timeout=0):
                pytest.fail("timeout must not release another writer's lock")
        (tmp_path / "release").touch()
        _finish(process)
        with file_lock(target, timeout=1):
            assert sidecar.stat().st_ino == identity
    finally:
        _stop([process])


def test_dead_owner_is_recovered_without_removing_sidecar(tmp_path):
    target = tmp_path / "value.json"
    process = _start("hold", target, tmp_path, "owner")
    try:
        _wait_for(tmp_path / "ready-owner")
        sidecar = target.with_name(target.name + ".lock")
        identity = sidecar.stat().st_ino
        process.kill()  # No finally block or cooperative unlock in the holder.
        process.wait(timeout=10)
        with file_lock(target, timeout=1):
            atomic_io.save_json(target, {"recovered": True})
        assert sidecar.stat().st_ino == identity
        assert json.loads(target.read_text(encoding="utf-8")) == {"recovered": True}
    finally:
        _stop([process])


def test_lock_releases_on_exception(tmp_path):
    target = tmp_path / "value.json"
    with pytest.raises(RuntimeError, match="write failed"):
        with file_lock(target):
            raise RuntimeError("write failed")
    with file_lock(target, timeout=0):
        pass


def test_unexpected_lock_error_is_not_reported_as_timeout(monkeypatch, tmp_path):
    def fail(fd):
        raise OSError(errno.EBADF, "invalid descriptor")

    monkeypatch.setattr(lock_module, "_try_lock", fail)
    with pytest.raises(OSError) as error:
        with file_lock(tmp_path / "value.json"):
            pass
    assert error.value.errno == errno.EBADF


@pytest.mark.parametrize("kwargs", [{"timeout": -1}, {"timeout": float("nan")},
                                   {"poll_interval": 0}, {"poll_interval": float("inf")}])
def test_lock_rejects_invalid_wait_parameters(tmp_path, kwargs):
    with pytest.raises(ValueError):
        with file_lock(tmp_path / "value.json", **kwargs):
            pass
    assert not list(tmp_path.iterdir())


def test_windows_backend_locks_and_unlocks_same_byte(monkeypatch):
    calls = []
    monkeypatch.setattr(lock_module, "os", SimpleNamespace(
        name="nt", SEEK_SET=0, lseek=lambda *args: calls.append(("seek", *args)),
    ))
    monkeypatch.setattr(lock_module, "msvcrt", SimpleNamespace(
        LK_NBLCK=2, LK_UNLCK=0, locking=lambda *args: calls.append(("lock", *args)),
    ), raising=False)
    lock_module._try_lock(123)
    lock_module._unlock(123)
    assert calls == [("seek", 123, 0, 0), ("lock", 123, 2, 1),
                     ("seek", 123, 0, 0), ("lock", 123, 0, 1)]


def test_concurrent_id_allocation_is_unique_and_keeps_other_keys(tmp_path):
    target = tmp_path / "kb"
    (target / "indexes").mkdir(parents=True)
    (target / "evidence").mkdir()
    (target / "evidence" / "EV-MATH-000007.json").write_text("{}", encoding="utf-8")
    (target / "indexes" / "id_counters.json").write_text('{"claim:ENG": 9}', encoding="utf-8")
    results = _pair("id", target, tmp_path / "signals")
    identifiers = [value for output in results for value in json.loads(output)]
    assert len(set(identifiers)) == 40
    assert set(identifiers) == {f"EV-MATH-{number:06d}" for number in range(8, 48)}
    counters = json.loads((target / "indexes" / "id_counters.json").read_text(encoding="utf-8"))
    assert counters == {"claim:ENG": 9, "evidence:MATH": 47}


def test_concurrent_run_allocation_is_unique(tmp_path):
    target = tmp_path / "kb"
    results = _pair("run", target, tmp_path / "signals")
    identifiers = [value for output in results for value in json.loads(output)]
    assert len(set(identifiers)) == 40
    counters = json.loads((target / "indexes" / "run_counters.json").read_text(encoding="utf-8"))
    assert sum(counters.values()) == 40


def test_concurrent_event_batches_keep_all_jsonl_records(tmp_path):
    target = tmp_path / "kb"
    (target / "learner").mkdir(parents=True)
    events = target / "learner" / "learner_events.jsonl"
    events.write_text('{"event_id": "seed"}', encoding="utf-8")
    _pair("events", target, tmp_path / "signals")
    records = [json.loads(line) for line in events.read_text(encoding="utf-8").splitlines()]
    assert len(records) == 81
    assert {record["event_id"] for record in records} == {"seed"} | {
        f"{writer}-{number}-{suffix}" for writer in range(2) for number in range(20) for suffix in "ab"
    }


def test_concurrent_cache_index_keeps_all_request_keys(tmp_path):
    target = tmp_path / "kb"
    target.mkdir()
    (target / "source.txt").write_text("source", encoding="utf-8")
    _pair("cache", target, tmp_path / "signals")
    indexes = list((target / "cache" / "indexes" / "by-file").glob("*.json"))
    assert len(indexes) == 1
    keys = json.loads(indexes[0].read_text(encoding="utf-8"))["request_keys"]
    assert len(keys) == 40
    assert set(keys) == {f"{writer}-{number}" for writer in range(2) for number in range(20)}


def test_concurrent_refinement_reviews_keep_all_history(tmp_path):
    target = tmp_path / "kb"
    target.mkdir()
    queue = target / "queue.json"
    queue.write_text('{"items": [{"refinement_id": "r", "status": "open"}]}', encoding="utf-8")
    _pair("review", target, tmp_path / "signals")
    item = json.loads(queue.read_text(encoding="utf-8"))["items"][0]
    assert item["status"] == "accepted"
    assert len(item["review_history"]) == 40
    assert {entry["note"] for entry in item["review_history"]} == {
        f"{writer}-{number}" for writer in range(2) for number in range(20)
    }


def test_concurrent_overlay_reviews_keep_all_blocks(tmp_path):
    target = tmp_path / "kb"
    normalized = target / "cache" / "normalized" / "request.json"
    normalized.parent.mkdir(parents=True)
    normalized.write_text(json.dumps({"chunk_candidates": [
        {"block_id": f"{writer}-{number}"} for writer in range(2) for number in range(20)
    ]}), encoding="utf-8")
    _pair("overlay", target, tmp_path / "signals")
    overlay = target / "cache" / "overlays" / "request.json"
    items = json.loads(overlay.read_text(encoding="utf-8"))["items"]
    assert len(items) == 40
    assert {item["block_id"] for item in items} == {
        f"{writer}-{number}" for writer in range(2) for number in range(20)
    }


@pytest.mark.parametrize("builder", ["queue", "views"])
def test_rebuilding_does_not_overwrite_concurrent_refinement_reviews(tmp_path, builder):
    from build_refinement_queue import build_refinement_items

    target = tmp_path / "kb"
    learner = target / "learner"
    learner.mkdir(parents=True)
    history = [{"subject": "数学", "chapter_title": "导数", "question": question,
                "answer_mode": "chapter_fallback", "saved_at": "2026-10-31"}
               for question in ("定义", "例题")]
    candidate = build_refinement_items(history)[0]
    candidate["refinement_id"] = "r"
    (learner / "question_history.json").write_text(json.dumps({"items": history}), encoding="utf-8")
    queue = learner / "refinement_queue.json"
    queue.write_text(json.dumps({"items": [candidate]}), encoding="utf-8")
    _pair((builder, "queue-review"), target, tmp_path / "signals")
    item = json.loads(queue.read_text(encoding="utf-8"))["items"][0]
    assert item["status"] == "accepted"
    assert len(item["review_history"]) == 20
    assert {entry["note"] for entry in item["review_history"]} == {f"1-{number}" for number in range(20)}


def test_atomic_temp_collision_does_not_delete_another_writers_file(monkeypatch, tmp_path):
    target = tmp_path / "value.json"
    foreign = target.with_name(f".{target.name}.{os.getpid()}.collision.tmp")
    foreign.write_text("another writer's content", encoding="utf-8")
    monkeypatch.setattr(atomic_io.uuid, "uuid4", lambda: SimpleNamespace(hex="collision"))
    with pytest.raises(FileExistsError):
        atomic_io._atomic_write_text(target, "replacement")
    assert foreign.read_text(encoding="utf-8") == "another writer's content"
    assert not target.exists()


@pytest.mark.parametrize("operation", ["fsync", "replace"])
def test_atomic_write_failure_preserves_original_and_cleans_own_temp(monkeypatch, tmp_path, operation):
    target = tmp_path / "value.json"
    target.write_text("original", encoding="utf-8")

    def fail(*args):
        raise OSError("write failed")

    monkeypatch.setattr(atomic_io.os, operation, fail)
    with pytest.raises(OSError, match="write failed"):
        atomic_io._atomic_write_text(target, "replacement")
    assert target.read_text(encoding="utf-8") == "original"
    assert list(tmp_path.iterdir()) == [target]


if __name__ == "__main__":
    _worker(sys.argv[1], Path(sys.argv[2]), Path(sys.argv[3]), sys.argv[4])
