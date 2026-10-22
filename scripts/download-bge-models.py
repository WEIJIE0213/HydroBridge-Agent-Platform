"""Download pinned official BGE files, resume partials and verify repository hashes."""
import hashlib
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
REVISIONS = {
    "BAAI/bge-m3": "5617a9f61b028005a4858fdac845db406aefb181",
    "BAAI/bge-reranker-v2-m3": "953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e",
}
NAMES = {"config.json", "pytorch_model.bin", "model.safetensors", "tokenizer.json",
         "tokenizer_config.json", "special_tokens_map.json", "sentencepiece.bpe.model",
         "README.md"}

def sha256(path):
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for block in iter(lambda: handle.read(8 * 1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()

def fetch(repo, revision, item):
    destination = ROOT / 'models' / repo.split('/')[1] / item['rfilename']
    destination.parent.mkdir(parents=True, exist_ok=True)
    expected = item.get('lfs', {}).get('sha256')
    if destination.exists() and (not expected or sha256(destination) == expected):
        return {'file': str(destination.relative_to(ROOT)), 'sha256': sha256(destination)}
    partial = destination.with_name(destination.name + '.partial')
    proxy = os.environ.get('MODEL_DOWNLOAD_PROXY')
    with httpx.Client(proxy=proxy, timeout=httpx.Timeout(120, connect=45), follow_redirects=True,
                      trust_env=False) as client:
        for attempt in range(10):
            offset = partial.stat().st_size if partial.exists() else 0
            if offset == item['size']:
                break
            try:
                url = f'https://huggingface.co/{repo}/resolve/{revision}/{item["rfilename"]}'
                with client.stream('GET', url, headers={'Range': f'bytes={offset}-'} if offset else {}) as response:
                    response.raise_for_status()
                    if offset and response.status_code != 206:
                        raise RuntimeError('Server did not honor resume range')
                    if offset and not response.headers.get('content-range', '').startswith(f'bytes {offset}-'):
                        raise RuntimeError('Resume offset mismatch')
                    written = offset
                    announced = offset // (128 * 1024 * 1024)
                    with partial.open('ab' if offset else 'wb') as output:
                        for data in response.iter_bytes(1024 * 1024):
                            output.write(data)
                            written += len(data)
                            if written // (128 * 1024 * 1024) > announced:
                                announced = written // (128 * 1024 * 1024)
                                print(repo, item['rfilename'], f'{written / item["size"]:.0%}', flush=True)
                break
            except Exception as exc:
                print(repo, item['rfilename'], 'retry', attempt + 1, type(exc).__name__, flush=True)
                if attempt == 9:
                    raise
                time.sleep(min(10, attempt + 1))
    if partial.stat().st_size != item['size']:
        raise ValueError('Downloaded size mismatch: ' + str(destination))
    digest = sha256(partial)
    if expected and digest != expected:
        raise ValueError('Downloaded hash mismatch: ' + str(destination))
    partial.replace(destination)
    print('verified', repo, item['rfilename'], flush=True)
    return {'file': str(destination.relative_to(ROOT)), 'sha256': digest}

def main():
    manifest = {'repositories': []}
    with httpx.Client(proxy=os.environ.get('MODEL_DOWNLOAD_PROXY'), timeout=60, trust_env=False) as client:
        for repo, revision in REVISIONS.items():
            response = client.get(f'https://huggingface.co/api/models/{repo}/revision/{revision}', params={'blobs':'true'})
            response.raise_for_status()
            metadata = response.json()
            assert metadata['sha'] == revision
            selected = [item for item in metadata['siblings'] if item['rfilename'] in NAMES]
            with ThreadPoolExecutor(max_workers=3) as pool:
                files = list(pool.map(lambda item: fetch(repo, revision, item), selected))
            manifest['repositories'].append({'repo': repo, 'revision': revision, 'files': files})
            (ROOT / 'models/manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')

if __name__ == '__main__':
    main()
