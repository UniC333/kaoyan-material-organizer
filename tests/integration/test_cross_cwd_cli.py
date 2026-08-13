from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
KB_ENTRY = REPO_ROOT / "scripts" / "kb.py"
SCRIPTS = REPO_ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from kaoyan_kb.domain.index_freshness import fingerprint_index_inputs


def test_query_uses_selected_config_from_external_cwd(tmp_path: Path) -> None:
    external_cwd = tmp_path / "external"
    workspace = tmp_path / "workspace"
    vault_root = workspace / "vault"
    kb_root = workspace / ".kaoyan-kb"
    external_cwd.mkdir()
    vault_root.mkdir(parents=True)
    (kb_root / "indexes").mkdir(parents=True)
    locator_index = kb_root / "indexes" / "page_locator_index.json"
    locator_index.write_text(
        json.dumps(
            {
                "schema_version": "page-locator.v3",
                "generated_by": "test",
                "input_fingerprint": fingerprint_index_inputs(
                    files=[], metadata={"paper_book_metadata_dir": "metadata"}
                ),
                "entries": [],
                "sources": [],
            }
        ),
        encoding="utf-8",
    )
    config_path = workspace / "selected.config.json"
    config_path.write_text(
        json.dumps(
            {
                "workspace_root": ".",
                "vault_root": "vault",
                "kb_root": ".kaoyan-kb",
                "backup_root": ".kaoyan-backups",
                "migration_root": "_migration",
                "python_executable": sys.executable,
                "ocr_allow_remote": False,
            }
        ),
        encoding="utf-8",
    )
    child_env = os.environ.copy()
    for key in tuple(child_env):
        if key.startswith("KAOYAN_"):
            child_env.pop(key)

    command_prefix = [sys.executable, str(KB_ENTRY), "--config", str(config_path)]
    doctor_completed = subprocess.run(
        [*command_prefix, "doctor", "--format", "json"],
        cwd=external_cwd,
        env=child_env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    completed = subprocess.run(
        [
            *command_prefix,
            "query",
            "--subject",
            "数学",
            "--printed-page",
            "9999",
            "--query",
            "不存在的页码",
            "--format",
            "json",
        ],
        cwd=external_cwd,
        env=child_env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )

    payload = json.loads(completed.stdout)
    doctor = json.loads(doctor_completed.stdout)
    runtime = payload["runtime_context"]
    assert payload["answer_mode"] == "page_not_found"
    assert payload["page_anchor"]["match_status"] == "not_found"
    assert runtime["configured"] is True
    assert runtime["config_source"] == "command-line"
    assert Path(runtime["config_path"]) == config_path.resolve()
    assert Path(runtime["vault_root"]) == vault_root.resolve()
    assert Path(runtime["kb_root"]) == kb_root.resolve()
    assert runtime["page_locator_index_available"] is True
    for field in ("config_path", "workspace_root", "vault_root", "kb_root", "python_executable", "config_source"):
        assert runtime[field] == doctor[field]

    locator_index.unlink()
    unavailable_completed = subprocess.run(
        [
            *command_prefix,
            "query",
            "--subject",
            "数学",
            "--printed-page",
            "9999",
            "--query",
            "索引隔离测试",
            "--format",
            "json",
        ],
        cwd=external_cwd,
        env=child_env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    unavailable = json.loads(unavailable_completed.stdout)
    assert unavailable["answer_mode"] == "page_unavailable"
    assert unavailable["page_anchor"]["match_status"] == "unavailable"
    assert unavailable["runtime_context"]["page_locator_index_available"] is False
    assert unavailable["runtime_context"]["page_locator_unavailable_reason"] == "page_locator_index_missing"
