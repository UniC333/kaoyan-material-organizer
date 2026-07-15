#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

from common import ensure_kb_layout, now_iso, register_source_material, resolve_subject, save_json

try:
    from pypdf import PdfReader
except ImportError as exc:  # pragma: no cover - surfaced via CLI
    raise SystemExit("pypdf is required; install project dependencies first") from exc


CHAPTER_TITLE_PATTERN = re.compile(r"^(第[0-9一二三四五六七八九十百零]+章|\d+\.\d+|\d+\.|chapter\s+\d+)", re.IGNORECASE)
STRICT_CHAPTER_PATTERN = re.compile(r"^(第[0-9一二三四五六七八九十百零]+章|chapter\s+\d+)", re.IGNORECASE)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Register a textbook PDF as a formal source and extract outline anchors.")
    parser.add_argument("--subject", required=True)
    parser.add_argument("--book-title", required=True)
    parser.add_argument("--pdf-path", required=True)
    parser.add_argument("--edition", default="")
    parser.add_argument("--format", choices=("json", "quiet"), default="json")
    return parser


def _normalize_outline_items(raw: Any) -> list[Any]:
    if raw is None:
        return []
    if isinstance(raw, list):
        return raw
    return [raw]


def _walk_outline(reader: PdfReader, items: list[Any], *, level: int = 1) -> list[dict[str, Any]]:
    anchors: list[dict[str, Any]] = []
    for item in items:
        if isinstance(item, list):
            anchors.extend(_walk_outline(reader, item, level=level + 1))
            continue
        title = str(getattr(item, "title", "") or "").strip()
        if not title:
            continue
        try:
            page_start = int(reader.get_destination_page_number(item)) + 1
        except Exception:
            page_start = None
        anchor_type = "chapter" if STRICT_CHAPTER_PATTERN.match(title) else ("section" if CHAPTER_TITLE_PATTERN.match(title) else "bookmark")
        anchors.append(
            {
                "title": title,
                "page_start": page_start,
                "level": level,
                "anchor_type": anchor_type,
            }
        )
    return anchors


def extract_outline_anchors(pdf_path: Path) -> dict[str, Any]:
    reader = PdfReader(str(pdf_path))
    raw_outline = _normalize_outline_items(getattr(reader, "outline", []))
    anchors = _walk_outline(reader, raw_outline)
    page_count = len(reader.pages)

    chapter_candidates = [dict(item) for item in anchors if item.get("anchor_type") == "chapter" and item.get("page_start")]
    for index, item in enumerate(chapter_candidates, start=1):
        next_start = chapter_candidates[index]["page_start"] if index < len(chapter_candidates) else page_count + 1
        item["chapter_index"] = index
        item["page_end"] = max(int(item["page_start"]), int(next_start) - 1)

    return {
        "page_count": page_count,
        "outline_count": len(anchors),
        "anchors": anchors,
        "chapter_anchors": chapter_candidates,
        "chapter_anchor_count": len(chapter_candidates),
    }


def register_pdf_book_source(*, subject: str, book_title: str, pdf_path: Path, edition: str = "") -> dict[str, Any]:
    resolved_subject, _ = resolve_subject(subject)
    pdf_path = pdf_path.resolve()
    if not pdf_path.exists():
        raise SystemExit(f"[ERROR] pdf not found: {pdf_path}")
    if pdf_path.suffix.lower() != ".pdf":
        raise SystemExit(f"[ERROR] pdf path must end with .pdf: {pdf_path}")

    source_payload = register_source_material(
        subject=resolved_subject,
        source_name=book_title,
        material_type="book-pdf",
        material_path=pdf_path.parent,
        edition=edition,
        include_paths=[pdf_path],
    )
    layout = ensure_kb_layout()
    outline_payload = extract_outline_anchors(pdf_path)
    file_payload = source_payload["files"][0]

    anchors_payload = {
        "source_id": source_payload["source_id"],
        "file_id": file_payload["file_id"],
        "subject": resolved_subject,
        "book_title": book_title,
        "edition": edition,
        "pdf_path": str(pdf_path),
        "material_type": "book-pdf",
        "updated_at": now_iso(),
        **outline_payload,
    }
    anchors_path = layout["indexes"] / "pdf_book_anchors" / f"{source_payload['source_id']}.json"
    written = save_json(anchors_path, anchors_payload)

    return {
        "subject": resolved_subject,
        "book_title": book_title,
        "pdf_path": str(pdf_path),
        "source_id": source_payload["source_id"],
        "file_id": file_payload["file_id"],
        "file_count": int(source_payload.get("file_count", 0) or 0),
        "page_count": outline_payload["page_count"],
        "outline_count": outline_payload["outline_count"],
        "chapter_anchor_count": outline_payload["chapter_anchor_count"],
        "anchors_path": str(anchors_path),
        "written": written,
    }


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    args = build_parser().parse_args()
    payload = register_pdf_book_source(
        subject=args.subject,
        book_title=args.book_title,
        pdf_path=Path(args.pdf_path),
        edition=args.edition,
    )
    if args.format == "json":
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
