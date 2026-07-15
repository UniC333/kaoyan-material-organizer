#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

from common import INDEX_DIRNAME, default_vault_root_arg, load_json, sanitize_name

MASTER_CARD_JSON = "master_card_candidates.json"
DRAFT_DIR = "18_主卡片草稿"
DRAFT_INDEX_MD = "00_主卡片草稿索引.md"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--vault-root", default=default_vault_root_arg())
    parser.add_argument("--topn", type=int, default=10)
    return parser.parse_args()


def wiki_link_for(path: str | Path, vault_root: Path) -> str:
    relative = Path(path).relative_to(vault_root).with_suffix("")
    return f"[[{relative.as_posix()}]]"


def draft_name(candidate: dict, index: int) -> str:
    concept = sanitize_name(str(candidate.get("suggested_master_card_name", "")).strip() or str(candidate.get("concept_name", "")).strip() or "主卡片")
    return f"{index:02d}_{concept}.md"


def render_draft(candidate: dict, vault_root: Path) -> str:
    name = str(candidate.get("suggested_master_card_name", "")).strip() or str(candidate.get("concept_name", "")).strip()
    lines = [
        f"# {name}",
        "",
        f"- 候选来源：{candidate.get('consolidation_scope', '')}",
        f"- 当前概念名：{candidate.get('concept_name', '')}",
        f"- 涉及章节数：{candidate.get('chapter_count', 0)}",
        f"- 当前来源卡片数：{len(candidate.get('source_card_files', []))}",
    ]
    if candidate.get("stable_summary"):
        lines.extend(["", "## 稳定定义草稿", "", candidate["stable_summary"]])
    else:
        lines.extend(["", "## 稳定定义草稿", "", "待补充"])
    if candidate.get("stable_rule"):
        lines.extend(["", "## 稳定规则草稿", "", f"- {candidate['stable_rule']}"])
    else:
        lines.extend(["", "## 稳定规则草稿", "", "- 待补充"])
    if candidate.get("aliases_to_merge"):
        lines.extend(["", "## 待合并别名", ""])
        for alias in candidate["aliases_to_merge"][:6]:
            lines.append(f"- {alias}")
    refs = candidate.get("reference_entries", [])
    if refs:
        lines.extend(["", "## 来源回链", ""])
        for ref in refs[:10]:
            subject = str(ref.get("subject", "")).strip()
            chapter_title = str(ref.get("chapter_title", "")).strip()
            section = str(ref.get("section", "")).strip()
            question_entry = str(ref.get("question_entry", "")).strip()
            chapter_body = str(ref.get("chapter_body", "")).strip()
            card_file = str(ref.get("card_file", "")).strip()
            lines.append(f"- {subject} - {chapter_title} / {section}")
            if chapter_body:
                lines.append(f"  正文：{wiki_link_for(chapter_body, vault_root)}")
            if question_entry:
                lines.append(f"  提问入口：{wiki_link_for(question_entry, vault_root)}")
            if card_file and chapter_body:
                card_path = Path(chapter_body).parent.parent / "40_知识点卡片" / card_file
                if card_path.exists():
                    lines.append(f"  来源卡片：{wiki_link_for(card_path, vault_root)}")
    if candidate.get("consolidation_notes"):
        lines.extend(["", "## 收束建议", ""])
        for note in candidate["consolidation_notes"][:5]:
            lines.append(f"- {note}")
    if candidate.get("followup_questions"):
        lines.extend(["", "## 后续可问", ""])
        for question in candidate["followup_questions"][:5]:
            lines.append(f"- {question}")
    return "\n".join(lines).rstrip() + "\n"


def render_index(draft_paths: list[Path], candidates: list[dict], vault_root: Path) -> str:
    lines = [
        "# 主卡片草稿索引",
        "",
        "- 用途：把可收束的概念簇先落成草稿，后续再决定是否正式提升为通用主卡片。",
        "",
    ]
    if not draft_paths:
        lines.append("- 当前还没有主卡片草稿。")
        return "\n".join(lines) + "\n"
    for path, candidate in zip(draft_paths, candidates):
        lines.append(
            f"- {wiki_link_for(path, vault_root)}：{candidate.get('consolidation_scope', '')} / {candidate.get('chapter_count', 0)} 章 / {len(candidate.get('source_card_files', []))} 张来源卡"
        )
    return "\n".join(lines).rstrip() + "\n"


def main() -> int:
    args = parse_args()
    vault_root = Path(args.vault_root)
    index_root = vault_root / INDEX_DIRNAME
    payload = load_json(index_root / MASTER_CARD_JSON)
    candidates = payload.get("candidates", [])[: max(1, args.topn)]

    draft_root = index_root / DRAFT_DIR
    draft_root.mkdir(parents=True, exist_ok=True)
    for path in sorted(draft_root.glob("*.md")):
        if path.name != DRAFT_INDEX_MD:
            path.unlink()

    draft_paths: list[Path] = []
    for index, candidate in enumerate(candidates, start=1):
        path = draft_root / draft_name(candidate, index)
        path.write_text(render_draft(candidate, vault_root), encoding="utf-8")
        draft_paths.append(path)

    (draft_root / DRAFT_INDEX_MD).write_text(render_index(draft_paths, candidates, vault_root), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
