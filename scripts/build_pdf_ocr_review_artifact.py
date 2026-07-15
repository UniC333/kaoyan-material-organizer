#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

from common import ensure_kb_layout, load_json_or_default, now_iso, sanitize_name, save_json, save_text
from ocr.review import queue_review_items

ARTIFACT_ID = "r23-pdf-ocr-review-queue-artifact"
ARTIFACT_CONTRACT_VERSION = "r23.pdf-ocr-review-queue.v1"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Summarize a bridged PDF OCR review queue and emit a classify-handoff ledger."
    )
    parser.add_argument("--subject", required=True)
    parser.add_argument("--book-title", required=True)
    parser.add_argument("--pdf-source-id", default="")
    parser.add_argument("--bridge-report-path", default="")
    parser.add_argument("--format", choices=("json", "quiet"), default="json")
    return parser


def _bridge_report_path(subject: str, book_title: str, explicit: str) -> Path:
    if explicit:
        return Path(explicit)
    layout = ensure_kb_layout()
    return layout["indexes"] / "pdf_ocr_review_status" / f"{subject.lower()}-{sanitize_name(book_title)}.json"


def _load_bridge_report(subject: str, book_title: str, pdf_source_id: str, explicit: str) -> dict[str, Any]:
    path = _bridge_report_path(subject, book_title, explicit)
    payload = load_json_or_default(path, {})
    if not payload:
        raise SystemExit(f"[ERROR] pdf OCR review/status bridge report not found: {path}")
    if pdf_source_id and str(payload.get("pdf_source_id", "")) != pdf_source_id:
        raise SystemExit(
            f"[ERROR] bridge report pdf_source_id mismatch: expected {pdf_source_id}, got {payload.get('pdf_source_id', '')}"
        )
    return {**payload, "bridge_report_path": str(path)}


def _page_key(item: dict[str, Any]) -> str:
    return str(item.get("page_id", "")).strip()


def _page_summary(page_id: str, items: list[dict[str, Any]]) -> dict[str, Any]:
    first = items[0]
    counts = {
        "pending_count": sum(1 for item in items if item.get("review_status") == "pending"),
        "accepted_count": sum(1 for item in items if item.get("review_status") == "accepted"),
        "rejected_count": sum(1 for item in items if item.get("review_status") == "rejected"),
        "ignored_count": sum(1 for item in items if item.get("review_status") == "ignored"),
    }
    if counts["accepted_count"]:
        review_status = "accepted"
    elif counts["pending_count"]:
        review_status = "pending"
    elif counts["rejected_count"]:
        review_status = "rejected"
    elif counts["ignored_count"]:
        review_status = "ignored"
    else:
        review_status = "pending"
    return {
        "page_id": page_id,
        "book_id": first.get("book_id", ""),
        "printed_page": first.get("printed_page"),
        "printed_page_label": first.get("printed_page_label", ""),
        "chapter_id": first.get("chapter_id"),
        "chapter_title": first.get("chapter_title"),
        "request_key": first.get("request_key", ""),
        "review_status": review_status,
        **counts,
    }


def _classify_handoff_ledger(page_summaries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ledger: list[dict[str, Any]] = []
    for item in page_summaries:
        ledger.append(
            {
                "page_id": item.get("page_id", ""),
                "book_id": item.get("book_id", ""),
                "printed_page": item.get("printed_page"),
                "chapter_id": item.get("chapter_id"),
                "chapter_title": item.get("chapter_title"),
                "review_status": item.get("review_status", ""),
                "handoff_status": "classify-candidate-only",
                "evidence_gate_status": "not-started",
                "claim_publication_status": "not-started",
            }
        )
    return ledger


def build_pdf_ocr_review_artifact(
    *,
    subject: str,
    book_title: str,
    pdf_source_id: str = "",
    bridge_report_path: Path | None = None,
) -> dict[str, Any]:
    layout = ensure_kb_layout()
    bridge = _load_bridge_report(subject, book_title, pdf_source_id, str(bridge_report_path or ""))
    book_root = Path(str(bridge.get("book_root", "")).strip())
    if not book_root.exists():
        raise SystemExit(f"[ERROR] bridged shadow book_root does not exist: {book_root}")

    queue_payload = queue_review_items(book_root=book_root, review_type=None)
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in queue_payload.get("items", []):
        page_id = _page_key(item)
        if page_id:
            grouped[page_id].append(item)
    page_summaries = [_page_summary(page_id, grouped[page_id]) for page_id in sorted(grouped)]

    review_ready_pages = [item for item in page_summaries if item["review_status"] == "accepted"]
    candidate_review_pages = [
        item for item in page_summaries if item["review_status"] != "accepted" and int(item.get("pending_count", 0) or 0) > 0
    ]
    blocked_review_pages = [item for item in page_summaries if item["review_status"] in {"rejected", "ignored"}]
    classify_handoff = _classify_handoff_ledger(page_summaries)

    output_root = layout["indexes"] / "pdf_ocr_review_artifacts"
    output_stem = f"{subject.lower()}-{sanitize_name(book_title)}"
    artifact_path = output_root / f"{output_stem}.json"
    markdown_path = output_root / f"{output_stem}.md"
    payload = {
        "artifact_contract_version": ARTIFACT_CONTRACT_VERSION,
        "artifact_id": ARTIFACT_ID,
        "subject": subject,
        "book_title": book_title,
        "pdf_source_id": bridge.get("pdf_source_id", ""),
        "scope": "bridged PDF OCR review queue -> classify-handoff candidate ledger",
        "bridge_report_path": bridge.get("bridge_report_path", ""),
        "book_root": str(book_root),
        "review_status_summary": dict(queue_payload.get("summary", {})),
        "review_ready_pages": review_ready_pages,
        "candidate_review_pages": candidate_review_pages,
        "blocked_review_pages": blocked_review_pages,
        "classify_handoff_ledger": classify_handoff,
        "review_layer_boundary": {
            "evidence_acceptance_written": False,
            "claim_publication_written": False,
            "pdf_ocr_baseline_rerun": False,
            "remote_ocr_called": False,
        },
        "readiness_status": "ready-for-r23-close",
        "updated_at": now_iso(),
        "artifact_path": str(artifact_path),
        "markdown_path": str(markdown_path),
    }
    save_json(artifact_path, payload, ignored_compare_keys=())
    save_text(markdown_path, render_markdown(payload))
    return payload


def render_markdown(payload: dict[str, Any]) -> str:
    boundary = dict(payload.get("review_layer_boundary", {}))
    summary = dict(payload.get("review_status_summary", {}))
    lines = [
        "# R23-T02 PDF OCR review queue artifact",
        "",
        f"- artifact_id: {payload.get('artifact_id', '')}",
        f"- readiness_status: {payload.get('readiness_status', '')}",
        f"- scope: {payload.get('scope', '')}",
        "",
        "## Review Status Summary",
        "",
        f"- total_count: {summary.get('total_count', 0)}",
        f"- pending_count: {summary.get('pending_count', 0)}",
        f"- accepted_count: {summary.get('accepted_count', 0)}",
        f"- rejected_count: {summary.get('rejected_count', 0)}",
        f"- ignored_count: {summary.get('ignored_count', 0)}",
        "",
        "## Boundary",
        "",
        f"- evidence_acceptance_written: {boundary.get('evidence_acceptance_written', False)}",
        f"- claim_publication_written: {boundary.get('claim_publication_written', False)}",
        f"- pdf_ocr_baseline_rerun: {boundary.get('pdf_ocr_baseline_rerun', False)}",
        f"- remote_ocr_called: {boundary.get('remote_ocr_called', False)}",
        "",
        "## Classify Handoff",
        "",
        f"- classify_handoff_count: {len(payload.get('classify_handoff_ledger', []))}",
        "- handoff_status: classify-candidate-only",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    args = build_parser().parse_args()
    payload = build_pdf_ocr_review_artifact(
        subject=args.subject,
        book_title=args.book_title,
        pdf_source_id=args.pdf_source_id,
        bridge_report_path=Path(args.bridge_report_path) if args.bridge_report_path else None,
    )
    if args.format == "json":
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
