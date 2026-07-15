#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from build_daily_study_card import ARTIFACT_JSON as DAILY_CARD_JSON
from common import INDEX_DIRNAME, default_vault_root_arg, save_json, save_text

ARTIFACT_JSON = "28_r17_review_followups.json"
ARTIFACT_MD = "28_r17_review_followups.md"
ARTIFACT_ID = "r17-review-followups"
ARTIFACT_CONTRACT_VERSION = "r17.review-followups.v1"
POST_R17_T04_SUCCESSOR = {
    "track_id": "R17-T05",
    "title": "weekly orchestration, schedule adjustment, and human override boundary",
    "scope": "review follow-ups -> weekly orchestration -> override policy",
    "machine_readable_entry_point": "R17-T05 -> M8-T05",
    "status": "defined_not_started",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--vault-root", default=default_vault_root_arg())
    parser.add_argument("--plan-date", required=True)
    parser.add_argument("--format", choices=("json", "quiet"), default="json")
    return parser.parse_args()


def _load_daily_card(index_root: Path) -> dict[str, Any]:
    path = index_root / DAILY_CARD_JSON
    if not path.exists():
        raise SystemExit("missing daily study card artifact; run build_daily_study_card.py first")
    return json.loads(path.read_text(encoding="utf-8"))


def _explanation_refs(action: dict[str, Any]) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    for ref in list(action.get("source_refs", [])):
        refs.append(dict(ref))
    for ref in list(action.get("weak_point_refs", [])):
        refs.append({"ref_type": "weak_point", **dict(ref)})
    deduped: list[dict[str, Any]] = []
    seen: set[str] = set()
    for ref in refs:
        marker = json.dumps(ref, ensure_ascii=False, sort_keys=True)
        if marker in seen:
            continue
        seen.add(marker)
        deduped.append(ref)
    return deduped


def _build_formal_follow_up(action: dict[str, Any]) -> dict[str, Any]:
    reason = ""
    decision = "continue_as_planned"
    if action.get("recommendation_eligibility") == "stale":
        decision = "needs_refresh_review"
        reason = "event_outside_freshness_window"
    else:
        reason = str(action.get("why_this_action", "")).strip()
    return {
        "question": action.get("question", ""),
        "subject": action.get("subject", ""),
        "chapter_title": action.get("chapter_title", ""),
        "review_decision": decision,
        "explanation_refs": _explanation_refs(action),
        "follow_up_reason": reason,
        "source_event_ids": [action.get("event_id", "")] if str(action.get("event_id", "")).strip() else [],
        "fact_writeback_allowed": False,
    }


def _build_review_only(action: dict[str, Any]) -> dict[str, Any]:
    return {
        "question": action.get("question", ""),
        "subject": action.get("subject", ""),
        "chapter_title": action.get("chapter_title", ""),
        "review_decision": "review_only_insight",
        "explanation_refs": _explanation_refs(action),
        "follow_up_reason": str(action.get("why_this_action", "")).strip(),
        "source_event_ids": [action.get("event_id", "")] if str(action.get("event_id", "")).strip() else [],
        "fact_writeback_allowed": False,
    }


def _build_blocked(action: dict[str, Any]) -> dict[str, Any]:
    blocked_reasons = [str(item).strip() for item in list(action.get("blocked_reasons", [])) if str(item).strip()]
    if not blocked_reasons:
        blocked_reasons.append(str(action.get("scope_reason", "")).strip())
    return {
        "question": action.get("question", ""),
        "subject": action.get("subject", ""),
        "chapter_title": action.get("chapter_title", ""),
        "review_decision": "blocked_follow_up",
        "explanation_refs": _explanation_refs(action),
        "follow_up_reason": ",".join(blocked_reasons),
        "source_event_ids": [action.get("event_id", "")] if str(action.get("event_id", "")).strip() else [],
        "fact_writeback_allowed": False,
    }


def build_payload(index_root: Path, plan_date: str) -> dict[str, Any]:
    daily_card = _load_daily_card(index_root)
    formal_follow_ups: list[dict[str, Any]] = []
    review_only_insights: list[dict[str, Any]] = []
    blocked_follow_ups: list[dict[str, Any]] = []

    for action in list(daily_card.get("recommended_actions", [])):
        formal_follow_ups.append(_build_formal_follow_up(action))
    for action in list(daily_card.get("review_needed_actions", [])):
        if str(action.get("recommendation_eligibility", "")).strip() == "review_only":
            review_only_insights.append(_build_review_only(action))
        else:
            formal_follow_ups.append(_build_formal_follow_up(action))
    for action in list(daily_card.get("blocked_actions", [])):
        blocked_follow_ups.append(_build_blocked(action))
    for action in list(daily_card.get("out_of_scope_actions", [])):
        blocked_follow_ups.append(_build_blocked(action))

    remaining_gaps = list(daily_card.get("remaining_gaps", []))
    readiness_status = "ready-for-r17-t05" if formal_follow_ups else "not-ready-for-r17-t05"
    return {
        "artifact_contract_version": ARTIFACT_CONTRACT_VERSION,
        "artifact_id": ARTIFACT_ID,
        "plan_date": plan_date,
        "scope": "daily recommendation -> review loop -> explanation and follow-up boundary",
        "input_contract_refs": [
            {
                "name": "r17_t03_daily_study_card",
                "version": daily_card.get("artifact_contract_version", ""),
            }
        ],
        "formal_follow_ups": formal_follow_ups,
        "review_only_insights": review_only_insights,
        "blocked_follow_ups": blocked_follow_ups,
        "fact_writeback_allowed": False,
        "remaining_gaps": remaining_gaps,
        "readiness_status": readiness_status,
        "post_r17_t04_successor": POST_R17_T04_SUCCESSOR,
    }


def render_markdown(payload: dict[str, Any]) -> str:
    successor = dict(payload.get("post_r17_t04_successor", {}))
    lines = [
        "# R17-T04 review follow-ups",
        "",
        f"- artifact_id: {payload.get('artifact_id', '')}",
        f"- plan_date: {payload.get('plan_date', '')}",
        f"- readiness_status: {payload.get('readiness_status', '')}",
        "",
        "## Follow-up summary",
        "",
        f"- formal_follow_ups: {len(list(payload.get('formal_follow_ups', [])))}",
        f"- review_only_insights: {len(list(payload.get('review_only_insights', [])))}",
        f"- blocked_follow_ups: {len(list(payload.get('blocked_follow_ups', [])))}",
        "",
        "## Post-R17-T04 successor",
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
    payload = build_payload(index_root, args.plan_date)
    save_json(index_root / ARTIFACT_JSON, payload)
    save_text(index_root / ARTIFACT_MD, render_markdown(payload))
    result = {
        "artifact_id": payload["artifact_id"],
        "plan_date": payload["plan_date"],
        "formal_follow_ups": payload["formal_follow_ups"],
        "review_only_insights": payload["review_only_insights"],
        "blocked_follow_ups": payload["blocked_follow_ups"],
        "fact_writeback_allowed": payload["fact_writeback_allowed"],
        "readiness_status": payload["readiness_status"],
        "post_r17_t04_successor": payload["post_r17_t04_successor"],
    }
    if args.format == "json":
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
