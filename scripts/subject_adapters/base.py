from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


def normalize_text(value: str) -> str:
    return str(value or "").strip().lower()


def dedupe_terms(values: Iterable[str]) -> list[str]:
    seen: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if text and text not in seen:
            seen.append(text)
    return seen


@dataclass(frozen=True)
class SubjectAdapter:
    subject: str
    adapter_id: str

    def evidence_terms(self, evidence: dict) -> list[str]:
        return dedupe_terms([evidence.get("title", ""), evidence.get("content", "")])

    def node_aliases(self, node: dict) -> list[str]:
        return []

    def node_keywords(self, node: dict) -> list[str]:
        return []

    def score_bonus(self, evidence: dict, node: dict) -> float:
        return 0.0
