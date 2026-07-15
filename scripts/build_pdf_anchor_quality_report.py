#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from build_pdf_acceptance_checklist import _discover_book_pdf_sources
from common import ensure_kb_layout, load_json_or_default, now_iso, save_json

WHOLE_BOOK_MIN_CHAPTERS = 5
EXPECTATIONS = {
    "王道数据结构": {
        "type": "min_chapters",
        "required_chapters": [1, 2, 3],
        "description": "至少稳定覆盖第 1~3 章",
    },
    "王道计算机组成原理": {
        "type": "whole_book_structure",
        "minimum_chapter_count": WHOLE_BOOK_MIN_CHAPTERS,
        "description": "至少具备整本章节结构",
    },
    "王道计算机网络": {
        "type": "whole_book_structure",
        "minimum_chapter_count": WHOLE_BOOK_MIN_CHAPTERS,
        "description": "至少具备整本章节结构",
    },
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate current PDF book anchor quality for the 408 phase.")
    parser.add_argument("--subject", required=True)
    parser.add_argument("--book-title", action="append", default=[])
    parser.add_argument("--format", choices=("json", "quiet"), default="json")
    return parser


def _validate_anchor_structure(chapter_anchors: list[dict[str, Any]]) -> list[str]:
    issues: list[str] = []
    prev_page_start = 0
    prev_chapter_index = 0
    for idx, anchor in enumerate(chapter_anchors, start=1):
        title = str(anchor.get("title") or "").strip()
        page_start = anchor.get("page_start")
        page_end = anchor.get("page_end")
        chapter_index = anchor.get("chapter_index")
        if not title:
            issues.append(f"chapter-{idx}-missing-title")
        if not isinstance(page_start, int) or page_start < 1:
            issues.append(f"chapter-{idx}-invalid-page-start")
        if not isinstance(page_end, int) or (isinstance(page_start, int) and page_end < page_start):
            issues.append(f"chapter-{idx}-invalid-page-end")
        if anchor.get("anchor_type") != "chapter":
            issues.append(f"chapter-{idx}-invalid-anchor-type")
        if not isinstance(chapter_index, int) or chapter_index < 1:
            issues.append(f"chapter-{idx}-invalid-chapter-index")
        if isinstance(page_start, int) and page_start <= prev_page_start:
            issues.append(f"chapter-{idx}-non-monotonic-page-start")
        if isinstance(chapter_index, int) and chapter_index != prev_chapter_index + 1:
            issues.append(f"chapter-{idx}-non-sequential-chapter-index")
        if isinstance(page_start, int):
            prev_page_start = page_start
        if isinstance(chapter_index, int):
            prev_chapter_index = chapter_index
    return issues


def _validate_expectation(book_title: str, chapter_anchors: list[dict[str, Any]]) -> tuple[dict[str, Any], list[str]]:
    expectation = dict(EXPECTATIONS.get(book_title) or {
        "type": "whole_book_structure",
        "minimum_chapter_count": WHOLE_BOOK_MIN_CHAPTERS,
        "description": "默认按整本章节结构守卫",
    })
    issues: list[str] = []
    chapter_indices = [anchor.get("chapter_index") for anchor in chapter_anchors if isinstance(anchor.get("chapter_index"), int)]

    if expectation["type"] == "min_chapters":
        required = list(expectation.get("required_chapters") or [])
        missing = [item for item in required if item not in chapter_indices]
        if missing:
            issues.append("missing-required-chapters")
        if len(chapter_anchors) < len(required):
            issues.append("chapter-count-below-minimum")
    elif expectation["type"] == "whole_book_structure":
        minimum = int(expectation.get("minimum_chapter_count", WHOLE_BOOK_MIN_CHAPTERS))
        if len(chapter_anchors) < minimum:
            issues.append("chapter-count-below-minimum")
        if chapter_indices and max(chapter_indices) != len(chapter_anchors):
            issues.append("chapter-index-count-mismatch")
    return expectation, issues


def build_pdf_anchor_quality_report(*, subject: str, requested_titles: list[str] | None = None) -> dict[str, Any]:
    layout = ensure_kb_layout()
    sources = _discover_book_pdf_sources(subject=subject, requested_titles=list(requested_titles or []), layout=layout)

    books: list[dict[str, Any]] = []
    for source_payload in sources:
        source_id = str(source_payload["source_id"])
        book_title = str(source_payload.get("source_name") or "")
        anchor_path = layout["indexes"] / "pdf_book_anchors" / f"{source_id}.json"
        anchor_payload = load_json_or_default(anchor_path, {})
        chapter_anchors = list(anchor_payload.get("chapter_anchors") or [])
        issues = []
        if not anchor_payload:
            issues.append("missing-anchor-file")
        if not chapter_anchors:
            issues.append("missing-chapter-anchors")
        issues.extend(_validate_anchor_structure(chapter_anchors))
        expectation, expectation_issues = _validate_expectation(book_title, chapter_anchors)
        issues.extend(expectation_issues)
        books.append(
            {
                "source_id": source_id,
                "book_title": book_title,
                "anchor_path": str(anchor_path),
                "page_count": int(anchor_payload.get("page_count", 0) or 0),
                "outline_count": int(anchor_payload.get("outline_count", 0) or 0),
                "chapter_anchor_count": int(anchor_payload.get("chapter_anchor_count", len(chapter_anchors)) or 0),
                "first_chapters": [
                    {
                        "chapter_index": anchor.get("chapter_index"),
                        "title": anchor.get("title", ""),
                        "page_start": anchor.get("page_start"),
                        "page_end": anchor.get("page_end"),
                    }
                    for anchor in chapter_anchors[:3]
                ],
                "expectation": expectation,
                "issues": issues,
                "passed": not issues,
            }
        )

    output_path = layout["indexes"] / "pdf_anchor_quality" / f"{subject.lower()}-anchor-quality.json"
    payload = {
        "subject": subject,
        "generated_at": now_iso(),
        "passed": all(item["passed"] for item in books),
        "books": books,
        "summary": {
            "books_checked": len(books),
            "books_passed": sum(1 for item in books if item["passed"]),
            "books_failed": sum(1 for item in books if not item["passed"]),
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
    payload = build_pdf_anchor_quality_report(subject=args.subject, requested_titles=args.book_title)
    if args.format == "json":
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
