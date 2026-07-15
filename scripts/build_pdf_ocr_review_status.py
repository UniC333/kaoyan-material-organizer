#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from common import ensure_kb_layout, load_all_json, load_json_or_default, now_iso, sanitize_name, save_json
from config import load_runtime_config


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Adapt PDF OCR baseline outputs into a reviewable shadow book_root with page_ocr_status and page_classifications."
    )
    parser.add_argument("--subject", required=True)
    parser.add_argument("--book-title", required=True)
    parser.add_argument("--pdf-source-id", default="")
    parser.add_argument("--report-path", default="")
    parser.add_argument("--format", choices=("json", "quiet"), default="json")
    return parser


def _resolve_pdf_source_id(subject: str, book_title: str, layout: dict[str, Path], explicit: str) -> str:
    if explicit:
        return explicit
    candidates = []
    for payload in load_all_json(layout["sources"]):
        if payload.get("subject") != subject:
            continue
        if payload.get("material_type") != "book-pdf":
            continue
        if payload.get("source_name") != book_title:
            continue
        candidates.append(payload)
    if not candidates:
        raise SystemExit(f"[ERROR] no registered book-pdf source found for {subject} / {book_title}")
    candidates.sort(key=lambda item: str(item.get("updated_at") or ""))
    return str(candidates[-1]["source_id"])


def _resolve_report_path(subject: str, book_title: str, layout: dict[str, Path], explicit: str) -> Path:
    if explicit:
        return Path(explicit)
    return layout["indexes"] / "pdf_ocr_runs" / f"{subject.lower()}-{sanitize_name(book_title)}.json"


def _metadata_paths(book_root: Path, metadata_dirname: str) -> dict[str, Path]:
    metadata_root = book_root / metadata_dirname
    return {
        "root": metadata_root,
        "page_ocr_status": metadata_root / "page_ocr_status.json",
        "page_classifications": metadata_root / "page_classifications.json",
        "chapter_definitions": metadata_root / "chapter_definitions.json",
    }


def _render_chapter_view(chapter: dict[str, Any], chapter_items: list[dict[str, Any]]) -> str:
    lines = [
        "---",
        "generated_by: kaoyan-material-organizer",
        f"chapter_id: {chapter['chapter_id']}",
        "---",
        f"# {chapter['chapter_title']}",
        "",
        f"- PDF 锚点页: 第{chapter['page_start']}页",
        "",
        "## 复核入口",
        "",
    ]
    for item in chapter_items:
        lines.append(f"- 第{item['printed_page']}页 | {item['page_id']} | {item['classification_status']}")
    return "\n".join(lines) + "\n"


def build_pdf_ocr_review_status(
    *,
    subject: str,
    book_title: str,
    pdf_source_id: str = "",
    report_path: Path | None = None,
) -> dict[str, Any]:
    runtime = load_runtime_config()
    layout = ensure_kb_layout()
    resolved_source_id = _resolve_pdf_source_id(subject, book_title, layout, pdf_source_id)
    resolved_report_path = report_path or _resolve_report_path(subject, book_title, layout, "")
    report_payload = load_json_or_default(resolved_report_path, {})
    if not report_payload:
        raise SystemExit(f"[ERROR] pdf OCR report not found: {resolved_report_path}")

    bridge_root = layout["review_queues"] / "pdf-ocr-books" / f"{subject.lower()}-{sanitize_name(book_title)}"
    metadata_paths = _metadata_paths(bridge_root, runtime.paper_book_metadata_dir)
    metadata_paths["root"].mkdir(parents=True, exist_ok=True)
    chapter_views_root = bridge_root / "views" / "by-chapter"
    chapter_views_root.mkdir(parents=True, exist_ok=True)

    book_id = f"PDFOCR-{resolved_source_id}"
    status_items: list[dict[str, Any]] = []
    classification_items: list[dict[str, Any]] = []
    chapter_definitions: list[dict[str, Any]] = []
    chapter_view_paths: list[str] = []

    for chapter in report_payload.get("chapters", []):
        chapter_number = int(chapter.get("chapter_number", 0) or 0)
        printed_page = int(chapter.get("page_start", 0) or 0)
        chapter_id = f"PDFCH-{resolved_source_id}-{chapter_number:04d}"
        page_id = f"PDFPAGE-{resolved_source_id}-{chapter_number:04d}-{printed_page:04d}"
        normalized_path = Path(str(chapter.get("normalized_path", "")).strip())
        raw_path = Path(str(chapter.get("raw_path", "")).strip())
        normalized_payload = load_json_or_default(normalized_path, {})
        completed_at = str(normalized_payload.get("normalized_at") or report_payload.get("updated_at") or now_iso())
        updated_at = str(report_payload.get("updated_at") or now_iso())
        source_file_sha256 = str(normalized_payload.get("source_file_sha256", "")).strip()

        status_items.append(
            {
                "page_id": page_id,
                "book_id": book_id,
                "scan_index": chapter_number,
                "printed_page": printed_page,
                "printed_page_label": f"第{printed_page}页",
                "current_version_id": f"{page_id}-v1",
                "source_image_path": str(chapter.get("rendered_image_path", "")),
                "source_image_sha256": source_file_sha256,
                "quality_status": "accepted",
                "request_key": str(chapter.get("request_key", "")).strip(),
                "provider": str(chapter.get("provider", "")).strip(),
                "model": str(chapter.get("model", "")).strip(),
                "exact_model": str(normalized_payload.get("exact_model", "")).strip(),
                "status": "completed",
                "attempt_count": 0,
                "error_history": [],
                "last_error": None,
                "normalized_path": str(normalized_path),
                "raw_path": str(raw_path),
                "last_run_id": "",
                "completed_at": completed_at,
                "updated_at": updated_at,
            }
        )
        classification_items.append(
            {
                "page_classification_id": f"PCLASS-{resolved_source_id}-{chapter_number:04d}",
                "page_id": page_id,
                "book_id": book_id,
                "chapter_id": chapter_id,
                "chapter_title": str(chapter.get("chapter_title", "")).strip(),
                "section_id": None,
                "section_title": None,
                "classification_method": "pdf_anchor_page",
                "classification_status": "confirmed",
                "classification_confidence": 1.0,
                "classification_source": "pdf_book_anchors",
                "confirmed_by": "pdf-outline-anchor",
                "confirmed_at": updated_at,
                "updated_at": updated_at,
                "printed_page": printed_page,
            }
        )
        chapter_definitions.append(
            {
                "chapter_id": chapter_id,
                "book_id": book_id,
                "chapter_title": str(chapter.get("chapter_title", "")).strip(),
                "page_start": printed_page,
                "page_end": int(chapter.get("page_end", printed_page) or printed_page),
                "definition_status": "confirmed",
                "sections": [],
                "created_at": updated_at,
                "updated_at": updated_at,
            }
        )

    by_chapter_id = {item["chapter_id"]: [] for item in classification_items if item.get("chapter_id")}
    for item in classification_items:
        if item.get("chapter_id"):
            by_chapter_id[str(item["chapter_id"])].append(item)
    for chapter in chapter_definitions:
        view_path = chapter_views_root / f"{chapter['chapter_id']}_{sanitize_name(chapter['chapter_title'])}.md"
        view_path.write_text(_render_chapter_view(chapter, by_chapter_id.get(chapter["chapter_id"], [])), encoding="utf-8")
        chapter_view_paths.append(str(view_path))

    status_payload = {
        "book_id": book_id,
        "source_root": str(bridge_root),
        "provider": str(report_payload.get("chapters", [{}])[0].get("provider", "") if report_payload.get("chapters") else ""),
        "model": str(report_payload.get("chapters", [{}])[0].get("model", "") if report_payload.get("chapters") else ""),
        "created_at": report_payload.get("updated_at") or now_iso(),
        "updated_at": now_iso(),
        "items": status_items,
        "summary": {
            "completed_count": len(status_items),
            "failed_count": 0,
            "retry_exhausted_count": 0,
            "budget_blocked_count": 0,
            "skipped_quality_count": 0,
            "pending_count": 0,
        },
    }
    classifications_payload = {
        "book_id": book_id,
        "created_at": report_payload.get("updated_at") or now_iso(),
        "updated_at": now_iso(),
        "items": classification_items,
        "summary": {
            "confirmed_count": len(classification_items),
            "candidate_count": 0,
            "conflict_count": 0,
            "unassigned_count": 0,
        },
    }
    chapter_definitions_payload = {
        "book_id": book_id,
        "created_at": report_payload.get("updated_at") or now_iso(),
        "updated_at": now_iso(),
        "items": chapter_definitions,
    }

    save_json(metadata_paths["page_ocr_status"], status_payload, ignored_compare_keys=())
    save_json(metadata_paths["page_classifications"], classifications_payload, ignored_compare_keys=())
    save_json(metadata_paths["chapter_definitions"], chapter_definitions_payload, ignored_compare_keys=())
    (bridge_root / "book.yaml").write_text(f"book_title: {book_title}\n", encoding="utf-8")

    bridge_report_path = layout["indexes"] / "pdf_ocr_review_status" / f"{subject.lower()}-{sanitize_name(book_title)}.json"
    bridge_payload = {
        "subject": subject,
        "book_title": book_title,
        "book_id": book_id,
        "pdf_source_id": resolved_source_id,
        "pdf_ocr_report_path": str(resolved_report_path),
        "book_root": str(bridge_root),
        "page_ocr_status_path": str(metadata_paths["page_ocr_status"]),
        "page_classifications_path": str(metadata_paths["page_classifications"]),
        "chapter_definitions_path": str(metadata_paths["chapter_definitions"]),
        "chapter_view_paths": chapter_view_paths,
        "updated_at": now_iso(),
        "summary": {
            "completed_count": len(status_items),
            "chapter_count": len(chapter_definitions),
            "reviewable_request_count": len(status_items),
        },
    }
    save_json(bridge_report_path, bridge_payload, ignored_compare_keys=())
    return {**bridge_payload, "report_path": str(bridge_report_path)}


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    args = build_parser().parse_args()
    payload = build_pdf_ocr_review_status(
        subject=args.subject,
        book_title=args.book_title,
        pdf_source_id=args.pdf_source_id,
        report_path=Path(args.report_path) if args.report_path else None,
    )
    if args.format == "json":
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
