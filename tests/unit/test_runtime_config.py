from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest


SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import config


CONFIG_ENV_KEYS = (
    "KAOYAN_CONFIG_FILE",
    "KAOYAN_CONFIG_SOURCE",
    "KAOYAN_RUNTIME_CONFIGURED",
    "KAOYAN_WORKSPACE_ROOT",
    "KAOYAN_KB_WORKSPACE",
    "KAOYAN_VAULT_ROOT",
    "KAOYAN_KB_ROOT",
    "KAOYAN_BACKUP_ROOT",
    "KAOYAN_MIGRATION_ROOT",
    "KAOYAN_SYLLABUS_ROOT",
    "KAOYAN_PYTHON",
    "KAOYAN_OCR_MONTHLY_PAGE_BUDGET",
    "KAOYAN_OCR_ALLOW_REMOTE",
)


@pytest.fixture(autouse=True)
def isolated_runtime_config(monkeypatch):
    for key in CONFIG_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    config.reset_runtime_config_cache()
    yield
    config.reset_runtime_config_cache()


def write_config(path: Path, *, kb_root: str = ".kaoyan-kb") -> None:
    path.write_text(
        json.dumps(
            {
                "workspace_root": ".",
                "vault_root": "vault",
                "kb_root": kb_root,
                "backup_root": ".backups",
                "migration_root": "_migration",
                "python_executable": sys.executable,
                "ocr_allow_remote": False,
            }
        ),
        encoding="utf-8",
    )


def test_external_cwd_falls_back_to_skill_config(monkeypatch, tmp_path: Path) -> None:
    skill_root = tmp_path / "skill"
    external = tmp_path / "external"
    skill_root.mkdir()
    external.mkdir()
    write_config(skill_root / "kaoyan.config.json")
    monkeypatch.setattr(config, "SKILL_ROOT", skill_root)
    monkeypatch.chdir(external)

    runtime = config.load_runtime_config()

    assert runtime.configured is True
    assert runtime.config_source == "skill"
    assert runtime.config_path == (skill_root / "kaoyan.config.json").resolve()
    assert runtime.workspace_root == skill_root.resolve()
    assert runtime.vault_root == (skill_root / "vault").resolve()
    assert runtime.kb_root == (skill_root / ".kaoyan-kb").resolve()


def test_cwd_config_overrides_skill_config(monkeypatch, tmp_path: Path) -> None:
    skill_root = tmp_path / "skill"
    external = tmp_path / "external"
    skill_root.mkdir()
    external.mkdir()
    write_config(skill_root / "kaoyan.config.json", kb_root="skill-kb")
    write_config(external / "kaoyan.config.json", kb_root="cwd-kb")
    monkeypatch.setattr(config, "SKILL_ROOT", skill_root)
    monkeypatch.chdir(external)

    runtime = config.load_runtime_config()

    assert runtime.config_source == "cwd"
    assert runtime.config_path == (external / "kaoyan.config.json").resolve()
    assert runtime.kb_root == (external / "cwd-kb").resolve()


def test_explicit_config_overrides_cwd_and_skill(monkeypatch, tmp_path: Path) -> None:
    skill_root = tmp_path / "skill"
    external = tmp_path / "external"
    explicit_root = tmp_path / "explicit"
    for path in (skill_root, external, explicit_root):
        path.mkdir()
    write_config(skill_root / "kaoyan.config.json", kb_root="skill-kb")
    write_config(external / "kaoyan.config.json", kb_root="cwd-kb")
    explicit_path = explicit_root / "chosen.json"
    write_config(explicit_path, kb_root="explicit-kb")
    monkeypatch.setattr(config, "SKILL_ROOT", skill_root)
    monkeypatch.chdir(external)
    monkeypatch.setenv("KAOYAN_CONFIG_FILE", str(explicit_path))

    runtime = config.load_runtime_config()

    assert runtime.config_source == "environment"
    assert runtime.config_path == explicit_path.resolve()
    assert runtime.kb_root == (explicit_root / "explicit-kb").resolve()


def test_missing_explicit_config_fails_closed(monkeypatch, tmp_path: Path) -> None:
    missing = tmp_path / "missing.json"
    monkeypatch.setenv("KAOYAN_CONFIG_FILE", str(missing))

    with pytest.raises(config.RuntimeConfigError, match="explicit runtime config does not exist"):
        config.load_runtime_config()


def test_malformed_selected_config_fails_closed(monkeypatch, tmp_path: Path) -> None:
    malformed = tmp_path / "kaoyan.config.json"
    malformed.write_text("{not-json", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    with pytest.raises(config.RuntimeConfigError, match="runtime config is not valid JSON"):
        config.load_runtime_config()


def test_missing_optional_config_is_marked_unconfigured(monkeypatch, tmp_path: Path) -> None:
    skill_root = tmp_path / "skill"
    external = tmp_path / "external"
    skill_root.mkdir()
    external.mkdir()
    monkeypatch.setattr(config, "SKILL_ROOT", skill_root)
    monkeypatch.chdir(external)

    runtime = config.load_runtime_config()

    assert runtime.configured is False
    assert runtime.config_source == "default"
    assert runtime.config_path is None


def test_explicit_zero_ocr_budget_overrides_environment(monkeypatch, tmp_path: Path) -> None:
    config_path = tmp_path / "kaoyan.config.json"
    write_config(config_path)
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    payload["ocr_monthly_page_budget"] = 0
    config_path.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("KAOYAN_OCR_MONTHLY_PAGE_BUDGET", "200")

    runtime = config.load_runtime_config()

    assert runtime.ocr_monthly_page_budget == 0


def test_remote_ocr_defaults_to_disabled_when_config_and_environment_are_absent(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(config, "_discover_config_path", lambda: (None, "default"))
    config.reset_runtime_config_cache()

    runtime = config.load_runtime_config(default_workspace=str(tmp_path))

    assert runtime.ocr_allow_remote is False


def test_remote_ocr_environment_override_is_explicit(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(config, "_discover_config_path", lambda: (None, "default"))
    monkeypatch.setenv("KAOYAN_OCR_ALLOW_REMOTE", "true")

    runtime = config.load_runtime_config(default_workspace=str(tmp_path))

    assert runtime.ocr_allow_remote is True


@pytest.mark.parametrize("configured,environment", [(False, "true"), (True, "false")])
def test_remote_ocr_selected_config_takes_precedence(monkeypatch, tmp_path, configured, environment):
    config_path = tmp_path / "kaoyan.config.json"
    write_config(config_path)
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    payload["ocr_allow_remote"] = configured
    config_path.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setenv("KAOYAN_CONFIG_FILE", str(config_path))
    monkeypatch.setenv("KAOYAN_OCR_ALLOW_REMOTE", environment)

    assert config.load_runtime_config().ocr_allow_remote is configured


def test_remote_ocr_config_true_remains_persistent(monkeypatch, tmp_path: Path) -> None:
    config_path = tmp_path / "kaoyan.config.json"
    write_config(config_path)
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    payload["ocr_allow_remote"] = True
    config_path.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    runtime = config.load_runtime_config()

    assert runtime.ocr_allow_remote is True
