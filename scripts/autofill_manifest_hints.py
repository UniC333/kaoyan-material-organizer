#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
from pathlib import Path

from common import format_page_label, is_placeholder, load_json, normalize_context, parse_manifest_table, save_json

MANIFEST_NAME = "00_章节图片清单.md"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--context-json", required=True)
    return parser.parse_args()


def clean_chapter_title(title: str) -> str:
    value = str(title or "").strip()
    value = re.sub(r"图片批次.*$", "", value).strip()
    value = re.sub(r"[：:]\s*$", "", value).strip()
    return value or "本章"


def chapter_stub(context: dict) -> str:
    chapter_title = clean_chapter_title(str(context.get("chapter_title") or context.get("scope") or ""))
    material_leaf = Path(str(context.get("material_path", ""))).name
    material_leaf = re.sub(r"^\d+[_-]*", "", material_leaf)
    material_leaf = material_leaf.replace("-原图", "").replace("_原图", "").strip()
    material_leaf = material_leaf.replace("原图", "").replace("截图", "").strip()
    if material_leaf and material_leaf not in chapter_title:
        return f"{chapter_title}{material_leaf}".strip()
    return chapter_title or material_leaf or "本章"


def infer_page_hint(file_name: str) -> str:
    match = re.search(r"[Pp](\d{1,4})", file_name)
    if match:
        return format_page_label(int(match.group(1)))
    return "待补充"


def infer_usage_hint(file_name: str) -> str:
    lowered = file_name.lower()
    if "解析" in file_name or "题解" in file_name:
        return "题解/解析"
    if "题目" in file_name or "习题" in file_name or "练习" in file_name:
        return "习题"
    if "例" in file_name:
        return "例题"
    if "定义" in file_name or "概念" in file_name:
        return "概念定义"
    if "定理" in file_name or "公式" in file_name or "法则" in file_name:
        return "定理/公式"
    if "总结" in file_name or "归纳" in file_name or "拓展" in file_name:
        return "小结/过渡"
    if lowered.endswith(".jpg") or lowered.endswith(".jpeg") or lowered.endswith(".png"):
        return "待人工复核"
    return "待人工复核"


def infer_section_hint(context: dict, file_name: str, usage_hint: str) -> str:
    stub = chapter_stub(context)
    if usage_hint == "题解/解析":
        return f"{stub}题解与解析"
    if usage_hint == "习题":
        return f"{stub}题型训练"
    if usage_hint == "例题":
        return f"{stub}代表例题"
    if usage_hint == "定理/公式":
        return f"{stub}关键规则"
    if usage_hint == "概念定义":
        return f"{stub}核心概念"
    if usage_hint == "小结/过渡":
        return f"{stub}归纳总结"
    return f"{stub}待人工复核"


def normalize_section_hint(context: dict, section_hint: str, usage_hint: str) -> str:
    text = str(section_hint or "").strip()
    if is_placeholder(text):
        return infer_section_hint(context, "", usage_hint)
    text = text.replace("题目段待细化", "题型训练")
    text = text.replace("解析段待细化", "题解与解析")
    text = text.replace("待细化", "").strip(" -+")
    if text.endswith("题目段"):
        text = text[:-3] + "题型训练"
    if text.endswith("解析段"):
        text = text[:-3] + "题解与解析"
    return text or infer_section_hint(context, "", usage_hint)


def render_manifest(context: dict, rows: list[dict]) -> str:
    lines = [
        "# 章节图片清单",
        "",
        f"- 批次编号：{context['batch_id']}",
        f"- 学科：{context['subject']}",
        f"- 材料来源：{context['source_name']}",
        f"- 处理范围：{context['scope']}",
        f"- 图片总数：{len(rows)}",
        f"- 页码位置规则：{context['page_number_position_label']}",
        "",
        "| 序号 | 文件名 | 相对路径 | 页码/定位 | 当前用途 | 所属小节 | 状态 |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for row in rows:
        lines.append(
            f"| {row['seq']} | {row['file_name']} | {row['relative_path']} | {row['page_hint']} | {row['usage_hint']} | {row['section_hint']} | {row['status_hint']} |"
        )
    return "\n".join(lines) + "\n"


def main() -> int:
    args = parse_args()
    context_path = Path(args.context_json)
    context = normalize_context(load_json(context_path))
    batch_dir = context_path.parent
    manifest_path = batch_dir / MANIFEST_NAME
    rows = parse_manifest_table(manifest_path)
    if not rows:
        return 0

    inferred_page = False
    changed = False
    for row in rows:
        if is_placeholder(row["page_hint"]):
            page_hint = infer_page_hint(row["file_name"])
            if not is_placeholder(page_hint):
                row["page_hint"] = page_hint
                inferred_page = True
                changed = True

        usage_hint = row["usage_hint"]
        if is_placeholder(usage_hint) or usage_hint == "待判定":
            usage_hint = infer_usage_hint(row["file_name"])
            if not is_placeholder(usage_hint):
                row["usage_hint"] = usage_hint
                changed = True

        normalized_section = normalize_section_hint(context, row["section_hint"], row["usage_hint"])
        if normalized_section != row["section_hint"]:
            row["section_hint"] = normalized_section
            changed = True

        row["status_hint"] = "已初填" if not (
            is_placeholder(row["page_hint"]) or is_placeholder(row["usage_hint"]) or is_placeholder(row["section_hint"])
        ) else "待整理"

    if not changed:
        return 0

    if inferred_page and context.get("page_number_source") in {"manual", None, ""}:
        context["page_number_source"] = "filename-page-hint"
        context["page_number_source_label"] = "按文件名中的页码候选推断"
    manifest_path.write_text(render_manifest(context, rows), encoding="utf-8")
    save_json(context_path, context)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
