#!/usr/bin/env python3
"""Record an explicit human review decision for one PDF OCR page."""
from __future__ import annotations

import argparse
import json
import sys

from common import ensure_kb_layout, load_json_or_default, now_iso, save_json


def source_file_sha256(layout: dict, source_id: str) -> str:
    source = load_json_or_default(layout["sources"] / f"{source_id}.json", {})
    files = [item for item in source.get("files", []) if isinstance(item, dict)]
    return str((files[0] if files else {}).get("sha256") or "").strip()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pdf-source-id", required=True)
    parser.add_argument("--pdf-page", required=True, type=int)
    parser.add_argument("--printed-page", type=int)
    parser.add_argument("--page-header-confirmed", action="store_true")
    parser.add_argument("--review-status", required=True, choices=("pending", "accepted", "rejected"))
    parser.add_argument("--note", default="")
    parser.add_argument("--format", choices=("json", "quiet"), default="json")
    args = parser.parse_args()
    if args.pdf_page < 1 or (args.printed_page is not None and args.printed_page < 1):
        raise SystemExit("page numbers must be positive")
    if args.review_status == "accepted" and (args.printed_page is None or not args.page_header_confirmed):
        raise SystemExit("accepted PDF page review requires --printed-page and --page-header-confirmed")
    layout = ensure_kb_layout()
    source_sha = source_file_sha256(layout, args.pdf_source_id)
    if args.review_status == "accepted" and not source_sha:
        raise SystemExit("accepted PDF page review requires a registered source file SHA-256")
    path = layout["review_queues"] / "pdf-page-review" / f"{args.pdf_source_id}.json"
    payload = load_json_or_default(path, {"queue_type": "pdf-page-review", "source_id": args.pdf_source_id, "items": []})
    items = [item for item in payload.get("items", []) if int(item.get("pdf_page", 0) or 0) != args.pdf_page]
    items.append({"pdf_page": args.pdf_page, "printed_page": args.printed_page or 0, "review_status": args.review_status, "page_header_verified": bool(args.page_header_confirmed), "source_file_sha256": source_sha, "note": args.note, "reviewed_at": now_iso()})
    items.sort(key=lambda item: int(item["pdf_page"]))
    result = {"queue_type": "pdf-page-review", "source_id": args.pdf_source_id, "items": items, "summary": {"accepted_count": sum(item["review_status"] == "accepted" for item in items), "pending_count": sum(item["review_status"] == "pending" for item in items), "rejected_count": sum(item["review_status"] == "rejected" for item in items)}}
    save_json(path, result, ignored_compare_keys=())
    if args.format == "json": print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
