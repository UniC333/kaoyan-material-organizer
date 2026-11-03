from __future__ import annotations

from typing import Any

from ocr.providers.base import OCRProviderRequest


# Pin the bundled models as well as the engine in the optional dependency.
MODEL_ID = "rapidocr-onnxruntime-1.4.4-bundled"


def _build_engine() -> Any:
    try:
        from rapidocr_onnxruntime import RapidOCR
    except ImportError as exc:
        raise RuntimeError(
            'RapidOCR local OCR requires the optional dependency; '
            'install it with: python -m pip install -e ".[ocr-local]" '
            f"(import failed: {exc})"
        ) from exc
    try:
        return RapidOCR(
            det_use_cuda=False, cls_use_cuda=False, rec_use_cuda=False,
            intra_op_num_threads=1, inter_op_num_threads=1,
        )
    except Exception as exc:
        raise RuntimeError(f"RapidOCR local engine/model initialization failed: {exc}") from exc


def require_single_image(request: OCRProviderRequest) -> None:
    if request.pages not in {"0", "0-0"}:
        raise RuntimeError("local image providers accept only --pages 0-0; render PDF pages first with book ocr-pdf-source")
    from PIL import Image

    with Image.open(request.file_path) as image:
        if getattr(image, "n_frames", 1) != 1:
            raise RuntimeError("local OCR requires a single-frame image; split multi-page images first")
        image.verify()


class RapidOCRProvider:
    name = "rapidocr"

    def __init__(self) -> None:
        self._engine: Any = None

    def run(self, request: OCRProviderRequest) -> dict[str, Any]:
        if request.model != MODEL_ID:
            raise RuntimeError(f"rapidocr supports only the bundled model: --model {MODEL_ID}")
        require_single_image(request)
        if self._engine is None:
            self._engine = _build_engine()
        # The pinned package ships its ONNX models. Inference reads local pixels;
        # no API keys, HTTP clients or remote permissions are involved.
        result, _ = self._engine(str(request.file_path))
        blocks = []
        lines = []
        for index, (quad, text, score) in enumerate(result or []):
            lines.append(str(text))
            xs = [float(point[0]) for point in quad]
            ys = [float(point[1]) for point in quad]
            blocks.append({
                "block_id": f"OCRBLK-001-{index + 1:03d}",
                "block_type": "paragraph",
                "text": str(text),
                "bbox": [min(xs), min(ys), max(xs), max(ys)],
                "confidence": float(score),
            })
        return {
            "provider": self.name,
            "exact_model": MODEL_ID,
            "remote_call_count": 0,
            "pages": [{"page_index": 0, "text": "\n".join(lines),
                       "blocks": blocks if request.include_blocks else []}],
        }
