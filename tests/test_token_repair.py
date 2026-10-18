"""Credential repair uses completed-device admin HTTP and the bounded HTTPS transport."""
import copy
import json
import time
from uuid import uuid4

import pytest

from test_setup import csrf
from test_setup_organization import IDENTITY, TOKEN, wizard
from test_scope_change import change, completed, store_at
from test_eoffice_sync import EVENT, SNAPSHOT, terminal
from test_setup_models import DIGEST, model_sources
from test_setup_readiness import camera_headers, finish, inference_providers, prepare_models, preview
from test_setup_organization import connect_and_select

REPLACEMENT='b'*64


def repair(client,token=REPLACEMENT):
    return client.post('/setup/token',data={'csrf_token':csrf(client,'/setup/token/manage'),'token':token},
                       headers={'Accept':'application/json'})


def test_replacement_form_is_admin_only_and_never_contains_the_saved_secret(completed):
    app,client,_,calls=completed
    page=client.get('/setup/token/manage')
    assert page.status_code==200
    assert 'Token pengganti' in page.get_data(as_text=True)
    assert TOKEN not in page.get_data(as_text=True)
    assert app.test_client().get('/setup/token/manage').status_code==302
    assert calls==[]


def test_same_device_replacement_sends_original_pending_bytes_and_keeps_key_and_identity(completed,tmp_path):
    app,client,replies,calls=completed
    body=json.dumps(EVENT).encode()
    store=store_at(tmp_path)
    store.enqueue(body)
    store.close()
    key=(tmp_path/'biometric_keys.json').read_bytes()
    result=terminal(EVENT['event_id'])
    replies['/api/kiosk/events']=result
    assert client.post('/setup/token',data={'token':REPLACEMENT}).status_code==302
    response=repair(client)
    assert response.status_code==200
    assert response.json['capture_held'] is False
    assert response.json['recovery_pending'] is False
    assert app.config['EOFFICE_DEVICE_TOKEN']==REPLACEMENT
    assert (tmp_path/'biometric_keys.json').read_bytes()==key
    assert REPLACEMENT not in response.get_data(as_text=True)
    assert REPLACEMENT not in client.get('/setup/token/manage').get_data(as_text=True)
    events=[call for call in calls if call.full_url.endswith('/events')]
    assert len(events)==1 and events[0].data==body
    assert all(call.get_header('Authorization')=='Bearer '+REPLACEMENT for call in calls)
    store=store_at(tmp_path)
    try:
        assert store.pending()==[]
        assert store.terminal(EVENT['event_id'])==result
    finally:
        store.close()


def test_other_device_cannot_reuse_or_overwrite_existing_credentials_queue_and_key(completed,tmp_path):
    app,client,replies,calls=completed
    body=json.dumps(EVENT).encode()
    store=store_at(tmp_path)
    store.enqueue(body)
    store.close()
    key=(tmp_path/'biometric_keys.json').read_bytes()
    encrypted=(tmp_path/'eoffice.sqlite3').read_bytes()
    replies['/api/kiosk/organizations']['device']={**IDENTITY,'device_id':str(uuid4())}
    response=repair(client)
    assert response.status_code==403 and response.json['error']=='device_mismatch'
    assert response.json['capture_held'] is True
    assert app.config['EOFFICE_DEVICE_TOKEN']==TOKEN
    assert (tmp_path/'biometric_keys.json').read_bytes()==key
    assert (tmp_path/'eoffice.sqlite3').read_bytes()==encrypted
    assert len(calls)==1 and calls[0].full_url.endswith('/organizations')


@pytest.mark.parametrize('http_status',[401,403])
def test_rejected_credential_holds_capture_with_safe_recovery_guidance(completed,tmp_path,http_status,caplog):
    from integration.client import RequestFailure
    app,client,replies,_=completed
    store=store_at(tmp_path)
    store.enqueue(json.dumps(EVENT).encode())
    store.close()
    encrypted=(tmp_path/'eoffice.sqlite3').read_bytes()
    replies['/api/kiosk/organizations']=RequestFailure(http_status)
    response=repair(client)
    assert response.status_code==403 and response.json['capture_held'] is True
    assert app.config['EOFFICE_DEVICE_TOKEN']==TOKEN
    assert (tmp_path/'eoffice.sqlite3').read_bytes()==encrypted
    assert 'token' in response.json['message'].lower()
    assert REPLACEMENT not in response.get_data(as_text=True) and REPLACEMENT not in caplog.text
    assert 'capture_hold' in client.get('/setup/readiness').json['blocked']


def test_failed_refresh_restarts_and_retries_saved_same_device_token_without_sending_unverified_snapshot(
        completed,tmp_path):
    from app import create_app
    app,client,replies,calls=completed
    body=json.dumps(EVENT).encode()
    store=store_at(tmp_path)
    store.enqueue(body)
    store.close()
    key=(tmp_path/'biometric_keys.json').read_bytes()
    encrypted=(tmp_path/'eoffice.sqlite3').read_bytes()
    replies['/api/kiosk/snapshot']=b'{"protocol_version":1,'
    response=repair(client)
    assert response.status_code==409
    assert response.json['capture_held'] is True and response.json['recovery_pending'] is True
    assert app.config['EOFFICE_DEVICE_TOKEN']==REPLACEMENT
    assert (tmp_path/'eoffice.sqlite3').read_bytes()==encrypted
    assert not any(call.full_url.endswith('/events') for call in calls)
    restarted=create_app('testing',overrides={'FIRST_SETUP_ENABLED':True,'ATTENDANCE_MODE':'eoffice',
        'SECRET_KEY':None,'SQLALCHEMY_DATABASE_URI':f'sqlite:///{tmp_path}/app.sqlite3',
        'SQLALCHEMY_ENGINE_OPTIONS':{},'WTF_CSRF_ENABLED':True,
        'KIOSK_LOCK_FILE':str(tmp_path/'camera.lock'),'KIOSK_STATE_FILE':str(tmp_path/'camera.json')},
        instance_path=str(tmp_path))
    resumed=restarted.test_client()
    assert resumed.post('/login',data={'csrf_token':csrf(resumed,'/login'),
        'username':'operator','password':'PasswordUji123'}).status_code==302
    assert resumed.get('/setup/token').json['recovery_pending'] is True
    assert 'Coba lagi dengan token tersimpan' in resumed.get('/setup/token/manage').get_data(as_text=True)
    newer=copy.deepcopy(SNAPSHOT)
    newer['snapshot_id']=newer['clock_anchor_id']=str(uuid4())
    replies['/api/kiosk/snapshot']=newer
    result=terminal(EVENT['event_id'])
    replies['/api/kiosk/events']=result
    calls.clear()
    retried=resumed.post('/setup/token/retry',data={'csrf_token':csrf(resumed,'/setup/token/manage')},
                         headers={'Accept':'application/json'})
    try:
        assert retried.status_code==200
        assert retried.json['capture_held'] is False and retried.json['recovery_pending'] is False
        assert (tmp_path/'biometric_keys.json').read_bytes()==key
        assert all(call.get_header('Authorization')=='Bearer '+REPLACEMENT for call in calls)
        assert [call.data for call in calls if call.full_url.endswith('/events')]==[body]
        store=store_at(tmp_path)
        try:
            assert store.terminal(EVENT['event_id'])==result
            assert store.load_snapshot()==newer
        finally:
            store.close()
    finally:
        restarted.extensions['browser_camera'].close()


def test_credential_repair_keeps_uncertain_scope_intent_and_its_capture_hold(completed,tmp_path):
    _,client,replies,_=completed
    intent={'skpd_id':9,'scope_type':'parent','subunit_id':None}
    replies['/api/kiosk/scope']=TimeoutError('sensitive-response')
    assert change(client,skpd_id='9',scope_type='parent').json['pending_scope']==intent
    marker=(tmp_path/'camera.lock.pause').read_bytes()
    response=repair(client)
    assert response.status_code==200
    assert response.json['capture_held'] is True and response.json['scope_change_pending'] is True
    assert client.get('/setup/scope').json['pending_scope']==intent
    assert (tmp_path/'camera.lock.pause').read_bytes()==marker


def test_changed_server_scope_keeps_old_queue_and_context_held_until_restored(completed,tmp_path):
    app,client,replies,calls=completed
    body=json.dumps(EVENT).encode()
    store=store_at(tmp_path)
    store.enqueue(body)
    store.close()
    encrypted=(tmp_path/'eoffice.sqlite3').read_bytes()
    context=app.config['EOFFICE_SNAPSHOT_CONTEXT']
    replies['/api/kiosk/organizations']['device']={**IDENTITY,'scope_type':'parent'}
    response=repair(client)
    assert response.status_code==409 and response.json['error']=='scope_mismatch'
    assert response.json['capture_held'] is True and response.json['scope_mismatch'] is True
    assert app.config['EOFFICE_DEVICE_TOKEN']==REPLACEMENT
    assert app.config['EOFFICE_SCOPE_TYPE']=='all_skpd' and app.config['EOFFICE_SNAPSHOT_CONTEXT']==context
    assert (tmp_path/'eoffice.sqlite3').read_bytes()==encrypted
    assert len(calls)==1 and calls[0].full_url.endswith('/organizations')


@pytest.mark.parametrize('storage',['missing','empty'])
def test_missing_or_empty_old_storage_cannot_be_reinitialized_during_recovery(completed,tmp_path,storage):
    _,client,_,calls=completed
    path=tmp_path/'eoffice.sqlite3'
    if storage=='missing':
        path.unlink()
    else:
        path.write_bytes(b'')
    response=repair(client)
    assert response.status_code==503 and response.json['error']=='storage_unavailable'
    assert response.json['capture_held'] is True
    assert not path.exists() if storage=='missing' else path.read_bytes()==b''
    assert calls==[]


def test_test_mode_cannot_gain_official_access_from_credential_repair(completed):
    app,client,_,calls=completed
    app.config['EOFFICE_TEST_MODE']=True
    response=repair(client)
    assert response.status_code==403 and response.json['error']=='test_mode'
    assert response.json['capture_held'] is False
    assert calls==[]


@pytest.mark.parametrize('http_status',[401,403])
def test_access_revoked_after_identity_check_retains_pending_bytes_for_explicit_retry(completed,tmp_path,http_status):
    from integration.client import RequestFailure
    _,client,replies,_=completed
    body=json.dumps(EVENT).encode()
    store=store_at(tmp_path)
    store.enqueue(body)
    store.close()
    replies['/api/kiosk/events']=RequestFailure(http_status)
    response=repair(client)
    assert response.status_code==409 and response.json['error']=='refresh_unverified'
    assert response.json['capture_held'] is True and response.json['recovery_pending'] is True
    store=store_at(tmp_path)
    try:
        assert store.pending()[0].body==body
        assert store.terminal(EVENT['event_id']) is None
    finally:
        store.close()


def test_revoked_operational_access_blocks_recognition_then_repair_replaces_owner_clock_and_gallery(
        wizard,tmp_path,model_sources,inference_providers):
    from integration.client import RequestFailure
    from test_browser_camera import jpeg
    app,client,replies,calls=wizard
    app.config.update(KIOSK_LOCK_FILE=str(tmp_path/'camera.lock'),KIOSK_STATE_FILE=str(tmp_path/'camera.json'))
    snapshot=copy.deepcopy(SNAPSHOT)
    for model in snapshot['models'].values():
        model['weights_sha256']=DIGEST
    replies['/api/kiosk/snapshot']=snapshot
    connect_and_select(client)
    assert prepare_models(app,client,model_sources).json['ready'] is True
    preview(client)
    body=json.dumps(EVENT).encode()
    store=store_at(tmp_path)
    store.enqueue(body)
    store.close()
    key=(tmp_path/'biometric_keys.json').read_bytes()
    replies['/api/kiosk/snapshot']=RequestFailure(401)
    calls.clear()
    assert finish(client).status_code==200
    old=app.extensions['browser_camera']
    auth=camera_headers(client)
    started=client.post('/kiosk/camera/start',headers=auth)
    assert started.status_code==200
    auth['X-Camera-Owner']=started.json['owner']
    challenge=client.post('/kiosk/camera/challenge',headers=auth).json['challenge']
    assert client.post('/kiosk/camera/frame',headers={**auth,'X-Camera-Challenge':challenge},
                       data=jpeg(),content_type='image/jpeg').status_code==200
    deadline=time.monotonic()+3
    while time.monotonic()<deadline:
        ready=client.get('/setup/readiness').json
        if 'access' in ready['blocked'] and 'clock' in ready['blocked']:
            break
        time.sleep(.02)
    assert ready['ready'] is False and {'access','clock'}<=set(ready['blocked'])
    assert not any(call.full_url.endswith('/events') for call in calls)
    page=client.get('/setup/readiness/manage').get_data(as_text=True)
    assert 'token pengganti perangkat yang sama' in page and '/setup/token/manage' in page
    newer=copy.deepcopy(snapshot)
    newer['snapshot_id']=newer['clock_anchor_id']=str(uuid4())
    replies['/api/kiosk/snapshot']=newer
    replies['/api/kiosk/events']=terminal(EVENT['event_id'])
    response=repair(client)
    try:
        assert response.status_code==200 and response.json['capture_held'] is False
        assert old.closed and app.extensions['browser_camera'] is not old
        assert client.post('/kiosk/camera/challenge',headers=auth).status_code==410
        fresh=camera_headers(client)
        fresh['X-Camera-Owner']=client.post('/kiosk/camera/start',headers=fresh).json['owner']
        challenge=client.post('/kiosk/camera/challenge',headers=fresh).json['challenge']
        assert client.post('/kiosk/camera/frame',headers={**fresh,'X-Camera-Challenge':challenge},
                           data=jpeg(),content_type='image/jpeg').status_code==200
        deadline=time.monotonic()+3
        while time.monotonic()<deadline:
            ready=client.get('/setup/readiness').json
            if ready['ready']:
                break
            time.sleep(.02)
        assert ready['ready'] is True,ready['blocked']
        assert (tmp_path/'biometric_keys.json').read_bytes()==key
        events=[call for call in calls if call.full_url.endswith('/events')]
        assert len(events)==1 and events[0].data==body
        assert events[0].get_header('Authorization')=='Bearer '+REPLACEMENT
    finally:
        app.extensions['browser_camera'].close()
