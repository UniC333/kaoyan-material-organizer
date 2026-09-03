#!/usr/bin/env python3
"""Extract *unapproved* PDF-to-printed-page candidates from OCR footer blocks."""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

from common import ensure_kb_layout, load_json_or_default, now_iso, sanitize_name, save_json


FOOTER_NUMBER = re.compile(r"^(?:第\s*)?(\d{1,4})(?:\s*页)?$")


def _candidate_from_normalized(payload: dict[str, Any]) -> tuple[int | None, str]:
    values: list[int] = []
    for block in payload.get("chunk_candidates", []) or []:
        if not isinstance(block, dict) or str(block.get("block_type") or "") != "footer":
            continue
        match = FOOTER_NUMBER.fullmatch(str(block.get("text") or "").strip())
        if match:
            values.append(int(match.group(1)))
    unique = sorted(set(values))
    if len(unique) == 1:
        return unique[0], "exact-footer-token"
    if not unique:
        return None, "missing-footer-token"
    return None, "ambiguous-footer-token"


def _continuous_segments(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    segments: list[dict[str, Any]] = []
    current: list[dict[str, Any]] = []
    for item in items:
        candidate = item.get("printed_page_candidate")
        if candidate is None:
            if current:
                segments.append(_segment(current))
                current = []
            continue
        if current and (int(item["pdf_page"]) != int(current[-1]["pdf_page"]) + 1 or int(candidate) != int(current[-1]["printed_page_candidate"]) + 1):
            segments.append(_segment(current))
            current = []
        current.append(item)
    if current:
        segments.append(_segment(current))
    return segments


def _segment(items: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "pdf_page_start": int(items[0]["pdf_page"]),
        "pdf_page_end": int(items[-1]["pdf_page"]),
        "printed_page_start_candidate": int(items[0]["printed_page_candidate"]),
        "printed_page_end_candidate": int(items[-1]["printed_page_candidate"]),
        "page_count": len(items),
        "candidate_status": "continuous-footer-candidates-only",
    }


def build_candidates(*, subject: str, book_title: str, pdf_source_id: str, report_path: Path | None = None) -> dict[str, Any]:
    layout = ensure_kb_layout()
    report_path = report_path or layout["indexes"] / "pdf_ocr_runs" / f"{subject.lower()}-{sanitize_name(book_title)}.json"
    report = load_json_or_default(report_path, {})
    if not report or str(report.get("pdf_source_id") or "") != pdf_source_id:
        raise SystemExit("[ERROR] OCR report is missing or does not match --pdf-source-id")
    source = load_json_or_default(layout["sources"] / f"{pdf_source_id}.json", {})
    files = [item for item in source.get("files", []) or [] if isinstance(item, dict)]
    source_sha = str((files[0] if files else {}).get("sha256") or "").strip()
    if not source_sha:
        raise SystemExit("[ERROR] registered PDF source SHA-256 is required")
    items: list[dict[str, Any]] = []
    for page in report.get("pages", report.get("chapters", [])):
        if not isinstance(page, dict):
            continue
        pdf_page = int(page.get("pdf_page", page.get("page_start", 0)) or 0)
        if pdf_page < 1:
            continue
        normalized = load_json_or_default(Path(str(page.get("normalized_path") or "")), {})
        candidate, status = _candidate_from_normalized(normalized)
        items.append({
            "pdf_page": pdf_page,
            "printed_page_candidate": candidate,
            "candidate_status": status,
            "request_key": str(page.get("request_key") or normalized.get("request_key") or ""),
            "source_image_path": str(page.get("rendered_image_path") or ""),
            "source_image_sha256": str(normalized.get("source_file_sha256") or ""),
        })
    items.sort(key=lambda item: item["pdf_page"])
    payload = {
        "schema_version": "pdf-page-mapping-candidates.v1",
        "candidate_only": True,
        "approval_required": "visual confirmation of original rendered endpoint pages plus interval approval",
        "subject": subject,
        "book_title": book_title,
        "pdf_source_id": pdf_source_id,
        "source_file_sha256": source_sha,
        "report_path": str(report_path),
        "items": items,
        "continuous_segments": _continuous_segments(items),
        "summary": {
            "page_count": len(items),
            "exact_footer_candidate_count": sum(item["printed_page_candidate"] is not None for item in items),
            "unmapped_candidate_count": sum(item["printed_page_candidate"] is None for item in items),
        },
        "updated_at": now_iso(),
    }
    destination = layout["indexes"] / "pdf_page_mapping_candidates" / f"{pdf_source_id}.json"
    save_json(destination, payload, ignored_compare_keys=())
    return {**payload, "candidate_path": str(destination)}


def main() -> int:
    parser = argparse.ArgumentParser(description="Create unapproved PDF page-number candidates from OCR footers.")
    parser.add_argument("--subject", required=True)
    parser.add_argument("--book-title", required=True)
    parser.add_argument("--pdf-source-id", required=True)
    parser.add_argument("--report-path", default="")
    parser.add_argument("--format", choices=("json", "quiet"), default="json")
    args = parser.parse_args()
    payload = build_candidates(subject=args.subject, book_title=args.book_title, pdf_source_id=args.pdf_source_id, report_path=Path(args.report_path) if args.report_path else None)
    if args.format == "json":
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())
