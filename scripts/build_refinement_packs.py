#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

from common import INDEX_DIRNAME, default_vault_root_arg, load_json, sanitize_name

QUEUE_JSON = "refinement_queue.json"
REGISTRY_JSON = "chapter_knowledge_registry.json"
PACK_DIR = "17_待精修包"
PACK_INDEX_MD = "00_待精修包索引.md"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--vault-root", default=default_vault_root_arg())
    parser.add_argument("--topn", type=int, default=12)
    return parser.parse_args()


def wiki_link_for(path: str | Path, vault_root: Path) -> str:
    relative = Path(path).relative_to(vault_root).with_suffix("")
    return f"[[{relative.as_posix()}]]"


def chapter_registry_map(registry_payload: dict) -> dict[tuple[str, str], dict]:
    return {
        (str(item.get("subject", "")).strip(), str(item.get("chapter_title", "")).strip()): item
        for item in registry_payload.get("chapters", [])
    }


def pack_name(item: dict, index: int) -> str:
    subject = sanitize_name(str(item.get("subject", "")).strip() or "未知学科")
    chapter = sanitize_name(str(item.get("chapter_title", "")).strip() or "未命名章节")
    item_name = sanitize_name(str(item.get("item_name", "")).strip() or "待精修")
    return f"{index:02d}_{subject}_{chapter}_{item_name}.md"


def render_pack(item: dict, chapter_entry: dict | None, vault_root: Path) -> str:
    lines = [
        f"# {item.get('item_name', '')}",
        "",
        f"- 学科：{item.get('subject', '')}",
        f"- 章节：{item.get('chapter_title', '')}",
        f"- 精修类型：{item.get('item_type', '')}",
        f"- 当前原因：{item.get('reason', '') or '待补充'}",
        f"- 下一步：{item.get('next_step', '') or '待补充'}",
    ]
    source_path = str(item.get("source_path", "")).strip()
    if source_path:
        lines.append(f"- 对应文件：{wiki_link_for(source_path, vault_root)}")
    card_link = str(item.get("card_link", "")).strip()
    if card_link:
        lines.append(f"- 优先回看卡片：{card_link}")
    if chapter_entry:
        body = str(chapter_entry.get("chapter_body", "")).strip()
        question_entry = str(chapter_entry.get("question_entry", "")).strip()
        if body:
            lines.append(f"- 章节正文：{wiki_link_for(body, vault_root)}")
        if question_entry:
            lines.append(f"- 提问入口：{wiki_link_for(question_entry, vault_root)}")
    lines.extend(["", "## 这次精修先做什么", ""])
    lines.append(item.get("next_step", "") or "待补充")
    lines.extend(["", "## 精修时重点盯什么", ""])
    lines.append(f"- 先解决：{item.get('reason', '') or '待补充'}")
    if item.get("item_type") == "priority_card":
        lines.append("- 先把定义、规则、易混点压缩成能直接回忆的一张卡。")
    elif item.get("item_type") == "chunk_write_gap":
        lines.append("- 先把这一段整理成自己能落笔复述的口径，再回到题型入口。")
    elif item.get("item_type") == "weak_spot":
        lines.append("- 先反向对照概念卡片，补一条自己的区分口径。")
    else:
        lines.append("- 先补掉章节结构性缺口，再考虑扩写卡片和追问。")
    if chapter_entry:
        question_prompts = chapter_entry.get("question_prompts", [])
        weak_spots = chapter_entry.get("weak_spots", [])
        if question_prompts:
            lines.extend(["", "## 这章可顺手继续追问", ""])
            for prompt in question_prompts[:5]:
                lines.append(f"- {prompt}")
        if weak_spots:
            lines.extend(["", "## 这章高频易混点", ""])
            for weak in weak_spots[:5]:
                lines.append(f"- {weak}")
    return "\n".join(lines).rstrip() + "\n"


def render_index(pack_paths: list[Path], items: list[dict], vault_root: Path) -> str:
    lines = [
        "# 待精修包索引",
        "",
        "- 用途：把待精修队列展开成可以直接回看的学习包，减少在多个章节状态页之间来回翻。",
        "",
    ]
    if not pack_paths:
        lines.append("- 当前还没有待精修包。")
        return "\n".join(lines) + "\n"
    for path, item in zip(pack_paths, items):
        lines.append(
            f"- {wiki_link_for(path, vault_root)}：{item.get('subject', '')}-{item.get('chapter_title', '')} / {item.get('item_type', '')}"
        )
    return "\n".join(lines).rstrip() + "\n"


def main() -> int:
    args = parse_args()
    vault_root = Path(args.vault_root)
    index_root = vault_root / INDEX_DIRNAME
    queue_payload = load_json(index_root / QUEUE_JSON)
    registry_payload = load_json(index_root / REGISTRY_JSON)
    chapter_map = chapter_registry_map(registry_payload)

    pack_root = index_root / PACK_DIR
    pack_root.mkdir(parents=True, exist_ok=True)
    for path in sorted(pack_root.glob("*.md")):
        if path.name != PACK_INDEX_MD:
            path.unlink()

    items = queue_payload.get("items", [])[: max(1, args.topn)]
    pack_paths: list[Path] = []
    for index, item in enumerate(items, start=1):
        chapter_entry = chapter_map.get((str(item.get("subject", "")).strip(), str(item.get("chapter_title", "")).strip()))
        path = pack_root / pack_name(item, index)
        path.write_text(render_pack(item, chapter_entry, vault_root), encoding="utf-8")
        pack_paths.append(path)

    (pack_root / PACK_INDEX_MD).write_text(render_index(pack_paths, items, vault_root), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
