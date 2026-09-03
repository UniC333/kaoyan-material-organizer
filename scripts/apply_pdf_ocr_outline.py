#!/usr/bin/env python3
"""Apply a visually confirmed contents outline to formally mapped PDF pages."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from common import ensure_kb_layout, load_json_or_default, now_iso, sanitize_name, save_json


def apply_outline(*, subject: str, book_title: str, pdf_source_id: str, outline_path: Path) -> dict[str, Any]:
    layout = ensure_kb_layout()
    source = load_json_or_default(layout["sources"] / f"{pdf_source_id}.json", {})
    files = [item for item in source.get("files", []) or [] if isinstance(item, dict)]
    source_sha = str((files[0] if files else {}).get("sha256") or "")
    outline = load_json_or_default(outline_path, {})
    if not source_sha or outline.get("pdf_source_id") != pdf_source_id or outline.get("source_file_sha256") != source_sha:
        raise SystemExit("[ERROR] approved outline must name the source and match its SHA-256")
    starts = [item for item in outline.get("items", []) if isinstance(item, dict) and int(item.get("printed_page_start", 0) or 0) > 0]
    starts.sort(key=lambda item: int(item["printed_page_start"]))
    if not starts:
        raise SystemExit("[ERROR] outline has no valid entries")
    review_path = layout["review_queues"] / "pdf-page-review" / f"{pdf_source_id}.json"
    reviews = [item for item in load_json_or_default(review_path, {}).get("items", []) if isinstance(item, dict) and item.get("review_status") == "accepted" and item.get("page_header_verified") and item.get("source_file_sha256") == source_sha]
    if not reviews:
        raise SystemExit("[ERROR] no formally mapped PDF pages available for classification")
    bridge = load_json_or_default(layout["indexes"] / "pdf_ocr_review_status" / f"{subject.lower()}-{sanitize_name(book_title)}.json", {})
    classifications_path = Path(str(bridge.get("page_classifications_path") or ""))
    definitions_path = Path(str(bridge.get("chapter_definitions_path") or ""))
    if not classifications_path or not definitions_path:
        raise SystemExit("[ERROR] create the PDF review-status bridge before applying the outline")
    now = now_iso()
    items: list[dict[str, Any]] = []
    chapters: dict[str, dict[str, Any]] = {}
    for review in sorted(reviews, key=lambda item: int(item["pdf_page"])):
        printed = int(review["printed_page"])
        applicable = [item for item in starts if int(item["printed_page_start"]) <= printed]
        if not applicable:
            continue
        entry = applicable[-1]
        part = str(entry.get("part_title") or "").strip()
        chapter = str(entry.get("chapter_title") or "").strip()
        section = str(entry.get("section_title") or "").strip()
        chapter_key = f"{part} / {chapter}".strip(" / ")
        chapter_id = f"PDFCH-{pdf_source_id}-{sanitize_name(chapter_key)}"
        pdf_page = int(review["pdf_page"])
        items.append({
            "page_classification_id": f"PCLASS-{pdf_source_id}-{pdf_page:04d}",
            "page_id": f"PDFPAGE-{pdf_source_id}-{pdf_page:04d}",
            "book_id": f"PDFOCR-{pdf_source_id}",
            "chapter_id": chapter_id,
            "chapter_title": chapter_key,
            "section_id": f"{chapter_id}-{sanitize_name(section)}" if section else None,
            "section_title": section or None,
            "part_title": part or None,
            "classification_method": "verified-contents-outline",
            "classification_status": "confirmed",
            "classification_confidence": 1.0,
            "classification_source": "original-pdf-contents-visual-review",
            "confirmed_by": "contents-page-visual-review",
            "confirmed_at": now,
            "updated_at": now,
            "printed_page": printed,
            "pdf_page": pdf_page,
        })
        definition = chapters.setdefault(chapter_id, {"chapter_id": chapter_id, "book_id": f"PDFOCR-{pdf_source_id}", "chapter_title": chapter_key, "page_start": printed, "page_end": printed, "definition_status": "confirmed", "sections": [], "created_at": now, "updated_at": now})
        definition["page_start"] = min(int(definition["page_start"]), printed)
        definition["page_end"] = max(int(definition["page_end"]), printed)
        if section and section not in definition["sections"]:
            definition["sections"].append(section)
    payload = {"book_id": f"PDFOCR-{pdf_source_id}", "created_at": now, "updated_at": now, "items": items, "summary": {"confirmed_count": len(items), "candidate_count": 0, "conflict_count": 0, "unassigned_count": 0}}
    save_json(classifications_path, payload, ignored_compare_keys=())
    save_json(definitions_path, {"book_id": f"PDFOCR-{pdf_source_id}", "created_at": now, "updated_at": now, "items": sorted(chapters.values(), key=lambda item: (int(item["page_start"]), item["chapter_id"]))}, ignored_compare_keys=())
    audit = {"pdf_source_id": pdf_source_id, "source_file_sha256": source_sha, "outline_path": str(outline_path), "classification_status": "confirmed", "mapped_page_count": len(items), "unmapped_pages_remain_unclassified": True, "approved_at": now}
    audit_path = layout["indexes"] / "pdf_ocr_outline_approvals" / f"{pdf_source_id}.json"
    save_json(audit_path, audit, ignored_compare_keys=())
    return {**audit, "page_classifications_path": str(classifications_path), "chapter_definitions_path": str(definitions_path), "audit_path": str(audit_path)}


def main() -> int:
    parser = argparse.ArgumentParser(description="Apply a visually confirmed contents outline to mapped PDF pages.")
    parser.add_argument("--subject", required=True)
    parser.add_argument("--book-title", required=True)
    parser.add_argument("--pdf-source-id", required=True)
    parser.add_argument("--outline-json", required=True)
    parser.add_argument("--yes", action="store_true")
    parser.add_argument("--format", choices=("json", "quiet"), default="json")
    args = parser.parse_args()
    if not args.yes:
        raise SystemExit("[ERROR] outline application writes formal classifications; repeat with --yes")
    payload = apply_outline(subject=args.subject, book_title=args.book_title, pdf_source_id=args.pdf_source_id, outline_path=Path(args.outline_json))
    if args.format == "json": print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"): sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())
