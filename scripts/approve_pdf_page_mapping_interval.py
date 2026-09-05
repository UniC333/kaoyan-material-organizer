#!/usr/bin/env python3
"""Approve a fully continuous, endpoint-verified PDF page mapping interval."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from common import kb_layout, load_json_or_default, now_iso, save_json
from publication_support import FileTransaction, file_input, publication_plan_fingerprint, require_matching_plan_fingerprint


def _source_sha(layout: dict[str, Path], source_id: str) -> str:
    source = load_json_or_default(layout["sources"] / f"{source_id}.json", {})
    files = [item for item in source.get("files", []) or [] if isinstance(item, dict)]
    return str((files[0] if files else {}).get("sha256") or "").strip()


def approve_interval(*, pdf_source_id: str, pdf_start: int, pdf_end: int, printed_start: int, printed_end: int, note: str,
                     yes: bool = False, expected_plan_fingerprint: str | None = None) -> dict[str, Any]:
    if min(pdf_start, pdf_end, printed_start, printed_end) < 1 or pdf_end < pdf_start or printed_end < printed_start:
        raise SystemExit("[ERROR] page ranges must be positive and ascending")
    if pdf_end - pdf_start != printed_end - printed_start:
        raise SystemExit("[ERROR] a continuous mapping must have equal PDF and printed-page span lengths")
    layout = kb_layout()
    source_sha = _source_sha(layout, pdf_source_id)
    candidates_path = layout["indexes"] / "pdf_page_mapping_candidates" / f"{pdf_source_id}.json"
    candidates = load_json_or_default(candidates_path, {})
    if not source_sha or not candidates or str(candidates.get("source_file_sha256") or "") != source_sha:
        raise SystemExit("[ERROR] candidates must exist and match the registered source SHA-256")
    candidate_items = [item for item in candidates.get("items", []) if isinstance(item, dict)]
    by_pdf = {int(item.get("pdf_page", 0) or 0): item for item in candidate_items}
    if len(by_pdf) != len(candidate_items):
        raise SystemExit("[ERROR] duplicate PDF mapping candidates")
    for pdf_page in range(pdf_start, pdf_end + 1):
        item = by_pdf.get(pdf_page, {})
        expected = printed_start + pdf_page - pdf_start
        if item.get("printed_page_candidate") != expected:
            raise SystemExit(f"[ERROR] candidate mismatch at PDF page {pdf_page}: expected printed page {expected}")
    now = now_iso()
    interval = {
        "pdf_page_start": pdf_start,
        "pdf_page_end": pdf_end,
        "printed_page_start": printed_start,
        "printed_page_end": printed_end,
        "source_file_sha256": source_sha,
        "mapping_basis": "continuous_endpoint_verified",
        "endpoint_visual_confirmation": {
            "start": {"pdf_page": pdf_start, "printed_page": printed_start},
            "end": {"pdf_page": pdf_end, "printed_page": printed_end},
            "method": "original-rendered-page-visual-review",
        },
        "note": note,
        "approved_at": now,
    }
    interval_path = layout["review_queues"] / "pdf-page-mapping-interval" / f"{pdf_source_id}.json"
    prior = load_json_or_default(interval_path, {"queue_type": "pdf-page-mapping-interval", "source_id": pdf_source_id, "items": []})
    intervals = [item for item in prior.get("items", []) if isinstance(item, dict) and (int(item.get("pdf_page_end", 0) or 0) < pdf_start or int(item.get("pdf_page_start", 0) or 0) > pdf_end)]
    intervals.append(interval)
    intervals.sort(key=lambda item: int(item["pdf_page_start"]))
    interval_payload = {"queue_type": "pdf-page-mapping-interval", "source_id": pdf_source_id, "source_file_sha256": source_sha, "items": intervals, "updated_at": now}
    review_path = layout["review_queues"] / "pdf-page-review" / f"{pdf_source_id}.json"
    prior_reviews = load_json_or_default(review_path, {"queue_type": "pdf-page-review", "source_id": pdf_source_id, "items": []})
    reviews = [item for item in prior_reviews.get("items", []) if isinstance(item, dict) and (int(item.get("pdf_page", 0) or 0) < pdf_start or int(item.get("pdf_page", 0) or 0) > pdf_end)]
    for pdf_page in range(pdf_start, pdf_end + 1):
        reviews.append({
            "pdf_page": pdf_page,
            "printed_page": printed_start + pdf_page - pdf_start,
            "review_status": "accepted",
            "page_header_verified": True,
            "source_file_sha256": source_sha,
            "mapping_basis": "continuous_endpoint_verified",
            "mapping_interval": {"pdf_page_start": pdf_start, "pdf_page_end": pdf_end, "printed_page_start": printed_start, "printed_page_end": printed_end},
            "note": note,
            "reviewed_at": now,
        })
    reviews.sort(key=lambda item: int(item["pdf_page"]))
    result = {"queue_type": "pdf-page-review", "source_id": pdf_source_id, "source_file_sha256": source_sha, "items": reviews,
              "summary": {f"{status}_count": sum(item.get("review_status") == status for item in reviews) for status in ("accepted", "pending", "rejected")}, "updated_at": now}
    plan = {
        "publication_kind": "pdf-mapping-interval",
        "arguments": {"source_id": pdf_source_id, "pdf_start": pdf_start, "pdf_end": pdf_end,
                      "printed_start": printed_start, "printed_end": printed_end, "note": note},
        "inputs": [file_input(label, path) for label, path in (
            ("source", layout["sources"] / f"{pdf_source_id}.json"), ("candidates", candidates_path),
            ("intervals", interval_path), ("page-reviews", review_path))],
        "writes": {"paths": [str(interval_path), str(review_path)]},
    }
    fingerprint = publication_plan_fingerprint(plan)
    if yes or expected_plan_fingerprint:
        require_matching_plan_fingerprint(expected_plan_fingerprint, fingerprint)
    if yes:
        transaction = FileTransaction([interval_path, review_path])
        try:
            save_json(interval_path, interval_payload, ignored_compare_keys=())
            save_json(review_path, result, ignored_compare_keys=())
        except BaseException:
            transaction.restore()
            raise
        transaction.commit()
    return {**plan, "plan_fingerprint": fingerprint, "preview_only": not yes,
            "approved_interval": interval, "interval_path": str(interval_path), "page_review_path": str(review_path),
            "approved_page_count": pdf_end - pdf_start + 1 if yes else 0, "planned_page_count": pdf_end - pdf_start + 1}


def main() -> int:
    parser = argparse.ArgumentParser(description="Approve a candidate PDF mapping interval after visual endpoint confirmation.")
    parser.add_argument("--pdf-source-id", required=True)
    parser.add_argument("--pdf-start", type=int, required=True)
    parser.add_argument("--pdf-end", type=int, required=True)
    parser.add_argument("--printed-start", type=int, required=True)
    parser.add_argument("--printed-end", type=int, required=True)
    parser.add_argument("--visual-review-note", required=True)
    parser.add_argument("--yes", action="store_true")
    parser.add_argument("--plan-fingerprint")
    parser.add_argument("--format", choices=("json", "quiet"), default="json")
    args = parser.parse_args()
    payload = approve_interval(pdf_source_id=args.pdf_source_id, pdf_start=args.pdf_start, pdf_end=args.pdf_end, printed_start=args.printed_start, printed_end=args.printed_end, note=args.visual_review_note, yes=args.yes, expected_plan_fingerprint=args.plan_fingerprint)
    if args.format == "json":
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())
