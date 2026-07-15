#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from build_learner_acceptance_artifact import ARTIFACT_JSON as LEARNER_ACCEPTANCE_JSON
from build_r16_changed_only_artifact import ARTIFACT_JSON as R16_T02_ARTIFACT_JSON
from build_r16_formal_usable_artifact import ARTIFACT_JSON as R16_T06_ARTIFACT_JSON
from common import INDEX_DIRNAME, default_vault_root_arg, ensure_kb_layout, learner_file_map, load_json_or_default, save_json, save_text
from learner_events import load_events

ARTIFACT_JSON = "26_r17_study_orchestration_context.json"
ARTIFACT_MD = "26_r17_study_orchestration_context.md"
ARTIFACT_ID = "r17-study-orchestration-context"
ARTIFACT_CONTRACT_VERSION = "r17.orchestration-context.v1"
POST_R17_T02_SUCCESSOR = {
    "track_id": "R17-T03",
    "title": "daily recommendation, study card, and scope-aware action packaging boundary",
    "scope": "orchestration input -> learner-day context -> daily study card packaging",
    "machine_readable_entry_point": "R17-T03 -> M8-T03",
    "status": "defined_not_started",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--vault-root", default=default_vault_root_arg())
    parser.add_argument("--as-of")
    parser.add_argument("--freshness-days", type=int, default=14)
    parser.add_argument("--format", choices=("json", "quiet"), default="json")
    return parser.parse_args()


def _load_artifact(index_root: Path, filename: str, default: dict[str, Any] | None = None) -> dict[str, Any]:
    return load_json_or_default(index_root / filename, default or {})


def _parse_iso(value: str) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _format_subjects(items: list[str]) -> list[str]:
    return [str(item).strip() for item in items if str(item).strip()]


def _build_formal_inputs(index_root: Path) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], list[str]]:
    formal_usable = _load_artifact(
        index_root,
        R16_T06_ARTIFACT_JSON,
        {
            "artifact_id": "",
            "readiness_status": "missing-r16-t06-artifact",
            "release_decision": {},
        },
    )
    learner_acceptance = _load_artifact(
        index_root,
        LEARNER_ACCEPTANCE_JSON,
        {
            "artifact_id": "",
            "readiness_status": "missing-r15-t06-artifact",
            "fact_safety_status": {"status": "unknown"},
        },
    )
    changed_only = _load_artifact(
        index_root,
        R16_T02_ARTIFACT_JSON,
        {
            "artifact_id": "",
            "readiness_status": "missing-r16-t02-artifact",
        },
    )
    layout = ensure_kb_layout()
    search_manifest = load_json_or_default(
        layout["indexes"] / "search_manifest.json",
        {
            "doc_count": 0,
            "changed_count": 0,
            "rebuild_mode": "missing",
            "performance_boundary": {"status": "missing", "reasons": []},
            "manifest_delta": {},
        },
    )

    release_decision = dict(formal_usable.get("release_decision", {}))
    runtime_ready = str(formal_usable.get("readiness_status", "")).strip() == "ready-for-r17-t01"
    learner_ready = str(learner_acceptance.get("readiness_status", "")).strip() == "ready-for-post-r15-planning"
    retrieval_ready = (
        int(search_manifest.get("doc_count", 0)) > 0
        and str(search_manifest.get("performance_boundary", {}).get("status", "")).strip() != "hard_failure"
        and str(changed_only.get("readiness_status", "")).strip() == "ready-for-r16-t03"
    )

    remaining_gaps: list[str] = []
    if not runtime_ready:
        remaining_gaps.append("R16-T06 formal-usable acceptance is not ready, so teacher-loop intake cannot rely on runtime scope yet.")
    if not learner_ready:
        remaining_gaps.append("R15-T06 learner-layer acceptance is not ready, so learner-day context is not yet grounded.")
    if not retrieval_ready:
        remaining_gaps.append("Retrieval input is not ready because search manifest or changed-only retrieval evidence is missing.")

    return (
        {
            "artifact_id": formal_usable.get("artifact_id", ""),
            "readiness_status": formal_usable.get("readiness_status", ""),
            "release_decision_status": release_decision.get("status", ""),
        },
        {
            "artifact_id": learner_acceptance.get("artifact_id", ""),
            "readiness_status": learner_acceptance.get("readiness_status", ""),
            "fact_safety_status": dict(learner_acceptance.get("fact_safety_status", {})).get("status", ""),
        },
        {
            "search_manifest_path": str(layout["indexes"] / "search_manifest.json"),
            "retrieval_ready": retrieval_ready,
            "doc_count": int(search_manifest.get("doc_count", 0)),
            "changed_count": int(search_manifest.get("changed_count", 0)),
            "rebuild_mode": search_manifest.get("rebuild_mode", ""),
            "performance_status": str(search_manifest.get("performance_boundary", {}).get("status", "")).strip(),
            "changed_only_readiness_status": changed_only.get("readiness_status", ""),
        },
        remaining_gaps,
    )


def _recommendation_status(
    *,
    subject: str,
    intake_status: str,
    event_at: datetime | None,
    as_of: datetime,
    freshness_days: int,
    in_scope_subjects: set[str],
    formal_inputs_ready: bool,
) -> tuple[str, str]:
    if subject not in in_scope_subjects:
        return "out_of_scope", "subject_not_in_current_formal_scope"
    if intake_status == "blocked":
        return "blocked", "learner_intake_blocked"
    if event_at is None:
        return "blocked", "missing_event_timestamp"
    if event_at < as_of - timedelta(days=max(freshness_days, 1)):
        return "stale", "event_outside_freshness_window"
    if intake_status == "review_only":
        return "review_only", "review_only_context_requires_followup"
    if not formal_inputs_ready:
        return "blocked", "formal_inputs_not_ready"
    return "eligible", "formal_inputs_ready_and_in_scope"


def _weak_nodes(learner_model: dict[str, Any], in_scope_subjects: set[str]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for subject, subject_model in dict(learner_model.get("subjects", {})).items():
        if subject not in in_scope_subjects:
            continue
        for node_id, node_model in dict(subject_model.get("node_mastery", {})).items():
            if str(node_model.get("mastery_band", "")).strip() == "stable":
                continue
            items.append(
                {
                    "subject": subject,
                    "node_id": node_id,
                    "title": node_model.get("title", ""),
                    "mastery_band": node_model.get("mastery_band", ""),
                    "mastery_score": float(node_model.get("mastery_score", 0.0)),
                    "question_count": int(node_model.get("question_count", 0)),
                }
            )
    items.sort(key=lambda item: (item["mastery_band"] != "weak", item["mastery_score"], -item["question_count"], item["node_id"]))
    return items


def _build_learner_day_context(
    *,
    events: list[dict[str, Any]],
    learner_model: dict[str, Any],
    as_of: datetime,
    freshness_days: int,
    in_scope_subjects: list[str],
    formal_inputs_ready: bool,
) -> tuple[dict[str, Any], dict[str, int]]:
    recommendation_inputs: list[dict[str, Any]] = []
    summary = {
        "eligible_count": 0,
        "stale_count": 0,
        "review_only_count": 0,
        "blocked_count": 0,
        "out_of_scope_count": 0,
    }
    allowed_subjects = set(in_scope_subjects)
    review_only_questions: list[dict[str, Any]] = []

    for event in events:
        if event.get("event_type") != "question_saved":
            continue
        payload = dict(event.get("payload", {}))
        intake = dict(event.get("intake_decision", {}))
        source_provenance = dict(event.get("source_provenance", {}))
        subject = str(event.get("subject", "")).strip()
        event_at = _parse_iso(str(event.get("occurred_at", "")).strip())
        status, reason = _recommendation_status(
            subject=subject,
            intake_status=str(intake.get("status", "")).strip(),
            event_at=event_at,
            as_of=as_of,
            freshness_days=freshness_days,
            in_scope_subjects=allowed_subjects,
            formal_inputs_ready=formal_inputs_ready,
        )
        question = str(payload.get("question", "")).strip()
        item = {
            "event_id": str(event.get("event_id", "")).strip(),
            "subject": subject,
            "chapter_title": str(event.get("chapter_title", "")).strip(),
            "question": question,
            "occurred_at": str(event.get("occurred_at", "")).strip(),
            "intake_status": str(intake.get("status", "")).strip(),
            "intake_reason": str(intake.get("reason", "")).strip(),
            "recommendation_eligibility": status,
            "eligibility_reason": reason,
            "answer_mode": str(source_provenance.get("answer_mode", "")).strip(),
            "citation_coverage_ok": bool(source_provenance.get("citation_coverage_ok", False)),
            "reference_count": int(source_provenance.get("reference_count", 0)),
            "syllabus_node_ids": list(source_provenance.get("syllabus_node_ids", [])),
        }
        recommendation_inputs.append(item)
        summary[f"{status}_count"] += 1
        if status == "review_only":
            review_only_questions.append(
                {
                    "event_id": item["event_id"],
                    "subject": subject,
                    "chapter_title": item["chapter_title"],
                    "question": question,
                }
            )

    recommendation_inputs.sort(key=lambda item: (item["occurred_at"], item["question"]), reverse=True)
    weak_nodes = _weak_nodes(learner_model, allowed_subjects)
    return (
        {
            "as_of": as_of.isoformat(),
            "freshness_window_days": freshness_days,
            "recommendation_inputs": recommendation_inputs,
            "due_review_set": {
                "review_only_question_count": len(review_only_questions),
                "review_only_questions": review_only_questions[:10],
                "weak_node_count": len(weak_nodes),
                "weak_nodes": weak_nodes[:10],
            },
        },
        summary,
    )


def build_payload(index_root: Path, *, as_of: datetime, freshness_days: int) -> dict[str, Any]:
    runtime_input, learner_input, retrieval_input, formal_input_gaps = _build_formal_inputs(index_root)
    release_decision = dict(_load_artifact(index_root, R16_T06_ARTIFACT_JSON).get("release_decision", {}))
    in_scope_subjects = _format_subjects(list(release_decision.get("scope_limited_to_subjects", [])))
    out_of_scope_subjects = _format_subjects(list(release_decision.get("out_of_scope_subjects", [])))

    files = learner_file_map()
    learner_model = load_json_or_default(files["learner_model"], {"subjects": {}})
    events = load_events()
    formal_inputs_ready = (
        runtime_input["readiness_status"] == "ready-for-r17-t01"
        and learner_input["readiness_status"] == "ready-for-post-r15-planning"
        and bool(retrieval_input["retrieval_ready"])
    )
    learner_day_context, recommendation_summary = _build_learner_day_context(
        events=events,
        learner_model=learner_model,
        as_of=as_of,
        freshness_days=freshness_days,
        in_scope_subjects=in_scope_subjects,
        formal_inputs_ready=formal_inputs_ready,
    )

    remaining_gaps = list(formal_input_gaps)
    if recommendation_summary["eligible_count"] <= 0:
        remaining_gaps.append("No in-scope and fresh learner recommendation input is currently eligible for daily orchestration.")
    readiness_status = "ready-for-r17-t03" if not remaining_gaps and recommendation_summary["eligible_count"] > 0 else "not-ready-for-r17-t03"

    return {
        "artifact_contract_version": ARTIFACT_CONTRACT_VERSION,
        "artifact_id": ARTIFACT_ID,
        "scope": "formal retrieval input -> learner-day context -> recommendation eligibility boundary",
        "input_contract_refs": [
            {"name": "r16_t06_formal_usable_acceptance_artifact", "version": runtime_input.get("artifact_id", "")},
            {"name": "r15_t06_learner_layer_acceptance_artifact", "version": learner_input.get("artifact_id", "")},
            {"name": "search_manifest", "version": retrieval_input.get("rebuild_mode", "")},
        ],
        "scope_filter": {
            "in_scope_subjects": in_scope_subjects,
            "out_of_scope_subjects": out_of_scope_subjects,
        },
        "formal_inputs": {
            "runtime": runtime_input,
            "learner": learner_input,
            "retrieval": retrieval_input,
        },
        "learner_day_context": learner_day_context,
        "recommendation_eligibility_summary": recommendation_summary,
        "remaining_gaps": remaining_gaps,
        "readiness_status": readiness_status,
        "post_r17_t02_successor": POST_R17_T02_SUCCESSOR,
    }


def render_markdown(payload: dict[str, Any]) -> str:
    runtime_input = dict(payload.get("formal_inputs", {}).get("runtime", {}))
    learner_input = dict(payload.get("formal_inputs", {}).get("learner", {}))
    retrieval_input = dict(payload.get("formal_inputs", {}).get("retrieval", {}))
    summary = dict(payload.get("recommendation_eligibility_summary", {}))
    successor = dict(payload.get("post_r17_t02_successor", {}))
    lines = [
        "# R17-T02 study orchestration context",
        "",
        f"- artifact_id: {payload.get('artifact_id', '')}",
        f"- readiness_status: {payload.get('readiness_status', '')}",
        f"- scope: {payload.get('scope', '')}",
        "",
        "## Formal inputs",
        "",
        f"- runtime_readiness: {runtime_input.get('readiness_status', '')}",
        f"- learner_readiness: {learner_input.get('readiness_status', '')}",
        f"- retrieval_ready: {str(bool(retrieval_input.get('retrieval_ready', False))).lower()}",
        "",
        "## Recommendation eligibility summary",
        "",
        f"- eligible_count: {summary.get('eligible_count', 0)}",
        f"- stale_count: {summary.get('stale_count', 0)}",
        f"- review_only_count: {summary.get('review_only_count', 0)}",
        f"- blocked_count: {summary.get('blocked_count', 0)}",
        f"- out_of_scope_count: {summary.get('out_of_scope_count', 0)}",
        "",
        "## Post-R17-T02 successor",
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
    as_of = _parse_iso(args.as_of) if args.as_of else datetime.now().astimezone()
    if as_of is None:
        raise SystemExit("invalid --as-of value")
    index_root = Path(args.vault_root) / INDEX_DIRNAME
    index_root.mkdir(parents=True, exist_ok=True)
    payload = build_payload(index_root, as_of=as_of, freshness_days=max(1, args.freshness_days))
    save_json(index_root / ARTIFACT_JSON, payload)
    save_text(index_root / ARTIFACT_MD, render_markdown(payload))
    result = {
        "artifact_id": payload["artifact_id"],
        "readiness_status": payload["readiness_status"],
        "recommendation_eligibility_summary": payload["recommendation_eligibility_summary"],
        "post_r17_t02_successor": payload["post_r17_t02_successor"],
    }
    if args.format == "json":
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
