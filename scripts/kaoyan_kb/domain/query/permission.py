"""One teaching decision shared by query, views, batches and save validation."""
from __future__ import annotations

from functools import wraps


def teaching_permission(payload: dict) -> bool:
    request = payload.get('request_resolution') or {}
    kind = request.get('source_request_kind')
    grounding = payload.get('answer_grounding') or {}
    teaching = payload.get('teaching_bundle') or {}
    page = payload.get('page_anchor') or {}
    crosscheck = payload.get('page_crosscheck') or {}
    runtime = payload.get('runtime_context') or {}
    if any(runtime.get(key) is False for key in ['page_locator_index_available', 'exercise_locator_index_available']):
        return False
    if crosscheck.get('required') and crosscheck.get('status') != 'confirmed':
        return False
    if payload.get('book_resolution', {}).get('status') in {'ambiguous', 'not_found', 'unavailable'}:
        return False
    if kind == 'page_content':
        body = payload.get('page_content_bundle') or {}
        return bool(page.get('match_status') == 'exact_evidence' and body.get('status') == 'exact' and body.get('content') and body.get('evidence_ids'))
    if kind != 'exercise':
        return False
    problem = grounding.get('problem') or {}
    if page.get('exercise_match_status') not in {None, 'matched'}:
        return False
    expected_page = (request.get('page') or {}).get('number', page.get('requested_page'))
    if (request.get('page') or {}).get('semantics', 'exact_page') == 'exact_page' and expected_page is not None:
        if problem.get('printed_pages') and expected_page not in problem['printed_pages']:
            return False
    requested_book_id = (payload.get('book_resolution') or {}).get('book_id') or page.get('book_id')
    if requested_book_id and problem.get('book_id') and requested_book_id != problem['book_id']:
        return False
    if page.get('requested_page') is not None and page.get('match_status') != 'exact_evidence':
        return False
    if page.get('match_status') in {'unavailable', 'stale', 'unmapped', 'ambiguous'}:
        return False
    citations = teaching.get('citations') or {}
    if 'citation_coverage_ok' in payload and not payload['citation_coverage_ok']:
        return False
    if not (grounding.get('status') == 'exact_answer' and grounding.get('can_conclude') and teaching.get('status') == 'exact'):
        return False
    if not (teaching.get('problem_text', '').strip() and teaching.get('source_answer_text', '').strip()):
        return False
    for side, key in [('problem', 'problem_evidence_ids'), ('solution', 'solution_evidence_ids')]:
        evidence = grounding.get(side) or {}
        ids = set(evidence.get('evidence_ids') or ([evidence['evidence_id']] if evidence.get('evidence_id') else []))
        if not citations.get(key) or (ids and not ids.issubset(citations[key])):
            return False
    return True


def finalize_result(result: dict) -> dict:
    kind = (result.get('request_resolution') or {}).get('source_request_kind')
    if kind not in {'exercise', 'page_content'}:
        return result
    allowed = teaching_permission(result)
    verification = result.setdefault('page_verification', {})
    verification['textbook_explanation_allowed'] = allowed
    if allowed:
        if kind == 'exercise':
            result['answer_mode'] = 'exercise_pair'
            location = result.setdefault('textbook_location', {})
            location.update(status='exact', printed_pages=list((result['answer_grounding'].get('problem') or {}).get('printed_pages') or []))
        result['fallback_note'] = ''
    else:
        grounding = result.get('answer_grounding') or {}
        page = result.get('page_anchor') or {}
        reason = ((result.get('book_resolution') or {}).get('reason')
                  or (result.get('page_crosscheck') or {}).get('reason')
                  or page.get('unavailable_reason')
                  or grounding.get('failure_reason')
                  or (result.get('page_content_bundle') or {}).get('failure_reason')
                  or '教材身份、页码、答案或引用尚未完成一致核验。')
        next_action = grounding.get('next_action') or '请核对该教材的正式定位及证据状态后重试。'
        if kind == 'exercise':
            grounding.update(can_conclude=False, failure_reason=reason, next_action=next_action)
            if grounding.get('status') == 'exact_answer':
                grounding['status'] = 'answer_unavailable' if page.get('match_status') in {'unavailable', 'unmapped', 'stale'} else 'answer_ambiguous'
            for side in ['problem', 'solution']:
                for key in ['content', 'text', 'source_answer_text']:
                    (grounding.get(side) or {}).pop(key, None)
            teaching = result.get('teaching_bundle') or {}
            teaching.update(status='blocked', problem_text='', source_answer_text='', failure_reason=reason)
            for key in teaching.get('citations', {}):
                teaching['citations'][key] = []
            result['teaching_bundle'] = teaching
            result['answer_grounding'] = grounding
            if result.get('answer_mode') in {'exercise_pair', 'accepted_evidence'}:
                result['answer_mode'] = 'exercise_unconfirmed'
        else:
            body = result.get('page_content_bundle') or {}
            if body.get('status') == 'exact':
                body.update(status='blocked', content='', evidence_ids=[], failure_reason=reason, next_action=next_action)
        result['concept_routes'] = []
        # Keep location pointers, never leak rejected bodies in diagnostic query output.
        for key in ['evidence_hits', 'claim_hits', 'retrieval_hits', 'fallback_hits']:
            result[key] = []
        (result.get('page_anchor') or {}).pop('snippets', None)
        for key in ['question_content', 'answer_content']:
            (result.get('exercise_anchor') or {}).pop(key, None)
        for side in ['question', 'solution']:
            (result.get('exercise_route', {}).get(side) or {}).pop('content', None)
        result['fallback_note'] = reason
        if not verification.get('summary') or '可在证据范围' in verification.get('summary', ''):
            verification['summary'] = reason
    verification['answer_mode'] = result.get('answer_mode', '')
    return result


def finalized(function):
    @wraps(function)
    def wrapper(*args, **kwargs):
        return finalize_result(function(*args, **kwargs))
    return wrapper
