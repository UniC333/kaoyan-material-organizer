#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from typing import Any

from common import learner_file_map, load_json_or_default, now_iso, save_json, stable_fingerprint

VALID_STATUSES = {"open", "accepted", "implemented", "verified", "rejected"}
REFINEMENT_CONTRACT_VERSION = "r15.refinement.v1"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--vault-root")
    parser.add_argument("--subject")
    parser.add_argument("--chapter")
    parser.add_argument("--topn", type=int, default=20)
    parser.add_argument("--format", choices=("text", "json", "quiet"), default="json")
    return parser.parse_args()


def dedupe_strings(items: list[str]) -> list[str]:
    seen: list[str] = []
    for item in items:
        text = str(item or "").strip()
        if text and text not in seen:
            seen.append(text)
    return seen


def _node_ids_for(item: dict[str, Any]) -> list[str]:
    node_ids: list[str] = []
    for route in item.get("syllabus_route", []):
        node_id = str(route.get("node_id", "")).strip()
        if node_id and node_id not in node_ids:
            node_ids.append(node_id)
    return node_ids


def _bucket_key(subject: str, chapter_title: str, candidate_type: str, node_ids: list[str]) -> tuple[str, str, str, tuple[str, ...]]:
    return (subject, chapter_title, candidate_type, tuple(sorted(node_ids)))


def candidate_type_for(items: list[dict[str, Any]]) -> str:
    fallback_count = sum(1 for item in items if item.get("answer_mode") == "chapter_fallback")
    compare_count = sum(1 for item in items if item.get("intent") == "compare")
    diagnose_count = sum(1 for item in items if item.get("intent") == "diagnose")
    plan_count = sum(1 for item in items if item.get("intent") == "plan")
    if compare_count >= 2:
        return "补比较 claim 候选"
    if diagnose_count >= 1 and fallback_count >= 1:
        return "补诊断解释候选"
    if fallback_count >= 2:
        return "映射修正候选"
    if plan_count >= 1:
        return "补题型候选"
    return "补解释候选"


def _build_candidate(subject: str, chapter_title: str, items: list[dict[str, Any]], existing: dict[str, Any] | None = None) -> dict[str, Any]:
    candidate_type = candidate_type_for(items)
    node_ids = dedupe_strings([node_id for item in items for node_id in _node_ids_for(item)])
    refinement_key = stable_fingerprint(
        {
            "subject": subject,
            "chapter_title": chapter_title,
            "candidate_type": candidate_type,
            "node_ids": sorted(node_ids),
        }
    )
    latest = max((str(item.get("saved_at", "")).strip() for item in items), default="")
    earliest = min((str(item.get("saved_at", "")).strip() for item in items if str(item.get("saved_at", "")).strip()), default=latest)
    history = list(existing.get("review_history", [])) if existing else []
    status = str(existing.get("status", "")).strip() if existing else ""
    if status not in VALID_STATUSES:
        status = "open"
    refinement_id = str(existing.get("refinement_id", "")).strip() if existing else ""
    if not refinement_id:
        refinement_id = stable_fingerprint({"kind": "refinement", "refinement_key": refinement_key})
    return {
        "refinement_id": refinement_id,
        "refinement_key": refinement_key,
        "subject": subject,
        "chapter_title": chapter_title,
        "candidate_type": candidate_type,
        "status": status,
        "question_count": len(items),
        "node_ids": node_ids[:4],
        "questions": dedupe_strings([str(item.get("question", "")).strip() for item in items][-5:]),
        "answer_modes": dedupe_strings([str(item.get("answer_mode", "")).strip() for item in items]),
        "intents": dedupe_strings([str(item.get("intent", "")).strip() for item in items]),
        "source_event_ids": dedupe_strings([str(item.get("event_id", "")).strip() for item in items]),
        "source_intake_statuses": dedupe_strings([str(item.get("intake_status", "")).strip() for item in items]),
        "first_seen_at": str(existing.get("first_seen_at", "")).strip() if existing and existing.get("first_seen_at") else earliest,
        "last_seen_at": latest,
        "updated_at": latest,
        "review_history": history,
    }


def build_refinement_items(
    history: list[dict[str, Any]],
    existing_items: list[dict[str, Any]] | None = None,
    *,
    subject_filter: str | None = None,
    chapter_filter: str | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for item in history:
        subject = str(item.get("subject", "")).strip()
        chapter_title = str(item.get("chapter_title", "")).strip()
        if subject_filter and subject != subject_filter:
            continue
        if chapter_filter and chapter_filter not in chapter_title:
            continue
        grouped[(subject, chapter_title)].append(dict(item))

    existing_by_key: dict[str, dict[str, Any]] = {}
    for item in existing_items or []:
        key = str(item.get("refinement_key", "")).strip()
        if key:
            existing_by_key[key] = dict(item)

    built_items: list[dict[str, Any]] = []
    for (subject, chapter_title), items in grouped.items():
        if len(items) < 2:
            continue
        candidate_type = candidate_type_for(items)
        node_ids = dedupe_strings([node_id for item in items for node_id in _node_ids_for(item)])
        refinement_key = stable_fingerprint(
            {
                "subject": subject,
                "chapter_title": chapter_title,
                "candidate_type": candidate_type,
                "node_ids": sorted(node_ids),
            }
        )
        existing = existing_by_key.get(refinement_key)
        built_items.append(_build_candidate(subject, chapter_title, items, existing))

    built_items.sort(
        key=lambda item: (
            item.get("status") not in {"open", "accepted"},
            -int(item.get("question_count", 0)),
            item.get("subject", ""),
            item.get("chapter_title", ""),
            item.get("candidate_type", ""),
        )
    )
    return built_items[: max(1, limit)]


def build_refinement_queue(
    *,
    subject: str | None = None,
    chapter: str | None = None,
    limit: int = 20,
) -> dict[str, Any]:
    files = learner_file_map()
    history = load_json_or_default(files["question_history"], {"items": []}).get("items", [])
    existing_queue = load_json_or_default(files["refinement_queue"], {"items": []})
    items = build_refinement_items(
        list(history),
        list(existing_queue.get("items", [])),
        subject_filter=subject,
        chapter_filter=chapter,
        limit=limit,
    )
    payload = {
        "refinement_contract_version": REFINEMENT_CONTRACT_VERSION,
        "derived_from_question_history_count": len(list(history)),
        "updated_at": now_iso(),
        "items": items,
    }
    save_json(files["refinement_queue"], payload)
    return payload


def render_text(payload: dict[str, Any]) -> str:
    lines = ["# Refinement Queue", ""]
    items = list(payload.get("items", []))
    if not items:
        lines.append("- 当前没有待处理 refinement 候选。")
        return "\n".join(lines) + "\n"
    for index, item in enumerate(items, start=1):
        lines.extend(
            [
                f"## {index}. {item.get('candidate_type', '')}",
                "",
                f"- subject: {item.get('subject', '')}",
                f"- chapter: {item.get('chapter_title', '')}",
                f"- status: {item.get('status', '')}",
                f"- question_count: {item.get('question_count', 0)}",
                f"- node_ids: {', '.join(item.get('node_ids', []))}",
                f"- questions: {'；'.join(item.get('questions', []))}",
                "",
            ]
        )
    return "\n".join(lines)


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    args = parse_args()
    payload = build_refinement_queue(subject=args.subject, chapter=args.chapter, limit=args.topn)
    result = {
        "refinement_contract_version": payload.get("refinement_contract_version", ""),
        "derived_from_question_history_count": payload.get("derived_from_question_history_count", 0),
        "count": len(payload.get("items", [])),
        "items": payload.get("items", []),
    }
    if args.format == "json":
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif args.format == "text":
        print(render_text(payload), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
