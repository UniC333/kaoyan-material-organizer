#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from common import ensure_kb_layout, load_all_json, load_json_or_default, now_iso, sanitize_name, save_json


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build a unified acceptance checklist for current PDF book sources.")
    parser.add_argument("--subject", required=True)
    parser.add_argument("--book-title", action="append", default=[])
    parser.add_argument("--format", choices=("json", "quiet"), default="json")
    return parser


def _discover_book_pdf_sources(
    *,
    subject: str,
    requested_titles: list[str],
    layout: dict[str, Path],
) -> list[dict[str, Any]]:
    requested = [title.strip() for title in requested_titles if title.strip()]
    candidates = [
        payload
        for payload in load_all_json(layout["sources"])
        if payload.get("subject") == subject and payload.get("material_type") == "book-pdf"
    ]
    if not candidates:
        raise SystemExit(f"[ERROR] no book-pdf sources found for subject: {subject}")

    latest_by_title: dict[str, dict[str, Any]] = {}
    for payload in candidates:
        title = str(payload.get("source_name") or "").strip()
        if not title:
            continue
        current = latest_by_title.get(title)
        if current is None or str(payload.get("updated_at") or "") > str(current.get("updated_at") or ""):
            latest_by_title[title] = payload

    if requested:
        missing = [title for title in requested if title not in latest_by_title]
        if missing:
            raise SystemExit(f"[ERROR] requested book-pdf sources not found for {subject}: {', '.join(missing)}")
        return [latest_by_title[title] for title in requested]

    return sorted(latest_by_title.values(), key=lambda item: str(item.get("source_name") or ""))


def _load_parallel_links(layout: dict[str, Path]) -> dict[tuple[str, str], dict[str, Any]]:
    links_root = layout["indexes"] / "book_parallel_source_links"
    if not links_root.exists():
        return {}
    payloads: dict[tuple[str, str], dict[str, Any]] = {}
    for payload in load_all_json(links_root):
        subject = str(payload.get("subject") or "").strip()
        book_title = str(payload.get("book_title") or "").strip()
        if subject and book_title:
            payloads[(subject, book_title)] = payload
    return payloads


def _book_entry(
    *,
    source_payload: dict[str, Any],
    anchor_payload: dict[str, Any],
    anchor_path: Path,
    link_payload: dict[str, Any],
    link_path: Path | None,
) -> dict[str, Any]:
    files = list(source_payload.get("files") or [])
    file_payload = files[0] if files else {}
    chapter_anchor_count = int(anchor_payload.get("chapter_anchor_count", 0) or 0)
    chapter_count = int(link_payload.get("summary", {}).get("chapter_count", 0) or 0)
    image_linked_count = int(link_payload.get("summary", {}).get("image_linked_count", 0) or 0)
    context_linked_count = int(link_payload.get("summary", {}).get("context_linked_count", 0) or 0)
    pdf_only_count = int(link_payload.get("summary", {}).get("pdf_only_count", 0) or 0)

    gaps: list[str] = []
    if not anchor_payload:
        gaps.append("missing-anchor-index")
    if chapter_anchor_count == 0:
        gaps.append("missing-chapter-anchors")
    if not link_payload:
        gaps.append("missing-parallel-source-link")
    if link_payload and image_linked_count == 0:
        gaps.append("parallel-link-without-image-match")

    return {
        "source_id": str(source_payload.get("source_id") or ""),
        "book_title": str(source_payload.get("source_name") or ""),
        "file_id": str(file_payload.get("file_id") or ""),
        "source_registered": True,
        "source_status": str(source_payload.get("status") or ""),
        "pdf_file": {
            "relative_path": str(file_payload.get("relative_path") or ""),
            "suffix": str(file_payload.get("suffix") or ""),
            "size_bytes": int(file_payload.get("size_bytes", 0) or 0),
            "sha256": str(file_payload.get("sha256") or ""),
        },
        "anchors": {
            "path": str(anchor_path),
            "exists": bool(anchor_payload),
            "page_count": int(anchor_payload.get("page_count", 0) or 0),
            "outline_count": int(anchor_payload.get("outline_count", 0) or 0),
            "chapter_anchor_count": chapter_anchor_count,
        },
        "parallel_source_link": {
            "path": str(link_path) if link_path else "",
            "exists": bool(link_payload),
            "chapter_count": chapter_count,
            "image_linked_count": image_linked_count,
            "context_linked_count": context_linked_count,
            "pdf_only_count": pdf_only_count,
        },
        "gaps": gaps,
        "has_gaps": bool(gaps),
    }


def build_pdf_acceptance_checklist(*, subject: str, requested_titles: list[str] | None = None) -> dict[str, Any]:
    layout = ensure_kb_layout()
    requested_titles = list(requested_titles or [])
    sources = _discover_book_pdf_sources(subject=subject, requested_titles=requested_titles, layout=layout)
    parallel_links = _load_parallel_links(layout)

    books: list[dict[str, Any]] = []
    for source_payload in sources:
        source_id = str(source_payload["source_id"])
        book_title = str(source_payload.get("source_name") or "")
        anchor_path = layout["indexes"] / "pdf_book_anchors" / f"{source_id}.json"
        anchor_payload = load_json_or_default(anchor_path, {})
        link_payload = parallel_links.get((subject, book_title), {})
        link_path = None
        if link_payload:
            output_path = str(link_payload.get("output_path") or "").strip()
            if output_path:
                candidate = Path(output_path)
                if candidate.exists():
                    link_path = candidate
            if link_path is None:
                candidate = layout["indexes"] / "book_parallel_source_links" / f"{subject.lower()}-{sanitize_name(book_title)}.json"
                if candidate.exists():
                    link_path = candidate
        books.append(
            _book_entry(
                source_payload=source_payload,
                anchor_payload=anchor_payload,
                anchor_path=anchor_path,
                link_payload=link_payload,
                link_path=link_path,
            )
        )

    output_root = layout["indexes"] / "pdf_source_acceptance"
    output_path = output_root / f"{subject.lower()}-acceptance-checklist.json"
    payload = {
        "subject": subject,
        "generated_at": now_iso(),
        "book_count": len(books),
        "books": books,
        "summary": {
            "registered_count": sum(1 for item in books if item["source_registered"]),
            "anchor_ready_count": sum(1 for item in books if item["anchors"]["chapter_anchor_count"] > 0),
            "parallel_link_ready_count": sum(1 for item in books if item["parallel_source_link"]["exists"]),
            "books_with_gaps": sum(1 for item in books if item["has_gaps"]),
        },
    }
    written = save_json(output_path, payload, ignored_compare_keys=("generated_at",))
    payload["output_path"] = str(output_path)
    payload["written"] = written
    return payload


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    args = build_parser().parse_args()
    payload = build_pdf_acceptance_checklist(subject=args.subject, requested_titles=args.book_title)
    if args.format == "json":
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
