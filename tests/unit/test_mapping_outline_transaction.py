from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
import approve_pdf_page_mapping_interval as mapping
import apply_pdf_ocr_outline as outline
import kb
from kaoyan_kb.cli.book_commands import dispatch_book


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def setup_route(monkeypatch, tmp_path, route):
    layout = {name: tmp_path / name for name in ("sources", "indexes", "review_queues")}
    write(layout["sources"] / "SRC.json", {"files": [{"sha256": "sha"}]})
    module = mapping if route == "mapping" else outline
    monkeypatch.setattr(module, "kb_layout", lambda: layout)
    if route == "mapping":
        write(layout["indexes"] / "pdf_page_mapping_candidates/SRC.json", {
            "source_file_sha256": "sha", "items": [{"pdf_page": 121, "printed_page_candidate": 109},
                                                      {"pdf_page": 122, "printed_page_candidate": 110}]})
        call = lambda **extra: mapping.approve_interval(pdf_source_id="SRC", pdf_start=121, pdf_end=122,
            printed_start=109, printed_end=110, note="reviewed endpoints", **extra)
    else:
        write(tmp_path / "outline.json", {"pdf_source_id": "SRC", "source_file_sha256": "sha",
            "items": [{"printed_page_start": 109, "chapter_title": "chapter"}]})
        write(layout["review_queues"] / "pdf-page-review/SRC.json", {"source_id": "SRC", "items": [
            {"pdf_page": 121, "printed_page": 109, "review_status": "accepted",
             "page_header_verified": True, "source_file_sha256": "sha"}]})
        write(layout["indexes"] / "pdf_ocr_review_status/math-Book.json", {
            "page_classifications_path": str(tmp_path / "metadata/classes.json"),
            "chapter_definitions_path": str(tmp_path / "metadata/chapters.json")})
        call = lambda **extra: outline.apply_outline(subject="math", book_title="Book", pdf_source_id="SRC",
            outline_path=tmp_path / "outline.json", **extra)
    return module, layout, call


def state(root):
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()}


@pytest.mark.parametrize("route", ["mapping", "outline"])
def test_preview_and_missing_fingerprint_are_zero_write(monkeypatch, tmp_path, route):
    _, _, call = setup_route(monkeypatch, tmp_path, route)
    before = state(tmp_path)
    dirs = {p for p in tmp_path.rglob("*") if p.is_dir()}
    plan = call()
    assert plan["preview_only"] is True
    assert call()["plan_fingerprint"] == plan["plan_fingerprint"]
    with pytest.raises(SystemExit, match="fingerprint"):
        call(yes=True)
    assert state(tmp_path) == before
    assert {p for p in tmp_path.rglob("*") if p.is_dir()} == dirs


@pytest.mark.parametrize("route", ["mapping", "outline"])
def test_input_drift_is_rejected_before_writes(monkeypatch, tmp_path, route):
    _, layout, call = setup_route(monkeypatch, tmp_path, route)
    plan = call()
    source_path = layout["sources"] / "SRC.json"
    source_path.write_text(source_path.read_text() + "\n", encoding="utf-8")
    before = state(tmp_path)
    with pytest.raises(SystemExit, match="fingerprint"):
        call(yes=True, expected_plan_fingerprint=plan["plan_fingerprint"])
    assert state(tmp_path) == before


@pytest.mark.parametrize("route,fail_at", [("mapping", 2), ("outline", 2), ("outline", 3)])
@pytest.mark.parametrize("existing", [False, True])
def test_later_write_failure_restores_exact_target_bytes(monkeypatch, tmp_path, route, fail_at, existing):
    module, _, call = setup_route(monkeypatch, tmp_path, route)
    plan = call()
    if existing:
        for target in plan["writes"]["paths"]:
            write(Path(target), {"items": [], "prior": "content"})
    plan = call()
    before = state(tmp_path)
    save = module.save_json
    count = 0

    def fail(path, value, **kwargs):
        nonlocal count
        count += 1
        save(path, value, **kwargs)
        if count == fail_at:
            raise OSError("injected write failure")

    monkeypatch.setattr(module, "save_json", fail)
    with pytest.raises(OSError, match="injected"):
        call(yes=True, expected_plan_fingerprint=plan["plan_fingerprint"])
    assert state(tmp_path) == before


@pytest.mark.parametrize("route", ["mapping", "outline"])
def test_success_keeps_independent_page_numbers(monkeypatch, tmp_path, route):
    _, _, call = setup_route(monkeypatch, tmp_path, route)
    plan = call()
    result = call(yes=True, expected_plan_fingerprint=plan["plan_fingerprint"])
    assert result["preview_only"] is False
    target = result["page_review_path"] if route == "mapping" else result["page_classifications_path"]
    row = json.loads(Path(target).read_text(encoding="utf-8"))["items"][0]
    assert (row["pdf_page"], row["printed_page"]) == (121, 109)


def test_outline_rejects_missing_bridge_and_conflicting_reviews(monkeypatch, tmp_path):
    _, layout, call = setup_route(monkeypatch, tmp_path, "outline")
    bridge = layout["indexes"] / "pdf_ocr_review_status/math-Book.json"
    write(bridge, {})
    with pytest.raises(SystemExit, match="bridge"):
        call()
    reviews = layout["review_queues"] / "pdf-page-review/SRC.json"
    payload = json.loads(reviews.read_text())
    payload["items"].append({**payload["items"][0], "review_status": "pending"})
    write(reviews, payload)
    with pytest.raises(SystemExit, match="conflicting"):
        call()


def test_cli_accepts_and_forwards_mapping_fingerprint():
    parser = kb.build_parser()
    args = parser.parse_args(['book', 'pdf-ocr-approve-mapping-interval', '--pdf-source-id', 'SRC',
        '--pdf-start', '121', '--pdf-end', '122', '--printed-start', '109', '--printed-end', '110',
        '--visual-review-note', 'reviewed', '--yes', '--plan-fingerprint', 'fp'])
    calls = []
    dispatch_book(args, lambda *values: calls.append(values))
    assert '--plan-fingerprint' in calls[0]
    assert 'fp' in calls[0]
