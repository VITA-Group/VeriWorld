#!/usr/bin/env python3
"""Download, resume and verify VeriWorld byte-split archives (Python 3.10+)."""
import argparse
import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

BLOCK = 4 * 1024 * 1024

def sha256(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda: f.read(BLOCK), b''):
            h.update(block)
    return h.hexdigest()

def verified(path, entry):
    return path.is_file() and path.stat().st_size == entry['bytes'] and sha256(path) == entry['sha256']

def validate_entry(entry):
    name = entry['name']
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]+', name):
        raise ValueError('Unsafe archive or part name in manifest')
    if not isinstance(entry['bytes'], int) or entry['bytes'] <= 0:
        raise ValueError('Invalid size in manifest')
    if not re.fullmatch(r'[0-9a-f]{64}', entry['sha256']):
        raise ValueError('Invalid SHA256 in manifest')

def download_part(url, path, entry):
    if path.exists():
        if verified(path, entry):
            print(f'Already verified: {path.name}', flush=True)
            return
        raise ValueError(f'Existing file failed verification: {path}. Move it aside before retrying.')
    partial = path.with_name(path.name + '.partial')
    last_error = None
    for attempt in range(3):
        offset = partial.stat().st_size if partial.exists() else 0
        if offset == entry['bytes']:
            if verified(partial, entry):
                partial.rename(path)
                return
            raise ValueError(f'Checksum mismatch: {partial}. Move it aside before retrying.')
        if offset > entry['bytes']:
            raise ValueError(f'Partial file is larger than expected: {partial}')
        headers = {'User-Agent': 'VeriWorld-release-downloader/1', 'Accept-Encoding': 'identity'}
        if offset:
            headers['Range'] = f'bytes={offset}-'
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=60) as response:
                status = response.status
                if status == 206:
                    content_range = response.headers.get('Content-Range', '')
                    if not content_range.startswith(f'bytes {offset}-'):
                        raise ValueError('Server returned an unexpected byte range')
                    mode = 'ab'
                elif status == 200:
                    offset, mode = 0, 'wb'  # Server ignored Range: restart only our partial file.
                else:
                    raise ValueError(f'Unexpected HTTP status {status}')
                with partial.open(mode) as out:
                    for block in iter(lambda: response.read(BLOCK), b''):
                        offset += len(block)
                        if offset > entry['bytes']:
                            raise ValueError('Download exceeds the size in the manifest')
                        out.write(block)
            if verified(partial, entry):
                partial.rename(path)
                print(f'Verified: {path.name}', flush=True)
                return
            if partial.stat().st_size == entry['bytes']:
                raise ValueError(f'Checksum mismatch: {partial}. Move it aside before retrying.')
            last_error = OSError('Incomplete response')
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as exc:
            last_error = exc
        if attempt < 2:
            time.sleep(2 ** (attempt + 1))
    raise RuntimeError(f'Download incomplete for {path.name}; partial data retained. {last_error}')

def assemble(entry, parts_dir, output):
    target = output / entry['name']
    if target.exists():
        if verified(target, entry):
            print(f'Archive already verified: {target}')
            return target
        raise ValueError(f'Will not overwrite a different archive: {target}')
    temporary = target.with_name(target.name + '.assembling')
    if temporary.exists():
        raise ValueError(f'Previous assembly remains: {temporary}. Move it aside before retrying.')
    total_hash, total_bytes = hashlib.sha256(), 0
    try:
        with temporary.open('xb') as out:
            for part in entry['parts']:
                path = parts_dir / part['name']
                h, count = hashlib.sha256(), 0
                with path.open('rb') as src:
                    for block in iter(lambda: src.read(BLOCK), b''):
                        count += len(block)
                        h.update(block)
                        total_hash.update(block)
                        out.write(block)
                if count != part['bytes'] or h.hexdigest() != part['sha256']:
                    raise ValueError(f'Part checksum mismatch: {path.name}')
                total_bytes += count
        if total_bytes != entry['bytes'] or total_hash.hexdigest() != entry['sha256']:
            raise ValueError('Reassembled archive checksum mismatch')
        temporary.rename(target)
    except BaseException:
        if temporary.exists():
            temporary.unlink()  # Only this invocation's incomplete output, never input parts.
        raise
    print(f'Archive SHA256 verified: {target}\n{entry["sha256"]}', flush=True)
    return target

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('platform', choices=['windows', 'linux'])
    parser.add_argument('--manifest', type=Path, default=Path(__file__).with_name('manifest.json'))
    parser.add_argument('--repo', help='GitHub owner/repository; defaults to manifest value')
    parser.add_argument('--tag', help='Release tag; defaults to manifest value')
    parser.add_argument('--output', type=Path, default=Path('downloads'))
    parser.add_argument('--local-parts', type=Path, help='Offline: use downloaded parts; no network requests')
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text(encoding='utf-8'))
    if manifest['schema_version'] != 1:
        raise ValueError('Unsupported manifest version')
    entry = manifest['archives'][args.platform]
    validate_entry(entry)
    names = set()
    for part in entry['parts']:
        validate_entry(part)
        if part['name'] in names or part['bytes'] >= 2**31:
            raise ValueError('Invalid or duplicated release part')
        names.add(part['name'])
    if sum(p['bytes'] for p in entry['parts']) != entry['bytes']:
        raise ValueError('Manifest sizes do not agree')
    args.output.mkdir(parents=True, exist_ok=True)
    lock = args.output / (entry['name'] + '.lock')
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        raise RuntimeError(f'Another download may be running. Inspect before removing {lock}')
    try:
        os.close(fd)
        parts_dir = args.local_parts or (args.output / 'parts')
        if not args.local_parts:
            parts_dir.mkdir(parents=True, exist_ok=True)
            repo, tag = args.repo or manifest.get('repository'), args.tag or manifest.get('tag')
            if not repo or not tag or not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repo):
                raise ValueError('Supply --repo owner/repo and --tag release-tag')
            base = f'https://github.com/{repo}/releases/download/{urllib.parse.quote(tag, safe="")}/'
            for part in entry['parts']:
                download_part(base + part['name'], parts_dir / part['name'], part)
        assemble(entry, parts_dir, args.output)
    finally:
        lock.unlink()

if __name__ == '__main__':
    try:
        main()
    except (ValueError, RuntimeError, OSError, KeyError) as exc:
        sys.exit(f'Error: {exc}')
