"""Offline invitation registration acceptance: isolated SQLite, no market/network."""
import hashlib
import json
import logging
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import pytest

from quant.api.account_management import handle_account_request
from quant.api.auth import Auth
from quant.api.auth_routes import auth_response
from quant.api.auth_store import AuthStore
from quant.api.http_handler import HttpApiHandler
from quant.api.user_scope import UserScopedStores


@pytest.fixture
def auth(tmp_path):
    store = AuthStore(tmp_path / 'auth' / 'users.sqlite3')
    store.create_user('admin', 'a-unique-admin-password')
    store.create_user('researcher', 'a-unique-researcher-password')
    return Auth(store, tmp_path / 'auth' / 'secret.key')


def claims(auth, username='admin'):
    return auth.verify_token(auth.issue_token(auth.store.get_by_username(username)))


def admin_call(auth, path='/api/admin/invites', payload=None, method='POST', identity=None):
    return handle_account_request(auth, path, payload if payload is not None else {'current_password': 'a-unique-admin-password'}, identity or claims(auth), 'admin-ip', 'offline-test', method)


def code_hash(code):
    return hashlib.sha256(code.encode()).hexdigest()


def register(auth, code, username='newresearcher', password='a-unique-personal-password', ip='reg-ip'):
    return auth_response(auth, '/api/auth/register', {'username':username,'password':password,'invite_code':code}, None, ip, logging.getLogger('test-registration'), 'offline-test')


def test_single_use_fixed_role_no_cookie_no_forced_password_change(auth):
    status, body, _ = admin_call(auth)
    assert status == 201 and len(body['code']) == 16
    assert register(auth, body['code']) == (201, {'ok':True,'username':'newresearcher'}, {})
    user = auth.store.get_by_username('newresearcher')
    assert user.role == 'researcher' and not user.disabled and not user.must_change_password
    assert register(auth, body['code'], 'another')[1]['error'] == 'invite_invalid'
    with sqlite3.connect(auth.store.path) as db:
        assert db.execute('SELECT COUNT(*) FROM sessions WHERE user_id=?', (user.id,)).fetchone()[0] == 0
        stored_hash, used_by = db.execute('SELECT code_hash,used_by FROM invites').fetchone()
        assert stored_hash == code_hash(body['code']) and used_by == user.id


@pytest.mark.parametrize('state', ['missing','expired','used','revoked','malformed'])
def test_invalid_invitation_errors_are_indistinguishable(auth, state):
    code, _ = auth.store.create_invite(auth.store.get_by_username('admin').id)
    if state == 'expired':
        with sqlite3.connect(auth.store.path) as db:
            db.execute('UPDATE invites SET expires_at=?', (time.time()-1,))
    elif state == 'used':
        assert register(auth, code, 'firstuser')[0] == 201
    elif state == 'revoked':
        assert auth.store.revoke_invite(code_hash(code))
    elif state == 'missing':
        code = '0' * 16
    elif state == 'malformed':
        code = 'not-a-code'
    assert register(auth, code)[0:2] == (403, {'ok':False,'error':'invite_invalid'})


def test_username_conflict_rolls_back_invite_consumption(auth):
    code, _ = auth.store.create_invite(auth.store.get_by_username('admin').id)
    assert register(auth, code, 'researcher')[0:2] == (409, {'ok':False,'error':'user_exists'})
    with sqlite3.connect(auth.store.path) as db:
        assert db.execute('SELECT used_at,used_by FROM invites').fetchone() == (None,None)
    assert register(auth, code, 'newresearcher')[0] == 201


def test_parallel_consumption_creates_exactly_one_user(auth):
    code, _ = auth.store.create_invite(auth.store.get_by_username('admin').id)
    with ThreadPoolExecutor(max_workers=2) as pool:
        statuses = list(pool.map(lambda index: register(auth, code, f'parallel{index}', ip=f'ip{index}')[0], range(2)))
    assert sorted(statuses) == [201,403]


def test_registration_limit_is_ip_scoped_and_persistent(auth):
    for _ in range(5):
        assert register(auth,'0'*16)[0] == 403
    status, body, headers = register(auth,'0'*16)
    assert status == 429 and body['error'] == 'too_many_attempts' and int(headers['Retry-After']) > 0
    restarted = Auth(auth.store, auth.store.path.parent / 'secret.key')
    assert register(restarted,'0'*16)[0] == 429
    assert register(restarted,'0'*16,ip='another-ip')[0] == 403
    with sqlite3.connect(auth.store.path) as db:
        keys = {row[0] for row in db.execute('SELECT key FROM login_attempts')}
    assert 'ip:reg-ip' in keys and not any(key.startswith('user:') for key in keys)
    assert 'pair:' + hashlib.sha256(b'reg:reg-ip\0reg-ip').hexdigest() in keys
    assert 'pair:' + hashlib.sha256(b'register\0reg-ip').hexdigest() not in keys


def test_invalid_password_counts_as_failure_without_consuming_code(auth):
    code, _ = auth.store.create_invite(auth.store.get_by_username('admin').id)
    for _ in range(5):
        assert register(auth, code, password='short')[1]['error'] == 'password_length'
    assert register(auth,code)[0] == 429
    assert register(auth,code,ip='fresh-ip')[0] == 201


def test_admin_permission_no_step_up_limit_and_metadata(auth):
    anon = handle_account_request(auth,'/api/admin/invites',{},None,'ip','ua','POST')
    assert anon[0] == 401
    assert admin_call(auth,identity=claims(auth,'researcher'))[0] == 403
    # User explicitly chose no step-up for authenticated sensitive actions.
    # Keep anonymity/role rejection, quota and single-consumption assertions.
    created = admin_call(auth, payload={})
    assert created[0] == 201
    assert admin_call(auth, '/api/admin/invites/revoke',
                      {'code_hash': code_hash(created[1]['code'])})[0] == 200
    actor = auth.store.get_by_username('admin')
    codes = [auth.store.create_invite(actor.id)[0] for _ in range(20)]
    assert admin_call(auth)[0:2] == (409,{'ok':False,'error':'invite_limit'})
    result = admin_call(auth,'/api/admin/invites/list')[1]['invites']
    assert len(result) == 20 and all(len(row['code_hash']) == 8 and 'code' not in row for row in result)
    assert not any(code in json.dumps(result) for code in codes)
    assert admin_call(auth,'/api/admin/invites/revoke',{'code_hash':code_hash(codes[0])[:8]})[0] == 200
    assert register(auth,codes[0])[1]['error'] == 'invite_invalid'
    assert admin_call(auth)[0] == 201
    assert admin_call(auth,method='GET')[0] == 200


def test_ambiguous_prefix_revoke_cannot_delete_any_code(auth):
    actor = auth.store.get_by_username('admin')
    with sqlite3.connect(auth.store.path) as db:
        db.executemany('INSERT INTO invites(code_hash,created_by,created_at,expires_at) VALUES(?,?,?,?)', [('abcdef12'+suffix*56,actor.id,time.time(),time.time()+100) for suffix in ('0','1')])
    status, body, _ = admin_call(auth,'/api/admin/invites/revoke',{'current_password':'a-unique-admin-password','code_hash':'abcdef12'})
    assert status == 409 and body['error'] == 'invite_ambiguous'
    assert len(auth.store.list_invites()) == 2


def test_persistent_audit_never_contains_password_or_plain_invite(auth):
    _, body, _ = admin_call(auth)
    code = body['code']
    assert register(auth,code)[0] == 201
    assert register(auth,code,'another')[0] == 403
    _, unused, _ = admin_call(auth)
    admin_call(auth,'/api/admin/invites/revoke',{'current_password':'a-unique-admin-password','code_hash':code_hash(unused['code'])})
    with sqlite3.connect(auth.store.path) as db:
        rows = db.execute('SELECT action,success,target,ip,user_agent FROM security_audit').fetchall()
    assert ('register',1) in [row[:2] for row in rows]
    assert ('register',0) in [row[:2] for row in rows]
    assert ('admin.invite_create',1) in [row[:2] for row in rows]
    assert ('admin.invite_revoke',1) in [row[:2] for row in rows]
    assert code not in repr(rows) and unused['code'] not in repr(rows)
    assert 'a-unique-admin-password' not in repr(rows) and 'a-unique-personal-password' not in repr(rows)


@pytest.mark.parametrize('headers', [{},{'X-A-Pi-Request':'1','Content-Type':'text/plain'},{'Content-Type':'application/json'}])
def test_registration_csrf_rejected_before_identity_or_body(headers):
    handler = object.__new__(HttpApiHandler)
    handler.path = '/api/auth/register'
    handler.headers = headers
    results = []
    with patch.object(handler,'_request_boundary',return_value=True), patch.object(handler,'_send_json',side_effect=lambda *args: results.append(args)), patch.object(handler,'_authorize',side_effect=AssertionError('Anonymous registration must not authorize')):
        handler.do_POST()
    assert results[0][:2] == (403,{'ok':False,'error':'login_csrf_rejected'})


def test_registration_good_csrf_skips_session_gate():
    handler = object.__new__(HttpApiHandler)
    handler.path = '/api/auth/register'
    handler.headers = {'X-A-Pi-Request':'1','Content-Type':'application/json; charset=utf-8'}
    with patch.object(handler,'_request_boundary',return_value=True), patch.object(handler,'_auth_endpoint') as dispatch, patch.object(handler,'_authorize',side_effect=AssertionError('Session gate must be skipped')):
        handler.do_POST()
    dispatch.assert_called_once_with('/api/auth/register')


def test_registered_user_gets_separate_empty_stores(auth,tmp_path):
    code, _ = auth.store.create_invite(auth.store.get_by_username('admin').id)
    assert register(auth,code)[0] == 201
    stores = UserScopedStores(tmp_path / 'business')
    try:
        owner = stores.get('admin')
        registered = stores.get('newresearcher')
        owner.factor_store.register('owner private factor','momentum',19)
        owner.paper_store.create_order('000001.SZ','BUY',100,order_id='private-order')
        assert owner.paper_store.path != registered.paper_store.path
        assert registered.paper_store.state()['orders'] == []
        assert not any(row.name == 'owner private factor' for row in registered.factor_store.definitions())
        assert owner.ledger.path != registered.ledger.path
        assert registered.ledger.status('baseline')['remaining'] == 20
    finally:
        stores.close()
