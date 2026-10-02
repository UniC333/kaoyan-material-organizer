from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import restore_snapshot as restore
from common import display_path, filesystem_path


def tree(root):
    return {Path(display_path(p)).relative_to(root).as_posix(): p.read_bytes() if p.is_file() else None
            for p in Path(filesystem_path(root)).rglob("*")}


@pytest.fixture
def snapshot(tmp_path, monkeypatch):
    roots = {name: tmp_path / name for name in ("workspace", "vault", "kb")}
    for root in roots.values():
        root.mkdir()
    runtime = SimpleNamespace(**{name + "_root": path for name, path in roots.items()},
                              backup_root=tmp_path / "backups")
    monkeypatch.setattr(restore, "load_runtime_config", lambda: runtime)
    snapshot_dir = runtime.backup_root / "snapshots" / "SNAP-TEST"
    files = []
    for name, relative, content in [("vault", "notes.md", b"SAVED NOTES"),
                                    ("workspace", "new/nested/config.txt", b"SAVED CONFIG")]:
        source = snapshot_dir / "files" / name / relative
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_bytes(content)
        files.append({"root": name, "relative_path": relative,
                      "sha256": hashlib.sha256(content).hexdigest()})
    manifest = snapshot_dir / "manifest.json"
    manifest.write_text(json.dumps({"files": files, "file_count": len(files)}), encoding="utf-8")
    (roots["vault"] / "notes.md").write_bytes(b"CURRENT GOOD NOTES")
    (roots["kb"] / "runs").mkdir()
    (roots["kb"] / "runs/RUN-CURRENT.json").write_bytes(b"CURRENT RUN")
    (roots["kb"] / "human-note.md").write_bytes(b"KEEP HUMAN NOTE")
    monkeypatch.setattr(restore, "parse_args", lambda: SimpleNamespace(snapshot_id="SNAP-TEST", format="json"))
    return tmp_path, roots, snapshot_dir, manifest


@pytest.mark.parametrize("fault", ["corrupt", "missing", "unknown_root", "escape", "absolute", "bad_manifest"])
def test_invalid_snapshot_fails_before_any_write_or_cleanup(snapshot, capsys, fault):
    root, roots, snapshot_dir, manifest = snapshot
    source = snapshot_dir / "files/vault/notes.md"
    payload = json.loads(manifest.read_text())
    if fault == "corrupt":
        source.write_bytes(b"CORRUPTED SNAPSHOT")
    elif fault == "missing":
        source.unlink()
    elif fault == "unknown_root":
        payload["files"][0]["root"] = "unknown"
    elif fault == "escape":
        payload["files"][0]["relative_path"] = "../outside.md"
    elif fault == "absolute":
        payload["files"][0]["relative_path"] = str(root / "outside.md")
    else:
        payload = {"files": "invalid"}
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    before = tree(root)
    assert restore.main() == 1
    assert not json.loads(capsys.readouterr().out)["restored"]
    assert tree(root) == before


def test_missing_snapshot_returns_nonzero(snapshot, monkeypatch, capsys):
    root, *_ = snapshot
    monkeypatch.setattr(restore, "parse_args", lambda: SimpleNamespace(snapshot_id="MISSING", format="json"))
    before = tree(root)
    assert restore.main() == 1
    assert json.loads(capsys.readouterr().out)["reason"] == "snapshot_not_found"
    assert tree(root) == before


@pytest.mark.parametrize("failure", ["copy", "destination_hash", "cleanup", "report"])
def test_failure_rolls_back_overwrites_new_files_and_pruned_runs(snapshot, monkeypatch, capsys, failure):
    root, roots, snapshot_dir, _ = snapshot
    before = tree(root)
    copy2 = restore.shutil.copy2
    copies = []
    def broken_copy(source, destination, *args, **kwargs):
        result = copy2(source, destination, *args, **kwargs)
        if str(snapshot_dir / "files") in str(source):
            copies.append(source)
            if failure == "copy" and len(copies) == 2:
                Path(destination).write_bytes(b"PARTIAL COPY")
                raise OSError("injected copy failure")
            if failure == "destination_hash" and len(copies) == 2:
                Path(destination).write_bytes(b"WRONG BYTES")
        return result
    monkeypatch.setattr(restore.shutil, "copy2", broken_copy)
    if failure == "cleanup":
        original = restore.prune_machine_only_files
        def broken_cleanup(*args, **kwargs):
            original(*args, **kwargs)
            raise OSError("injected cleanup failure")
        monkeypatch.setattr(restore, "prune_machine_only_files", broken_cleanup)
    if failure == "report":
        def broken_report(path, payload):
            Path(path).write_bytes(b"PARTIAL REPORT")
            raise OSError("injected report failure")
        monkeypatch.setattr(restore, "save_json", broken_report)
    assert restore.main() == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["rolled_back"]
    assert tree(root) == before


def test_valid_restore_cleans_only_machine_runs_after_all_copies(snapshot, capsys):
    _, roots, _, _ = snapshot
    assert restore.main() == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["restored"] and payload["restored_files"] == 2
    assert (roots["vault"] / "notes.md").read_bytes() == b"SAVED NOTES"
    assert (roots["workspace"] / "new/nested/config.txt").read_bytes() == b"SAVED CONFIG"
    assert not (roots["kb"] / "runs/RUN-CURRENT.json").exists()
    assert (roots["kb"] / "human-note.md").read_bytes() == b"KEEP HUMAN NOTE"
    assert payload["cleanup_summary"]["pruned_relative_paths"] == ["runs/RUN-CURRENT.json"]


def test_corrupt_snapshot_cli_returns_nonzero_and_preserves_notes(snapshot):
    root, roots, snapshot_dir, _ = snapshot
    (snapshot_dir / "files/vault/notes.md").write_bytes(b"CORRUPTED SNAPSHOT")
    config = root / "config.json"
    config.write_text(json.dumps({"workspace_root": str(roots["workspace"]), "vault_root": str(roots["vault"]),
                                 "kb_root": str(roots["kb"]), "backup_root": str(root / "backups")}), encoding="utf-8")
    env = {key: value for key, value in os.environ.items() if not key.startswith("KAOYAN_")}
    env["KAOYAN_CONFIG_FILE"] = str(config)
    before = tree(root)
    result = subprocess.run([sys.executable, str(SCRIPTS / "restore_snapshot.py"),
                             "--snapshot-id", "SNAP-TEST", "--format", "json"],
                            cwd=root, env=env, capture_output=True, text=True, encoding="utf-8")
    assert result.returncode == 1, result.stderr
    assert not json.loads(result.stdout)["restored"]
    assert tree(root) == before


def test_normalized_snapshot_run_is_not_pruned_and_human_path_is_kept(snapshot, capsys):
    root, roots, snapshot_dir, manifest = snapshot
    payload = json.loads(manifest.read_text())
    source = snapshot_dir / "files/kb/runs/RUN-SAVED.json"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"SAVED RUN")
    payload["files"].append({"root": "kb", "relative_path": "runs/./RUN-SAVED.json",
                             "sha256": hashlib.sha256(b"SAVED RUN").hexdigest()})
    payload["file_count"] = len(payload["files"])
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    protected = roots["kb"] / " runs" / "user-note.md"
    protected.parent.mkdir()
    protected.write_bytes(b"HUMAN DIRECTORY")
    assert restore.main() == 0
    capsys.readouterr()
    assert (roots["kb"] / "runs/RUN-SAVED.json").read_bytes() == b"SAVED RUN"
    assert protected.read_bytes() == b"HUMAN DIRECTORY"


def test_machine_cleanup_preserves_symlink(snapshot, capsys):
    _, roots, _, _ = snapshot
    link = roots["kb"] / "runs/RUN-LINK.json"
    try:
        link.symlink_to(roots["kb"] / "human-note.md")
    except OSError:
        pytest.skip("symlinks are not available to this runner")
    assert restore.main() == 0
    capsys.readouterr()
    assert link.is_symlink()
    assert link.read_bytes() == b"KEEP HUMAN NOTE"


def make_redirect(link, target, kind):
    if kind == "junction":
        if os.name != "nt":
            pytest.skip("junctions require Windows")
        result = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)],
                                capture_output=True, text=True)
        assert result.returncode == 0, result.stdout + result.stderr
    else:
        try:
            link.symlink_to(target, target_is_directory=kind == "directory")
        except OSError:
            if os.name == "nt":
                pytest.skip("symlinks are not available to this runner")
            raise


@pytest.mark.parametrize("kind", ["file", "dangling", "directory", "junction"])
@pytest.mark.parametrize("outside", [False, True])
def test_restore_rejects_redirected_targets_before_any_write(snapshot, kind, outside):
    root, roots, _, _ = snapshot
    if kind in {"file", "dangling"}:
        link = roots["vault"] / "notes.md"
        link.unlink()
        target = (root if outside else roots["vault"]) / "unrelated.md"
        if kind != "dangling":
            target.write_bytes(b"UNRELATED HUMAN NOTE")
    else:
        link = roots["workspace"] / "new"
        target = (root if outside else roots["workspace"]) / "unrelated"
        (target / "nested").mkdir(parents=True)
        (target / "nested/config.txt").write_bytes(b"UNRELATED HUMAN CONFIG")
    make_redirect(link, target, kind)
    before = tree(root)

    result = restore.restore_snapshot("SNAP-TEST")

    assert result["restored"] is False
    assert result["reason"] == "snapshot_preflight_failed"
    assert any("restore_destination_redirected" in error["reason"] for error in result["errors"])
    assert tree(root) == before
    assert link.exists() or link.is_symlink()


@pytest.mark.parametrize("kind", ["file", "directory", "junction"])
def test_restore_rejects_redirected_recovery_report_before_any_write(snapshot, kind):
    root, _, snapshot_dir, _ = snapshot
    report_dir = snapshot_dir.parent / "recovery"
    if kind == "file":
        report_dir.mkdir()
        link = report_dir / "latest_restore_summary.json"
        target = root / "unrelated-report.json"
        target.write_text("{}", encoding="utf-8")
    else:
        link = report_dir
        target = root / "unrelated-reports"
        target.mkdir()
        (target / "latest_restore_summary.json").write_text("{}", encoding="utf-8")
    make_redirect(link, target, kind)
    before = tree(root)

    result = restore.restore_snapshot("SNAP-TEST")

    assert result["restored"] is False
    assert "restore_destination_redirected" in result["error"]
    assert tree(root) == before


@pytest.mark.parametrize("kind", ["directory", "junction"])
def test_machine_cleanup_does_not_traverse_redirected_directories(snapshot, kind):
    _, roots, _, _ = snapshot
    make_redirect(roots["kb"] / "runs/linked-notes", roots["vault"], kind)

    result = restore.restore_snapshot("SNAP-TEST")

    assert result["restored"] is True
    assert (roots["vault"] / "notes.md").read_bytes() == b"SAVED NOTES"
    assert result["cleanup_summary"]["pruned_relative_paths"] == ["runs/RUN-CURRENT.json"]


def long_directory(root, length):
    path = root
    while len(str(path)) < length:
        remaining = length - len(str(path)) - 1
        if remaining <= 0:
            raise AssertionError("cannot construct requested path length")
        path /= "d" * min(60, remaining)
    assert len(str(path)) == length
    Path(filesystem_path(path)).mkdir(parents=True, exist_ok=True)
    return path


@pytest.mark.parametrize("failure", [None, "copy", "cleanup", "report"])
def test_existing_280_character_file_is_backed_up_before_overwrite(snapshot, monkeypatch, capsys, failure):
    root, roots, snapshot_dir, manifest = snapshot
    # A long filename in a short directory isolates stat/backup from mkstemp.
    name = "n" * (280 - len(str(roots["vault"])) - 1 - len(".md")) + ".md"
    assert len(name) <= 255
    notes = roots["vault"] / name
    assert len(str(notes)) == 280
    Path(filesystem_path(notes)).write_bytes(b"CURRENT GOOD NOTES")
    payload = json.loads(manifest.read_text())
    payload["files"][0]["relative_path"] = name
    Path(filesystem_path(snapshot_dir / "files/vault" / name)).write_bytes(b"SAVED NOTES")
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    before = tree(root)
    original_copy = restore.shutil.copy2
    def copy_with_failure(source, destination, *args, **kwargs):
        result = original_copy(source, destination, *args, **kwargs)
        if failure == "copy" and display_path(source) == str(snapshot_dir / "files/workspace/new/nested/config.txt"):
            assert Path(filesystem_path(notes)).read_bytes() == b"SAVED NOTES"
            raise OSError("injected second copy failure")
        return result
    monkeypatch.setattr(restore.shutil, "copy2", copy_with_failure)
    if failure == "cleanup":
        original_cleanup = restore.prune_machine_only_files
        def cleanup_with_failure(*args, **kwargs):
            original_cleanup(*args, **kwargs)
            raise OSError("injected cleanup failure")
        monkeypatch.setattr(restore, "prune_machine_only_files", cleanup_with_failure)
    if failure == "report":
        def report_with_failure(*args):
            raise OSError("injected report failure")
        monkeypatch.setattr(restore, "save_json", report_with_failure)
    assert restore.main() == (1 if failure else 0)
    result = json.loads(capsys.readouterr().out)
    assert result["restored"] is (failure is None)
    if failure:
        assert result["rolled_back"] is True
        assert Path(filesystem_path(notes)).read_bytes() == b"CURRENT GOOD NOTES"
        assert tree(root) == before
    else:
        assert Path(filesystem_path(notes)).read_bytes() == b"SAVED NOTES"


@pytest.fixture
def long_snapshot(snapshot, monkeypatch, request):
    root, roots, snapshot_dir, manifest = snapshot
    # Existing notes are 280 characters, or inside a 296-character directory.
    roots["vault"] = long_directory(root / "long-vault", request.param)
    Path(filesystem_path(roots["vault"] / "notes.md")).write_bytes(b"CURRENT GOOD NOTES")
    roots["workspace"] = long_directory(root / "long-workspace", 296)
    roots["kb"] = long_directory(root / "long-kb", 276)
    Path(filesystem_path(roots["kb"] / "runs")).mkdir()
    Path(filesystem_path(roots["kb"] / "runs/RUN-CURRENT.json")).write_bytes(b"CURRENT RUN")
    Path(filesystem_path(roots["kb"] / "human-note.md")).write_bytes(b"KEEP HUMAN NOTE")
    backup_root = long_directory(root / "long-backups", 296)
    new_snapshot = backup_root / "snapshots/SNAP-TEST"
    restore.shutil.copytree(filesystem_path(snapshot_dir), filesystem_path(new_snapshot))
    report = backup_root / "snapshots/recovery/latest_restore_summary.json"
    Path(filesystem_path(report.parent)).mkdir(parents=True)
    Path(filesystem_path(report)).write_bytes(b"CURRENT REPORT")
    runtime = SimpleNamespace(**{name + "_root": path for name, path in roots.items()}, backup_root=backup_root)
    monkeypatch.setattr(restore, "load_runtime_config", lambda: runtime)
    if os.name == "nt" and os.environ.get("KAOYAN_TEST_LONG_PATHS_DISABLED") == "1":
        import ctypes
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\FileSystem") as key:
            assert winreg.QueryValueEx(key, "LongPathsEnabled")[0] == 0
        # Prove this process cannot access the existing file through a plain path.
        attributes = ctypes.windll.kernel32.GetFileAttributesW
        attributes.argtypes = [ctypes.c_wchar_p]
        attributes.restype = ctypes.c_uint32
        notes = roots["vault"] / "notes.md"
        assert attributes(str(notes)) == 0xFFFFFFFF
        assert attributes(filesystem_path(notes)) != 0xFFFFFFFF
    return root, roots, new_snapshot, report


@pytest.mark.parametrize("long_snapshot", [271, 296], indirect=True)
@pytest.mark.parametrize("failure", [None, "copy", "destination_hash", "cleanup", "report"])
def test_long_path_restore_and_exception_rollback(long_snapshot, monkeypatch, capsys, failure):
    root, roots, snapshot_dir, report = long_snapshot
    notes = Path(filesystem_path(roots["vault"] / "notes.md"))
    config = Path(filesystem_path(roots["workspace"] / "new/nested/config.txt"))
    before = tree(root)
    original_copy = restore.shutil.copy2
    copies = []
    def copy_with_failure(source, destination, *args, **kwargs):
        result = original_copy(source, destination, *args, **kwargs)
        if str(snapshot_dir / "files") in display_path(source):
            copies.append(source)
            if len(copies) == 2:
                assert notes.read_bytes() == b"SAVED NOTES"  # first target really was overwritten
                if failure == "copy":
                    Path(destination).write_bytes(b"PARTIAL COPY")
                    raise OSError("injected second copy failure")
                if failure == "destination_hash":
                    Path(destination).write_bytes(b"WRONG BYTES")
        return result
    monkeypatch.setattr(restore.shutil, "copy2", copy_with_failure)
    if failure == "cleanup":
        original_cleanup = restore.prune_machine_only_files
        def cleanup_with_failure(*args, **kwargs):
            original_cleanup(*args, **kwargs)
            assert notes.read_bytes() == b"SAVED NOTES"
            assert not Path(filesystem_path(roots["kb"] / "runs/RUN-CURRENT.json")).exists()
            raise OSError("injected cleanup failure")
        monkeypatch.setattr(restore, "prune_machine_only_files", cleanup_with_failure)
    if failure == "report":
        def report_with_failure(path, payload):
            Path(filesystem_path(path)).write_bytes(b"PARTIAL REPORT")
            raise OSError("injected report failure")
        monkeypatch.setattr(restore, "save_json", report_with_failure)
    assert restore.main() == (1 if failure else 0)
    payload = json.loads(capsys.readouterr().out)
    assert payload["restored"] is (failure is None)
    if failure:
        assert payload["rolled_back"] is True
        assert notes.read_bytes() == b"CURRENT GOOD NOTES"
        assert not config.exists()
        assert Path(filesystem_path(report)).read_bytes() == b"CURRENT REPORT"
        assert tree(root) == before  # includes long run paths and temporary directories
    else:
        assert notes.read_bytes() == b"SAVED NOTES"
        assert config.read_bytes() == b"SAVED CONFIG"
        assert not Path(filesystem_path(roots["kb"] / "runs/RUN-CURRENT.json")).exists()
        assert json.loads(Path(filesystem_path(report)).read_text())["restored"] is True
        assert not any(".snapshot-" in name or ".restore-rollback-" in name for name in tree(root))


@pytest.mark.parametrize("long_snapshot", [271], indirect=True)
@pytest.mark.parametrize("rollback_fault", ["raises", "wrong_bytes"])
def test_long_path_rollback_failure_keeps_backup_and_reports_failure(long_snapshot, monkeypatch, capsys, rollback_fault):
    _, roots, snapshot_dir, _ = long_snapshot
    original_copy = restore.shutil.copy2
    original_copyfile = restore.shutil.copyfile
    def bad_rollback(source, destination, *args, **kwargs):
        if ".snapshot-rollback-" in str(destination):
            if rollback_fault == "raises":
                raise OSError("injected rollback failure")
            Path(destination).write_bytes(b"INCORRECT ROLLBACK")
            return destination
        return original_copyfile(source, destination, *args, **kwargs)
    def fail_after_overwrite(source, destination, *args, **kwargs):
        result = original_copy(source, destination, *args, **kwargs)
        if display_path(source) == str(snapshot_dir / "files/workspace/new/nested/config.txt"):
            monkeypatch.setattr(restore.shutil, "copyfile", bad_rollback)
            raise OSError("injected copy failure")
        return result
    monkeypatch.setattr(restore.shutil, "copy2", fail_after_overwrite)
    assert restore.main() == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["restored"] is False and payload["rolled_back"] is False
    assert payload["rollback_error"]
    backup = Path(filesystem_path(payload["rollback_backup_dir"]))
    mapping = json.loads((backup / "manifest.json").read_text())
    notes = roots["vault"] / "notes.md"
    assert Path(filesystem_path(mapping[str(restore.resolve_path(notes))])).read_bytes() == b"CURRENT GOOD NOTES"
    assert Path(filesystem_path(notes)).read_bytes() != b"CURRENT GOOD NOTES"
