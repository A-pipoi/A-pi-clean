import gzip
import hashlib
import json
import threading
import urllib.request
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

import pytest

from quant.api.frontend_release import (
    activate_frontend_release,
    validate_frontend_release,
)
from quant.api.http_handler import HttpApiHandler


def test_release_integrity_and_missing_or_changed_assets(tmp_path):
    release = 'a' * 20
    prefix = f'/releases/{release}/assets/'
    assets = tmp_path / 'releases' / release / 'assets'
    assets.mkdir(parents=True)
    (assets / 'app.js').write_bytes(b'console.log(1)')
    url = prefix + 'app.js'
    manifest = {'release': release, 'entries': {'app': url}, 'files': {url: {'sha256': hashlib.sha256(b'console.log(1)').hexdigest()}}}
    (assets.parent / 'manifest.json').write_text(json.dumps(manifest))
    (tmp_path / 'index.html').write_text(f'<meta name="a-pi-release" content="{release}"/><script src="{url}"></script>')
    assert validate_frontend_release(tmp_path) == release
    (assets / 'app.js').write_bytes(b'changed')
    with pytest.raises(ValueError, match='integrity'):
        validate_frontend_release(tmp_path)


def test_immutable_assets_gzip_and_html_revalidation(tmp_path):
    class StaticHandler(HttpApiHandler):
        def do_GET(self):
            SimpleHTTPRequestHandler.do_GET(self)

    release = 'b' * 20
    assets = tmp_path / 'releases' / release / 'assets'
    assets.mkdir(parents=True)
    (assets / 'app.js').write_bytes(b'console.log(1)')
    (assets / 'app.js.gz').write_bytes(gzip.compress(b'console.log(1)'))
    (tmp_path / 'index.html').write_text('<p>test</p>')
    server = ThreadingHTTPServer(('127.0.0.1', 0), partial(StaticHandler, directory=str(tmp_path)))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f'http://127.0.0.1:{server.server_port}'
    try:
        with urllib.request.urlopen(base + '/') as response:
            assert response.headers['Cache-Control'] == 'no-cache'
            assert "require-trusted-types-for 'script'" in response.headers['Content-Security-Policy']
            assert "trusted-types dompurify" in response.headers['Content-Security-Policy']
        request = urllib.request.Request(base + f'/releases/{release}/assets/app.js', headers={'Accept-Encoding': 'gzip'})
        with urllib.request.urlopen(request) as response:
            assert response.headers['Cache-Control'].endswith('immutable')
            assert response.headers['Content-Encoding'] == 'gzip'
            assert response.headers['Vary'] == 'Accept-Encoding'
            assert gzip.decompress(response.read()) == b'console.log(1)'
        with urllib.request.urlopen(base + f'/releases/{release}/assets/app.js') as response:
            assert not response.headers.get('Content-Encoding')
            assert response.read() == b'console.log(1)'
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_explicit_release_activation_retains_old_assets_and_fails_closed(tmp_path):
    def release(key, content):
        identity = key * 20
        prefix = f'/releases/{identity}/assets/'
        assets = tmp_path / 'releases' / identity / 'assets'
        assets.mkdir(parents=True)
        (assets / 'app.js').write_bytes(content)
        manifest = {'release': identity, 'entries': {'app':prefix+'app.js'},
            'files':{prefix+'app.js':{'sha256':hashlib.sha256(content).hexdigest()}}}
        (assets.parent / 'manifest.json').write_text(json.dumps(manifest))
        html = f'<meta name="a-pi-release" content="{identity}"/><script src="{prefix}app.js"></script>'
        (assets.parent / 'index.html').write_text(html)
        return html
    first = release('a', b'first')
    release('b', b'second')
    (tmp_path / 'index.html').write_text(first)
    assert activate_frontend_release(tmp_path, 'b'*20) == 'a'*20
    assert validate_frontend_release(tmp_path) == 'b'*20
    assert activate_frontend_release(tmp_path, 'a'*20) == 'b'*20
    assert validate_frontend_release(tmp_path) == 'a'*20
    with pytest.raises(ValueError, match='Invalid release'):
        activate_frontend_release(tmp_path, '../')
    (tmp_path/'releases'/('b'*20)/'assets'/'app.js').write_bytes(b'tampered')
    with pytest.raises(ValueError, match='integrity'):
        activate_frontend_release(tmp_path, 'b'*20)
    assert (tmp_path/'index.html').read_text() == first
