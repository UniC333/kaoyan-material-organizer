#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from common import ensure_kb_layout, load_all_json, now_iso, sanitize_name, save_json

REQUIRED_BOOKS = {
    "王道数据结构": {
        "required_linked_chapters": [1, 2, 3],
        "require_linked_no_context_after": 3,
        "description": "第 1~3 章必须保持 linked，后续未补 context 的章节仍需显式保留 linked-no-context。",
    }
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate parallel PDF-plus-image source links for current 408 guardrails.")
    parser.add_argument("--subject", required=True)
    parser.add_argument("--book-title", action="append", default=[])
    parser.add_argument("--format", choices=("json", "quiet"), default="json")
    return parser


def _load_parallel_links(layout: dict[str, Path], subject: str, requested_titles: list[str]) -> list[dict[str, Any]]:
    links_root = layout["indexes"] / "book_parallel_source_links"
    if not links_root.exists():
        raise SystemExit(f"[ERROR] parallel source link root missing for subject: {subject}")
    payloads = [
        payload
        for payload in load_all_json(links_root)
        if str(payload.get("subject") or "").strip() == subject
    ]
    by_title = {str(payload.get("book_title") or "").strip(): payload for payload in payloads}
    if requested_titles:
        missing = [title for title in requested_titles if title not in by_title]
        if missing:
            raise SystemExit(f"[ERROR] parallel source link report missing for {subject}: {', '.join(missing)}")
        return [by_title[title] for title in requested_titles]
    return sorted(payloads, key=lambda item: str(item.get("book_title") or ""))


def _book_report(layout: dict[str, Path], payload: dict[str, Any]) -> dict[str, Any]:
    book_title = str(payload.get("book_title") or "")
    subject = str(payload.get("subject") or "")
    config = REQUIRED_BOOKS.get(book_title) or {
        "required_linked_chapters": [],
        "require_linked_no_context_after": 0,
        "description": "默认不附加特定并行来源守卫。",
    }
    chapters = list(payload.get("chapters") or [])
    issues: list[str] = []

    linked_chapter_1_to_3 = 0
    linked_no_context_count = 0
    for chapter in chapters:
        chapter_number = int(chapter.get("chapter_number", 0) or 0)
        chapter_title = str(chapter.get("chapter_title") or "")
        pdf_title = str((chapter.get("pdf_anchor") or {}).get("title") or "")
        image_title = str((chapter.get("image_chapter") or {}).get("chapter_title_guess") or "")
        link_status = str(chapter.get("link_status") or "")
        context_links = list(chapter.get("context_links") or [])

        if chapter_number in config["required_linked_chapters"]:
            if link_status != "linked":
                issues.append("required-chapter-not-linked")
            else:
                linked_chapter_1_to_3 += 1
            if context_links:
                context_number = context_links[0].get("chapter_number")
                if isinstance(context_number, int) and context_number != chapter_number:
                    issues.append("context-chapter-number-drift")
            if chapter_title and pdf_title and chapter_title != pdf_title:
                issues.append("pdf-chapter-title-drift")
        if chapter_number > int(config.get("require_linked_no_context_after", 0) or 0) and link_status == "linked-no-context":
            linked_no_context_count += 1
        if link_status == "linked" and not context_links:
            issues.append("linked-without-context")
        if link_status == "linked-no-context" and context_links:
            issues.append("linked-no-context-with-context")
        if not image_title and chapter_number in config["required_linked_chapters"]:
            issues.append("missing-image-chapter-title")

    output_path = layout["indexes"] / "book_parallel_source_links" / f"{subject.lower()}-{sanitize_name(book_title)}.json"
    return {
        "subject": subject,
        "source_id": str(payload.get("pdf_source_id") or ""),
        "book_title": book_title,
        "parallel_link_path": str(output_path),
        "expectation": config,
        "summary": {
            "chapter_count": len(chapters),
            "required_linked_chapters": len(config["required_linked_chapters"]),
            "linked_chapter_1_to_3": linked_chapter_1_to_3,
            "linked_no_context_count": linked_no_context_count,
        },
        "issues": sorted(set(issues)),
        "passed": not issues,
    }


def build_parallel_source_guard_report(*, subject: str, requested_titles: list[str] | None = None) -> dict[str, Any]:
    layout = ensure_kb_layout()
    requested_titles = [title.strip() for title in (requested_titles or []) if title.strip()]
    payloads = _load_parallel_links(layout, subject, requested_titles)
    books = [_book_report(layout, payload) for payload in payloads]
    output_path = layout["indexes"] / "parallel_source_guardrails" / f"{subject.lower()}-parallel-source-guard.json"
    report = {
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
    written = save_json(output_path, report, ignored_compare_keys=("generated_at",))
    report["output_path"] = str(output_path)
    report["written"] = written
    return report


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    args = build_parser().parse_args()
    report = build_parallel_source_guard_report(subject=args.subject, requested_titles=args.book_title)
    if args.format == "json":
        print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
