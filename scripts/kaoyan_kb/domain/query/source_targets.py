"""Request-local source targets; registered identities, never a second catalogue."""
from __future__ import annotations

from copy import deepcopy
import re
from typing import Any

from common import kb_layout
from .read_session import read_json, with_read_scope
from .book_identity import normalize_title

VERSION = "source-targets.v1"


def catalogue() -> list[dict]:
    path = kb_layout()['indexes'] / 'book_series_index.json'
    return read_json(path).get('series', []) if path.is_file() else []


def book_mentions(text: str) -> list[dict]:
    """Longest registered mention wins; a descriptive title must be unique."""
    hits = []
    series_list = catalogue()
    for series in series_list:
        volumes = series.get('volumes', [])
        entries = [(n, volumes) for n in [series.get('canonical_title'), *series.get('aliases', [])] if n]
        for volume in volumes:
            entries.extend((n, [volume]) for n in [volume.get('title'), volume.get('book_id'), *(volume.get('aliases') or [])] if n)
        for name, candidates in entries:
            # Ignore presentation whitespace, not arbitrary title fragments.
            pattern = r'\s*'.join(re.escape(c) for c in str(name) if not c.isspace())
            for match in re.finditer(pattern, text, re.I):
                if name.isdigit() and ((match.start() and text[match.start()-1].isascii() and text[match.start()-1].isalnum()) or (match.end() < len(text) and text[match.end()].isdigit())):
                    continue
                narrowed = [v for v in candidates if normalize_title(name) in normalize_title(v.get('title'))]
                hits.append(dict(start=match.start(), end=match.end(), text=match.group(), candidates=narrowed or candidates, series_id=series.get('series_id', ''), basis='registered_alias'))
    for match in re.finditer(r'(?:高数|高等数学|线代|线性代数)(?:辅导讲义|讲义)?\s*(?:基础篇|强化篇)', text):
        words = match.group().replace('高数', '高等数学').replace('线代', '线性代数')
        subject = '高等数学' if '高等数学' in words else '线性代数'
        stage = '基础篇' if '基础篇' in words else '强化篇'
        candidates = [v for s in series_list for v in s.get('volumes', []) if subject in v.get('title', '') and stage in v.get('title', '')]
        hits.append(dict(start=match.start(), end=match.end(), text=match.group(), candidates=candidates, series_id='', basis='subject_and_stage'))
    for match in re.finditer(r'《([^》]+)》', text):
        inside = [h for h in hits if match.start() <= h['start'] and h['end'] <= match.end()]
        if not inside:
            hits.append(dict(start=match.start(), end=match.end(), text=match.group(1), candidates=[], series_id='', basis='unregistered_title'))
    selected = []
    for hit in sorted(hits, key=lambda h: (h['start'], -(h['end']-h['start']))):
        if selected and hit['start'] < selected[-1]['end']:
            continue
        if selected and hit['start'] == selected[-1]['end'] and hit['series_id'] and hit['series_id'] == selected[-1]['series_id']:
            selected[-1]['end'] = hit['end']
            selected[-1]['text'] = text[selected[-1]['start']:hit['end']]
            continue
        selected.append(hit)
    return selected


def page_mentions(text: str) -> list[dict]:
    hits = []
    aliases = None
    for match in re.finditer(r'(?:第?\s*([0-9]+)\s*页|[Pp]\s*[.．]?\s*([0-9]+)(?![0-9]))', text):
        if match.group(2) and match.start() and re.match(r'[A-Za-z0-9]', text[match.start()-1]):
            if aliases is None:
                aliases = book_mentions(text)
            if not any(h['end'] == match.start() for h in aliases):
                continue
        hits.append(dict(start=match.start(), end=match.end(), number=int(match.group(1) or match.group(2))))
    return hits


def split_targets(query: str) -> list[dict]:
    pages = page_mentions(query)
    if len(pages) < 2:
        return []
    # A page interval is a single scope, not two independent targets.
    if len(pages) == 2 and re.fullmatch(r'\s*[-—–~～至到]\s*', query[pages[0]['end']:pages[1]['start']]):
        return []
    mentions = book_mentions(query)
    starts = [0]
    for previous, page in zip(pages, pages[1:]):
        new_books = [h for h in mentions if previous['end'] <= h['start'] < page['start']]
        starts.append(new_books[0]['start'] if new_books else page['start'])
    targets = []
    inherited = None
    for index, page in enumerate(pages):
        start, end = starts[index], starts[index+1] if index+1 < len(starts) else len(query)
        own = [h for h in mentions if start <= h['start'] < page['start']]
        if own:
            inherited = own[-1]
        ids = {h['series_id'] or h['text'] for h in own}
        targets.append(dict(target_id=f't{index+1}', fragment=query[start:end], span=[start, end], printed_page=page['number'], book_hint=deepcopy(inherited), reason='target-book-ambiguous' if len(ids) > 1 else ''))
    return targets


def resolve_mentioned_book(query: str, *, book_hint: dict | None = None) -> dict | None:
    mentions = [book_hint] if book_hint is not None else book_mentions(query)
    if not mentions:
        return None
    candidates = {str(v['book_id']): v for h in mentions for v in h['candidates'] if v.get('book_id') and v.get('status', 'active') == 'active'}
    if len(mentions) > 1:
        common_ids = set.intersection(*(set(v.get('book_id') for v in h['candidates']) for h in mentions))
        if not common_ids:
            return dict(status='ambiguous', source='query', book_title='', candidates=list(candidates.values()), reason='multiple-book-mentions')
        candidates = {k: v for k, v in candidates.items() if k in common_ids}
    if len(candidates) == 1:
        volume = next(iter(candidates.values()))
        return dict(status='exact', source='query', book_title=volume['title'], book_id=volume['book_id'], current_task_path='', match_basis=mentions[0]['basis'])
    # Preserve a registered series alias for formal relation-based disambiguation.
    hint = mentions[0]
    title = hint['text'] if hint.get('series_id') else ''
    return dict(status='ambiguous' if candidates else 'not_found', source='query', book_title=title, candidates=list(candidates.values()), current_task_path='', reason='book-identity-unconfirmed')


def is_multi(payload: dict) -> bool:
    return payload.get('source_targets_version') == VERSION


@with_read_scope
def query_source_targets(*, query_one, vault_root, subject, chapter, query, topk, book_title=None, printed_page=None, exercise_label=None, confirmed_book_title=None):
    targets = split_targets(query)
    if not targets:
        return None
    from common import runtime_context_payload, validate_entity_contract
    from .permission import teaching_permission
    results, cache = [], {}
    for target in targets:
        reason = target['reason']
        if printed_page is not None or exercise_label:
            reason = 'global-target-override-ambiguous'
        hint = target.pop('book_hint')
        binding = resolve_mentioned_book(target['fragment'], book_hint=hint) if hint else None
        target_book = binding.get('book_title') if binding else book_title
        if binding and not target_book:
            reason = binding.get('reason', 'book-identity-unconfirmed')
        result = None
        if not reason:
            # The full text affects exercise scope, concept routes and teaching.
            # Share JSON reads across targets, but only reuse identical requests.
            key = (target_book, target['fragment'])
            if key not in cache:
                context = {'confirmed_book_title': confirmed_book_title} if confirmed_book_title else {}
                cache[key] = query_one(vault_root, subject, chapter, target['fragment'], topk, None, target_book, **context)
            result = deepcopy(cache[key])
            result['query'] = target['fragment']
            result['request_resolution']['original_query'] = target['fragment']
            result['request_resolution']['page']['explicit_cli'] = False
            result['request_resolution'].setdefault('field_sources', {})['printed_page'] = 'query'
            # Report the original source of the book, not the internal call parameter.
            if binding:
                result['book_resolution']['source'] = 'query'
                result['request_resolution']['field_sources']['book_title'] = 'query'
                binding = dict(result['book_resolution'], requested_text=hint['text'])
            if not teaching_permission(result):
                reason = (result.get('answer_grounding') or {}).get('failure_reason') or (result.get('page_content_bundle') or {}).get('failure_reason') or 'target-evidence-unconfirmed'
        results.append(dict(**target, status='blocked' if reason else 'exact', failure_reason=reason, next_action='请核对该目标的教材、页码或证据状态。' if reason else '', binding=binding or {}, result=result))
    count = sum(x['status'] == 'exact' for x in results)
    payload = dict(source_targets_version=VERSION, view='query', original_query=query, subject=subject, runtime_context=runtime_context_payload(vault_root_override=vault_root), status='exact' if count == len(results) else 'partial' if count else 'blocked', comparison_allowed=count == len(results), items=results)
    validate_entity_contract('source_targets_view', payload)
    return payload


def project_targets(payload: dict, *, view: str) -> dict:
    from answer_local_question import build_answer_contract, build_teaching_answer_view
    projected = deepcopy(payload)
    projected['view'] = view
    for item in projected['items']:
        if item['result'] is not None:
            contract = build_answer_contract(item['result'])
            item['result'] = build_teaching_answer_view(contract, saved=False, saved_at='') if view == 'teaching' else contract
    return projected


def render_targets(payload: dict) -> str:
    from answer_local_question import build_answer_contract, render_text
    lines = [f"多目标检索：{payload['status']}；可比较：{payload['comparison_allowed']}\n"]
    for item in payload['items']:
        lines.append(f"\n## {item['target_id']}：{item['fragment']}\n")
        if item['result'] is not None:
            result = item['result']
            lines.append(render_text(result if 'answer_contract_version' in result else build_answer_contract(result)))
        else:
            lines.append(item['failure_reason'] + '\n' + item['next_action'])
    return '\n'.join(lines) + '\n'
