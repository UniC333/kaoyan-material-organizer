#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from build_adaptive_coaching_context import ARTIFACT_JSON as ADAPTIVE_CONTEXT_JSON
from common import INDEX_DIRNAME, default_vault_root_arg, save_json, save_text

ARTIFACT_JSON = "32_r18_adaptive_coaching_packet.json"
ARTIFACT_MD = "32_r18_adaptive_coaching_packet.md"
ARTIFACT_ID = "r18-adaptive-coaching-packet"
ARTIFACT_CONTRACT_VERSION = "r18.adaptive-coaching-packet.v1"
POST_R18_T03_SUCCESSOR = {
    "track_id": "R18-T04",
    "title": "post-action feedback intake and intervention traceability boundary",
    "scope": "adaptive coaching packet -> intervention traceability -> feedback intake",
    "machine_readable_entry_point": "R18-T04 -> M9-T04",
    "status": "defined_not_started",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--vault-root", default=default_vault_root_arg())
    parser.add_argument("--plan-date", required=True)
    parser.add_argument("--format", choices=("json", "quiet"), default="json")
    return parser.parse_args()


def _load_context(index_root: Path) -> dict[str, Any]:
    path = index_root / ADAPTIVE_CONTEXT_JSON
    if not path.exists():
        raise SystemExit("missing adaptive coaching context artifact; run build_adaptive_coaching_context.py first")
    return json.loads(path.read_text(encoding="utf-8"))


def _scope_reason(item: dict[str, Any]) -> str:
    if str(item.get("intervention_eligibility", "")).strip() == "out_of_scope":
        return "subject_not_in_current_formal_scope"
    return "subject_in_current_formal_scope"


def _blocked_reasons(item: dict[str, Any]) -> list[str]:
    eligibility = str(item.get("intervention_eligibility", "")).strip()
    if eligibility in {"blocked", "out_of_scope"}:
        reason = str(item.get("eligibility_reason", "")).strip()
        return [reason] if reason else []
    return []


def _intervention_type(eligibility: str) -> str:
    mapping = {
        "eligible": "recommended",
        "stale": "review_needed",
        "review_only": "review_needed",
        "blocked": "blocked",
        "out_of_scope": "out_of_scope",
    }
    return mapping.get(eligibility, "blocked")


def _build_intervention(item: dict[str, Any]) -> dict[str, Any]:
    eligibility = str(item.get("intervention_eligibility", "")).strip()
    question = str(item.get("question", "")).strip()
    return {
        "question": question,
        "subject": item.get("subject", ""),
        "chapter_title": item.get("chapter_title", ""),
        "intervention_type": _intervention_type(eligibility),
        "intervention_eligibility": eligibility,
        "priority_reason": str(item.get("priority_reason", "")).strip(),
        "source_refs": list(item.get("source_refs", [])),
        "scope_reason": _scope_reason(item),
        "blocked_reasons": _blocked_reasons(item),
        "intervention_rationale": str(item.get("eligibility_reason", "")).strip(),
    }


def build_payload(index_root: Path, plan_date: str) -> dict[str, Any]:
    context = _load_context(index_root)
    recommended_interventions: list[dict[str, Any]] = []
    review_needed_interventions: list[dict[str, Any]] = []
    blocked_interventions: list[dict[str, Any]] = []
    out_of_scope_interventions: list[dict[str, Any]] = []

    for item in list(context.get("adaptive_context", {}).get("intervention_inputs", [])):
        intervention = _build_intervention(item)
        if intervention["intervention_type"] == "recommended":
            recommended_interventions.append(intervention)
        elif intervention["intervention_type"] == "review_needed":
            review_needed_interventions.append(intervention)
        elif intervention["intervention_type"] == "out_of_scope":
            out_of_scope_interventions.append(intervention)
        else:
            blocked_interventions.append(intervention)

    remaining_gaps = list(context.get("remaining_gaps", []))
    if not recommended_interventions:
        remaining_gaps.append("No recommended intervention is currently available for the plan date.")
    readiness_status = "ready-for-r18-t04" if recommended_interventions else "not-ready-for-r18-t04"

    return {
        "artifact_contract_version": ARTIFACT_CONTRACT_VERSION,
        "artifact_id": ARTIFACT_ID,
        "coach_packet_id": f"r18-coach-packet-{plan_date}",
        "plan_date": plan_date,
        "scope": "adaptive coaching context -> intervention packaging -> action-priority boundary",
        "input_contract_refs": [
            {
                "name": "r18_t02_adaptive_coaching_context",
                "version": context.get("artifact_contract_version", ""),
            }
        ],
        "recommended_interventions": recommended_interventions,
        "review_needed_interventions": review_needed_interventions,
        "blocked_interventions": blocked_interventions,
        "out_of_scope_interventions": out_of_scope_interventions,
        "remaining_gaps": remaining_gaps,
        "readiness_status": readiness_status,
        "post_r18_t03_successor": POST_R18_T03_SUCCESSOR,
    }


def render_markdown(payload: dict[str, Any]) -> str:
    successor = dict(payload.get("post_r18_t03_successor", {}))
    lines = [
        "# R18-T03 adaptive coaching packet",
        "",
        f"- artifact_id: {payload.get('artifact_id', '')}",
        f"- coach_packet_id: {payload.get('coach_packet_id', '')}",
        f"- plan_date: {payload.get('plan_date', '')}",
        f"- readiness_status: {payload.get('readiness_status', '')}",
        "",
        "## Intervention summary",
        "",
        f"- recommended_interventions: {len(list(payload.get('recommended_interventions', [])))}",
        f"- review_needed_interventions: {len(list(payload.get('review_needed_interventions', [])))}",
        f"- blocked_interventions: {len(list(payload.get('blocked_interventions', [])))}",
        f"- out_of_scope_interventions: {len(list(payload.get('out_of_scope_interventions', [])))}",
        "",
        "## Post-R18-T03 successor",
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
        "coach_packet_id": payload["coach_packet_id"],
        "plan_date": payload["plan_date"],
        "recommended_interventions": payload["recommended_interventions"],
        "review_needed_interventions": payload["review_needed_interventions"],
        "blocked_interventions": payload["blocked_interventions"],
        "out_of_scope_interventions": payload["out_of_scope_interventions"],
        "readiness_status": payload["readiness_status"],
        "post_r18_t03_successor": payload["post_r18_t03_successor"],
    }
    if args.format == "json":
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
