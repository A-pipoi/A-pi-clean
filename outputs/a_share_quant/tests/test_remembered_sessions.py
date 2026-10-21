"""Remembered-device reads survive idle/restarts; writes need fresh credentials."""
import logging
import sqlite3
from unittest.mock import patch

from quant.api.auth import Auth
from quant.api.auth_routes import auth_response
from quant.api.auth_store import AuthStore
from tests.conftest import request


def login(auth, remember=True):
    status, _, headers = auth_response(auth, '/api/auth/login', {
        'username': 'alice', 'password': 'alice-long-password', 'remember': remember,
    }, None, 'local', logging.getLogger('test'))
    assert status == 200
    return headers['Set-Cookie'].split(';')[0].split('=', 1)[1]


def test_remembered_survives_idle_purge_restart_and_expires(auth):
    token = login(auth)
    claims = auth.verify_token(token)
    assert claims.remembered and claims.exp - claims.iat == 604800
    with patch('time.time', return_value=claims.iat + 86400):
        auth.store.purge_security_records()
        restarted = Auth(AuthStore(auth.store.path), auth.store.path.parent / 'secret.key')
        assert restarted.verify_token(token)
        assert restarted.store.list_sessions(claims.sub, claims.jti)
    with patch('time.time', return_value=claims.exp - 1):
        assert auth.verify_token(token)
    with patch('time.time', return_value=claims.exp):
        assert auth.verify_token(token) is None
        auth.store.purge_security_records()
        assert not auth.store.list_sessions(claims.sub, claims.jti)


def test_nonremembered_still_idle_expires(auth):
    token = login(auth, False)
    claims = auth.verify_token(token)
    assert not claims.remembered
    with patch('time.time', return_value=claims.iat + 1800):
        assert auth.verify_token(token) is None
        auth.store.purge_security_records()
        assert not auth.store.list_sessions(claims.sub, claims.jti)


def test_remember_flag_cannot_disagree_with_registry(auth):
    token = login(auth)
    with sqlite3.connect(auth.store.path) as db:
        db.execute('UPDATE sessions SET remembered=0')
    assert auth.verify_token(token) is None


def test_remembered_revocation_and_password_changes(auth):
    token = login(auth)
    auth.revoke(auth.verify_token(token).jti)
    assert auth.verify_token(token) is None
    token = login(auth)
    auth.store.set_password('alice', 'new-secure-passphrase')
    assert auth.verify_token(token) is None


def test_remembered_session_keeps_business_writes_without_fresh_login(auth, http_api):
    token = login(auth)
    claims = auth.verify_token(token)
    headers = {'Cookie': 'a_pi_session=' + token, 'X-A-Pi-Request': '1'}
    with patch('time.time', return_value=claims.iat + 1800):
        assert request(http_api, 'GET', '/api/auth/session', headers=headers)[0] == 200
        status, _, body = request(
            http_api, 'POST', '/api/paper/orders', {},
            {'Cookie': 'a_pi_session=' + token},
        )
        assert status == 403 and body['error'] == 'csrf_header_required'
        _, _, body = request(http_api, 'POST', '/api/paper/orders', {}, headers)
        assert body.get('error') != 'reauthentication_required'
        assert request(http_api, 'POST', '/api/auth/logout', {}, headers)[0] == 204
