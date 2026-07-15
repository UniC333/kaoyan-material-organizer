#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from build_saved_qa_registry import build_chapter_summaries, iter_saved_notes, parse_note, render_registry
from common import INDEX_DIRNAME, default_vault_root_arg, learner_file_map, now_iso, resolve_subject, sanitize_name, save_json, save_text, stable_fingerprint
from learner_events import append_event, load_events, rebuild_views


CONTRACT_VERSION = "r54.conversation-distillation.v1"
SESSION_ID_PATTERN = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE)
MAX_MESSAGES = 200
MAX_TEXT_CHARS = 200_000
CONTROL_PREFIXES = (
    "<environment_context>",
    "<apps_instructions>",
    "<plugins_instructions>",
    "<recommended_plugins>",
    "# AGENTS.md instructions for ",
)
ALLOWED_TEACHING_SCOPES = {"topic", "chapter", "subject"}
DEFAULT_REVIEW_DAYS = 90


class DistillationError(ValueError):
    pass


def _inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _session_file(session_id: str, codex_home: Path) -> Path:
    if not SESSION_ID_PATTERN.fullmatch(session_id):
        raise DistillationError("invalid session id")
    sessions_root = (codex_home / "sessions").resolve()
    if not sessions_root.exists():
        raise DistillationError(f"session not found: {session_id}")
    matches = [
        path.resolve()
        for path in sessions_root.rglob(f"*{session_id}.jsonl")
        if path.is_file() and path.name.endswith(f"{session_id}.jsonl")
    ]
    matches = [path for path in matches if _inside(path, sessions_root)]
    if not matches:
        raise DistillationError(f"session not found: {session_id}")
    if len(matches) > 1:
        raise DistillationError(f"multiple session files found: {session_id}")
    return matches[0]


def _message_text(content: Any) -> str:
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for item in content:
        if not isinstance(item, dict) or item.get("type") not in {"input_text", "output_text"}:
            continue
        text = str(item.get("text", "")).strip()
        if text:
            parts.append(text)
    return "\n".join(parts).strip()


def load_codex_session_bundle(session_id: str, *, codex_home: Path | None = None) -> dict[str, Any]:
    home = (codex_home or Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))).resolve()
    path = _session_file(session_id, home)
    messages: list[dict[str, str]] = []
    total_chars = 0
    try:
        with path.open("r", encoding="utf-8", errors="strict") as handle:
            for line_number, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise DistillationError(f"malformed session JSONL at line {line_number}") from exc
                if record.get("type") != "response_item":
                    continue
                payload = record.get("payload")
                if not isinstance(payload, dict) or payload.get("type") != "message":
                    continue
                role = str(payload.get("role", "")).strip()
                if role not in {"user", "assistant"}:
                    continue
                text = _message_text(payload.get("content"))
                if not text or text.startswith(CONTROL_PREFIXES):
                    continue
                if messages and messages[-1]["role"] == role and messages[-1]["text"] == text:
                    continue
                total_chars += len(text)
                if len(messages) >= MAX_MESSAGES or total_chars > MAX_TEXT_CHARS:
                    raise DistillationError("session exceeds bounded distillation limits")
                messages.append(
                    {
                        "id": str(payload.get("id", "")).strip() or f"line-{line_number}",
                        "role": role,
                        "timestamp": str(record.get("timestamp", "")).strip(),
                        "text": text,
                    }
                )
    except UnicodeDecodeError as exc:
        raise DistillationError("session is not valid UTF-8") from exc
    except OSError as exc:
        raise DistillationError(f"session cannot be read: {session_id}") from exc
    if not messages:
        raise DistillationError("session contains no eligible user or assistant messages")
    digest_source = json.dumps(messages, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return {
        "contract_version": CONTRACT_VERSION,
        "session_id": session_id,
        "source_kind": "codex_session",
        "message_count": len(messages),
        "source_digest": hashlib.sha256(digest_source.encode("utf-8")).hexdigest(),
        "messages": messages,
    }


def _required_text(payload: dict[str, Any], key: str, *, limit: int = 120) -> str:
    value = str(payload.get(key, "")).strip()
    if not value:
        raise DistillationError(f"{key} is required")
    if len(value) > limit:
        raise DistillationError(f"{key} exceeds length limit")
    if key in {"chapter_title", "topic"} and (".." in value or "/" in value or "\\" in value or "\x00" in value):
        raise DistillationError(f"{key} contains an unsafe path fragment")
    return value


def _string_list(payload: dict[str, Any], key: str, *, required: bool = False, limit: int = 8) -> list[str]:
    raw = payload.get(key, [])
    if not isinstance(raw, list):
        raise DistillationError(f"{key} must be a list")
    values: list[str] = []
    for item in raw:
        text = str(item).strip()
        if not text:
            continue
        if len(text) > 500:
            raise DistillationError(f"{key} item exceeds length limit")
        if text not in values:
            values.append(text)
    if required and not values:
        raise DistillationError(f"{key} must contain at least one item")
    if len(values) > limit:
        raise DistillationError(f"{key} exceeds item limit")
    return values


def _unhelpful_routes(payload: dict[str, Any]) -> list[dict[str, str]]:
    raw = payload.get("unhelpful_routes", [])
    if not isinstance(raw, list) or len(raw) > 8:
        raise DistillationError("unhelpful_routes must be a bounded list")
    routes: list[dict[str, str]] = []
    for item in raw:
        if not isinstance(item, dict):
            raise DistillationError("unhelpful_routes items must be objects")
        method = str(item.get("method", "")).strip()
        reason = str(item.get("reason", "")).strip()
        if not method or not reason:
            raise DistillationError("unhelpful_routes requires method and reason")
        if len(method) > 120 or len(reason) > 500:
            raise DistillationError("unhelpful_routes item exceeds length limit")
        routes.append({"method": method, "reason": reason})
    return routes


def _teaching_scope(payload: dict[str, Any]) -> str:
    value = str(payload.get("teaching_scope", "chapter")).strip() or "chapter"
    if value not in ALLOWED_TEACHING_SCOPES:
        raise DistillationError("teaching_scope must be topic, chapter, or subject")
    return value


def _review_after(payload: dict[str, Any], created_at: str) -> str:
    value = str(payload.get("review_after", "")).strip()
    if value:
        try:
            date.fromisoformat(value)
        except ValueError as exc:
            raise DistillationError("review_after must be an ISO date") from exc
        return value
    try:
        created_date = date.fromisoformat(created_at[:10])
    except ValueError as exc:
        raise DistillationError("created_at must start with an ISO date") from exc
    return (created_date + timedelta(days=DEFAULT_REVIEW_DAYS)).isoformat()


def build_distillation_candidate(bundle: dict[str, Any], payload: dict[str, Any], *, now: str | None = None) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise DistillationError("candidate payload must be an object")
    subject = _required_text(payload, "subject", limit=30)
    resolve_subject(subject)
    chapter_title = _required_text(payload, "chapter_title")
    topic = _required_text(payload, "topic")
    accepted_core = _string_list(payload, "accepted_core", required=True)
    created_at = now or now_iso()
    source = {
        "kind": "codex_session",
        "session_id": str(bundle.get("session_id", "")),
        "source_digest": str(bundle.get("source_digest", "")),
        "message_ids": [str(item.get("id", "")) for item in bundle.get("messages", []) if str(item.get("id", ""))],
    }
    candidate_id = stable_fingerprint(
        {"kind": "conversation_distillation", "session_id": source["session_id"], "source_digest": source["source_digest"], "topic": topic}
    )
    return {
        "contract_version": CONTRACT_VERSION,
        "candidate_id": candidate_id,
        "status": "draft",
        "subject": subject,
        "chapter_title": chapter_title,
        "topic": topic,
        "accepted_core": accepted_core,
        "derivation_route": _string_list(payload, "derivation_route"),
        "unhelpful_routes": _unhelpful_routes(payload),
        "teaching_preferences": _string_list(payload, "teaching_preferences"),
        "self_checks": _string_list(payload, "self_checks"),
        "next_questions": _string_list(payload, "next_questions"),
        "teaching_scope": _teaching_scope(payload),
        "review_after": _review_after(payload, created_at),
        "supersedes_candidate_ids": _string_list(payload, "supersedes_candidate_ids"),
        "source": source,
        "created_at": created_at,
        "updated_at": created_at,
        "fact_write_allowed": False,
    }


def render_candidate_note(candidate: dict[str, Any]) -> str:
    lines = [
        f"# {candidate['topic']}",
        "",
        f"- 学科：{candidate['subject']}",
        f"- 章节：{candidate['chapter_title']}",
        f"- 记录日期：{str(candidate['created_at'])[:10]}",
        f"- 来源会话：{candidate['source']['session_id']}",
        f"- 理解候选：{candidate['candidate_id']}",
        f"- 教学偏好范围：{candidate['teaching_scope']}",
        f"- 建议复核日期：{candidate['review_after']}",
        "",
        "## 当前回答",
        "",
        candidate["accepted_core"][0],
        "",
        "## 我的核心抓手",
        "",
    ]
    lines.extend(f"- {item}" for item in candidate["accepted_core"])
    if candidate["derivation_route"]:
        lines.extend(["", "## 最短推导路线", ""])
        lines.extend(f"- {item}" for item in candidate["derivation_route"])
    if candidate["unhelpful_routes"]:
        lines.extend(["", "## 暂不采用的解释", ""])
        lines.extend(f"- {item['method']}：{item['reason']}" for item in candidate["unhelpful_routes"])
    if candidate["teaching_preferences"]:
        lines.extend(["", "## 以后怎样讲", ""])
        lines.extend(f"- {item}" for item in candidate["teaching_preferences"])
    if candidate["self_checks"]:
        lines.extend(["", "## 自测", ""])
        lines.extend(f"- {item}" for item in candidate["self_checks"])
    if candidate["next_questions"]:
        lines.extend(["", "## 下一步可继续问", ""])
        lines.extend(f"- {item}" for item in candidate["next_questions"])
    return "\n".join(lines).rstrip() + "\n"


def _candidate_store() -> tuple[Path, dict[str, Any]]:
    path = learner_file_map()["distillation_candidates"]
    if path.exists():
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise DistillationError("distillation candidate store is malformed") from exc
    else:
        payload = {"contract_version": CONTRACT_VERSION, "items": []}
    if not isinstance(payload.get("items"), list):
        raise DistillationError("distillation candidate store items must be a list")
    return path, payload


def save_candidate(candidate: dict[str, Any]) -> dict[str, Any]:
    path, store = _candidate_store()
    existing = next((item for item in store["items"] if item.get("candidate_id") == candidate["candidate_id"]), None)
    if existing and existing.get("status") == "published":
        return existing
    store["items"] = [item for item in store["items"] if item.get("candidate_id") != candidate["candidate_id"]]
    store["items"].append(candidate)
    store["items"].sort(key=lambda item: (item.get("status") != "draft", item.get("created_at", ""), item.get("candidate_id", "")))
    save_json(path, store)
    return candidate


def validate_stored_candidate(candidate: Any) -> dict[str, Any]:
    if not isinstance(candidate, dict):
        raise DistillationError("stored candidate must be an object")
    if candidate.get("contract_version") != CONTRACT_VERSION:
        raise DistillationError("stored candidate contract_version is unsupported")
    if candidate.get("fact_write_allowed") is not False:
        raise DistillationError("stored candidate fact_write_allowed must remain false")
    source = candidate.get("source")
    if not isinstance(source, dict) or source.get("kind") != "codex_session":
        raise DistillationError("stored candidate source is invalid")
    session_id = str(source.get("session_id", "")).strip()
    source_digest = str(source.get("source_digest", "")).strip()
    message_ids = source.get("message_ids")
    if not SESSION_ID_PATTERN.fullmatch(session_id):
        raise DistillationError("stored candidate session id is invalid")
    if not re.fullmatch(r"[0-9a-f]{64}", source_digest, re.IGNORECASE):
        raise DistillationError("stored candidate source digest is invalid")
    if not isinstance(message_ids, list) or not message_ids or len(message_ids) > MAX_MESSAGES:
        raise DistillationError("stored candidate message ids are invalid")
    clean_message_ids = [str(item).strip() for item in message_ids]
    if any(not item or len(item) > 200 for item in clean_message_ids):
        raise DistillationError("stored candidate message ids are invalid")
    created_at = str(candidate.get("created_at", "")).strip()
    if not created_at:
        raise DistillationError("stored candidate created_at is required")
    normalized = build_distillation_candidate(
        {
            "session_id": session_id,
            "source_digest": source_digest,
            "messages": [{"id": item} for item in clean_message_ids],
        },
        {
            "subject": candidate.get("subject"),
            "chapter_title": candidate.get("chapter_title"),
            "topic": candidate.get("topic"),
            "accepted_core": candidate.get("accepted_core"),
            "derivation_route": candidate.get("derivation_route"),
            "unhelpful_routes": candidate.get("unhelpful_routes"),
            "teaching_preferences": candidate.get("teaching_preferences"),
            "self_checks": candidate.get("self_checks"),
            "next_questions": candidate.get("next_questions"),
            "teaching_scope": candidate.get("teaching_scope", "chapter"),
            "review_after": candidate.get("review_after", ""),
            "supersedes_candidate_ids": candidate.get("supersedes_candidate_ids", []),
        },
        now=created_at,
    )
    if normalized["candidate_id"] != candidate.get("candidate_id"):
        raise DistillationError("stored candidate id does not match its content")
    return normalized


def _write_index(path: Path, title: str, notes: list[Path], vault_root: Path) -> None:
    lines = [f"# {title}", "", "## 已保存问答", ""]
    if notes:
        for note in notes:
            relative = note.relative_to(vault_root).with_suffix("")
            lines.append(f"- [[{relative.as_posix()}]]")
    else:
        lines.append("- 暂无记录。")
    save_text(path, "\n".join(lines).rstrip() + "\n")


def _rebuild_saved_qa(vault_root: Path) -> None:
    notes = [parse_note(path, vault_root) for path in iter_saved_notes(vault_root)]
    chapters = build_chapter_summaries(notes)
    index_root = vault_root / INDEX_DIRNAME
    save_json(index_root / "saved_qa_registry.json", {"notes": notes, "chapters": chapters})
    save_text(index_root / "13_问答沉淀索引.md", render_registry(notes, chapters))


def publish_candidate(candidate_id: str, *, vault_root: Path, confirmed: bool) -> dict[str, Any]:
    if not confirmed:
        raise DistillationError("explicit --yes confirmation is required")
    store_path, store = _candidate_store()
    candidate_index = next((index for index, item in enumerate(store["items"]) if item.get("candidate_id") == candidate_id), None)
    candidate = store["items"][candidate_index] if candidate_index is not None else None
    if candidate is None:
        raise DistillationError(f"candidate not found: {candidate_id}")
    if candidate.get("status") == "published":
        return {"candidate_id": candidate_id, "note_path": candidate.get("note_path", ""), "already_published": True}
    if candidate.get("status") != "draft":
        raise DistillationError(f"candidate is not publishable: {candidate.get('status', '')}")
    candidate = validate_stored_candidate(candidate)
    store["items"][candidate_index] = candidate

    subject, config = resolve_subject(str(candidate["subject"]))
    root = vault_root.resolve()
    chapter_slug = sanitize_name(str(candidate["chapter_title"]))
    title_slug = sanitize_name(str(candidate["topic"])[:60])
    if not chapter_slug or not title_slug:
        raise DistillationError("candidate cannot resolve a safe note path")
    qa_root = root / config["dir"] / "00_课程入口" / "10_问答沉淀"
    chapter_dir = qa_root / chapter_slug
    note_path = (chapter_dir / f"{str(candidate['created_at'])[:10]}_{title_slug}.md").resolve()
    if not _inside(note_path, root):
        raise DistillationError("resolved note path is outside the vault")
    save_text(note_path, render_candidate_note(candidate))
    chapter_index = chapter_dir / "00_本章问答入口.md"
    _write_index(chapter_index, f"{candidate['chapter_title']}问答入口", sorted(path for path in chapter_dir.glob("*.md") if path.name != chapter_index.name), root)
    subject_index = qa_root / "00_知识问答入口.md"
    _write_index(subject_index, f"{subject}知识问答入口", sorted(qa_root.rglob("00_本章问答入口.md")), root)
    _rebuild_saved_qa(root)

    existing_event = next(
        (
            event
            for event in load_events()
            if event.get("event_type") == "understanding_distilled"
            and dict(event.get("payload") or {}).get("candidate_id") == candidate_id
        ),
        None,
    )
    if existing_event is None:
        append_event(
            subject=subject,
            chapter_title=str(candidate["chapter_title"]),
            event_type="understanding_distilled",
            payload={
                "candidate_id": candidate_id,
                "source_kind": "codex_conversation_distillation",
                "answer_contract_version": CONTRACT_VERSION,
                "answer_mode": "learner_understanding",
                "citation_coverage_ok": True,
                "references": [],
                "syllabus_route": [],
                "fact_write_intent": "",
                "accepted_core": candidate["accepted_core"],
                "derivation_route": candidate["derivation_route"],
                "unhelpful_routes": candidate["unhelpful_routes"],
                "teaching_preferences": candidate["teaching_preferences"],
                "self_checks": candidate["self_checks"],
                "topic": candidate["topic"],
                "teaching_scope": candidate["teaching_scope"],
                "review_after": candidate["review_after"],
                "history_status": "active",
                "supersedes_candidate_ids": candidate["supersedes_candidate_ids"],
                "saved_note": str(note_path),
                "source_session_id": candidate["source"]["session_id"],
                "source_digest": candidate["source"]["source_digest"],
            },
        )
    rebuild_views()

    published_at = now_iso()
    candidate["status"] = "published"
    candidate["published_at"] = published_at
    candidate["updated_at"] = published_at
    candidate["note_path"] = str(note_path)
    save_json(store_path, store)
    return {"candidate_id": candidate_id, "note_path": str(note_path), "already_published": False}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="action", required=True)
    inspect = commands.add_parser("inspect")
    inspect.add_argument("--session-id", required=True)
    inspect.add_argument("--codex-home")
    inspect.add_argument("--format", choices=("json", "quiet"), default="json")
    propose = commands.add_parser("propose")
    propose.add_argument("--session-id", required=True)
    propose.add_argument("--codex-home")
    propose.add_argument("--candidate-json", required=True)
    propose.add_argument("--vault-root", default=default_vault_root_arg())
    propose.add_argument("--format", choices=("json", "quiet"), default="json")
    apply = commands.add_parser("apply")
    apply.add_argument("--candidate-id", required=True)
    apply.add_argument("--vault-root", default=default_vault_root_arg())
    apply.add_argument("--yes", action="store_true")
    apply.add_argument("--format", choices=("json", "quiet"), default="json")
    queue = commands.add_parser("queue")
    queue.add_argument("--format", choices=("json", "quiet"), default="json")
    return parser.parse_args()


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="strict")
    args = parse_args()
    try:
        if args.action == "inspect":
            result = load_codex_session_bundle(args.session_id, codex_home=Path(args.codex_home) if args.codex_home else None)
        elif args.action == "propose":
            bundle = load_codex_session_bundle(args.session_id, codex_home=Path(args.codex_home) if args.codex_home else None)
            try:
                payload = json.loads(Path(args.candidate_json).read_text(encoding="utf-8", errors="strict"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise DistillationError("candidate JSON cannot be read") from exc
            candidate = save_candidate(build_distillation_candidate(bundle, payload))
            result = {"candidate": candidate, "preview_markdown": render_candidate_note(candidate)}
        elif args.action == "apply":
            result = publish_candidate(args.candidate_id, vault_root=Path(args.vault_root), confirmed=bool(args.yes))
        else:
            _, result = _candidate_store()
        if args.format == "json":
            print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except DistillationError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
