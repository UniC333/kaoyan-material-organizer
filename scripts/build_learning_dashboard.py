#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from common import (
    INDEX_DIRNAME,
    default_vault_root_arg,
    learner_file_map,
    load_json,
    load_json_or_default,
    resolve_subject,
)

REGISTRY_JSON = "chapter_knowledge_registry.json"
AUDIT_JSON = "knowledge_batch_audit.json"
QA_INDEX_MD = "10_章节知识问答总入口.md"
BRIDGE_CONCEPT_MD = "11_跨章节知识串联.md"
BRIDGE_CHAPTER_MD = "12_同教材章节递进.md"
DASHBOARD_QA_MD = "13_问答沉淀索引.md"
REFINEMENT_MD = "15_待精修队列.md"
MASTER_CARD_MD = "16_跨章节主卡片候选.md"
DRAFT_PACK_INDEX_MD = "17_待精修包/00_待精修包索引.md"
MASTER_DRAFT_INDEX_MD = "18_主卡片草稿/00_主卡片草稿索引.md"
DASHBOARD_MD = "00_资料整理总览.md"
AUDIT_MD = "11_章节批次巡检总览.md"
SAVED_QA_JSON = "saved_qa_registry.json"
MASTER_REGISTRY_JSON = "master_card_registry.json"
MASTER_CARD_INDEX_MD = "20_主卡片/00_主卡片索引.md"
FEEDBACK_SUMMARY_JSON = "19_learner_feedback_summary.json"
FEEDBACK_CONTRACT_VERSION = "r15.feedback.v1"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--vault-root", default=default_vault_root_arg())
    return parser.parse_args()


def wiki_link_for(path: str | Path, vault_root: Path) -> str:
    candidate = Path(path)
    relative = candidate.relative_to(vault_root).with_suffix("")
    return f"[[{relative.as_posix()}]]"


def normalize_text(value: str) -> str:
    return str(value or "").strip()


def chapter_sort_key(item: dict[str, Any]) -> tuple[int, int, str]:
    quality = normalize_text(item.get("quality_level", ""))
    quality_rank = {"高质量成品": 0, "学习成品": 1, "可提问": 2}.get(quality, 3)
    pending = int(item.get("pending_chunk_count", 0))
    return (quality_rank, pending, normalize_text(item.get("chapter_title", "")))


def subject_entry_path(vault_root: Path, subject: str) -> Path | None:
    if not subject:
        return None
    try:
        _, config = resolve_subject(subject)
    except SystemExit:
        return None
    subject_root = vault_root / config["dir"]
    subject_label = subject_root.name.split("_", 1)[-1]
    return subject_root / "00_课程入口" / f"00_{subject_label}入口.md"


def recent_qa_notes(vault_root: Path, limit: int) -> list[Path]:
    qa_root = vault_root.rglob("10_问答沉淀\\*.md")
    paths = sorted(qa_root, key=lambda path: path.stat().st_mtime, reverse=True)
    results: list[Path] = []
    for path in paths:
        if path.name.startswith("00_"):
            continue
        results.append(path)
        if len(results) >= limit:
            break
    return results


def recent_saved_questions(qa_payload: dict[str, Any], limit: int) -> list[dict[str, Any]]:
    notes = sorted(
        qa_payload.get("notes", []),
        key=lambda item: (item.get("saved_at", ""), item.get("question", "")),
        reverse=True,
    )
    return notes[:limit]


def render_subject_section(vault_root: Path, batches: list[dict[str, Any]]) -> list[str]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for batch in batches:
        grouped.setdefault(normalize_text(batch.get("subject", "")), []).append(batch)

    lines = ["## 学科入口", ""]
    for subject in sorted(key for key in grouped if key):
        subject_batches = sorted(grouped[subject], key=chapter_sort_key)
        high_quality = sum(1 for item in subject_batches if normalize_text(item.get("quality_level", "")) == "高质量成品")
        ready = sum(1 for item in subject_batches if normalize_text(item.get("quality_level", "")) in {"高质量成品", "学习成品", "可提问"})
        entry_path = subject_entry_path(vault_root, subject)
        entry_link = wiki_link_for(entry_path, vault_root) if entry_path and entry_path.exists() else subject
        next_step = normalize_text(subject_batches[0].get("next_step", "")) if subject_batches else "待补充"
        lines.append(f"- {subject}：{entry_link}，已接入 {len(subject_batches)} 章，高质量成品 {high_quality}，可学习入口 {ready}。")
        if next_step:
            lines.append(f"  当前下一步：{next_step}")
    if len(lines) == 2:
        lines.append("- 当前还没有可用的学科入口。")
    lines.append("")
    return lines


def render_learning_ready(batches: list[dict[str, Any]], vault_root: Path) -> list[str]:
    ready_batches = [item for item in batches if normalize_text(item.get("quality_level", "")) in {"高质量成品", "学习成品"}]
    ready_batches = sorted(ready_batches, key=chapter_sort_key)[:6]
    lines = ["## 当前可直接学习", ""]
    if not ready_batches:
        lines.append("- 当前还没有进入可直接学习状态的章节。")
        lines.append("")
        return lines
    for item in ready_batches:
        batch_dir = Path(str(item.get("batch_dir", "")))
        body_path = batch_dir / "20_章节整理" / "01_章节整理正文.md"
        qa_path = batch_dir / "50_提问索引" / "03_知识点问答入口.md"
        lines.append(
            f"- {item.get('subject', '')}-{item.get('chapter_title', '')}：{item.get('quality_level', '待评估')}；正文 {wiki_link_for(body_path, vault_root)}；提问 {wiki_link_for(qa_path, vault_root)}"
        )
    lines.append("")
    return lines


def render_boost_list(batches: list[dict[str, Any]], vault_root: Path) -> list[str]:
    targets = [item for item in batches if normalize_text(item.get("quality_level", "")) != "高质量成品"]
    targets = sorted(targets, key=chapter_sort_key)[:6]
    lines = ["## 当前最值得补强", ""]
    if not targets:
        lines.append("- 当前已没有明显需要优先补强的章节。")
        lines.append("")
        return lines
    for item in targets:
        batch_dir = Path(str(item.get("batch_dir", "")))
        status_path = batch_dir / "00_章节状态总览.md"
        lines.append(
            f"- {item.get('subject', '')}-{item.get('chapter_title', '')}：{item.get('quality_level', '待评估')}；下一步：{item.get('next_step', '待补充')}；状态 {wiki_link_for(status_path, vault_root)}"
        )
    lines.append("")
    return lines


def weak_node_items(learner_model: dict[str, Any], limit: int = 6) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for subject, subject_model in learner_model.get("subjects", {}).items():
        for node_id, node_model in subject_model.get("node_mastery", {}).items():
            item = dict(node_model)
            item["subject"] = subject
            item["node_id"] = node_id
            items.append(item)
    rank = {"weak": 0, "developing": 1, "stable": 2}
    items.sort(
        key=lambda item: (
            rank.get(str(item.get("mastery_band", "")).strip(), 3),
            float(item.get("mastery_score", 0.0)),
            -int(item.get("fallback_count", 0)),
            -int(item.get("question_count", 0)),
            str(item.get("node_id", "")),
        )
    )
    return items[:limit]


def build_feedback_summary(learner_model: dict[str, Any], refinement_payload: dict[str, Any]) -> dict[str, Any]:
    weak_nodes = weak_node_items(learner_model)
    learner_facing_summary: list[dict[str, Any]] = []
    for subject, subject_model in sorted(learner_model.get("subjects", {}).items()):
        summary = dict(subject_model.get("mastery_summary", {}))
        learner_facing_summary.append(
            {
                "subject": subject,
                "question_count": int(subject_model.get("question_count", 0)),
                "fallback_count": int(summary.get("fallback_count", 0)),
                "compare_count": int(summary.get("compare_count", 0)),
                "diagnose_count": int(summary.get("diagnose_count", 0)),
                "plan_count": int(summary.get("plan_count", 0)),
            }
        )

    review_only_insights: list[dict[str, Any]] = []
    for item in list(refinement_payload.get("items", []))[:6]:
        review_only_insights.append(
            {
                "status": item.get("status", ""),
                "subject": item.get("subject", ""),
                "chapter_title": item.get("chapter_title", ""),
                "candidate_type": item.get("candidate_type", ""),
                "question_count": int(item.get("question_count", 0)),
                "node_ids": list(item.get("node_ids", [])),
            }
        )

    return {
        "feedback_contract_version": FEEDBACK_CONTRACT_VERSION,
        "fact_writeback_allowed": False,
        "learner_facing_summary": learner_facing_summary,
        "review_only_insights": review_only_insights,
        "weak_points": [
            {
                "subject": item.get("subject", ""),
                "node_id": item.get("node_id", ""),
                "title": item.get("title", ""),
                "mastery_band": item.get("mastery_band", ""),
                "mastery_score": float(item.get("mastery_score", 0.0)),
                "fallback_count": int(item.get("fallback_count", 0)),
                "question_count": int(item.get("question_count", 0)),
            }
            for item in weak_nodes
        ],
    }


def render_learner_section(learner_model: dict[str, Any], refinement_payload: dict[str, Any]) -> list[str]:
    lines = ["## 周复习与薄弱点", ""]
    subjects = learner_model.get("subjects", {})
    if not subjects:
        lines.append("- 当前还没有可用的 learner 问答或做题记录。")
        lines.append("")
        return lines

    lines.extend(["### learner-facing summary", ""])
    for subject in sorted(subjects):
        subject_model = dict(subjects[subject])
        summary = dict(subject_model.get("mastery_summary", {}))
        lines.append(
            f"- {subject}：questions={subject_model.get('question_count', 0)}；fallback={summary.get('fallback_count', 0)}；compare={summary.get('compare_count', 0)}；diagnose={summary.get('diagnose_count', 0)}；plan={summary.get('plan_count', 0)}"
        )

    lines.extend(["", "### 当前最值得先复习的考点", ""])
    weak_nodes = weak_node_items(learner_model)
    if weak_nodes:
        for item in weak_nodes:
            lines.append(
                f"- {item.get('subject', '')} | {item.get('node_id', '')} | {item.get('title', '')} | band={item.get('mastery_band', '')} | score={float(item.get('mastery_score', 0.0)):.2f} | fallback={item.get('fallback_count', 0)} | questions={item.get('question_count', 0)}"
            )
    else:
        lines.append("- 暂无可用的考点掌握度视图。")

    lines.extend(["", "### review-only insights", ""])
    queue_items = list(refinement_payload.get("items", []))
    if queue_items:
        for item in queue_items[:6]:
            lines.append(
                f"- {item.get('status', '')} | {item.get('subject', '')} | {item.get('chapter_title', '')} | {item.get('candidate_type', '')} | node_ids={', '.join(item.get('node_ids', []))} | questions={item.get('question_count', 0)}"
            )
    else:
        lines.append("- 当前没有待处理 refinement 候选。")
    lines.extend(["", "### no fact writeback", "", "- learner-facing summary 与 review-only insights 只来自 learner-side derived state，不回写 evidence / claim / canonical-card / syllabus。"])
    lines.append("")
    return lines


def render_cross_chapter(vault_root: Path) -> list[str]:
    return [
        "## 跨章节继续学",
        "",
        f"- 跨章节重复概念：{wiki_link_for(vault_root / INDEX_DIRNAME / BRIDGE_CONCEPT_MD, vault_root)}",
        f"- 同教材章节递进：{wiki_link_for(vault_root / INDEX_DIRNAME / BRIDGE_CHAPTER_MD, vault_root)}",
        f"- 主卡片候选：{wiki_link_for(vault_root / INDEX_DIRNAME / MASTER_CARD_MD, vault_root)}",
        f"- 待精修队列：{wiki_link_for(vault_root / INDEX_DIRNAME / REFINEMENT_MD, vault_root)}",
        f"- 待精修包：{wiki_link_for(vault_root / INDEX_DIRNAME / DRAFT_PACK_INDEX_MD, vault_root)}",
        f"- 主卡片草稿：{wiki_link_for(vault_root / INDEX_DIRNAME / MASTER_DRAFT_INDEX_MD, vault_root)}",
        f"- 正式主卡片：{wiki_link_for(vault_root / INDEX_DIRNAME / MASTER_CARD_INDEX_MD, vault_root)}",
        "",
    ]


def render_question_section(vault_root: Path, qa_payload: dict[str, Any]) -> list[str]:
    qa_index = vault_root / INDEX_DIRNAME / QA_INDEX_MD
    audit_index = vault_root / INDEX_DIRNAME / AUDIT_MD
    qa_registry = vault_root / INDEX_DIRNAME / DASHBOARD_QA_MD
    notes = recent_qa_notes(vault_root, 6)
    recent_questions = recent_saved_questions(qa_payload, 6)
    lines = [
        "## 提问与回看",
        "",
        f"- 章节知识问答总入口：{wiki_link_for(qa_index, vault_root)}",
        f"- 章节批次巡检总览：{wiki_link_for(audit_index, vault_root)}",
        f"- 问答沉淀索引：{wiki_link_for(qa_registry, vault_root)}",
    ]
    if recent_questions:
        lines.extend(["", "### 最近问过的问题", ""])
        for item in recent_questions:
            lines.append(f"- {item.get('subject', '')}-{item.get('chapter_title', '')}-{item.get('question', '')}")
    if notes:
        lines.extend(["", "### 最近问答沉淀", ""])
        for path in notes:
            lines.append(f"- {wiki_link_for(path, vault_root)}")
    lines.append("")
    return lines


def render_master_card_section(vault_root: Path, master_payload: dict[str, Any]) -> list[str]:
    promoted = master_payload.get("promoted_cards", [])
    lines = ["## 已提升主卡片", ""]
    if not promoted:
        lines.append("- 当前还没有达到提升门槛的主卡片。")
        lines.append("")
        return lines
    lines.append(f"- 已提升主卡片数：{len(promoted)}")
    lines.append(f"- 主卡片索引：{wiki_link_for(vault_root / INDEX_DIRNAME / MASTER_CARD_INDEX_MD, vault_root)}")
    lines.append("")
    for item in promoted[:6]:
        name = item.get("suggested_master_card_name") or item.get("concept_name", "")
        master_path_str = normalize_text(item.get("master_card_path", ""))
        link = wiki_link_for(master_path_str, vault_root) if master_path_str else name
        lines.append(f"- {link}：涉及 {item.get('chapter_count', 0)} 章；{item.get('quality_status', '待质检')}")
    lines.append("")
    return lines


def render_dashboard(
    vault_root: Path,
    batches: list[dict[str, Any]],
    registry_payload: dict[str, Any],
    qa_payload: dict[str, Any],
    master_payload: dict[str, Any],
    learner_model: dict[str, Any],
    refinement_payload: dict[str, Any],
) -> str:
    chapter_count = len(registry_payload.get("chapters", []))
    high_quality = sum(1 for item in batches if normalize_text(item.get("quality_level", "")) == "高质量成品")
    qa_count = len(qa_payload.get("notes", []))
    promoted_count = len(master_payload.get("promoted_cards", []))
    lines = [
        "# 学习总入口",
        "",
        f"- 主资料库：{vault_root}",
        f"- 已接入章节：{chapter_count}",
        f"- 高质量成品：{high_quality}",
        f"- 已沉淀问答：{qa_count}",
        f"- 已提升主卡片：{promoted_count}",
        f"- 章节巡检：{wiki_link_for(vault_root / INDEX_DIRNAME / AUDIT_MD, vault_root)}",
        "",
    ]
    lines.extend(render_subject_section(vault_root, batches))
    lines.extend(render_learning_ready(batches, vault_root))
    lines.extend(render_boost_list(batches, vault_root))
    lines.extend(render_learner_section(learner_model, refinement_payload))
    lines.extend(render_master_card_section(vault_root, master_payload))
    lines.extend(render_cross_chapter(vault_root))
    lines.extend(render_question_section(vault_root, qa_payload))
    return "\n".join(lines).rstrip() + "\n"


def main() -> int:
    args = parse_args()
    vault_root = Path(args.vault_root)
    index_root = vault_root / INDEX_DIRNAME
    registry_payload = load_json(index_root / REGISTRY_JSON)
    audit_payload = load_json(index_root / AUDIT_JSON) if (index_root / AUDIT_JSON).exists() else {"batches": []}
    qa_payload = load_json(index_root / SAVED_QA_JSON) if (index_root / SAVED_QA_JSON).exists() else {"notes": [], "chapters": []}
    master_payload = load_json(index_root / MASTER_REGISTRY_JSON) if (index_root / MASTER_REGISTRY_JSON).exists() else {"promoted_cards": []}
    learner_files = learner_file_map()
    learner_model = load_json_or_default(learner_files["learner_model"], {"subjects": {}, "updated_at": ""})
    refinement_payload = load_json_or_default(learner_files["refinement_queue"], {"items": [], "updated_at": ""})
    feedback_summary = build_feedback_summary(learner_model, refinement_payload)
    batches = audit_payload.get("batches", [])
    index_root.mkdir(parents=True, exist_ok=True)
    (index_root / DASHBOARD_MD).write_text(
        render_dashboard(vault_root, batches, registry_payload, qa_payload, master_payload, learner_model, refinement_payload),
        encoding="utf-8",
    )
    (index_root / FEEDBACK_SUMMARY_JSON).write_text(json.dumps(feedback_summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
