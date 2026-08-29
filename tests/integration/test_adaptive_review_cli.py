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


def _batch_payload() -> dict:
    return {
        "contract_version": "adaptive-review-batch.v1",
        "subject": "数学",
        "chapter_title": "第二章 导数与微分",
        "review_id": "chapter-review-2026-08-27-math-ch2",
        "reviewed_at": "2026-08-27T19:30:00+08:00",
        "source": {
            "task_id": "01a0422f-12aa-7cc1-ac13-fcb4db7b3110",
            "message_ids": ["msg-review-answer"],
        },
        "items": [
            {
                "node_id": "MATH-CALC-02-INVERSE-DERIVATIVE",
                "question_id": "inverse-derivative",
                "result": "wrong",
                "fluency": "needs_remediation",
                "hint_used": False,
                "duration_minutes": 5,
                "tags": [],
                "note": "把反函数导数误说成原函数本身的倒数。",
            },
            {
                "node_id": "MATH-CALC-02-DERIVATIVE-DEFINITION",
                "question_id": "derivative-definition",
                "result": "partial",
                "fluency": "not_fluent",
                "hint_used": False,
                "duration_minutes": 5,
                "tags": [],
                "note": "题型入口识别不稳定。",
            },
            {
                "node_id": "MATH-CALC-02-LOG-DIFFERENTIATION",
                "question_id": "log-differentiation",
                "result": "partial",
                "fluency": "not_fluent",
                "hint_used": True,
                "duration_minutes": 5,
                "tags": [],
                "note": "需要提示才能启动。",
            },
            {
                "node_id": "MATH-CALC-02-CHAIN-RULE",
                "question_id": "chain-rule",
                "result": "right",
                "fluency": "fairly_fluent",
                "hint_used": False,
                "duration_minutes": 5,
                "tags": [],
                "note": "能够复述逐层求导。",
            },
        ],
    }


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


def test_chapter_review_batch_previews_then_commits_atomically_and_idempotently(tmp_path: Path) -> None:
    config, workspace, child_env = _config(tmp_path)
    batch_path = workspace / "chapter-review.json"
    batch_path.write_text(json.dumps(_batch_payload(), ensure_ascii=False), encoding="utf-8")
    command = [
        sys.executable,
        str(KB_ENTRY),
        "--config",
        str(config),
        "learner",
        "exercise",
        "--batch-json",
        str(batch_path),
        "--format",
        "json",
    ]

    preview = _run(command, cwd=workspace, env=child_env)
    events_path = workspace / ".kaoyan-kb" / "learner" / "learner_events.jsonl"
    assert preview["mode"] == "preview"
    assert preview["would_write_count"] == 4
    assert preview["written_count"] == 0
    assert preview["derived_views_rebuilt"] is False
    assert not events_path.exists()
    assert {item["due_date"] for item in preview["items"]} == {
        "2026-08-28",
        "2026-08-30",
        "2026-09-03",
    }

    committed = _run([*command[:-2], "--yes", *command[-2:]], cwd=workspace, env=child_env)
    original_events = events_path.read_text(encoding="utf-8")
    duplicate = _run([*command[:-2], "--yes", *command[-2:]], cwd=workspace, env=child_env)

    assert committed["written_count"] == 4
    assert committed["derived_views_rebuilt"] is True
    assert duplicate["written_count"] == 0
    assert duplicate["reused_count"] == 4
    assert duplicate["derived_views_rebuilt"] is False
    assert events_path.read_text(encoding="utf-8") == original_events

    events = [json.loads(line) for line in original_events.splitlines()]
    assert len(events) == 4
    assert {event["occurred_at"] for event in events} == {"2026-08-27T19:30:00+08:00"}
    assert all(event["payload"]["source_task_id"] == _batch_payload()["source"]["task_id"] for event in events)
    assert all(event["payload"]["source_message_ids"] == ["msg-review-answer"] for event in events)

    schedule = json.loads(
        (workspace / ".kaoyan-kb" / "learner" / "review_schedule.json").read_text(encoding="utf-8")
    )
    assert len(schedule["items"]) == 4
    assert all(item["source_task_id"] == _batch_payload()["source"]["task_id"] for item in schedule["items"])

    followups = _run(
        [
            sys.executable,
            str(KB_ENTRY),
            "--config",
            str(config),
            "learner",
            "review-followups",
            "--plan-date",
            "2026-08-30",
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
    assert len(followups["scheduled_reviews"]["selected"]) == 3
    assert followups["scheduled_reviews"]["estimated_minutes"] == 15


def test_invalid_chapter_review_batch_has_zero_learner_writes(tmp_path: Path) -> None:
    config, workspace, child_env = _config(tmp_path)
    payload = _batch_payload()
    payload["items"][1]["result"] = "almost"
    batch_path = workspace / "invalid-chapter-review.json"
    batch_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    completed = subprocess.run(
        [
            sys.executable,
            str(KB_ENTRY),
            "--config",
            str(config),
            "learner",
            "exercise",
            "--batch-json",
            str(batch_path),
            "--yes",
            "--format",
            "json",
        ],
        cwd=workspace,
        env=child_env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )

    assert completed.returncode != 0
    assert "no learner write was made" in completed.stderr
    assert not (workspace / ".kaoyan-kb" / "learner" / "learner_events.jsonl").exists()
