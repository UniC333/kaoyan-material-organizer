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

from PIL import Image

from common import ensure_kb_layout, load_all_json, load_json_or_default, now_iso, run_utf8_subprocess, sanitize_name, save_json
from config import load_runtime_config
from ocr_document import run_ocr_for_file


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Render PDF chapter anchor pages and send them through the existing OCR pipeline.")
    parser.add_argument("--subject", required=True)
    parser.add_argument("--book-title", required=True)
    parser.add_argument("--pdf-source-id", default="")
    parser.add_argument("--chapter-number", action="append", type=int, default=[])
    parser.add_argument("--page-start", type=int)
    parser.add_argument("--page-end", type=int)
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
    # Poppler PNGs are valid locally but some OCR providers reject their encoded
    # color/profile combination. Re-encoding gives the provider a plain RGB PNG
    # while retaining the rendered pixels and keeps the page hash traceable.
    normalized_path = output_prefix.with_name(f"{output_prefix.name}-rgb").with_suffix(".png")
    with Image.open(png_path) as image:
        image.convert("RGB").save(normalized_path, format="PNG", optimize=False)
    normalized_path.replace(png_path)
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
    page_start: int | None = None,
    page_end: int | None = None,
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

    if (page_start is None) != (page_end is None):
        raise SystemExit("--page-start and --page-end must be provided together")
    if page_start is not None and (page_start < 1 or page_end < page_start):
        raise SystemExit("invalid PDF page range")
    requested = list(chapter_numbers or [])
    chapters = []
    for anchor in anchors_payload.get("chapter_anchors", []):
        chapter_number = int(anchor.get("chapter_index", 0) or 0)
        if requested and chapter_number not in requested:
            continue
        chapters.append(anchor)
    if not chapters:
        raise SystemExit("no chapter anchors selected for OCR")

    pages: list[dict[str, Any]] = []
    if page_start is not None:
        ordered = sorted(anchors_payload.get("chapter_anchors", []), key=lambda item: int(item.get("page_start", 0) or 0))
        for pdf_page in range(page_start, page_end + 1):
            containing = [item for item in ordered if int(item.get("page_start", 0) or 0) <= pdf_page <= int(item.get("page_end", pdf_page) or pdf_page)]
            anchor = containing[-1] if containing else {}
            pages.append({"pdf_page": pdf_page, "chapter_number": int(anchor.get("chapter_index", 0) or 0), "chapter_title": str(anchor.get("title", "")).strip()})
    else:
        for anchor in chapters:
            pages.append({"pdf_page": int(anchor["page_start"]), "chapter_number": int(anchor["chapter_index"]), "chapter_title": str(anchor.get("title", "")).strip()})

    render_root = runtime.workspace_root / "tmp" / "pdfs" / sanitize_name(book_title)
    report_pages: list[dict[str, Any]] = []
    remote_requests = 0
    for page in pages:
        chapter_number = int(page["chapter_number"])
        page_number = int(page["pdf_page"])
        output_prefix = render_root / f"chapter-{chapter_number:02d}-page-{page_number:04d}"
        rendered_image = _render_pdf_page_to_png(pdf_path=pdf_path, page_number=page_number, output_prefix=output_prefix, dpi=dpi)
        try:
            result = run_ocr_for_file(
                source_file=rendered_image,
                runtime=runtime,
                provider_name=resolved_provider,
                model=resolved_model,
                pages="0-0",
                fixture_json=fixture_json,
                allow_remote=remote_enabled,
            )
        except Exception as exc:
            # Some provider endpoints reject otherwise valid PNG payloads. Retry
            # once with a newly encoded JPEG for this same rendered page.
            jpeg_path = rendered_image.with_suffix(".jpg")
            with Image.open(rendered_image) as image:
                image.convert("RGB").save(jpeg_path, format="JPEG", quality=92)
            try:
                result = run_ocr_for_file(
                    source_file=jpeg_path,
                    runtime=runtime,
                    provider_name=resolved_provider,
                    model=resolved_model,
                    pages="0-0",
                    fixture_json=fixture_json,
                    allow_remote=remote_enabled,
                )
            except Exception:
                raise exc
        remote_requests += int(result.get("remote_calls", 0) or 0)
        report_pages.append(
            {
                "chapter_number": chapter_number,
                "chapter_title": str(page["chapter_title"]),
                "pdf_page": page_number,
                "page_start": page_number,
                "page_end": page_number,
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

    report_path = layout["indexes"] / "pdf_ocr_runs" / f"{subject.lower()}-{sanitize_name(book_title)}.json"
    previous = load_json_or_default(report_path, {})
    merged_pages = {
        int(item.get("pdf_page", item.get("page_start", 0)) or 0): item
        for item in previous.get("pages", previous.get("chapters", []))
        if isinstance(item, dict) and int(item.get("pdf_page", item.get("page_start", 0)) or 0)
    }
    merged_pages.update({int(item["pdf_page"]): item for item in report_pages})
    all_pages = [merged_pages[key] for key in sorted(merged_pages)]
    report_payload = {
        "subject": subject,
        "book_title": book_title,
        "pdf_source_id": resolved_source_id,
        "pdf_path": str(pdf_path),
        "anchors_path": str(anchors_path),
        "updated_at": now_iso(),
        "chapters": all_pages,
        "pages": all_pages,
        "summary": {
            "chapter_count": len({item["chapter_number"] for item in all_pages}),
            "processed_count": len(all_pages),
            "remote_requests": remote_requests,
        },
    }
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
        page_start=args.page_start,
        page_end=args.page_end,
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
