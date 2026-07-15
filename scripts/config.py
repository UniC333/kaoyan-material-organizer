#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

SKILL_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_VAULT_ROOT = SKILL_ROOT / "vault"
DEFAULT_KB_ROOT_NAME = ".kaoyan-kb"
DEFAULT_BACKUP_ROOT_NAME = ".kaoyan-backups"
DEFAULT_SYLLABUS_VERSION = "2027"
DEFAULT_CONFIG_FILENAME = "kaoyan.config.json"


@dataclass(frozen=True)
class RuntimeConfig:
    vault_root: Path
    workspace_root: Path
    kb_root: Path
    backup_root: Path
    migration_root: Path
    syllabus_root: Path
    python_executable: Path
    default_syllabus_version: str
    ocr_provider: str
    ocr_model: str
    ocr_cache_root: Path
    ocr_include_blocks: bool
    ocr_confidence_granularity: str
    ocr_table_format: str
    ocr_extract_header: bool
    ocr_extract_footer: bool
    ocr_max_concurrency: int
    ocr_monthly_page_budget: int
    ocr_allow_remote: bool
    paper_book_incoming_dir: str
    paper_book_metadata_dir: str
    paper_book_min_width: int
    paper_book_min_height: int
    paper_book_blur_threshold: float
    paper_book_phash_distance: int
    config_path: Path | None = None


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _resolve_path(raw: str | os.PathLike[str] | None, *, base_dir: Path | None = None) -> Path | None:
    if raw in (None, ""):
        return None
    path = Path(raw).expanduser()
    if not path.is_absolute() and base_dir is not None:
        path = (base_dir / path).resolve()
    return path


def _discover_config_path() -> Path | None:
    explicit = os.environ.get("KAOYAN_CONFIG_FILE")
    if explicit:
        path = Path(explicit).expanduser()
        return path if path.exists() else None

    cwd_candidate = Path.cwd() / DEFAULT_CONFIG_FILENAME
    if cwd_candidate.exists():
        return cwd_candidate

    try:
        cwd_resolved = Path.cwd().resolve()
        skill_root_resolved = SKILL_ROOT.resolve()
        if cwd_resolved == skill_root_resolved or skill_root_resolved in cwd_resolved.parents:
            skill_candidate = SKILL_ROOT / DEFAULT_CONFIG_FILENAME
            if skill_candidate.exists():
                return skill_candidate
    except OSError:
        pass
    return None


@lru_cache(maxsize=8)
def load_runtime_config(default_workspace: str | None = None) -> RuntimeConfig:
    config_path = _discover_config_path()
    config_payload = _read_json(config_path) if config_path else {}
    if default_workspace:
        # Keep runtime policy from a discovered config, but derive storage roots from the explicit test/workspace root.
        config_payload = dict(config_payload)
        for key in ("workspace_root", "vault_root", "kb_root", "backup_root", "migration_root", "syllabus_root", "ocr_cache_root"):
            config_payload.pop(key, None)
    base_dir = config_path.parent if config_path else None

    workspace_root = (
        _resolve_path(os.environ.get("KAOYAN_WORKSPACE_ROOT"))
        or _resolve_path(os.environ.get("KAOYAN_KB_WORKSPACE"))
        or _resolve_path(default_workspace)
        or _resolve_path(config_payload.get("workspace_root"), base_dir=base_dir)
        or Path.cwd()
    )
    vault_root = (
        _resolve_path(os.environ.get("KAOYAN_VAULT_ROOT"))
        or _resolve_path(config_payload.get("vault_root"), base_dir=base_dir)
        or DEFAULT_VAULT_ROOT
    )
    kb_root = (
        _resolve_path(os.environ.get("KAOYAN_KB_ROOT"))
        or _resolve_path(config_payload.get("kb_root"), base_dir=base_dir)
        or workspace_root / DEFAULT_KB_ROOT_NAME
    )
    backup_root = (
        _resolve_path(os.environ.get("KAOYAN_BACKUP_ROOT"))
        or _resolve_path(config_payload.get("backup_root"), base_dir=base_dir)
        or workspace_root / DEFAULT_BACKUP_ROOT_NAME
    )
    migration_root = (
        _resolve_path(os.environ.get("KAOYAN_MIGRATION_ROOT"))
        or _resolve_path(config_payload.get("migration_root"), base_dir=base_dir)
        or workspace_root / "_migration"
    )
    syllabus_root = (
        _resolve_path(os.environ.get("KAOYAN_SYLLABUS_ROOT"))
        or _resolve_path(config_payload.get("syllabus_root"), base_dir=base_dir)
        or kb_root / "syllabus"
    )
    python_executable = (
        _resolve_path(os.environ.get("KAOYAN_PYTHON"))
        or _resolve_path(config_payload.get("python_executable"), base_dir=base_dir)
        or Path(sys.executable)
    )
    default_syllabus_version = str(
        os.environ.get("KAOYAN_SYLLABUS_VERSION")
        or config_payload.get("default_syllabus_version")
        or DEFAULT_SYLLABUS_VERSION
    )
    ocr_provider = str(config_payload.get("ocr_provider") or os.environ.get("KAOYAN_OCR_PROVIDER") or "mistral")
    ocr_model = str(config_payload.get("ocr_model") or os.environ.get("KAOYAN_OCR_MODEL") or "mistral-ocr-4-0")
    ocr_cache_root = (
        _resolve_path(config_payload.get("ocr_cache_root"), base_dir=base_dir)
        or _resolve_path(os.environ.get("KAOYAN_OCR_CACHE_ROOT"))
        or kb_root / "ocr"
    )
    ocr_include_blocks = str(config_payload.get("ocr_include_blocks", os.environ.get("KAOYAN_OCR_INCLUDE_BLOCKS", True))).lower() not in {"0", "false", "no"}
    ocr_confidence_granularity = str(
        config_payload.get("ocr_confidence_granularity")
        or os.environ.get("KAOYAN_OCR_CONFIDENCE_GRANULARITY")
        or "word"
    )
    ocr_table_format = str(config_payload.get("ocr_table_format") or os.environ.get("KAOYAN_OCR_TABLE_FORMAT") or "html")
    ocr_extract_header = str(config_payload.get("ocr_extract_header", os.environ.get("KAOYAN_OCR_EXTRACT_HEADER", True))).lower() not in {"0", "false", "no"}
    ocr_extract_footer = str(config_payload.get("ocr_extract_footer", os.environ.get("KAOYAN_OCR_EXTRACT_FOOTER", True))).lower() not in {"0", "false", "no"}
    ocr_max_concurrency = int(config_payload.get("ocr_max_concurrency") or os.environ.get("KAOYAN_OCR_MAX_CONCURRENCY") or 2)
    ocr_monthly_page_budget = int(
        config_payload.get("ocr_monthly_page_budget") or os.environ.get("KAOYAN_OCR_MONTHLY_PAGE_BUDGET") or 0
    )
    ocr_allow_remote = str(config_payload.get("ocr_allow_remote", os.environ.get("KAOYAN_OCR_ALLOW_REMOTE", True))).lower() in {"1", "true", "yes"}
    paper_book_incoming_dir = str(
        os.environ.get("KAOYAN_PAPER_BOOK_INCOMING_DIR")
        or config_payload.get("paper_book_incoming_dir")
        or "incoming"
    )
    paper_book_metadata_dir = str(
        os.environ.get("KAOYAN_PAPER_BOOK_METADATA_DIR")
        or config_payload.get("paper_book_metadata_dir")
        or "metadata"
    )
    paper_book_min_width = int(
        os.environ.get("KAOYAN_PAPER_BOOK_MIN_WIDTH")
        or config_payload.get("paper_book_min_width")
        or 1000
    )
    paper_book_min_height = int(
        os.environ.get("KAOYAN_PAPER_BOOK_MIN_HEIGHT")
        or config_payload.get("paper_book_min_height")
        or 1400
    )
    paper_book_blur_threshold = float(
        os.environ.get("KAOYAN_PAPER_BOOK_BLUR_THRESHOLD")
        or config_payload.get("paper_book_blur_threshold")
        or 80.0
    )
    paper_book_phash_distance = int(
        os.environ.get("KAOYAN_PAPER_BOOK_PHASH_DISTANCE")
        or config_payload.get("paper_book_phash_distance")
        or 6
    )

    return RuntimeConfig(
        vault_root=vault_root,
        workspace_root=workspace_root,
        kb_root=kb_root,
        backup_root=backup_root,
        migration_root=migration_root,
        syllabus_root=syllabus_root,
        python_executable=python_executable,
        default_syllabus_version=default_syllabus_version,
        ocr_provider=ocr_provider,
        ocr_model=ocr_model,
        ocr_cache_root=ocr_cache_root,
        ocr_include_blocks=ocr_include_blocks,
        ocr_confidence_granularity=ocr_confidence_granularity,
        ocr_table_format=ocr_table_format,
        ocr_extract_header=ocr_extract_header,
        ocr_extract_footer=ocr_extract_footer,
        ocr_max_concurrency=ocr_max_concurrency,
        ocr_monthly_page_budget=ocr_monthly_page_budget,
        ocr_allow_remote=ocr_allow_remote,
        paper_book_incoming_dir=paper_book_incoming_dir,
        paper_book_metadata_dir=paper_book_metadata_dir,
        paper_book_min_width=paper_book_min_width,
        paper_book_min_height=paper_book_min_height,
        paper_book_blur_threshold=paper_book_blur_threshold,
        paper_book_phash_distance=paper_book_phash_distance,
        config_path=config_path,
    )


def reset_runtime_config_cache() -> None:
    load_runtime_config.cache_clear()
