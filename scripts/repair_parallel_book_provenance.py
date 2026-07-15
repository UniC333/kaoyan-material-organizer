#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

from common import ensure_kb_layout, load_all_json, load_json_or_default, now_iso, sanitize_name, save_json


CHAPTER_NUMBER_PATTERN = re.compile(r"第\s*([0-9]+)\s*章")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Repair stale evidence and page classification provenance from parallel source links.")
    parser.add_argument("--subject", required=True)
    parser.add_argument("--book-title", required=True)
    parser.add_argument("--chapter-number", type=int, required=True)
    parser.add_argument("--format", choices=("json", "quiet"), default="json")
    return parser


def _parallel_link_path(subject: str, book_title: str, layout: dict[str, Path]) -> Path:
    return layout["indexes"] / "book_parallel_source_links" / f"{subject.lower()}-{sanitize_name(book_title)}.json"


def _parse_chapter_number(text: str) -> int | None:
    match = CHAPTER_NUMBER_PATTERN.search(str(text or ""))
    if not match:
        return None
    try:
        return int(match.group(1))
    except ValueError:
        return None


def _find_target_chapter(link_payload: dict[str, Any], chapter_number: int) -> dict[str, Any]:
    for item in link_payload.get("chapters", []):
        if int(item.get("chapter_number", 0) or 0) == chapter_number:
            return item
    raise SystemExit(f"[ERROR] chapter {chapter_number} not found in parallel source links")


def _chapter_view_path(batch_output_dir: str, chapter_id: str, chapter_title: str) -> str:
    if not batch_output_dir:
        return ""
    candidate = Path(batch_output_dir) / "views" / "by-chapter" / f"{chapter_id}_{sanitize_name(chapter_title)}.md"
    return str(candidate) if candidate.exists() else ""


def _book_id(subject: str, book_title: str) -> str:
    return f"{subject.lower()}-{sanitize_name(book_title)}"


def _build_repaired_ref(
    *,
    source_sha: str,
    existing_ref: dict[str, Any] | None,
    subject: str,
    book_title: str,
    chapter_id: str,
    chapter_title: str,
    chapter_view_path: str,
) -> dict[str, Any]:
    existing_ref = existing_ref or {}
    page_id = str(existing_ref.get("page_id", "")).strip()
    printed_page = existing_ref.get("printed_page")
    if not page_id:
        printed_token = int(printed_page or 0)
        page_id = f"PAGE-{_book_id(subject, book_title)}-{printed_token:04d}" if printed_token else ""
    return {
        "source_file_sha256": source_sha,
        "book_id": _book_id(subject, book_title),
        "book_title": book_title,
        "page_id": page_id,
        "printed_page": printed_page,
        "chapter_id": chapter_id,
        "chapter_title": chapter_title,
        "section_id": "",
        "section_title": "",
        "classification_status": "confirmed",
        "classification_method": "parallel_source_context",
        "chapter_view_path": chapter_view_path,
        "section_view_path": "",
    }


def _update_source_payload(source_id: str, chapter_id: str, layout: dict[str, Path]) -> bool:
    changed = False
    for path in (layout["sources"] / f"{source_id}.json", layout["manifest_sources"] / f"{source_id}.json"):
        payload = load_json_or_default(path, {})
        if not payload:
            continue
        chapter_ids = [str(item).strip() for item in payload.get("chapter_ids", []) if str(item).strip()]
        if chapter_id not in chapter_ids:
            chapter_ids.append(chapter_id)
            payload["chapter_ids"] = chapter_ids
            payload["updated_at"] = now_iso()
            save_json(path, payload, ignored_compare_keys=())
            changed = True
    return changed


def _write_chapter_manifest(
    *,
    subject: str,
    book_title: str,
    chapter_id: str,
    chapter_title: str,
    source_id: str,
    context_link: dict[str, Any],
    image_chapter: dict[str, Any],
    layout: dict[str, Path],
) -> bool:
    path = layout["manifest_chapters"] / f"{chapter_id}.json"
    existing = load_json_or_default(path, {})
    payload = {
        "chapter_id": chapter_id,
        "source_id": source_id,
        "subject": subject,
        "source_name": book_title,
        "chapter_title": chapter_title,
        "chapter_slug": sanitize_name(chapter_title),
        "batch_id": existing.get("batch_id") or sanitize_name(f"{book_title}-{chapter_id}"),
        "chapter_fingerprint": existing.get("chapter_fingerprint") or sanitize_name(f"{source_id}-{chapter_id}-{chapter_title}"),
        "created_at": existing.get("created_at") or now_iso(),
        "context_json_path": context_link.get("context_json_path", ""),
        "batch_output_dir": context_link.get("batch_output_dir", ""),
        "material_path": image_chapter.get("folder_path", ""),
        "mode": "chapter-photo",
        "page_sequence_mode": existing.get("page_sequence_mode", "ordered"),
        "start_page_number": existing.get("start_page_number"),
        "page_number_source": existing.get("page_number_source", "parallel-source-context"),
        "image_count": int(context_link.get("image_count", 0) or 0),
        "updated_at": now_iso(),
    }
    return save_json(path, payload, ignored_compare_keys=())


def repair_parallel_book_provenance(*, subject: str, book_title: str, chapter_number: int) -> dict[str, Any]:
    layout = ensure_kb_layout()
    link_path = _parallel_link_path(subject, book_title, layout)
    link_payload = load_json_or_default(link_path, {})
    if not link_payload:
        raise SystemExit(f"[ERROR] parallel source link not found: {link_path}")

    chapter_entry = _find_target_chapter(link_payload, chapter_number)
    context_links = list(chapter_entry.get("context_links", []) or [])
    if not context_links:
        raise SystemExit(f"[ERROR] chapter {chapter_number} has no context link to repair from")
    context_link = context_links[0]
    source_id = str(context_link.get("source_id", "")).strip()
    chapter_id = str(context_link.get("chapter_id", "")).strip()
    chapter_title = str(chapter_entry.get("chapter_title", "")).strip()
    if not source_id or not chapter_id or not chapter_title:
        raise SystemExit("[ERROR] target chapter is missing source_id, chapter_id, or chapter_title")

    chapter_view_path = _chapter_view_path(str(context_link.get("batch_output_dir", "")), chapter_id, chapter_title)
    source_changed = _update_source_payload(source_id, chapter_id, layout)
    chapter_manifest_written = _write_chapter_manifest(
        subject=subject,
        book_title=book_title,
        chapter_id=chapter_id,
        chapter_title=chapter_title,
        source_id=source_id,
        context_link=context_link,
        image_chapter=dict(chapter_entry.get("image_chapter", {}) or {}),
        layout=layout,
    )

    evidence_repaired_count = 0
    for path in sorted(layout["evidence"].glob("*.json")):
        payload = load_json_or_default(path, {})
        if payload.get("subject") != subject or payload.get("source_id") != source_id:
            continue
        source_spans = list(payload.get("source_spans", []) or [])
        if not any(str(item.get("chapter_id", "")).strip() == chapter_id for item in source_spans):
            continue
        existing_refs = {
            str(item.get("source_file_sha256", "")).strip(): item
            for item in payload.get("page_classification_refs", [])
            if str(item.get("source_file_sha256", "")).strip()
        }
        repaired_refs = []
        for span in source_spans:
            source_sha = str(span.get("source_file_sha256", "")).strip()
            if not source_sha:
                continue
            repaired_refs.append(
                _build_repaired_ref(
                    source_sha=source_sha,
                    existing_ref=existing_refs.get(source_sha),
                    subject=subject,
                    book_title=book_title,
                    chapter_id=chapter_id,
                    chapter_title=chapter_title,
                    chapter_view_path=chapter_view_path,
                )
            )
        if not repaired_refs:
            continue
        payload["book_title"] = book_title
        payload["chapter_id"] = chapter_id
        payload["chapter_title"] = chapter_title
        payload["page_classification_refs"] = repaired_refs
        payload["updated_at"] = now_iso()
        save_json(path, payload, ignored_compare_keys=())
        evidence_repaired_count += 1

    index_path = layout["root"] / "ocr" / "indexes" / "page_classification_index.json"
    index_payload = load_json_or_default(index_path, {})
    index_refs_repaired_count = 0
    if index_payload:
        items = []
        for item in index_payload.get("items", []):
            source_sha = str(item.get("source_file_sha256", "")).strip()
            refs = list(item.get("refs", []) or [])
            matching_ref = None
            for ref in refs:
                if str(ref.get("chapter_id", "")).strip() == chapter_id or str(ref.get("book_title", "")).strip() == book_title:
                    matching_ref = ref
                    break
                if str(ref.get("source_file_sha256", "")).strip() == source_sha:
                    matching_ref = ref
            if not refs:
                items.append(item)
                continue
            if any(str(ref.get("chapter_id", "")).strip() == "CH-408-0001" for ref in refs):
                repaired_ref = _build_repaired_ref(
                    source_sha=source_sha,
                    existing_ref=matching_ref or refs[0],
                    subject=subject,
                    book_title=book_title,
                    chapter_id=chapter_id,
                    chapter_title=chapter_title,
                    chapter_view_path=chapter_view_path,
                )
                items.append({"source_file_sha256": source_sha, "refs": [repaired_ref]})
                index_refs_repaired_count += 1
            else:
                items.append(item)
        index_payload["items"] = items
        index_payload["updated_at"] = now_iso()
        save_json(index_path, index_payload, ignored_compare_keys=())

    return {
        "subject": subject,
        "book_title": book_title,
        "chapter_number": chapter_number,
        "chapter_id": chapter_id,
        "chapter_title": chapter_title,
        "parallel_link_path": str(link_path),
        "summary": {
            "source_updated": source_changed,
            "chapter_manifest_written": chapter_manifest_written,
            "evidence_repaired_count": evidence_repaired_count,
            "index_refs_repaired_count": index_refs_repaired_count,
        },
    }


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    args = build_parser().parse_args()
    payload = repair_parallel_book_provenance(
        subject=args.subject,
        book_title=args.book_title,
        chapter_number=int(args.chapter_number),
    )
    if args.format == "json":
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
