#!/usr/bin/env python3
"""Promote qualifying master-card candidates into formal master cards.

Reads master_card_candidates.json, filters by promotion threshold
(chapter_count >= 2 and stable_summary non-empty), then:

1. Generates formal master-card Markdown files in 99_索引与状态/20_主卡片/
2. Generates an index page 00_主卡片索引.md
3. Generates master_card_registry.json for downstream consumption
4. Injects bidirectional backlinks into chapter Q&A entries and knowledge cards
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

from common import INDEX_DIRNAME, default_vault_root_arg, load_json, sanitize_name, save_json

MASTER_CANDIDATE_JSON = "master_card_candidates.json"
MASTER_REGISTRY_JSON = "master_card_registry.json"
MASTER_CARD_DIR = "20_主卡片"
MASTER_INDEX_MD = "00_主卡片索引.md"
BACKLINK_MARKER = "<!-- master-card-backlink -->"
QA_ENTRY_NAME = "03_知识点问答入口.md"
CARD_DIR_NAME = "40_知识点卡片"

MIN_CHAPTER_COUNT = 2


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--vault-root", default=default_vault_root_arg())
    parser.add_argument("--min-chapters", type=int, default=MIN_CHAPTER_COUNT)
    parser.add_argument("--format", choices=("text", "json"), default="text")
    return parser.parse_args()


def wiki_link_for(path: str | Path, vault_root: Path) -> str:
    relative = Path(path).relative_to(vault_root).with_suffix("")
    return f"[[{relative.as_posix()}]]"


def is_promotable(candidate: dict, min_chapters: int) -> bool:
    chapter_count = int(candidate.get("chapter_count", 0))
    stable_summary = str(candidate.get("stable_summary", "")).strip()
    return chapter_count >= min_chapters and len(stable_summary) > 0


def card_file_name(candidate: dict, index: int) -> str:
    concept = sanitize_name(
        str(candidate.get("suggested_master_card_name", "")).strip()
        or str(candidate.get("concept_name", "")).strip()
        or "主卡片"
    )
    return f"{index:02d}_{concept}.md"


def render_master_card(candidate: dict, vault_root: Path) -> str:
    name = (
        str(candidate.get("suggested_master_card_name", "")).strip()
        or str(candidate.get("concept_name", "")).strip()
    )
    lines = [
        f"# {name}",
        "",
        f"- 质检状态：待质检",
        f"- 收束范围：{candidate.get('consolidation_scope', '')}",
        f"- 涉及章节数：{candidate.get('chapter_count', 0)}",
        f"- 当前来源卡片数：{len(candidate.get('source_card_files', []))}",
    ]

    # Stable definition
    stable_summary = str(candidate.get("stable_summary", "")).strip()
    lines.extend(["", "## 一句话定义", ""])
    lines.append(stable_summary if stable_summary else "待补充")

    # Stable rule
    stable_rule = str(candidate.get("stable_rule", "")).strip()
    lines.extend(["", "## 关键规则", ""])
    lines.append(f"- {stable_rule}" if stable_rule else "- 待补充")

    # Aliases
    aliases = candidate.get("aliases_to_merge", [])
    if aliases:
        lines.extend(["", "## 待合并别名", ""])
        for alias in aliases[:8]:
            lines.append(f"- {alias}")

    # Chapter backlinks
    chapters = candidate.get("chapters", [])
    ref_entries = candidate.get("reference_entries", [])
    if chapters:
        lines.extend(["", "## 涉及章节", ""])
        for chapter in chapters:
            subject = str(chapter.get("subject", "")).strip()
            chapter_title = str(chapter.get("chapter_title", "")).strip()
            # Try to find matching reference entry for wiki link
            ref = _find_ref_for_chapter(ref_entries, subject, chapter_title)
            if ref:
                body = str(ref.get("chapter_body", "")).strip()
                qa = str(ref.get("question_entry", "")).strip()
                body_link = wiki_link_for(body, vault_root) if body and Path(body).exists() else ""
                qa_link = wiki_link_for(qa, vault_root) if qa and Path(qa).exists() else ""
                links = " / ".join(link for link in [body_link, qa_link] if link)
                lines.append(f"- {subject} - {chapter_title}：{links}" if links else f"- {subject} - {chapter_title}")
            else:
                lines.append(f"- {subject} - {chapter_title}")

    # Source card files
    source_cards = candidate.get("source_card_files", [])
    if source_cards:
        lines.extend(["", "## 来源卡片", ""])
        for card in source_cards[:10]:
            lines.append(f"- {card}")

    # Consolidation notes
    notes = candidate.get("consolidation_notes", [])
    if notes:
        lines.extend(["", "## 收束建议", ""])
        for note in notes[:5]:
            lines.append(f"- {note}")

    # Followup questions
    followups = candidate.get("followup_questions", [])
    if followups:
        lines.extend(["", "## 后续可问", ""])
        for q in followups[:5]:
            lines.append(f"- {q}")

    return "\n".join(lines).rstrip() + "\n"


def _find_ref_for_chapter(
    ref_entries: list[dict], subject: str, chapter_title: str
) -> dict | None:
    for ref in ref_entries:
        if (
            str(ref.get("subject", "")).strip() == subject
            and str(ref.get("chapter_title", "")).strip() == chapter_title
        ):
            return ref
    return None


def render_index(
    card_paths: list[Path], candidates: list[dict], vault_root: Path
) -> str:
    lines = [
        "# 主卡片索引",
        "",
        "- 用途：跨章节稳定概念的正式主卡片，由系统自动从候选中提升。",
        "- 提升门槛：涉及章节数 ≥ 2 且有稳定定义。",
        "",
        f"- 当前主卡片数：{len(card_paths)}",
        "",
    ]
    if not card_paths:
        lines.append("- 当前还没有达到提升门槛的主卡片。")
        return "\n".join(lines) + "\n"

    lines.extend([
        "| 主卡片 | 涉及章节 | 来源卡片数 | 质检状态 |",
        "| --- | --- | --- | --- |",
    ])
    for path, candidate in zip(card_paths, candidates):
        lines.append(
            f"| {wiki_link_for(path, vault_root)} | {candidate.get('chapter_count', 0)} | {len(candidate.get('source_card_files', []))} | 待质检 |"
        )

    return "\n".join(lines).rstrip() + "\n"


def build_registry(
    card_paths: list[Path], candidates: list[dict], backlink_log: list[dict]
) -> dict:
    promoted: list[dict] = []
    for path, candidate in zip(card_paths, candidates):
        backlinked = [
            entry for entry in backlink_log
            if entry.get("concept_name") == candidate.get("concept_name")
        ]
        promoted.append({
            "concept_name": candidate.get("concept_name", ""),
            "suggested_master_card_name": candidate.get("suggested_master_card_name", ""),
            "master_card_path": str(path),
            "theme_key": candidate.get("theme_key", ""),
            "chapter_count": candidate.get("chapter_count", 0),
            "stable_summary": candidate.get("stable_summary", ""),
            "stable_rule": candidate.get("stable_rule", ""),
            "source_card_files": candidate.get("source_card_files", []),
            "chapters": candidate.get("chapters", []),
            "aliases": candidate.get("aliases_to_merge", []),
            "followup_questions": candidate.get("followup_questions", []),
            "quality_status": "待质检",
            "backlinked_entries": [e.get("target_path", "") for e in backlinked],
        })
    return {
        "promoted_cards": promoted,
        "promotion_threshold": {
            "min_chapter_count": MIN_CHAPTER_COUNT,
            "require_stable_summary": True,
        },
    }


# ---------------------------------------------------------------------------
# Bidirectional backlink injection
# ---------------------------------------------------------------------------

def _backlink_section(concept_name: str, master_card_path: Path, vault_root: Path) -> str:
    link = wiki_link_for(master_card_path, vault_root)
    return (
        f"\n{BACKLINK_MARKER}\n"
        f"## 相关主卡片\n\n"
        f"- {concept_name}：{link}\n"
    )


def _has_backlink_marker(text: str) -> bool:
    return BACKLINK_MARKER in text


def _strip_old_backlinks(text: str) -> str:
    """Remove any existing master-card backlink section."""
    marker_pos = text.find(BACKLINK_MARKER)
    if marker_pos < 0:
        return text
    return text[:marker_pos].rstrip() + "\n"


def inject_backlinks(
    candidates: list[dict],
    card_paths: list[Path],
    vault_root: Path,
) -> list[dict]:
    """Inject backlinks into chapter Q&A entries and knowledge cards.

    Returns a log of all injected backlinks.
    """
    log: list[dict] = []

    # Build a mapping: (subject, chapter_title) -> list of (concept_name, master_card_path)
    chapter_to_cards: dict[tuple[str, str], list[tuple[str, Path]]] = {}
    for candidate, master_path in zip(candidates, card_paths):
        concept_name = (
            str(candidate.get("suggested_master_card_name", "")).strip()
            or str(candidate.get("concept_name", "")).strip()
        )
        for chapter in candidate.get("chapters", []):
            key = (
                str(chapter.get("subject", "")).strip(),
                str(chapter.get("chapter_title", "")).strip(),
            )
            chapter_to_cards.setdefault(key, []).append((concept_name, master_path))

    # Also build a mapping for source card files -> master card
    card_file_to_master: dict[str, list[tuple[str, Path]]] = {}
    for candidate, master_path in zip(candidates, card_paths):
        concept_name = (
            str(candidate.get("suggested_master_card_name", "")).strip()
            or str(candidate.get("concept_name", "")).strip()
        )
        for card_file in candidate.get("source_card_files", []):
            card_file = str(card_file).strip()
            if card_file:
                card_file_to_master.setdefault(card_file, []).append(
                    (concept_name, master_path)
                )

    # Load registry for chapter dir resolution
    registry_path = vault_root / INDEX_DIRNAME / "chapter_knowledge_registry.json"
    registry = load_json(registry_path) if registry_path.exists() else {}
    chapter_dirs: dict[tuple[str, str], str] = {}
    for entry in registry.get("chapters", []):
        key = (
            str(entry.get("subject", "")).strip(),
            str(entry.get("chapter_title", "")).strip(),
        )
        chapter_dirs[key] = str(entry.get("chapter_dir", ""))

    # Inject into Q&A entries
    for (subject, chapter_title), cards in chapter_to_cards.items():
        chapter_dir = chapter_dirs.get((subject, chapter_title), "")
        if not chapter_dir:
            continue
        qa_path = Path(chapter_dir) / "50_提问索引" / QA_ENTRY_NAME
        if not qa_path.exists():
            continue

        backlink_lines = _build_multi_card_section(cards, vault_root)
        _inject_into_file(qa_path, backlink_lines)
        for concept_name, _ in cards:
            log.append({
                "concept_name": concept_name,
                "target_path": str(qa_path),
                "target_type": "qa_entry",
            })

    # Inject into knowledge card files
    for (subject, chapter_title), chapter_dir_str in chapter_dirs.items():
        if not chapter_dir_str:
            continue
        card_dir = Path(chapter_dir_str) / CARD_DIR_NAME
        if not card_dir.exists():
            continue
        for card_file_path in sorted(card_dir.glob("*.md")):
            masters = card_file_to_master.get(card_file_path.name)
            if not masters:
                continue
            backlink_lines = _build_multi_card_section(masters, vault_root)
            _inject_into_file(card_file_path, backlink_lines)
            for concept_name, _ in masters:
                log.append({
                    "concept_name": concept_name,
                    "target_path": str(card_file_path),
                    "target_type": "knowledge_card",
                })

    return log


def _build_multi_card_section(
    cards: list[tuple[str, Path]], vault_root: Path
) -> str:
    """Build a backlink section for multiple master cards."""
    lines = [f"\n{BACKLINK_MARKER}", "## 相关主卡片", ""]
    seen: set[str] = set()
    for concept_name, master_path in cards:
        link = wiki_link_for(master_path, vault_root)
        key = f"{concept_name}:{master_path}"
        if key not in seen:
            seen.add(key)
            lines.append(f"- {concept_name}：{link}")
    return "\n".join(lines) + "\n"


def _inject_into_file(target_path: Path, backlink_section: str) -> None:
    """Inject or replace backlink section in a file (idempotent)."""
    try:
        text = target_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return
    cleaned = _strip_old_backlinks(text)
    target_path.write_text(cleaned + backlink_section, encoding="utf-8")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    args = parse_args()
    vault_root = Path(args.vault_root)
    index_root = vault_root / INDEX_DIRNAME

    # Load candidates
    candidate_path = index_root / MASTER_CANDIDATE_JSON
    if not candidate_path.exists():
        if args.format == "json":
            print(json.dumps({"promoted_cards": [], "skipped": "no candidates file"}, ensure_ascii=False))
        else:
            print("当前没有主卡片候选文件，跳过提升。")
        return 0

    payload = load_json(candidate_path)
    all_candidates = payload.get("candidates", [])

    # Filter promotable
    promotable = [c for c in all_candidates if is_promotable(c, args.min_chapters)]

    # Prepare output directory
    master_dir = index_root / MASTER_CARD_DIR
    master_dir.mkdir(parents=True, exist_ok=True)

    # Clean old cards (except index)
    for old_path in sorted(master_dir.glob("*.md")):
        if old_path.name != MASTER_INDEX_MD:
            old_path.unlink()

    # Generate cards
    card_paths: list[Path] = []
    for idx, candidate in enumerate(promotable, start=1):
        fname = card_file_name(candidate, idx)
        card_path = master_dir / fname
        card_path.write_text(render_master_card(candidate, vault_root), encoding="utf-8")
        card_paths.append(card_path)

    # Generate index
    index_path = master_dir / MASTER_INDEX_MD
    index_path.write_text(
        render_index(card_paths, promotable, vault_root), encoding="utf-8"
    )

    # Inject backlinks
    backlink_log = inject_backlinks(promotable, card_paths, vault_root)

    # Build and save registry
    registry = build_registry(card_paths, promotable, backlink_log)
    save_json(index_root / MASTER_REGISTRY_JSON, registry)

    # Output
    if args.format == "json":
        print(json.dumps({
            "promoted_count": len(promotable),
            "total_candidates": len(all_candidates),
            "backlinks_injected": len(backlink_log),
            "promoted_cards": [
                {
                    "concept_name": c.get("concept_name", ""),
                    "chapter_count": c.get("chapter_count", 0),
                    "path": str(p),
                }
                for c, p in zip(promotable, card_paths)
            ],
        }, ensure_ascii=False, indent=2))
    else:
        print(f"主卡片提升完成：{len(promotable)}/{len(all_candidates)} 张候选达到提升门槛。")
        print(f"回链注入：{len(backlink_log)} 处。")
        for card, path in zip(promotable, card_paths):
            print(f"  - {card.get('concept_name', '')}：{path.name}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
