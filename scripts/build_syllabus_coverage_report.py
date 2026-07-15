#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from common import default_vault_root_arg, ensure_kb_layout, load_all_json, load_json, resolve_subject


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--subject", required=True)
    parser.add_argument("--vault-root", default=default_vault_root_arg())
    parser.add_argument("--format", choices=("json", "quiet"), default="json")
    return parser.parse_args()


def accepted_grouped(layout: dict[str, Path], subject: str) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for evidence in load_all_json(layout["evidence"]):
        if evidence.get("subject") != subject:
            continue
        for node in evidence.get("accepted_syllabus_nodes", []):
            node_id = str(node.get("node_id", "")).strip()
            if node_id:
                grouped.setdefault(node_id, []).append(evidence)
    return grouped


def review_items(layout: dict[str, Path], subject: str) -> list[dict[str, Any]]:
    queue_path = layout["review_syllabus_mapping"] / f"{subject}.json"
    if queue_path.exists():
        payload = load_json(queue_path)
        return [item for item in payload.get("items", []) if isinstance(item, dict)]

    items: list[dict[str, Any]] = []
    for evidence in load_all_json(layout["evidence"]):
        if evidence.get("subject") != subject or evidence.get("mapping_status") != "review":
            continue
        items.append(
            {
                "evidence_id": evidence.get("evidence_id", ""),
                "title": evidence.get("title", ""),
                "chapter_id": evidence.get("chapter_id", ""),
                "top_candidates": list(evidence.get("syllabus_candidates", []))[:3],
                "updated_at": evidence.get("updated_at", ""),
            }
        )
    items.sort(key=lambda item: item.get("evidence_id", ""))
    return items


def build_summary(subject: str, syllabus: dict[str, Any], grouped: dict[str, list[dict[str, Any]]], review: list[dict[str, Any]]) -> dict[str, Any]:
    nodes = list(syllabus.get("nodes", []))
    covered_node_ids = sorted(node_id for node_id, refs in grouped.items() if refs)
    uncovered_nodes = [node for node in nodes if node.get("node_id") not in grouped]
    return {
        "subject": subject,
        "total_nodes": len(nodes),
        "covered_nodes": len(covered_node_ids),
        "covered_node_ids": covered_node_ids,
        "uncovered_nodes": [str(node.get("node_id", "")).strip() for node in uncovered_nodes],
        "uncovered_titles": [str(node.get("title", "")).strip() for node in uncovered_nodes],
        "review_count": len(review),
    }


def render_report(subject: str, syllabus: dict[str, Any], grouped: dict[str, list[dict[str, Any]]], review: list[dict[str, Any]], summary: dict[str, Any]) -> str:
    lines = [
        f"# {subject}考纲地图",
        "",
        "- 说明：这里只展示学习时需要看的考纲节点、已覆盖证据、待复核映射和当前知识缺口。",
        "",
        "## 覆盖统计",
        "",
        f"- 总节点数：{summary['total_nodes']}",
        f"- 已覆盖节点数：{summary['covered_nodes']}",
        f"- 待复核映射数：{summary['review_count']}",
        "",
        "## 知识缺口",
        "",
    ]
    if summary["uncovered_nodes"]:
        for node in syllabus.get("nodes", []):
            if node.get("node_id") in summary["uncovered_nodes"]:
                lines.append(f"- {node.get('title', '')} | {node.get('node_id', '')}")
    else:
        lines.append("- 当前无明显考纲覆盖缺口。")
    lines.extend(["", "## 待复核映射", ""])
    if review:
        for item in review:
            top = item.get("top_candidates", [])
            candidate_text = "；".join(
                f"{candidate.get('title', '')}({candidate.get('confidence', '')})" for candidate in top[:3]
            ) or "无候选"
            lines.append(
                f"- {item.get('title', '')} | {item.get('chapter_id', '')} | 候选：{candidate_text}"
            )
    else:
        lines.append("- 当前无待复核映射。")
    lines.append("")
    for node in syllabus.get("nodes", []):
        refs = grouped.get(node["node_id"], [])
        lines.append(f"## {node['title']}")
        lines.append("")
        lines.append(f"- 节点ID：`{node['node_id']}`")
        lines.append(f"- 别名：{', '.join(node.get('aliases', [])) or '无'}")
        lines.append(f"- 已接入证据：{len(refs)}")
        if refs:
            for item in refs[:5]:
                locator = item.get("locator", {})
                lines.append(
                    f"- {item.get('title', '')} | 页段 {locator.get('page_start', '')}-{locator.get('page_end', '')} | {item.get('chapter_id', '')}"
                )
        else:
            lines.append("- 当前暂无高置信度证据")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    args = parse_args()
    subject, _ = resolve_subject(args.subject)
    layout = ensure_kb_layout()
    syllabus_path = layout["syllabus"] / f"{subject}.json"
    if not syllabus_path.exists():
        raise SystemExit(f"[ERROR] missing syllabus registry: {syllabus_path}")
    syllabus = load_json(syllabus_path)
    grouped = accepted_grouped(layout, subject)
    review = review_items(layout, subject)
    summary = build_summary(subject, syllabus, grouped, review)

    report_dir = Path(args.vault_root) / "00_考纲地图"
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / f"{subject}考纲地图.md"
    report_path.write_text(render_report(subject, syllabus, grouped, review, summary), encoding="utf-8")

    payload = {
        "subject": subject,
        "path": str(report_path),
        **summary,
    }
    if args.format == "json":
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
