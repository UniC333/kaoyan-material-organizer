#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

from common import ensure_kb_layout, load_json, load_json_or_default, save_json, scan_json_files, stable_fingerprint


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--warning-scan-threshold", type=int, default=5000)
    parser.add_argument("--warning-parse-threshold", type=int, default=1000)
    parser.add_argument("--hard-failure-scan-threshold", type=int, default=50000)
    parser.add_argument("--hard-failure-parse-threshold", type=int, default=10000)
    parser.add_argument("--format", choices=("json", "quiet"), default="json")
    return parser.parse_args()


def normalize_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().lower())


def tokenize(text: str) -> list[str]:
    tokens: list[str] = []
    for chunk in re.findall(r"[A-Za-z0-9]+|[\u4e00-\u9fff]+", text):
        normalized = normalize_text(chunk)
        if not normalized:
            continue
        if re.fullmatch(r"[\u4e00-\u9fff]+", normalized):
            max_n = min(4, len(normalized))
            for size in range(1, max_n + 1):
                for idx in range(0, len(normalized) - size + 1):
                    tokens.append(normalized[idx : idx + size])
        else:
            tokens.append(normalized)
    seen: list[str] = []
    for token in tokens:
        if token and token not in seen:
            seen.append(token)
    return seen


def evidence_doc(evidence: dict[str, Any]) -> dict[str, Any]:
    node_ids = [item.get("node_id", "") for item in evidence.get("accepted_syllabus_nodes", []) if item.get("node_id")]
    text_parts = [
        evidence.get("title", ""),
        evidence.get("content", ""),
        evidence.get("subject", ""),
        evidence.get("book_id", ""),
        evidence.get("book_title", ""),
        evidence.get("chapter_title", ""),
        evidence.get("chapter_id", ""),
        *node_ids,
    ]
    text = "\n".join(str(part) for part in text_parts if str(part).strip())
    return {
        "doc_id": f"evidence:{evidence['evidence_id']}",
        "doc_type": "evidence",
        "entity_id": evidence["evidence_id"],
        "subject": evidence.get("subject", ""),
        "book_id": evidence.get("book_id", ""),
        "book_title": evidence.get("book_title", ""),
        "chapter_id": evidence.get("chapter_id", ""),
        "syllabus_node_ids": node_ids,
        "text": text,
    }


def claim_doc(claim: dict[str, Any]) -> dict[str, Any]:
    text_parts = [
        claim.get("text", ""),
        claim.get("canonical_text", ""),
        claim.get("subject", ""),
        claim.get("book_id", ""),
        claim.get("book_title", ""),
        claim.get("chapter_id", ""),
        claim.get("syllabus_node_id", ""),
        claim.get("claim_type", ""),
    ]
    text = "\n".join(str(part) for part in text_parts if str(part).strip())
    return {
        "doc_id": f"claim:{claim['claim_id']}",
        "doc_type": "claim",
        "entity_id": claim["claim_id"],
        "subject": claim.get("subject", ""),
        "book_id": claim.get("book_id", ""),
        "book_title": claim.get("book_title", ""),
        "chapter_id": claim.get("chapter_id", ""),
        "syllabus_node_ids": [claim.get("syllabus_node_id", "")] if claim.get("syllabus_node_id") else [],
        "text": text,
    }


def doc_fingerprint(doc: dict[str, Any]) -> str:
    return stable_fingerprint(
        {
            "doc_id": doc["doc_id"],
            "doc_type": doc["doc_type"],
            "subject": doc.get("subject", ""),
            "book_id": doc.get("book_id", ""),
            "book_title": doc.get("book_title", ""),
            "chapter_id": doc.get("chapter_id", ""),
            "syllabus_node_ids": doc.get("syllabus_node_ids", []),
            "text": doc.get("text", ""),
        }
    )


def file_signature(record: dict[str, Any]) -> str:
    return f"{record['size']}:{record['mtime_ns']}"


def build_doc_from_payload(doc_type: str, payload: dict[str, Any]) -> dict[str, Any] | None:
    if doc_type == "evidence":
        if payload.get("verification_status") == "stale" or payload.get("mapping_status") == "stale":
            return None
        return evidence_doc(payload)
    if doc_type == "claim":
        if payload.get("status") != "accepted":
            return None
        return claim_doc(payload)
    raise ValueError(f"unsupported doc_type: {doc_type}")


def entity_records(layout: dict[str, Path]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for record in scan_json_files(layout["evidence"]):
        records.append({"doc_type": "evidence", **record})
    for record in scan_json_files(layout["claims"]):
        records.append({"doc_type": "claim", **record})
    records.sort(key=lambda item: (item["doc_type"], item["name"]))
    return records


def build_inverted_index(documents: list[dict[str, Any]]) -> dict[str, list[str]]:
    terms: dict[str, list[str]] = {}
    for doc in documents:
        for token in doc.get("tokens", []):
            bucket = terms.setdefault(token, [])
            if doc["doc_id"] not in bucket:
                bucket.append(doc["doc_id"])
    for doc_ids in terms.values():
        doc_ids.sort()
    return dict(sorted(terms.items(), key=lambda item: item[0]))


def build_performance_boundary(
    *,
    scanned_files_count: int,
    parsed_files_count: int,
    warning_scan_threshold: int,
    warning_parse_threshold: int,
    hard_failure_scan_threshold: int,
    hard_failure_parse_threshold: int,
) -> dict[str, Any]:
    reasons: list[str] = []
    status = "ok"
    if scanned_files_count >= hard_failure_scan_threshold:
        status = "hard_failure"
        reasons.append("scanned-files-hard-threshold-exceeded")
    elif scanned_files_count >= warning_scan_threshold:
        status = "warning"
        reasons.append("scanned-files-threshold-exceeded")

    if parsed_files_count >= hard_failure_parse_threshold:
        status = "hard_failure"
        reasons.append("parsed-files-hard-threshold-exceeded")
    elif parsed_files_count >= warning_parse_threshold and status != "hard_failure":
        status = "warning"
        reasons.append("parsed-files-threshold-exceeded")

    return {
        "status": status,
        "reasons": reasons,
        "thresholds": {
            "warning_scan_threshold": warning_scan_threshold,
            "warning_parse_threshold": warning_parse_threshold,
            "hard_failure_scan_threshold": hard_failure_scan_threshold,
            "hard_failure_parse_threshold": hard_failure_parse_threshold,
        },
        "scanned_files_count": scanned_files_count,
        "parsed_files_count": parsed_files_count,
    }


def build_bucket_manifest(documents: list[dict[str, Any]]) -> dict[str, Any]:
    def count_values(values: list[str]) -> dict[str, int]:
        counts: dict[str, int] = {}
        for value in values:
            normalized = str(value or "").strip()
            if not normalized:
                continue
            counts[normalized] = counts.get(normalized, 0) + 1
        return dict(sorted(counts.items(), key=lambda item: item[0]))

    subject_counts = count_values([doc.get("subject", "") for doc in documents])
    book_counts = count_values([doc.get("book_id", "") or doc.get("book_title", "") for doc in documents])
    chapter_counts = count_values([doc.get("chapter_id", "") for doc in documents])
    doc_type_counts = count_values([doc.get("doc_type", "") for doc in documents])
    syllabus_counts = count_values(
        [node_id for doc in documents for node_id in list(doc.get("syllabus_node_ids", []) or [])]
    )
    return {
        "subject": {"bucket_count": len(subject_counts), "counts": subject_counts},
        "book": {"bucket_count": len(book_counts), "counts": book_counts},
        "chapter": {"bucket_count": len(chapter_counts), "counts": chapter_counts},
        "syllabus_node": {"bucket_count": len(syllabus_counts), "counts": syllabus_counts},
        "doc_type": {"bucket_count": len(doc_type_counts), "counts": doc_type_counts},
    }


def duplicate_metrics(documents: list[dict[str, Any]]) -> dict[str, Any]:
    fingerprint_counts: dict[str, int] = {}
    duplicate_doc_ids: list[str] = []
    for doc in documents:
        fingerprint = doc_fingerprint(doc)
        fingerprint_counts[fingerprint] = fingerprint_counts.get(fingerprint, 0) + 1
        if fingerprint_counts[fingerprint] > 1:
            duplicate_doc_ids.append(doc["doc_id"])
    duplicate_count = len(duplicate_doc_ids)
    doc_count = len(documents)
    duplicate_rate = round(duplicate_count / doc_count, 4) if doc_count else 0.0
    return {
        "duplicate_doc_count": duplicate_count,
        "duplicate_rate": duplicate_rate,
        "duplicate_doc_ids": sorted(duplicate_doc_ids),
    }


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    args = parse_args()
    layout = ensure_kb_layout()
    manifest_path = layout["indexes"] / "search_manifest.json"
    docs_path = layout["indexes"] / "search_documents.json"
    inverted_path = layout["indexes"] / "inverted_index.json"

    previous_manifest = load_json_or_default(manifest_path, {"documents": {}})
    previous_docs = load_json_or_default(docs_path, {"documents": []})
    previous_doc_map = {item.get("doc_id", ""): item for item in previous_docs.get("documents", []) if item.get("doc_id")}
    previous_manifest_docs = dict(previous_manifest.get("documents", {}))
    previous_manifest_by_file = {
        (str(item.get("doc_type", "")).strip(), str(item.get("file_name", "")).strip()): {
            "doc_id": doc_id,
            **dict(item),
        }
        for doc_id, item in previous_manifest_docs.items()
        if str(item.get("file_name", "")).strip()
    }

    records = entity_records(layout)
    scanned_files_count = len(records)
    parsed_files_count = 0
    changed_doc_ids: list[str] = []
    unchanged_count = 0
    final_documents: list[dict[str, Any]] = []
    manifest_documents: dict[str, Any] = {}

    for record in records:
        record_signature = file_signature(record)
        path = Path(record["path"])
        previous_entry = previous_manifest_by_file.get((record["doc_type"], record["name"]), {})
        previous_doc_id = str(previous_entry.get("doc_id", "")).strip()
        previous_signature = str(previous_entry.get("file_signature", "")).strip()

        if previous_doc_id and previous_signature == record_signature and previous_doc_id in previous_doc_map:
            unchanged_count += 1
            doc = dict(previous_doc_map[previous_doc_id])
            final_documents.append(doc)
            manifest_documents[previous_doc_id] = {
                "fingerprint": previous_entry.get("fingerprint", ""),
                "doc_type": record["doc_type"],
                "entity_id": previous_entry.get("entity_id", ""),
                "file_name": record["name"],
                "file_signature": record_signature,
            }
            continue

        try:
            payload = load_json(path)
        except Exception:
            continue
        doc = build_doc_from_payload(record["doc_type"], payload)
        parsed_files_count += 1
        if doc is None:
            continue
        doc["tokens"] = tokenize(doc.get("text", ""))
        fingerprint = doc_fingerprint(doc)
        doc_id = doc["doc_id"]
        previous_fingerprint = ""
        if previous_doc_id and previous_doc_id in previous_manifest_docs:
            previous_fingerprint = str(previous_manifest_docs.get(previous_doc_id, {}).get("fingerprint", ""))
        manifest_documents[doc_id] = {
            "fingerprint": fingerprint,
            "doc_type": doc["doc_type"],
            "entity_id": doc["entity_id"],
            "file_name": record["name"],
            "file_signature": record_signature,
        }
        if previous_fingerprint == fingerprint and doc_id in previous_doc_map:
            unchanged_count += 1
            final_documents.append(previous_doc_map[doc_id])
            continue
        changed_doc_ids.append(doc_id)
        final_documents.append(doc)

    final_documents.sort(key=lambda item: item["doc_id"])
    final_doc_ids = {item["doc_id"] for item in final_documents}
    previous_doc_ids = set(previous_doc_map)
    removed_doc_ids = sorted(previous_doc_ids - final_doc_ids)
    added_count = sum(1 for doc_id in changed_doc_ids if doc_id not in previous_doc_ids)
    updated_count = sum(1 for doc_id in changed_doc_ids if doc_id in previous_doc_ids)
    if not previous_manifest_docs:
        rebuild_mode = "full-rebuild"
    elif changed_doc_ids:
        rebuild_mode = "changed-only"
    else:
        rebuild_mode = "changed-only-noop"
    manifest_delta = {
        "added_count": added_count,
        "updated_count": updated_count,
        "removed_count": len(removed_doc_ids),
        "unchanged_count": unchanged_count,
        "removed_doc_ids": removed_doc_ids,
    }
    performance_boundary = build_performance_boundary(
        scanned_files_count=scanned_files_count,
        parsed_files_count=parsed_files_count,
        warning_scan_threshold=max(1, args.warning_scan_threshold),
        warning_parse_threshold=max(1, args.warning_parse_threshold),
        hard_failure_scan_threshold=max(1, args.hard_failure_scan_threshold),
        hard_failure_parse_threshold=max(1, args.hard_failure_parse_threshold),
    )
    inverted_terms = build_inverted_index(final_documents)
    bucket_manifest = build_bucket_manifest(final_documents)
    duplicates = duplicate_metrics(final_documents)
    docs_payload = {"doc_count": len(final_documents), "documents": final_documents}
    inverted_payload = {"term_count": len(inverted_terms), "terms": inverted_terms}
    manifest_payload = {
        "doc_count": len(final_documents),
        "changed_count": len(changed_doc_ids),
        "rebuild_mode": rebuild_mode,
        "manifest_delta": manifest_delta,
        "performance_boundary": performance_boundary,
        "bucket_manifest": bucket_manifest,
        "duplicate_metrics": duplicates,
        "index_size": {
            "document_count": len(final_documents),
            "term_count": len(inverted_terms),
        },
        "query_path_contract": {
            "query_candidate_set_ready": True,
            "normal_retrieval_path": "retrieval/search index -> candidate doc ids -> targeted json reads",
            "full_json_scan_policy": "diagnostic_or_migration_only",
        },
        "documents": manifest_documents,
    }
    save_json(docs_path, docs_payload)
    save_json(inverted_path, inverted_payload)
    save_json(manifest_path, manifest_payload)

    result = {
        "doc_count": len(final_documents),
        "changed_count": len(changed_doc_ids),
        "unchanged_count": unchanged_count,
        "changed_doc_ids": changed_doc_ids,
        "scanned_files_count": scanned_files_count,
        "parsed_files_count": parsed_files_count,
        "rebuild_mode": rebuild_mode,
        "manifest_delta": manifest_delta,
        "performance_boundary": performance_boundary,
        "bucket_manifest": bucket_manifest,
        "duplicate_metrics": duplicates,
        "index_size": manifest_payload["index_size"],
        "query_path_contract": manifest_payload["query_path_contract"],
        "paths": {
            "documents": str(docs_path),
            "inverted_index": str(inverted_path),
            "manifest": str(manifest_path),
        },
    }
    if args.format == "json":
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
