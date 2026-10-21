"""Validate the exact immutable release referenced by the entry HTML."""
from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path


def validate_frontend_release(root: Path, *, html: str | None = None) -> str:
    if html is None:
        html = (root / 'index.html').read_text(encoding='utf-8')
    marker = re.search(r'name="a-pi-release" content="([0-9a-f]{20})"', html)
    if not marker:
        raise ValueError('Missing compiled frontend release; run npm run verify in a_pi_site')
    release = marker[1]
    manifest = json.loads((root / 'releases' / release / 'manifest.json').read_text(encoding='utf-8'))
    if manifest.get('release') != release:
        raise ValueError('Frontend release identity mismatch')
    prefix = f'/releases/{release}/assets/'
    for url, info in manifest['files'].items():
        if not url.startswith(prefix) or '/' in url[len(prefix):] or '..' in url[len(prefix):]:
            raise ValueError('Unsafe frontend release path')
        data = (root / url.lstrip('/')).read_bytes()
        if hashlib.sha256(data).hexdigest() != info['sha256']:
            raise ValueError(f'Frontend asset integrity mismatch: {url}')
    for url in manifest['entries'].values():
        if url not in manifest['files'] or url not in html:
            raise ValueError('Frontend entry point mismatch')
    return release


def activate_frontend_release(root: Path, release: str) -> str:
    """Hash-validate a saved release and atomically change the HTML entry only.

    No service stop, user-data changes, asset deletion or public-port changes.
    Old immutable assets remain available to already-open client sessions.
    """
    if not re.fullmatch(r'[0-9a-f]{20}', release):
        raise ValueError('Invalid release ID')
    root = root.resolve(strict=True)
    candidate = (root / 'releases' / release / 'index.html').read_text(encoding='utf-8')
    if validate_frontend_release(root, html=candidate) != release:
        raise ValueError('Release entry identity mismatch')
    # Share the same publication lock as the frontend builder.
    lock = root / '.build.lock'
    descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        previous = validate_frontend_release(root)
        current_html = (root / 'index.html').read_text(encoding='utf-8')
        (root / 'releases' / previous / 'index.html').write_text(current_html, encoding='utf-8')
        pending = root / '.index.next'
        pending.write_text(candidate, encoding='utf-8')
        os.replace(pending, root / 'index.html')
        manifest = (root / 'releases' / release / 'manifest.json').read_bytes()
        pending_manifest = root / '.release.next'
        pending_manifest.write_bytes(manifest)
        os.replace(pending_manifest, root / 'release.json')
        return previous
    finally:
        os.close(descriptor)
        lock.unlink()


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='Validate or atomically activate/roll back a compiled frontend release')
    parser.add_argument('--site', type=Path, required=True)
    parser.add_argument('--activate', help='explicit 20-character release ID; omit for read-only validation')
    args = parser.parse_args()
    if args.activate:
        print(f'Activated {args.activate}; previous release: {activate_frontend_release(args.site, args.activate)}')
    else:
        print(f'Validated {validate_frontend_release(args.site)}')
