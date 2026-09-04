from __future__ import annotations

import json
import sys
from argparse import Namespace
from pathlib import Path

import pytest


SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import build_search_index
from kaoyan_kb.domain import evidence_publication as publication
from kaoyan_kb.domain import evidence_publication_repair as repair


def evidence(
    evidence_id: str,
    *,
    origin_type: str = "paper_book_reviewed_ocr",
    mapping_status: str = "",
    pdf_page: int | None = None,
    printed_page: int | None = None,
    source_id: str = "SRC-1",
    source_sha: str = "source-sha",
) -> dict:
    span = {
        "source_id": source_id,
        "file_id": f"FILE-{evidence_id}",
        "source_file_sha256": source_sha,
        "locator": {"page_start": 1, "page_end": 1, "image_start": 1, "image_end": 1},
    }
    payload = {
        "evidence_id": evidence_id,
        "evidence_key": f"{evidence_id}-key",
        "subject": "408",
        "source_id": source_id,
        "chapter_id": "CH-1",
        "chunk_id": f"CHUNK-{evidence_id}",
        "title": "source title",
        "content": "source content",
        "origin_type": origin_type,
        "verification_status": "reviewed",
        "review_status": "accepted",
        "source_grounded": True,
        "source_spans": [span],
        "provenance": {
            "origin_type": origin_type,
            "verification_status": "reviewed",
            "source_grounded": True,
            "source_spans": [span],
        },
    }
    if mapping_status:
        payload["mapping_status"] = mapping_status
    if pdf_page is not None:
        payload["pdf_page"] = pdf_page
    if printed_page is not None:
        payload["printed_page"] = printed_page
    return payload


def claim(claim_id: str, evidence_ids: list[str]) -> dict:
    return {
        "claim_id": claim_id,
        "claim_key": f"{claim_id}-key",
        "subject": "408",
        "syllabus_node_id": "NODE-1",
        "claim_type": "rule",
        "canonical_text": "canonical source claim",
        "evidence_ids": evidence_ids,
        "status": "accepted",
    }


def write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def layout_for(tmp_path: Path) -> dict[str, Path]:
    layout = {
        "root": tmp_path / ".kaoyan-kb",
        "manifests": tmp_path / ".kaoyan-kb" / "manifests",
        "manifest_sources": tmp_path / ".kaoyan-kb" / "manifests" / "sources",
        "sources": tmp_path / ".kaoyan-kb" / "sources",
        "evidence": tmp_path / ".kaoyan-kb" / "evidence",
        "claims": tmp_path / ".kaoyan-kb" / "claims",
        "conflicts": tmp_path / ".kaoyan-kb" / "conflicts",
        "indexes": tmp_path / ".kaoyan-kb" / "indexes",
        "review_queues": tmp_path / ".kaoyan-kb" / "review-queues",
    }
    for path in layout.values():
        path.mkdir(parents=True, exist_ok=True)
    (layout["review_queues"] / "pdf-page-review").mkdir(parents=True, exist_ok=True)
    return layout


def test_nested_locator_is_authoritative_and_root_mirror_is_compatibility_only() -> None:
    payload = evidence("EV-NESTED")
    payload.pop("locator", None)
    decision = publication.evidence_publication_decision(payload)
    assert decision["publishable"] is True
    assert decision["top_level_locator_compatibility"] == "ignored_when_source_spans_locator_is_complete"


def test_profile_hint_is_audit_only_and_never_publishable() -> None:
    decision = publication.evidence_publication_decision(evidence("EV-PROFILE", origin_type="profile_hint"))
    assert decision["publishable"] is False
    assert decision["classification"] == "audit_only"
    assert "forbidden-origin-type" in decision["audit_reason_codes"]


def test_pdf_requires_independent_pages_and_formal_mapping() -> None:
    payload = evidence("EV-PDF", origin_type="pdf_page_ocr", mapping_status="unmapped", pdf_page=79, printed_page=67)
    decision = publication.evidence_publication_decision(payload)
    assert decision["publishable"] is False
    assert "pdf-mapping-not-mapped" in decision["audit_reason_codes"]
    payload["mapping_status"] = "mapped"
    assert publication.is_publishable_source_evidence(payload)
    payload.pop("printed_page")
    assert not publication.is_publishable_source_evidence(payload)


def test_repair_mapping_ignores_stale_locator_index_and_rejects_duplicate_review_rows(tmp_path: Path) -> None:
    layout = layout_for(tmp_path)
    write_json(layout["sources"] / "SRC-PDF.json", {"source_id": "SRC-PDF", "files": [{"sha256": "source-sha"}]})
    payload = evidence("EV-PDF", origin_type="pdf_page_ocr", mapping_status="unmapped", pdf_page=79, printed_page=67, source_id="SRC-PDF")
    write_json(layout["indexes"] / "page_locator_index.json", {"entries": [{"source_id": "SRC-PDF", "pdf_page": 79, "printed_page": 67, "source_file_sha256": "source-sha"}]})
    write_json(layout["review_queues"] / "pdf-page-review" / "SRC-PDF.json", {"source_id": "SRC-PDF", "items": []})
    assert repair.formal_pdf_mapping_decision(layout, payload)["status"] == "unresolved"
    write_json(
        layout["review_queues"] / "pdf-page-review" / "SRC-PDF.json",
        {"source_id": "SRC-PDF", "items": [
            {"pdf_page": 79, "printed_page": 67, "review_status": "accepted", "page_header_verified": True, "source_file_sha256": "source-sha"},
            {"pdf_page": 79, "printed_page": 67, "review_status": "pending", "page_header_verified": False, "source_file_sha256": "source-sha"},
        ]},
    )
    assert repair.formal_pdf_mapping_decision(layout, payload)["reason"] == "formal-review-mapping-duplicate-or-conflict"


def test_claim_requires_every_support_record_to_be_publishable() -> None:
    valid = evidence("EV-VALID")
    invalid = evidence("EV-PROFILE", origin_type="profile_hint")
    decision = publication.claim_publication_decision(
        claim("CL-MIXED", ["EV-VALID", "EV-PROFILE"]),
        {"EV-VALID": valid, "EV-PROFILE": invalid},
    )
    assert decision["publishable"] is False
    assert decision["valid_support_count"] == 1
    assert "non-publishable-claim-evidence" in decision["audit_reason_codes"]


def test_build_index_excludes_ineligible_records_and_reports_reasons(tmp_path: Path) -> None:
    layout = layout_for(tmp_path)
    write_json(layout["evidence"] / "EV-VALID.json", evidence("EV-VALID"))
    write_json(layout["evidence"] / "EV-PROFILE.json", evidence("EV-PROFILE", origin_type="profile_hint"))
    write_json(
        layout["evidence"] / "EV-PDF.json",
        evidence("EV-PDF", origin_type="pdf_page_ocr", mapping_status="unmapped", pdf_page=79, printed_page=67),
    )
    write_json(layout["claims"] / "CL-VALID.json", claim("CL-VALID", ["EV-VALID"]))
    write_json(layout["claims"] / "CL-BLOCKED.json", claim("CL-BLOCKED", ["EV-PROFILE"]))

    result = build_search_index.build_index(
        layout,
        args=Namespace(
            warning_scan_threshold=5000,
            warning_parse_threshold=1000,
            hard_failure_scan_threshold=50000,
            hard_failure_parse_threshold=10000,
            format="quiet",
        ),
    )
    documents = json.loads((layout["indexes"] / "search_documents.json").read_text(encoding="utf-8"))["documents"]
    assert {item["doc_id"] for item in documents} == {"claim:CL-VALID", "evidence:EV-VALID"}
    assert result["exclusion_count"] == 3
    assert result["exclusion_summary"]["by_reason"]["forbidden-origin-type"] == 1
    assert result["exclusion_summary"]["by_reason"]["non-publishable-claim-evidence"] == 1
    assert result["exclusion_summary"]["by_reason"]["pdf-mapping-not-mapped"] == 1


def test_repair_preview_is_zero_write_and_apply_is_recoverable_and_index_consistent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    layout = layout_for(tmp_path)
    source = {"source_id": "SRC-PDF", "files": [{"file_id": "FILE-PDF", "sha256": "source-sha"}]}
    write_json(layout["sources"] / "SRC-PDF.json", source)
    write_json(
        layout["review_queues"] / "pdf-page-review" / "SRC-PDF.json",
        {
            "queue_type": "pdf-page-review",
            "source_id": "SRC-PDF",
            "items": [{"pdf_page": 79, "printed_page": 67, "review_status": "accepted", "page_header_verified": True, "source_file_sha256": "source-sha"}],
        },
    )
    pdf = evidence("EV-PDF", origin_type="pdf_page_ocr", mapping_status="unmapped", pdf_page=79, printed_page=67, source_id="SRC-PDF")
    profile = evidence("EV-PROFILE", origin_type="profile_hint")
    write_json(layout["evidence"] / "EV-PDF.json", pdf)
    write_json(layout["evidence"] / "EV-PROFILE.json", profile)
    write_json(layout["claims"] / "CL-PROFILE.json", claim("CL-PROFILE", ["EV-PROFILE"]))
    before_files = {path: path.read_bytes() for path in layout["root"].rglob("*") if path.is_file()}

    preview = repair.repair_publication(layout=layout, apply=False)
    assert preview["mode"] == "preview"
    assert preview["summary"]["write_count"] == 3
    assert {path: path.read_bytes() for path in layout["root"].rglob("*") if path.is_file()} == before_files

    internal_plan = repair.plan_publication_repairs(layout=layout)
    queue_path = layout["review_queues"] / "pdf-page-review" / "SRC-PDF.json"
    queue_payload = json.loads(queue_path.read_text(encoding="utf-8"))
    queue_payload["note"] = "changed after preview"
    write_json(queue_path, queue_payload)
    with pytest.raises(ValueError, match="input fingerprint changed"):
        repair.apply_publication_repairs(layout=layout, plan=internal_plan)
    write_json(
        queue_path,
        {
            "queue_type": "pdf-page-review",
            "source_id": "SRC-PDF",
            "items": [{"pdf_page": 79, "printed_page": 67, "review_status": "accepted", "page_header_verified": True, "source_file_sha256": "source-sha"}],
        },
    )

    def fake_refresh(current_layout: dict[str, Path]) -> dict:
        index_result = build_search_index.build_index(current_layout)
        return {"ok": True, "steps": ["build_page_locator_index", "build_exercise_locator_index", "build_search_index", "build_book_series_indexes"], "completed_steps": ["build_search_index"], "failed_steps": [], "search_result": index_result}

    monkeypatch.setattr(repair, "refresh_formal_indexes", fake_refresh)
    applied = repair.repair_publication(
        layout=layout,
        apply=True,
        expected_fingerprint=preview["input_fingerprint"],
    )
    assert applied["write_count"] == 3
    recovery_path = Path(applied["recovery_manifest"])
    recovery = json.loads(recovery_path.read_text(encoding="utf-8"))
    assert recovery["recoverable"] is True
    assert {item["entity_id"] for item in recovery["items"]} == {"EV-PDF", "EV-PROFILE", "CL-PROFILE"}
    assert json.loads((layout["evidence"] / "EV-PDF.json").read_text(encoding="utf-8"))["mapping_status"] == "mapped"
    assert json.loads((layout["evidence"] / "EV-PROFILE.json").read_text(encoding="utf-8"))["publication_status"] == "audit_only"
    assert json.loads((layout["claims"] / "CL-PROFILE.json").read_text(encoding="utf-8"))["status"] == "needs_review"
    documents = json.loads((layout["indexes"] / "search_documents.json").read_text(encoding="utf-8"))["documents"]
    assert {item["doc_id"] for item in documents} == {"evidence:EV-PDF"}
    assert all("before" in item and item["before_sha256"] for item in recovery["items"])

    restored = repair.restore_publication_repair(layout=layout, recovery_manifest=recovery_path, apply=True)
    assert restored["mode"] == "restore"
    assert json.loads((layout["evidence"] / "EV-PDF.json").read_text(encoding="utf-8"))["mapping_status"] == "unmapped"
