from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

ENTRY = Path(__file__).resolve().parents[2] / "scripts" / "kb.py"


@pytest.mark.parametrize("command,question_flag", [("query", "--query"), ("ask", "--question")])
@pytest.mark.parametrize("index_state", ["missing", "stale", "version-mismatch"])
def test_one_cli_command_refreshes_real_fixture_indexes_and_answers(tmp_path, command, question_flag, index_state):
    kb_root = tmp_path / "kb"
    evidence_root = kb_root / "evidence"
    evidence_root.mkdir(parents=True)
    config = tmp_path / "config.json"
    config.write_text(json.dumps({
        "workspace_root": str(tmp_path), "vault_root": str(tmp_path / "vault"),
        "kb_root": str(kb_root), "backup_root": str(tmp_path / "backups"),
        "migration_root": str(tmp_path / "migration"), "python_executable": sys.executable,
        "ocr_allow_remote": False,
    }), encoding="utf-8")
    span = {"source_id": "SRC", "file_id": "FILE", "source_file_sha256": "sha",
            "locator": {"page_start": 1, "page_end": 1, "image_start": 1, "image_end": 1}}
    evidence = {
        "evidence_id": "EV-MATH-1", "evidence_key": "key", "source_id": "SRC",
        "subject": "数学", "chapter_id": "CH", "chunk_id": "CHUNK", "book_id": "BOOK",
        "book_title": "测试教材", "origin_type": "paper_book_reviewed_ocr",
        "verification_status": "source_grounded", "review_status": "accepted", "source_grounded": True,
        "source_spans": [span], "provenance": {"origin_type": "paper_book_reviewed_ocr",
            "verification_status": "source_grounded", "source_grounded": True, "source_spans": [span]},
        "content": "导数的定义：函数在一点的导数是函数增量与自变量增量之比的极限。",
    }
    path = evidence_root / "EV-MATH-1.json"
    path.write_text(json.dumps(evidence, ensure_ascii=False), encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if not k.startswith("KAOYAN_")}
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    prefix = [sys.executable, str(ENTRY), "--config", str(config)]
    def run(*args):
        completed = subprocess.run([*prefix, *args], cwd=tmp_path, env=env,
                                   capture_output=True, text=True, encoding="utf-8")
        assert completed.returncode == 0, completed.stderr
        return completed.stdout
    if index_state != "missing":
        run("sync", "--indexes-only", "--format", "quiet")
    if index_state == "stale":
        evidence["content"] += "导数反映瞬时变化率。"
        path.write_text(json.dumps(evidence, ensure_ascii=False), encoding="utf-8")
    if index_state == "version-mismatch":
        locator = kb_root / "indexes" / "exercise_locator_index.json"
        payload = json.loads(locator.read_text(encoding="utf-8"))
        payload["schema_version"] = "exercise-locator.v2"
        locator.write_text(json.dumps(payload), encoding="utf-8")

    arguments = [command, "--subject", "数学", "--book-title", "测试教材",
                 question_flag, "导数的定义", "--format", "json"]
    def snapshot():
        return {p.relative_to(tmp_path): (p.read_bytes(), p.stat().st_mtime_ns) if p.is_file() else None
                for p in tmp_path.rglob("*")}
    if index_state in {"missing", "stale"}:
        before = snapshot()
        blocked = json.loads(run(*arguments, "--printed-page", "9999"))
        assert blocked["runtime_context"]["page_locator_index_available"] is False
        assert snapshot() == before, "default stale/missing query must not write"
    output = json.loads(run(*arguments, "--auto-sync"))
    def assert_answer(payload):
        if command == "query":
            assert payload["answer_mode"] == "accepted_evidence"
            assert payload["evidence_hits"][0]["content"] == evidence["content"]
            assert payload["references"][0]["evidence_id"] == "EV-MATH-1"
        else:
            result = payload["answer"]
            assert result["generic_answer_bundle"]["status"] == "exact"
            assert result["generic_answer_bundle"]["conclusion"] == evidence["content"]
            assert result["citation_coverage_ok"] is True
    assert_answer(output)
    assert (kb_root / "indexes" / "query-indexes.lock").read_bytes() == b""
    assert json.loads((kb_root / "indexes" / "exercise_locator_index.json").read_text())["schema_version"] == "exercise-locator.v3"

    before = snapshot()
    assert_answer(json.loads(run(*arguments, "--auto-sync")))
    assert snapshot() == before, "fresh --auto-sync must not write or rebuild"
    assert_answer(json.loads(run(*arguments)))
    assert snapshot() == before, "default query/ask must remain read-only"
