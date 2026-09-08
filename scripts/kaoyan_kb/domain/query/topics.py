from __future__ import annotations

from .request import normalize_text
from common import (
    ensure_kb_layout, is_publishable_source_evidence, load_json,
)
from typing import Any
import re
import unicodedata


GENERIC_QUERY_NOISE = (
    "请问",
    "帮我",
    "讲一下",
    "讲解",
    "解释一下",
    "解释",
    "说明一下",
    "说明",
    "告诉我",
    "什么是",
    "是什么",
    "有什么区别",
    "有什么不同",
    "怎么区别",
    "如何区别",
    "怎么区分",
    "如何区分",
    "有什么联系",
    "有什么关系",
    "区别",
    "联系",
    "关系",
    "比较",
    "对比",
    "怎么做",
    "如何做",
    "怎么理解",
    "为什么",
    "的定义",
    "定义",
    "概念",
    "吗",
    "呢",
)

GENERIC_ANSWER_MODES = {"canonical_claim", "accepted_evidence"}

def tokenize(query: str) -> list[str]:
    tokens: list[str] = []
    for chunk in re.findall(r"[A-Za-z0-9]+|[\u4e00-\u9fff]+", query):
        value = normalize_text(chunk)
        if not value:
            continue
        if re.fullmatch(r"[\u4e00-\u9fff]+", value):
            max_n = min(4, len(value))
            for size in range(1, max_n + 1):
                for idx in range(0, len(value) - size + 1):
                    tokens.append(value[idx : idx + size])
        else:
            tokens.append(value)
    seen: list[str] = []
    for token in tokens:
        if token and token not in seen:
            seen.append(token)
    return seen


def compare_parts(query: str) -> list[str]:
    cleaned = re.sub(r"(怎么区分|如何区分|怎么区别|如何区别|区别|区分|比较|对比|有什么不同|有什么区别)", " ", query)
    parts = re.split(r"[和与跟及、/]|vs|VS", cleaned)
    normalized = [
        re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", normalize_text(part)).rstrip("的")
        for part in parts
        if normalize_text(part)
    ]
    compact = [part for part in normalized if len(part) <= 12]
    return compact[:3]


def generic_topic_terms(query: str, intent: str = "define") -> list[str]:
    """Extract topic terms while dropping question and routing boilerplate."""
    text = unicodedata.normalize("NFKC", str(query or "")).lower()
    for phrase in sorted(GENERIC_QUERY_NOISE, key=len, reverse=True):
        text = text.replace(phrase, " ")
    text = text.replace("的", " ")
    text = re.sub(r"[？?。！!，,；;：:（）()\[\]{}]", " ", text)
    parts = re.split(r"\s*(?:和|与|跟|及|、|/|vs)\s*", text, flags=re.IGNORECASE)
    terms: list[str] = []
    for part in parts:
        term = re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", part)
        if not term or term in {"我", "想", "了解", "一下", "请", "帮"}:
            continue
        if len(term) == 1 and not re.search(r"[\u4e00-\u9fff]", term):
            continue
        if term not in terms:
            terms.append(term)
    if intent == "compare":
        compare_terms: list[str] = []
        for raw in compare_parts(query):
            term = re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", str(raw).lower())
            if term and term not in {"什么", "不同"} and term not in compare_terms:
                compare_terms.append(term)
        return compare_terms
    return terms[:5]


def topic_coverage(candidate_text: Any, topic_terms: list[str] | None) -> dict[str, Any]:
    """Return deterministic topic coverage for one candidate."""
    terms = [str(term).strip() for term in (topic_terms or []) if str(term).strip()]
    normalized = re.sub(r"\s+", "", unicodedata.normalize("NFKC", str(candidate_text or "")).lower())
    matched = [term for term in terms if term in normalized]
    return {
        "topic_terms": terms,
        "matched_terms": list(dict.fromkeys(matched)),
        "ok": bool(terms) and len(matched) == len(terms),
    }


def is_definition_request(query: str, intent: str = "define") -> bool:
    """Identify requests that require a definition/theorem statement, not a usage mention."""
    text = unicodedata.normalize("NFKC", str(query or "")).lower()
    if intent != "define":
        return False
    return any(token in text for token in ("什么是", "是什么", "的定义", "定义", "概念", "含义"))


def evidence_has_formal_topic_statement(evidence: dict[str, Any], topic_terms: list[str] | None) -> bool:
    """Require a nearby formal statement before exposing a definition answer."""
    terms = [
        re.sub(r"\s+", "", unicodedata.normalize("NFKC", str(term or "")).lower())
        for term in (topic_terms or [])
        if str(term or "").strip()
    ]
    terms = [term for term in terms if term]
    if not terms:
        return False
    raw_lines = [re.sub(r"\s+", " ", str(line)).strip() for line in str(evidence.get("content") or "").splitlines()]
    formal_labels = ("定义", "定理", "概念", "法则")
    rejected_prefixes = (
        "例",
        "例如",
        "解",
        "证明",
        "证法",
        "由",
        "利用",
        "根据",
        "应用",
        "本题",
        "问题",
        "练习",
        "题型",
        "方法",
    )

    def compact_line(value: str) -> str:
        return re.sub(r"\s+", "", value.lstrip("#>*- 【[（(").lower())

    def rejected_line(compact: str) -> bool:
        return (
            any(compact.startswith(prefix) for prefix in rejected_prefixes)
            or "应用" in compact
            or "用法" in compact
            or any(marker in compact for marker in ("（ ）", "()", "选项", "真题", "选择题", "试编写", "下列"))
        )

    def has_formal_statement(compact: str, *, require_topic: bool = False) -> bool:
        if not compact or (require_topic and not any(term in compact for term in terms)):
            return False
        if rejected_line(compact):
            return False

        # A direct predicate such as “栈是……” or “泰勒公式为……” is a
        # definition-shaped sentence.  Do not treat the word “公式” in a
        # topic name as a formal label by itself.
        direct_definition = any(
            re.search(
                re.escape(term) + r"(?:[（(][^）)]{0,12}[）)]|的(?:定义|概念|特点|特性|核心规则))?(?:是|为|指|叫做|称为|定义为)",
                compact,
            )
            for term in terms
        )
        condition_statement = (
            ("设" in compact and "则" in compact)
            or ("若" in compact and "则" in compact)
            or ("如果" in compact and "则" in compact)
            or ("对任意" in compact and ("存在" in compact or "则" in compact))
            or ("对于" in compact and ("存在" in compact or "则" in compact or "满足" in compact))
        )
        labeled_definition = any(label in compact for label in formal_labels) and (
            direct_definition or condition_statement or "定义" in compact
        )
        return direct_definition or condition_statement or labeled_definition

    def has_formula_statement(compact: str) -> bool:
        return not rejected_line(compact) and any(
            marker in compact for marker in ("=", "\\in", "\\le", "\\ge", "\\forall", "\\exists")
        )

    for index, raw_line in enumerate(raw_lines):
        compact = compact_line(raw_line)
        if not any(term in compact for term in terms):
            continue
        if rejected_line(compact):
            continue
        if has_formal_statement(compact, require_topic=True):
            return True

        # A clean section/definition heading may carry the topic while the
        # actual formal sentence starts on the next line.  Restrict this
        # bridge to headings so an example sentence cannot borrow a nearby
        # “设……则……” from the same page.
        heading_like = raw_line.startswith("#") or len(compact) <= 16
        if heading_like and any(
            has_formal_statement(compact_line(candidate)) or has_formula_statement(compact_line(candidate))
            for candidate in raw_lines[index + 1 : index + 6]
        ):
            return True
    return False


def _claim_topic_text(claim: dict[str, Any]) -> str:
    variants = claim.get("variants") or []
    if isinstance(variants, str):
        variants = [variants]
    return "\n".join(
        str(value or "")
        for value in [claim.get("text"), claim.get("canonical_text"), *variants]
    )


def score_text(text: str, tokens: list[str], full_query: str) -> float:
    hay = normalize_text(text)
    if not hay:
        return 0.0
    score = 0.0
    if full_query and full_query in hay:
        score += 1.5
    for token in tokens:
        if token in hay:
            score += 0.25
    return score


def title_match_score(title: str, aliases: list[str], keywords: list[str], tokens: list[str], parts: list[str], intent: str) -> float:
    haystacks = [normalize_text(title), *[normalize_text(alias) for alias in aliases], *[normalize_text(keyword) for keyword in keywords]]
    score = 0.0
    for hay in haystacks:
        if not hay:
            continue
        score += score_text(hay, tokens, normalize_text(title)) * 0.4
    if intent == "compare" and parts:
        matched_parts = 0
        for part in parts:
            if any(part in hay for hay in haystacks):
                matched_parts += 1
                score += 1.2
        if matched_parts >= 2:
            score += 3.0
    if normalize_text(title) in parts:
        score += 2.0
    return score


def _normalized_book_title(value: Any) -> str:
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", unicodedata.normalize("NFKC", str(value or "")).lower())


def _book_title_matches(actual: Any, requested: Any) -> bool:
    left = _normalized_book_title(actual)
    right = _normalized_book_title(requested)
    return bool(left and right and (left == right or left in right or right in left))


def evidence_matches_book(evidence: dict[str, Any], book_title: str | None) -> bool:
    """Require an evidence record to identify the requested textbook."""
    requested = str(book_title or "").strip()
    if not requested:
        return False
    titles = [evidence.get("book_title"), evidence.get("source_name")]
    titles.extend(
        ref.get("book_title")
        for ref in evidence.get("page_classification_refs", []) or []
        if isinstance(ref, dict)
    )
    return any(_book_title_matches(title, requested) for title in titles if str(title or "").strip())


def _claim_support_evidence(claim: dict[str, Any], book_title: str | None) -> list[dict[str, Any]]:
    """Load every claim support and require publishable, same-book evidence."""
    evidence_ids = [str(item).strip() for item in claim.get("evidence_ids", []) or [] if str(item).strip()]
    if not evidence_ids:
        return []
    layout = ensure_kb_layout()
    loaded: list[dict[str, Any]] = []
    for evidence_id in evidence_ids:
        path = layout["evidence"] / f"{evidence_id}.json"
        if not path.is_file():
            return []
        evidence = load_json(path)
        if not is_publishable_source_evidence(evidence):
            return []
        if book_title is not None and not evidence_matches_book(evidence, book_title):
            return []
        loaded.append(evidence)
    return loaded


def _retrieval_hit_matches_book(hit: dict[str, Any], book_title: str | None) -> bool:
    """Validate indexed candidates against the requested book before exposing them."""
    if book_title is None:
        return True
    requested = str(book_title or "").strip()
    if not requested:
        return False
    layout = ensure_kb_layout()
    entity_id = str(hit.get("entity_id") or "").strip()
    if hit.get("doc_type") == "evidence" and entity_id:
        path = layout["evidence"] / f"{entity_id}.json"
        if not path.is_file():
            return False
        evidence = load_json(path)
        return is_publishable_source_evidence(evidence) and evidence_matches_book(evidence, requested)
    if hit.get("doc_type") == "claim" and entity_id:
        path = layout["claims"] / f"{entity_id}.json"
        if not path.is_file():
            return False
        claim = load_json(path)
        return bool(_claim_support_evidence(claim, requested))
    return False


def _generic_candidate_records(
    *,
    claims: list[dict],
    evidences: list[dict],
    compare_bundle: dict | None,
    book_title: str,
    topic_terms: list[str],
    formal_only: bool = False,
    require_topic_formal_coverage: bool = False,
) -> tuple[list[dict[str, Any]], list[str], bool]:
    """Return only publishable same-book candidates and their covered topics."""
    records: list[dict[str, Any]] = []
    covered_terms: list[str] = []
    safe = True

    def formal_matched_terms(support: list[dict[str, Any]]) -> list[str]:
        return [
            term
            for term in topic_terms
            if any(evidence_has_formal_topic_statement(item, [term]) for item in support)
        ]

    def add_terms(text: Any, support: list[dict[str, Any]] | None = None) -> None:
        if require_topic_formal_coverage:
            for term in formal_matched_terms(support or []):
                if term not in covered_terms:
                    covered_terms.append(term)
            return
        coverage = topic_coverage(text, topic_terms)
        for term in coverage.get("matched_terms", []):
            if term not in covered_terms:
                covered_terms.append(term)

    for claim in claims:
        support = _claim_support_evidence(claim, book_title)
        if not support:
            safe = False
            continue
        if formal_only and not all(evidence_has_formal_topic_statement(item, topic_terms) for item in support):
            continue
        if require_topic_formal_coverage and not formal_matched_terms(support):
            continue
        records.append(
            {
                "kind": "claim",
                "claim_id": str(claim.get("claim_id") or ""),
                "evidence_ids": [str(item.get("evidence_id") or "") for item in support if item.get("evidence_id")],
                "text": _claim_topic_text(claim),
            }
        )
        add_terms(_claim_topic_text(claim), support)

    for evidence in evidences:
        if not is_publishable_source_evidence(evidence) or not evidence_matches_book(evidence, book_title):
            safe = False
            continue
        if formal_only and not evidence_has_formal_topic_statement(evidence, topic_terms):
            continue
        if require_topic_formal_coverage and not formal_matched_terms([evidence]):
            continue
        evidence_id = str(evidence.get("evidence_id") or "").strip()
        if not evidence_id:
            safe = False
            continue
        records.append(
            {
                "kind": "evidence",
                "evidence_id": evidence_id,
                "evidence_ids": [evidence_id],
                "text": f"{evidence.get('title', '')}\n{evidence.get('content', '')}",
            }
        )
        add_terms(records[-1]["text"], [evidence])

    # Keep the selected comparison components explicit.  They are the only
    # candidates a comparison summary is allowed to depend on.
    if compare_bundle and not require_topic_formal_coverage:
        selected = []
        for key in ("primary_claim", "left_claim", "right_claim"):
            value = compare_bundle.get(key)
            if isinstance(value, dict):
                selected.append(("claim", value))
        for key in ("primary_evidence", "left_evidence", "right_evidence"):
            value = compare_bundle.get(key)
            if isinstance(value, dict):
                selected.append(("evidence", value))
        for kind, value in selected:
            if kind == "claim":
                add_terms(_claim_topic_text(value))
            else:
                add_terms(f"{value.get('title', '')}\n{value.get('content', '')}")

    return records, covered_terms, safe


def build_generic_gate(
    *,
    answer_mode: str,
    book_title: str,
    intent: str,
    claims: list[dict],
    evidences: list[dict],
    compare_bundle: dict | None,
    topic_terms: list[str],
    formal_only: bool = False,
    require_topic_formal_coverage: bool = False,
) -> dict[str, Any]:
    """Build a fail-closed gate for ordinary, book-scoped answers."""
    requested = str(book_title or "").strip()
    terms = [str(item).strip() for item in topic_terms if str(item).strip()]
    base = {
        "status": "blocked",
        "book_title": requested,
        "topic_terms": terms,
        "matched_terms": [],
        "dependency_evidence_ids": [],
        "candidate_count": 0,
        "relevance_ok": False,
        "same_book_ok": False,
        "structured_answer_ok": answer_mode in GENERIC_ANSWER_MODES,
        "formal_statement_required": formal_only,
        "formal_statement_ok": not formal_only,
        "formal_topic_coverage_required": require_topic_formal_coverage,
        "formal_topic_coverage_ok": not require_topic_formal_coverage,
        "failure_reason": "",
        "next_action": "",
    }
    if not requested:
        base.update(
            failure_reason="未能确定当前教材，已停止通用检索。",
            next_action="请明确教材名称，或先在当前任务中确认唯一主教材。",
        )
        return base
    if not terms:
        base.update(
            failure_reason="问题中没有提取到稳定的主题词，已停止不确定回答。",
            next_action="请补充具体概念、公式或两个要比较的对象。",
        )
        return base

    records, covered_terms, safe = _generic_candidate_records(
        claims=claims,
        evidences=evidences,
        compare_bundle=compare_bundle,
        book_title=requested,
        topic_terms=terms,
        formal_only=formal_only,
        require_topic_formal_coverage=require_topic_formal_coverage,
    )
    dependency_ids = list(
        dict.fromkeys(
            str(evidence_id).strip()
            for record in records
            for evidence_id in record.get("evidence_ids", [])
            if str(evidence_id).strip()
        )
    )
    relevance_ok = bool(records) and all(term in covered_terms for term in terms)
    same_book_ok = bool(records) and safe and bool(dependency_ids)
    base.update(
        matched_terms=covered_terms,
        dependency_evidence_ids=dependency_ids,
        candidate_count=len(records),
        relevance_ok=relevance_ok,
        same_book_ok=same_book_ok,
        formal_statement_ok=(not formal_only) or bool(records),
        formal_topic_coverage_ok=(not require_topic_formal_coverage) or all(term in covered_terms for term in terms),
    )
    if not records and require_topic_formal_coverage:
        base.update(
            failure_reason="比较请求没有找到当前教材中分别覆盖全部主题的正式同书证据。",
            next_action="请补充每个比较对象各自的正式定义、性质或比较证据。",
        )
    elif not records and formal_only:
        base.update(
            failure_reason="定义/‘是什么’请求没有找到当前教材中支持正式定义或定理陈述的证据。",
            next_action="请补充当前教材中包含正式定义、定理或公式陈述的正文证据。",
        )
    elif not records:
        base.update(
            failure_reason="当前教材范围内没有找到与问题主题匹配的可发布证据。",
            next_action="请补充章节、页码或先发布该概念的同书审核证据。",
        )
    elif not same_book_ok:
        base.update(
            failure_reason="检索候选未能全部证明属于当前教材的可发布证据。",
            next_action="请补充当前教材的正式证据；不会用其他教材内容填补。",
        )
    elif not relevance_ok:
        base.update(
            failure_reason=(
                "比较请求的正式同书证据没有分别覆盖问题中的全部主题。"
                if require_topic_formal_coverage
                else "当前教材证据没有覆盖问题中的全部有效主题词。"
            ),
            next_action=(
                "请补充缺失比较对象的正式定义、性质或比较证据。"
                if require_topic_formal_coverage
                else "请拆分问题或补充覆盖全部主题词的同书证据。"
            ),
        )
    elif not base["structured_answer_ok"]:
        base.update(
            failure_reason="当前只有章节层回退，不能把章节概览当作通用知识结论。",
            next_action="请先补充审核通过的主张或正文证据。",
        )
    else:
        base["status"] = "exact"
    return base
