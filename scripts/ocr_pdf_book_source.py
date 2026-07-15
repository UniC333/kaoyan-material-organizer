#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from common import ensure_kb_layout, load_all_json, load_json_or_default, now_iso, run_utf8_subprocess, sanitize_name, save_json
from config import load_runtime_config
from ocr_document import run_ocr_for_file


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Render PDF chapter anchor pages and send them through the existing OCR pipeline.")
    parser.add_argument("--subject", required=True)
    parser.add_argument("--book-title", required=True)
    parser.add_argument("--pdf-source-id", default="")
    parser.add_argument("--chapter-number", action="append", type=int, default=[])
    parser.add_argument("--provider")
    parser.add_argument("--model")
    parser.add_argument("--fixture-json")
    parser.add_argument("--allow-remote", action="store_true")
    parser.add_argument("--yes", action="store_true")
    parser.add_argument("--dpi", type=int, default=200)
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


def _resolve_pdf_path(source_id: str, layout: dict[str, Path]) -> Path:
    source_payload = load_json_or_default(layout["sources"] / f"{source_id}.json", {})
    if not source_payload:
        raise SystemExit(f"[ERROR] source manifest not found: {source_id}")
    files = list(source_payload.get("files", []) or [])
    if not files:
        raise SystemExit(f"[ERROR] source {source_id} has no files")
    absolute_path = str(files[0].get("absolute_path", "")).strip()
    if not absolute_path:
        raise SystemExit(f"[ERROR] source {source_id} does not expose an absolute PDF path")
    pdf_path = Path(absolute_path)
    if not pdf_path.exists():
        raise SystemExit(f"[ERROR] pdf not found: {pdf_path}")
    return pdf_path


def _resolve_pdftoppm_path() -> str:
    explicit = str(os.environ.get("KAOYAN_PDFTOPPM_PATH", "")).strip()
    if explicit:
        return explicit
    discovered = shutil.which("pdftoppm")
    if discovered:
        return discovered
    bundled = Path.home() / ".cache" / "codex-runtimes" / "codex-primary-runtime" / "dependencies" / "native" / "poppler" / "Library" / "bin" / "pdftoppm.exe"
    if bundled.exists():
        return str(bundled)
    raise SystemExit("pdftoppm not found; set KAOYAN_PDFTOPPM_PATH or install Poppler")


def _render_pdf_page_to_png(*, pdf_path: Path, page_number: int, output_prefix: Path, dpi: int) -> Path:
    pdftoppm_program = _resolve_pdftoppm_path()
    command: list[str]
    if pdftoppm_program.lower().endswith(".py"):
        command = [
            sys.executable,
            pdftoppm_program,
            "-f",
            str(page_number),
            "-l",
            str(page_number),
            "-r",
            str(dpi),
            "-png",
            "-singlefile",
            str(pdf_path),
            str(output_prefix),
        ]
    else:
        command = [
            pdftoppm_program,
            "-f",
            str(page_number),
            "-l",
            str(page_number),
            "-r",
            str(dpi),
            "-png",
            "-singlefile",
            str(pdf_path),
            str(output_prefix),
        ]
    output_prefix.parent.mkdir(parents=True, exist_ok=True)
    completed = run_utf8_subprocess(command, command_label="external:pdftoppm")
    if completed.returncode != 0:
        raise SystemExit((completed.stderr or completed.stdout or "pdftoppm failed").strip())
    png_path = output_prefix.with_suffix(".png")
    if not png_path.exists():
        raise SystemExit(f"rendered PNG missing: {png_path}")
    return png_path


def _allowed_remote(provider_name: str, runtime, *, allow_remote: bool, yes: bool) -> bool:
    if provider_name != "mistral":
        return True
    return runtime.ocr_allow_remote or (allow_remote and yes)


def ocr_pdf_book_source(
    *,
    subject: str,
    book_title: str,
    pdf_source_id: str = "",
    chapter_numbers: list[int] | None = None,
    provider_name: str | None = None,
    model: str | None = None,
    fixture_json: Path | None = None,
    allow_remote: bool = False,
    yes: bool = False,
    dpi: int = 200,
) -> dict[str, Any]:
    runtime = load_runtime_config()
    layout = ensure_kb_layout()
    resolved_provider = provider_name or runtime.ocr_provider
    resolved_model = model or runtime.ocr_model
    remote_enabled = _allowed_remote(resolved_provider, runtime, allow_remote=allow_remote, yes=yes)
    if not remote_enabled:
        if resolved_provider == "mistral":
            raise SystemExit("mistral OCR is disabled; enable KAOYAN_OCR_ALLOW_REMOTE=true or pass --allow-remote --yes")

    resolved_source_id = _resolve_pdf_source_id(subject, book_title, layout, pdf_source_id)
    pdf_path = _resolve_pdf_path(resolved_source_id, layout)
    anchors_path = layout["indexes"] / "pdf_book_anchors" / f"{resolved_source_id}.json"
    anchors_payload = load_json_or_default(anchors_path, {})
    if not anchors_payload:
        raise SystemExit(f"[ERROR] pdf anchors not found for source: {resolved_source_id}")

    requested = list(chapter_numbers or [])
    chapters = []
    for anchor in anchors_payload.get("chapter_anchors", []):
        chapter_number = int(anchor.get("chapter_index", 0) or 0)
        if requested and chapter_number not in requested:
            continue
        chapters.append(anchor)
    if not chapters:
        raise SystemExit("no chapter anchors selected for OCR")

    render_root = runtime.workspace_root / "tmp" / "pdfs" / sanitize_name(book_title)
    report_chapters: list[dict[str, Any]] = []
    remote_requests = 0
    for anchor in chapters:
        chapter_number = int(anchor["chapter_index"])
        page_number = int(anchor["page_start"])
        output_prefix = render_root / f"chapter-{chapter_number:02d}-page-{page_number:04d}"
        rendered_image = _render_pdf_page_to_png(pdf_path=pdf_path, page_number=page_number, output_prefix=output_prefix, dpi=dpi)
        result = run_ocr_for_file(
            source_file=rendered_image,
            runtime=runtime,
            provider_name=resolved_provider,
            model=resolved_model,
            pages="0-0",
            fixture_json=fixture_json,
            allow_remote=remote_enabled,
        )
        remote_requests += int(result.get("remote_calls", 0) or 0)
        report_chapters.append(
            {
                "chapter_number": chapter_number,
                "chapter_title": str(anchor.get("title", "")).strip(),
                "page_start": page_number,
                "page_end": int(anchor.get("page_end", page_number) or page_number),
                "rendered_image_path": str(rendered_image),
                "provider": resolved_provider,
                "model": resolved_model,
                "request_key": result["request_key"],
                "cache_hit": bool(result.get("cache_hit")),
                "remote_calls": int(result.get("remote_calls", 0) or 0),
                "normalized_path": str(result["normalized_path"]),
                "raw_path": str(result["raw_path"]),
            }
        )

    report_payload = {
        "subject": subject,
        "book_title": book_title,
        "pdf_source_id": resolved_source_id,
        "pdf_path": str(pdf_path),
        "anchors_path": str(anchors_path),
        "updated_at": now_iso(),
        "chapters": report_chapters,
        "summary": {
            "chapter_count": len(report_chapters),
            "processed_count": len(report_chapters),
            "remote_requests": remote_requests,
        },
    }
    report_path = layout["indexes"] / "pdf_ocr_runs" / f"{subject.lower()}-{sanitize_name(book_title)}.json"
    save_json(report_path, report_payload, ignored_compare_keys=())
    return {**report_payload, "report_path": str(report_path)}


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    args = build_parser().parse_args()
    payload = ocr_pdf_book_source(
        subject=args.subject,
        book_title=args.book_title,
        pdf_source_id=args.pdf_source_id,
        chapter_numbers=[int(item) for item in args.chapter_number],
        provider_name=args.provider,
        model=args.model,
        fixture_json=Path(args.fixture_json) if args.fixture_json else None,
        allow_remote=args.allow_remote,
        yes=args.yes,
        dpi=max(72, int(args.dpi)),
    )
    if args.format == "json":
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
