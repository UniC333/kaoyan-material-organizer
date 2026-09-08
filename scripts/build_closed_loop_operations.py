#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from kaoyan_kb.domain.learner_artifact_files import COACHING_FEEDBACK_LOOP as FEEDBACK_LOOP_JSON
from kaoyan_kb.domain.artifact_support import load_overrides as _load_overrides
from kaoyan_kb.cli.learner_artifact_runner import run_artifact
from common import default_vault_root_arg

from kaoyan_kb.domain.learner_artifact_files import CLOSED_LOOP_OPERATIONS as ARTIFACT_JSON
ARTIFACT_MD = "34_r18_closed_loop_operations.md"
ARTIFACT_ID = "r18-closed-loop-operations"
ARTIFACT_CONTRACT_VERSION = "r18.closed-loop-operations.v1"
POST_R18_T05_SUCCESSOR = {
    "track_id": "R18-T06",
    "title": "adaptive-coaching acceptance artifact and post-R18 successor preparation",
    "scope": "closed-loop operations -> cadence safety -> adaptive-coaching acceptance",
    "machine_readable_entry_point": "R18-T06 -> M9-T06",
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
        raise SystemExit("missing coaching feedback loop artifact; run build_coaching_feedback_loop.py first")
    return json.loads(path.read_text(encoding="utf-8"))


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
        "intervention_refs": list(item.get("intervention_refs", [])),
        "source_refs": list(item.get("source_refs", [])),
    }


def _build_auto(item: dict[str, Any]) -> dict[str, Any]:
    payload = _base_item(item)
    result = str(item.get("result", "")).strip()
    action = "keep_current_cadence"
    target_day_offset = 0
    if result == "wrong":
        action = "retry_soon"
        target_day_offset = 1
    elif result == "partial":
        action = "review_next_cycle"
        target_day_offset = 2
    payload.update(
        {
            "auto_adjust_allowed": True,
            "override_reason": "",
            "cadence_adjustment": {
                "action": action,
                "target_day_offset": target_day_offset,
            },
        }
    )
    return payload


def _build_locked(item: dict[str, Any], manual_lock: dict[str, Any]) -> dict[str, Any]:
    payload = _base_item(item)
    payload.update(
        {
            "auto_adjust_allowed": False,
            "override_reason": str(manual_lock.get("reason", "")).strip(),
            "cadence_adjustment": {
                "action": "manual_lock",
                "target_day_offset": 0,
            },
        }
    )
    return payload


def _build_override(item: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    payload = _base_item(item)
    adjustment = dict(override.get("cadence_adjustment", {}))
    payload.update(
        {
            "auto_adjust_allowed": False,
            "override_reason": str(override.get("override_reason", "")).strip(),
            "cadence_adjustment": {
                "action": str(adjustment.get("action", "manual_override")).strip() or "manual_override",
                "target_day_offset": int(adjustment.get("target_day_offset", 0) or 0),
            },
        }
    )
    return payload


def _build_blocked(item: dict[str, Any]) -> dict[str, Any]:
    payload = _base_item(item)
    payload.update(
        {
            "auto_adjust_allowed": False,
            "override_reason": str(item.get("follow_up_reason", "")).strip(),
            "cadence_adjustment": {
                "action": "blocked_no_adjustment",
                "target_day_offset": 0,
            },
        }
    )
    return payload


def build_payload(index_root: Path, plan_date: str, override_json: str | None) -> dict[str, Any]:
    feedback_loop = _load_feedback_loop(index_root)
    overrides = _load_overrides(override_json)
    manual_locks = _manual_lock_map(overrides)
    operator_overrides = _operator_override_map(overrides)

    auto_adjustable_operations: list[dict[str, Any]] = []
    locked_manual_edits: list[dict[str, Any]] = []
    override_items: list[dict[str, Any]] = []
    scope_blocked_operations: list[dict[str, Any]] = []

    for item in list(feedback_loop.get("formal_feedback_intake", [])):
        question = str(item.get("question", "")).strip()
        auto_adjustable_operations.append(_build_auto(item))
        if question in operator_overrides:
            override_items.append(_build_override(item, operator_overrides[question]))

    for item in list(feedback_loop.get("review_only_feedback", [])):
        question = str(item.get("question", "")).strip()
        if question in manual_locks:
            locked_manual_edits.append(_build_locked(item, manual_locks[question]))
        else:
            locked_manual_edits.append(
                {
                    **_base_item(item),
                    "auto_adjust_allowed": False,
                    "override_reason": "review_only_requires_human_lock",
                    "cadence_adjustment": {"action": "manual_lock", "target_day_offset": 0},
                }
            )

    for item in list(feedback_loop.get("out_of_scope_feedback", [])):
        scope_blocked_operations.append(_build_blocked(item))
    for item in list(feedback_loop.get("blocked_follow_ups", [])):
        scope_blocked_operations.append(_build_blocked(item))

    return {
        "artifact_contract_version": ARTIFACT_CONTRACT_VERSION,
        "artifact_id": ARTIFACT_ID,
        "plan_date": plan_date,
        "scope": "coaching feedback loop -> cadence adjustment -> operator override safety boundary",
        "input_contract_refs": [
            {
                "name": "r18_t04_coaching_feedback_loop",
                "version": feedback_loop.get("artifact_contract_version", ""),
            }
        ],
        "operator_override_policy": {
            "preserve_human_owned_edits": True,
            "fact_writeback_allowed": False,
            "override_source": "optional_override_json",
        },
        "auto_adjustable_operations": auto_adjustable_operations,
        "operator_overrides": override_items,
        "locked_manual_edits": locked_manual_edits,
        "scope_blocked_operations": scope_blocked_operations,
        "remaining_gaps": list(feedback_loop.get("remaining_gaps", [])),
        "readiness_status": "ready-for-r18-t06",
        "post_r18_t05_successor": POST_R18_T05_SUCCESSOR,
    }


def render_markdown(payload: dict[str, Any]) -> str:
    successor = dict(payload.get("post_r18_t05_successor", {}))
    lines = [
        "# R18-T05 closed-loop operations",
        "",
        f"- artifact_id: {payload.get('artifact_id', '')}",
        f"- plan_date: {payload.get('plan_date', '')}",
        f"- readiness_status: {payload.get('readiness_status', '')}",
        "",
        "## Closed-loop summary",
        "",
        f"- auto_adjustable_operations: {len(list(payload.get('auto_adjustable_operations', [])))}",
        f"- operator_overrides: {len(list(payload.get('operator_overrides', [])))}",
        f"- locked_manual_edits: {len(list(payload.get('locked_manual_edits', [])))}",
        f"- scope_blocked_operations: {len(list(payload.get('scope_blocked_operations', [])))}",
        "",
        "## Post-R18-T05 successor",
        "",
        f"- track_id: {successor.get('track_id', '')}",
        f"- machine_readable_entry_point: {successor.get('machine_readable_entry_point', '')}",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    return run_artifact(
        parse_args,
        lambda index_root, args: build_payload(index_root, args.plan_date, args.override_json),
        render_markdown, ARTIFACT_JSON, ARTIFACT_MD,
        (
            'artifact_id', 'plan_date', 'operator_override_policy', 'auto_adjustable_operations',
            'operator_overrides', 'locked_manual_edits', 'scope_blocked_operations',
            'readiness_status', 'post_r18_t05_successor',
        ),
    )


if __name__ == "__main__":
    raise SystemExit(main())
