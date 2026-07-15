from __future__ import annotations

from .base import SubjectAdapter, dedupe_terms


def _contains_any(text: str, patterns: list[str]) -> bool:
    return any(pattern and pattern in text for pattern in patterns)


class MathAdapter(SubjectAdapter):
    def evidence_terms(self, evidence: dict) -> list[str]:
        terms = super().evidence_terms(evidence)
        text = " ".join(terms)
        integral_context = _contains_any(
            text,
            [
                "\u79ef\u5206",
                "\u4e0d\u5b9a\u79ef\u5206",
                "\u5b9a\u79ef\u5206",
                "\u53d8\u9650\u79ef\u5206",
                "\u725b\u987f-\u83b1\u5e03\u5c3c\u8328",
                "\u53cd\u5e38\u79ef\u5206",
                "\u79ef\u5206\u4e2d\u503c\u5b9a\u7406",
            ],
        )
        if _contains_any(text, ["\u8fde\u7eed", "\u5de6\u8fde\u7eed", "\u53f3\u8fde\u7eed", "\u95f4\u65ad"]):
            terms.extend(["\u8fde\u7eed\u6027", "\u8fde\u7eed\u51fd\u6570", "\u95f4\u65ad", "\u95f4\u65ad\u70b9"])
        if _contains_any(text, ["\u6781\u9650", "\u65e0\u7a77\u5c0f", "\u65e0\u7a77\u5927"]):
            terms.extend(["\u6781\u9650", "\u51fd\u6570\u6781\u9650", "\u65e0\u7a77\u5c0f", "\u65e0\u7a77\u5927"])
        if not integral_context and _contains_any(text, ["\u5bfc\u6570", "\u6c42\u5bfc", "\u5fae\u5206", "\u53d8\u5316\u7387", "\u5207\u7ebf\u659c\u7387", "\u53ef\u5bfc", "\u9ad8\u9636\u5bfc\u6570"]):
            terms.extend(["\u5bfc\u6570", "\u6c42\u5bfc", "\u5fae\u5206", "\u53d8\u5316\u7387", "\u5207\u7ebf\u659c\u7387", "\u53ef\u5bfc", "\u9ad8\u9636\u5bfc\u6570", "\u5bfc\u51fd\u6570"])
        if _contains_any(
            text,
            [
                "\u79ef\u5206",
                "\u4e0d\u5b9a\u79ef\u5206",
                "\u5b9a\u79ef\u5206",
                "\u53d8\u9650\u79ef\u5206",
                "\u725b\u987f-\u83b1\u5e03\u5c3c\u8328",
                "\u53cd\u5e38\u79ef\u5206",
                "\u79ef\u5206\u4e2d\u503c\u5b9a\u7406",
            ],
        ):
            terms.extend(
                [
                    "\u79ef\u5206",
                    "\u4e0d\u5b9a\u79ef\u5206",
                    "\u5b9a\u79ef\u5206",
                    "\u53d8\u9650\u79ef\u5206",
                    "\u725b\u987f-\u83b1\u5e03\u5c3c\u8328\u516c\u5f0f",
                    "\u53cd\u5e38\u79ef\u5206",
                    "\u79ef\u5206\u8ba1\u7b97",
                    "\u79ef\u5206\u5e94\u7528",
                    "\u79ef\u5206\u4e2d\u503c\u5b9a\u7406",
                    "\u79ef\u5206\u6c42\u5bfc",
                ]
            )
        return dedupe_terms(terms)

    def score_bonus(self, evidence: dict, node: dict) -> float:
        haystack = " ".join(self.evidence_terms(evidence))
        node_text = " ".join([node.get("title", ""), *node.get("aliases", []), *node.get("keywords", [])])
        node_id = str(node.get("node_id", "")).strip()
        integral_context = _contains_any(
            haystack,
            [
                "\u79ef\u5206",
                "\u4e0d\u5b9a\u79ef\u5206",
                "\u5b9a\u79ef\u5206",
                "\u53d8\u9650\u79ef\u5206",
                "\u725b\u987f-\u83b1\u5e03\u5c3c\u8328",
                "\u53cd\u5e38\u79ef\u5206",
            ],
        )
        if _contains_any(haystack, ["\u8fde\u7eed", "\u95f4\u65ad"]) and _contains_any(node_text, ["\u8fde\u7eed", "\u95f4\u65ad"]):
            return 0.18
        if _contains_any(haystack, ["\u6781\u9650", "\u65e0\u7a77\u5c0f", "\u65e0\u7a77\u5927"]) and _contains_any(node_text, ["\u6781\u9650", "\u65e0\u7a77\u5c0f", "\u65e0\u7a77\u5927"]):
            return 0.12
        if (not integral_context) and _contains_any(haystack, ["\u5bfc\u6570", "\u6c42\u5bfc", "\u5fae\u5206", "\u53d8\u5316\u7387", "\u5207\u7ebf\u659c\u7387", "\u53ef\u5bfc"]) and _contains_any(node_text, ["\u5bfc\u6570", "\u6c42\u5bfc", "\u5fae\u5206", "\u53d8\u5316\u7387", "\u5207\u7ebf\u659c\u7387", "\u53ef\u5bfc"]):
            return 0.18
        if integral_context and node_id == "SYL-MATH-013":
            return 0.0
        if _contains_any(
            haystack,
            [
                "\u79ef\u5206",
                "\u4e0d\u5b9a\u79ef\u5206",
                "\u5b9a\u79ef\u5206",
                "\u53d8\u9650\u79ef\u5206",
                "\u725b\u987f-\u83b1\u5e03\u5c3c\u8328",
                "\u53cd\u5e38\u79ef\u5206",
            ],
        ) and _contains_any(
            node_text,
            [
                "\u79ef\u5206",
                "\u4e0d\u5b9a\u79ef\u5206",
                "\u5b9a\u79ef\u5206",
                "\u53d8\u9650\u79ef\u5206",
                "\u725b\u987f-\u83b1\u5e03\u5c3c\u8328",
                "\u53cd\u5e38\u79ef\u5206",
            ],
        ):
            return 0.22
        return 0.0


class Cs408Adapter(SubjectAdapter):
    def evidence_terms(self, evidence: dict) -> list[str]:
        terms = super().evidence_terms(evidence)
        text = " ".join(terms)
        if _contains_any(text, ["\u590d\u6742\u5ea6", "\u65f6\u95f4\u590d\u6742\u5ea6", "\u7a7a\u95f4\u590d\u6742\u5ea6"]):
            terms.extend(["\u590d\u6742\u5ea6", "\u65f6\u95f4\u590d\u6742\u5ea6", "\u7a7a\u95f4\u590d\u6742\u5ea6", "\u6570\u91cf\u7ea7", "o"])
        if _contains_any(text, ["\u6570\u636e\u7ed3\u6784", "\u903b\u8f91\u7ed3\u6784", "\u5b58\u50a8\u7ed3\u6784"]):
            terms.extend(["\u6570\u636e\u7ed3\u6784", "\u903b\u8f91\u7ed3\u6784", "\u5b58\u50a8\u7ed3\u6784", "\u6570\u636e\u5bf9\u8c61"])
        return dedupe_terms(terms)

    def score_bonus(self, evidence: dict, node: dict) -> float:
        haystack = " ".join(self.evidence_terms(evidence))
        node_text = " ".join([node.get("title", ""), *node.get("aliases", []), *node.get("keywords", [])])
        node_title = str(node.get("title", "")).strip()
        if _contains_any(haystack, ["\u590d\u6742\u5ea6", "\u65f6\u95f4\u590d\u6742\u5ea6", "\u7a7a\u95f4\u590d\u6742\u5ea6"]) and "\u7b97\u6cd5\u590d\u6742\u5ea6" in node_title:
            return 0.18
        if _contains_any(haystack, ["\u6570\u636e\u7ed3\u6784", "\u903b\u8f91\u7ed3\u6784", "\u5b58\u50a8\u7ed3\u6784"]) and _contains_any(node_text, ["\u6570\u636e\u7ed3\u6784", "\u903b\u8f91\u7ed3\u6784", "\u5b58\u50a8\u7ed3\u6784"]):
            return 0.12
        return 0.0


class EnglishAdapter(SubjectAdapter):
    def evidence_terms(self, evidence: dict) -> list[str]:
        terms = super().evidence_terms(evidence)
        text = " ".join(terms)
        if _contains_any(text, ["\u4ece\u53e5", "\u957f\u96be\u53e5", "\u4e3b\u5e72", "\u53e5\u6cd5"]):
            terms.extend(["\u957f\u96be\u53e5", "\u4ece\u53e5\u5206\u6790", "\u4ece\u53e5\u8bc6\u522b", "\u53e5\u5b50\u4e3b\u5e72", "\u957f\u96be\u53e5\u5206\u6790"])
        return dedupe_terms(terms)

    def node_aliases(self, node: dict) -> list[str]:
        text = " ".join([node.get("title", ""), *node.get("aliases", []), *node.get("keywords", [])])
        if _contains_any(text, ["\u4e3b\u5e72", "\u957f\u96be\u53e5", "\u4ece\u53e5"]):
            return ["\u957f\u96be\u53e5\u5206\u6790", "\u4ece\u53e5\u5206\u6790", "\u4ece\u53e5\u8bc6\u522b", "\u53e5\u5b50\u4e3b\u5e72"]
        return []

    def score_bonus(self, evidence: dict, node: dict) -> float:
        haystack = " ".join(self.evidence_terms(evidence))
        node_text = " ".join([node.get("title", ""), *node.get("aliases", []), *node.get("keywords", []), *self.node_aliases(node)])
        if _contains_any(haystack, ["\u4ece\u53e5", "\u957f\u96be\u53e5", "\u4e3b\u5e72"]) and _contains_any(node_text, ["\u4ece\u53e5", "\u957f\u96be\u53e5", "\u4e3b\u5e72"]):
            return 0.36
        return 0.0


class PoliticsAdapter(SubjectAdapter):
    def evidence_terms(self, evidence: dict) -> list[str]:
        terms = super().evidence_terms(evidence)
        text = " ".join(terms)
        if _contains_any(text, ["\u77db\u76fe", "\u5bf9\u7acb\u7edf\u4e00", "\u54f2\u5b66"]):
            terms.extend(["\u77db\u76fe", "\u77db\u76fe\u89c4\u5f8b", "\u5bf9\u7acb\u7edf\u4e00", "\u5bf9\u7acb\u7edf\u4e00\u89c4\u5f8b", "\u77db\u76fe\u5206\u6790\u6cd5"])
        return dedupe_terms(terms)

    def node_aliases(self, node: dict) -> list[str]:
        text = " ".join([node.get("title", ""), *node.get("aliases", []), *node.get("keywords", [])])
        if _contains_any(text, ["\u77db\u76fe", "\u5bf9\u7acb\u7edf\u4e00"]):
            return ["\u77db\u76fe\u89c4\u5f8b", "\u5bf9\u7acb\u7edf\u4e00\u89c4\u5f8b", "\u77db\u76fe\u5206\u6790\u6cd5"]
        return []

    def score_bonus(self, evidence: dict, node: dict) -> float:
        haystack = " ".join(self.evidence_terms(evidence))
        node_text = " ".join([node.get("title", ""), *node.get("aliases", []), *node.get("keywords", []), *self.node_aliases(node)])
        if _contains_any(haystack, ["\u77db\u76fe", "\u5bf9\u7acb\u7edf\u4e00"]) and _contains_any(node_text, ["\u77db\u76fe", "\u5bf9\u7acb\u7edf\u4e00"]):
            return 0.42
        return 0.0


ADAPTERS: dict[str, SubjectAdapter] = {
    "\u6570\u5b66": MathAdapter(subject="\u6570\u5b66", adapter_id="math-v1"),
    "408": Cs408Adapter(subject="408", adapter_id="408-v1"),
    "\u82f1\u8bed": EnglishAdapter(subject="\u82f1\u8bed", adapter_id="english-v1"),
    "\u653f\u6cbb": PoliticsAdapter(subject="\u653f\u6cbb", adapter_id="politics-v1"),
}


def get_subject_adapter(subject: str) -> SubjectAdapter:
    try:
        return ADAPTERS[subject]
    except KeyError as exc:
        raise KeyError(f"unsupported subject adapter: {subject}") from exc
