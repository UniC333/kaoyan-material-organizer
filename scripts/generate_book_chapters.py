#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

from common import load_json_or_default, save_json


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--book-root", required=True)
    parser.add_argument("--context-json", required=True)
    parser.add_argument("--plan-json", required=True)
    parser.add_argument("--format", choices=("json", "quiet"), default="json")
    return parser


def _page_number(value: Any) -> int:
    text = str(value or "").strip()
    match = re.search(r"(\d+)", text)
    if not match:
        raise SystemExit(f"invalid page label: {value!r}")
    return int(match.group(1))


def _normalize_chunks(plan_payload: dict[str, Any], *, book_id: str) -> list[dict[str, Any]]:
    chunks = list(plan_payload.get("chunks", []))
    if not chunks:
        raise SystemExit("chunk plan is empty")
    normalized: list[dict[str, Any]] = []
    for index, chunk in enumerate(chunks, start=1):
        section_title = str(chunk.get("section_guess") or chunk.get("chunk_title") or f"section-{index}").strip()
        normalized.append(
            {
                "section_id": f"SEC-{book_id}-{index:04d}",
                "section_title": section_title,
                "page_start": _page_number(chunk.get("page_start")),
                "page_end": _page_number(chunk.get("page_end")),
            }
        )
    return normalized


def generate_book_chapters(*, book_root: Path, context_json: Path, plan_json: Path) -> dict[str, Any]:
    context_payload = load_json_or_default(context_json, {})
    plan_payload = load_json_or_default(plan_json, {})
    if not context_payload:
        raise SystemExit("context json is missing or empty")
    if not plan_payload:
        raise SystemExit("plan json is missing or empty")

    book_asset_path = book_root / "metadata" / "book_asset.json"
    book_asset_payload = load_json_or_default(book_asset_path, {})
    if not book_asset_payload:
        raise SystemExit("book_asset.json is missing; run book inspect first")

    book_id = str(book_asset_payload.get("book_id") or "").strip()
    if not book_id:
        raise SystemExit("book_id is missing in book_asset.json")

    chapter_id = str(context_payload.get("chapter_id") or f"CH-{book_id}-0001").strip()
    chapter_title = str(context_payload.get("chapter_title") or "").strip()
    if not chapter_title:
        raise SystemExit("chapter_title is missing in context json")

    sections = _normalize_chunks(plan_payload, book_id=book_id)
    page_start = min(section["page_start"] for section in sections)
    page_end = max(section["page_end"] for section in sections)

    chapters_path = book_root / "chapters.yaml"
    existing_payload = load_json_or_default(chapters_path, {})
    created_at = str(existing_payload.get("created_at") or "")

    payload = {
        "created_at": created_at or "generated",
        "chapters": [
            {
                "chapter_id": chapter_id,
                "chapter_title": chapter_title,
                "page_start": page_start,
                "page_end": page_end,
                "sections": sections,
            }
        ],
    }
    written = save_json(chapters_path, payload)
    return {
        "book_id": book_id,
        "book_root": str(book_root),
        "chapters_path": str(chapters_path),
        "chapter_count": 1,
        "section_count": len(sections),
        "written": written,
    }


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    args = build_parser().parse_args()
    payload = generate_book_chapters(
        book_root=Path(args.book_root),
        context_json=Path(args.context_json),
        plan_json=Path(args.plan_json),
    )
    if args.format == "json":
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
