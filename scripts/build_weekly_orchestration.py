#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from build_review_followups import ARTIFACT_JSON as REVIEW_FOLLOWUPS_JSON
from common import INDEX_DIRNAME, default_vault_root_arg, save_json, save_text

ARTIFACT_JSON = "29_r17_weekly_orchestration.json"
ARTIFACT_MD = "29_r17_weekly_orchestration.md"
ARTIFACT_ID = "r17-weekly-orchestration"
ARTIFACT_CONTRACT_VERSION = "r17.weekly-orchestration.v1"
POST_R17_T05_SUCCESSOR = {
    "track_id": "R17-T06",
    "title": "teacher-loop intake acceptance artifact and post-R17 successor preparation",
    "scope": "weekly orchestration -> override safety -> teacher-loop intake acceptance",
    "machine_readable_entry_point": "R17-T06 -> M8-T06",
    "status": "defined_not_started",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--vault-root", default=default_vault_root_arg())
    parser.add_argument("--plan-date", required=True)
    parser.add_argument("--override-json")
    parser.add_argument("--format", choices=("json", "quiet"), default="json")
    return parser.parse_args()


def _load_review_followups(index_root: Path) -> dict[str, Any]:
    path = index_root / REVIEW_FOLLOWUPS_JSON
    if not path.exists():
        raise SystemExit("missing review followups artifact; run build_review_followups.py first")
    return json.loads(path.read_text(encoding="utf-8"))


def _load_overrides(path: str | None) -> dict[str, Any]:
    if not path:
        return {"manual_locks": [], "operator_overrides": []}
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return {
        "manual_locks": list(payload.get("manual_locks", [])),
        "operator_overrides": list(payload.get("operator_overrides", [])),
    }


def _manual_lock_map(overrides: dict[str, Any]) -> dict[str, dict[str, Any]]:
    items: dict[str, dict[str, Any]] = {}
    for item in overrides.get("manual_locks", []):
        question = str(item.get("question", "")).strip()
        if question:
            items[question] = dict(item)
    return items


def _operator_override_map(overrides: dict[str, Any]) -> dict[str, dict[str, Any]]:
    items: dict[str, dict[str, Any]] = {}
    for item in overrides.get("operator_overrides", []):
        question = str(item.get("question", "")).strip()
        if question:
            items[question] = dict(item)
    return items


def _base_action(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "question": item.get("question", ""),
        "subject": item.get("subject", ""),
        "chapter_title": item.get("chapter_title", ""),
        "source_event_ids": list(item.get("source_event_ids", [])),
        "explanation_refs": list(item.get("explanation_refs", [])),
    }


def _build_auto(item: dict[str, Any]) -> dict[str, Any]:
    payload = _base_action(item)
    payload.update(
        {
            "auto_reschedule_allowed": True,
            "override_reason": "",
            "schedule_adjustment": {
                "action": "keep_current_week",
                "target_day_offset": 0,
            },
        }
    )
    return payload


def _build_locked(item: dict[str, Any], manual_lock: dict[str, Any]) -> dict[str, Any]:
    payload = _base_action(item)
    payload.update(
        {
            "auto_reschedule_allowed": False,
            "override_reason": str(manual_lock.get("reason", "")).strip(),
            "schedule_adjustment": {
                "action": "manual_lock",
                "target_day_offset": 0,
            },
        }
    )
    return payload


def _build_override(item: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    payload = _base_action(item)
    adjustment = dict(override.get("schedule_adjustment", {}))
    payload.update(
        {
            "auto_reschedule_allowed": False,
            "override_reason": str(override.get("override_reason", "")).strip(),
            "schedule_adjustment": {
                "action": str(adjustment.get("action", "manual_override")).strip() or "manual_override",
                "target_day_offset": int(adjustment.get("target_day_offset", 0) or 0),
            },
        }
    )
    return payload


def _build_blocked(item: dict[str, Any]) -> dict[str, Any]:
    payload = _base_action(item)
    payload.update(
        {
            "auto_reschedule_allowed": False,
            "override_reason": str(item.get("follow_up_reason", "")).strip(),
            "schedule_adjustment": {
                "action": "blocked_no_reschedule",
                "target_day_offset": 0,
            },
        }
    )
    return payload


def build_payload(index_root: Path, plan_date: str, override_json: str | None) -> dict[str, Any]:
    review_followups = _load_review_followups(index_root)
    overrides = _load_overrides(override_json)
    manual_locks = _manual_lock_map(overrides)
    operator_overrides = _operator_override_map(overrides)

    auto_reschedulable_actions: list[dict[str, Any]] = []
    locked_manual_edits: list[dict[str, Any]] = []
    override_actions: list[dict[str, Any]] = []
    scope_blocked_actions: list[dict[str, Any]] = []

    for item in list(review_followups.get("formal_follow_ups", [])):
        question = str(item.get("question", "")).strip()
        if question in operator_overrides:
            override_actions.append(_build_override(item, operator_overrides[question]))
        else:
            auto_reschedulable_actions.append(_build_auto(item))

    for item in list(review_followups.get("review_only_insights", [])):
        question = str(item.get("question", "")).strip()
        if question in manual_locks:
            locked_manual_edits.append(_build_locked(item, manual_locks[question]))
        else:
            locked_manual_edits.append(
                {
                    **_base_action(item),
                    "auto_reschedule_allowed": False,
                    "override_reason": "review_only_requires_human_lock",
                    "schedule_adjustment": {"action": "manual_lock", "target_day_offset": 0},
                }
            )

    for item in list(review_followups.get("blocked_follow_ups", [])):
        scope_blocked_actions.append(_build_blocked(item))

    readiness_status = "ready-for-r17-t06"
    return {
        "artifact_contract_version": ARTIFACT_CONTRACT_VERSION,
        "artifact_id": ARTIFACT_ID,
        "plan_date": plan_date,
        "scope": "review follow-ups -> weekly orchestration -> operator override safety boundary",
        "input_contract_refs": [
            {
                "name": "r17_t04_review_followups",
                "version": review_followups.get("artifact_contract_version", ""),
            }
        ],
        "operator_override_policy": {
            "preserve_human_owned_edits": True,
            "fact_writeback_allowed": False,
            "override_source": "optional_override_json",
        },
        "auto_reschedulable_actions": auto_reschedulable_actions,
        "operator_overrides": override_actions,
        "locked_manual_edits": locked_manual_edits,
        "scope_blocked_actions": scope_blocked_actions,
        "remaining_gaps": list(review_followups.get("remaining_gaps", [])),
        "readiness_status": readiness_status,
        "post_r17_t05_successor": POST_R17_T05_SUCCESSOR,
    }


def render_markdown(payload: dict[str, Any]) -> str:
    successor = dict(payload.get("post_r17_t05_successor", {}))
    lines = [
        "# R17-T05 weekly orchestration",
        "",
        f"- artifact_id: {payload.get('artifact_id', '')}",
        f"- plan_date: {payload.get('plan_date', '')}",
        f"- readiness_status: {payload.get('readiness_status', '')}",
        "",
        "## Weekly summary",
        "",
        f"- auto_reschedulable_actions: {len(list(payload.get('auto_reschedulable_actions', [])))}",
        f"- operator_overrides: {len(list(payload.get('operator_overrides', [])))}",
        f"- locked_manual_edits: {len(list(payload.get('locked_manual_edits', [])))}",
        f"- scope_blocked_actions: {len(list(payload.get('scope_blocked_actions', [])))}",
        "",
        "## Post-R17-T05 successor",
        "",
        f"- track_id: {successor.get('track_id', '')}",
        f"- machine_readable_entry_point: {successor.get('machine_readable_entry_point', '')}",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    args = parse_args()
    index_root = Path(args.vault_root) / INDEX_DIRNAME
    index_root.mkdir(parents=True, exist_ok=True)
    payload = build_payload(index_root, args.plan_date, args.override_json)
    save_json(index_root / ARTIFACT_JSON, payload)
    save_text(index_root / ARTIFACT_MD, render_markdown(payload))
    result = {
        "artifact_id": payload["artifact_id"],
        "plan_date": payload["plan_date"],
        "operator_override_policy": payload["operator_override_policy"],
        "auto_reschedulable_actions": payload["auto_reschedulable_actions"],
        "operator_overrides": payload["operator_overrides"],
        "locked_manual_edits": payload["locked_manual_edits"],
        "scope_blocked_actions": payload["scope_blocked_actions"],
        "readiness_status": payload["readiness_status"],
        "post_r17_t05_successor": payload["post_r17_t05_successor"],
    }
    if args.format == "json":
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
