from __future__ import annotations

import argparse
from collections.abc import Callable


def add_book_commands(subparsers: argparse._SubParsersAction, *, formatter_class: type[argparse.HelpFormatter] | None = None) -> None:
    opts = {} if formatter_class is None else {"formatter_class": formatter_class}
    book = subparsers.add_parser(
        "book",
        help="paper-book intake and OCR publication",
        description=(
            "Paper-book/PDF workflow: inspect -> map-pages -> OCR -> review -> classify -> "
            "publish -> query/ask. OCR publication is preview-only unless --yes is supplied."
        ),
        epilog="Publication previews expose a stable plan fingerprint; --yes must use the unchanged inputs.",
        **opts,
    )
    commands = book.add_subparsers(dest="book_command", required=True)
    def command(name: str) -> argparse.ArgumentParser: return commands.add_parser(name)
    def path(name: str, *, dry: bool = False) -> argparse.ArgumentParser:
        item = command(name); item.add_argument("--book-root", required=True)
        if dry: item.add_argument("--dry-run", action="store_true")
        item.add_argument("--format", choices=("json", "quiet"), default="json"); return item
    inspect = path("inspect", dry=True); inspect.add_argument("--min-width", type=int); inspect.add_argument("--min-height", type=int); inspect.add_argument("--blur-threshold", type=float); inspect.add_argument("--phash-distance", type=int)
    path("map-pages", dry=True); path("classify"); path("register-photo-source")
    publish_exercises = path("publish-exercises")
    publish_exercises.description = (
        "Preview or publish reviewed photo-book exercises. Workflow: inspect -> map-pages -> OCR -> "
        "review -> classify -> publish -> query/ask. Default is zero-write; --yes requires the "
        "unchanged --plan-fingerprint from preview."
    )
    publish_exercises.add_argument("--yes", action="store_true", help="execute the reviewed plan and refresh all retrieval indexes")
    publish_exercises.add_argument("--plan-fingerprint", help="fingerprint from preview; reject execution after input drift")
    publish_exercises.add_argument("--chapter-id", action="append", default=[], help="limit publication to the listed confirmed chapter IDs")
    chapters = path("generate-chapters"); chapters.add_argument("--context-json", required=True); chapters.add_argument("--plan-json", required=True)
    pdf = command("register-pdf-source"); pdf.add_argument("--subject", required=True); pdf.add_argument("--book-title", required=True); pdf.add_argument("--pdf-path", required=True); pdf.add_argument("--edition", default=""); pdf.add_argument("--format", choices=("json", "quiet"), default="json")
    parallel = command("link-parallel-sources"); parallel.add_argument("--subject", required=True); parallel.add_argument("--book-title", required=True); parallel.add_argument("--image-book-root", required=True); parallel.add_argument("--pdf-source-id", default=""); parallel.add_argument("--context-root", default=""); parallel.add_argument("--format", choices=("json", "quiet"), default="json")
    repair = command("repair-parallel-provenance"); repair.add_argument("--subject", required=True); repair.add_argument("--book-title", required=True); repair.add_argument("--chapter-number", type=int, required=True); repair.add_argument("--format", choices=("json", "quiet"), default="json")
    for name in ("pdf-acceptance-checklist", "pdf-anchor-quality", "parallel-source-guard"):
        item = command(name); item.add_argument("--subject", required=True); item.add_argument("--book-title", action="append", default=[]); item.add_argument("--format", choices=("json", "quiet"), default="json")
    ocr = path("ocr"); ocr.add_argument("--provider"); ocr.add_argument("--model"); ocr.add_argument("--fixture-json"); ocr.add_argument("--allow-remote", action="store_true"); ocr.add_argument("--yes", action="store_true"); ocr.add_argument("--max-retries", type=int, default=2); ocr.add_argument("--require-quality-gate", action="store_true"); ocr.add_argument("--quality-report"); ocr.add_argument("--stage", choices=("basic", "advanced")); ocr.add_argument("--chapter-id", action="append", default=[]); ocr.add_argument("--dry-run", action="store_true")
    ocr_publish = path("ocr-publish")
    ocr_publish.description = (
        "Preview or publish reviewed image OCR. Workflow: inspect -> map-pages -> OCR -> review -> "
        "classify -> publish -> query/ask. Default is zero-write; --yes executes the plan."
    )
    ocr_publish.add_argument("--chapter-id", action="append", default=[])
    ocr_publish.add_argument("--require-complete", action="store_true")
    ocr_publish.add_argument("--yes", action="store_true", help="execute the plan and refresh all retrieval indexes")
    ocr_publish.add_argument("--plan-fingerprint", help="fingerprint from preview; reject execution after input drift")
    ocr_pdf = command("ocr-pdf-source"); ocr_pdf.add_argument("--subject", required=True); ocr_pdf.add_argument("--book-title", required=True); ocr_pdf.add_argument("--pdf-source-id", default=""); ocr_pdf.add_argument("--chapter-number", action="append", type=int, default=[]); ocr_pdf.add_argument("--page-start", type=int); ocr_pdf.add_argument("--page-end", type=int); ocr_pdf.add_argument("--provider"); ocr_pdf.add_argument("--model"); ocr_pdf.add_argument("--fixture-json"); ocr_pdf.add_argument("--allow-remote", action="store_true"); ocr_pdf.add_argument("--yes", action="store_true"); ocr_pdf.add_argument("--dpi", type=int, default=200); ocr_pdf.add_argument("--format", choices=("json", "quiet"), default="json")
    for name, extra in (("pdf-ocr-review-status", "--report-path"), ("pdf-ocr-review-artifact", "--bridge-report-path")):
        item = command(name); item.add_argument("--subject", required=True); item.add_argument("--book-title", required=True); item.add_argument("--pdf-source-id", default=""); item.add_argument(extra, default=""); item.add_argument("--format", choices=("json", "quiet"), default="json")
    pdf_publish = command("pdf-ocr-publish")
    pdf_publish.description = (
        "Preview or publish reviewed PDF OCR. Workflow: inspect -> map-pages -> OCR -> review -> "
        "classify -> publish -> query/ask. Default is zero-write; --yes executes the plan."
    )
    pdf_publish.add_argument("--subject", required=True)
    pdf_publish.add_argument("--book-title", required=True)
    pdf_publish.add_argument("--pdf-source-id", required=True)
    pdf_publish.add_argument("--report-path", required=True)
    pdf_publish.add_argument("--review-artifact-path", required=True)
    pdf_publish.add_argument("--chapter-number", type=int)
    pdf_publish.add_argument("--require-complete", action="store_true", help="block the whole selected scope when any page is not publishable")
    pdf_publish.add_argument("--yes", action="store_true", help="execute the plan and refresh all retrieval indexes")
    pdf_publish.add_argument("--plan-fingerprint", help="fingerprint from preview; reject execution after input drift")
    pdf_publish.add_argument("--format", choices=("json", "quiet"), default="json")
    page_review = command("pdf-ocr-page-review"); page_review.add_argument("--pdf-source-id", required=True); page_review.add_argument("--pdf-page", required=True, type=int); page_review.add_argument("--printed-page", type=int); page_review.add_argument("--page-header-confirmed", action="store_true"); page_review.add_argument("--review-status", choices=("pending", "accepted", "rejected"), required=True); page_review.add_argument("--request-key", default=""); page_review.add_argument("--source-image-sha256", default=""); page_review.add_argument("--note", default=""); page_review.add_argument("--format", choices=("json", "quiet"), default="json")
    mapping_candidates = command("pdf-ocr-map-candidates"); mapping_candidates.add_argument("--subject", required=True); mapping_candidates.add_argument("--book-title", required=True); mapping_candidates.add_argument("--pdf-source-id", required=True); mapping_candidates.add_argument("--report-path", default=""); mapping_candidates.add_argument("--format", choices=("json", "quiet"), default="json")
    mapping_interval = command("pdf-ocr-approve-mapping-interval"); mapping_interval.add_argument("--pdf-source-id", required=True); mapping_interval.add_argument("--pdf-start", type=int, required=True); mapping_interval.add_argument("--pdf-end", type=int, required=True); mapping_interval.add_argument("--printed-start", type=int, required=True); mapping_interval.add_argument("--printed-end", type=int, required=True); mapping_interval.add_argument("--visual-review-note", required=True); mapping_interval.add_argument("--yes", action="store_true"); mapping_interval.add_argument("--format", choices=("json", "quiet"), default="json")
    mapping_interval.add_argument("--plan-fingerprint")
    outline = command("pdf-ocr-apply-outline"); outline.add_argument("--subject", required=True); outline.add_argument("--book-title", required=True); outline.add_argument("--pdf-source-id", required=True); outline.add_argument("--outline-json", required=True); outline.add_argument("--yes", action="store_true"); outline.add_argument("--format", choices=("json", "quiet"), default="json")
    outline.add_argument("--plan-fingerprint")
    coverage = command("exercise-coverage"); coverage.add_argument("--subject", required=True); coverage.add_argument("--book-title", required=True)
    coverage_source = coverage.add_mutually_exclusive_group(); coverage_source.add_argument("--source-id", default=""); coverage_source.add_argument("--pdf-source-id", default="", help="compatibility alias for --source-id")
    coverage.add_argument("--chapter-number", type=int); coverage.add_argument("--verify-query-ask", action="store_true"); coverage.add_argument("--expected-relations", type=int); coverage.add_argument("--require-complete", action="store_true"); coverage.add_argument("--format", choices=("json", "quiet"), default="json")
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
    if c == "register-photo-source":
        return run_script("register_photo_book_source.py", *_format(args, "--book-root", args.book_root))
    if c == "publish-exercises":
        forwarded = _format(args, "--book-root", args.book_root)
        for chapter_id in args.chapter_id:
            forwarded.extend(["--chapter-id", chapter_id])
        if args.yes: forwarded.append("--yes")
        if args.plan_fingerprint: forwarded.extend(["--plan-fingerprint", args.plan_fingerprint])
        return run_script("publish_book_exercises.py", *forwarded)
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
        if args.stage: forwarded.extend(["--stage", args.stage])
        for chapter_id in args.chapter_id: forwarded.extend(["--chapter-id", chapter_id])
        if args.dry_run: forwarded.append("--dry-run")
        return run_script("ocr_book_pages.py", *forwarded)
    if c == "ocr-publish":
        forwarded = _format(args, "--book-root", args.book_root)
        for chapter_id in args.chapter_id:
            forwarded.extend(["--chapter-id", chapter_id])
        if args.require_complete: forwarded.append("--require-complete")
        if args.yes: forwarded.append("--yes")
        if args.plan_fingerprint: forwarded.extend(["--plan-fingerprint", args.plan_fingerprint])
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
        forwarded = _format(args, "--subject", args.subject, "--book-title", args.book_title, "--pdf-source-id", args.pdf_source_id, "--report-path", args.report_path, "--review-artifact-path", args.review_artifact_path)
        if args.chapter_number is not None: forwarded.extend(["--chapter-number", str(args.chapter_number)])
        if args.require_complete: forwarded.append("--require-complete")
        if args.yes: forwarded.append("--yes")
        if args.plan_fingerprint: forwarded.extend(["--plan-fingerprint", args.plan_fingerprint])
        return run_script("publish_pdf_ocr_evidence.py", *forwarded)
    if c == "pdf-ocr-page-review":
        forwarded = _format(args, "--pdf-source-id", args.pdf_source_id, "--pdf-page", str(args.pdf_page), "--review-status", args.review_status, "--note", args.note)
        if args.printed_page is not None: forwarded.extend(["--printed-page", str(args.printed_page)])
        if args.page_header_confirmed: forwarded.append("--page-header-confirmed")
        if args.request_key: forwarded.extend(["--request-key", args.request_key])
        if args.source_image_sha256: forwarded.extend(["--source-image-sha256", args.source_image_sha256])
        return run_script("review_pdf_ocr_page.py", *forwarded)
    if c == "pdf-ocr-map-candidates":
        forwarded = _format(args, "--subject", args.subject, "--book-title", args.book_title, "--pdf-source-id", args.pdf_source_id)
        if args.report_path: forwarded.extend(["--report-path", args.report_path])
        return run_script("build_pdf_page_mapping_candidates.py", *forwarded)
    if c == "pdf-ocr-approve-mapping-interval":
        forwarded = _format(args, "--pdf-source-id", args.pdf_source_id, "--pdf-start", str(args.pdf_start), "--pdf-end", str(args.pdf_end), "--printed-start", str(args.printed_start), "--printed-end", str(args.printed_end), "--visual-review-note", args.visual_review_note)
        if args.plan_fingerprint: forwarded.extend(["--plan-fingerprint", args.plan_fingerprint])
        if args.yes: forwarded.append("--yes")
        return run_script("approve_pdf_page_mapping_interval.py", *forwarded)
    if c == "pdf-ocr-apply-outline":
        forwarded = _format(args, "--subject", args.subject, "--book-title", args.book_title, "--pdf-source-id", args.pdf_source_id, "--outline-json", args.outline_json)
        if args.plan_fingerprint: forwarded.extend(["--plan-fingerprint", args.plan_fingerprint])
        if args.yes: forwarded.append("--yes")
        return run_script("apply_pdf_ocr_outline.py", *forwarded)
    if c == "exercise-coverage":
        forwarded = _format(args, "--subject", args.subject, "--book-title", args.book_title)
        if args.source_id: forwarded.extend(["--source-id", args.source_id])
        elif args.pdf_source_id: forwarded.extend(["--pdf-source-id", args.pdf_source_id])
        if args.chapter_number is not None: forwarded.extend(["--chapter-number", str(args.chapter_number)])
        if args.verify_query_ask: forwarded.append("--verify-query-ask")
        if args.expected_relations is not None: forwarded.extend(["--expected-relations", str(args.expected_relations)])
        if args.require_complete: forwarded.append("--require-complete")
        return run_script("build_exercise_coverage_report.py", *forwarded)
    if c == "ocr-review":
        if args.ocr_review_command == "queue":
            forwarded = _format(args, "queue", "--book-root", args.book_root)
            if args.review_type: forwarded.extend(["--review-type", args.review_type])
        else: forwarded = _format(args, "apply", "--request-key", args.request_key, "--block-id", args.block_id, "--review-status", args.review_status, "--corrected-text", args.corrected_text, "--note", args.note)
        return run_script("ocr\\review.py", *forwarded)
    return None
