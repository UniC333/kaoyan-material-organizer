#!/usr/bin/env python3
"""Build a derived, per-chapter exercise OCR/review/publication status report."""
from __future__ import annotations
import argparse, json
from pathlib import Path
from common import ensure_kb_layout, load_all_json, load_json_or_default, sanitize_name, save_json

def main() -> int:
    p=argparse.ArgumentParser(); p.add_argument('--subject',required=True); p.add_argument('--book-title',required=True); p.add_argument('--pdf-source-id',default=''); p.add_argument('--chapter-number',type=int); p.add_argument('--format',choices=('json','quiet'),default='json'); a=p.parse_args()
    layout=ensure_kb_layout(); source_id=a.pdf_source_id
    if not source_id:
        matches=[x for x in load_all_json(layout['sources']) if x.get('subject')==a.subject and x.get('source_name')==a.book_title and x.get('material_type')=='book-pdf']
        if len(matches)!=1: raise SystemExit('[ERROR] book PDF source is not unique')
        source_id=str(matches[0]['source_id'])
    report=load_json_or_default(layout['indexes']/ 'pdf_ocr_runs'/f"{a.subject.lower()}-{sanitize_name(a.book_title)}.json", {})
    review={int(x.get('pdf_page',0) or 0):x for x in load_json_or_default(layout['review_queues']/'pdf-page-review'/f'{source_id}.json',{}).get('items',[]) if isinstance(x,dict)}
    evidence={int((x.get('locator') or {}).get('page_start',0) or 0):x for x in load_all_json(layout['evidence']) if x.get('source_id')==source_id and x.get('origin_type')=='pdf_page_ocr'}
    relations=[x for x in load_json_or_default(layout['indexes']/'exercise_locator_index.json',{}).get('relations',[]) if x.get('source_id')==source_id]
    if a.chapter_number:
        chapter_prefix=str(a.chapter_number)
        relations=[x for x in relations if str(x.get('section_root','')).split('.',1)[0]==chapter_prefix]
    pages=[]
    for x in report.get('pages',report.get('chapters',[])):
        n=int(x.get('pdf_page',x.get('page_start',0)) or 0); ch=int(x.get('chapter_number',0) or 0)
        if a.chapter_number and ch!=a.chapter_number: continue
        decision=review.get(n,{}); ev=evidence.get(n,{})
        status='published' if ev else ('reviewed' if decision.get('review_status')=='accepted' else decision.get('review_status') or 'ocr_pending_review')
        pages.append({'pdf_page':n,'chapter_number':ch,'chapter_title':x.get('chapter_title',''),'status':status,'review_note':decision.get('note',''),'evidence_id':ev.get('evidence_id','')})
    payload={'schema_version':'exercise-coverage.v1','subject':a.subject,'book_title':a.book_title,'source_id':source_id,'chapter_number':a.chapter_number,'pages':sorted(pages,key=lambda x:x['pdf_page']),'relations':relations,'summary':{'page_count':len(pages),'published_count':sum(x['status']=='published' for x in pages),'reviewed_count':sum(x['status']=='reviewed' for x in pages),'pending_count':sum(x['status']=='ocr_pending_review' for x in pages),'rejected_count':sum(x['status']=='rejected' for x in pages),'relation_count':len(relations)}}
    stem=f"{a.subject.lower()}-{sanitize_name(a.book_title)}"+(f'-ch{a.chapter_number}' if a.chapter_number else '')
    path=layout['indexes']/'exercise_coverage'/f'{stem}.json'; save_json(path,payload,ignored_compare_keys=())
    payload['report_path']=str(path)
    if a.format=='json': print(json.dumps(payload,ensure_ascii=False,indent=2))
    return 0
if __name__=='__main__': raise SystemExit(main())
