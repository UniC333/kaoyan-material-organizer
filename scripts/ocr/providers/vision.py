"""Import an explicit AI visual transcription; this provider does not run OCR."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from common import sha256_for_file
from ocr.providers.base import OCRProviderRequest
from ocr.providers.rapidocr import require_single_image


MAX_TRANSCRIPTION_BYTES = 1024 * 1024


def transcription_path(source_file: Path) -> Path:
    return source_file.with_name(source_file.name + ".vision.json")


def _transcription_bytes(source_file: Path) -> bytes:
    path = transcription_path(source_file)
    if not path.is_file():
        raise RuntimeError(f"AI visual transcription missing: {path}; see references/material-intake.md")
    with path.open("rb") as handle:
        content = handle.read(MAX_TRANSCRIPTION_BYTES + 1)
    if len(content) > MAX_TRANSCRIPTION_BYTES:
        raise RuntimeError("AI visual transcription exceeds the 1 MiB per-image limit")
    return content


def transcription_model_id(source_file: Path) -> str:
    return "vision-transcription-sha256:" + hashlib.sha256(_transcription_bytes(source_file)).hexdigest()


class VisionOCRProvider:
    name = "vision"

    def run(self, request: OCRProviderRequest) -> dict[str, Any]:
        require_single_image(request)
        if not request.include_blocks:
            raise RuntimeError("vision requires ocr_include_blocks=true for mandatory human review")
        content = _transcription_bytes(request.file_path)
        content_hash = hashlib.sha256(content).hexdigest()
        if request.model != "vision-transcription-sha256:" + content_hash:
            raise RuntimeError("AI visual transcription changed; rerun to obtain a new request key")
        try:
            payload = json.loads(content.decode("utf-8"))
        except (UnicodeError, ValueError) as exc:
            raise RuntimeError("AI visual transcription must be valid UTF-8 JSON") from exc
        if not isinstance(payload, dict):
            raise RuntimeError("AI visual transcription must be a JSON object")
        if payload.get("source_file_sha256") != sha256_for_file(request.file_path):
            raise RuntimeError("AI visual transcription source_file_sha256 does not match the source image")
        if payload.get("provider") != self.name:
            raise RuntimeError('AI visual transcription must declare provider="vision"')
        exact_model = payload.get("exact_model")
        text = payload.get("text")
        if not isinstance(exact_model, str) or not exact_model.strip():
            raise RuntimeError("AI visual transcription requires the actual exact_model name")
        if not isinstance(text, str) or not text.strip():
            raise RuntimeError("AI visual transcription requires nonempty text read from the image")
        # Unknown confidence deliberately queues every transcription for review.
        # Imported review flags are never trusted or carried into the pipeline.
        return {
            "provider": self.name, "exact_model": exact_model.strip(),
            "remote_call_count": 0, "transcription_sha256": request.model.split(":", 1)[1],
            "pages": [{"page_index": 0, "text": text.strip(), "blocks": [{
                "block_id": "OCRBLK-001-001", "block_type": "paragraph",
                "text": text.strip(), "bbox": [], "confidence": 0.0,
            }]}],
        }
