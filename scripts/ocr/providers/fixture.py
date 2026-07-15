from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ocr.providers.base import OCRProviderRequest


class FixtureOCRProvider:
    name = "fixture"

    def __init__(self, fixture_json: Path | None = None) -> None:
        self.fixture_json = fixture_json

    def run(self, request: OCRProviderRequest) -> dict[str, Any]:
        if self.fixture_json and self.fixture_json.exists():
            payload = json.loads(self.fixture_json.read_text(encoding="utf-8"))
        else:
            payload = {
                "provider": "fixture",
                "exact_model": "fixture-ocr-1",
                "pages": [
                    {
                        "page_index": 0,
                        "text": request.file_path.stem,
                        "blocks": [],
                    }
                ],
            }
        fail_for_files = {str(item) for item in payload.get("fail_for_files", [])}
        fail_for_stems = {str(item) for item in payload.get("fail_for_stems", [])}
        if request.file_path.name in fail_for_files or request.file_path.stem in fail_for_stems:
            raise RuntimeError(f"fixture forced failure for {request.file_path.name}")
        pages_by_file = payload.get("pages_by_file", {})
        pages_by_stem = payload.get("pages_by_stem", {})
        if isinstance(pages_by_file, dict) and request.file_path.name in pages_by_file:
            payload["pages"] = pages_by_file[request.file_path.name]
        elif isinstance(pages_by_stem, dict) and request.file_path.stem in pages_by_stem:
            payload["pages"] = pages_by_stem[request.file_path.stem]
        payload.setdefault("provider", self.name)
        payload.setdefault("exact_model", "fixture-ocr-1")
        return payload
