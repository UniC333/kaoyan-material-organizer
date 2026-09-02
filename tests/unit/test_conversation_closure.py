from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest


SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from kaoyan_kb.domain import conversation_closure as closure


OLD = "2026-08-29T22:00:22+08:00"
UPPER = "2026-09-02T12:00:00+08:00"
NEW = "2026-09-01T09:00:00+08:00"


def _item(task_id: str, updated_at: str = NEW, source_kind: str = "local_codex") -> dict:
    return {
        "task_id": task_id,
        "source_kind": source_kind,
        "host_id": "local" if source_kind == "local_codex" else "cloud",
        "updated_at": updated_at,
    }


def _body(
    task_id: str,
    *,
    updated_at: str = NEW,
    source_kind: str = "local_codex",
    after: str | None = None,
    turns: list[dict] | None = None,
) -> dict:
    return {
        "task_id": task_id,
        "source_kind": source_kind,
        "host_id": "local" if source_kind == "local_codex" else "cloud",
        "status": "idle",
        "updated_at_before": updated_at,
        "updated_at_after": after or updated_at,
        "history_exhausted": True,
        "next_cursor": "",
        "read_error": "",
        "body_source": "official",
        "turns": turns
        if turns is not None
        else [
            {
                "turn_id": f"turn-{task_id}",
                "started_at": updated_at,
                "user_messages": [{"message_id": f"message-{task_id}", "text": "我完成了这一节。"}],
            }
        ],
    }


def _manifest() -> dict:
    return {
        "schema_version": closure.MANIFEST_VERSION,
        "mode": "lightweight",
        "budget": {"page_count": 1, "ambiguity_task_count": 0, "limit_triggered": False},
        "write_authorized": True,
        "old_local_watermark": OLD,
        "old_global_watermark": OLD,
        "scan_upper": UPPER,
        "discovery": {
            "active": {"limit": 50, "returned_count": 1, "items": [_item("task-1")]},
            "pinned": {"complete": True, "items": []},
            "archived": {"local_complete": True, "global_complete": True, "items": []},
            "local_fallback": {"schema_status": "not_needed", "schema_version": "", "items": []},
            "unavailable_hosts": [],
            "unavailable_sources": [],
        },
        "tasks": [_body("task-1")],
        "facts": [
            {
                "task_id": "task-1",
                "turn_id": "turn-task-1",
                "user_message_id": "message-task-1",
                "fact_type": "progress",
                "subject": "数学",
                "text": "完成这一节",
                "confirmation": "explicit",
                "user_confirmed": True,
                "explicit_reference": False,
            }
        ],
    }


def _vault(tmp_path: Path) -> Path:
    vault = tmp_path / "vault"
    task = vault / "01_任务" / "当前任务.md"
    task.parent.mkdir(parents=True)
    task.write_text(
        "# 当前任务\n\n"
        f"- 本地 Codex 已核验至：{OLD}\n"
        f"- 全局会话已核验至：{OLD}\n"
        f"- 本地覆盖审计截至：{OLD}\n",
        encoding="utf-8",
    )
    return vault


def _supported_probe() -> dict:
    return {
        "schema_status": "supported",
        "compatible_schema_version": closure.LOCAL_INDEX_VERSION,
    }


def test_complete_manifest_produces_stable_fact_key_and_advance_decision(tmp_path: Path) -> None:
    manifest = _manifest()
    first = closure.validate_manifest(manifest, vault_root=_vault(tmp_path))
    second = closure.validate_manifest(manifest, vault_root=_vault(tmp_path / "again"))

    assert first["coverage"]["local"]["complete"] is True
    assert first["coverage"]["global"]["complete"] is True
    assert first["actions"]["local_watermark_advance_eligible_after_successful_writes"] is True
    assert first["actions"]["global_watermark_advance_eligible_after_successful_writes"] is True
    assert first["facts"][0]["fact_key"] == second["facts"][0]["fact_key"]
    assert first["facts"][0]["fact_key"].startswith("closure-fact-v1:")


def test_time_window_excludes_lower_boundary_and_includes_upper_boundary() -> None:
    manifest = _manifest()
    manifest["discovery"]["active"]["items"][0]["updated_at"] = UPPER
    manifest["tasks"] = [
        _body(
            "task-1",
            updated_at=UPPER,
            turns=[
                {
                    "turn_id": "at-old",
                    "started_at": OLD,
                    "user_messages": [{"message_id": "old-message", "text": "旧边界"}],
                },
                {
                    "turn_id": "at-upper",
                    "started_at": UPPER,
                    "user_messages": [{"message_id": "upper-message", "text": "上边界"}],
                },
            ],
        )
    ]
    manifest["facts"] = []

    result = closure.validate_manifest(manifest)

    assert [turn["turn_id"] for turn in result["eligible_turns"]] == ["at-upper"]


def test_turn_after_scan_upper_blocks_both_watermarks() -> None:
    manifest = _manifest()
    manifest["tasks"][0]["turns"].append(
        {
            "turn_id": "too-late",
            "started_at": "2026-09-02T12:00:01+08:00",
            "user_messages": [{"message_id": "too-late-message", "text": "扫描上界之后"}],
        }
    )
    manifest["facts"] = []

    result = closure.validate_manifest(manifest)

    assert result["coverage"]["local"]["complete"] is False
    assert result["coverage"]["global"]["complete"] is False
    assert any("turn_after_scan_upper" in gap for gap in result["coverage"]["local"]["gaps"])


def test_51_item_overflow_can_complete_local_but_not_global() -> None:
    manifest = _manifest()
    active_items = [_item(f"task-{index}") for index in range(1, 51)]
    fallback_item = _item("task-51")
    manifest["discovery"]["active"] = {"limit": 50, "returned_count": 50, "items": active_items}
    manifest["discovery"]["local_fallback"] = {
        "schema_status": "supported",
        "schema_version": closure.LOCAL_INDEX_VERSION,
        "items": [fallback_item],
    }
    manifest["tasks"] = [_body(f"task-{index}") for index in range(1, 52)]
    manifest["facts"] = []

    result = closure.validate_manifest(manifest, local_index_probe=_supported_probe())

    assert result["coverage"]["local"]["complete"] is True
    assert len(result["coverage"]["local"]["candidate_task_ids"]) == 51
    assert result["coverage"]["global"]["complete"] is False
    assert "active_listing_does_not_reach_global_watermark" in result["coverage"]["global"]["gaps"]


@pytest.mark.parametrize("returned_count", [49, 50])
def test_49_and_50_item_boundaries_can_prove_listing_coverage(returned_count: int) -> None:
    manifest = _manifest()
    if returned_count == 49:
        items = [_item(f"task-{index}") for index in range(1, 50)]
        bodies = [_body(f"task-{index}") for index in range(1, 50)]
    else:
        items = [_item("boundary-task", OLD), *[_item(f"task-{index}") for index in range(1, 50)]]
        bodies = [_body(f"task-{index}") for index in range(1, 50)]
    manifest["discovery"]["active"] = {"limit": 50, "returned_count": returned_count, "items": items}
    manifest["tasks"] = bodies
    manifest["facts"] = []

    result = closure.validate_manifest(manifest)

    assert result["coverage"]["active_listing"]["reaches_local_watermark"] is True
    assert result["coverage"]["local"]["complete"] is True
    assert result["coverage"]["global"]["complete"] is True


def test_schema_drift_in_required_local_fallback_fails_closed() -> None:
    manifest = _manifest()
    active_items = [_item(f"task-{index}") for index in range(1, 51)]
    manifest["discovery"]["active"] = {"limit": 50, "returned_count": 50, "items": active_items}
    manifest["discovery"]["local_fallback"] = {
        "schema_status": "unsupported",
        "schema_version": "future-schema",
        "items": [],
    }
    manifest["tasks"] = [_body(f"task-{index}") for index in range(1, 51)]
    manifest["facts"] = []

    result = closure.validate_manifest(manifest)

    assert result["coverage"]["local"]["complete"] is False
    assert "active_listing_truncated_without_supported_local_fallback" in result["coverage"]["local"]["gaps"]


def test_manifest_cannot_self_attest_local_fallback_compatibility() -> None:
    manifest = _manifest()
    active_items = [_item(f"task-{index}") for index in range(1, 51)]
    manifest["discovery"]["active"] = {"limit": 50, "returned_count": 50, "items": active_items}
    manifest["discovery"]["local_fallback"] = {
        "schema_status": "supported",
        "schema_version": closure.LOCAL_INDEX_VERSION,
        "items": [],
    }
    manifest["tasks"] = [_body(f"task-{index}") for index in range(1, 51)]
    manifest["facts"] = []

    result = closure.validate_manifest(manifest)

    assert result["coverage"]["local_fallback"]["probe_verified"] is False
    assert result["coverage"]["local"]["complete"] is False


def test_global_unavailable_source_does_not_block_local_watermark() -> None:
    manifest = _manifest()
    manifest["discovery"]["unavailable_sources"] = [{"id": "chatgpt", "scope": "global_other"}]

    result = closure.validate_manifest(manifest)

    assert result["coverage"]["local"]["complete"] is True
    assert result["coverage"]["global"]["complete"] is False
    assert result["actions"]["local_watermark_advance_eligible_after_successful_writes"] is True
    assert result["actions"]["global_watermark_advance_eligible_after_successful_writes"] is False


@pytest.mark.parametrize("explicit_reference, expected", [(True, True), (False, False)])
def test_explicit_cloud_fact_can_write_without_advancing_global_watermark(
    explicit_reference: bool,
    expected: bool,
) -> None:
    manifest = _manifest()
    manifest["discovery"]["active"]["items"] = [_item("cloud-task", source_kind="chatgpt")]
    manifest["discovery"]["archived"]["global_complete"] = False
    manifest["tasks"] = [_body("cloud-task", source_kind="chatgpt")]
    manifest["facts"] = [
        {
            "task_id": "cloud-task",
            "turn_id": "turn-cloud-task",
            "user_message_id": "message-cloud-task",
            "fact_type": "progress",
            "subject": "英语",
            "text": "完成长难句练习",
            "confirmation": "explicit",
            "user_confirmed": True,
            "explicit_reference": explicit_reference,
        }
    ]

    result = closure.validate_manifest(manifest)

    assert result["coverage"]["local"]["complete"] is True
    assert result["coverage"]["global"]["complete"] is False
    assert result["facts"][0]["write_eligible"] is expected
    assert result["actions"]["global_watermark_advance_eligible_after_successful_writes"] is False


def test_task_updated_during_read_fails_closed() -> None:
    manifest = _manifest()
    manifest["tasks"] = [_body("task-1", after="2026-09-01T09:00:01+08:00")]
    manifest["facts"] = []

    result = closure.validate_manifest(manifest)

    assert any("updated_during_read" in gap for gap in result["coverage"]["local"]["gaps"])
    assert result["actions"]["local_watermark_advance_eligible_after_successful_writes"] is False


def test_running_task_and_unfinished_cursor_fail_closed() -> None:
    manifest = _manifest()
    manifest["tasks"][0]["status"] = "running"
    manifest["tasks"][0]["history_exhausted"] = False
    manifest["tasks"][0]["next_cursor"] = "older-page"
    manifest["facts"] = []

    result = closure.validate_manifest(manifest)

    gaps = result["coverage"]["local"]["gaps"]
    assert any("unstable_status:running" in gap for gap in gaps)
    assert any("watermark_not_crossed" in gap for gap in gaps)


def test_incomplete_local_archived_listing_blocks_local_and_global_coverage() -> None:
    manifest = _manifest()
    manifest["discovery"]["archived"]["local_complete"] = False

    result = closure.validate_manifest(manifest)

    assert "local_archived_listing_incomplete" in result["coverage"]["local"]["gaps"]
    assert "local_archived_listing_incomplete" in result["coverage"]["global"]["gaps"]


def test_pinned_duplicate_is_deduplicated_by_task_id() -> None:
    manifest = _manifest()
    manifest["discovery"]["pinned"]["items"] = [_item("task-1")]

    result = closure.validate_manifest(manifest)

    assert result["report"]["discovered_task_count"] == 1
    assert result["coverage"]["local"]["candidate_task_ids"] == ["task-1"]


def _build_index_fixture(root: Path, *, drift: bool = False) -> None:
    root.mkdir(parents=True)
    updated_ms = int(closure.parse_timestamp(NEW, "fixture timestamp").timestamp() * 1000)
    state = sqlite3.connect(root / "state_5.sqlite")
    state.execute(
        """
        CREATE TABLE threads (
            id TEXT PRIMARY KEY,
            rollout_path TEXT NOT NULL,
            updated_at INTEGER NOT NULL,
            updated_at_ms INTEGER,
            archived INTEGER NOT NULL,
            has_user_event INTEGER NOT NULL,
            agent_path TEXT,
            thread_source TEXT
        )
        """
    )
    if drift:
        state.execute("ALTER TABLE threads DROP COLUMN rollout_path")
    else:
        for name in ("user.jsonl", "automation.jsonl", "subagent.jsonl"):
            (root / name).write_text("{}\n", encoding="utf-8")
        state.executemany(
            "INSERT INTO threads VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                ("user-task", str(root / "user.jsonl"), 0, updated_ms, 0, 0, None, "user"),
                ("automation-task", str(root / "automation.jsonl"), 0, updated_ms + 1000, 0, 1, None, "automation"),
                ("subagent-task", str(root / "subagent.jsonl"), 0, updated_ms + 2000, 0, 1, "/root/helper", "subagent"),
            ],
        )
    state.commit()
    state.close()

    history = sqlite3.connect(root / "thread_history_1.sqlite")
    history.execute(
        "CREATE TABLE thread_turns (thread_id TEXT, turn_id TEXT, status TEXT, started_at INTEGER)"
    )
    history.execute(
        "CREATE TABLE thread_items (thread_id TEXT, turn_id TEXT, item_id TEXT, created_at_ms INTEGER, item_json TEXT)"
    )
    history.commit()
    history.close()
    (root / "session_index.jsonl").write_text(
        json.dumps({"id": "user-task", "updated_at": NEW}) + "\n",
        encoding="utf-8",
    )


def test_local_index_probe_and_discovery_are_read_only_and_exclude_subagents(tmp_path: Path) -> None:
    home = tmp_path / "codex"
    _build_index_fixture(home)

    probe = closure.probe_local_indexes(home)
    discovered = closure.discover_local_tasks(
        after="2026-09-01T08:59:00+08:00",
        through="2026-09-01T09:01:00+08:00",
        codex_home=home,
    )

    assert probe["schema_status"] == "supported"
    assert discovered["compatible_schema_version"] == closure.LOCAL_INDEX_VERSION
    assert {item["task_id"] for item in discovered["tasks"]} == {"user-task", "automation-task"}


def test_local_index_schema_drift_is_reported_and_discovery_stops(tmp_path: Path) -> None:
    home = tmp_path / "codex"
    _build_index_fixture(home, drift=True)

    probe = closure.probe_local_indexes(home)

    assert probe["schema_status"] == "unsupported"
    with pytest.raises(closure.ClosureValidationError, match="not compatible"):
        closure.discover_local_tasks(after=OLD, through=UPPER, codex_home=home)


def test_unknown_task_status_is_not_treated_as_stable() -> None:
    manifest = _manifest()
    manifest["tasks"][0]["status"] = "future_status"
    manifest["facts"] = []

    result = closure.validate_manifest(manifest)

    assert any("unstable_status:future_status" in gap for gap in result["coverage"]["local"]["gaps"])


def test_manifest_watermark_must_match_vault_state(tmp_path: Path) -> None:
    manifest = _manifest()
    manifest["old_local_watermark"] = "2026-08-30T00:00:00+08:00"

    with pytest.raises(closure.ClosureValidationError, match="does not match current task"):
        closure.validate_manifest(manifest, vault_root=_vault(tmp_path))


def test_lightweight_limit_trigger_blocks_writes_and_watermarks() -> None:
    manifest = _manifest()
    manifest["budget"]["limit_triggered"] = True

    result = closure.validate_manifest(manifest)

    assert result["coverage"]["local"]["complete"] is False
    assert "lightweight_limit_triggered" in result["coverage"]["local"]["gaps"]
    assert result["facts"][0]["write_eligible"] is False
    assert result["actions"]["local_watermark_advance_eligible_after_successful_writes"] is False
