from __future__ import annotations

import argparse
import hashlib
import importlib
import inspect
import json
import subprocess
import sys
from pathlib import Path, PureWindowsPath
from types import SimpleNamespace

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS))
FIXTURE_PATH = Path(__file__).resolve().parents[1] / "fixtures" / "runtime_artifact_compatibility.json"
FAMILY = ("run_manifest", "changed_only", "recovery", "gray_release", "formal_usable")
STEMS = tuple(f"build_{name}_artifact" for name in FAMILY) + ("build_study_orchestration_context",)


def public_api(module):
    """Snapshot exported data and builder signatures, including legacy aliases."""
    result = {}
    for name, value in vars(module).items():
        if name.startswith("_"):
            continue
        if isinstance(value, (str, int, bool, list, dict)):
            result[name] = value
        elif isinstance(value, Path):
            result[name] = value.as_posix()
        elif inspect.isfunction(value) and value.__module__ == module.__name__:
            result[name] = str(inspect.signature(value))
    return result


def normalize_artifact_text(text, root):
    try:
        json.loads(text)
    except json.JSONDecodeError:
        normalized = text.replace("\\\\", "/").replace("\\", "/")
    else:
        # JSON doubles path separators; preserve other escapes such as \u and \n.
        normalized = text.replace("\\\\", "/")
    return normalized.replace(root.as_posix(), "<ROOT>")


def run_stage(module, scenario, root, monkeypatch, capsys, inputs):
    """Exercise real entrypoints and writes using only an isolated fixture tree."""
    from common import INDEX_DIRNAME

    root.mkdir(parents=True, exist_ok=True)
    layout = {name: root / "kb" / name for name in ("indexes", "runs")}
    layout["root"] = root / "kb"
    for path in layout.values():
        path.mkdir(parents=True, exist_ok=True)
    for relative, payload in inputs[scenario].items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    args = argparse.Namespace(
        vault_root=str(root / "vault"), format="json",
        as_of="2026-11-03T12:00:00", freshness_days=14,
    )
    runtime = SimpleNamespace(workspace_root=root / "workspace", backup_root=root / "backup")
    monkeypatch.setattr(module, "parse_args", lambda: args)
    if hasattr(module, "ensure_kb_layout"):
        monkeypatch.setattr(module, "ensure_kb_layout", lambda: layout)
    if hasattr(module, "load_runtime_config"):
        monkeypatch.setattr(module, "load_runtime_config", lambda: runtime)
    if hasattr(module, "learner_file_map"):
        monkeypatch.setattr(module, "learner_file_map", lambda: {"learner_model": root / "model.json"})
        monkeypatch.setattr(module, "load_events", lambda: [{
            "event_id": "fixture-event", "event_type": "question_saved", "subject": "数学",
            "occurred_at": "2026-11-03T10:00:00", "intake_decision": {"status": "accepted"},
            "payload": {"question": "fixture question"},
        }])
    if hasattr(module, "build_report"):
        # The audit subprocess is outside the rename; retain real artifact/report renderers.
        monkeypatch.setattr(module, "build_report", lambda vault: inputs[scenario]["report.json"])

    written = set()
    for name in ("save_json", "save_text"):
        original = getattr(module, name)

        def save(path, payload, _original=original, **kwargs):
            written.add(path)
            return _original(path, payload, **kwargs)

        monkeypatch.setattr(module, name, save)
    assert module.main() == 0
    stdout = capsys.readouterr().out
    outputs = {path.relative_to(root).as_posix(): path.read_bytes() for path in written}
    args.format = "quiet"
    assert module.main() == 0
    assert capsys.readouterr().out == ""
    assert outputs == {path.relative_to(root).as_posix(): path.read_bytes() for path in written}

    def digest(text):
        normalized = normalize_artifact_text(text, root)
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()

    payload = json.loads((root / "vault" / INDEX_DIRNAME / module.ARTIFACT_JSON).read_text(encoding="utf-8"))
    return {
        "files": {name: digest(data.decode("utf-8")) for name, data in sorted(outputs.items())},
        "stdout": digest(stdout),
        "readiness": payload.get("readiness_status", payload.get("durability_intake_status", {}).get("status")),
    }


@pytest.mark.parametrize("serialize", (str, json.dumps), ids=("plain", "json"))
def test_windows_path_normalization_matches_fixture_form(serialize):
    root = PureWindowsPath("C:/tmp/rename-test")
    relative = "vault/04_索引/20_run_manifest_artifact.json"
    windows_text = serialize(str(root / relative))
    posix_text = serialize(f"{root.as_posix()}/{relative}")
    expected = serialize(f"<ROOT>/{relative}")
    assert normalize_artifact_text(windows_text, root) == expected
    assert normalize_artifact_text(posix_text, root) == expected


@pytest.mark.parametrize("stem", STEMS)
def test_public_imports_are_compatible(stem):
    fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    assert public_api(importlib.import_module(stem)) == fixture["public_api"][stem]


@pytest.mark.parametrize("name", FAMILY)
def test_old_module_path_is_removed(name, tmp_path):
    old_stem = f"build_r16_{name}_artifact"
    assert not (SCRIPTS / f"{old_stem}.py").exists()
    code = """
import importlib.util
import sys
sys.path.insert(0, sys.argv[1])
assert importlib.util.find_spec(sys.argv[2]) is None
assert importlib.import_module(sys.argv[3]).ARTIFACT_JSON.endswith('.json')
"""
    subprocess.run(
        [sys.executable, "-c", code, str(SCRIPTS), old_stem, f"build_{name}_artifact"],
        cwd=tmp_path, check=True,
    )


@pytest.mark.parametrize("stem", STEMS)
@pytest.mark.parametrize("scenario", ("missing", "ready"))
def test_artifact_bytes_match_before_rename(stem, scenario, monkeypatch, tmp_path, capsys):
    # Golden hashes were captured from the original builders before any rename.
    fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    actual = run_stage(importlib.import_module(stem), scenario, tmp_path, monkeypatch, capsys, fixture["inputs"])
    assert actual == fixture["outputs"][scenario][stem]


def test_python_sources_have_no_old_release_references():
    for path in SCRIPTS.rglob("*.py"):
        assert "r16" not in path.read_text(encoding="utf-8"), path
