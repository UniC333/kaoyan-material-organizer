#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from build_search_index import tokenize
from common import ensure_kb_layout, load_json, resolve_subject


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--subject")
    parser.add_argument("--query", required=True)
    parser.add_argument("--topk", type=int, default=5)
    parser.add_argument("--format", choices=("json", "text", "quiet"), default="json")
    return parser.parse_args()


def load_index(layout: dict[str, Path]) -> tuple[list[dict[str, Any]], dict[str, list[str]]]:
    docs_path = layout["indexes"] / "search_documents.json"
    inverted_path = layout["indexes"] / "inverted_index.json"
    if not docs_path.exists() or not inverted_path.exists():
        raise SystemExit("[ERROR] missing search index; run build_search_index.py first")
    docs_payload = load_json(docs_path)
    inverted_payload = load_json(inverted_path)
    return list(docs_payload.get("documents", [])), dict(inverted_payload.get("terms", {}))


def candidate_doc_ids(query_tokens: list[str], inverted: dict[str, list[str]]) -> set[str]:
    candidates: set[str] = set()
    for token in query_tokens:
        candidates.update(inverted.get(token, []))
    return candidates


def doc_references(layout: dict[str, Path], doc: dict[str, Any]) -> list[str]:
    if doc.get("doc_type") == "evidence":
        return [doc.get("entity_id", "")] if doc.get("entity_id") else []
    if doc.get("doc_type") == "claim":
        claim_path = layout["claims"] / f"{doc.get('entity_id', '')}.json"
        if not claim_path.exists():
            return []
        claim = load_json(claim_path)
        return [str(item) for item in claim.get("evidence_ids", []) if str(item).strip()]
    return []


def evidence_payload(layout: dict[str, Path], evidence_id: str) -> dict[str, Any]:
    path = layout["evidence"] / f"{evidence_id}.json"
    return load_json(path) if path.exists() else {}


def doc_metadata(layout: dict[str, Path], doc: dict[str, Any], references: list[str]) -> dict[str, Any]:
    if doc.get("doc_type") == "evidence":
        evidence = evidence_payload(layout, doc.get("entity_id", ""))
        source_id = str(evidence.get("source_id", "")).strip()
        confidence = float(evidence.get("confidence", 0.0) or 0.0)
        source_grounded = bool(evidence.get("source_grounded"))
        verification_status = str(evidence.get("verification_status", "")).strip()
        mapping_status = str(evidence.get("mapping_status", "")).strip()
        credibility = 0.0
        credibility += min(max(confidence, 0.0), 1.0) * 0.25
        credibility += 0.35 if source_grounded else 0.0
        credibility += 0.25 if verification_status in {"source_grounded", "reviewed"} else 0.0
        credibility += 0.15 if mapping_status == "accepted" else 0.0
        return {
            "source_ids": [source_id] if source_id else [],
            "source_id": source_id,
            "confidence": confidence,
            "source_grounded": source_grounded,
            "verification_status": verification_status,
            "mapping_status": mapping_status,
            "credibility_score": round(min(credibility, 1.0), 4),
        }

    source_ids: list[str] = []
    grounded_count = 0
    confidence_sum = 0.0
    for evidence_id in references:
        evidence = evidence_payload(layout, evidence_id)
        source_id = str(evidence.get("source_id", "")).strip()
        if source_id and source_id not in source_ids:
            source_ids.append(source_id)
        if evidence.get("source_grounded"):
            grounded_count += 1
        confidence_sum += float(evidence.get("confidence", 0.0) or 0.0)
    claim_path = layout["claims"] / f"{doc.get('entity_id', '')}.json"
    claim = load_json(claim_path) if claim_path.exists() else {}
    support_count = int(claim.get("support_count", 0) or 0)
    average_confidence = confidence_sum / max(len(references), 1)
    credibility = 0.0
    credibility += min(max(average_confidence, 0.0), 1.0) * 0.2
    credibility += min(support_count, 3) * 0.12
    credibility += 0.35 if grounded_count else 0.0
    credibility += 0.15 if references else 0.0
    return {
        "source_ids": source_ids,
        "source_id": source_ids[0] if source_ids else "",
        "confidence": round(average_confidence, 4),
        "source_grounded": bool(grounded_count),
        "verification_status": "",
        "mapping_status": "",
        "credibility_score": round(min(credibility, 1.0), 4),
    }


def bm25_score(
    *,
    query_tokens: list[str],
    doc_tokens: list[str],
    doc_count: int,
    document_frequency: dict[str, int],
    avg_doc_len: float,
) -> float:
    if not doc_tokens:
        return 0.0
    tf = Counter(doc_tokens)
    doc_len = len(doc_tokens)
    k1 = 1.5
    b = 0.75
    score = 0.0
    for token in query_tokens:
        freq = tf.get(token, 0)
        if freq <= 0:
            continue
        df = max(document_frequency.get(token, 0), 1)
        idf = math.log(1 + (doc_count - df + 0.5) / (df + 0.5))
        denom = freq + k1 * (1 - b + b * doc_len / max(avg_doc_len, 1.0))
        score += idf * (freq * (k1 + 1)) / denom
    return score


def retrieve(layout: dict[str, Path], *, subject: str | None, query: str, topk: int) -> dict[str, Any]:
    docs, inverted = load_index(layout)
    query_tokens = tokenize(query)
    candidates = candidate_doc_ids(query_tokens, inverted)
    if subject:
        normalized_subject, _ = resolve_subject(subject)
    else:
        normalized_subject = None

    document_frequency = {term: len(doc_ids) for term, doc_ids in inverted.items()}
    avg_doc_len = sum(len(doc.get("tokens", [])) for doc in docs) / max(len(docs), 1)
    scored: list[dict[str, Any]] = []
    for doc in docs:
        if doc["doc_id"] not in candidates:
            continue
        if normalized_subject and doc.get("subject") != normalized_subject:
            continue
        references = doc_references(layout, doc)
        if not references:
            continue
        lexical_score = bm25_score(
            query_tokens=query_tokens,
            doc_tokens=list(doc.get("tokens", [])),
            doc_count=len(docs),
            document_frequency=document_frequency,
            avg_doc_len=avg_doc_len,
        )
        metadata = doc_metadata(layout, doc, references)
        credibility_score = float(metadata["credibility_score"])
        score = lexical_score * (0.7 + credibility_score)
        if doc.get("doc_type") == "claim":
            score *= 1.8
        if score <= 0:
            continue
        score = round(score, 4)
        scored.append(
            {
                "doc_id": doc.get("doc_id", ""),
                "doc_type": doc.get("doc_type", ""),
                "entity_id": doc.get("entity_id", ""),
                "subject": doc.get("subject", ""),
                "chapter_id": doc.get("chapter_id", ""),
                "source_id": metadata["source_id"],
                "source_ids": metadata["source_ids"],
                "syllabus_node_ids": list(doc.get("syllabus_node_ids", [])),
                "score": score,
                "references": references,
                "ranking_factors": {
                    "lexical_score": round(lexical_score, 4),
                    "credibility_score": credibility_score,
                    "source_diversity_bonus": 0.0,
                    "final_score": score,
                    "source_grounded": metadata["source_grounded"],
                    "confidence": metadata["confidence"],
                    "verification_status": metadata["verification_status"],
                    "mapping_status": metadata["mapping_status"],
                },
            }
        )
    limited = diversify_results(scored, max(topk, 1))
    return {
        "query": query,
        "subject": normalized_subject or "",
        "retrieval_mode": "bm25_ngram",
        "embedding_used": False,
        "topk": topk,
        "count": len(limited),
        "query_tokens": query_tokens,
        "results": limited,
    }


def diversify_results(scored: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    remaining = sorted(scored, key=lambda item: (-item["score"], item["doc_type"] != "claim", item["doc_id"]))
    selected: list[dict[str, Any]] = []
    used_sources: set[str] = set()
    while remaining and len(selected) < limit:
        ranked: list[tuple[float, dict[str, Any], float]] = []
        for item in remaining:
            source_ids = {source_id for source_id in item.get("source_ids", []) if source_id}
            diversity_bonus = 0.35 if source_ids and not source_ids.intersection(used_sources) and selected else 0.0
            ranked.append((float(item["score"]) + diversity_bonus, item, diversity_bonus))
        ranked.sort(key=lambda pair: (-pair[0], pair[1]["doc_type"] != "claim", pair[1]["doc_id"]))
        final_score, chosen, diversity_bonus = ranked[0]
        remaining.remove(chosen)
        chosen = dict(chosen)
        ranking_factors = dict(chosen.get("ranking_factors", {}))
        ranking_factors["source_diversity_bonus"] = round(diversity_bonus, 4)
        ranking_factors["final_score"] = round(final_score, 4)
        chosen["ranking_factors"] = ranking_factors
        chosen["score"] = ranking_factors["final_score"]
        selected.append(chosen)
        used_sources.update(source_id for source_id in chosen.get("source_ids", []) if source_id)
    return selected


def render_text(payload: dict[str, Any]) -> str:
    lines = [
        "# 本地检索结果",
        "",
        f"- query: {payload['query']}",
        f"- mode: {payload['retrieval_mode']}",
        f"- count: {payload['count']}",
        "",
    ]
    for item in payload["results"]:
        lines.append(f"- {item['doc_id']} | score={item['score']} | refs={', '.join(item['references'])}")
    return "\n".join(lines).rstrip() + "\n"


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    args = parse_args()
    layout = ensure_kb_layout()
    payload = retrieve(layout, subject=args.subject, query=args.query, topk=args.topk)
    if args.format == "json":
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    elif args.format == "text":
        print(render_text(payload), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
