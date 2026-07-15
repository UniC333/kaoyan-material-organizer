from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from common import load_json_or_default, save_json, sha256_for_file


@dataclass(frozen=True)
class OCRRequestConfig:
    provider: str
    model: str
    pages: str
    include_blocks: bool
    confidence_granularity: str
    table_format: str
    extract_header: bool
    extract_footer: bool
    normalizer_version: str


def request_key_for_file(source_file: Path, config: OCRRequestConfig) -> str:
    payload = {
        "source_file_sha256": sha256_for_file(source_file),
        "pages": config.pages,
        "provider": config.provider,
        "exact_model_id": config.model,
        "include_blocks": config.include_blocks,
        "confidence_granularity": config.confidence_granularity,
        "table_format": config.table_format,
        "extract_header": config.extract_header,
        "extract_footer": config.extract_footer,
        "normalizer_version": config.normalizer_version,
    }
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def cache_paths_for_request(cache_root: Path, request_key: str) -> dict[str, Path]:
    return {
        "raw": cache_root / "raw" / f"{request_key}.json",
        "normalized": cache_root / "normalized" / f"{request_key}.json",
        "overlay": cache_root / "overlays" / f"{request_key}.json",
    }


def load_cached_normalized(cache_root: Path, request_key: str) -> dict[str, Any] | None:
    return load_json_or_default(cache_paths_for_request(cache_root, request_key)["normalized"], None)


def save_cached_payloads(
    cache_root: Path,
    request_key: str,
    *,
    raw_payload: dict[str, Any],
    normalized_payload: dict[str, Any],
    source_file: Path,
) -> dict[str, Path]:
    paths = cache_paths_for_request(cache_root, request_key)
    save_json(paths["raw"], raw_payload, ignored_compare_keys=())
    save_json(paths["normalized"], normalized_payload, ignored_compare_keys=())
    index_path = cache_root / "indexes" / "by-file" / f"{sha256_for_file(source_file)}.json"
    index_payload = load_json_or_default(index_path, {"source_file": str(source_file), "request_keys": []})
    request_keys = list(index_payload.get("request_keys", []))
    if request_key not in request_keys:
        request_keys.append(request_key)
    index_payload["source_file"] = str(source_file)
    index_payload["request_keys"] = request_keys
    save_json(index_path, index_payload, ignored_compare_keys=())
    return {**paths, "index": index_path}
