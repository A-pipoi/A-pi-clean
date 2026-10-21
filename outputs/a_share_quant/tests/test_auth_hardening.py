"""Real transport negative tests plus persistent/concurrent security controls."""
import base64
import json
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor
from http.client import HTTPConnection
from unittest.mock import patch

from quant.api.auth_routes import client_ip, session_cookie
from quant.api.http_security import allowed_request
from quant.api.login_limits import LoginLimits
from quant.api.two_factor import totp
from tests.conftest import request


def test_parallel_reservations_survive_restart(tmp_path):
    path = tmp_path / 'security.sqlite3'
    limits = LoginLimits(path)
    with ThreadPoolExecutor(max_workers=20) as pool:
        results = list(pool.map(lambda index: LoginLimits(path).reserve('alice', 'same-ip'), range(20)))
    accepted = [identity for identity, delay in results if identity]
    assert len(accepted) == 5
    for identity in accepted:
        limits.finish(identity, successful=False)
    assert LoginLimits(path).locked_seconds('alice', 'same-ip') > 0
    assert LoginLimits(path).locked_seconds('alice', 'fresh-ip') == 0


def test_fifth_failure_lock_is_full_fifteen_minutes(tmp_path):
    limits = LoginLimits(tmp_path / 'security.sqlite3')
    with patch('quant.api.login_limits.time.time', return_value=1000):
        for index in range(4):
            limits.failed('alice', 'same-ip')
    with patch('quant.api.login_limits.time.time', return_value=1600):
        limits.failed('alice', 'same-ip')
    # First failures have aged out, but the fifth-failure lock is still active.
    with patch('quant.api.login_limits.time.time', return_value=2000):
        assert LoginLimits(limits.path).locked_seconds('alice', 'same-ip') == 500
    with patch('quant.api.login_limits.time.time', return_value=2501):
        assert limits.locked_seconds('alice', 'same-ip') == 0


def test_forwarded_ip_requires_explicit_trust(monkeypatch):
    monkeypatch.delenv('A_PI_TRUSTED_PROXIES', raising=False)
    assert client_ip('127.0.0.1', '203.0.113.2') == '127.0.0.1'
    monkeypatch.setenv('A_PI_TRUSTED_PROXIES', '127.0.0.1')
    assert client_ip('127.0.0.1', '198.51.100.8, 203.0.113.2') == '203.0.113.2'
    assert client_ip('203.0.113.3', '203.0.113.2') == '203.0.113.3'


def test_local_and_public_cookie_boundaries(monkeypatch):
    monkeypatch.setenv('A_PI_PUBLIC_ORIGIN', 'https://quant.example.test')
    monkeypatch.setenv('A_PI_TRUSTED_PROXIES', '127.0.0.1')
    assert allowed_request('127.0.0.1:8000', 'http://127.0.0.1:8000', '127.0.0.1', None) == (True, False)
    assert allowed_request('127.0.0.1:8000', 'https://127.0.0.1:8000', '127.0.0.1', None, direct_tls_connection=True) == (True, True)
    assert allowed_request('127.0.0.1:8000', 'http://127.0.0.1:8000', '127.0.0.1', None, direct_tls_connection=True) == (False, True)
    assert allowed_request('quant.example.test', 'https://quant.example.test', '127.0.0.1', 'https') == (True, True)
    for host, origin, peer, proto in [('evil.test',None,'127.0.0.1',None), ('quant.example.test',None,'203.0.113.1','https'), ('quant.example.test','https://evil.test','127.0.0.1','https'), ('quant.example.test',None,'127.0.0.1','http')]:
        assert not allowed_request(host, origin, peer, proto)[0]
    assert 'Secure' in session_cookie('test-only', secure=True)
    assert 'Secure' not in session_cookie('test-only', secure=False)


def test_login_csrf_and_dns_rebinding_rejected(http_api):
    for headers in ({}, {'Content-Type':'text/plain','X-A-Pi-Request':'1'}, {'Content-Type':'application/json','X-A-Pi-Request':'1','Origin':'https://evil.test'}):
        connection = HTTPConnection('127.0.0.1', http_api)
        connection.request('POST', '/api/auth/login', json.dumps({'username':'alice','password':'alice-long-password'}), headers)
        response = connection.getresponse()
        assert response.status == 403
        assert response.getheader('X-Frame-Options') == 'DENY'
        response.read()
        connection.close()
    assert request(http_api,'GET','/api/health',headers={'Host':'evil.test'})[0] == 403


def test_idle_session_requires_real_activity(auth, http_api):
    user = auth.store.get_by_username('alice')
    token = auth.issue_token(user)
    claims = auth.verify_token(token)
    headers = {'Cookie':'a_pi_session=' + token}
    before = time.time() - 120
    with sqlite3.connect(auth.store.path) as db:
        db.execute('UPDATE sessions SET last_seen=? WHERE jti=?', (before, claims.jti))
    assert request(http_api,'GET','/api/auth/session',headers=headers)[0] == 200
    with sqlite3.connect(auth.store.path) as db:
        assert db.execute('SELECT last_seen FROM sessions WHERE jti=?',(claims.jti,)).fetchone()[0] == before
    headers['X-A-Pi-Activity'] = '1'
    assert request(http_api,'GET','/api/auth/session',headers=headers)[0] == 200
    with sqlite3.connect(auth.store.path) as db:
        assert db.execute('SELECT last_seen FROM sessions WHERE jti=?',(claims.jti,)).fetchone()[0] > before
        db.execute('UPDATE sessions SET last_seen=? WHERE jti=?', (time.time()-1801, claims.jti))
    assert request(http_api,'GET','/api/auth/session',headers=headers)[0] == 401


def test_admin_http_permissions_and_reauthentication(auth, http_api):
    alice = auth.store.get_by_username('alice')
    bob = auth.store.get_by_username('bob')
    admin = {'Cookie':'a_pi_session='+auth.issue_token(alice),'X-A-Pi-Request':'1'}
    researcher = {'Cookie':'a_pi_session='+auth.issue_token(bob),'X-A-Pi-Request':'1'}
    assert request(http_api,'GET','/api/admin/users',headers=researcher)[0] == 403
    assert request(http_api,'GET','/api/admin/users',headers=admin)[0] == 200
    body = {'username':'charlie','password':'charlie-test-password','role':'researcher','current_password':'wrong-password'}
    assert request(http_api,'POST','/api/admin/users',body,admin)[0] == 201
    body['username'] = 'david'
    body['current_password'] = 'alice-long-password'
    assert request(http_api,'POST','/api/admin/users',body,admin)[0] == 201
    assert request(http_api,'POST','/api/admin/users/action',{'username':'alice','action':'disable','current_password':'alice-long-password'},admin)[0] == 409
    sessions = request(http_api,'GET','/api/auth/sessions',headers=researcher)[2]['sessions']
    assert all(item['jti'] != auth.verify_token(admin['Cookie'].split('=',1)[1]).jti for item in sessions)
    assert request(http_api,'POST','/api/auth/revoke-session',{'jti':sessions[0]['jti']},admin)[0] == 404


def test_totp_reference_vector_and_recovery_atomicity(auth):
    secret = base64.b32encode(b'12345678901234567890').decode().rstrip('=')
    assert totp(secret, 1) == '287082'  # 6-digit RFC reference at 59 seconds
    alice = auth.store.get_by_username('alice')
    with patch('quant.api.two_factor.time.time',return_value=3000):
        setup = auth.second_factor.setup(alice.id,'alice')
        code = totp(setup['secret'], 100)
        recovery = auth.second_factor.enable(alice.id,code)
        assert recovery and len(recovery)==8
        assert not auth.second_factor.verify(alice.id,code)
    with patch('quant.api.two_factor.time.time',return_value=3030):
        assert auth.second_factor.verify(alice.id,totp(setup['secret'],101))
        assert not auth.second_factor.verify(alice.id,totp(setup['secret'],101))
    with ThreadPoolExecutor(max_workers=8) as pool:
        uses = list(pool.map(lambda _: auth.second_factor.verify(alice.id,recovery[0]),range(8)))
    assert sum(uses)==1


def test_totp_login_and_enrollment_http(auth,http_api):
    alice = auth.store.get_by_username('alice')
    token = auth.issue_token(alice)
    headers = {'Cookie':'a_pi_session='+token,'X-A-Pi-Request':'1'}
    setup = request(http_api,'POST','/api/auth/2fa/setup',{},headers)[2]
    code = totp(setup['secret'],int(time.time())//30)
    result = request(http_api,'POST','/api/auth/2fa/enable',{'otp':code},headers)
    assert result[0]==200 and len(result[2]['recovery_codes'])==8
    assert auth.verify_token(token) is not None
    login = {'username':'alice','password':'alice-long-password'}
    assert request(http_api,'POST','/api/auth/login',login)[2]['error']=='totp_required'
    login['otp'] = result[2]['recovery_codes'][0]
    assert request(http_api,'POST','/api/auth/login',login)[0]==200
    assert request(http_api,'POST','/api/auth/login',login)[2]['error']=='totp_invalid'
    disabled = request(http_api,'POST','/api/auth/2fa/disable',{},headers)
    assert disabled[0] == 204
    assert auth.verify_token(token) is not None
    login.pop('otp', None)
    assert request(http_api,'POST','/api/auth/login',login)[0] == 200
