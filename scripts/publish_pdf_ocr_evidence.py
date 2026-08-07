#!/usr/bin/env python3
"""Publish reviewed PDF-anchor OCR into source-grounded evidence records."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from common import allocate_kb_id, build_provenance_record, build_source_span, ensure_kb_layout, load_all_json, load_json_or_default, now_iso, save_json, stable_fingerprint, validate_entity_contract


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Publish a PDF OCR review artifact as bounded, cited evidence.")
    parser.add_argument("--subject", required=True)
    parser.add_argument("--book-title", required=True)
    parser.add_argument("--pdf-source-id", required=True)
    parser.add_argument("--report-path", required=True)
    parser.add_argument("--review-artifact-path", required=True)
    parser.add_argument("--format", choices=("json", "quiet"), default="json")
    return parser.parse_args()


def _source_file(source: dict[str, Any]) -> dict[str, Any]:
    files = [item for item in source.get("files", []) if isinstance(item, dict)]
    if not files:
        raise SystemExit(f"[ERROR] source has no registered file: {source.get('source_id', '')}")
    return files[0]


def _existing_by_key(layout: dict[str, Path]) -> dict[str, dict[str, Any]]:
    return {str(item.get("evidence_key", "")): item for item in load_all_json(layout["evidence"]) if item.get("evidence_key")}


def publish(*, subject: str, book_title: str, pdf_source_id: str, report_path: Path, review_artifact_path: Path) -> dict[str, Any]:
    layout = ensure_kb_layout()
    report = load_json_or_default(report_path, {})
    artifact = load_json_or_default(review_artifact_path, {})
    if not report or not artifact:
        raise SystemExit("[ERROR] PDF OCR report or review artifact is missing")
    if report.get("pdf_source_id") != pdf_source_id or artifact.get("pdf_source_id") != pdf_source_id:
        raise SystemExit("[ERROR] PDF source id does not match the report and review artifact")
    handoff = {str(item.get("page_id", "")): item for item in artifact.get("classify_handoff_ledger", [])}
    source = load_json_or_default(layout["sources"] / f"{pdf_source_id}.json", {})
    source_file = _source_file(source)
    existing = _existing_by_key(layout)
    written: list[dict[str, Any]] = []
    for chapter in report.get("pages", report.get("chapters", [])):
        chapter_number = int(chapter.get("chapter_number", 0) or 0)
        pdf_page = int(chapter.get("pdf_page", chapter.get("page_start", 0)) or 0)
        printed_page = pdf_page
        page_id = f"PDFPAGE-{pdf_source_id}-{pdf_page:04d}"
        handoff_item = handoff.get(page_id, {})
        if handoff_item.get("review_status") not in {"accepted", "not-required"}:
            continue
        normalized = load_json_or_default(Path(str(chapter.get("normalized_path", ""))), {})
        pages = normalized.get("pages", [])
        text = "\n".join(str(item.get("text", "")).strip() for item in pages if isinstance(item, dict) and str(item.get("text", "")).strip())
        if not text:
            continue
        chunk_id = f"PDFOCR-{chapter_number:04d}-{pdf_page:04d}"
        chapter_id = f"PDFCH-{pdf_source_id}-{chapter_number:04d}"
        evidence_key = stable_fingerprint({"source_id": pdf_source_id, "chapter_id": chapter_id, "chunk_id": chunk_id, "source_sha256": normalized.get("source_file_sha256", "")})
        current = existing.get(evidence_key, {})
        span = build_source_span(
            source_id=pdf_source_id,
            file_id=str(source_file.get("file_id", "")),
            source_file_sha256=str(source_file.get("sha256", "")),
            chapter_id=chapter_id,
            chunk_id=chunk_id,
            page_start=pdf_page,
            page_end=pdf_page,
            image_start=pdf_page,
            image_end=pdf_page,
            origin_type="pdf_page_ocr",
            verification_status="reviewed",
            block_ids=[str(item.get("block_id", "")) for item in normalized.get("chunk_candidates", []) if isinstance(item, dict) and item.get("block_id")],
            notes="PDF chapter-anchor OCR; bounded to the rendered anchor page.",
        )
        evidence = {
            "evidence_id": current.get("evidence_id") or allocate_kb_id("evidence", subject),
            "evidence_key": evidence_key,
            "subject": subject,
            "book_title": book_title,
            "source_id": pdf_source_id,
            "chapter_id": chapter_id,
            "chapter_title": str(chapter.get("chapter_title", "")).strip(),
            "chunk_id": chunk_id,
            "title": str(chapter.get("chapter_title", "")).strip() or f"第{chapter_number}章",
            "content": text,
            "evidence_type": "concept",
            "origin_type": "pdf_page_ocr",
            "verification_status": "reviewed",
            "review_status": "accepted",
            "review_decision": "explicit-page-review",
            "review_note": str(handoff_item.get("page_review_note", "")),
            "reviewed_at": now_iso(),
            "confidence": 0.85,
            "source_grounded": True,
            "source_spans": [span],
            "locator": dict(span["locator"]),
            "provenance": build_provenance_record(origin_type="pdf_page_ocr", verification_status="reviewed", source_spans=[span], source_grounded=True),
            "syllabus_candidates": current.get("syllabus_candidates", []),
            "accepted_syllabus_nodes": current.get("accepted_syllabus_nodes", []),
            "mapping_status": current.get("mapping_status", "unmapped"),
            "pdf_ocr_request_key": normalized.get("request_key", ""),
            "pdf_ocr_normalized_path": str(chapter.get("normalized_path", "")),
            "coverage_note": "One explicitly reviewed rendered PDF page; no neighboring-page inference.",
            "updated_at": now_iso(),
        }
        validate_entity_contract("evidence", evidence)
        save_json(layout["evidence"] / f"{evidence['evidence_id']}.json", evidence)
        written.append(evidence)
    return {"count": len(written), "evidence_ids": [item["evidence_id"] for item in written]}


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    args = parse_args()
    payload = publish(
        subject=args.subject,
        book_title=args.book_title,
        pdf_source_id=args.pdf_source_id,
        report_path=Path(args.report_path),
        review_artifact_path=Path(args.review_artifact_path),
    )
    if args.format == "json":
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
