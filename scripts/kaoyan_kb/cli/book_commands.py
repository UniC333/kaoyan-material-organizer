from __future__ import annotations

import argparse
from collections.abc import Callable


def add_book_commands(subparsers: argparse._SubParsersAction, *, formatter_class: type[argparse.HelpFormatter] | None = None) -> None:
    opts = {} if formatter_class is None else {"formatter_class": formatter_class}
    book = subparsers.add_parser("book", help="paper-book intake, OCR, PDF source registration, and classify handoff", description="Paper-book intake, OCR, PDF source registration, and classify handoff.", **opts)
    commands = book.add_subparsers(dest="book_command", required=True)
    def command(name: str) -> argparse.ArgumentParser: return commands.add_parser(name)
    def path(name: str, *, dry: bool = False) -> argparse.ArgumentParser:
        item = command(name); item.add_argument("--book-root", required=True)
        if dry: item.add_argument("--dry-run", action="store_true")
        item.add_argument("--format", choices=("json", "quiet"), default="json"); return item
    inspect = path("inspect", dry=True); inspect.add_argument("--min-width", type=int); inspect.add_argument("--min-height", type=int); inspect.add_argument("--blur-threshold", type=float); inspect.add_argument("--phash-distance", type=int)
    path("map-pages", dry=True); path("classify")
    chapters = path("generate-chapters"); chapters.add_argument("--context-json", required=True); chapters.add_argument("--plan-json", required=True)
    pdf = command("register-pdf-source"); pdf.add_argument("--subject", required=True); pdf.add_argument("--book-title", required=True); pdf.add_argument("--pdf-path", required=True); pdf.add_argument("--edition", default=""); pdf.add_argument("--format", choices=("json", "quiet"), default="json")
    parallel = command("link-parallel-sources"); parallel.add_argument("--subject", required=True); parallel.add_argument("--book-title", required=True); parallel.add_argument("--image-book-root", required=True); parallel.add_argument("--pdf-source-id", default=""); parallel.add_argument("--context-root", default=""); parallel.add_argument("--format", choices=("json", "quiet"), default="json")
    repair = command("repair-parallel-provenance"); repair.add_argument("--subject", required=True); repair.add_argument("--book-title", required=True); repair.add_argument("--chapter-number", type=int, required=True); repair.add_argument("--format", choices=("json", "quiet"), default="json")
    for name in ("pdf-acceptance-checklist", "pdf-anchor-quality", "parallel-source-guard"):
        item = command(name); item.add_argument("--subject", required=True); item.add_argument("--book-title", action="append", default=[]); item.add_argument("--format", choices=("json", "quiet"), default="json")
    ocr = path("ocr"); ocr.add_argument("--provider"); ocr.add_argument("--model"); ocr.add_argument("--fixture-json"); ocr.add_argument("--allow-remote", action="store_true"); ocr.add_argument("--yes", action="store_true"); ocr.add_argument("--max-retries", type=int, default=2); ocr.add_argument("--require-quality-gate", action="store_true"); ocr.add_argument("--quality-report")
    ocr_publish = path("ocr-publish")
    ocr_publish.add_argument("--require-complete", action="store_true")
    ocr_publish.add_argument("--no-refresh-indexes", action="store_true")
    ocr_pdf = command("ocr-pdf-source"); ocr_pdf.add_argument("--subject", required=True); ocr_pdf.add_argument("--book-title", required=True); ocr_pdf.add_argument("--pdf-source-id", default=""); ocr_pdf.add_argument("--chapter-number", action="append", type=int, default=[]); ocr_pdf.add_argument("--page-start", type=int); ocr_pdf.add_argument("--page-end", type=int); ocr_pdf.add_argument("--provider"); ocr_pdf.add_argument("--model"); ocr_pdf.add_argument("--fixture-json"); ocr_pdf.add_argument("--allow-remote", action="store_true"); ocr_pdf.add_argument("--yes", action="store_true"); ocr_pdf.add_argument("--dpi", type=int, default=200); ocr_pdf.add_argument("--format", choices=("json", "quiet"), default="json")
    for name, extra in (("pdf-ocr-review-status", "--report-path"), ("pdf-ocr-review-artifact", "--bridge-report-path")):
        item = command(name); item.add_argument("--subject", required=True); item.add_argument("--book-title", required=True); item.add_argument("--pdf-source-id", default=""); item.add_argument(extra, default=""); item.add_argument("--format", choices=("json", "quiet"), default="json")
    pdf_publish = command("pdf-ocr-publish"); pdf_publish.add_argument("--subject", required=True); pdf_publish.add_argument("--book-title", required=True); pdf_publish.add_argument("--pdf-source-id", required=True); pdf_publish.add_argument("--report-path", required=True); pdf_publish.add_argument("--review-artifact-path", required=True); pdf_publish.add_argument("--format", choices=("json", "quiet"), default="json")
    page_review = command("pdf-ocr-page-review"); page_review.add_argument("--pdf-source-id", required=True); page_review.add_argument("--pdf-page", required=True, type=int); page_review.add_argument("--printed-page", type=int); page_review.add_argument("--page-header-confirmed", action="store_true"); page_review.add_argument("--review-status", choices=("pending", "accepted", "rejected"), required=True); page_review.add_argument("--note", default=""); page_review.add_argument("--format", choices=("json", "quiet"), default="json")
    coverage = command("exercise-coverage"); coverage.add_argument("--subject", required=True); coverage.add_argument("--book-title", required=True); coverage.add_argument("--pdf-source-id", default=""); coverage.add_argument("--chapter-number", type=int); coverage.add_argument("--format", choices=("json", "quiet"), default="json")
    review = command("ocr-review").add_subparsers(dest="ocr_review_command", required=True)
    queue = review.add_parser("queue"); queue.add_argument("--book-root", required=True); queue.add_argument("--review-type", choices=("table", "equation", "low-confidence")); queue.add_argument("--format", choices=("json", "quiet"), default="json")
    apply = review.add_parser("apply"); apply.add_argument("--request-key", required=True); apply.add_argument("--block-id", required=True); apply.add_argument("--review-status", choices=("pending", "accepted", "rejected", "ignored"), required=True); apply.add_argument("--corrected-text", default=""); apply.add_argument("--note", default=""); apply.add_argument("--format", choices=("json", "quiet"), default="json")


def _format(args: argparse.Namespace, *items: str) -> list[str]: return [*items, "--format", args.format]


def dispatch_book(args: argparse.Namespace, run_script: Callable[..., str]) -> str | None:
    if args.command != "book": return None
    c = args.book_command
    if c == "inspect":
        forwarded = _format(args, "--book-root", args.book_root)
        if args.dry_run: forwarded.append("--dry-run")
        for name in ("min_width", "min_height", "blur_threshold", "phash_distance"):
            value = getattr(args, name)
            if value is not None: forwarded.extend([f"--{name.replace('_', '-')}", str(value)])
        return run_script("ingest_paper_book.py", *forwarded)
    if c in {"map-pages", "classify"}:
        forwarded = _format(args, "--book-root", args.book_root)
        if c == "map-pages" and args.dry_run: forwarded.append("--dry-run")
        return run_script("map_book_pages.py" if c == "map-pages" else "classify_book_pages.py", *forwarded)
    if c == "generate-chapters": return run_script("generate_book_chapters.py", *_format(args, "--book-root", args.book_root, "--context-json", args.context_json, "--plan-json", args.plan_json))
    if c == "register-pdf-source": return run_script("register_pdf_book_source.py", *_format(args, "--subject", args.subject, "--book-title", args.book_title, "--pdf-path", args.pdf_path, "--edition", args.edition))
    if c == "link-parallel-sources":
        forwarded = _format(args, "--subject", args.subject, "--book-title", args.book_title, "--image-book-root", args.image_book_root)
        if args.pdf_source_id: forwarded.extend(["--pdf-source-id", args.pdf_source_id])
        if args.context_root: forwarded.extend(["--context-root", args.context_root])
        return run_script("link_parallel_book_sources.py", *forwarded)
    if c == "repair-parallel-provenance": return run_script("repair_parallel_book_provenance.py", *_format(args, "--subject", args.subject, "--book-title", args.book_title, "--chapter-number", str(args.chapter_number)))
    reports = {"pdf-acceptance-checklist": "build_pdf_acceptance_checklist.py", "pdf-anchor-quality": "build_pdf_anchor_quality_report.py", "parallel-source-guard": "build_parallel_source_guard_report.py"}
    if c in reports:
        forwarded = _format(args, "--subject", args.subject)
        for title in args.book_title: forwarded.extend(["--book-title", title])
        return run_script(reports[c], *forwarded)
    if c == "ocr":
        forwarded = _format(args, "--book-root", args.book_root, "--max-retries", str(args.max_retries))
        for name in ("provider", "model", "fixture_json", "quality_report"):
            value = getattr(args, name)
            if value: forwarded.extend([f"--{name.replace('_', '-')}", value])
        for name in ("allow_remote", "yes", "require_quality_gate"):
            if getattr(args, name): forwarded.append(f"--{name.replace('_', '-')}")
        return run_script("ocr_book_pages.py", *forwarded)
    if c == "ocr-publish":
        forwarded = _format(args, "--book-root", args.book_root)
        if args.require_complete: forwarded.append("--require-complete")
        if args.no_refresh_indexes: forwarded.append("--no-refresh-indexes")
        return run_script("publish_book_ocr_evidence.py", *forwarded)
    if c == "ocr-pdf-source":
        forwarded = _format(args, "--subject", args.subject, "--book-title", args.book_title, "--pdf-source-id", args.pdf_source_id, "--dpi", str(args.dpi))
        for item in args.chapter_number: forwarded.extend(["--chapter-number", str(item)])
        for name in ("page_start", "page_end"):
            value = getattr(args, name)
            if value is not None: forwarded.extend([f"--{name.replace('_', '-')}", str(value)])
        for name in ("provider", "model", "fixture_json"):
            if getattr(args, name): forwarded.extend([f"--{name.replace('_', '-')}", getattr(args, name)])
        for name in ("allow_remote", "yes"):
            if getattr(args, name): forwarded.append(f"--{name.replace('_', '-')}")
        return run_script("ocr_pdf_book_source.py", *forwarded)
    if c in {"pdf-ocr-review-status", "pdf-ocr-review-artifact"}:
        option = "report_path" if c.endswith("status") else "bridge_report_path"; forwarded = _format(args, "--subject", args.subject, "--book-title", args.book_title, "--pdf-source-id", args.pdf_source_id)
        if getattr(args, option): forwarded.extend([f"--{option.replace('_', '-')}", getattr(args, option)])
        return run_script("build_pdf_ocr_review_status.py" if c.endswith("status") else "build_pdf_ocr_review_artifact.py", *forwarded)
    if c == "pdf-ocr-publish":
        return run_script("publish_pdf_ocr_evidence.py", *_format(args, "--subject", args.subject, "--book-title", args.book_title, "--pdf-source-id", args.pdf_source_id, "--report-path", args.report_path, "--review-artifact-path", args.review_artifact_path))
    if c == "pdf-ocr-page-review":
        forwarded = _format(args, "--pdf-source-id", args.pdf_source_id, "--pdf-page", str(args.pdf_page), "--review-status", args.review_status, "--note", args.note)
        if args.printed_page is not None: forwarded.extend(["--printed-page", str(args.printed_page)])
        if args.page_header_confirmed: forwarded.append("--page-header-confirmed")
        return run_script("review_pdf_ocr_page.py", *forwarded)
    if c == "exercise-coverage":
        forwarded = _format(args, "--subject", args.subject, "--book-title", args.book_title, "--pdf-source-id", args.pdf_source_id)
        if args.chapter_number is not None: forwarded.extend(["--chapter-number", str(args.chapter_number)])
        return run_script("build_exercise_coverage_report.py", *forwarded)
    if c == "ocr-review":
        if args.ocr_review_command == "queue":
            forwarded = _format(args, "queue", "--book-root", args.book_root)
            if args.review_type: forwarded.extend(["--review-type", args.review_type])
        else: forwarded = _format(args, "apply", "--request-key", args.request_key, "--block-id", args.block_id, "--review-status", args.review_status, "--corrected-text", args.corrected_text, "--note", args.note)
        return run_script("ocr\\review.py", *forwarded)
    return None
