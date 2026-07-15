#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from common import preferred_python_executable, run_utf8_subprocess, runtime_subprocess_env


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--subject")
    parser.add_argument("--context-json")
    parser.add_argument("--rebuild-kb", action="store_true")
    parser.add_argument("--publish-canonical", action="store_true")
    parser.add_argument("--refresh-learning", action="store_true")
    parser.add_argument("--yes", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--no-backup", action="store_true")
    parser.add_argument("--format", choices=("json", "quiet"), default="json")
    return parser.parse_args()


def script_path(name: str) -> Path:
    return Path(__file__).with_name(name)


def run_script(name: str, *args: str) -> None:
    run_utf8_subprocess(
        [preferred_python_executable(), str(script_path(name)), *args],
        command_label=f"python:{name}",
        check=True,
        env=runtime_subprocess_env(),
    )


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    args = parse_args()
    execute = args.yes or args.force
    steps: list[str] = []
    if args.context_json:
        run_script("sync_chapter_knowledge.py", "--context-json", args.context_json)
        steps.append("sync_chapter_knowledge")
    if args.rebuild_kb:
        rebuild_args = ["--format", "quiet"]
        if args.subject:
            rebuild_args.extend(["--subject", args.subject])
        else:
            rebuild_args.append("--all")
        if execute:
            rebuild_args.append("--yes")
        if args.no_backup:
            rebuild_args.append("--no-backup")
        run_script("rebuild_kb_layer.py", *rebuild_args)
        steps.append("rebuild_kb_layer")
    if args.subject:
        syllabus_args = ["--subject", args.subject, "--format", "quiet"]
        if execute:
            syllabus_args.append("--yes")
        if args.no_backup:
            syllabus_args.append("--no-backup")
        run_script("build_syllabus_registry.py", *syllabus_args)
        run_script("build_syllabus_coverage_report.py", "--subject", args.subject)
        steps.append("syllabus")
        run_script("reconcile_exam_knowledge.py", "--subject", args.subject, "--format", "quiet")
    else:
        run_script("reconcile_exam_knowledge.py", "--format", "quiet")
    steps.append("reconcile_exam_knowledge")
    run_script("build_claim_registry.py", "--format", "quiet")
    run_script("build_conflict_registry.py", "--format", "quiet")
    run_script("lint_kb_entities.py", "--format", "quiet")
    steps.extend(["build_claim_registry", "build_conflict_registry", "lint_kb_entities"])
    if args.publish_canonical:
        publish_args = ["--format", "quiet"]
        if args.subject:
            publish_args.extend(["--subject", args.subject])
        if execute:
            publish_args.append("--yes")
        if args.no_backup:
            publish_args.append("--no-backup")
        run_script("publish_canonical_cards.py", *publish_args)
        steps.append("publish_canonical_cards")
    if args.refresh_learning:
        run_script("review_refinement_candidates.py", "--format", "quiet")
        steps.append("review_refinement_candidates")
    if args.format == "json":
        print(json.dumps({"steps": steps}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
