#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from build_r16_recovery_artifact import ARTIFACT_JSON as R16_T03_ARTIFACT_JSON
from common import (
    INDEX_DIRNAME,
    default_vault_root_arg,
    ensure_kb_layout,
    load_json_or_default,
    load_runtime_config,
    preferred_python_executable,
    run_utf8_subprocess,
    runtime_subprocess_env,
    save_json,
    save_text,
)

REPORT_JSON = "r16_in_scope_gray_release_report.json"
REPORT_MD = "r16_in_scope_gray_release_report.md"
ARTIFACT_JSON = "24_r16_gray_release_artifact.json"
ARTIFACT_MD = "24_r16_gray_release_artifact.md"
ARTIFACT_ID = "r16-gray-release-scope"
ARTIFACT_CONTRACT_VERSION = "r16.gray-release.v1"
IN_SCOPE_SUBJECTS = ["数学", "408"]
TRACKED_SUBJECTS = ["数学", "408", "英语", "政治"]
POST_R16_T04_SUCCESSOR = {
    "track_id": "R16-T05",
    "title": "install, daily-use, review, and recovery handoff boundary",
    "scope": "gray release -> release-scope -> operator and learner handoff",
    "machine_readable_entry_point": "R16-T05 -> M7-T05",
    "status": "defined_not_started",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--vault-root", default=default_vault_root_arg())
    parser.add_argument("--format", choices=("json", "quiet"), default="json")
    return parser.parse_args()


def script_path(name: str) -> Path:
    return Path(__file__).with_name(name)


def run_script(name: str, *args: str) -> str:
    command = [preferred_python_executable(), str(script_path(name)), *args]
    completed = run_utf8_subprocess(
        command,
        command_label=f"python:{name}",
        check=True,
        env=runtime_subprocess_env(),
    )
    return completed.stdout.strip()


def _load_r16_t03_artifact(index_root: Path) -> dict[str, Any]:
    return load_json_or_default(
        index_root / R16_T03_ARTIFACT_JSON,
        {
            "artifact_id": "",
            "readiness_status": "missing-r16-t03-artifact",
        },
    )


def _subject_dir(subject: str) -> str:
    mapping = {
        "数学": "10_数学",
        "英语": "20_英语",
        "408": "30_408",
        "政治": "40_政治",
    }
    return mapping[subject]


def _subject_metrics(vault_root: Path, kb_root: Path, subject: str, batches: list[dict[str, Any]]) -> dict[str, Any]:
    subject_root = vault_root / _subject_dir(subject)
    chapter_bodies = list(subject_root.rglob("01_章节整理正文.md"))
    qa_entries = list(subject_root.rglob("chapter_knowledge_index.json"))
    card_index = load_json_or_default(kb_root / "indexes" / "canonical_cards.json", {"items": []})
    canonical_cards = [item for item in card_index.get("items", []) if str(item.get("subject", "")).strip() == subject]
    syllabus_map = (kb_root / "syllabus" / f"{subject}.json").exists()
    return {
        "subject": subject,
        "knowledge_batches": len(batches),
        "chapter_bodies": len(chapter_bodies),
        "qa_entries": len(qa_entries),
        "canonical_cards": len(canonical_cards),
        "course_entries": 1 if subject_root.exists() else 0,
        "syllabus_map": syllabus_map,
    }


def _in_scope_status(subject: str, batches: list[dict[str, Any]], metrics: dict[str, Any]) -> tuple[str, list[str], str]:
    if not batches:
        return (
            "blocked",
            [f"{subject} 当前没有纳入 gray release 的 chapter-photo 批次。"],
            "先补齐至少一条正式 chapter-photo 主链，再重新评估当前正式范围内的 gray release。",
        )

    if (
        metrics["knowledge_batches"] > 0
        and metrics["chapter_bodies"] > 0
        and metrics["canonical_cards"] > 0
        and metrics["syllabus_map"]
    ):
        return (
            "ready",
            [
                f"{subject} 当前已存在正式章节正文、canonical cards 与 syllabus map。",
                "即使同学科还有未纳入本轮正式范围的 stub 批次，也不阻断本轮 in-scope gray release 结论。",
            ],
            "继续扩大真实资料覆盖即可，不需要回退到主链能力补课。",
        )

    issues = [issue for batch in batches for issue in batch.get("issues", [])]
    return (
        "review-needed",
        issues or [f"{subject} 主链已存在，但 release-scope 仍缺少章节正文、主卡或 syllabus map 的至少一项。"],
        "补齐 release-scope 缺项后再重新评估。",
    )


def _out_of_scope_status(subject: str, batches: list[dict[str, Any]], metrics: dict[str, Any]) -> tuple[str, list[str], str]:
    if batches or metrics["course_entries"] > 0:
        return (
            "out-of-scope",
            [f"{subject} 当前不在正式 gray release 范围内，本轮只做范围标注，不把它当同层阻塞。"],
            "如需纳入正式范围，应在后续阶段单独解锁并重新定义发布口径。",
        )
    return (
        "source-missing",
        [f"{subject} 当前既不在正式范围内，也缺少可评估的正式来源输入。"],
        "先补来源，再决定是否未来纳入正式范围。",
    )


def build_report(vault_root: Path) -> dict[str, Any]:
    kb_root = ensure_kb_layout()["root"]
    audit_payload = json.loads(
        run_script(
            "audit_knowledge_batches.py",
            "--vault-root",
            str(vault_root),
            "--write-report",
            "--format",
            "json",
        )
    )
    batches_by_subject: dict[str, list[dict[str, Any]]] = {subject: [] for subject in TRACKED_SUBJECTS}
    for batch in audit_payload.get("batches", []):
        subject = str(batch.get("subject", "")).strip()
        if subject in batches_by_subject:
            batches_by_subject[subject].append(batch)

    subjects: list[dict[str, Any]] = []
    in_scope_ready_subjects: list[str] = []
    for subject in TRACKED_SUBJECTS:
        batches = batches_by_subject.get(subject, [])
        metrics = _subject_metrics(vault_root, kb_root, subject, batches)
        if subject in IN_SCOPE_SUBJECTS:
            status, evidence, next_action = _in_scope_status(subject, batches, metrics)
            scope_status = "in-scope"
            if status == "ready":
                in_scope_ready_subjects.append(subject)
        else:
            status, evidence, next_action = _out_of_scope_status(subject, batches, metrics)
            scope_status = status
        subjects.append(
            {
                **metrics,
                "scope_status": scope_status,
                "status": status,
                "evidence": evidence,
                "next_action": next_action,
            }
        )

    overall_status = "ready-with-scope-limits" if sorted(in_scope_ready_subjects) == sorted(IN_SCOPE_SUBJECTS) else "review-needed"
    return {
        "task_id": "M7-T04",
        "audit_date": datetime.now().strftime("%Y-%m-%d"),
        "audit_scope": "in-scope-real-material-gray-release-and-release-scope",
        "source_vault": str(vault_root),
        "release_scope": {
            "in_scope_subjects": IN_SCOPE_SUBJECTS,
            "tracked_subjects": TRACKED_SUBJECTS,
        },
        "overall_status": overall_status,
        "overall_summary": {
            "in_scope_ready_subjects": in_scope_ready_subjects,
            "in_scope_ready_count": len(in_scope_ready_subjects),
            "out_of_scope_subjects": [item["subject"] for item in subjects if item["status"] == "out-of-scope"],
            "source_missing_subjects": [item["subject"] for item in subjects if item["status"] == "source-missing"],
            "review_needed_subjects": [item["subject"] for item in subjects if item["status"] == "review-needed"],
            "blocked_subjects": [item["subject"] for item in subjects if item["status"] == "blocked"],
        },
        "subjects": subjects,
    }


def render_report_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# R16-T04 当前正式范围 gray release 报告",
        "",
        f"- task_id: {payload.get('task_id', '')}",
        f"- overall_status: {payload.get('overall_status', '')}",
        f"- in_scope_subjects: {', '.join(payload.get('release_scope', {}).get('in_scope_subjects', []))}",
        "",
        "| Subject | Scope | Status | Chapter Bodies | QA Entries | Cards |",
        "| --- | --- | --- | ---: | ---: | ---: |",
    ]
    for item in payload.get("subjects", []):
        lines.append(
            f"| {item.get('subject', '')} | {item.get('scope_status', '')} | {item.get('status', '')} | {item.get('chapter_bodies', 0)} | {item.get('qa_entries', 0)} | {item.get('canonical_cards', 0)} |"
        )
    return "\n".join(lines) + "\n"


def build_artifact(index_root: Path, report_payload: dict[str, Any]) -> dict[str, Any]:
    t03_artifact = _load_r16_t03_artifact(index_root)
    overall_status = str(report_payload.get("overall_status", "")).strip()
    readiness_status = "ready-for-r16-t05" if overall_status == "ready-with-scope-limits" else "not-ready-for-r16-t05"
    return {
        "artifact_contract_version": ARTIFACT_CONTRACT_VERSION,
        "artifact_id": ARTIFACT_ID,
        "scope": "recovery boundary -> in-scope gray release -> release-scope declaration",
        "input_contract_refs": [
            {"name": "r16_t03_recovery_artifact", "version": t03_artifact.get("artifact_contract_version", "")},
            {"name": "r16_t04_gray_release_report", "version": "gray-release.v1"},
        ],
        "recovery_input": {
            "artifact_id": t03_artifact.get("artifact_id", ""),
            "readiness_status": t03_artifact.get("readiness_status", ""),
        },
        "gray_release_status": {
            "overall_status": overall_status,
            "in_scope_ready_subjects": report_payload.get("overall_summary", {}).get("in_scope_ready_subjects", []),
            "out_of_scope_subjects": report_payload.get("overall_summary", {}).get("out_of_scope_subjects", []),
            "source_missing_subjects": report_payload.get("overall_summary", {}).get("source_missing_subjects", []),
            "review_needed_subjects": report_payload.get("overall_summary", {}).get("review_needed_subjects", []),
            "blocked_subjects": report_payload.get("overall_summary", {}).get("blocked_subjects", []),
        },
        "readiness_status": readiness_status,
        "remaining_gaps": [] if readiness_status == "ready-for-r16-t05" else ["Current in-scope subjects are not all ready for gray release."],
        "post_r16_t04_successor": POST_R16_T04_SUCCESSOR,
    }


def render_artifact_markdown(payload: dict[str, Any]) -> str:
    gray = dict(payload.get("gray_release_status", {}))
    successor = dict(payload.get("post_r16_t04_successor", {}))
    lines = [
        "# R16-T04 gray release artifact",
        "",
        f"- artifact_id: {payload.get('artifact_id', '')}",
        f"- readiness_status: {payload.get('readiness_status', '')}",
        f"- overall_status: {gray.get('overall_status', '')}",
        f"- in_scope_ready_subjects: {', '.join(gray.get('in_scope_ready_subjects', [])) or 'none'}",
        "",
        "## Post-R16-T04 successor",
        "",
        f"- track_id: {successor.get('track_id', '')}",
        f"- title: {successor.get('title', '')}",
        f"- machine_readable_entry_point: {successor.get('machine_readable_entry_point', '')}",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    args = parse_args()
    vault_root = Path(args.vault_root)
    runtime = load_runtime_config()
    reports_root = runtime.workspace_root / "reports"
    reports_root.mkdir(parents=True, exist_ok=True)
    index_root = vault_root / INDEX_DIRNAME
    index_root.mkdir(parents=True, exist_ok=True)

    report_payload = build_report(vault_root)
    save_json(reports_root / REPORT_JSON, report_payload)
    save_text(reports_root / REPORT_MD, render_report_markdown(report_payload))

    artifact_payload = build_artifact(index_root, report_payload)
    save_json(index_root / ARTIFACT_JSON, artifact_payload)
    save_text(index_root / ARTIFACT_MD, render_artifact_markdown(artifact_payload))

    result = {
        "artifact_id": artifact_payload["artifact_id"],
        "overall_status": report_payload["overall_status"],
        "readiness_status": artifact_payload["readiness_status"],
        "post_r16_t04_successor": artifact_payload["post_r16_t04_successor"],
    }
    if args.format == "json":
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
