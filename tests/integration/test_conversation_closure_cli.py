from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_closure_validate_runs_through_stable_kb_entry(tmp_path: Path) -> None:
    old = "2026-08-29T22:00:22+08:00"
    vault = tmp_path / "vault"
    current_task = vault / "01_任务" / "当前任务.md"
    current_task.parent.mkdir(parents=True)
    current_task.write_text(
        "# 当前任务\n\n"
        f"- 本地 Codex 已核验至：{old}\n"
        f"- 全局会话已核验至：{old}\n"
        f"- 本地覆盖审计截至：{old}\n",
        encoding="utf-8",
    )
    manifest = {
        "schema_version": "conversation-scan-manifest.v1",
        "mode": "lightweight",
        "budget": {"page_count": 0, "ambiguity_task_count": 0, "limit_triggered": False},
        "write_authorized": False,
        "old_local_watermark": old,
        "old_global_watermark": old,
        "scan_upper": "2026-09-02T12:00:00+08:00",
        "discovery": {
            "active": {"limit": 50, "returned_count": 0, "items": []},
            "pinned": {"complete": True, "items": []},
            "archived": {"local_complete": True, "global_complete": True, "items": []},
            "local_fallback": {"schema_status": "not_needed", "schema_version": "", "items": []},
            "unavailable_hosts": [],
            "unavailable_sources": [],
        },
        "tasks": [],
        "facts": [],
    }
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    completed = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "kb.py"),
            "learner",
            "closure",
            "validate",
            "--manifest-json",
            str(manifest_path),
            "--vault-root",
            str(vault),
            "--format",
            "json",
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )

    payload = json.loads(completed.stdout)
    assert payload["schema_version"] == "conversation-closure-decision.v1"
    assert payload["coverage"]["local"]["complete"] is True
    assert payload["actions"]["write_authorized"] is False
