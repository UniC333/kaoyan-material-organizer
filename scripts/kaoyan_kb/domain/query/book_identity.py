"""Exact textbook identity and explicitly registered, unambiguous aliases."""
from __future__ import annotations

import re
import unicodedata
from typing import Any
from types import MappingProxyType

from common import kb_layout
from .read_session import read_derived


def normalize_title(value: Any) -> str:
    return re.sub(r'[^0-9a-z\u4e00-\u9fff]+', '', unicodedata.normalize('NFKC', str(value or '')).lower())


def registered_identities(title: Any) -> set[str]:
    token = normalize_title(title)
    if not token:
        return set()
    path = kb_layout()['indexes'] / 'book_series_index.json'
    if not path.is_file():
        return set()
    return set(read_derived(path, _identity_map).get(token, ()))


def _identity_map(index: dict):
    identities: dict[str, set[str]] = {}
    for series in index.get('series', []):
        volumes = [v for v in series.get('volumes', []) if v.get('book_id')]
        for volume in volumes:
            names = [volume.get('book_id'), volume.get('title'), *volume.get('aliases', [])]
            for token in {normalize_title(name) for name in names if name}:
                identities.setdefault(token, set()).add(str(volume['book_id']))
        series_names = [series.get('canonical_title'), *series.get('aliases', [])]
        # Containment is only used to assign an explicitly registered alias to a
        # volume in its own series, never to accept arbitrary user title fragments.
        for token in {normalize_title(name) for name in series_names if name}:
            candidates = [v for v in volumes if token in normalize_title(v.get('title'))]
            identities.setdefault(token, set()).update(str(v['book_id']) for v in (candidates or volumes))
    return MappingProxyType({token: frozenset(ids) for token, ids in identities.items()})


def titles_match(actual: Any, requested: Any) -> bool:
    left, right = normalize_title(actual), normalize_title(requested)
    if not left or not right:
        return False
    actual_ids, requested_ids = registered_identities(actual), registered_identities(requested)
    if len(actual_ids) > 1 or len(requested_ids) > 1:
        return False
    if actual_ids and requested_ids:
        return actual_ids == requested_ids
    return left == right
