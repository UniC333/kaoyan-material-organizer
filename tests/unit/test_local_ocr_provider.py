from __future__ import annotations

import builtins
import importlib.util
import json
import os
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest
from PIL import Image, ImageDraw, ImageFont

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import config
import ocr_document
from common import sha256_for_file
from ocr.providers.base import OCRProviderRequest
from ocr.providers.fixture import FixtureOCRProvider
from ocr.providers.mistral import MistralOCRProvider
from ocr.providers import rapidocr
from ocr.providers.rapidocr import MODEL_ID, RapidOCRProvider
from ocr.providers.vision import VisionOCRProvider, transcription_model_id, transcription_path


def _engine_unavailable_reason() -> str:
    if importlib.util.find_spec("rapidocr_onnxruntime") is None:
        return 'rapidocr-onnxruntime is not installed; install -e ".[ocr-local]" (dependency/model download may require network)'
    try:
        rapidocr._build_engine()
    except Exception as exc:
        return str(exc)
    return ""


ENGINE_UNAVAILABLE = _engine_unavailable_reason()


def make_image(path: Path, text: str, *, chinese: bool = False) -> Path:
    if chinese:
        candidates = [
            Path(os.environ.get("KAOYAN_TEST_CJK_FONT", "__unset__")),
            Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
            Path("C:/Windows/Fonts/msyh.ttc"),
            Path("/System/Library/Fonts/PingFang.ttc"),
        ]
        font_path = next((item for item in candidates if item.is_file()), None)
        if font_path is None:
            pytest.skip("Chinese image generation requires a CJK font; set KAOYAN_TEST_CJK_FONT")
        font = ImageFont.truetype(str(font_path), 48)
    else:
        # Pillow 10.1+ supports size; Pillow 10.0 requires the no-argument form.
        try:
            font = ImageFont.load_default(size=48)
        except TypeError:
            font = ImageFont.load_default()
    image = Image.new("RGB", (1100, 180), "white")
    ImageDraw.Draw(image).text((40, 50), text, font=font, fill="black")
    image.save(path)
    return path


def request(path: Path, *, model: str = MODEL_ID) -> OCRProviderRequest:
    return OCRProviderRequest(path, "0-0", model, True, "word", "html", True, True)


@pytest.fixture
def isolated_runtime(tmp_path, monkeypatch):
    selected = tmp_path / "config.json"
    selected.write_text(json.dumps({
        "workspace_root": str(tmp_path), "vault_root": str(tmp_path / "vault"),
        "kb_root": str(tmp_path / "isolated-kb"), "ocr_cache_root": str(tmp_path / "ocr"),
        "ocr_provider": "rapidocr", "ocr_allow_remote": False,
    }), encoding="utf-8")
    for key in tuple(os.environ):
        if key.startswith("KAOYAN_") or key == "MISTRAL_API_KEY":
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("KAOYAN_CONFIG_FILE", str(selected))
    config.reset_runtime_config_cache()
    yield config.load_runtime_config()
    config.reset_runtime_config_cache()


@pytest.mark.skipif(bool(ENGINE_UNAVAILABLE), reason=ENGINE_UNAVAILABLE)
@pytest.mark.parametrize("text,chinese", [("LOCAL OCR 12345", False), ("考研数学学习 2027", True)])
def test_real_local_ocr_on_pillow_image(tmp_path, isolated_runtime, text, chinese):
    source = make_image(tmp_path / "pixels.png", text, chinese=chinese)
    result = ocr_document.run_ocr_for_file(source_file=source, runtime=isolated_runtime)
    recognized = result["normalized_payload"]["pages"][0]["text"]
    assert "".join(text.split()) in "".join(recognized.split())
    assert result["provider"] == "rapidocr"
    assert result["exact_model"] == MODEL_ID
    assert result["remote_calls"] == 0
    assert result["cache_hit"] is False
    assert result["published_evidence"] is False
    assert result["normalized_payload"]["pages"][0]["blocks"]
    cached = ocr_document.run_ocr_for_file(source_file=source, runtime=isolated_runtime)
    assert cached["cache_hit"] is True
    assert cached["request_key"] == result["request_key"]


@pytest.mark.parametrize("name,expected", [
    ("rapidocr", RapidOCRProvider), ("vision", VisionOCRProvider),
    ("fixture", FixtureOCRProvider), ("mistral", MistralOCRProvider),
])
def test_provider_selection(name, expected):
    assert isinstance(ocr_document.provider_for(name, allow_remote=False, fixture_json=None), expected)


def test_unknown_provider_is_rejected():
    with pytest.raises(SystemExit, match="unsupported OCR provider"):
        ocr_document.provider_for("unknown", allow_remote=False, fixture_json=None)


def test_config_selects_local_model_and_cli_provider_override(isolated_runtime):
    assert isolated_runtime.ocr_provider == "rapidocr"
    assert isolated_runtime.ocr_model == MODEL_ID
    remote = replace(isolated_runtime, ocr_provider="mistral", ocr_model="mistral-ocr-4-0")
    assert ocr_document.resolve_ocr_model("rapidocr", None, remote) == MODEL_ID
    assert ocr_document.resolve_ocr_model("mistral", None, remote) == "mistral-ocr-4-0"
    assert ocr_document.resolve_ocr_model("rapidocr", "explicit", remote) == "explicit"


def test_environment_can_select_local_provider(tmp_path, monkeypatch):
    selected = tmp_path / "config.json"
    selected.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("KAOYAN_CONFIG_FILE", str(selected))
    monkeypatch.setenv("KAOYAN_OCR_PROVIDER", "rapidocr")
    monkeypatch.delenv("KAOYAN_OCR_MODEL", raising=False)
    config.reset_runtime_config_cache()
    try:
        assert config.load_runtime_config().ocr_model == MODEL_ID
        assert config.load_runtime_config().ocr_provider == "rapidocr"
    finally:
        config.reset_runtime_config_cache()


def test_missing_optional_dependency_is_actionable(tmp_path, monkeypatch):
    source = make_image(tmp_path / "pixels.png", "LOCAL OCR 12345")
    original_import = builtins.__import__

    def without_rapidocr(name, *args, **kwargs):
        if name == "rapidocr_onnxruntime":
            raise ModuleNotFoundError("No module named 'rapidocr_onnxruntime'")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", without_rapidocr)
    with pytest.raises(RuntimeError, match=r'pip install -e "\.\[ocr-local\]"'):
        RapidOCRProvider().run(request(source))


def test_model_initialization_failure_is_explicit(monkeypatch):
    from types import SimpleNamespace

    def broken_engine(**kwargs):
        raise OSError("model file unavailable")

    monkeypatch.setitem(sys.modules, "rapidocr_onnxruntime", SimpleNamespace(RapidOCR=broken_engine))
    with pytest.raises(RuntimeError, match="engine/model initialization failed: model file unavailable"):
        rapidocr._build_engine()


@pytest.mark.parametrize("change,error", [
    ({"pages": "1-2"}, "only --pages 0-0"),
    ({"model": "unavailable-model"}, "supports only the bundled model"),
])
def test_unsupported_local_requests_fail_before_loading_engine(tmp_path, change, error):
    source = make_image(tmp_path / "pixels.png", "LOCAL OCR 12345")
    with pytest.raises(RuntimeError, match=error):
        RapidOCRProvider().run(replace(request(source), **change))


def write_transcription(source: Path, text: str = "Visual transcription 12345", **overrides):
    # Import contract data, never presented as engine recognition.
    payload = {"provider": "vision", "exact_model": "test-visual-reader",
               "source_file_sha256": sha256_for_file(source), "text": text}
    payload.update(overrides)
    transcription_path(source).write_text(json.dumps(payload), encoding="utf-8")


def test_vision_import_uses_pipeline_and_invalidates_changed_transcription(tmp_path, isolated_runtime):
    source = make_image(tmp_path / "pixels.png", "Visual transcription 12345")
    write_transcription(source, review_status="accepted")
    first = ocr_document.run_ocr_for_file(source_file=source, runtime=isolated_runtime, provider_name="vision")
    assert first["provider"] == "vision"
    assert first["exact_model"] == "test-visual-reader"
    assert first["remote_calls"] == 0
    assert first["published_evidence"] is False
    block = first["normalized_payload"]["chunk_candidates"][0]
    assert block["confidence"] == 0.0
    assert "review_status" not in block
    from ocr.review import _review_reasons
    assert _review_reasons(block) == ["low-confidence"]
    cached = ocr_document.run_ocr_for_file(source_file=source, runtime=isolated_runtime, provider_name="vision")
    assert cached["cache_hit"] is True
    write_transcription(source, text="Corrected visual transcription")
    changed = ocr_document.run_ocr_for_file(source_file=source, runtime=isolated_runtime, provider_name="vision")
    assert changed["request_key"] != first["request_key"]
    assert changed["cache_hit"] is False
    from ocr_book_pages import _request_key_for_page
    assert _request_key_for_page({"source_image_path": str(source)}, isolated_runtime, "vision", "ignored") == changed["request_key"]


@pytest.mark.parametrize("overrides,error", [
    ({"source_file_sha256": "wrong"}, "does not match"),
    ({"provider": "fixture"}, 'must declare provider="vision"'),
    ({"exact_model": ""}, "actual exact_model"),
    ({"text": ""}, "nonempty text"),
])
def test_vision_rejects_invalid_transcription(tmp_path, overrides, error):
    source = make_image(tmp_path / "pixels.png", "Visual transcription 12345")
    write_transcription(source, **overrides)
    with pytest.raises(RuntimeError, match=error):
        VisionOCRProvider().run(request(source, model=transcription_model_id(source)))


def test_vision_requires_transcription_and_blocks(tmp_path):
    source = make_image(tmp_path / "pixels.png", "Visual transcription 12345")
    with pytest.raises(RuntimeError, match="transcription missing"):
        transcription_model_id(source)
    write_transcription(source)
    with pytest.raises(RuntimeError, match="ocr_include_blocks=true"):
        VisionOCRProvider().run(replace(request(source), include_blocks=False))


def test_vision_rejects_changed_or_oversized_transcription(tmp_path):
    from ocr.providers.vision import MAX_TRANSCRIPTION_BYTES

    source = make_image(tmp_path / "pixels.png", "Visual transcription 12345")
    write_transcription(source)
    original_request = request(source, model=transcription_model_id(source))
    write_transcription(source, text="Changed transcription")
    with pytest.raises(RuntimeError, match="transcription changed"):
        VisionOCRProvider().run(original_request)
    transcription_path(source).write_bytes(b"x" * (MAX_TRANSCRIPTION_BYTES + 1))
    with pytest.raises(RuntimeError, match="1 MiB per-image limit"):
        transcription_model_id(source)


def test_vision_book_pipeline_records_request_and_pending_review(tmp_path, isolated_runtime):
    from ocr_book_pages import run_book_ocr
    from ocr.review import queue_review_items

    source = make_image(tmp_path / "pixels.png", "Visual transcription 12345")
    write_transcription(source)
    metadata = tmp_path / "book" / "metadata"
    metadata.mkdir(parents=True)
    (metadata / "page_assets.json").write_text(json.dumps({
        "book_id": "BOOK-TEST", "items": [{
            "page_id": "PAGE-1", "book_id": "BOOK-TEST", "scan_index": 1,
            "source_image_path": str(source), "source_image_sha256": sha256_for_file(source),
            "quality_status": "accepted",
        }],
    }), encoding="utf-8")
    kwargs = dict(book_root=metadata.parent, provider_name="vision", model=None,
                  fixture_json=None, allow_remote=False, yes=False, max_retries=2)
    preview = run_book_ocr(**kwargs, dry_run=True)
    assert not (metadata / "page_ocr_status.json").exists()
    result = run_book_ocr(**kwargs)
    assert result["summary"]["completed_count"] == 1
    assert result["remote_requests"] == 0
    status = json.loads((metadata / "page_ocr_status.json").read_text())
    assert status["items"][0]["request_key"] == preview["selection"]["items"][0]["request_key"]
    assert status["items"][0]["printed_page"] is None
    review = queue_review_items(book_root=metadata.parent, review_type=None)
    assert review["summary"]["pending_count"] == 1
    assert run_book_ocr(**kwargs)["summary"]["cached_count"] == 1


def test_vision_photo_publication_requires_explicit_block_review(tmp_path, isolated_runtime):
    from publish_book_ocr_evidence import collect_publication_items
    from ocr.review import apply_overlay

    source = make_image(tmp_path / "pixels.png", "Visual transcription 12345")
    write_transcription(source)
    result = ocr_document.run_ocr_for_file(source_file=source, runtime=isolated_runtime, provider_name="vision")
    kwargs = dict(runtime=isolated_runtime, root=tmp_path,
                  assets={"items": [{"page_id": "PAGE-1"}]},
                  status={"items": [{"page_id": "PAGE-1", "status": "completed", **result}]},
                  classes={"PAGE-1": {"classification_status": "confirmed"}},
                  locator_by_page={"PAGE-1": {"printed_page": 42}})
    items, blocked = collect_publication_items(**kwargs)
    assert items == []
    assert blocked[0]["reason"] == "sensitive-block-review-pending"
    apply_overlay(request_key=result["request_key"], block_id="OCRBLK-001-001",
                 review_status="accepted", corrected_text="Reviewed visual transcription", note="checked pixels")
    items, blocked = collect_publication_items(**kwargs)
    assert blocked == []
    assert items[0]["content"] == "Reviewed visual transcription"


def test_vision_pdf_publication_requires_block_review_and_preserves_both_pages(tmp_path, isolated_runtime):
    from publish_pdf_ocr_evidence import _validate_pdf_page

    source = make_image(tmp_path / "pixels.png", "Visual transcription 12345")
    write_transcription(source)
    result = ocr_document.run_ocr_for_file(source_file=source, runtime=isolated_runtime, provider_name="vision")
    image_sha = sha256_for_file(source)
    key = result["request_key"]
    binding = dict(request_key=key, source_file_sha256="registered-pdf-sha",
                   source_image_sha256=image_sha, pdf_page=7, printed_page=42,
                   review_status="accepted", page_header_verified=True)
    kwargs = dict(
        page={**binding, "normalized_path": result["normalized_path"], "ocr_source_image_path": str(source)},
        handoff={**binding, "page_id": "PDFPAGE-SRC-TEST-0007", "classification_status": "confirmed",
                 "chapter_id": "CH-1", "chapter_title": "Chapter 1"},
        decision=binding, source={"source_id": "SRC-TEST"},
        source_file={"sha256": "registered-pdf-sha"}, source_sha="registered-pdf-sha",
        report_path=tmp_path / "report.json", artifact_path=tmp_path / "artifact.json",
        overlay_path=tmp_path / "overlay.json",
        locator_index={"entries": [{"source_id": "SRC-TEST", "pdf_page": 7,
                                   "printed_page": 42, "source_file_sha256": "registered-pdf-sha"}]},
    )
    prepared, blocked, _ = _validate_pdf_page(**kwargs, overlay={})
    assert prepared is None
    assert any(item["reason"] == "sensitive-block-review-pending" for item in blocked)
    prepared, blocked, _ = _validate_pdf_page(**kwargs, overlay={
        "OCRBLK-001-001": {"review_status": "accepted", "corrected_text": "Reviewed visual transcription"},
    })
    assert blocked == []
    assert prepared["pdf_page"] == 7
    assert prepared["printed_page"] == 42
    assert prepared["content"] == "Reviewed visual transcription"


def test_vision_cli_without_api_key(tmp_path, isolated_runtime):
    source = make_image(tmp_path / "pixels.png", "Visual transcription 12345")
    write_transcription(source)
    completed = subprocess.run([
        sys.executable, str(SCRIPTS / "ocr_document.py"), "--file", str(source),
        "--provider", "vision", "--format", "json",
    ], capture_output=True, text=True, env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))
    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout)
    assert result["remote_calls"] == 0
    assert result["published_evidence"] is False
    normalized = json.loads(Path(result["normalized_path"]).read_text(encoding="utf-8"))
    assert normalized["pages"][0]["text"] == "Visual transcription 12345"


def test_provider_override_from_rapidocr_uses_mistral_default(isolated_runtime, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["ocr_document.py", "--file", "pixels.png", "--provider", "mistral"])
    args = ocr_document.parse_args()
    assert isolated_runtime.ocr_provider == "rapidocr"
    assert isolated_runtime.ocr_model == MODEL_ID
    assert ocr_document.resolve_ocr_model(args.provider, args.model, isolated_runtime) == "mistral-ocr-4-0"
    assert ocr_document.resolve_ocr_model("vision", None, isolated_runtime) == ""
    for provider in ("mistral", "rapidocr", "vision"):
        assert ocr_document.resolve_ocr_model(provider, "explicit", isolated_runtime) == "explicit"


def test_make_image_falls_back_to_pillow_10_default_font(tmp_path, monkeypatch):
    original = ImageFont.load_default
    calls = []

    def legacy_load_default(*args, **kwargs):
        calls.append(kwargs)
        if kwargs:
            raise TypeError("load_default() got an unexpected keyword argument 'size'")
        return original()

    monkeypatch.setattr(ImageFont, "load_default", legacy_load_default)
    path = make_image(tmp_path / "legacy.png", "LOCAL OCR 12345")
    assert calls == [{"size": 48}, {}]
    with Image.open(path) as image:
        assert image.size == (1100, 180)


@pytest.mark.parametrize("publication_kind", ["photo", "pdf"])
@pytest.mark.parametrize("change", ["text", "whitespace", "missing"])
def test_changed_vision_transcription_blocks_publication(
    tmp_path, monkeypatch, isolated_runtime, publication_kind, change,
):
    import publish_book_ocr_evidence as photo_publish
    import publish_pdf_ocr_evidence as pdf_publish
    from ocr.review import apply_overlay
    from publication_support import read_only_layout

    def write_json(path, payload):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload), encoding="utf-8")

    source = make_image(tmp_path / "pixels.png", "Visual transcription 12345")
    write_transcription(source)
    result = ocr_document.run_ocr_for_file(
        source_file=source, runtime=isolated_runtime, provider_name="vision",
    )
    apply_overlay(request_key=result["request_key"], block_id="OCRBLK-001-001",
                  review_status="accepted", corrected_text="Reviewed transcription", note="checked pixels")
    image_sha = sha256_for_file(source)
    layout = read_only_layout(isolated_runtime.kb_root)
    if publication_kind == "photo":
        publisher = photo_publish
        book_root = tmp_path / "book"
        metadata = book_root / isolated_runtime.paper_book_metadata_dir
        binding = {"page_id": "PAGE-1", "source_id": "SRC-TEST", "scan_index": 1,
                   "source_image_path": str(source), "source_image_sha256": image_sha}
        write_json(book_root / "book.yaml", {"subject": "数学", "book_id": "BOOK-TEST"})
        write_json(metadata / "page_assets.json", {"book_id": "BOOK-TEST", "items": [binding]})
        write_json(metadata / "page_ocr_status.json", {
            "items": [{**binding, "status": "completed", "request_key": result["request_key"],
                       "normalized_path": result["normalized_path"]}],
        })
        write_json(metadata / "page_classifications.json", {
            "items": [{"page_id": "PAGE-1", "classification_status": "confirmed", "chapter_id": "CH-1"}],
        })
        locator = {"entries": [{**binding, "printed_page": 42}]}
        kwargs = {"book_root": book_root}
    else:
        publisher = pdf_publish
        pdf_path = tmp_path / "source.pdf"
        pdf_path.write_bytes(b"registered-pdf")
        pdf_sha = sha256_for_file(pdf_path)
        binding = {"pdf_page": 7, "printed_page": 42, "source_file_sha256": pdf_sha,
                   "source_image_sha256": image_sha, "request_key": result["request_key"],
                   "review_status": "accepted", "page_header_verified": True}
        scope = {"subject": "数学", "book_title": "书", "pdf_source_id": "SRC-TEST",
                 "source_file_sha256": pdf_sha}
        write_json(layout["sources"] / "SRC-TEST.json", {
            "source_id": "SRC-TEST", "subject": "数学", "source_name": "书",
            "material_type": "book-pdf", "status": "active",
            "files": [{"absolute_path": str(pdf_path), "sha256": pdf_sha}],
        })
        report_path = tmp_path / "report.json"
        write_json(report_path, {**scope, "pdf_path": str(pdf_path), "pages": [{
            **binding, "normalized_path": result["normalized_path"], "ocr_source_image_path": str(source),
        }]})
        page_review_path = layout["review_queues"] / "pdf-page-review" / "SRC-TEST.json"
        write_json(page_review_path, {"source_id": "SRC-TEST", "source_file_sha256": pdf_sha,
                                     "items": [binding]})
        artifact_path = tmp_path / "artifact.json"
        write_json(artifact_path, {
            **scope, "pdf_ocr_report_path": str(report_path),
            "pdf_ocr_report_sha256": sha256_for_file(report_path),
            "page_review_queue_path": str(page_review_path),
            "page_review_sha256": sha256_for_file(page_review_path),
            "classify_handoff_ledger": [{**binding, "page_id": "PDFPAGE-SRC-TEST-0007",
                                        "classification_status": "confirmed",
                                        "chapter_id": "CH-1", "chapter_title": "第一章"}],
        })
        locator = {"entries": [{"source_id": "SRC-TEST", "pdf_page": 7, "printed_page": 42,
                                "source_file_sha256": pdf_sha}]}
        kwargs = {"subject": "数学", "book_title": "书", "pdf_source_id": "SRC-TEST",
                  "report_path": report_path, "review_artifact_path": artifact_path}

    monkeypatch.setattr(publisher, "load_page_locator_index", lambda: locator)
    before = publisher.publish(**kwargs)
    assert before["can_execute"] is True
    assert before["blocked"] == []
    transcription = transcription_path(source)
    assert any(item["path"] == str(transcription) and item["sha256"] == sha256_for_file(transcription)
               for item in before["inputs"])
    if change == "text":
        write_transcription(source, text="Changed without re-import")
    elif change == "whitespace":
        transcription.write_bytes(transcription.read_bytes() + b"\n")
    else:
        transcription.unlink()
    after = publisher.publish(**kwargs)
    assert after["can_execute"] is False
    assert after["plan_fingerprint"] != before["plan_fingerprint"]
    assert after["blocked"][0]["reason"] == "vision-transcription-hash-mismatch"
    assert "re-import" in after["blocked"][0]["detail"]
    assert after["items"] == []
    with pytest.raises(SystemExit, match="fingerprint changed"):
        publisher.publish(**kwargs, yes=True, expected_plan_fingerprint=before["plan_fingerprint"])
    blocked = publisher.publish(**kwargs, yes=True, expected_plan_fingerprint=after["plan_fingerprint"])
    assert blocked["publish_status"] == "blocked"
    assert blocked["published_count"] == 0
    assert not list(layout["evidence"].glob("*.json"))
