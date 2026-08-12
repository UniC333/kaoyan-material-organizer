from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
KB_ENTRY = REPO_ROOT / "scripts" / "kb.py"


def _config(tmp_path: Path) -> tuple[Path, Path, dict[str, str]]:
    workspace = tmp_path / "workspace"
    vault = workspace / "vault"
    vault.mkdir(parents=True)
    config = workspace / "kaoyan.config.json"
    config.write_text(
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
    return config, workspace, child_env


def _run(command: list[str], *, cwd: Path, env: dict[str, str]) -> dict:
    completed = subprocess.run(
        command,
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    return json.loads(completed.stdout)


def test_chapter_review_is_scheduled_idempotently_and_becomes_due(tmp_path: Path) -> None:
    config, workspace, child_env = _config(tmp_path)
    prefix = [sys.executable, str(KB_ENTRY), "--config", str(config)]
    exercise = [
        *prefix,
        "learner",
        "exercise",
        "--subject",
        "数学",
        "--chapter",
        "第三章",
        "--node",
        "MATH-INTEGRAL-1",
        "--result",
        "right",
        "--context",
        "chapter_review",
        "--review-id",
        "chapter-3-review",
        "--question-id",
        "question-1",
        "--fluency",
        "not_fluent",
        "--duration-minutes",
        "8",
        "--format",
        "json",
    ]

    first = _run(exercise, cwd=workspace, env=child_env)
    duplicate = _run(exercise, cwd=workspace, env=child_env)

    assert first["effective_fluency"] == "not_fluent"
    assert first["interval_days"] == 3
    assert duplicate["event_id"] == first["event_id"]
    assert duplicate["idempotent_reuse"] is True

    followups = _run(
        [
            *prefix,
            "learner",
            "review-followups",
            "--plan-date",
            first["due_date"],
            "--time-budget-minutes",
            "20",
            "--max-items",
            "3",
            "--format",
            "json",
        ],
        cwd=workspace,
        env=child_env,
    )

    scheduled = followups["scheduled_reviews"]
    assert scheduled["status"] == "ready"
    assert scheduled["selected"][0]["node_id"] == "MATH-INTEGRAL-1"
    assert scheduled["estimated_minutes"] == 8
    assert followups["review_checkin"]["query_marks_completed"] is False
    assert followups["review_checkin"]["completed_days_last_7_days"] >= 0

    events_path = workspace / ".kaoyan-kb" / "learner" / "learner_events.jsonl"
    assert len(events_path.read_text(encoding="utf-8").splitlines()) == 1


def test_ordinary_exercise_remains_backward_compatible(tmp_path: Path) -> None:
    config, workspace, child_env = _config(tmp_path)
    payload = _run(
        [
            sys.executable,
            str(KB_ENTRY),
            "--config",
            str(config),
            "learner",
            "exercise",
            "--subject",
            "408",
            "--chapter",
            "第二章",
            "--node",
            "DS-LIST-1",
            "--result",
            "right",
            "--format",
            "json",
        ],
        cwd=workspace,
        env=child_env,
    )

    assert payload["result"] == "right"
    assert payload["fluency"] == ""
    assert payload["due_date"] == ""
