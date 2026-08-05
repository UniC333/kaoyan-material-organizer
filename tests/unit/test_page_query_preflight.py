from __future__ import annotations

import sys
from pathlib import Path

import pytest


SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import ask_local_knowledge as ask_module
import query_local_knowledge as query_module


@pytest.mark.parametrize(
    ("query", "printed_page"),
    [("P76 例3.19", None), ("例3.19", 76)],
)
def test_explicit_page_without_subject_has_actionable_error(query: str, printed_page: int | None) -> None:
    error = query_module.explicit_page_subject_error(subject=None, query=query, printed_page=printed_page)

    assert "require --subject" in error
    assert "--printed-page 76" in error
    assert "page_not_found" not in error


def test_query_cli_stops_explicit_page_without_subject(monkeypatch) -> None:
    monkeypatch.setattr(sys, "argv", ["query_local_knowledge.py", "--query", "P76 例3.19"])

    with pytest.raises(SystemExit, match="explicit page requests require --subject"):
        query_module.main()


def test_ask_cli_stops_explicit_page_without_subject(monkeypatch) -> None:
    monkeypatch.setattr(sys, "argv", ["ask_local_knowledge.py", "--printed-page", "76", "--question", "例3.19"])

    with pytest.raises(SystemExit, match="explicit page requests require --subject"):
        ask_module.main()
