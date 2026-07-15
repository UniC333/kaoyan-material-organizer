from __future__ import annotations

import base64
import mimetypes
import os
from pathlib import Path
from typing import Any

from ocr.providers.base import OCRProviderRequest

SUPPORTED_IMAGE_MIME_TYPES = {
    "image/jpeg",
    "image/png",
    "image/webp",
    "image/tiff",
    "image/bmp",
    "image/gif",
}


def _build_mistral_client(api_key: str) -> Any:
    from mistralai.client.sdk import Mistral

    return Mistral(api_key=api_key)


def _serialize_response(response: Any) -> dict[str, Any]:
    if hasattr(response, "model_dump"):
        return response.model_dump(mode="json")
    if hasattr(response, "dict"):
        return response.dict()
    raise TypeError("SDK response cannot be serialized with model_dump/dict")


def _guess_mime_type(file_path: Path) -> str:
    mime_type, _ = mimetypes.guess_type(file_path.name)
    if mime_type not in SUPPORTED_IMAGE_MIME_TYPES:
        raise RuntimeError(f"unsupported or unknown image MIME type: {mime_type!r}")
    return mime_type


def _data_url_for_file(file_path: Path) -> str:
    mime_type = _guess_mime_type(file_path)
    payload = base64.b64encode(file_path.read_bytes()).decode("ascii")
    return f"data:{mime_type};base64,{payload}"


def _normalize_block(raw_block: dict[str, Any], page_index: int, block_index: int) -> dict[str, Any]:
    top_left_x = float(raw_block.get("top_left_x", 0) or 0)
    top_left_y = float(raw_block.get("top_left_y", 0) or 0)
    bottom_right_x = float(raw_block.get("bottom_right_x", 0) or 0)
    bottom_right_y = float(raw_block.get("bottom_right_y", 0) or 0)
    confidence_scores = raw_block.get("confidence_scores") or {}
    confidence = confidence_scores.get("aggregate") or confidence_scores.get("page") or 1.0
    try:
        confidence_value = float(confidence)
    except (TypeError, ValueError):
        confidence_value = 1.0
    return {
        "block_id": str(raw_block.get("id") or f"OCRBLK-{page_index + 1:03d}-{block_index + 1:03d}"),
        "block_type": str(raw_block.get("type") or "text").strip().lower() or "text",
        "text": str(raw_block.get("content") or "").strip(),
        "bbox": [top_left_x, top_left_y, bottom_right_x, bottom_right_y],
        "confidence": confidence_value,
    }


def _normalize_page(raw_page: dict[str, Any], page_offset: int) -> dict[str, Any]:
    page_index = int(raw_page.get("index", page_offset) or 0)
    blocks = raw_page.get("blocks") or []
    return {
        "page_index": page_index,
        "text": str(raw_page.get("markdown") or "").strip(),
        "blocks": [_normalize_block(block, page_index, idx) for idx, block in enumerate(blocks) if isinstance(block, dict)],
    }


class MistralOCRProvider:
    name = "mistral"

    def __init__(self, *, allow_remote: bool) -> None:
        self.allow_remote = allow_remote

    def run(self, request: OCRProviderRequest) -> dict[str, Any]:
        if not self.allow_remote:
            raise RuntimeError("remote OCR is disabled; use fixture provider or enable allow_remote explicitly")

        api_key = os.environ.get("MISTRAL_API_KEY")
        if not api_key:
            raise RuntimeError("MISTRAL_API_KEY is not set")

        client = _build_mistral_client(api_key)
        response = client.ocr.process(
            model=request.model,
            document={"type": "image_url", "image_url": _data_url_for_file(request.file_path)},
            pages=request.pages,
            include_blocks=request.include_blocks,
            table_format=request.table_format,
            extract_header=request.extract_header,
            extract_footer=request.extract_footer,
            confidence_scores_granularity=request.confidence_granularity,
        )
        payload = _serialize_response(response)
        return {
            "provider": self.name,
            "exact_model": str(payload.get("model") or request.model),
            "remote_call_count": 1,
            "usage_info": payload.get("usage_info", {}),
            "pages": [_normalize_page(page, idx) for idx, page in enumerate(payload.get("pages") or []) if isinstance(page, dict)],
        }
