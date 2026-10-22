"""Join extracted source representations by catalog serial and exact normalized titles."""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
import re
import sys
import uuid
from pathlib import Path

import fitz
from lxml import html
from openpyxl import load_workbook

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
from app.importers.water_data import normalize_document, normalize_title

CACHE = ROOT / 'tmp/corpus'
OUTPUT = ROOT / 'output/corpus'


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str), encoding='utf-8')


def compact(text):
    return re.sub(r'\s+', '', text)


def clean_title(title):
    title = re.sub(r'\s+', '', title).strip('《》“”"')
    return title


def filename_title(filename):
    value = Path(filename).stem
    value = re.sub(r'\.(doc|jpg)$', '', value, flags=re.I).rstrip('·')
    value = re.sub(r'^\d{7,8}\s*[-－]', '', value)
    value = re.sub(r'\s*[-－]?\s*副本$', '', value)
    value = re.sub(r'\s*\(\d+\)$', '', value)
    value = re.sub(r'[-－](?:\d+(?:\.\d+)?分?|无)(?:\.jpg)?\s*$', '', value)
    return clean_title(value)


def sheet_records(item):
    workbook = load_workbook(item['local_path'], read_only=True, data_only=True)
    records = []
    for sheet in workbook:
        current = None
        wide = item['filename'] == '111.xlsx'
        evaluation = '评价' in item['filename']
        title_col, content_col = (3, 9) if wide else (1, 4 if evaluation else 3)
        for row_number, row in enumerate(sheet.values, 1):
            if not any(v is not None and str(v).strip() for v in row):
                continue
            row = list(row) + [None] * 20
            if isinstance(row[0], (int, float)) and 1 <= row[0] <= 10000:
                if current:
                    records.append(current)
                current = {'serial': int(row[0]), 'title': '', 'content': [], 'row_start': row_number,
                           'row_end': row_number, 'sheet': sheet.title, 'evaluation': evaluation}
            if current is None:
                continue
            current['row_end'] = row_number
            if row[title_col]:
                current['title'] += str(row[title_col])
            content = [str(v) for v in row[content_col:16 if wide else 5] if v is not None]
            current['content'].extend(content)
        if current:
            records.append(current)
    workbook.close()
    for record in records:
        record['title'] = clean_title(record['title'])
        record['text'] = '\n'.join(record.pop('content'))
        record['locator'] = f"{record['sheet']}!行{record['row_start']}:{record['row_end']}（序号{record['serial']}）"
    return records


def catalog_pdf(item):
    cache = CACHE / 'catalog-pdf-v2.json'
    if cache.exists():
        return json.loads(cache.read_text(encoding='utf-8'))
    result = collections.defaultdict(list)
    current = None
    with fitz.open(item['local_path']) as document:
        for n, page in enumerate(document, 1):
            markers = []
            for block in page.get_text('blocks', clip=fitz.Rect(30, 0, 65, page.rect.height)):
                x0, y0, x1, y1, text, *_ = block
                text = compact(text)
                if text.isdigit() and 1 <= int(text) <= 3966:
                    serial = int(text)
                    if current is None or serial > current:
                        markers.append((y0-1, serial))
            markers.sort()
            boundaries = [(0, current), *markers, (page.rect.height, None)]
            for (start, serial), (end, _) in zip(boundaries, boundaries[1:]):
                if serial is None:
                    continue
                text = page.get_text(clip=fitz.Rect(230, start, page.rect.width, end)).strip()
                if text:
                    result[str(serial)].append({'text': text, 'locator': f'PDF 第 {n} 页（目录序号 {serial}）', 'page': n, 'method': 'native-column'})
            if markers:
                current = markers[-1][1]
            if n % 1000 == 0:
                print('catalog PDF', n, flush=True)
    save(cache, result)
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--catalog-only', action='store_true')
    args = parser.parse_args()
    originals = json.loads((OUTPUT / 'originals.json').read_text(encoding='utf-8'))
    records, audit = {}, []
    sources = {i['sha256']: dict(i, document_id=str(uuid.uuid5(uuid.NAMESPACE_URL, 'hydrobridge-corpus:'+i['sha256']))) for i in originals}
    def get_record(title):
        title = clean_title(title)
        key = normalize_title(title)
        if not key:
            raise ValueError('Empty achievement title')
        if key not in records:
            records[key] = {'title': title, 'normalized_title': key, 'source_documents': [], 'source_refs': [], 'catalog_ids': [], 'structured_variants': []}
        return records[key]
    def attach(record, source, segments, *, index=True, basis='exact_title'):
        ref = {'sha256': source['sha256'], 'document_id': source['document_id'], 'title': source['filename'], 'locators': [s['locator'] for s in segments], 'mapping_basis': basis}
        record['source_refs'].append(ref)
        # Retain each original and all locators even when its text is identical.
        fingerprint = hashlib.sha256(compact('\n'.join(s['text'] for s in segments)).encode()).hexdigest()
        duplicate = next((d for d in record['source_documents'] if d['text_fingerprint'] == fingerprint), None)
        document = {**ref, 'segments': segments, 'text_fingerprint': fingerprint, 'index': index and not duplicate}
        if duplicate:
            document['duplicate_text_of'] = duplicate['sha256']
        record['source_documents'].append(document)

    catalogs = [i for i in sources.values() if i['extension'] == '.xlsx' and ('成果查询' in i['filename'] or i['filename']=='111.xlsx')]
    catalogs.sort(key=lambda i: (i['filename'] != '水利推广-成果查询20250217.xlsx', i['filename']=='111.xlsx', i['filename']))
    by_serial = {}
    for source in catalogs:
        rows = sheet_records(source)
        audit.append({'source': source['filename'], 'records': len(rows), 'kind': 'catalog'})
        for row in rows:
            if source['filename'] == '水利推广-成果查询20250217.xlsx':
                record = get_record(row['title'])
                by_serial[row['serial']] = record
                record['catalog_ids'].append(row['serial'])
            else:
                record = by_serial.get(row['serial'])
                if record is None:
                    raise ValueError(f'Alternate catalog serial absent: {row["serial"]}')
                if normalize_title(row['title']) != record['normalized_title']:
                    audit.append({'kind': 'catalog_title_variant', 'serial': row['serial'], 'canonical': record['title'], 'variant': row['title'], 'source': source['filename']})
            attach(record, source, [{'text': row['text'], 'locator': row['locator'], 'method': 'xlsx'}], basis='catalog_serial')
    print('Catalog:',len(by_serial),'serials',len(records),'distinct titles',flush=True)
    html_catalogs = [s for s in sources.values() if s['extension'] in {'.htm','.html'} and '水利推广-成果查询' in s['filename']]
    for source in html_catalogs:
        root = html.fromstring(Path(source['local_path']).read_bytes())
        for tr in root.xpath('//table/tr'):
            cells = tr.findall('td')
            if len(cells)!=4 or not cells[0].text_content().strip().isdigit():
                continue
            serial = int(cells[0].text_content().strip())
            record = by_serial[serial]
            if normalize_title(cells[1].text_content()) != record['normalized_title']:
                raise ValueError(f'HTML/XLSX catalog title mismatch at {serial}')
            attach(record, source, [{'text':cells[3].text_content(),'locator':f'HTML 目录第 {serial} 条','method':'html'}], basis='catalog_serial_and_verified_title')
    pdfs = [s for s in sources.values() if s['extension']=='.pdf' and '水利推广-成果查询' in s['filename']]
    for source in pdfs:
        pdf_records = catalog_pdf(source)
        missing = set(by_serial) - {int(i) for i in pdf_records}
        if missing:
            raise ValueError(f'PDF catalog missing serials: {sorted(missing)}')
        for serial, segments in pdf_records.items():
            attach(by_serial[int(serial)], source, segments, basis='catalog_serial_and_pdf_column')

    if not args.catalog_only:
        group_titles = {}
        # Explicit mappings are reviewed from each bundle's application form or research cover.
        map_path = ROOT / 'evaluation' / 'water_corpus_mappings.json'
        mappings = json.loads(map_path.read_text(encoding='utf-8')) if map_path.exists() else {}
        group_titles.update(mappings.get('archive_groups', {}))
        for source in sources.values():
            if source in catalogs or source in pdfs or source in html_catalogs:
                continue
            if source['sha256'] in mappings.get('exclude', {}):
                audit.append({'kind':'excluded_after_inspection','sha256':source['sha256'],'reason':mappings['exclude'][source['sha256']]})
                continue
            path = CACHE / 'extracted' / (source['sha256']+'.json')
            if not path.exists():
                audit.append({'kind':'pending_extraction','sha256':source['sha256'],'filename':source['filename']})
                continue
            data = json.loads(path.read_text(encoding='utf-8'))
            if data.get('status') != 'extracted':
                audit.append({'kind':data.get('status','error'),'sha256':source['sha256'],'filename':source['filename'],'error':data.get('error','')})
                continue
            if source['sha256'] in mappings.get('administrative', {}):
                for title in dict.fromkeys(group_titles.values()):
                    attach(get_record(title), source, data['segments'], index=False, basis='shared_application_checklist')
                continue
            if source['extension'] == '.xlsx':
                for row in sheet_records(source):
                    record = get_record(row['title'])
                    attach(record, source, [{'text':row['text'],'locator':row['locator'],'method':'xlsx'}])
                continue
            obj = data.get('json')
            if isinstance(obj, dict):
                entities = [e for e in obj.get('entities',[]) if e.get('type')=='科技成果']
                title = entities[0]['name'] if entities else obj.get('basicInfo',{}).get('name')
                if not title:
                    audit.append({'kind':'non_achievement_json','sha256':source['sha256']})
                    continue
                record = get_record(title)
                record['structured_variants'].append({'sha256':source['sha256'],'data':obj})
                if entities and 'normalized_fields' not in record:
                    record['normalized_fields'] = normalize_document(obj, source['filename'])
                attach(record, source, data['segments'])
                continue
            title = mappings.get('documents',{}).get(source['sha256']) or group_titles.get(source['group'])
            if not title:
                if source['disposition'] == 'inspect' or source['group'] in group_titles:
                    audit.append({'kind':'needs_title_review','sha256':source['sha256'],'filename':source['filename'],'group':source['group']})
                    continue
                title = filename_title(source['filename'])
            title = mappings.get('title_aliases',{}).get(title, title)
            record = get_record(title)
            attach(record, source, data['segments'], basis='reviewed_mapping' if source['sha256'] in mappings.get('documents',{}) or source['group'] else 'normalized_filename')
    for record in records.values():
        joined = {}
        for document in record['source_documents']:
            target = joined.setdefault(document['sha256'], {**document, 'segments': [], 'index': True})
            for segment in document['segments']:
                if not any(s['text'] == segment['text'] and s['locator'] == segment['locator'] for s in target['segments']):
                    target['segments'].append({**segment, 'index': bool(document['index'])})
        record['source_documents'] = list(joined.values())
    used = {d['sha256'] for r in records.values() for d in r['source_documents']}
    package = {'schema_version':1, 'scope':'用户确认的成果原始材料、成果目录及结构化 JSON，保留原文与来源，排除代码、汇报稿及学习论文',
               'documents':[sources[s] for s in sorted(used)], 'records':list(records.values()), 'audit':audit}
    save(OUTPUT / ('catalog-package.json' if args.catalog_only else 'corpus-package.json'), package)
    summary = {'achievements':len(records),'catalog_serials':len(by_serial),'source_files':len(used),
               'source_links':sum(len(r['source_documents']) for r in records.values()),
               'indexed_segments':sum(s['index'] for r in records.values() for d in r['source_documents'] for s in d['segments']),
               'indexed_characters':sum(len(s['text']) for r in records.values() for d in r['source_documents'] for s in d['segments'] if s['index']),
               'audit':dict(collections.Counter(i['kind'] for i in audit))}
    save(OUTPUT / ('catalog-summary.json' if args.catalog_only else 'assembly-summary.json'),summary)
    print(json.dumps(summary,ensure_ascii=False),flush=True)


if __name__ == '__main__':
    main()
