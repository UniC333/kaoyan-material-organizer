from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any
from collections.abc import Callable
from common import INDEX_DIRNAME, load_json_or_default
from config import load_runtime_config


def _build_weekly_refresh_fallback(args: argparse.Namespace, failure_message: str = "", *, run_script: Callable[..., str]) -> dict[str, Any]:
    common_args: list[str] = []
    if args.vault_root:
        common_args.extend(["--vault-root", args.vault_root])

    run_script("build_global_knowledge_registry.py", *common_args)
    run_script("build_subject_course_index.py", *common_args)
    audit_args = [*common_args, "--write-report", "--format", "json"]
    if args.subject:
        audit_args.extend(["--subject", args.subject])
    if args.chapter:
        audit_args.extend(["--chapter", args.chapter])
    audit_payload = json.loads(run_script("audit_knowledge_batches.py", *audit_args))
    run_script("build_saved_qa_registry.py", *common_args)
    refinement_args = [*common_args, "--topn", str(args.topn), "--format", "json"]
    if args.subject:
        refinement_args.extend(["--subject", args.subject])
    if args.chapter:
        refinement_args.extend(["--chapter", args.chapter])
    refinement_payload = json.loads(run_script("build_refinement_queue.py", *refinement_args))
    run_script("build_learning_dashboard.py", *common_args)
    feedback_summary_path = Path(args.vault_root or load_runtime_config().vault_root) / INDEX_DIRNAME / "19_learner_feedback_summary.json"
    feedback_summary = load_json_or_default(
        feedback_summary_path,
        {
            "feedback_contract_version": "",
            "fact_writeback_allowed": False,
            "learner_facing_summary": [],
            "review_only_insights": [],
        },
    )
    return {
        "sync_mode": "fallback-no-sync",
        "sync": {"count": 0, "chapters": []},
        "audit": audit_payload,
        "top_actions": audit_payload.get("batches", [])[: max(1, args.topn)],
        "top_reuse_candidates": [],
        "top_refinement_queue": refinement_payload.get("items", [])[: max(1, args.topn)],
        "top_master_card_candidates": [],
        "promoted_master_cards": [],
        "steps": [
            "build_global_knowledge_registry",
            "build_subject_course_index",
            "audit_knowledge_batches",
            "build_saved_qa_registry",
            "build_refinement_queue",
            "build_learning_dashboard",
        ],
        "fallback_reason": "sync_chapter_knowledge_prerequisites_missing",
        "maintenance_error": failure_message,
        "feedback_contract_version": feedback_summary.get("feedback_contract_version", ""),
        "fact_writeback_allowed": bool(feedback_summary.get("fact_writeback_allowed", False)),
        "learner_facing_summary": feedback_summary.get("learner_facing_summary", []),
        "review_only_insights": feedback_summary.get("review_only_insights", []),
    }
