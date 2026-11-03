from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import kb
from kaoyan_kb.storage import tmp_pruning


@pytest.fixture
def dead_pid():
    process = subprocess.Popen([sys.executable, "-c", "pass"])
    process.wait(timeout=10)
    assert not tmp_pruning._pid_running(process.pid)
    return process.pid


def temp_for(target: Path, pid: int, tag: str = "a") -> Path:
    path = target.with_name(f".{target.name}.{pid}.{tag * 32}.tmp")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("interrupted write", encoding="utf-8")
    return path


def doctor(tmp_path: Path, root: Path, *args: str) -> subprocess.CompletedProcess:
    config = tmp_path / "selected.config.json"
    config.write_text(json.dumps({"kb_root": str(root), "vault_root": str(tmp_path / "vault"),
                                  "ocr_cache_root": str(root / "ocr")}), encoding="utf-8")
    return subprocess.run(
        [sys.executable, str(SCRIPTS / "kb.py"), "--config", str(config), "doctor", *args],
        cwd=tmp_path, env={k: v for k, v in os.environ.items() if not k.startswith("KAOYAN_")},
        capture_output=True, text=True, encoding="utf-8", timeout=20,
    )


@pytest.mark.parametrize("output_format", ["json", "text"])
def test_doctor_cleans_manually_placed_orphans_and_reports_actual_count(tmp_path, dead_pid, output_format):
    root = tmp_path / "kb"
    targets = [root / "schema-version.json", root / "indexes/search_manifest.json",
               root / "ocr/normalized/request.json"]
    temps = [temp_for(target, dead_pid) for target in targets]
    completed = doctor(tmp_path, root, "--prune-tmp", "--format", output_format)
    assert completed.returncode == 0, completed.stderr
    actual_deletions = sum(not path.exists() for path in temps)
    assert actual_deletions == 3
    if output_format == "json":
        result = json.loads(completed.stdout)
        assert result["deleted_count"] == actual_deletions
        assert result["skipped_count"] == 0
        assert {item["path"] for item in result["items"]} == {str(p) for p in temps}
        assert all(item["status"] == "deleted" and item["reason"] for item in result["items"])
    else:
        assert f"deleted_count: {actual_deletions}" in completed.stdout
        assert "skipped_count: 0" in completed.stdout
        assert all(f"deleted: {path} (target-lock-acquired; creator-pid-exited)" in completed.stdout
                   for path in temps)
    # Maintenance mode must not initialize schemas, data targets or the Vault.
    assert all(not target.exists() for target in targets)
    assert not (root / "schemas").exists() and not (tmp_path / "vault").exists()
    assert all(target.with_name(target.name + ".lock").exists() for target in targets)


@pytest.mark.parametrize("relative", ["schema-version.json", "indexes/id_counters.json"])
@pytest.mark.parametrize("kind", ["atomic", "restore"])
def test_doctor_skips_temp_with_target_lock_held_by_another_process(tmp_path, dead_pid, relative, kind):
    root = tmp_path / "kb"
    target = root / relative
    temporary = temp_for(target, dead_pid)
    if kind == "restore":
        renamed = target.with_name(f".{target.name}.restore.tmp")
        temporary.rename(renamed)
        temporary = renamed
    worker = """
import sys
sys.path.insert(0, sys.argv[1])
from kaoyan_kb.storage.file_lock import file_lock
with file_lock(sys.argv[2]):
    print('locked', flush=True)
    sys.stdin.read()
"""
    holder = subprocess.Popen([sys.executable, "-c", worker, str(SCRIPTS), str(target)],
                              stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              text=True, encoding="utf-8")
    try:
        assert holder.stdout.readline().strip() == "locked"
        sidecar = target.with_name(target.name + ".lock")
        identity = sidecar.stat().st_ino
        completed = doctor(tmp_path, root, "--prune-tmp", "--format", "json")
        assert completed.returncode == 0, completed.stderr
        result = json.loads(completed.stdout)
        assert result["deleted_count"] == 0 and result["skipped_count"] == 1
        assert result["items"] == [{"path": str(temporary), "status": "skipped",
                                    "reason": f"target-lock-held: {sidecar}"}]
        assert temporary.read_text(encoding="utf-8") == "interrupted write"
        assert holder.poll() is None and sidecar.stat().st_ino == identity
    finally:
        holder.communicate(timeout=10)
    # Reuse the same sidecar after its owner exits; only confirmed atomic
    # orphans become deletable, while the uncoordinated restore stays unknown.
    completed = doctor(tmp_path, root, "--prune-tmp", "--format", "json")
    assert completed.returncode == 0, completed.stderr
    after = json.loads(completed.stdout)
    assert sidecar.stat().st_ino == identity
    assert after["deleted_count"] == (1 if kind == "atomic" else 0)
    assert temporary.exists() == (kind == "restore")


def test_doctor_preserves_unknown_names_and_uncoordinated_restore(tmp_path, dead_pid):
    root = tmp_path / "kb"
    orphan = temp_for(root / "indexes/value.json", dead_pid)
    names = ["notes.tmp", "value.json.tmp", ".value.json.12.not-a-uuid.tmp",
             ".value.json.0." + "a" * 32 + ".tmp", ".value.json.restore.tmp"]
    preserved = [orphan.parent / name for name in names]
    for path in preserved:
        path.write_text("keep", encoding="utf-8")
    completed = doctor(tmp_path, root, "--prune-tmp", "--format", "json")
    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout)
    assert not orphan.exists() and all(path.read_text(encoding="utf-8") == "keep" for path in preserved)
    assert result["deleted_count"] == 1 and result["skipped_count"] == len(preserved)
    skipped = {item["path"]: item["reason"] for item in result["items"] if item["status"] == "skipped"}
    assert set(skipped) == {str(path) for path in preserved}
    assert all(skipped[str(path)] == "unrecognized-temp-name" for path in preserved[:-1])
    assert "orphan-status-unknown" in skipped[str(preserved[-1])]


def test_live_atomic_creator_without_target_lock_is_preserved(tmp_path):
    root = tmp_path / "kb"
    temporary = temp_for(root / "evidence/value.json", os.getpid())
    result = tmp_pruning.prune_tmp(root)
    assert temporary.exists() and result["deleted_count"] == 0 and result["skipped_count"] == 1
    assert "creator-pid-running" in result["items"][0]["reason"]


def test_uncertain_process_status_or_delete_failure_is_reported_as_skip(tmp_path, dead_pid, monkeypatch):
    temporary = temp_for(tmp_path / "indexes/value.json", dead_pid)

    def inaccessible(pid):
        raise PermissionError("cannot query process")

    monkeypatch.setattr(tmp_pruning, "_pid_running", inaccessible)
    result = tmp_pruning.prune_tmp(tmp_path)
    assert temporary.exists() and result["deleted_count"] == 0 and result["skipped_count"] == 1
    assert "cannot query process" in result["items"][0]["reason"]
    monkeypatch.setattr(tmp_pruning, "_pid_running", lambda pid: False)

    def cannot_unlink(path):
        raise PermissionError("cannot unlink temp")

    monkeypatch.setattr(tmp_pruning.os, "unlink", cannot_unlink)
    result = tmp_pruning.prune_tmp(tmp_path)
    assert temporary.exists() and result["deleted_count"] == 0
    assert "cannot unlink temp" in result["items"][0]["reason"]


def test_lock_release_failure_after_unlink_keeps_deleted_record(tmp_path, dead_pid, monkeypatch):
    target = tmp_path / "indexes/value.json"
    temporary = temp_for(target, dead_pid)
    lock = tmp_pruning.file_lock(target, timeout=0)

    class LockContext:
        def __enter__(self):
            return lock.__enter__()

        def __exit__(self, *args):
            return lock.__exit__(*args)

    def release_failure(self, *args):
        lock.__exit__(*args)
        assert not temporary.exists()
        raise OSError("cannot release target lock")

    monkeypatch.setattr(LockContext, "__exit__", release_failure)
    monkeypatch.setattr(tmp_pruning, "file_lock", lambda *args, **kwargs: LockContext())
    result = tmp_pruning.prune_tmp(tmp_path)

    assert not temporary.exists()
    assert result["deleted_count"] == 1
    assert result["skipped_count"] == 0
    assert result["items"] == [{
        "path": str(temporary), "status": "deleted",
        "reason": "target-lock-acquired; creator-pid-exited; lock-release-failed: cannot release target lock",
    }]
    assert "lock-release-failed: cannot release target lock" in tmp_pruning.render_prune_text(result)


def test_links_external_cache_and_unmanaged_directories_are_never_pruned(tmp_path, dead_pid):
    root = tmp_path / "kb"
    root.mkdir()
    outside = tmp_path / "outside"
    external_tmp = temp_for(outside / "value.json", dead_pid)
    (root / "evidence").symlink_to(outside, target_is_directory=True)
    indexes = root / "indexes"
    indexes.mkdir()
    (indexes / "linked-dir").symlink_to(outside, target_is_directory=True)
    linked_temp = indexes / external_tmp.name
    linked_temp.symlink_to(external_tmp)
    target_link_temp = temp_for(indexes / "linked-target.json", dead_pid)
    (indexes / "linked-target.json").symlink_to(outside / "target.json")
    sidecar_link_temp = temp_for(indexes / "linked-lock.json", dead_pid)
    (indexes / "linked-lock.json.lock").symlink_to(outside / "external.lock")
    historical = temp_for(root / "backups/value.json", dead_pid)
    unrelated = temp_for(root / "unmanaged/value.json", dead_pid)
    result = tmp_pruning.prune_tmp(root, ocr_cache_root=outside)
    assert result["deleted_count"] == 0 and result["skipped_count"] == 5
    assert all("symlink-or-outside-kb" in item["reason"] for item in result["items"])
    assert all(path.exists() for path in [external_tmp, linked_temp, target_link_temp,
                                          sidecar_link_temp, historical, unrelated])
    assert not (outside / "external.lock").exists()
    assert not (outside / "value.json.lock").exists()


def test_prune_rejects_unknown_extra_argument_before_cleanup(tmp_path, dead_pid):
    root = tmp_path / "kb"
    temporary = temp_for(root / "indexes/value.json", dead_pid)
    completed = doctor(tmp_path, root, "--prune-tmp", "validate", "--format", "json")
    assert completed.returncode == 2 and "unrecognized arguments: validate" in completed.stderr
    assert temporary.exists() and not list(root.rglob("*.lock"))


def test_original_doctor_remains_opt_in():
    assert not kb.build_parser().parse_args(["doctor"]).prune_tmp


def test_original_doctor_does_not_clean_temps(tmp_path, dead_pid):
    root = tmp_path / "kb"
    temporary = temp_for(root / "indexes/value.json", dead_pid)
    completed = doctor(tmp_path, root, "--format", "json")
    assert completed.returncode == 0, completed.stderr
    assert temporary.read_text(encoding="utf-8") == "interrupted write"
    assert "deleted_count" not in json.loads(completed.stdout)
