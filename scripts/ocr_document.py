#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from common import ensure_kb_layout, now_iso, save_json, sha256_for_file
from config import load_runtime_config
from ocr.cache import OCRRequestConfig, load_cached_normalized, request_key_for_file, save_cached_payloads
from ocr.normalize import NORMALIZER_VERSION, normalize_ocr_payload
from ocr.providers.base import OCRProviderRequest
from ocr.providers.fixture import FixtureOCRProvider
from ocr.providers.mistral import MistralOCRProvider


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--file", required=True)
    parser.add_argument("--pages", default="0-0")
    parser.add_argument("--provider")
    parser.add_argument("--model")
    parser.add_argument("--fixture-json")
    parser.add_argument("--format", choices=("json", "quiet"), default="json")
    return parser.parse_args()


def provider_for(name: str, *, allow_remote: bool, fixture_json: Path | None) -> Any:
    if name == "fixture":
        return FixtureOCRProvider(fixture_json=fixture_json)
    if name == "mistral":
        return MistralOCRProvider(allow_remote=allow_remote)
    raise SystemExit(f"[ERROR] unsupported OCR provider: {name}")


def normalize_payload(raw_payload: dict[str, Any], request_key: str, source_file: Path) -> dict[str, Any]:
    return normalize_ocr_payload(raw_payload, request_key=request_key, source_file=source_file)


def run_ocr_for_file(
    *,
    source_file: Path,
    runtime=None,
    provider_name: str | None = None,
    model: str | None = None,
    pages: str = "0-0",
    fixture_json: Path | None = None,
    allow_remote: bool | None = None,
) -> dict[str, Any]:
    runtime = runtime or load_runtime_config()
    ensure_kb_layout()
    resolved_provider = provider_name or runtime.ocr_provider
    resolved_model = model or runtime.ocr_model
    resolved_allow_remote = runtime.ocr_allow_remote if allow_remote is None else allow_remote
    request_config = OCRRequestConfig(
        provider=resolved_provider,
        model=resolved_model,
        pages=pages,
        include_blocks=runtime.ocr_include_blocks,
        confidence_granularity=runtime.ocr_confidence_granularity,
        table_format=runtime.ocr_table_format,
        extract_header=runtime.ocr_extract_header,
        extract_footer=runtime.ocr_extract_footer,
        normalizer_version=NORMALIZER_VERSION,
    )
    request_key = request_key_for_file(source_file, request_config)
    cached = load_cached_normalized(runtime.ocr_cache_root, request_key)
    cache_hit = cached is not None
    remote_calls = 0
    exact_model = resolved_model
    if cache_hit:
        normalized_payload = cached
    else:
        provider = provider_for(
            resolved_provider,
            allow_remote=resolved_allow_remote,
            fixture_json=fixture_json,
        )
        raw_payload = provider.run(
            OCRProviderRequest(
                file_path=source_file,
                pages=pages,
                model=resolved_model,
                include_blocks=runtime.ocr_include_blocks,
                confidence_granularity=runtime.ocr_confidence_granularity,
                table_format=runtime.ocr_table_format,
                extract_header=runtime.ocr_extract_header,
                extract_footer=runtime.ocr_extract_footer,
            )
        )
        remote_calls = int(raw_payload.get("remote_call_count", 0) or 0)
        exact_model = str(raw_payload.get("exact_model", resolved_model))
        normalized_payload = normalize_payload(raw_payload, request_key, source_file)
        save_cached_payloads(
            runtime.ocr_cache_root,
            request_key,
            raw_payload=raw_payload,
            normalized_payload=normalized_payload,
            source_file=source_file,
        )
        run_id = f"OCRRUN-{request_key[:12]}"
        save_json(
            runtime.ocr_cache_root / "runs" / f"{run_id}.json",
            {
                "ocr_run_id": run_id,
                "request_key": request_key,
                "provider": resolved_provider,
                "exact_model": exact_model,
                "source_file": str(source_file),
                "source_file_sha256": sha256_for_file(source_file),
                "pages": pages,
                "created_at": now_iso(),
                "remote_call_count": remote_calls,
                "published_evidence": False,
            },
            ignored_compare_keys=(),
        )
    return {
        "provider": resolved_provider,
        "request_key": request_key,
        "cache_hit": cache_hit,
        "remote_calls": remote_calls,
        "published_evidence": False,
        "exact_model": exact_model if not cache_hit else cached.get("exact_model", resolved_model),
        "normalized_path": str(runtime.ocr_cache_root / "normalized" / f"{request_key}.json"),
        "raw_path": str(runtime.ocr_cache_root / "raw" / f"{request_key}.json"),
        "normalized_payload": normalized_payload,
    }


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    args = parse_args()
    runtime = load_runtime_config()
    payload = run_ocr_for_file(
        source_file=Path(args.file),
        runtime=runtime,
        provider_name=args.provider,
        model=args.model,
        pages=args.pages,
        fixture_json=Path(args.fixture_json) if args.fixture_json else None,
    )
    payload.pop("normalized_payload", None)
    if args.format == "json":
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
