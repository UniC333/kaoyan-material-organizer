#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from common import INDEX_DIRNAME, default_vault_root_arg, load_json, preferred_python_executable, run_utf8_subprocess, runtime_subprocess_env

CARD_REUSE_JSON = "card_reuse_candidates.json"
REFINEMENT_QUEUE_JSON = "refinement_queue.json"
MASTER_CARD_JSON = "master_card_candidates.json"
MASTER_REGISTRY_JSON = "master_card_registry.json"
FEEDBACK_SUMMARY_JSON = "19_learner_feedback_summary.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--vault-root", default=default_vault_root_arg())
    parser.add_argument("--subject")
    parser.add_argument("--chapter")
    parser.add_argument("--max-images-per-chunk", type=int)
    parser.add_argument("--replan-chunks", action="store_true")
    parser.add_argument("--force-full-sync", action="store_true")
    parser.add_argument("--topn", type=int, default=5)
    parser.add_argument("--format", choices=("text", "json"), default="text")
    return parser.parse_args()


def script_path(name: str) -> Path:
    return Path(__file__).with_name(name)


def run_script(name: str, *args: str) -> str:
    command = [preferred_python_executable(), str(script_path(name)), *args]
    completed = run_utf8_subprocess(command, command_label=f"python:{name}", check=True, env=runtime_subprocess_env())
    return completed.stdout.strip()


def issue_rank(batch: dict) -> tuple[int, int, str]:
    quality = str(batch.get("quality_level", "")).strip()
    quality_rank = {"高质量成品": 3, "学习成品": 2, "可提问": 1}.get(quality, 0)
    pending = int(batch.get("pending_chunk_count", 0))
    issue_count = len(batch.get("issues", []))
    return (-issue_count, -pending, quality_rank, str(batch.get("chapter_title", "")))


def top_actions(audit_payload: dict, limit: int) -> list[dict]:
    batches = list(audit_payload.get("batches", []))
    batches.sort(key=issue_rank)
    results: list[dict] = []
    for batch in batches:
        if not batch.get("issues") and batch.get("quality_level") == "高质量成品":
            continue
        results.append(
            {
                "subject": batch.get("subject", ""),
                "chapter_title": batch.get("chapter_title", ""),
                "quality_level": batch.get("quality_level", ""),
                "knowledge_status": batch.get("knowledge_status", ""),
                "pending_chunk_count": int(batch.get("pending_chunk_count", 0)),
                "next_step": batch.get("next_step", ""),
                "issues": batch.get("issues", []),
                "priority_feedback_cards": batch.get("priority_feedback_cards", []),
            }
        )
        if len(results) >= limit:
            break
    return results


def build_sync_args(args: argparse.Namespace) -> list[str]:
    sync_args = ["--vault-root", args.vault_root, "--format", "json"]
    if args.subject:
        sync_args.extend(["--subject", args.subject])
    if args.chapter:
        sync_args.extend(["--chapter", args.chapter])
    if not args.force_full_sync:
        sync_args.append("--changed-only")
    if args.replan_chunks:
        sync_args.append("--replan-chunks")
    if args.max_images_per_chunk:
        sync_args.extend(["--max-images-per-chunk", str(args.max_images_per_chunk)])
    return sync_args


def build_audit_args(args: argparse.Namespace) -> list[str]:
    audit_args = ["--vault-root", args.vault_root, "--write-report", "--format", "json"]
    if args.subject:
        audit_args.extend(["--subject", args.subject])
    if args.chapter:
        audit_args.extend(["--chapter", args.chapter])
    return audit_args


def load_candidates(vault_root: str, file_name: str, key: str, limit: int) -> list[dict]:
    path = Path(vault_root) / INDEX_DIRNAME / file_name
    if not path.exists():
        return []
    payload = load_json(path)
    return payload.get(key, [])[:limit]


def render_text(
    sync_payload: dict,
    audit_payload: dict,
    actions: list[dict],
    reuse_candidates: list[dict],
    refinement_queue: list[dict],
    master_candidates: list[dict],
    promoted_cards: list[dict],
    force_full_sync: bool,
) -> str:
    summary = audit_payload.get("summary", {})
    mode = "full-sync" if force_full_sync else "changed-only"
    lines = [
        "# 知识归纳维护结果",
        "",
        f"- 同步模式：{mode}",
        f"- 本轮实际同步章节：{sync_payload.get('count', 0)}",
        f"- 当前纳管章节：{summary.get('total', 0)}",
        f"- 高质量成品：{summary.get('high_quality', 0)}",
        f"- 已进入可学习状态：{summary.get('learning_ready', 0)}",
        f"- 仍有结构性问题：{summary.get('with_issues', 0)}",
        "",
        "## 本轮同步",
        "",
    ]
    chapters = sync_payload.get("chapters", [])
    if chapters:
        for item in chapters:
            suffix = ""
            reasons = item.get("change_reasons", [])
            if reasons:
                suffix = f"；变更原因：{', '.join(reasons)}"
            lines.append(
                f"- {item.get('subject', '')}-{item.get('chapter_title', '')}：{item.get('quality_level') or '待评估'}；{item.get('knowledge_status') or '待补充'}{suffix}"
            )
    else:
        lines.append("- 本轮没有命中需要增量同步的章节。")

    lines.extend(["", "## 建议优先继续补", ""])
    if actions:
        for item in actions:
            issue_text = "；".join(item.get("issues", [])[:3]) or "无明显结构性问题"
            cards = item.get("priority_feedback_cards", [])
            card_text = ""
            if cards:
                names = "、".join(card.get("concept_name", "") for card in cards[:3] if card.get("concept_name"))
                if names:
                    card_text = f"；优先卡片：{names}"
            lines.append(
                f"- {item.get('subject', '')}-{item.get('chapter_title', '')}：下一步 {item.get('next_step') or '待补充'}；问题：{issue_text}{card_text}"
            )
    else:
        lines.append("- 当前没有明显需要优先补强的章节。")

    lines.extend(["", "## 当前待精修", ""])
    if refinement_queue:
        for item in refinement_queue:
            lines.append(
                f"- {item.get('subject', '')}-{item.get('chapter_title', '')}：{item.get('item_name', '')}；原因：{item.get('reason', '')}"
            )
    else:
        lines.append("- 当前没有待精修项。")

    lines.extend(["", "## 跨章节卡片复用候选", ""])
    if reuse_candidates:
        for item in reuse_candidates:
            chapters_text = "、".join(f"{chapter.get('subject', '')}-{chapter.get('chapter_title', '')}" for chapter in item.get("chapters", [])[:3])
            cards = "、".join(item.get("card_files", [])[:3]) or "待补充"
            lines.append(f"- {item.get('concept_name', '')}：涉及 {item.get('chapter_count', 0)} 章；现有卡片：{cards}；章节：{chapters_text}")
    else:
        lines.append("- 当前还没有稳定的跨章节卡片复用候选。")

    lines.extend(["", "## 主卡片候选", ""])
    if master_candidates:
        for item in master_candidates:
            lines.append(
                f"- {item.get('concept_name', '')}：建议收束为 {item.get('suggested_master_card_name', '')}；涉及 {item.get('chapter_count', 0)} 章"
            )
    else:
        lines.append("- 当前还没有稳定的主卡片候选。")

    lines.extend(["", "## 已提升主卡片", ""])
    if promoted_cards:
        for item in promoted_cards:
            lines.append(
                f"- {item.get('concept_name', '')}：涉及 {item.get('chapter_count', 0)} 章；质检状态 {item.get('quality_status', '待质检')}；路径 {Path(item.get('master_card_path', '')).name}"
            )
    else:
        lines.append("- 当前还没有达到提升门槛的主卡片。")
    return "\n".join(lines) + "\n"


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    args = parse_args()
    sync_payload = json.loads(run_script("sync_all_chapter_knowledge.py", *build_sync_args(args)))
    audit_payload = json.loads(run_script("audit_knowledge_batches.py", *build_audit_args(args)))
    run_script("build_refinement_queue.py", "--vault-root", args.vault_root, "--format", "json")
    run_script("build_refinement_packs.py", "--vault-root", args.vault_root)
    run_script("build_master_card_drafts.py", "--vault-root", args.vault_root)
    run_script("promote_master_cards.py", "--vault-root", args.vault_root, "--format", "json")
    run_script("build_learning_dashboard.py", "--vault-root", args.vault_root)

    actions = top_actions(audit_payload, max(1, args.topn))
    reuse_candidates = load_candidates(args.vault_root, CARD_REUSE_JSON, "candidates", max(1, args.topn))
    refinement_queue = load_candidates(args.vault_root, REFINEMENT_QUEUE_JSON, "items", max(1, args.topn))
    master_candidates = load_candidates(args.vault_root, MASTER_CARD_JSON, "candidates", max(1, args.topn))
    promoted_cards = load_candidates(args.vault_root, MASTER_REGISTRY_JSON, "promoted_cards", max(1, args.topn))
    feedback_summary = load_json(Path(args.vault_root) / INDEX_DIRNAME / FEEDBACK_SUMMARY_JSON)

    payload = {
        "sync_mode": "full-sync" if args.force_full_sync else "changed-only",
        "sync": sync_payload,
        "audit": audit_payload,
        "top_actions": actions,
        "top_reuse_candidates": reuse_candidates,
        "top_refinement_queue": refinement_queue,
        "top_master_card_candidates": master_candidates,
        "promoted_master_cards": promoted_cards,
        "feedback_contract_version": feedback_summary.get("feedback_contract_version", ""),
        "fact_writeback_allowed": bool(feedback_summary.get("fact_writeback_allowed", False)),
        "learner_facing_summary": feedback_summary.get("learner_facing_summary", []),
        "review_only_insights": feedback_summary.get("review_only_insights", []),
    }

    if args.format == "json":
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(
            render_text(
                sync_payload,
                audit_payload,
                actions,
                reuse_candidates,
                refinement_queue,
                master_candidates,
                promoted_cards,
                args.force_full_sync,
            ),
            end="",
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
