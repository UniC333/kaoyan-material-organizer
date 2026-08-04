#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from common import default_vault_root_arg, ensure_kb_layout, load_json, resolve_subject, validate_entity_contract
from query_local_knowledge import preferred_page_ref, query_knowledge, should_prefer_evidence_chapter


ANSWER_CONTRACT_VERSION = "m6.answer.v1"
STRUCTURED_ANSWER_MODES = {"canonical_claim", "accepted_evidence"}
CONTENT_SOURCE_LABELS = {
    "textbook_structured_evidence": "教材结构化证据",
    "page_asset_only": "仅原页定位",
    "supplementary_derivation": "补充推导",
    "learner_feedback": "学习者反馈",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--vault-root", default=default_vault_root_arg())
    parser.add_argument("--subject", required=True)
    parser.add_argument("--chapter")
    parser.add_argument("--book-title")
    parser.add_argument("--question", required=True)
    parser.add_argument("--topk", type=int, default=3)
    parser.add_argument("--printed-page", type=int)
    parser.add_argument("--format", choices=("text", "json"), default="text")
    return parser.parse_args()


def dedupe(items: list[str]) -> list[str]:
    seen: list[str] = []
    for item in items:
        text = str(item or "").strip()
        if text and text not in seen:
            seen.append(text)
    return seen


def page_anchor_snippets(result: dict) -> list[str]:
    anchor = result.get("page_anchor", {}) or {}
    snippets = anchor.get("snippets", []) or []
    return dedupe([str(item).strip() for item in snippets])


def direct_conclusion(result: dict) -> str:
    anchor_snippets = page_anchor_snippets(result)
    if anchor_snippets:
        return anchor_snippets[0]
    anchor = result.get("page_anchor", {}) or {}
    if anchor.get("match_status") == "exact_asset":
        return f"已精确定位教材原页：{anchor.get('source_image_path', '')}；该页尚无结构化 OCR，教材正文未确认。"
    if anchor.get("match_status") in {"ambiguous", "unmapped", "not_found"}:
        return result.get("fallback_note") or "当前无法唯一定位教材原页。"
    bundle = result.get("compare_bundle")
    if bundle:
        return bundle["summary"]
    if result["claim_hits"]:
        texts = dedupe([claim["text"] for claim in result["claim_hits"]])
        return "；".join(texts[:3])
    if result["evidence_hits"]:
        return "；".join(item.get("title", "") for item in result["evidence_hits"][:3])
    if result["fallback_hits"]:
        return result["fallback_hits"][0].get("chapter_overview", "") or "当前只能回退到章节级概览。"
    return "当前本地知识库里还没有足够稳定的命中。"


def intuitive_explanation(result: dict) -> str:
    if result.get("answer_mode") == "page_asset":
        return "页码和原图已经确认，但正文尚未进入正式证据层；可以查看原图讲解，不能把未核对的语义检索结果当作书上原文。"
    if result["answer_mode"] == "chapter_fallback":
        return "当前未命中正式主张，这次回答只基于章节层回退，不应把它当作正式知识结论。"
    bundle = result.get("compare_bundle")
    if bundle:
        if bundle.get("mode") == "single_node":
            return f"这次问题直接命中 {bundle['node']['title']}，说明教材里已经把这组概念放在同一考点下处理。"
        if bundle.get("mode") == "single_node_evidence":
            return f"这次问题直接命中 {bundle['node']['title']}，但当前正式 comparison claim 还不稳定，所以解释先基于该节点证据组织。"
        return f"这次问题同时命中了 {bundle['left_node']['title']} 和 {bundle['right_node']['title']}，回答会先分清两边各自在说什么。"
    if result["syllabus_route"]:
        return f"这次问题优先路由到了 {result['syllabus_route'][0]['title']}，所以回答以该考纲节点下的正式主张和证据为主。"
    return "这次没有稳定命中考纲节点，只能退回到证据层或章节概览。"


def strict_explanation(result: dict) -> list[str]:
    bundle = result.get("compare_bundle")
    if bundle:
        if bundle.get("mode") == "single_node":
            return [f"{bundle['node']['title']}: {bundle['primary_claim']['text']}"]
        if bundle.get("mode") == "single_node_evidence":
            evidence = bundle["primary_evidence"]
            first_lines = [line.strip() for line in str(evidence.get("content", "")).splitlines() if line.strip()]
            return [f"{bundle['node']['title']}: {first_lines[1] if len(first_lines) > 1 else evidence.get('title', '')}"]
        return [
            f"{bundle['left_node']['title']}: {bundle['left_claim']['text']}",
            f"{bundle['right_node']['title']}: {bundle['right_claim']['text']}",
        ]
    lines: list[str] = []
    anchor_snippets = page_anchor_snippets(result)
    for snippet in anchor_snippets:
        if snippet not in lines:
            lines.append(snippet)
    for claim in result["claim_hits"][:4]:
        line = f"{claim['claim_type']}: {claim['text']}"
        if line not in lines:
            lines.append(line)
    if not lines:
        for evidence in result["evidence_hits"][:3]:
            first_line = evidence.get("content", "").splitlines()[0] if evidence.get("content") else evidence.get("title", "")
            lines.append(f"{evidence.get('evidence_type', '')}: {first_line}")
    return lines or ["当前没有稳定主张，只能继续补证据。"]


def example_lines(result: dict) -> list[str]:
    examples = [claim["text"] for claim in result["claim_hits"] if claim.get("claim_type") == "example_type"]
    examples = dedupe(examples)
    if examples:
        return examples[:3]
    derived = [item.get("title", "") for item in result["evidence_hits"][:3] if item.get("evidence_type") in {"example", "exercise"}]
    return dedupe(derived)[:3]


def personalized_reminder(result: dict) -> str:
    teaching_context = dict(result.get("teaching_context") or {})
    guidance = personalized_teaching_guidance(result)
    self_check = str(teaching_context.get("self_check", "")).strip()
    if self_check:
        guidance.append(f"讲完后自测：{self_check}")
    if guidance:
        return "；".join(guidance)
    snapshot = result.get("learner_snapshot", {})
    question_count = int(snapshot.get("question_count", 0))
    if question_count >= 5:
        return "你在这个学科已经有连续提问记录，优先补高频卡点，不要再回到全章泛看。"
    if question_count >= 1:
        return "这个学科已经开始形成个人提问轨迹，建议继续围绕当前命中的考纲节点追问。"
    return "当前还没有形成个人历史，先把这个节点的定义、规则和易混点问扎实。"


def personalized_teaching_guidance(result: dict) -> list[str]:
    context = dict(result.get("teaching_context") or {})
    guidance: list[str] = []
    anchor = str(context.get("accepted_anchor", "")).strip()
    if anchor:
        guidance.append(f"先从已确认的核心抓手开始：{anchor}")
    routes = [str(item).strip() for item in context.get("preferred_routes", []) if str(item).strip()]
    if routes:
        guidance.append(f"优先按这条路线展开：{' -> '.join(routes)}")
    handoff = dict(context.get("learning_handoff") or {})
    original = dict(handoff.get("original_problem") or {})
    if original.get("title"):
        guidance.append(f"续接上次原题：{original['title']}（{original.get('mastery_status') or '待验证'}）")
    if handoff.get("handoff_summary"):
        guidance.append(f"真实停点：{handoff['handoff_summary']}")
    for item in context.get("avoid_as_first_explanation", []):
        if not isinstance(item, dict):
            continue
        method = str(item.get("method", "")).strip()
        reason = str(item.get("reason", "")).strip()
        if method:
            guidance.append(f"本次不先采用“{method}”；{reason or '它并非错误方法，只是当前不作为首选。'}")
    return guidance


def source_differences(result: dict) -> list[str]:
    titles = dedupe([str(evidence.get("title", "")).strip() for evidence in result["evidence_hits"][:5]])
    if result.get("fallback_note"):
        return [result["fallback_note"]]
    return titles[:3] or ["当前只有单一证据视角。"]


def next_steps(result: dict) -> list[str]:
    if result["answer_mode"] == "chapter_fallback" and result.get("refinement_candidates"):
        return [f"先处理 refinement：{item.get('candidate_type', '')}" for item in result["refinement_candidates"][:3]]
    if result["intent"] == "plan" and result["syllabus_route"]:
        return [f"继续追问 {item['title']} 的定义、条件和典型题型。" for item in result["syllabus_route"][:3]]
    bundle = result.get("compare_bundle")
    if bundle:
        if bundle.get("mode") == "single_node":
            return [f"继续追问 {bundle['node']['title']} 的判定口径、反例和易错点。"]
        if bundle.get("mode") == "single_node_evidence":
            return [f"继续把 {bundle['node']['title']} 补成正式 comparison/confusion claim。"]
        return [
            f"继续追问 {bundle['left_node']['title']} 的定义边界。",
            f"继续追问 {bundle['right_node']['title']} 的判断口径。",
        ]
    if result["claim_hits"]:
        return [f"把“{claim['text'][:24]}”继续追问成条件、反例或题型。" for claim in result["claim_hits"][:3]]
    if result["references"]:
        return [f"回到 {ref['title']}，按页段 {ref['page_span']} 继续补充。" for ref in result["references"][:3]]
    return ["先补当前章节的 chunk 提取，再重新同步知识库。"]


def _span_text(start: Any, end: Any) -> str:
    left = str(start or "").strip()
    right = str(end or "").strip()
    if left and right:
        return f"{left}-{right}"
    return left or right


def _ranking_by_evidence(result: dict) -> dict[str, dict[str, Any]]:
    ranking: dict[str, dict[str, Any]] = {}
    for hit in result.get("retrieval_hits", []):
        factors = dict(hit.get("ranking_factors", {}))
        factors.setdefault("final_score", hit.get("score", 0))
        factors["retrieval_doc_id"] = hit.get("doc_id", "")
        factors["retrieval_doc_type"] = hit.get("doc_type", "")
        for evidence_id in hit.get("references", []):
            if evidence_id and evidence_id not in ranking:
                ranking[evidence_id] = factors
    return ranking


def _evidence_from_id(layout: dict[str, Path], evidence_id: str) -> dict[str, Any]:
    path = layout["evidence"] / f"{evidence_id}.json"
    return load_json(path) if evidence_id and path.exists() else {}


def build_citations(result: dict, *, limit: int = 3) -> list[dict[str, Any]]:
    layout = ensure_kb_layout()
    ranking = _ranking_by_evidence(result)
    ordered_ids: list[str] = []

    for ref in result.get("references", []):
        evidence_id = str(ref.get("evidence_id", "")).strip()
        if evidence_id and evidence_id not in ordered_ids:
            ordered_ids.append(evidence_id)
    for hit in result.get("retrieval_hits", []):
        for evidence_id in hit.get("references", []):
            evidence_id = str(evidence_id).strip()
            if evidence_id and evidence_id not in ordered_ids:
                ordered_ids.append(evidence_id)
    for evidence in result.get("evidence_hits", []):
        evidence_id = str(evidence.get("evidence_id", "")).strip()
        if evidence_id and evidence_id not in ordered_ids:
            ordered_ids.append(evidence_id)

    citations: list[dict[str, Any]] = []
    fallback_refs = {ref.get("evidence_id", ""): ref for ref in result.get("references", [])}
    for evidence_id in ordered_ids[:limit]:
        evidence = _evidence_from_id(layout, evidence_id)
        ref = fallback_refs.get(evidence_id, {})
        locator = evidence.get("locator", {}) if evidence else {}
        page_refs = list(evidence.get("page_classification_refs", []) or ref.get("page_classification_refs", []) or [])
        primary_ref = preferred_page_ref(evidence, page_refs)
        use_evidence_chapter = should_prefer_evidence_chapter(evidence, primary_ref)
        citation = {
            "evidence_id": evidence_id,
            "title": evidence.get("title") or ref.get("title", ""),
            "source_id": evidence.get("source_id", ""),
            "chapter_id": evidence.get("chapter_id", ""),
            "chunk_id": evidence.get("chunk_id") or ref.get("chunk_id", ""),
            "chunk_kb_id": evidence.get("chunk_kb_id", ""),
            "page_span": _span_text(locator.get("page_start"), locator.get("page_end")) or ref.get("page_span", ""),
            "image_span": _span_text(locator.get("image_start"), locator.get("image_end")) or ref.get("image_span", ""),
            "page_classification_refs": page_refs,
            "book_id": primary_ref.get("book_id", ref.get("book_id", "")),
            "book_title": evidence.get("book_title", "") or primary_ref.get("book_title", ref.get("book_title", "")),
            "book_chapter_title": evidence.get("chapter_title", "") if use_evidence_chapter else (primary_ref.get("chapter_title", ref.get("book_chapter_title", "")) or evidence.get("chapter_title", "")),
            "section_title": primary_ref.get("section_title", ref.get("section_title", "")),
            "chapter_view_path": primary_ref.get("chapter_view_path", ref.get("chapter_view_path", "")),
            "section_view_path": primary_ref.get("section_view_path", ref.get("section_view_path", "")),
            "source_grounded": bool(evidence.get("source_grounded")),
            "verification_status": evidence.get("verification_status", ""),
            "confidence": evidence.get("confidence", 0),
            "ranking_factors": ranking.get(evidence_id, {}),
        }
        citations.append(citation)
    return citations


def build_evidence_assessment(result: dict, citations: list[dict[str, Any]]) -> dict[str, str]:
    """Describe what the local structured layer can prove without reading an image."""
    answer_mode = str(result.get("answer_mode", ""))
    source_verify = result.get("intent") == "source_verify"
    anchor = dict(result.get("page_anchor") or {})
    page_status = str(anchor.get("match_status", ""))

    if answer_mode in STRUCTURED_ANSWER_MODES and citations:
        return {
            "level": "structured_evidence",
            "can_confirm": "本地已整理的主张或证据支持本次结论。",
            "cannot_confirm": "当前结论仅覆盖已列出的引用范围。",
            "next_action": "可沿引用继续核对条件、例题或原始上下文。",
        }
    if page_status == "exact_asset":
        return {
            "level": "page_asset_only",
            "can_confirm": "已确认教材原页的位置，但该页尚无结构化 OCR 或正式证据，教材正文未确认。",
            "cannot_confirm": "不能仅据页码映射确认书上是否出现了某个公式、推导或原文表述。",
            "next_action": "如仍需核对，请明确要求人工阅图；也可先补该页 OCR 后重新查询。",
        }
    if page_status in {"ambiguous", "unmapped", "not_found"}:
        labels = {
            "ambiguous": "存在多个可能教材页",
            "unmapped": "该教材页尚未建立正式映射",
            "not_found": "正式页码索引未找到该页",
        }
        return {
            "level": f"page_{page_status}",
            "can_confirm": labels[page_status] + "。",
            "cannot_confirm": "当前无法确认书中具体内容。",
            "next_action": "请补充教材名或页码；映射完成后再进行结构化核验。",
        }
    if source_verify:
        return {
            "level": "structured_unconfirmed",
            "can_confirm": "当前本地结构化资料没有给出可核验的教材结论。",
            "cannot_confirm": "不能把章节摘要、检索不到的结果或补充讲解说成书上原文。",
            "next_action": "可补充书名、章节或页码后重查；如已定位原页，可再明确要求人工阅图。",
        }
    if answer_mode == "chapter_fallback":
        return {
            "level": "chapter_summary",
            "can_confirm": "当前只命中章节级概览，可用于确定大致主题。",
            "cannot_confirm": "章节概览不能证明具体公式、原文或例题就在书中出现。",
            "next_action": "先补充章节提取或 OCR，再回到本地检索。",
        }
    return {
        "level": "unconfirmed",
        "can_confirm": "当前没有足够稳定的本地证据。",
        "cannot_confirm": "不能据此给出教材事实结论。",
        "next_action": "补充检索条件或资料证据后再回答。",
    }


def content_provenance(result: dict, assessment: dict[str, str]) -> list[dict[str, Any]]:
    """Keep textbook assertions and supplemental teaching material separately labelled."""
    level = str(assessment.get("level", ""))
    anchor = dict(result.get("page_anchor") or {})
    if level == "structured_evidence":
        source_type = "textbook_structured_evidence"
    elif level == "page_asset_only":
        source_type = "page_asset_only"
    else:
        source_type = "learner_feedback" if result.get("intent") == "learner_feedback" else "supplementary_derivation"
    items: list[dict[str, Any]] = [
        {
            "content_id": "primary-answer",
            "content_type": "original_problem" if anchor.get("requested_page") is not None else "answer",
            "source_type": source_type,
            "source_label": CONTENT_SOURCE_LABELS[source_type],
            "textbook_assertion_allowed": source_type == "textbook_structured_evidence",
            "printed_page": anchor.get("requested_page"),
            "exercise_label": anchor.get("exercise_label", ""),
        }
    ]
    for index, raw in enumerate(result.get("supplementary_content", []) or [], start=1):
        if not isinstance(raw, dict):
            continue
        title = str(raw.get("title", "")).strip()
        explanation = str(raw.get("explanation", "")).strip()
        if not title and not explanation:
            continue
        items.append(
            {
                "content_id": str(raw.get("content_id", "")).strip() or f"supplement-{index}",
                "content_type": "supplementary_derivation",
                "source_type": "supplementary_derivation",
                "source_label": CONTENT_SOURCE_LABELS["supplementary_derivation"],
                "textbook_assertion_allowed": False,
                "title": title,
                "explanation": explanation,
                "related_to": str(raw.get("related_to", "primary-answer")).strip() or "primary-answer",
                # A generic derivation must never inherit the original problem's identity.
                "printed_page": None,
                "exercise_label": "",
            }
        )
    return items


def build_answer_contract(result: dict) -> dict[str, Any]:
    citations = build_citations(result)
    evidence_assessment = build_evidence_assessment(result, citations)
    provenance = content_provenance(result, evidence_assessment)
    direct = direct_conclusion(result)
    if result.get("intent") == "source_verify" and evidence_assessment["level"] != "structured_evidence":
        direct = evidence_assessment["can_confirm"]
    sections = {
        "syllabus_position": [
            {"node_id": item.get("node_id", ""), "title": item.get("title", ""), "score": item.get("score", 0)}
            for item in result.get("syllabus_route", [])
        ],
        "direct_conclusion": direct,
        "intuitive_explanation": intuitive_explanation(result),
        "strict_explanation": strict_explanation(result),
        "typical_examples": example_lines(result),
        "personalized_reminder": personalized_reminder(result),
        "source_differences": source_differences(result),
        "next_steps": next_steps(result),
        "content_boundaries": provenance,
    }
    citation_required = result.get("answer_mode") in {"canonical_claim", "accepted_evidence"}
    coverage_ok = (not citation_required) or bool(citations)
    contract = {
        "answer_contract_version": ANSWER_CONTRACT_VERSION,
        "subject": result.get("subject", ""),
        "chapter": result.get("chapter", ""),
        "question": result.get("query", ""),
        "query": result.get("query", ""),
        "intent": result.get("intent", ""),
        "answer_mode": result.get("answer_mode", ""),
        "fallback_note": result.get("fallback_note", ""),
        "citation_coverage_ok": coverage_ok,
        "evidence_assessment": evidence_assessment,
        "content_provenance": provenance,
        "sections": sections,
        "citations": citations,
        "teaching_context": dict(result.get("teaching_context") or {}),
        # Compatibility fields for older callers that consumed raw query output.
        "syllabus_route": result.get("syllabus_route", []),
        "references": result.get("references", []),
        "retrieval_hits": result.get("retrieval_hits", []),
        "claim_hits": result.get("claim_hits", []),
        "evidence_hits": result.get("evidence_hits", []),
        "fallback_hits": result.get("fallback_hits", []),
        "page_anchor": result.get("page_anchor", {}),
        "query_result": result,
    }
    validate_entity_contract("query_artifact", contract)
    return contract


def render_text(contract: dict) -> str:
    sections = contract["sections"]
    lines = [
        "# 本地问答草稿",
        "",
        f"- 问题：{contract['question']}",
        f"- 学科：{contract['subject']}",
        f"- 依据级别：{contract['evidence_assessment']['level']}",
        f"- 契约版本：{contract['answer_contract_version']}",
        "",
        "## 考纲定位",
        "",
    ]
    if sections["syllabus_position"]:
        for item in sections["syllabus_position"]:
            lines.append(f"- {item['title']} (`{item['node_id']}`)")
    else:
        lines.append("- 当前没有稳定命中正式考纲节点。")
    if contract.get("fallback_note"):
        lines.extend(["", "## 回退说明", "", f"- {contract['fallback_note']}"])
    assessment = contract["evidence_assessment"]
    lines.extend(
        [
            "",
            "## 证据边界",
            "",
            f"- 能确认：{assessment['can_confirm']}",
            f"- 不能确认：{assessment['cannot_confirm']}",
            f"- 下一步：{assessment['next_action']}",
        ]
    )
    lines.extend(["", "## 内容来源", ""])
    for item in contract.get("content_provenance", []):
        identity: list[str] = []
        if item.get("printed_page") is not None:
            identity.append(f"印刷页 {item['printed_page']}")
        if item.get("exercise_label"):
            identity.append(f"题号 {item['exercise_label']}")
        relation = f"；关联 {item['related_to']}" if item.get("related_to") else ""
        suffix = f"（{'，'.join(identity)}）" if identity else ""
        lines.append(f"- {item['source_label']}{suffix}{relation}")
    lines.extend(["", "## 直接结论", "", str(sections["direct_conclusion"])])
    lines.extend(["", "## 直观解释", "", str(sections["intuitive_explanation"])])
    lines.extend(["", "## 严格说明", ""])
    for line in sections["strict_explanation"]:
        lines.append(f"- {line}")
    lines.extend(["", "## 典型题型", ""])
    example_items = sections["typical_examples"]
    if example_items:
        for line in example_items:
            lines.append(f"- {line}")
    else:
        lines.append("- 当前还没有稳定题型卡。")
    lines.extend(["", "## 个性化提醒", "", str(sections["personalized_reminder"])])
    lines.extend(["", "## 来源差异", ""])
    for line in sections["source_differences"]:
        lines.append(f"- {line}")
    lines.extend(["", "## 下一步建议", ""])
    for line in sections["next_steps"]:
        lines.append(f"- {line}")
    lines.extend(["", "## 来源引用", ""])
    if contract["citations"]:
        for ref in contract["citations"]:
            section_text = f" | 小节 {ref['section_title']}" if ref.get("section_title") else ""
            view_text = f" | 视图 {ref['section_view_path']}" if ref.get("section_view_path") else ""
            lines.append(
                f"- {ref['evidence_id']} | {ref['title']} | 页段 {ref['page_span']} | 图片 {ref['image_span']} | chunk {ref['chunk_id']}{section_text}{view_text}"
            )
    else:
        lines.append("- 当前没有稳定证据引用。")
    return "\n".join(lines).rstrip() + "\n"


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    args = parse_args()
    subject, _ = resolve_subject(args.subject)
    result = query_knowledge(Path(args.vault_root), subject, args.chapter, args.question, args.topk, args.printed_page, args.book_title)
    contract = build_answer_contract(result)
    if args.format == "json":
        print(json.dumps(contract, ensure_ascii=False, indent=2))
    else:
        print(render_text(contract), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
