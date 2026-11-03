"""Small, synthetic KB following the integration tests' explicit-config style."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

EVIDENCE_ID = "EV-BASELINE-1"
QUESTION = "什么是导数"
BOOK_TITLE = "基线高等数学"


def create_fixture(root: Path) -> tuple[Path, dict[str, str]]:
    # Every storage root is explicit, including optional roots. No local config
    # or real KB is discovered, copied, enumerated, or used as a fallback.
    paths = {
        "workspace_root": root,
        "vault_root": root / "vault",
        "kb_root": root / "fixture-kb",
        "backup_root": root / "backups",
        "migration_root": root / "migration",
        "syllabus_root": root / "syllabus",
        "ocr_cache_root": root / "ocr-cache",
    }
    paths["vault_root"].mkdir()
    evidence_dir = paths["kb_root"] / "evidence"
    evidence_dir.mkdir(parents=True)
    span = {
        "source_id": "SRC-BASELINE", "file_id": "FILE-BASELINE",
        "source_file_sha256": "synthetic-baseline-sha",
        "locator": {"page_start": 2, "page_end": 2, "image_start": 2, "image_end": 2},
    }
    evidence = {
        "evidence_id": EVIDENCE_ID, "evidence_key": "baseline-1",
        "source_id": "SRC-BASELINE", "chapter_id": "CH-BASELINE",
        "chunk_id": "CHUNK-BASELINE", "subject": "数学",
        "book_id": "BOOK-BASELINE", "book_title": BOOK_TITLE,
        "chapter_title": "第二章 导数与微分", "title": "导数的定义",
        "content": "导数是函数增量与自变量增量之比在增量趋于零时的极限。",
        "origin_type": "pdf_page_ocr", "verification_status": "reviewed",
        "review_status": "accepted", "source_grounded": True,
        "pdf_page": 2, "printed_page": 1, "mapping_status": "mapped",
        "source_spans": [span],
        "provenance": {
            "origin_type": "pdf_page_ocr", "verification_status": "reviewed",
            "source_grounded": True, "source_spans": [span],
        },
    }
    (evidence_dir / f"{EVIDENCE_ID}.json").write_text(
        json.dumps(evidence, ensure_ascii=False), encoding="utf-8",
    )
    config = root / "baseline.config.json"
    config.write_text(json.dumps({
        **{key: str(value) for key, value in paths.items()},
        "python_executable": sys.executable, "ocr_allow_remote": False,
    }), encoding="utf-8")
    env = {key: value for key, value in os.environ.items()
           if not key.startswith("KAOYAN_") and key not in {"PYTHONPATH", "PYTHONHOME"}}
    env.update(PYTHONUTF8="1", PYTHONDONTWRITEBYTECODE="1", PYTHONNOUSERSITE="1")
    return config, env
