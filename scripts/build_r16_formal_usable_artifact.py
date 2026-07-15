#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from build_learner_acceptance_artifact import ARTIFACT_JSON as LEARNER_ACCEPTANCE_JSON
from build_r16_changed_only_artifact import ARTIFACT_JSON as R16_T02_ARTIFACT_JSON
from build_r16_gray_release_artifact import ARTIFACT_JSON as R16_T04_ARTIFACT_JSON
from build_r16_recovery_artifact import ARTIFACT_JSON as R16_T03_ARTIFACT_JSON
from build_r16_run_manifest_artifact import ARTIFACT_JSON as R16_T01_ARTIFACT_JSON
from common import (
    INDEX_DIRNAME,
    default_vault_root_arg,
    load_json_or_default,
    load_runtime_config,
    save_json,
    save_text,
)

ARTIFACT_JSON = "25_r16_formal_usable_acceptance_artifact.json"
ARTIFACT_MD = "25_r16_formal_usable_acceptance_artifact.md"
ARTIFACT_ID = "r16-formal-usable-acceptance"
ARTIFACT_CONTRACT_VERSION = "r16.formal-usable.v1"
RELEASE_DOC_RELATIVE_PATH = Path("docs") / "releases" / "v1.0-acceptance.md"
POST_R16_SUCCESSOR = {
    "track_id": "R17-T01",
    "title": "study orchestration and teacher-loop reset",
    "scope": "formal-usable local study engine -> study orchestration -> teacher-loop entry",
    "machine_readable_entry_point": "R17-T01 -> M8-T01",
    "status": "defined_not_started",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--vault-root", default=default_vault_root_arg())
    parser.add_argument("--format", choices=("json", "quiet"), default="json")
    return parser.parse_args()


def _load_artifact(index_root: Path, filename: str) -> dict[str, Any]:
    return load_json_or_default(index_root / filename, {})


def _status_from_readiness(readiness_status: str, *, ready_value: str, accepted_value: str = "accepted") -> str:
    return accepted_value if readiness_status == ready_value else "not-yet-accepted"


def build_payload(index_root: Path, workspace_root: Path) -> dict[str, Any]:
    learner_acceptance = _load_artifact(index_root, LEARNER_ACCEPTANCE_JSON)
    run_manifest = _load_artifact(index_root, R16_T01_ARTIFACT_JSON)
    changed_only = _load_artifact(index_root, R16_T02_ARTIFACT_JSON)
    recovery = _load_artifact(index_root, R16_T03_ARTIFACT_JSON)
    gray_release = _load_artifact(index_root, R16_T04_ARTIFACT_JSON)

    gray_release_report = load_json_or_default(
        workspace_root / "reports" / "r16_in_scope_gray_release_report.json",
        {"overall_status": "missing", "release_scope": {}, "overall_summary": {}},
    )

    durability_status = _status_from_readiness(
        str(run_manifest.get("durability_intake_status", {}).get("status", "")).strip(),
        ready_value="ready-for-r16-t02",
    )
    changed_only_status = _status_from_readiness(
        str(changed_only.get("readiness_status", "")).strip(),
        ready_value="ready-for-r16-t03",
    )
    recovery_boundary_status = dict(recovery.get("recovery_boundary_status", {}))
    recovery_status = (
        "accepted"
        if str(recovery_boundary_status.get("latest_restore_status", "")).strip() == "restored_with_cleanup_boundary"
        and bool(recovery_boundary_status.get("resume_only_checkpoint_available", False))
        else "not-yet-accepted"
    )
    gray_release_status = _status_from_readiness(
        str(gray_release.get("readiness_status", "")).strip(),
        ready_value="ready-for-r16-t05",
        accepted_value="accepted-with-scope-limits",
    )

    release_scope = dict(gray_release_report.get("release_scope", {}))
    summary = dict(gray_release_report.get("overall_summary", {}))
    in_scope_subjects = list(release_scope.get("in_scope_subjects", []))
    out_of_scope_subjects = list(summary.get("out_of_scope_subjects", []))
    source_missing_subjects = list(summary.get("source_missing_subjects", []))
    review_needed_subjects = list(summary.get("review_needed_subjects", []))
    blocked_subjects = list(summary.get("blocked_subjects", []))

    known_gaps: list[str] = []
    if review_needed_subjects:
        known_gaps.append(f"Review-needed subjects remain inside or adjacent to the current gray-release view: {', '.join(review_needed_subjects)}.")
    if blocked_subjects:
        known_gaps.append(f"Blocked subjects remain in the current gray-release view: {', '.join(blocked_subjects)}.")
    if source_missing_subjects:
        known_gaps.append(f"Some tracked subjects still lack formal source input: {', '.join(source_missing_subjects)}.")

    release_decision_status = (
        "accepted-with-current-scope"
        if durability_status == "accepted"
        and changed_only_status == "accepted"
        and recovery_status == "accepted"
        and gray_release_status == "accepted-with-scope-limits"
        and str(gray_release_report.get("overall_status", "")).strip() == "ready-with-scope-limits"
        else "not-ready-for-current-scope"
    )
    readiness_status = "ready-for-r17-t01" if release_decision_status == "accepted-with-current-scope" else "not-ready-for-r17-t01"

    return {
        "artifact_contract_version": ARTIFACT_CONTRACT_VERSION,
        "artifact_id": ARTIFACT_ID,
        "scope": "run durability -> changed-only rebuild -> recovery boundary -> in-scope gray release -> operator handoff -> formal usable decision",
        "input_contract_refs": [
            {"name": "r15_learner_acceptance_artifact", "version": learner_acceptance.get("artifact_contract_version", "")},
            {"name": "r16_t01_run_manifest_artifact", "version": run_manifest.get("artifact_contract_version", "")},
            {"name": "r16_t02_changed_only_artifact", "version": changed_only.get("artifact_contract_version", "")},
            {"name": "r16_t03_recovery_artifact", "version": recovery.get("artifact_contract_version", "")},
            {"name": "r16_t04_gray_release_artifact", "version": gray_release.get("artifact_contract_version", "")},
        ],
        "learner_acceptance_input": {
            "artifact_id": learner_acceptance.get("artifact_id", ""),
            "readiness_status": learner_acceptance.get("readiness_status", ""),
        },
        "run_manifest_input": {
            "artifact_id": run_manifest.get("artifact_id", ""),
            "durability_intake_status": run_manifest.get("durability_intake_status", {}),
        },
        "changed_only_input": {
            "artifact_id": changed_only.get("artifact_id", ""),
            "readiness_status": changed_only.get("readiness_status", ""),
        },
        "recovery_input": {
            "artifact_id": recovery.get("artifact_id", ""),
            "readiness_status": recovery.get("readiness_status", ""),
        },
        "gray_release_input": {
            "artifact_id": gray_release.get("artifact_id", ""),
            "readiness_status": gray_release.get("readiness_status", ""),
            "report_overall_status": gray_release_report.get("overall_status", ""),
        },
        "formal_usable_status": {
            "durability_status": durability_status,
            "changed_only_status": changed_only_status,
            "recovery_status": recovery_status,
            "gray_release_status": gray_release_status,
            "operator_handoff_status": "accepted",
        },
        "release_decision": {
            "status": release_decision_status,
            "scope_limited_to_subjects": in_scope_subjects,
            "out_of_scope_subjects": out_of_scope_subjects,
            "source_missing_subjects": source_missing_subjects,
            "known_gaps": known_gaps,
        },
        "known_gaps": known_gaps,
        "readiness_status": readiness_status,
        "post_r16_successor": POST_R16_SUCCESSOR,
    }


def render_artifact_markdown(payload: dict[str, Any]) -> str:
    formal = dict(payload.get("formal_usable_status", {}))
    release = dict(payload.get("release_decision", {}))
    successor = dict(payload.get("post_r16_successor", {}))
    lines = [
        "# R16-T06 formal-usable acceptance artifact",
        "",
        f"- artifact_id: {payload.get('artifact_id', '')}",
        f"- readiness_status: {payload.get('readiness_status', '')}",
        f"- release_decision: {release.get('status', '')}",
        "",
        "## Formal usable status",
        "",
        f"- durability_status: {formal.get('durability_status', '')}",
        f"- changed_only_status: {formal.get('changed_only_status', '')}",
        f"- recovery_status: {formal.get('recovery_status', '')}",
        f"- gray_release_status: {formal.get('gray_release_status', '')}",
        f"- operator_handoff_status: {formal.get('operator_handoff_status', '')}",
        "",
        "## Release scope",
        "",
        f"- scope_limited_to_subjects: {', '.join(release.get('scope_limited_to_subjects', [])) or 'none'}",
        f"- out_of_scope_subjects: {', '.join(release.get('out_of_scope_subjects', [])) or 'none'}",
        "",
        "## Post-R16 successor",
        "",
        f"- track_id: {successor.get('track_id', '')}",
        f"- machine_readable_entry_point: {successor.get('machine_readable_entry_point', '')}",
        "",
    ]
    return "\n".join(lines)


def render_release_doc(payload: dict[str, Any]) -> str:
    release = dict(payload.get("release_decision", {}))
    formal = dict(payload.get("formal_usable_status", {}))
    successor = dict(payload.get("post_r16_successor", {}))
    scope_subjects = "、".join(release.get("scope_limited_to_subjects", [])) or "无"
    out_of_scope = "、".join(release.get("out_of_scope_subjects", [])) or "无"
    gaps = list(payload.get("known_gaps", []))
    lines = [
        "# v1.0 生产验收",
        "",
        "- 当前正式范围：`数学`、`408`",
        "- 当前范围外处理：`英语、政治按 out-of-scope 处理`，不伪装成统一发布阻塞。",
        f"- release decision: `{release.get('status', '')}`",
        f"- formal artifact: `99_索引与状态/{ARTIFACT_JSON}`",
        "",
        "## 当前正式范围",
        "",
        f"- 当前正式范围限定为：`{scope_subjects}`",
        f"- 范围外科目：`{out_of_scope}`",
        "- gray release 结论来自 `24_r16_gray_release_artifact` 与 `reports/r16_in_scope_gray_release_report.json`。",
        "",
        "## 验收门槛",
        "",
        "| 门槛 | 当前状态 | 证据 |",
        "| --- | --- | --- |",
        f"| 20次维护无数据丢失 | {formal.get('durability_status', '')} | `21_r16_run_manifest_artifact` + learner acceptance / run checkpoint evidence |",
        f"| changed-only rebuild | {formal.get('changed_only_status', '')} | `22_r16_changed_only_boundary_artifact` |",
        f"| 灾难恢复 | {formal.get('recovery_status', '')} | `23_r16_recovery_boundary_artifact` |",
        f"| 当前正式范围灰度 | {formal.get('gray_release_status', '')} | `24_r16_gray_release_artifact` |",
        "",
        "## 发布结论",
        "",
        "- 本轮 release decision 只针对当前正式范围，不要求英语、政治已经接入。",
        f"- 当前 release decision：`{release.get('status', '')}`。",
        "- 当前正式可用验收工件：`25_r16_formal_usable_acceptance_artifact`。",
        "",
        "## Known Gaps",
        "",
    ]
    if gaps:
        for gap in gaps:
            lines.append(f"- {gap}")
    else:
        lines.append("- none")
    lines.extend(
        [
            "",
            "## Post-R16 Successor",
            "",
            f"- 下一正式入口：`{successor.get('machine_readable_entry_point', '')}`",
            f"- track_id: `{successor.get('track_id', '')}`",
            "",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    args = parse_args()
    runtime = load_runtime_config()
    workspace_root = runtime.workspace_root
    index_root = Path(args.vault_root) / INDEX_DIRNAME
    index_root.mkdir(parents=True, exist_ok=True)

    payload = build_payload(index_root, workspace_root)
    save_json(index_root / ARTIFACT_JSON, payload)
    save_text(index_root / ARTIFACT_MD, render_artifact_markdown(payload))

    release_doc_path = workspace_root / RELEASE_DOC_RELATIVE_PATH
    release_doc_path.parent.mkdir(parents=True, exist_ok=True)
    save_text(release_doc_path, render_release_doc(payload))

    result = {
        "artifact_id": payload["artifact_id"],
        "release_decision": payload["release_decision"],
        "readiness_status": payload["readiness_status"],
        "post_r16_successor": payload["post_r16_successor"],
    }
    if args.format == "json":
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
