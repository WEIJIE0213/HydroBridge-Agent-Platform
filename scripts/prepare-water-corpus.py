"""Read-only corpus discovery and resumable extraction; original files are never modified.

Run with backend[corpus]. LibreOffice and Tesseract (chi_sim+eng) are required on Windows.
Outputs are an auditable inventory plus hash-addressed originals and extracted text.
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
import os
import re
import shutil
import subprocess
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from xml.etree import ElementTree as ET

import fitz
from lxml import html
from openpyxl import load_workbook

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / 'tmp/corpus'
OUTPUT = ROOT / 'output/corpus'
EXTENSIONS = {'.doc', '.docx', '.pdf', '.json', '.xlsx', '.jpg', '.jpeg', '.png', '.tif', '.tiff', '.htm', '.html'}
IGNORED_DIRS = {'.git', '.venv', 'venv', 'node_modules', '__pycache__', 'site-packages', '.idea'}
SOFFICE = Path(r'C:\Program Files\LibreOffice\program\soffice.exe')
W = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str), encoding='utf-8')


def digest_file(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def classify(path, origin, archive=False):
    name = path.name
    if name.startswith('~$'):
        return 'office_lock'
    if '_评价报告' in name or name.startswith('temp_'):
        return 'generated_report'
    if path.suffix.lower() not in EXTENSIONS:
        return 'not_document'
    if archive:
        return 'include'
    if path.suffix.lower() in {'.htm','.html'}:
        return 'include' if '水利推广-成果查询' in name else 'non_achievement_html'
    if path.suffix.lower() == '.json':
        try:
            data = json.loads(path.read_text(encoding='utf-8-sig'))
            if isinstance(data, dict) and (
                data.get('basicInfo', {}).get('name')
                or any(e.get('type') == '科技成果' for e in data.get('entities', []) if isinstance(e, dict))
            ):
                return 'include'
        except (ValueError, UnicodeError):
            return 'non_achievement_json'
        return 'non_achievement_json'
    if path.suffix.lower() == '.xlsx':
        return 'include' if ('成果' in name or name == '111.xlsx') and 'best_matches' not in name else 'non_achievement_sheet'
    if '水利推广-成果查询' in name:
        return 'include'
    if any(part in origin for part in ['处理数据', '成果数据', '成果评价意见-', '原始申请材料10份', 'data/achievements', 'achievement_docs', '/eval/data/', '/eval/output/']):
        return 'include'
    if name in {'1.docx', '扫描全能王 2025-04-09 14.08.pdf'}:
        return 'inspect'
    return 'code_presentation_or_learning_material'


def zip_name(info):
    if not info.flag_bits & 0x800:
        try:
            return info.filename.encode('cp437').decode('gbk')
        except (UnicodeError, LookupError):
            pass
    return info.filename


def discover(source):
    inventory = []
    originals = {}
    archive_hashes = set()

    def register(path, origin, name=None, archive=False, group=''):
        display = Path(name or path.name)
        reason = classify(display if archive else path, origin, archive)
        entry = {'path': origin, 'filename': display.name, 'disposition': reason, 'group': group}
        inventory.append(entry)
        if reason not in {'include', 'inspect'}:
            return
        digest = digest_file(path)
        entry['sha256'] = digest
        if digest in originals:
            entry['disposition'] = 'duplicate_file'
            originals[digest]['paths'].append(origin)
            if group and not originals[digest]['group']:
                originals[digest]['group'] = group
            return
        target = CACHE / 'originals' / (digest + display.suffix.lower())
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            shutil.copyfile(path, target)
        originals[digest] = {
            'sha256': digest, 'filename': display.name, 'extension': display.suffix.lower(),
            'paths': [origin], 'local_path': str(target), 'size_bytes': target.stat().st_size,
            'group': group, 'disposition': reason,
        }

    def archive(path, origin, group, depth=0):
        if depth > 5:
            raise ValueError('Archive nesting limit exceeded')
        digest = digest_file(path)
        if digest in archive_hashes:
            inventory.append({'path': origin, 'disposition': 'duplicate_archive', 'sha256': digest})
            return
        archive_hashes.add(digest)
        inventory.append({'path': origin, 'disposition': 'expanded_archive', 'sha256': digest})
        if path.suffix.lower() == '.zip':
            with zipfile.ZipFile(path) as z:
                members = [(i, zip_name(i)) for i in z.infolist() if not i.is_dir()]
                for info, name in members:
                    accept_member(z.read, info, name, origin, group, depth)
        else:
            raw = subprocess.run(['tar', '-tf', str(path)], capture_output=True, check=True).stdout
            names = raw.decode('gbk').splitlines()
            for name in names:
                if Path(name).suffix.lower() not in EXTENSIONS | {'.zip', '.rar'}:
                    inventory.append({'path': origin + '!' + name, 'disposition': 'not_document'})
                    continue
                def read_member(member):
                    return subprocess.run(['tar', '-xOf', str(path), member], capture_output=True, check=True).stdout
                accept_member(read_member, name, name, origin, group, depth)

    def accept_member(reader, member, name, origin, group, depth):
        suffix = Path(name).suffix.lower()
        virtual = origin + '!' + name
        if suffix not in EXTENSIONS | {'.zip', '.rar'}:
            inventory.append({'path': virtual, 'disposition': 'not_document'})
            return
        data = reader(member)
        target = CACHE / 'archive-members' / (hashlib.sha256(data).hexdigest() + suffix)
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            target.write_bytes(data)
        if suffix in {'.zip', '.rar'}:
            archive(target, virtual, group, depth + 1)
        else:
            register(target, virtual, name, archive=True, group=group)

    for folder, dirs, files in os.walk(source):
        dirs[:] = sorted(d for d in dirs if d not in IGNORED_DIRS)
        for name in sorted(files):
            path = Path(folder) / name
            origin = path.as_posix()
            if path.suffix.lower() in EXTENSIONS:
                register(path, origin)
            elif path.suffix.lower() in {'.zip', '.rar'} and (
                '原始申请材料10份' in origin and '原始申请材料10份' not in name
                or path.parent == source and name in {'20250804标准json.zip', '20250815标准json.rar', '20250706-测试数据.rar', '处理数据.rar'}
            ):
                archive(path, origin, Path(name).stem)
    save(OUTPUT / 'inventory.json', inventory)
    save(OUTPUT / 'originals.json', list(originals.values()))
    print('Discovery:', collections.Counter(r['disposition'] for r in inventory), 'unique', len(originals), flush=True)
    return list(originals.values())


def ocr_image(image_path, key):
    target = CACHE / 'ocr' / key
    target.parent.mkdir(parents=True, exist_ok=True)
    text_path = target.with_suffix('.txt')
    if not text_path.exists():
        env = dict(os.environ, OMP_THREAD_LIMIT='1')
        subprocess.run(['tesseract', str(image_path), str(target), '-l', 'chi_sim+eng', '--psm', '3'],
                       capture_output=True, check=True, timeout=180, env=env)
    text = text_path.read_text(encoding='utf-8').strip()
    if len(text) < 200:
        alternate = target.parent / (key+'-psm6')
        alt_path = alternate.with_suffix('.txt')
        if not alt_path.exists():
            subprocess.run(['tesseract',str(image_path),str(alternate),'-l','chi_sim+eng','--psm','6'], capture_output=True, check=True, timeout=180, env=dict(os.environ,OMP_THREAD_LIMIT='1'))
        alt_text = alt_path.read_text(encoding='utf-8').strip()
        if len(re.findall(r'[\u4e00-\u9fff]',alt_text)) > len(re.findall(r'[\u4e00-\u9fff]',text)):
            text = alt_text
    return text


def pdf_segments(path, digest):
    segments, pages = [], []
    with fitz.open(path) as pdf:
        for i, page in enumerate(pdf):
            text = page.get_text(sort=True).strip()
            method = 'native'
            if len(re.sub(r'\s', '', text)) < 80 and page.get_images():
                image = CACHE / 'ocr-images' / f'{digest}-{i+1}.png'
                image.parent.mkdir(parents=True, exist_ok=True)
                page.get_pixmap(dpi=200).save(image)
                ocr = ocr_image(image, f'{digest}-{i+1}')
                if len(ocr) > len(text):
                    text, method = ocr, 'tesseract-chi_sim+eng'
            pages.append({'page': i+1, 'characters': len(text), 'method': method,
                          'images': len(page.get_images())})
            if text:
                segments.append({'text': text, 'locator': f'PDF 第 {i+1} 页', 'page': i+1, 'method': method})
    return segments, pages


def docx_segments(path):
    with zipfile.ZipFile(path) as z:
        root = ET.fromstring(z.read('word/document.xml'))
        media = [(name, z.read(name)) for name in z.namelist() if name.startswith('word/media/') and Path(name).suffix.lower() in {'.png','.jpg','.jpeg','.tiff','.tif','.bmp'}]
    body = root.find(W + 'body')
    lines = []
    for element in body:
        if element.tag == W + 'p':
            text = ''.join(n.text or '' for n in element.iter(W+'t'))
        elif element.tag == W + 'tbl':
            text = '\n'.join(' | '.join(''.join(n.text or '' for n in cell.iter(W+'t')) for cell in row.findall(W+'tc')) for row in element.findall(W+'tr'))
        else:
            continue
        if text.strip():
            lines.append(text)
    segments = [{'text': '\n'.join(lines), 'locator': 'DOCX 正文及表格（按原文顺序）', 'method': 'ooxml'}] if lines else []
    for name, data in media:
        pix = fitz.Pixmap(data)
        if pix.width*pix.height >= 150000 and (sum(map(len,lines)) < 1000 or pix.height > pix.width*1.15):
            key = hashlib.sha256(data).hexdigest()
            image = CACHE / 'ocr-images' / (key + Path(name).suffix)
            image.parent.mkdir(parents=True, exist_ok=True)
            image.write_bytes(data)
            text = ocr_image(image, key)
            if text:
                segments.append({'text':text,'locator':f'DOCX 内嵌图像 {name}（OCR）','method':'tesseract-chi_sim+eng'})
    return segments


def sheets(path):
    workbook = load_workbook(path, read_only=True, data_only=True)
    result = []
    for sheet in workbook:
        rows = [(n, list(row)) for n, row in enumerate(sheet.values, 1) if any(v is not None and str(v).strip() for v in row)]
        if not rows:
            continue
        result.append({'name': sheet.title, 'rows': rows})
    workbook.close()
    return result


def extract(item):
    path = Path(item['local_path'])
    target = CACHE / 'extracted' / (item['sha256'] + '.json')
    if target.exists():
        cached = json.loads(target.read_text(encoding='utf-8'))
        word_path = path if item['extension']=='.docx' else CACHE / 'converted' / (item['sha256']+'.docx')
        refresh = False
        if cached.get('parser_version') != 'water-corpus-v2' and word_path.exists() and zipfile.is_zipfile(word_path):
            with zipfile.ZipFile(word_path) as z:
                refresh = any(n.startswith('word/media/') for n in z.namelist())
        if not refresh:
            return cached
    result = dict(item, parser_version='water-corpus-v2', segments=[])
    suffix = item['extension']
    try:
        if suffix == '.xlsx':
            result['sheets'] = sheets(path)
        elif suffix in {'.htm','.html'}:
            root = html.fromstring(path.read_bytes())
            result['segments'] = [{'text':root.text_content(),'locator':'HTML 原始目录','method':'html'}]
        elif suffix == '.json':
            data = json.loads(path.read_text(encoding='utf-8-sig'))
            result['json'] = data
            result['segments'] = [{'text': json.dumps(data, ensure_ascii=False, indent=2), 'locator': 'JSON 全文', 'method': 'json'}]
        elif suffix == '.pdf':
            result['segments'], result['pages'] = pdf_segments(path, item['sha256'])
        elif suffix == '.docx':
            result['segments'] = docx_segments(path)
        elif suffix == '.doc':
            magic = path.read_bytes()[:8]
            if magic.startswith(b'PK'):
                result['segments'] = docx_segments(path)
            elif magic.startswith(b'\xff\xd8') or magic.startswith(b'\x89PNG'):
                result['segments'] = [{'text': ocr_image(path, item['sha256']), 'locator': '图像 OCR（原文件扩展名 .doc）', 'method': 'tesseract-chi_sim+eng'}]
            elif magic.startswith(b'%PDF'):
                result['segments'], result['pages'] = pdf_segments(path, item['sha256'])
            else:
                converted = CACHE / 'converted' / (item['sha256'] + '.docx')
                converted.parent.mkdir(parents=True, exist_ok=True)
                if not converted.exists():
                    profile = (CACHE / 'libreoffice-profile').as_uri()
                    subprocess.run([str(SOFFICE), '-env:UserInstallation='+profile, '--headless', '--convert-to', 'docx', '--outdir', str(converted.parent), str(path)], capture_output=True, check=True, timeout=120)
                result['segments'] = docx_segments(converted)
                for segment in result['segments']:
                    segment['locator'] = 'DOC 正文及表格（LibreOffice 转换）'
        else:
            result['segments'] = [{'text': ocr_image(path, item['sha256']), 'locator': '图像 OCR', 'method': 'tesseract-chi_sim+eng'}]
        result['characters'] = sum(len(s['text']) for s in result['segments'])
        result['status'] = 'extracted' if result['characters'] or result.get('sheets') else 'empty'
    except Exception as exc:
        result['status'] = 'error'
        result['error'] = f'{type(exc).__name__}: {exc}'
    save(target, result)
    print(result['status'], item['filename'], result.get('characters', 0), flush=True)
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', type=Path, default=Path(r'E:\水利部'))
    parser.add_argument('--discover-only', action='store_true')
    args = parser.parse_args()
    originals = discover(args.source)
    if args.discover_only:
        return
    # LibreOffice uses one isolated profile, so old Word conversion is serialized.
    results = [extract(i) for i in originals if i['extension'] == '.doc']
    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = [executor.submit(extract, i) for i in originals if i['extension'] != '.doc']
        for f in as_completed(futures):
            results.append(f.result())
    save(OUTPUT / 'extraction-summary.json', [
        {k: v for k, v in i.items() if k not in {'segments', 'sheets', 'json'}} for i in results
    ])
    print('Extraction:', collections.Counter(i['status'] for i in results), flush=True)


if __name__ == '__main__':
    main()
