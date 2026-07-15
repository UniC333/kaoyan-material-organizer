#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

from common import (
    SUBJECT_MAP,
    current_vault_root,
    ensure_kb_layout,
    load_all_json,
    load_json,
    load_json_or_default,
    now_iso,
    resolve_subject,
    sanitize_name,
    save_json,
)

CHAPTER_NUMBER_PATTERN = re.compile(r"第([0-9一二三四五六七八九十百零]+)章")
CHAPTER_SUFFIX_PATTERN = re.compile(r"第(?:[0-9一二三四五六七八九十百零]+)章[_\-\s]*(.*)$")
IGNORED_DIR_PREFIXES = {"00_", "90_", "99_"}
CN_DIGITS = {"零": 0, "一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Link PDF chapter anchors with chapter-photo folders and historical contexts.")
    parser.add_argument("--subject", required=True)
    parser.add_argument("--book-title", required=True)
    parser.add_argument("--image-book-root", required=True)
    parser.add_argument("--pdf-source-id", default="")
    parser.add_argument("--context-root", default="")
    parser.add_argument("--format", choices=("json", "quiet"), default="json")
    return parser


def _extract_chapter_number(text: str) -> int | None:
    match = CHAPTER_NUMBER_PATTERN.search(str(text or ""))
    if not match:
        return None
    token = match.group(1)
    if token.isdigit():
        return int(token)
    if token == "十":
        return 10
    if "十" in token:
        left, _, right = token.partition("十")
        tens = CN_DIGITS.get(left, 1 if left == "" else 0)
        ones = CN_DIGITS.get(right, 0 if right == "" else 0)
        return tens * 10 + ones
    if token in CN_DIGITS:
        return CN_DIGITS[token]
    return None


def _extract_chapter_suffix(text: str) -> str:
    match = CHAPTER_SUFFIX_PATTERN.search(str(text or ""))
    if not match:
        return ""
    return str(match.group(1) or "").strip(" _-")


def _normalized_chapter_title(text: str) -> str:
    number = _extract_chapter_number(text)
    if number is None:
        return str(text or "").strip()
    suffix = _extract_chapter_suffix(text)
    return f"第{number}章 {suffix}".strip()


def _discover_pdf_source_id(subject: str, book_title: str, layout: dict[str, Path]) -> str:
    candidates: list[dict[str, Any]] = []
    for payload in load_all_json(layout["sources"]):
        if payload.get("subject") != subject:
            continue
        if payload.get("material_type") != "book-pdf":
            continue
        if payload.get("source_name") != book_title:
            continue
        candidates.append(payload)
    if not candidates:
        raise SystemExit(f"[ERROR] no book-pdf source found for {subject} / {book_title}")
    candidates.sort(key=lambda item: str(item.get("updated_at") or ""))
    return str(candidates[-1]["source_id"])


def _image_file_count(path: Path) -> int:
    return sum(1 for child in path.rglob("*") if child.is_file() and child.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp", ".bmp"})


def _scan_image_chapters(image_book_root: Path) -> list[dict[str, Any]]:
    chapters: list[dict[str, Any]] = []
    for child in sorted(image_book_root.iterdir()):
        if not child.is_dir():
            continue
        if any(child.name.startswith(prefix) for prefix in IGNORED_DIR_PREFIXES):
            continue
        chapter_number = _extract_chapter_number(child.name)
        if chapter_number is None:
            continue
        chapters.append(
            {
                "chapter_number": chapter_number,
                "folder_name": child.name,
                "folder_path": str(child),
                "chapter_title_guess": _normalized_chapter_title(child.name),
                "image_count": _image_file_count(child),
            }
        )
    return chapters


def _default_context_root(subject: str) -> Path:
    _, config = resolve_subject(subject)
    return current_vault_root() / config["dir"] / config["content"]


def _scan_contexts(subject: str, book_title: str, context_root: Path) -> list[dict[str, Any]]:
    contexts: list[dict[str, Any]] = []
    if not context_root.exists():
        return contexts
    for path in sorted(context_root.rglob("00_批次上下文.json")):
        payload = load_json_or_default(path, {})
        if payload.get("subject") != subject and payload.get("resolved_subject") != subject:
            continue
        if payload.get("source_name") != book_title:
            continue
        chapter_title = str(payload.get("chapter_title") or payload.get("scope") or "").strip()
        chapter_number = _extract_chapter_number(chapter_title)
        contexts.append(
            {
                "context_json_path": str(path),
                "batch_output_dir": payload.get("batch_output_dir") or payload.get("content_output_dir") or "",
                "chapter_title": chapter_title,
                "chapter_number": chapter_number,
                "source_id": payload.get("source_id", ""),
                "chapter_id": payload.get("chapter_id", ""),
                "image_count": int(payload.get("image_count", 0) or 0),
                "knowledge_ready": bool(payload.get("knowledge_ready", False)),
            }
        )
    return contexts


def link_parallel_book_sources(
    *,
    subject: str,
    book_title: str,
    image_book_root: Path,
    pdf_source_id: str = "",
    context_root: Path | None = None,
) -> dict[str, Any]:
    resolved_subject, _ = resolve_subject(subject)
    image_book_root = image_book_root.resolve()
    if not image_book_root.exists():
        raise SystemExit(f"[ERROR] image book root not found: {image_book_root}")
    layout = ensure_kb_layout()
    pdf_source_id = str(pdf_source_id or "").strip() or _discover_pdf_source_id(resolved_subject, book_title, layout)
    anchors_path = layout["indexes"] / "pdf_book_anchors" / f"{pdf_source_id}.json"
    anchors_payload = load_json_or_default(anchors_path, {})
    if not anchors_payload:
        raise SystemExit(f"[ERROR] pdf anchors not found for source: {pdf_source_id}")

    resolved_context_root = context_root.resolve() if context_root else _default_context_root(resolved_subject)
    image_chapters = _scan_image_chapters(image_book_root)
    contexts = _scan_contexts(resolved_subject, book_title, resolved_context_root)
    image_by_number = {item["chapter_number"]: item for item in image_chapters}
    contexts_by_number: dict[int, list[dict[str, Any]]] = {}
    for item in contexts:
        chapter_number = item.get("chapter_number")
        if chapter_number is None:
            continue
        contexts_by_number.setdefault(int(chapter_number), []).append(item)

    linked_chapters: list[dict[str, Any]] = []
    for anchor in anchors_payload.get("chapter_anchors", []):
        chapter_number = _extract_chapter_number(anchor.get("title", ""))
        if chapter_number is None:
            continue
        image_match = image_by_number.get(chapter_number)
        context_matches = contexts_by_number.get(chapter_number, [])
        link_status = "linked" if image_match else "pdf-only"
        if image_match and not context_matches:
            link_status = "linked-no-context"
        linked_chapters.append(
            {
                "chapter_number": chapter_number,
                "chapter_title": _normalized_chapter_title(anchor.get("title", "")),
                "pdf_anchor": {
                    "source_id": pdf_source_id,
                    "title": anchor.get("title", ""),
                    "page_start": anchor.get("page_start"),
                    "page_end": anchor.get("page_end"),
                },
                "image_chapter": image_match or {},
                "context_links": context_matches,
                "link_status": link_status,
            }
        )

    payload = {
        "subject": resolved_subject,
        "book_title": book_title,
        "pdf_source_id": pdf_source_id,
        "pdf_anchors_path": str(anchors_path),
        "image_book_root": str(image_book_root),
        "context_root": str(resolved_context_root),
        "updated_at": now_iso(),
        "chapters": linked_chapters,
        "summary": {
            "chapter_count": len(linked_chapters),
            "image_linked_count": sum(1 for item in linked_chapters if item["image_chapter"]),
            "context_linked_count": sum(1 for item in linked_chapters if item["context_links"]),
            "pdf_only_count": sum(1 for item in linked_chapters if item["link_status"] == "pdf-only"),
        },
    }
    output_path = layout["indexes"] / "book_parallel_source_links" / f"{resolved_subject.lower()}-{sanitize_name(book_title)}.json"
    written = save_json(output_path, payload)
    payload["output_path"] = str(output_path)
    payload["written"] = written
    return payload


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    args = build_parser().parse_args()
    payload = link_parallel_book_sources(
        subject=args.subject,
        book_title=args.book_title,
        image_book_root=Path(args.image_book_root),
        pdf_source_id=args.pdf_source_id,
        context_root=Path(args.context_root) if args.context_root else None,
    )
    if args.format == "json":
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
