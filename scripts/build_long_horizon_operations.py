#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from build_tutoring_feedback_loop import ARTIFACT_JSON as FEEDBACK_LOOP_JSON
from common import INDEX_DIRNAME, default_vault_root_arg, save_json, save_text

ARTIFACT_JSON = "39_r19_long_horizon_operations.json"
ARTIFACT_MD = "39_r19_long_horizon_operations.md"
ARTIFACT_ID = "r19-long-horizon-operations"
ARTIFACT_CONTRACT_VERSION = "r19.long-horizon-operations.v1"
POST_R19_T05_SUCCESSOR = {
    "track_id": "R19-T06",
    "title": "longitudinal-tutoring acceptance artifact and post-R19 successor preparation",
    "scope": "goal-adjustment governance -> long-horizon operations -> longitudinal tutoring acceptance",
    "machine_readable_entry_point": "R19-T06 -> M10-T06",
    "status": "defined_not_started",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--vault-root", default=default_vault_root_arg())
    parser.add_argument("--plan-date", required=True)
    parser.add_argument("--override-json")
    parser.add_argument("--format", choices=("json", "quiet"), default="json")
    return parser.parse_args()


def _load_feedback_loop(index_root: Path) -> dict[str, Any]:
    path = index_root / FEEDBACK_LOOP_JSON
    if not path.exists():
        raise SystemExit("missing tutoring feedback loop artifact; run build_tutoring_feedback_loop.py first")
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


def _base_item(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "question": item.get("question", ""),
        "subject": item.get("subject", ""),
        "chapter_title": item.get("chapter_title", ""),
        "cycle_feedback_status": item.get("cycle_feedback_status", ""),
        "goal_progress_log": dict(item.get("goal_progress_log", {})),
        "strategy_refs": list(item.get("strategy_refs", [])),
        "source_refs": list(item.get("source_refs", [])),
    }


def _auto_goal_adjustment(item: dict[str, Any]) -> dict[str, Any]:
    payload = _base_item(item)
    result = str(item.get("result", "")).strip()
    action = "keep_current_goal"
    target_cycle_offset = 0
    if result == "wrong":
        action = "retry_current_goal_next_cycle"
        target_cycle_offset = 1
    elif result == "partial":
        action = "review_current_goal_next_cycle"
        target_cycle_offset = 1
    payload.update(
        {
            "auto_goal_adjust_allowed": True,
            "override_reason": "",
            "goal_adjustment": {
                "action": action,
                "target_cycle_offset": target_cycle_offset,
            },
        }
    )
    return payload


def _locked_manual_edit(item: dict[str, Any], reason: str) -> dict[str, Any]:
    payload = _base_item(item)
    payload.update(
        {
            "auto_goal_adjust_allowed": False,
            "override_reason": reason,
            "goal_adjustment": {
                "action": "manual_lock",
                "target_cycle_offset": 0,
            },
        }
    )
    return payload


def _operator_override(item: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    payload = _base_item(item)
    adjustment = dict(override.get("goal_adjustment", {}))
    payload.update(
        {
            "auto_goal_adjust_allowed": False,
            "override_reason": str(override.get("override_reason", "")).strip(),
            "goal_adjustment": {
                "action": str(adjustment.get("action", "manual_override")).strip() or "manual_override",
                "target_cycle_offset": int(adjustment.get("target_cycle_offset", 0) or 0),
            },
        }
    )
    return payload


def _scope_blocked(item: dict[str, Any]) -> dict[str, Any]:
    payload = _base_item(item)
    payload.update(
        {
            "auto_goal_adjust_allowed": False,
            "override_reason": str(item.get("follow_up_reason", "")).strip(),
            "goal_adjustment": {
                "action": "blocked_no_goal_adjustment",
                "target_cycle_offset": 0,
            },
        }
    )
    return payload


def build_payload(index_root: Path, plan_date: str, override_json: str | None) -> dict[str, Any]:
    feedback_loop = _load_feedback_loop(index_root)
    overrides = _load_overrides(override_json)
    manual_locks = _manual_lock_map(overrides)
    operator_overrides = _operator_override_map(overrides)

    long_horizon_operations: list[dict[str, Any]] = []
    locked_manual_edits: list[dict[str, Any]] = []
    override_items: list[dict[str, Any]] = []
    scope_blocked_operations: list[dict[str, Any]] = []

    for item in list(feedback_loop.get("formal_cycle_feedback", [])):
        question = str(item.get("question", "")).strip()
        long_horizon_operations.append(_auto_goal_adjustment(item))
        if question in operator_overrides:
            override_items.append(_operator_override(item, operator_overrides[question]))

    for item in list(feedback_loop.get("review_only_feedback", [])):
        question = str(item.get("question", "")).strip()
        reason = "review_only_requires_human_goal_lock"
        if question in manual_locks:
            reason = str(manual_locks[question].get("reason", "")).strip() or reason
        locked_manual_edits.append(_locked_manual_edit(item, reason))

    for item in list(feedback_loop.get("out_of_scope_feedback", [])):
        scope_blocked_operations.append(_scope_blocked(item))
    for item in list(feedback_loop.get("blocked_follow_ups", [])):
        scope_blocked_operations.append(_scope_blocked(item))

    return {
        "artifact_contract_version": ARTIFACT_CONTRACT_VERSION,
        "artifact_id": ARTIFACT_ID,
        "plan_date": plan_date,
        "scope": "tutoring feedback loop -> goal-adjustment governance -> long-horizon study operations boundary",
        "input_contract_refs": [
            {
                "name": "r19_t04_tutoring_feedback_loop",
                "version": feedback_loop.get("artifact_contract_version", ""),
            }
        ],
        "goal_adjustment_policy": {
            "preserve_human_owned_edits": True,
            "fact_writeback_allowed": False,
            "override_source": "optional_override_json",
        },
        "long_horizon_operations": long_horizon_operations,
        "operator_overrides": override_items,
        "locked_manual_edits": locked_manual_edits,
        "scope_blocked_operations": scope_blocked_operations,
        "remaining_gaps": list(feedback_loop.get("remaining_gaps", [])),
        "readiness_status": "ready-for-r19-t06",
        "post_r19_t05_successor": POST_R19_T05_SUCCESSOR,
    }


def render_markdown(payload: dict[str, Any]) -> str:
    successor = dict(payload.get("post_r19_t05_successor", {}))
    lines = [
        "# R19-T05 long-horizon operations",
        "",
        f"- artifact_id: {payload.get('artifact_id', '')}",
        f"- plan_date: {payload.get('plan_date', '')}",
        f"- readiness_status: {payload.get('readiness_status', '')}",
        "",
        "## Goal-adjustment summary",
        "",
        f"- long_horizon_operations: {len(list(payload.get('long_horizon_operations', [])))}",
        f"- operator_overrides: {len(list(payload.get('operator_overrides', [])))}",
        f"- locked_manual_edits: {len(list(payload.get('locked_manual_edits', [])))}",
        f"- scope_blocked_operations: {len(list(payload.get('scope_blocked_operations', [])))}",
        "",
        "## Post-R19-T05 successor",
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
        "goal_adjustment_policy": payload["goal_adjustment_policy"],
        "long_horizon_operations": payload["long_horizon_operations"],
        "operator_overrides": payload["operator_overrides"],
        "locked_manual_edits": payload["locked_manual_edits"],
        "scope_blocked_operations": payload["scope_blocked_operations"],
        "readiness_status": payload["readiness_status"],
        "post_r19_t05_successor": payload["post_r19_t05_successor"],
    }
    if args.format == "json":
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
