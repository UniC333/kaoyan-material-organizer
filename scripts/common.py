#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import unicodedata
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any

from config import DEFAULT_KB_ROOT_NAME, DEFAULT_VAULT_ROOT, RuntimeConfig, load_runtime_config
from kaoyan_kb.storage.atomic_io import load_json, load_json_or_default, save_json, save_text

VAULT_ROOT = DEFAULT_VAULT_ROOT
RUNTIME_PYTHON = Path(sys.executable)
KB_ROOT_NAME = DEFAULT_KB_ROOT_NAME
BATCH_DIRNAME = "90_\\u8d44\\u6599\\u6574\\u7406\\u6279\\u6b21".encode("utf-8").decode("unicode_escape")
INDEX_DIRNAME = "99_\\u7d22\\u5f15\\u4e0e\\u72b6\\u6001".encode("utf-8").decode("unicode_escape")
BATCH_TEMPLATE = VAULT_ROOT / BATCH_DIRNAME / "00_\\u6279\\u6b21\\u8bb0\\u5f55\\u6a21\\u677f.md".encode("utf-8").decode("unicode_escape")
BRIEF_TEMPLATE = VAULT_ROOT / INDEX_DIRNAME / "01_\\u6574\\u7406\\u6458\\u8981\\u6a21\\u677f.md".encode("utf-8").decode("unicode_escape")

SUBJECT_MAP = {
    "\\u6570\\u5b66".encode("utf-8").decode("unicode_escape"): {
        "dir": "10_\\u6570\\u5b66".encode("utf-8").decode("unicode_escape"),
        "content": "10_\\u6559\\u6750\\u6574\\u7406".encode("utf-8").decode("unicode_escape"),
        "aliases": {"\\u6570\\u5b66".encode("utf-8").decode("unicode_escape"), "10_\\u6570\\u5b66".encode("utf-8").decode("unicode_escape"), "math"},
    },
    "\\u82f1\\u8bed".encode("utf-8").decode("unicode_escape"): {
        "dir": "20_\\u82f1\\u8bed".encode("utf-8").decode("unicode_escape"),
        "content": "10_\\u6559\\u6750\\u6574\\u7406".encode("utf-8").decode("unicode_escape"),
        "aliases": {"\\u82f1\\u8bed".encode("utf-8").decode("unicode_escape"), "20_\\u82f1\\u8bed".encode("utf-8").decode("unicode_escape"), "english"},
    },
    "408": {
        "dir": "30_408",
        "content": "10_\\u6559\\u6750\\u6574\\u7406".encode("utf-8").decode("unicode_escape"),
        "aliases": {"408", "30_408", "408\\u6570\\u636e\\u7ed3\\u6784".encode("utf-8").decode("unicode_escape"), "408 \\u6570\\u636e\\u7ed3\\u6784".encode("utf-8").decode("unicode_escape"), "\\u6570\\u636e\\u7ed3\\u6784".encode("utf-8").decode("unicode_escape")},
    },
    "\\u653f\\u6cbb".encode("utf-8").decode("unicode_escape"): {
        "dir": "40_\\u653f\\u6cbb".encode("utf-8").decode("unicode_escape"),
        "content": "10_\\u6559\\u6750\\u6574\\u7406".encode("utf-8").decode("unicode_escape"),
        "aliases": {"\\u653f\\u6cbb".encode("utf-8").decode("unicode_escape"), "40_\\u653f\\u6cbb".encode("utf-8").decode("unicode_escape"), "politics"},
    },
}

STATUS_LABELS = {
    "success": "\\u6210\\u529f".encode("utf-8").decode("unicode_escape"),
    "partial": "\\u90e8\\u5206\\u6210\\u529f".encode("utf-8").decode("unicode_escape"),
    "failure": "\\u5931\\u8d25".encode("utf-8").decode("unicode_escape"),
    "pending": "\\u5f85\\u8865\\u8dd1".encode("utf-8").decode("unicode_escape"),
    "todo": "\\u5f85\\u6574\\u7406".encode("utf-8").decode("unicode_escape"),
    "doing": "\\u6574\\u7406\\u4e2d".encode("utf-8").decode("unicode_escape"),
    "need-pages": "\\u5f85\\u8865\\u9875".encode("utf-8").decode("unicode_escape"),
    "need-review": "\\u5f85\\u4eba\\u5de5\\u590d\\u6838".encode("utf-8").decode("unicode_escape"),
    "done": "\\u5df2\\u5b8c\\u6210".encode("utf-8").decode("unicode_escape"),
}

PAGE_NUMBER_POSITION_LABELS = {
    "top": "\\u9875\\u7801\\u5728\\u4e0a\\u65b9".encode("utf-8").decode("unicode_escape"),
    "bottom": "\\u9875\\u7801\\u5728\\u4e0b\\u65b9".encode("utf-8").decode("unicode_escape"),
    "mixed": "\\u9875\\u7801\\u4f4d\\u7f6e\\u6df7\\u5408".encode("utf-8").decode("unicode_escape"),
    "unknown": "\\u9875\\u7801\\u4f4d\\u7f6e\\u5f85\\u786e\\u8ba4".encode("utf-8").decode("unicode_escape"),
}

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"}
BROAD_SCOPE_KEYWORDS = {"\\u5168\\u90e8", "\\u5168\\u91cf", "\\u6574\\u672c", "\\u6574\\u95e8", "\\u6574\\u79d1", "\\u6574\\u5957", "\\u5168\\u4e66", "\\u6240\\u6709\\u7ae0\\u8282", "\\u5b8c\\u6574\\u6559\\u6750"}
BROAD_SCOPE_KEYWORDS = {item.encode("utf-8").decode("unicode_escape") for item in BROAD_SCOPE_KEYWORDS}
PLACEHOLDERS = {"", "\\u5f85\\u8865\\u5145", "\\u5f85\\u5224\\u5b9a", "\\u5f85\\u6574\\u7406", "\\u5f85\\u786e\\u8ba4", "\\u5f85\\u8865\\u9875", "\\u5f85\\u4eba\\u5de5\\u590d\\u6838", "-", "\\u672a\\u77e5"}
PLACEHOLDERS = {item.encode("utf-8").decode("unicode_escape") if "\\u" in item else item for item in PLACEHOLDERS}
PROFILE_DIR = Path(__file__).resolve().parent.parent / "profiles"
SCHEMA_VERSION = "0.4.0"
MACHINE_GENERATED_BY = "kaoyan-material-organizer"
REPOSITORY_SCHEMA_ROOT = Path(__file__).resolve().parent.parent / "schemas"
CORE_SCHEMA_MANIFEST_NAME = "core-schema-manifest.json"


@lru_cache(maxsize=1)
def load_core_schema_templates() -> dict[str, dict[str, Any]]:
    """Load the repository-owned core contracts without any network dependency."""
    manifest_path = REPOSITORY_SCHEMA_ROOT / CORE_SCHEMA_MANIFEST_NAME
    if not manifest_path.is_file():
        raise ValueError(f"missing repository schema manifest: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if str(manifest.get("schema_version", "")) != SCHEMA_VERSION:
        raise ValueError(f"schema version mismatch: expected {SCHEMA_VERSION}")
    names = manifest.get("schemas")
    if not isinstance(names, list) or not names or not all(isinstance(name, str) and name.endswith(".schema.json") for name in names):
        raise ValueError("invalid repository schema manifest")
    templates: dict[str, dict[str, Any]] = {}
    for name in names:
        schema_path = REPOSITORY_SCHEMA_ROOT / name
        if not schema_path.is_file():
            raise ValueError(f"missing repository schema: {name}")
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        if not isinstance(schema, dict) or schema.get("type") != "object" or not isinstance(schema.get("required"), list):
            raise ValueError(f"invalid repository schema: {name}")
        templates[name] = schema
    return templates


def validate_entity_contract(entity_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Fail at the entity boundary when a maintained cross-stage contract is incomplete."""
    schema = load_core_schema_templates().get(f"{entity_type}.schema.json")
    if schema is None:
        raise ValueError(f"unknown entity contract: {entity_type}")
    missing = [field for field in schema["required"] if field not in payload or payload[field] in (None, "")]
    if missing:
        raise ValueError(f"{entity_type} contract missing required fields: {', '.join(missing)}")
    if schema.get("additionalProperties") is False:
        allowed = set(schema.get("properties", {}))
        unknown = sorted(set(payload) - allowed)
        if unknown:
            raise ValueError(f"{entity_type} contract has unknown fields: {', '.join(unknown)}")
    return payload


def normalize_profile_rule(rule: dict[str, Any]) -> dict[str, Any]:
    payload = dict(rule or {})
    if "start" in payload:
        payload["start"] = int(payload["start"])
    if "end" in payload:
        payload["end"] = int(payload["end"])
    return payload


def normalize_chapter_profile(profile: dict[str, Any], source_path: Path) -> dict[str, Any]:
    payload = dict(profile or {})
    payload.setdefault("profile_id", sanitize_name(payload.get("chapter_title") or source_path.stem))
    payload.setdefault("source_profile_path", str(source_path))
    payload["match"] = dict(payload.get("match") or {})
    payload["page_rules"] = [normalize_profile_rule(item) for item in payload.get("page_rules", []) if isinstance(item, dict)]
    payload["chunk_rules"] = [normalize_profile_rule(item) for item in payload.get("chunk_rules", []) if isinstance(item, dict)]
    return payload


@lru_cache(maxsize=1)
def load_chapter_profiles() -> tuple[dict[str, Any], ...]:
    profiles: list[dict[str, Any]] = []
    if not PROFILE_DIR.exists():
        return tuple()
    for path in sorted(PROFILE_DIR.glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(payload, list):
            raw_profiles = payload
        elif isinstance(payload, dict) and isinstance(payload.get("profiles"), list):
            raw_profiles = payload["profiles"]
        elif isinstance(payload, dict):
            raw_profiles = [payload]
        else:
            continue
        for item in raw_profiles:
            if isinstance(item, dict):
                profiles.append(normalize_chapter_profile(item, path))
    return tuple(profiles)


def ensure_templates() -> None:
    for template in (batch_template_path(), brief_template_path()):
        if not template.exists():
            raise SystemExit(f"[ERROR] missing template: {template}")


def resolve_subject(raw: str) -> tuple[str, dict[str, Any]]:
    normalized = raw.strip().lower()
    for label, config in SUBJECT_MAP.items():
        aliases = {alias.lower() for alias in config["aliases"]}
        if normalized in aliases:
            return label, config
    supported = ", ".join(SUBJECT_MAP)
    raise SystemExit(f"[ERROR] unsupported subject: {raw}; supported: {supported}")


def sanitize_name(text: str) -> str:
    text = re.sub(r'[<>:"/\\|?*]+', "-", text.strip())
    text = re.sub(r"\s+", "-", text)
    text = re.sub(r"-{2,}", "-", text).strip("-")
    return text or "untitled"


def validate_scope(scope: str, mode: str) -> None:
    if mode in {"full", "chapter-photo"}:
        return
    if any(keyword in scope for keyword in BROAD_SCOPE_KEYWORDS):
        raise SystemExit("[ERROR] scope too broad; split it first")


def content_root_for(config: dict[str, Any]) -> Path:
    return current_vault_root() / config["dir"] / config["content"]


def build_batch_id(subject_key: str, source_name: str, scope: str, timestamp: str) -> str:
    return f"{timestamp}-{sanitize_name(subject_key)}-{sanitize_name(source_name)}-{sanitize_name(scope)}"


def choose_output_dir(output_dir: str | None, config: dict[str, Any], source_name: str, scope: str) -> Path:
    if output_dir:
        candidate = Path(output_dir)
        if not candidate.is_absolute():
            candidate = content_root_for(config) / candidate
        return candidate
    return content_root_for(config) / f"{sanitize_name(source_name)}-{sanitize_name(scope)}"


def collect_image_files(material_path: Path) -> list[Path]:
    files: list[Path] = []
    for child in sorted(material_path.rglob("*")):
        if child.is_file() and child.suffix.lower() in IMAGE_EXTS:
            files.append(child)
    return files


def list_material_files(material_path: Path) -> list[Path]:
    files: list[Path] = []
    for child in sorted(material_path.rglob("*")):
        if child.is_file():
            files.append(child)
    return files


def machine_frontmatter(generated_type: str) -> str:
    return "\n".join(
        [
            "---",
            f"generated_by: {MACHINE_GENERATED_BY}",
            f"generated_type: {generated_type}",
            "---",
            "",
        ]
    )


def wrap_generated_markdown(content: str, generated_type: str) -> str:
    body = content
    if body.startswith("---\n"):
        _, _, remainder = body.partition("\n---\n")
        body = remainder if remainder else body
    return machine_frontmatter(generated_type) + body.lstrip("\n")


def markdown_frontmatter(path: Path) -> dict[str, str]:
    if not path.exists() or path.suffix.lower() != ".md":
        return {}
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        return {}
    end = text.find("\n---\n", 4)
    if end < 0:
        return {}
    payload: dict[str, str] = {}
    for line in text[4:end].splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        payload[key.strip()] = value.strip()
    return payload


def is_owned_generated_markdown(path: Path, generated_type: str | None = None) -> bool:
    payload = markdown_frontmatter(path)
    if payload.get("generated_by") != MACHINE_GENERATED_BY:
        return False
    if generated_type is not None and payload.get("generated_type") != generated_type:
        return False
    return True


def build_source_span(
    *,
    source_id: str,
    file_id: str,
    source_file_sha256: str = "",
    page_start: Any,
    page_end: Any,
    image_start: Any,
    image_end: Any,
    origin_type: str,
    verification_status: str,
    chapter_id: str = "",
    chunk_id: str = "",
    block_ids: list[str] | None = None,
    bbox: list[float] | None = None,
    notes: str = "",
) -> dict[str, Any]:
    return {
        "source_id": source_id,
        "file_id": file_id,
        "source_file_sha256": source_file_sha256,
        "chapter_id": chapter_id,
        "chunk_id": chunk_id,
        "origin_type": origin_type,
        "verification_status": verification_status,
        "locator": {
            "page_start": page_start,
            "page_end": page_end,
            "image_start": image_start,
            "image_end": image_end,
            "block_ids": list(block_ids or []),
            "bbox": list(bbox or []),
        },
        "notes": notes,
    }


def build_provenance_record(
    *,
    origin_type: str,
    verification_status: str,
    source_spans: list[dict[str, Any]],
    source_grounded: bool,
    profile_hint_used: bool = False,
    title_inference_used: bool = False,
) -> dict[str, Any]:
    return {
        "origin_type": origin_type,
        "verification_status": verification_status,
        "source_grounded": bool(source_grounded),
        "profile_hint_used": bool(profile_hint_used),
        "title_inference_used": bool(title_inference_used),
        "source_spans": list(source_spans),
    }


def detect_subject_from_path(material_path: Path) -> str | None:
    joined = "\\".join(material_path.parts)
    for label, config in SUBJECT_MAP.items():
        if config["dir"] in joined:
            return label
    return None


def detect_input_path_warning(material_path: Path, resolved_subject: str) -> str:
    path_subject = detect_subject_from_path(material_path)
    if path_subject and path_subject != resolved_subject:
        return f"input path looks like {path_subject}, but batch subject is {resolved_subject}"
    return ""


def is_placeholder(value: Any) -> bool:
    if value is None:
        return True
    return str(value).strip() in PLACEHOLDERS


def ensure_learning_dirs(batch_dir: Path) -> dict[str, Path]:
    names = {
        "chunk_plan": "10_\\u5206\\u7247\\u8ba1\\u5212",
        "chapter_notes": "20_\\u7ae0\\u8282\\u6574\\u7406",
        "chunk_extracts": "30_\\u7247\\u6bb5\\u63d0\\u53d6",
        "cards": "40_\\u77e5\\u8bc6\\u70b9\\u5361\\u7247",
        "question_index": "50_\\u63d0\\u95ee\\u7d22\\u5f15",
    }
    dirs = {key: batch_dir / value.encode("utf-8").decode("unicode_escape") for key, value in names.items()}
    for path in dirs.values():
        path.mkdir(parents=True, exist_ok=True)
    return dirs


def parse_manifest_table(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    header = "\\u5e8f\\u53f7".encode("utf-8").decode("unicode_escape")
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line.startswith("|") or "---" in line:
            continue
        parts = [part.strip() for part in line.strip("|").split("|")]
        if len(parts) < 6 or parts[0] == header:
            continue
        try:
            seq = int(parts[0])
        except ValueError:
            continue
        if len(parts) >= 7:
            page_hint, usage_hint, section_hint, status_hint = parts[3], parts[4], parts[5], parts[6]
        else:
            page_hint = "\\u5f85\\u8865\\u5145".encode("utf-8").decode("unicode_escape")
            usage_hint, section_hint, status_hint = parts[3], parts[4], parts[5]
        rows.append(
            {
                "seq": seq,
                "file_name": parts[1],
                "relative_path": parts[2],
                "page_hint": page_hint,
                "usage_hint": usage_hint,
                "section_hint": section_hint,
                "status_hint": status_hint,
            }
        )
    return rows


def parse_page_token(value: str) -> str:
    text = value.strip()
    return text if not is_placeholder(text) else "\\u5f85\\u8865\\u5145".encode("utf-8").decode("unicode_escape")


def format_page_label(page_number: int) -> str:
    return f"第{page_number}页"


def resolve_chapter_profile(context: dict[str, Any]) -> dict[str, Any] | None:
    subject = str(context.get("subject") or context.get("resolved_subject") or context.get("input_subject") or "").strip()
    source_name = str(context.get("source_name") or "")
    chapter_title = str(context.get("chapter_title") or context.get("scope") or "")
    scope = str(context.get("scope") or "")
    material_path = str(context.get("material_path") or "")
    for profile in load_chapter_profiles():
        match = profile.get("match", {})
        if match.get("subject") and match["subject"] != subject:
            continue
        needle = match.get("source_name_contains")
        if needle and needle not in source_name:
            continue
        chapter_needle = match.get("chapter_title_contains")
        if chapter_needle and chapter_needle not in chapter_title:
            continue
        scope_needle = match.get("scope_contains")
        if scope_needle and scope_needle not in scope:
            continue
        path_needle = match.get("material_path_contains")
        if path_needle and path_needle not in material_path:
            continue
        return profile
    return None


def canonical_book_title(context: dict[str, Any]) -> str:
    """Resolve the existing book-level label required by evidence materialization.

    Explicit context metadata wins, followed by a matched chapter profile and the
    historic R6 subject-level compatibility map.  Absent those established
    identities, fail before an evidence record can be materialized with an
    invented book title.
    """
    explicit = str(context.get("book_title") or "").strip()
    if explicit and not is_placeholder(explicit):
        return explicit
    profile = resolve_chapter_profile(context)
    profile_title = str((profile or {}).get("book_title") or "").strip()
    if profile_title and not is_placeholder(profile_title):
        return profile_title
    subject = str(context.get("subject") or context.get("resolved_subject") or context.get("input_subject") or "").strip()
    subject_titles = {
        "数学": "李正元数一",
        "408": "王道数据结构",
    }
    if subject in subject_titles:
        return subject_titles[subject]
    raise ValueError("canonical book title is required for evidence materialization")


def find_page_rule(profile: dict[str, Any] | None, seq: int) -> dict[str, Any] | None:
    if not profile:
        return None
    for rule in profile.get("page_rules", []):
        if rule["start"] <= seq <= rule["end"]:
            return rule
    return None


def find_chunk_rule(profile: dict[str, Any] | None, image_start: int, image_end: int) -> dict[str, Any] | None:
    if not profile:
        return None
    for rule in profile.get("chunk_rules", []):
        if rule["start"] == image_start and rule["end"] == image_end:
            return rule
    return None


def build_manifest_row_defaults(context: dict[str, Any], seq: int) -> dict[str, str]:
    profile = resolve_chapter_profile(context)
    page_hint = "\\u5f85\\u8865\\u5145".encode("utf-8").decode("unicode_escape")
    page_sequence_mode = context.get("page_sequence_mode", "manual")
    start_page_number = int(context.get("start_page_number") or 1)
    if page_sequence_mode == "ordered":
        page_hint = format_page_label(start_page_number + seq - 1)
    usage_hint = "\\u5f85\\u4eba\\u5de5\\u590d\\u6838".encode("utf-8").decode("unicode_escape")
    section_hint = "\\u5f85\\u8865\\u5145".encode("utf-8").decode("unicode_escape")
    page_rule = find_page_rule(profile, seq)
    if page_rule:
        usage_hint = page_rule.get("usage", usage_hint)
        section_hint = page_rule.get("section", section_hint)
    status_hint = "已初填" if not (is_placeholder(page_hint) or is_placeholder(usage_hint) or is_placeholder(section_hint)) else "待整理"
    return {
        "page_hint": page_hint,
        "usage_hint": usage_hint,
        "section_hint": section_hint,
        "status_hint": status_hint,
    }


def split_chunks(rows: list[dict[str, Any]], max_images_per_chunk: int) -> list[list[dict[str, Any]]]:
    chunks: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    current_section: str | None = None
    for row in rows:
        section = row["section_hint"].strip()
        meaningful_section = None if is_placeholder(section) else section
        should_split = False
        if current and len(current) >= max_images_per_chunk:
            should_split = True
        elif current and meaningful_section and current_section and meaningful_section != current_section:
            should_split = True
        if should_split:
            chunks.append(current)
            current = []
        current.append(row)
        if meaningful_section:
            current_section = meaningful_section
    if current:
        chunks.append(current)
    return chunks


def markdown_list(items: list[str], empty_text: str = "\\u5f85\\u8865\\u5145".encode("utf-8").decode("unicode_escape")) -> str:
    if not items:
        return f"- {empty_text}"
    return "\n".join(f"- {item}" for item in items)


def normalize_context(context: dict[str, Any]) -> dict[str, Any]:
    subject = context.get("subject") or context.get("resolved_subject") or context.get("input_subject") or "unknown"
    context.setdefault("subject", subject)
    context.setdefault("resolved_subject", subject)
    context.setdefault("chapter_title", context.get("scope") or context.get("source_name") or "chapter")
    context.setdefault("chapter_slug", sanitize_name(context["chapter_title"]))
    context.setdefault("page_number_position_label", PAGE_NUMBER_POSITION_LABELS.get(context.get("page_number_position", "unknown"), PAGE_NUMBER_POSITION_LABELS["unknown"]))
    context.setdefault("input_path_warning", "")
    return context


def current_vault_root() -> Path:
    return load_runtime_config().vault_root


def default_vault_root_arg() -> str:
    return str(current_vault_root())


def batch_template_path() -> Path:
    return current_vault_root() / BATCH_DIRNAME / "00_\\u6279\\u6b21\\u8bb0\\u5f55\\u6a21\\u677f.md".encode("utf-8").decode("unicode_escape")


def brief_template_path() -> Path:
    return current_vault_root() / INDEX_DIRNAME / "01_\\u6574\\u7406\\u6458\\u8981\\u6a21\\u677f.md".encode("utf-8").decode("unicode_escape")


def preferred_python_executable() -> str:
    return str(load_runtime_config().python_executable)


def workspace_root(default: Path | None = None) -> Path:
    return load_runtime_config(str(default) if default else None).workspace_root


def kb_root(default: Path | None = None) -> Path:
    return load_runtime_config(str(default) if default else None).kb_root


def runtime_subprocess_env(
    runtime: RuntimeConfig | None = None,
    *,
    vault_root_override: Path | None = None,
    workspace_root_override: Path | None = None,
    kb_root_override: Path | None = None,
) -> dict[str, str]:
    resolved = runtime or load_runtime_config()
    env = os.environ.copy()
    env["KAOYAN_WORKSPACE_ROOT"] = str(workspace_root_override or resolved.workspace_root)
    env["KAOYAN_VAULT_ROOT"] = str(vault_root_override or resolved.vault_root)
    env["KAOYAN_KB_ROOT"] = str(kb_root_override or resolved.kb_root)
    env["KAOYAN_BACKUP_ROOT"] = str(resolved.backup_root)
    env["KAOYAN_SYLLABUS_ROOT"] = str(resolved.syllabus_root)
    env["KAOYAN_PYTHON"] = str(resolved.python_executable)
    env["KAOYAN_SYLLABUS_VERSION"] = str(resolved.default_syllabus_version)
    if resolved.config_path:
        env["KAOYAN_CONFIG_FILE"] = str(resolved.config_path)
    return env


class SubprocessUtf8Error(UnicodeError):
    """A subprocess emitted output that is unsafe to pass into maintained artifacts."""


def _decode_subprocess_stream(data: bytes | None, *, command_label: str, stream: str) -> str:
    raw = data or b""
    try:
        decoded = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise SubprocessUtf8Error(
            f"{command_label} emitted invalid UTF-8 on {stream}; configure the child for UTF-8 "
            "(for Python, set PYTHONUTF8=1) and retry"
        ) from exc
    if "\ufffd" in decoded:
        raise SubprocessUtf8Error(
            f"{command_label} emitted a Unicode replacement character on {stream}; repair the source encoding "
            "(for Python, set PYTHONUTF8=1) and retry"
        )
    return decoded.replace("\r\n", "\n").replace("\r", "\n")


def run_utf8_subprocess(
    command: list[str],
    *,
    command_label: str,
    check: bool = False,
    env: dict[str, str] | None = None,
    cwd: str | Path | None = None,
) -> subprocess.CompletedProcess[str]:
    """Capture a subprocess as bytes, then expose only strict, U+FFFD-free UTF-8 text.

    ``command_label`` is intentionally caller-owned and must not contain arguments,
    paths, or environment values. Decode failures therefore remain actionable without
    leaking command-line secrets.
    """
    child_env = dict(env or os.environ)
    child_env.setdefault("PYTHONUTF8", "1")
    child_env.setdefault("PYTHONIOENCODING", "utf-8")
    completed = subprocess.run(command, capture_output=True, env=child_env, cwd=cwd)
    stdout = _decode_subprocess_stream(completed.stdout, command_label=command_label, stream="stdout")
    stderr = _decode_subprocess_stream(completed.stderr, command_label=command_label, stream="stderr")
    result = subprocess.CompletedProcess(completed.args, completed.returncode, stdout, stderr)
    if check and result.returncode != 0:
        raise subprocess.CalledProcessError(result.returncode, result.args, output=stdout, stderr=stderr)
    return result


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def ensure_kb_layout(default: Path | None = None) -> dict[str, Path]:
    root = kb_root(default)
    paths = {
        "root": root,
        "manifests": root / "manifests",
        "manifest_sources": root / "manifests" / "sources",
        "manifest_files": root / "manifests" / "files",
        "manifest_chapters": root / "manifests" / "chapters",
        "manifest_chunks": root / "manifests" / "chunks",
        "sources": root / "sources",
        "evidence": root / "evidence",
        "syllabus": root / "syllabus",
        "claims": root / "claims",
        "conflicts": root / "conflicts",
        "indexes": root / "indexes",
        "learner": root / "learner",
        "runs": root / "runs",
        "review_queues": root / "review-queues",
        "review_syllabus_mapping": root / "review-queues" / "syllabus-mapping",
        "schemas": root / "schemas",
    }
    for path in paths.values():
        path.mkdir(parents=True, exist_ok=True)
    schema_version_path = root / "schema-version.json"
    existing_schema_version = load_json_or_default(schema_version_path, {})
    save_json(
        schema_version_path,
        {
            "schema_version": SCHEMA_VERSION,
            "created_at": existing_schema_version.get("created_at") or now_iso(),
            "updated_at": now_iso(),
            "root_policy": "machine-side metadata only; learning-facing outputs stay in Obsidian vault",
            "migration_state": "provenance-contract-enabled",
        },
    )
    for name, payload in load_core_schema_templates().items():
        schema_path = paths["schemas"] / name
        save_json(schema_path, payload, ignored_compare_keys=())
    return paths


def subject_id_code(subject: str) -> str:
    mapping = {
        "数学": "MATH",
        "英语": "ENG",
        "408": "408",
        "政治": "POL",
    }
    return mapping.get(subject, sanitize_name(subject).upper() or "GEN")


def _counter_key(kind: str, subject: str) -> str:
    return f"{kind}:{subject_id_code(subject)}"


def _id_pattern(kind: str, subject: str) -> re.Pattern[str]:
    code = re.escape(subject_id_code(subject))
    prefixes = {
        "source": rf"^SRC-{code}-(\d{{4}})$",
        "file": rf"^FILE-{code}-(\d{{6}})$",
        "chapter": rf"^CH-{code}-(\d{{4}})$",
        "chunk": rf"^CHUNK-{code}-(\d{{6}})$",
        "evidence": rf"^EV-{code}-(\d{{6}})$",
        "claim": rf"^CLAIM-{code}-(\d{{6}})$",
        "conflict": rf"^CONFLICT-{code}-(\d{{6}})$",
    }
    if kind not in prefixes:
        raise ValueError(f"unsupported kb id kind: {kind}")
    return re.compile(prefixes[kind])


def _id_scan_dirs(kind: str, layout: dict[str, Path]) -> list[Path]:
    mapping = {
        "source": [layout["sources"], layout["manifest_sources"]],
        "file": [layout["manifest_files"]],
        "chapter": [layout["manifest_chapters"]],
        "chunk": [layout["manifest_chunks"]],
        "evidence": [layout["evidence"]],
        "claim": [layout["claims"]],
        "conflict": [layout["conflicts"]],
    }
    if kind not in mapping:
        raise ValueError(f"unsupported kb id kind: {kind}")
    return mapping[kind]


def _max_existing_id_number(kind: str, subject: str, layout: dict[str, Path]) -> int:
    pattern = _id_pattern(kind, subject)
    maximum = 0
    for directory in _id_scan_dirs(kind, layout):
        for path in directory.glob("*.json"):
            match = pattern.match(path.stem)
            if not match:
                continue
            maximum = max(maximum, int(match.group(1)))
    return maximum


def allocate_kb_id(kind: str, subject: str, default: Path | None = None) -> str:
    layout = ensure_kb_layout(default)
    counter_path = layout["indexes"] / "id_counters.json"
    counters = load_json_or_default(counter_path, {})
    key = _counter_key(kind, subject)
    current_counter = int(counters.get(key, 0) or 0)
    existing_max = _max_existing_id_number(kind, subject, layout)
    counters[key] = max(current_counter, existing_max) + 1
    save_json(counter_path, counters)
    code = subject_id_code(subject)
    number = counters[key]
    if kind == "source":
        return f"SRC-{code}-{number:04d}"
    if kind == "file":
        return f"FILE-{code}-{number:06d}"
    if kind == "chapter":
        return f"CH-{code}-{number:04d}"
    if kind == "chunk":
        return f"CHUNK-{code}-{number:06d}"
    if kind == "evidence":
        return f"EV-{code}-{number:06d}"
    if kind == "claim":
        return f"CLAIM-{code}-{number:06d}"
    if kind == "conflict":
        return f"CONFLICT-{code}-{number:06d}"
    raise ValueError(f"unsupported kb id kind: {kind}")


def allocate_run_id(default: Path | None = None) -> str:
    layout = ensure_kb_layout(default)
    counter_path = layout["indexes"] / "run_counters.json"
    counters = load_json_or_default(counter_path, {})
    stamp = datetime.now().strftime("%Y%m%d")
    counters[stamp] = int(counters.get(stamp, 0)) + 1
    save_json(counter_path, counters)
    return f"RUN-{stamp}-{counters[stamp]:03d}"


def sha256_for_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(filesystem_path(path), "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def filesystem_path(path: str | Path) -> str:
    raw = os.fspath(path)
    if os.name != "nt" or not raw:
        return raw
    if raw.startswith("\\\\?\\"):
        return raw
    candidate = raw
    if not os.path.isabs(candidate):
        candidate = os.path.abspath(candidate)
    if candidate.startswith("\\\\"):
        return "\\\\?\\UNC\\" + candidate.lstrip("\\")
    return "\\\\?\\" + candidate


def display_path(path: str | Path) -> str:
    raw = os.fspath(path)
    if os.name != "nt" or not raw:
        return raw
    if raw.startswith("\\\\?\\UNC\\"):
        return "\\\\" + raw[len("\\\\?\\UNC\\") :]
    if raw.startswith("\\\\?\\"):
        return raw[len("\\\\?\\") :]
    return raw


def ensure_parent_dir(path: Path) -> None:
    os.makedirs(filesystem_path(path.parent), exist_ok=True)


def stable_fingerprint(payload: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


CLAIM_TEXT_PREFIX_PATTERNS = (
    "通常认为",
    "通常把",
    "通常将",
    "可以理解为",
    "可理解为",
    "是指",
    "指的是",
    "通常指",
)
CLAIM_TEXT_REPLACEMENTS = {
    "是": "",
    "指": "",
    "的": "",
    "一种": "",
    "一个": "",
}


def canonicalize_claim_text(text: str) -> str:
    value = unicodedata.normalize("NFKC", str(text or "")).strip().lower()
    value = re.sub(r"\s+", "", value)
    value = value.replace("：", ":").replace("，", ",").replace("。", "").replace("；", ";")
    value = re.sub(r"[,:;、（）()\[\]【】“”\"'·]", "", value)
    for prefix in CLAIM_TEXT_PREFIX_PATTERNS:
        if value.startswith(prefix):
            value = value[len(prefix) :]
            break
    for source, target in CLAIM_TEXT_REPLACEMENTS.items():
        value = value.replace(source, target)
    return value


def build_claim_key(payload: dict[str, Any]) -> str:
    canonical_text = str(payload.get("canonical_text") or payload.get("text") or "").strip()
    return stable_fingerprint(
        {
            "subject": payload.get("subject", ""),
            "syllabus_node_id": payload.get("syllabus_node_id") or payload.get("concept_id") or "",
            "claim_type": payload.get("claim_type", ""),
            "canonical_text": canonicalize_claim_text(canonical_text),
        }
    )


def build_relation_key(payload: dict[str, Any]) -> str:
    claim_ids = sorted(str(item).strip() for item in list(payload.get("claim_ids") or []) if str(item).strip())
    return stable_fingerprint(
        {
            "subject": payload.get("subject", ""),
            "syllabus_node_id": payload.get("syllabus_node_id", ""),
            "relation_type": payload.get("relation_type") or payload.get("conflict_type") or "",
            "claim_ids": claim_ids,
        }
    )


def merge_manual_resolution(existing: dict[str, Any] | None, incoming: dict[str, Any]) -> dict[str, Any]:
    payload = dict(incoming or {})
    current = dict(existing or {})
    resolution = current.get("resolution")
    if isinstance(resolution, dict) and any(str(value).strip() for value in resolution.values() if value is not None):
        payload["resolution"] = dict(resolution)
        if current.get("status"):
            payload["status"] = current["status"]
        if current.get("resolved_at") and not payload.get("resolved_at"):
            payload["resolved_at"] = current["resolved_at"]
        if current.get("resolved_by") and not payload.get("resolved_by"):
            payload["resolved_by"] = current["resolved_by"]
        if isinstance(current.get("review_history"), list) and not payload.get("review_history"):
            payload["review_history"] = list(current["review_history"])
        for key in ("review_status", "review_decision", "review_note", "reviewed_at"):
            if current.get(key) and not payload.get(key):
                payload[key] = current[key]
    return payload


def load_all_json(dir_path: Path) -> list[dict[str, Any]]:
    payloads: list[dict[str, Any]] = []
    if not dir_path.exists():
        return payloads
    for path in sorted(dir_path.glob("*.json")):
        try:
            payloads.append(load_json(path))
        except Exception:
            continue
    return payloads


def scan_json_files(dir_path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    if not dir_path.exists():
        return records
    for path in sorted(dir_path.glob("*.json")):
        try:
            stat = path.stat()
        except OSError:
            continue
        records.append(
            {
                "path": path,
                "name": path.name,
                "size": int(stat.st_size),
                "mtime_ns": int(getattr(stat, "st_mtime_ns", int(stat.st_mtime * 1_000_000_000))),
            }
        )
    return records


def register_source_material(
    *,
    subject: str,
    source_name: str,
    material_type: str,
    material_path: Path,
    edition: str = "",
    include_paths: list[Path] | None = None,
    default: Path | None = None,
) -> dict[str, Any]:
    layout = ensure_kb_layout(default)
    fingerprint = stable_fingerprint(
        {
            "subject": subject,
            "source_name": source_name,
            "edition": edition,
            "material_type": material_type,
            "material_path": str(material_path.resolve()),
        }
    )
    source_payload: dict[str, Any] | None = None
    for payload in load_all_json(layout["sources"]):
        if payload.get("source_fingerprint") == fingerprint:
            source_payload = payload
            break
    if source_payload is None:
        source_id = allocate_kb_id("source", subject, default)
        source_payload = {
            "source_id": source_id,
            "subject": subject,
            "source_name": source_name,
            "edition": edition,
            "material_type": material_type,
            "source_path": str(material_path),
            "source_fingerprint": fingerprint,
            "created_at": now_iso(),
            "updated_at": now_iso(),
            "status": "active",
            "file_count": 0,
            "image_count": 0,
            "chapter_ids": [],
            "files": [],
        }
    files = [Path(path) for path in include_paths] if include_paths else list_material_files(material_path)
    existing_files = {
        (item.get("relative_path"), item.get("sha256")): item
        for item in source_payload.get("files", [])
    }
    file_items: list[dict[str, Any]] = []
    for file_path in files:
        relative_path = file_path.relative_to(material_path).as_posix()
        sha_value = sha256_for_file(file_path)
        existing = existing_files.get((relative_path, sha_value))
        file_id = existing.get("file_id") if existing else allocate_kb_id("file", subject, default)
        file_payload = {
            "file_id": file_id,
            "source_id": source_payload["source_id"],
            "subject": subject,
            "relative_path": relative_path,
            "absolute_path": str(file_path),
            "sha256": sha_value,
            "size_bytes": file_path.stat().st_size,
            "suffix": file_path.suffix.lower(),
            "is_image": file_path.suffix.lower() in IMAGE_EXTS,
            "updated_at": now_iso(),
        }
        validate_entity_contract("file", file_payload)
        save_json(layout["manifest_files"] / f"{file_id}.json", file_payload)
        file_items.append(file_payload)
    source_payload["source_path"] = str(material_path)
    source_payload["file_count"] = len(file_items)
    source_payload["image_count"] = sum(1 for item in file_items if item["is_image"])
    source_payload["files"] = file_items
    source_payload["updated_at"] = now_iso()
    validate_entity_contract("source", source_payload)
    save_json(layout["sources"] / f"{source_payload['source_id']}.json", source_payload)
    save_json(layout["manifest_sources"] / f"{source_payload['source_id']}.json", source_payload)
    return source_payload


def register_chapter_manifest(
    context: dict[str, Any],
    *,
    source_payload: dict[str, Any],
    default: Path | None = None,
) -> dict[str, Any]:
    layout = ensure_kb_layout(default)
    chapter_id = str(context.get("chapter_id") or "").strip()
    if chapter_id:
        chapter_payload = load_json_or_default(layout["manifest_chapters"] / f"{chapter_id}.json", {})
        if chapter_payload and "chapter_id" not in chapter_payload:
            chapter_payload = {}
    else:
        fingerprint = stable_fingerprint(
            {
                "source_id": source_payload["source_id"],
                "chapter_title": context.get("chapter_title", ""),
                "batch_id": context.get("batch_id", ""),
                "material_path": context.get("material_path", ""),
            }
        )
        chapter_payload = {}
        for item in load_all_json(layout["manifest_chapters"]):
            if item.get("chapter_fingerprint") == fingerprint:
                chapter_payload = item
                break
        if not chapter_payload:
            chapter_payload = {"chapter_fingerprint": fingerprint}
    if not chapter_payload:
        chapter_payload = {}
    if "chapter_id" not in chapter_payload:
        chapter_payload["chapter_id"] = allocate_kb_id("chapter", context["subject"], default)
    if "chapter_fingerprint" not in chapter_payload:
        chapter_payload["chapter_fingerprint"] = stable_fingerprint(
            {
                "source_id": source_payload["source_id"],
                "chapter_title": context.get("chapter_title", ""),
                "batch_id": context.get("batch_id", ""),
                "material_path": context.get("material_path", ""),
            }
        )
    chapter_payload.setdefault("created_at", now_iso())
    chapter_payload.update(
        {
            "source_id": source_payload["source_id"],
            "subject": context["subject"],
            "source_name": context.get("source_name", ""),
            "chapter_title": context.get("chapter_title", context.get("scope", "")),
            "chapter_slug": context.get("chapter_slug", ""),
            "batch_id": context.get("batch_id", ""),
            "context_json_path": context.get("context_json_path", ""),
            "batch_output_dir": context.get("batch_output_dir", context.get("content_output_dir", "")),
            "material_path": context.get("material_path", ""),
            "mode": context.get("mode", ""),
            "page_sequence_mode": context.get("page_sequence_mode", "manual"),
            "start_page_number": context.get("start_page_number"),
            "page_number_source": context.get("page_number_source", "manual"),
            "image_count": int(context.get("image_count", 0) or 0),
            "updated_at": now_iso(),
        }
    )
    save_json(layout["manifest_chapters"] / f"{chapter_payload['chapter_id']}.json", chapter_payload)
    chapter_ids = [item for item in source_payload.get("chapter_ids", []) if item]
    if chapter_payload["chapter_id"] not in chapter_ids:
        chapter_ids.append(chapter_payload["chapter_id"])
    source_payload["chapter_ids"] = chapter_ids
    source_payload["updated_at"] = now_iso()
    save_json(layout["sources"] / f"{source_payload['source_id']}.json", source_payload)
    save_json(layout["manifest_sources"] / f"{source_payload['source_id']}.json", source_payload)
    return chapter_payload


def register_chunk_manifests(
    context: dict[str, Any],
    chunks: list[dict[str, Any]],
    *,
    default: Path | None = None,
) -> list[dict[str, Any]]:
    layout = ensure_kb_layout(default)
    chapter_id = str(context.get("chapter_id") or "").strip()
    if not chapter_id:
        raise ValueError("chapter_id missing when registering chunk manifests")
    existing = {
        item.get("logical_chunk_id"): item
        for item in load_all_json(layout["manifest_chunks"])
        if item.get("chapter_id") == chapter_id
    }
    registered: list[dict[str, Any]] = []
    for chunk in chunks:
        logical_chunk_id = str(chunk.get("chunk_id", "")).strip()
        payload = existing.get(logical_chunk_id, {})
        chunk_kb_id = payload.get("chunk_kb_id") or chunk.get("chunk_kb_id") or allocate_kb_id("chunk", context["subject"], default)
        chunk_payload = {
            "chunk_kb_id": chunk_kb_id,
            "chapter_id": chapter_id,
            "source_id": context.get("source_id", ""),
            "subject": context["subject"],
            "logical_chunk_id": logical_chunk_id,
            "chunk_id": logical_chunk_id,
            "chunk_title": chunk.get("chunk_title", ""),
            "chunk_index": chunk.get("chunk_index"),
            "image_start": chunk.get("image_start"),
            "image_end": chunk.get("image_end"),
            "page_start": chunk.get("page_start"),
            "page_end": chunk.get("page_end"),
            "section_guess": chunk.get("section_guess", ""),
            "focus_hint": chunk.get("focus_hint", ""),
            "formula_hints": chunk.get("formula_hints", []),
            "example_hints": chunk.get("example_hints", []),
            "question_prompt_hints": chunk.get("question_prompt_hints", []),
            "needs_review": bool(chunk.get("needs_review", False)),
            "context_json_path": context.get("context_json_path", ""),
            "updated_at": now_iso(),
        }
        save_json(layout["manifest_chunks"] / f"{chunk_kb_id}.json", chunk_payload)
        registered.append(chunk_payload)
    return registered


def vault_root_from_context_path(context_path: Path) -> Path:
    context_path = Path(context_path)
    for candidate in [context_path.parent, *context_path.parents]:
        if (candidate / INDEX_DIRNAME).exists():
            return candidate
    return current_vault_root()


def common_path_for(paths: list[Path]) -> Path | None:
    resolved = [Path(path).resolve() for path in paths if str(path)]
    if not resolved:
        return None
    try:
        return Path(os.path.commonpath([str(path) for path in resolved]))
    except ValueError:
        return None


def clear_kb_business_data(
    *,
    preserve_syllabus: bool = False,
    preserve_learner: bool = True,
    default: Path | None = None,
) -> dict[str, int]:
    layout = ensure_kb_layout(default)
    removed = {"files": 0, "dirs": 0}
    targets = [
        layout["manifest_sources"],
        layout["manifest_files"],
        layout["manifest_chapters"],
        layout["manifest_chunks"],
        layout["sources"],
        layout["evidence"],
        layout["claims"],
        layout["conflicts"],
        layout["indexes"],
        layout["runs"],
        layout["review_syllabus_mapping"],
    ]
    if not preserve_syllabus:
        targets.append(layout["syllabus"])
    if not preserve_learner:
        targets.append(layout["learner"])
    root = layout["root"].resolve()
    for target in targets:
        target = target.resolve()
        try:
            target.relative_to(root)
        except ValueError as exc:
            raise ValueError(f"refusing to clear target outside .kaoyan-kb: {target}") from exc
        if not target.exists():
            continue
        for child in sorted(target.rglob("*"), key=lambda item: len(item.parts), reverse=True):
            if child.is_file() and child.suffix.lower() in {".json", ".jsonl"}:
                child.unlink(missing_ok=True)
                removed["files"] += 1
            elif child.is_dir() and not any(child.iterdir()):
                child.rmdir()
                removed["dirs"] += 1
        target.mkdir(parents=True, exist_ok=True)
    return removed


def learner_file_map(default: Path | None = None) -> dict[str, Path]:
    layout = ensure_kb_layout(default)
    learner_root = layout["learner"]
    return {
        "root": learner_root,
        "events": learner_root / "learner_events.jsonl",
        "learner_model": learner_root / "learner_model.json",
        "question_history": learner_root / "question_history.json",
        "error_log": learner_root / "error_log.json",
        "review_history": learner_root / "review_history.json",
        "refinement_queue": learner_root / "refinement_queue.json",
        "distillation_candidates": learner_root / "distillation_candidates.json",
    }


def iter_context_jsons(
    *,
    subjects: list[str] | None = None,
    vault_root: Path | None = None,
) -> list[Path]:
    allowed: set[str] | None = None
    if subjects:
        allowed = {resolve_subject(item)[0] for item in subjects}
    context_paths: list[Path] = []
    root = vault_root or current_vault_root()
    for path in sorted(root.rglob("00_批次上下文.json")):
        try:
            payload = load_json(path)
        except Exception:
            continue
        subject = str(payload.get("subject") or payload.get("resolved_subject") or payload.get("input_subject") or "").strip()
        if allowed and subject not in allowed:
            continue
        context_paths.append(path)
    return context_paths
