from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import publish_canonical_cards as cards


def setup_cards(monkeypatch, tmp_path, *, execute=True):
    layout = {key: tmp_path / "kb" / key for key in ("indexes", "syllabus")}
    layout["syllabus"].mkdir(parents=True)
    (layout["syllabus"] / "subject.json").write_text(
        json.dumps({"nodes": [{"node_id": "NODE", "title": "card"}]}), encoding="utf-8"
    )
    args = SimpleNamespace(yes=execute, force=False, dry_run=False, no_backup=True,
                           subject=["subject"], vault_root=str(tmp_path / "vault"), format="quiet")
    monkeypatch.setattr(cards, "parse_args", lambda: args)
    monkeypatch.setattr(cards, "kb_layout", lambda: layout)
    monkeypatch.setattr(cards, "resolve_subject", lambda value: (value, {"dir": "subject"}))
    monkeypatch.setattr(cards, "validate_entity_contract", lambda *args: None)
    monkeypatch.setattr(cards, "grouped_card_materials", lambda *args: (
        {("subject", "NODE"): {"ready_clusters": [{"claims": [{"claim_id": "CL"}]}]}}, {}
    ))
    monkeypatch.setattr(cards, "render_card", lambda *args: "new card\n")
    path = tmp_path / "vault" / "subject" / cards.CARD_DIRNAME / "card.md"
    return args, layout, path


@pytest.mark.parametrize("existing", [False, True])
def test_index_failure_restores_card_bytes_and_existence(monkeypatch, tmp_path, existing):
    _, layout, path = setup_cards(monkeypatch, tmp_path)
    index = layout["indexes"] / "canonical_cards.json"
    before = b"\xef\xbb\xbfold card\r\n"
    if existing:
        path.parent.mkdir(parents=True)
        path.write_bytes(before)
        index.parent.mkdir(parents=True)
        index.write_bytes(b'{"count": 0, "items": []}\r\n')
    index_before = index.read_bytes() if index.exists() else None
    original_save = cards.save_json

    def fail_after_write(target, value):
        original_save(target, value)
        raise OSError("index write failed")

    monkeypatch.setattr(cards, "save_json", fail_after_write)
    with pytest.raises(OSError, match="index write failed"):
        cards.main()
    assert path.exists() is existing
    assert index.exists() is existing
    if existing:
        assert path.read_bytes() == before
        assert index.read_bytes() == index_before


def test_preview_is_zero_write_even_with_explicit_dry_run_and_yes(monkeypatch, tmp_path):
    args, _, path = setup_cards(monkeypatch, tmp_path)
    args.dry_run = True
    before = {p.relative_to(tmp_path): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    assert cards.main() == 0
    after = {p.relative_to(tmp_path): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    assert after == before
    assert not path.parent.exists()


def test_success_publishes_card_and_index(monkeypatch, tmp_path):
    _, layout, path = setup_cards(monkeypatch, tmp_path)
    assert cards.main() == 0
    assert "new card" in path.read_text(encoding="utf-8")
    index = json.loads((layout["indexes"] / "canonical_cards.json").read_text(encoding="utf-8"))
    assert index["count"] == 1
    assert index["items"][0]["card_path"] == str(path)


def test_failure_after_deletion_restores_deleted_card_and_index(monkeypatch, tmp_path):
    _, layout, new_card = setup_cards(monkeypatch, tmp_path)
    new_card.parent.mkdir(parents=True)
    old_card = new_card.with_name("old.md")
    old_bytes = b"owned old card\r\n"
    old_card.write_bytes(old_bytes)
    index = layout["indexes"] / "canonical_cards.json"
    index.parent.mkdir(parents=True)
    index_bytes = b'{"count": 0, "items": []}\r\n'
    index.write_bytes(index_bytes)
    monkeypatch.setattr(cards, "is_owned_generated_markdown", lambda *args: True)
    unlink = Path.unlink
    failed = False

    def fail_after_delete(path, *args, **kwargs):
        nonlocal failed
        unlink(path, *args, **kwargs)
        if path == old_card and not failed:
            failed = True
            raise OSError("delete failure")

    monkeypatch.setattr(Path, "unlink", fail_after_delete)
    with pytest.raises(OSError, match="delete failure"):
        cards.main()
    assert old_card.read_bytes() == old_bytes
    assert index.read_bytes() == index_bytes
    assert not new_card.exists()
