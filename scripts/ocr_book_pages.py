#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from common import allocate_run_id, load_json_or_default, now_iso, save_json
from config import load_runtime_config
from ocr.cache import OCRRequestConfig, load_cached_normalized, request_key_for_file
from ocr_document import run_ocr_for_file


def _metadata_paths(book_root: Path, metadata_dirname: str) -> dict[str, Path]:
    metadata_root = book_root / metadata_dirname
    return {
        "root": metadata_root,
        "page_assets": metadata_root / "page_assets.json",
        "page_ocr_status": metadata_root / "page_ocr_status.json",
    }


def _month_key() -> str:
    return datetime.now().strftime("%Y-%m")


def _load_monthly_usage(index_path: Path) -> dict[str, Any]:
    return load_json_or_default(index_path, {"months": {}})


def _status_compare_payload(item: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in item.items() if key not in {"updated_at"}}


def _load_quality_gate_report(report_path: Path) -> dict[str, Any]:
    return load_json_or_default(report_path, {})


def _build_quality_gate_report(page_assets_payload: dict[str, Any]) -> dict[str, Any]:
    """Derive the gate from the inspected assets; no invisible prerequisite file."""
    items = list(page_assets_payload.get("items", []) or [])
    eligible = [item for item in items if item.get("quality_status") in {"accepted", "needs_review"}]
    return {
        "book_id": page_assets_payload.get("book_id", ""),
        "checked_count": len(items),
        "eligible_count": len(eligible),
        "blocked_count": len(items) - len(eligible),
        "passed": bool(items) and len(eligible) == len(items),
    }


def _allowed_remote(provider_name: str, runtime, *, allow_remote: bool, yes: bool) -> bool:
    if provider_name != "mistral":
        return True
    return runtime.ocr_allow_remote or (allow_remote and yes)


def _request_key_for_page(page: dict[str, Any], runtime, provider_name: str, model: str) -> str:
    config = OCRRequestConfig(
        provider=provider_name,
        model=model,
        pages="0-0",
        include_blocks=runtime.ocr_include_blocks,
        confidence_granularity=runtime.ocr_confidence_granularity,
        table_format=runtime.ocr_table_format,
        extract_header=runtime.ocr_extract_header,
        extract_footer=runtime.ocr_extract_footer,
        normalizer_version="v1",
    )
    return request_key_for_file(Path(page["source_image_path"]), config)


def _build_status_item(
    *,
    page: dict[str, Any],
    request_key: str,
    provider_name: str,
    model: str,
    existing: dict[str, Any] | None,
) -> dict[str, Any]:
    existing = existing or {}
    if existing.get("request_key") != request_key:
        attempts = 0
        previous_errors: list[str] = []
    else:
        attempts = int(existing.get("attempt_count", 0) or 0)
        previous_errors = list(existing.get("error_history", []))
    return {
        "page_id": page["page_id"],
        "book_id": page["book_id"],
        "scan_index": page.get("scan_index"),
        "printed_page": page.get("printed_page"),
        "printed_page_label": page.get("printed_page_label"),
        "current_version_id": page.get("current_version_id"),
        "source_image_path": page.get("source_image_path"),
        "source_image_sha256": page.get("source_image_sha256"),
        "quality_status": page.get("quality_status"),
        "request_key": request_key,
        "provider": provider_name,
        "model": model,
        "exact_model": existing.get("exact_model", ""),
        "status": existing.get("status", "pending"),
        "attempt_count": attempts,
        "error_history": previous_errors,
        "last_error": existing.get("last_error"),
        "normalized_path": existing.get("normalized_path", ""),
        "raw_path": existing.get("raw_path", ""),
        "last_run_id": existing.get("last_run_id", ""),
        "completed_at": existing.get("completed_at"),
        "updated_at": existing.get("updated_at") or now_iso(),
    }


def _ensure_run_id(current_run_id: str | None) -> str:
    return current_run_id or allocate_run_id()


def run_book_ocr(
    *,
    book_root: Path,
    provider_name: str | None,
    model: str | None,
    fixture_json: Path | None,
    allow_remote: bool,
    yes: bool,
    max_retries: int,
    require_quality_gate: bool = False,
    quality_report: Path | None = None,
    format_name: str = "json",
) -> dict[str, Any]:
    runtime = load_runtime_config()
    paths = _metadata_paths(book_root, runtime.paper_book_metadata_dir)
    page_assets_payload = load_json_or_default(paths["page_assets"], {})
    if not page_assets_payload:
        raise SystemExit("page_assets.json is missing; run book inspect first")
    if require_quality_gate:
        report_path = quality_report or (paths["root"] / "quality_gate.json")
        report_payload = _load_quality_gate_report(report_path) or _build_quality_gate_report(page_assets_payload)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        save_json(report_path, report_payload, ignored_compare_keys=())
        if not bool(report_payload.get("passed")):
            raise SystemExit(f"quality gate failed: {report_path}")

    resolved_provider = provider_name or runtime.ocr_provider
    resolved_model = model or runtime.ocr_model
    remote_enabled = _allowed_remote(resolved_provider, runtime, allow_remote=allow_remote, yes=yes)
    if not remote_enabled:
        if resolved_provider == "mistral":
            raise SystemExit("mistral OCR is disabled; enable KAOYAN_OCR_ALLOW_REMOTE=true or pass --allow-remote --yes")

    items = list(page_assets_payload.get("items", []))
    items.sort(key=lambda item: (int(item.get("scan_index", 0) or 0), str(item.get("page_id", ""))))

    status_payload = load_json_or_default(paths["page_ocr_status"], {})
    existing_status = {
        str(item.get("page_id")): item
        for item in status_payload.get("items", [])
        if isinstance(item, dict) and item.get("page_id")
    }

    usage_path = runtime.ocr_cache_root / "indexes" / "monthly_usage.json"
    usage_payload = _load_monthly_usage(usage_path)
    month_key = _month_key()
    month_usage = usage_payload.setdefault("months", {}).setdefault(month_key, {"used_pages": 0, "updated_at": now_iso()})
    used_pages = int(month_usage.get("used_pages", 0) or 0)

    run_id: str | None = None
    summary = {
        "processed_count": 0,
        "cached_count": 0,
        "failed_count": 0,
        "retry_exhausted_count": 0,
        "budget_blocked_count": 0,
        "skipped_quality_count": 0,
        "completed_count": 0,
    }
    remote_requests = 0
    status_items: list[dict[str, Any]] = []

    def checkpoint() -> None:
        paths["root"].mkdir(parents=True, exist_ok=True)
        save_json(paths["page_ocr_status"], {
            "book_id": page_assets_payload.get("book_id"), "source_root": page_assets_payload.get("source_root"),
            "provider": resolved_provider, "model": resolved_model, "created_at": status_payload.get("created_at") or now_iso(),
            "updated_at": now_iso(), "items": status_items,
            "summary": {"completed_count": sum(1 for item in status_items if item["status"] == "completed"),
                        "failed_count": sum(1 for item in status_items if item["status"] == "failed"),
                        "retry_exhausted_count": sum(1 for item in status_items if item["status"] == "retry_exhausted"),
                        "budget_blocked_count": sum(1 for item in status_items if item["status"] == "budget_blocked"),
                        "skipped_quality_count": sum(1 for item in status_items if item["status"] == "skipped_quality"),
                        "pending_count": sum(1 for item in status_items if item["status"] == "pending")},
        })

    for page in items:
        request_key = _request_key_for_page(page, runtime, resolved_provider, resolved_model)
        existing = existing_status.get(str(page["page_id"]))
        status_item = _build_status_item(
            page=page,
            request_key=request_key,
            provider_name=resolved_provider,
            model=resolved_model,
            existing=existing,
        )

        if page.get("quality_status") not in {"accepted", "needs_review"}:
            status_item["status"] = "skipped_quality"
            status_item["last_error"] = None
            status_item["updated_at"] = now_iso()
            summary["skipped_quality_count"] += 1
            status_items.append(status_item)
            checkpoint()
            continue

        normalized_path = runtime.ocr_cache_root / "normalized" / f"{request_key}.json"
        raw_path = runtime.ocr_cache_root / "raw" / f"{request_key}.json"
        if load_cached_normalized(runtime.ocr_cache_root, request_key) is not None:
            status_item["status"] = "completed"
            status_item["normalized_path"] = str(normalized_path)
            status_item["raw_path"] = str(raw_path)
            status_item["exact_model"] = existing.get("exact_model", resolved_model) if existing else resolved_model
            status_item["last_error"] = None
            status_item["completed_at"] = existing.get("completed_at") if existing else now_iso()
            if existing:
                status_item["last_run_id"] = existing.get("last_run_id", "")
            status_item["updated_at"] = existing.get("updated_at") if existing and _status_compare_payload(status_item) == _status_compare_payload(existing) else now_iso()
            summary["cached_count"] += 1
            status_items.append(status_item)
            checkpoint()
            continue

        if runtime.ocr_monthly_page_budget > 0 and used_pages >= runtime.ocr_monthly_page_budget:
            status_item["status"] = "budget_blocked"
            status_item["updated_at"] = now_iso()
            summary["budget_blocked_count"] += 1
            status_items.append(status_item)
            checkpoint()
            continue

        if status_item["attempt_count"] >= max_retries:
            status_item["status"] = "retry_exhausted"
            status_item["updated_at"] = now_iso()
            summary["retry_exhausted_count"] += 1
            status_items.append(status_item)
            checkpoint()
            continue

        try:
            run_id = _ensure_run_id(run_id)
            result = run_ocr_for_file(
                source_file=Path(page["source_image_path"]),
                runtime=runtime,
                provider_name=resolved_provider,
                model=resolved_model,
                pages="0-0",
                fixture_json=fixture_json,
                allow_remote=remote_enabled,
            )
        except Exception as exc:
            status_item["attempt_count"] = int(status_item["attempt_count"]) + 1
            status_item["status"] = "failed"
            status_item["last_error"] = str(exc)
            history = list(status_item.get("error_history", []))
            history.append(str(exc))
            status_item["error_history"] = history[-max_retries:]
            status_item["updated_at"] = now_iso()
            summary["failed_count"] += 1
            status_items.append(status_item)
            checkpoint()
            continue

        used_pages += 1
        month_usage["used_pages"] = used_pages
        month_usage["updated_at"] = now_iso()
        remote_requests += int(result.get("remote_calls", 0) or 0)
        status_item["last_run_id"] = run_id
        status_item["status"] = "completed"
        status_item["normalized_path"] = str(result["normalized_path"])
        status_item["raw_path"] = str(result["raw_path"])
        status_item["exact_model"] = result["exact_model"]
        status_item["last_error"] = None
        status_item["completed_at"] = now_iso()
        status_item["updated_at"] = now_iso()
        summary["processed_count"] += 1
        status_items.append(status_item)
        checkpoint()

    summary["completed_count"] = sum(1 for item in status_items if item["status"] == "completed")
    stable_summary = {
        "completed_count": summary["completed_count"],
        "failed_count": sum(1 for item in status_items if item["status"] == "failed"),
        "retry_exhausted_count": sum(1 for item in status_items if item["status"] == "retry_exhausted"),
        "budget_blocked_count": sum(1 for item in status_items if item["status"] == "budget_blocked"),
        "skipped_quality_count": sum(1 for item in status_items if item["status"] == "skipped_quality"),
        "pending_count": sum(1 for item in status_items if item["status"] == "pending"),
    }
    page_ocr_status_payload = {
        "book_id": page_assets_payload.get("book_id"),
        "source_root": page_assets_payload.get("source_root"),
        "provider": resolved_provider,
        "model": resolved_model,
        "created_at": status_payload.get("created_at") or now_iso(),
        "updated_at": now_iso(),
        "items": status_items,
        "summary": stable_summary,
    }
    run_payload = {
        "run_id": run_id or "",
        "book_id": page_assets_payload.get("book_id"),
        "book_root": str(book_root),
        "provider": resolved_provider,
        "model": resolved_model,
        "created_at": now_iso(),
        "remote_requests": remote_requests,
        "summary": summary,
        "published_evidence": False,
    }

    writes = {"page_ocr_status": False, "monthly_usage": False, "run_manifest": False}
    if True:
        paths["root"].mkdir(parents=True, exist_ok=True)
        writes["page_ocr_status"] = save_json(paths["page_ocr_status"], page_ocr_status_payload)
        if run_id:
            writes["monthly_usage"] = save_json(usage_path, usage_payload, ignored_compare_keys=())
            writes["run_manifest"] = save_json(runtime.ocr_cache_root / "runs" / f"{run_id}.json", run_payload, ignored_compare_keys=())

    return {
        "book_id": page_assets_payload.get("book_id"),
        "book_root": str(book_root),
        "provider": resolved_provider,
        "model": resolved_model,
        "run_id": run_id or "",
        "remote_requests": remote_requests,
        "summary": summary,
        "page_ocr_status_path": str(paths["page_ocr_status"]),
        "writes": writes,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--book-root", required=True)
    parser.add_argument("--provider")
    parser.add_argument("--model")
    parser.add_argument("--fixture-json")
    parser.add_argument("--allow-remote", action="store_true")
    parser.add_argument("--yes", action="store_true")
    parser.add_argument("--max-retries", type=int, default=2)
    parser.add_argument("--require-quality-gate", action="store_true")
    parser.add_argument("--quality-report")
    parser.add_argument("--format", choices=("json", "quiet"), default="json")
    return parser


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    args = build_parser().parse_args()
    payload = run_book_ocr(
        book_root=Path(args.book_root),
        provider_name=args.provider,
        model=args.model,
        fixture_json=Path(args.fixture_json) if args.fixture_json else None,
        allow_remote=args.allow_remote,
        yes=args.yes,
        max_retries=max(1, int(args.max_retries)),
        require_quality_gate=args.require_quality_gate,
        quality_report=Path(args.quality_report) if args.quality_report else None,
        format_name=args.format,
    )
    if args.format == "json":
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
