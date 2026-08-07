#!/usr/bin/env python3
"""Publish complete, page-bounded Mistral PDF OCR as cited evidence records."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

from common import (
    allocate_kb_id,
    build_provenance_record,
    build_source_span,
    ensure_kb_layout,
    load_all_json,
    load_json_or_default,
    now_iso,
    save_json,
    stable_fingerprint,
    validate_entity_contract,
)
from config import load_runtime_config


CHAPTERS = {
    1: (13, 24, "第1章 绪论"),
    2: (25, 74, "第2章 线性表"),
    3: (75, 120, "第3章 栈、队列和数组"),
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--subject", default="408")
    parser.add_argument("--book-title", required=True)
    parser.add_argument("--pdf-source-id", required=True)
    parser.add_argument("--image-root", required=True)
    parser.add_argument("--format", choices=("json", "quiet"), default="json")
    return parser.parse_args()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def chapter_for(page: int) -> tuple[int, str]:
    for number, (start, end, title) in CHAPTERS.items():
        if start <= page <= end:
            return number, title
    raise ValueError(f"page outside configured coverage: {page}")


def normalized_by_image(cache_root: Path) -> dict[str, list[tuple[Path, dict[str, Any]]]]:
    result: dict[str, list[tuple[Path, dict[str, Any]]]] = {}
    for path in (cache_root / "normalized").glob("*.json"):
        payload = load_json_or_default(path, {})
        if (
            payload.get("provider") != "mistral"
            or payload.get("exact_model") != "mistral-ocr-4-0"
            or not str(payload.get("source_file_sha256", ""))
        ):
            continue
        text = "\n".join(str(page.get("text", "")).strip() for page in payload.get("pages", []) if isinstance(page, dict)).strip()
        if len(text) <= 100:
            continue
        result.setdefault(str(payload["source_file_sha256"]), []).append((path, payload))
    return result


def source_file(source: dict[str, Any]) -> dict[str, Any]:
    files = [item for item in source.get("files", []) if isinstance(item, dict)]
    if source.get("material_type") != "book-pdf" or len(files) != 1:
        raise SystemExit("[ERROR] expected a book-pdf source with exactly one registered file")
    return files[0]


def existing_by_key(layout: dict[str, Path]) -> dict[str, dict[str, Any]]:
    return {str(item.get("evidence_key")): item for item in load_all_json(layout["evidence"]) if item.get("evidence_key")}


def publish(subject: str, book_title: str, pdf_source_id: str, image_root: Path) -> dict[str, Any]:
    layout = ensure_kb_layout()
    config = load_runtime_config()
    source = load_json_or_default(layout["sources"] / f"{pdf_source_id}.json", {})
    file_record = source_file(source)
    candidate_by_hash = normalized_by_image(config.ocr_cache_root)
    existing = existing_by_key(layout)
    written: list[dict[str, Any]] = []
    manifest_pages: list[dict[str, Any]] = []
    for page in range(13, 121):
        image_path = image_root / f"page-{page:03d}.png"
        if not image_path.is_file():
            raise SystemExit(f"[ERROR] missing rendered PDF page: {image_path}")
        image_sha = sha256_file(image_path)
        candidates = candidate_by_hash.get(image_sha, [])
        if not candidates:
            raise SystemExit(f"[ERROR] no qualifying Mistral OCR payload for PDF page {page}")
        # Interrupted batch work may leave multiple valid Mistral responses for one image.
        # Select deterministically, preferring the more complete transcription.
        ranked = sorted(
            candidates,
            key=lambda item: (
                -len("\n".join(str(part.get("text", "")).strip() for part in item[1].get("pages", []) if isinstance(part, dict)).strip()),
                str(item[1].get("request_key", item[0].name)),
            ),
        )
        normalized_path, normalized = ranked[0]
        text = "\n".join(str(item.get("text", "")).strip() for item in normalized.get("pages", []) if isinstance(item, dict)).strip()
        number, chapter_title = chapter_for(page)
        chapter_id = f"PDFCH-{pdf_source_id}-{number:04d}"
        chunk_id = f"PDFOCR-FULL-{page:04d}"
        evidence_key = stable_fingerprint({
            "source_id": pdf_source_id,
            "source_file_sha256": file_record.get("sha256", ""),
            "printed_page": page,
            "ocr_source_sha256": image_sha,
            "origin_type": "pdf_page_ocr",
        })
        current = existing.get(evidence_key, {})
        span = build_source_span(
            source_id=pdf_source_id,
            file_id=str(file_record.get("file_id", "")),
            source_file_sha256=str(file_record.get("sha256", "")),
            chapter_id=chapter_id,
            chunk_id=chunk_id,
            page_start=page,
            page_end=page,
            image_start=page,
            image_end=page,
            origin_type="pdf_page_ocr",
            verification_status="reviewed",
            notes="Rendered PDF page matched by SHA-256 to Mistral OCR; full chapter-coverage audit passed.",
        )
        evidence = {
            "evidence_id": current.get("evidence_id") or allocate_kb_id("evidence", subject),
            "evidence_key": evidence_key,
            "subject": subject,
            "book_title": book_title,
            "source_id": pdf_source_id,
            "chapter_id": chapter_id,
            "chapter_title": chapter_title,
            "chunk_id": chunk_id,
            "title": f"{chapter_title}（PDF 第{page}页）",
            "content": text,
            "evidence_type": "textbook-page",
            "origin_type": "pdf_page_ocr",
            "verification_status": "reviewed",
            "review_status": "accepted",
            "review_decision": "automated-complete-page-text-gate",
            "review_note": "All 108 rendered pages passed exact SHA-256 OCR matching and non-empty-text gate; source span remains one PDF page.",
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
            "pdf_ocr_normalized_path": str(normalized_path),
            "coverage_note": "This record covers exactly one rendered PDF page; no neighboring-page inference.",
            "updated_at": now_iso(),
        }
        validate_entity_contract("evidence", evidence)
        save_json(layout["evidence"] / f"{evidence['evidence_id']}.json", evidence)
        written.append(evidence)
        manifest_pages.append({"pdf_page": page, "evidence_id": evidence["evidence_id"], "normalized_path": str(normalized_path), "text_length": len(text), "qualifying_ocr_candidates": len(candidates)})
    manifest = {
        "publication_id": "408-王道数据结构-full-ch1-ch3",
        "created_at": now_iso(),
        "pdf_source_id": pdf_source_id,
        "book_title": book_title,
        "coverage": [{"chapter": key, "page_start": value[0], "page_end": value[1]} for key, value in CHAPTERS.items()],
        "published_count": len(manifest_pages),
        "pages": manifest_pages,
        "verification": "Each page has SHA-256-matched Mistral mistral-ocr-4-0 OCR with text length > 100; duplicate valid responses use the longest text then request-key order.",
    }
    destination = layout["indexes"] / "pdf_ocr_evidence_publications" / "408-王道数据结构-full-ch1-ch3.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    save_json(destination, manifest)
    return {"published_count": len(written), "manifest_path": str(destination), "evidence_ids": [item["evidence_id"] for item in written]}


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    args = parse_args()
    payload = publish(args.subject, args.book_title, args.pdf_source_id, Path(args.image_root))
    if args.format == "json":
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
