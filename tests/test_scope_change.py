"""Completed devices change scope only through an active local administrator."""
import copy
import base64
import json
import subprocess
import sys
import time

import pytest

from test_setup import csrf
from test_setup_organization import IDENTITY, TOKEN, connect_and_select, post, wizard
from test_setup_readiness import finish
from test_setup_scope import authorize_subunits
from test_eoffice_sync import EVENT
from test_eoffice_sync import SNAPSHOT, terminal
from test_setup_models import DIGEST, model_sources
from test_setup_readiness import camera_headers, inference_providers, prepare_models, preview


def store_at(tmp_path):
    from integration.store import IntegrationStore
    key=base64.b64decode(json.loads((tmp_path/'biometric_keys.json').read_text())['keys']['1'])
    return IntegrationStore(tmp_path/'eoffice.sqlite3',key)


@pytest.fixture
def completed(wizard,tmp_path):
    app,client,replies,calls=wizard
    app.config.update(KIOSK_LOCK_FILE=str(tmp_path/'camera.lock'),KIOSK_STATE_FILE=str(tmp_path/'camera.json'))
    authorize_subunits(replies)
    connect_and_select(client)
    assert finish(client).status_code==200
    calls.clear()
    yield app,client,replies,calls
    runtime=app.extensions.get('browser_camera')
    if runtime is not None:
        runtime.close()


def change(client,**values):
    return client.post('/setup/scope',data={'csrf_token':csrf(client,'/setup/scope/manage'),**values},
                       headers={'Accept':'application/json'})


def test_completed_scope_management_requires_active_admin_and_csrf(completed):
    app,client,_,calls=completed
    page=client.get('/setup/scope/manage')
    assert page.status_code==200
    assert 'Induk/non-subunit' in page.get_data(as_text=True)
    assert 'Subunit A' in page.get_data(as_text=True)
    assert TOKEN not in page.get_data(as_text=True)
    anonymous=app.test_client()
    assert anonymous.get('/setup/scope/manage').status_code==302
    assert anonymous.get('/setup/scope').status_code==302
    assert client.post('/setup/scope',data={'skpd_id':'9','scope_type':'parent'}).status_code==302
    from extensions import db
    from models import User
    with app.app_context():
        user=db.session.execute(db.select(User).where(User.username=='operator')).scalar_one()
        user.is_active=False
        db.session.commit()
    assert client.get('/setup/scope/manage').status_code in (302,403)
    assert calls==[]


def test_logged_in_non_admin_cannot_change_scope(completed):
    app,client,_,calls=completed
    from extensions import db
    from models import User
    with app.app_context():
        user=db.session.execute(db.select(User).where(User.username=='operator')).scalar_one()
        user.role='viewer'
        db.session.commit()
    assert client.get('/setup/scope/manage').status_code==403
    assert client.get('/setup/scope').status_code==403
    assert calls==[]


def test_pending_queue_refuses_change_with_clear_reason_and_preserves_all_bytes(completed,tmp_path):
    _,client,_,calls=completed
    body=json.dumps(EVENT).encode()
    store=store_at(tmp_path)
    store.enqueue(body)
    store.close()
    key=(tmp_path/'biometric_keys.json').read_bytes()
    previous=(tmp_path/'eoffice.sqlite3').read_bytes()
    result=change(client,skpd_id='9',scope_type='parent')
    assert result.status_code==409
    assert result.json['error']=='queue_pending'
    assert 'antrean' in result.json['message'].lower()
    assert result.json['capture_held'] is False
    assert result.json['scope_type']=='all_skpd'
    assert calls==[]
    assert (tmp_path/'biometric_keys.json').read_bytes()==key
    assert (tmp_path/'eoffice.sqlite3').read_bytes()==previous
    store=store_at(tmp_path)
    try:
        assert store.pending()[0].body==body
    finally:
        store.close()


def test_scope_change_waits_for_native_owner_last_enqueue_before_queue_check(completed,tmp_path):
    _,client,replies,calls=completed
    replies['/api/kiosk/scope']={**IDENTITY,'scope_type':'parent'}
    ready,stop=tmp_path/'native-ready',tmp_path/'native-stop'
    # A separate native camera owner finishes an already acquired frame before releasing.
    script='''
import base64,json,sys,time
from pathlib import Path
from kiosk.lock import SingleInstanceLock
from integration.store import IntegrationStore
root=Path(sys.argv[1]);body=base64.b64decode(sys.argv[2])
with SingleInstanceLock(root/'camera.lock'):
    (root/'native-ready').touch()
    while not (root/'camera.lock.pause').exists() and not (root/'native-stop').exists():
        time.sleep(.005)
    if (root/'camera.lock.pause').exists():
        key=base64.b64decode(json.loads((root/'biometric_keys.json').read_text())['keys']['1'])
        store=IntegrationStore(root/'eoffice.sqlite3',key)
        store.enqueue(body);store.close()
'''
    body=json.dumps(EVENT).encode()
    process=subprocess.Popen([sys.executable,'-c',script,str(tmp_path),base64.b64encode(body).decode()],
                             stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    try:
        deadline=time.monotonic()+5
        while not ready.exists() and time.monotonic()<deadline:
            time.sleep(.01)
        assert ready.exists()
        result=change(client,skpd_id='9',scope_type='parent')
        assert result.status_code==409
        assert result.json['error']=='queue_pending'
        assert result.json['scope_type']=='all_skpd'
        assert calls==[]
    finally:
        stop.touch()
        process.wait(timeout=5)
    assert process.returncode==0,process.stderr.read().decode()
    store=store_at(tmp_path)
    try:
        assert store.pending()[0].body==body
    finally:
        store.close()


def test_partial_new_snapshot_stays_held_across_restart_and_retry_preserves_history(completed,tmp_path):
    from app import create_app
    from uuid import uuid4
    app,client,replies,_=completed
    store=store_at(tmp_path)
    store.enqueue(json.dumps(EVENT).encode())
    history=terminal(EVENT['event_id'])
    store.finish(EVENT['event_id'],history)
    store.close()
    key=(tmp_path/'biometric_keys.json').read_bytes()
    original=(tmp_path/'eoffice.sqlite3').read_bytes()
    scope={'skpd_id':9,'scope_type':'parent','subunit_id':None}
    replies['/api/kiosk/scope']={**IDENTITY,**scope}
    replies['/api/kiosk/snapshot']=b'{"protocol_version":1,'
    result=change(client,skpd_id='9',scope_type='parent')
    assert result.status_code==400
    assert result.json['capture_held'] is True
    assert result.json['pending_scope']==scope
    assert result.json['snapshot']['available'] is False
    assert 'capture_hold' in client.get('/setup/readiness').json['blocked']
    assert (tmp_path/'eoffice.sqlite3').read_bytes()==original
    held=app.test_cli_runner().invoke(args=['kiosk-run','--no-preview'])
    assert held.exit_code!=0 and 'pengaturan' in held.output.lower()
    restarted=create_app('testing',overrides={'FIRST_SETUP_ENABLED':True,'ATTENDANCE_MODE':'eoffice',
        'SECRET_KEY':None,'SQLALCHEMY_DATABASE_URI':f'sqlite:///{tmp_path}/app.sqlite3',
        'SQLALCHEMY_ENGINE_OPTIONS':{},'WTF_CSRF_ENABLED':True,
        'KIOSK_LOCK_FILE':str(tmp_path/'camera.lock'),'KIOSK_STATE_FILE':str(tmp_path/'camera.json')},
        instance_path=str(tmp_path))
    resumed=restarted.test_client()
    assert resumed.post('/login',data={'csrf_token':csrf(resumed,'/login'),
        'username':'operator','password':'PasswordUji123'}).status_code==302
    status=resumed.get('/setup/scope').json
    assert status['capture_held'] is True and status['pending_scope']==scope
    assert status['snapshot']['available'] is False
    new=copy.deepcopy(SNAPSHOT)
    new['snapshot_id']=new['clock_anchor_id']=str(uuid4())
    new['employees']=[]
    replies['/api/kiosk/snapshot']=new
    retry=change(resumed,skpd_id='9',scope_type='parent')
    assert retry.status_code==200
    assert retry.json['capture_held'] is False
    assert retry.json['pending_scope'] is None
    assert retry.json['scope_type']=='parent' and retry.json['snapshot']['empty'] is True
    assert (tmp_path/'biometric_keys.json').read_bytes()==key
    store=store_at(tmp_path)
    try:
        assert store.terminal(EVENT['event_id'])==history
        assert store.load_snapshot()==new
    finally:
        store.close()
        restarted.extensions['browser_camera'].close()


def test_changed_server_permissions_reject_old_choice_without_permanently_holding_capture(completed):
    _,client,replies,calls=completed
    info=copy.deepcopy(replies['/api/kiosk/organizations'])
    info['organizations'][0]['subunits']=[]
    replies['/api/kiosk/organizations']=info
    result=change(client,skpd_id='9',scope_type='subunit',subunit_id='3')
    assert result.status_code==403
    assert result.json['error']=='scope_forbidden'
    assert result.json['capture_held'] is False
    assert result.json['scope_type']=='all_skpd'
    assert not result.json['organizations'][0]['subunits']
    assert len(calls)==1 and calls[0].full_url.endswith('/organizations')


def test_uncertain_remote_selection_preserves_previous_context_and_intended_retry(completed,tmp_path):
    _,client,replies,_=completed
    original=(tmp_path/'eoffice.sqlite3').read_bytes()
    scope={'skpd_id':9,'scope_type':'parent','subunit_id':None}
    replies['/api/kiosk/scope']=TimeoutError('upstream-private-secret')
    result=change(client,skpd_id='9',scope_type='parent')
    assert result.status_code==400
    assert result.json['pending_scope']==scope and result.json['capture_held'] is True
    assert result.json['scope_type']=='all_skpd'
    assert 'upstream-private-secret' not in result.get_data(as_text=True)
    assert (tmp_path/'eoffice.sqlite3').read_bytes()==original
    assert 'value="parent" data-skpd-id="9" selected' in client.get('/setup/scope/manage').get_data(as_text=True)
    replies['/api/kiosk/organizations']['device']={**IDENTITY,**scope}
    replies['/api/kiosk/scope']={**IDENTITY,**scope}
    retry=change(client,skpd_id='9',scope_type='parent')
    assert retry.status_code==200 and retry.json['capture_held'] is False
    assert retry.json['device_id']==IDENTITY['device_id']


def test_device_identity_mismatch_never_takes_over_existing_configuration(completed,tmp_path):
    from uuid import uuid4
    _,client,replies,calls=completed
    original=(tmp_path/'eoffice.sqlite3').read_bytes()
    replies['/api/kiosk/organizations']['device']={**IDENTITY,'device_id':str(uuid4())}
    result=change(client,skpd_id='9',scope_type='parent')
    assert result.status_code==403 and result.json['error']=='device_mismatch'
    assert result.json['capture_held'] is True
    assert result.json['device_id']==IDENTITY['device_id']
    assert result.json['scope_type']=='all_skpd'
    assert (tmp_path/'eoffice.sqlite3').read_bytes()==original
    assert len(calls)==1 and calls[0].full_url.endswith('/organizations')


@pytest.mark.parametrize('values',[
    {'skpd_id':'10','scope_type':'parent'},
    {'skpd_id':'9','scope_type':'all_skpd','subunit_id':'3'},
    {'skpd_id':'9','scope_type':'subunit','subunit_id':'5'},
])
def test_out_of_grant_or_ambiguous_request_leaves_current_capture_configuration_unchanged(completed,values):
    _,client,_,calls=completed
    result=change(client,**values)
    assert result.status_code==400
    assert result.json['capture_held'] is False
    assert result.json['scope_type']=='all_skpd'
    assert result.json['pending_scope'] is None
    assert calls==[]


def test_timed_out_native_drain_can_resume_unchanged_scope_to_send_its_last_queue(completed,tmp_path):
    from kiosk.lock import SingleInstanceLock
    _,client,_,calls=completed
    with SingleInstanceLock(tmp_path/'camera.lock'):
        result=change(client,skpd_id='9',scope_type='parent')
    assert result.status_code==409 and result.json['error']=='camera_draining'
    assert result.json['capture_held'] is True and result.json['pending_scope'] is None
    store=store_at(tmp_path)
    store.enqueue(json.dumps(EVENT).encode())
    store.close()
    retry=change(client,skpd_id='9',scope_type='parent')
    assert retry.status_code==409 and retry.json['error']=='queue_pending'
    assert retry.json['capture_held'] is False
    assert retry.json['scope_type']=='all_skpd'
    assert calls==[]


def test_missing_existing_gallery_and_queue_is_not_reinitialized_to_claim_empty(completed,tmp_path):
    _,client,replies,calls=completed
    (tmp_path/'eoffice.sqlite3').unlink()
    replies['/api/kiosk/scope']={**IDENTITY,'scope_type':'parent'}
    result=change(client,skpd_id='9',scope_type='parent')
    assert result.status_code==503 and result.json['error']=='storage_unavailable'
    assert result.json['capture_held'] is True
    assert not (tmp_path/'eoffice.sqlite3').exists()
    assert calls==[]


def test_browser_scope_handoff_blocks_old_frames_and_reanchors_a_fresh_runtime(
        wizard,tmp_path,model_sources,inference_providers,monkeypatch):
    from threading import Event, Thread
    from urllib.parse import parse_qs, urlsplit
    from urllib.request import OpenerDirector
    from uuid import uuid4
    from test_browser_camera import jpeg
    app,client,replies,calls=wizard
    app.config.update(KIOSK_LOCK_FILE=str(tmp_path/'camera.lock'),KIOSK_STATE_FILE=str(tmp_path/'camera.json'))
    authorize_subunits(replies)
    snapshot=copy.deepcopy(SNAPSHOT)
    for model in snapshot['models'].values():
        model['weights_sha256']=DIGEST
    replies['/api/kiosk/snapshot']=snapshot
    connect_and_select(client)
    assert prepare_models(app,client,model_sources).json['ready'] is True
    preview(client)
    assert finish(client).status_code==200
    auth=camera_headers(client)
    auth['X-Camera-Owner']=client.post('/kiosk/camera/start',headers=auth).json['owner']
    nonce=client.post('/kiosk/camera/challenge',headers=auth).json['challenge']
    old=app.extensions['browser_camera']
    admin=app.test_client()
    assert admin.post('/login',data={'csrf_token':csrf(admin,'/login'),
        'username':'operator','password':'PasswordUji123'}).status_code==302
    token=csrf(admin,'/setup/scope/manage')
    replies['/api/kiosk/scope']={**IDENTITY,'scope_type':'parent'}
    newer=copy.deepcopy(snapshot)
    newer['snapshot_id']=newer['clock_anchor_id']=str(uuid4())
    replies['/api/kiosk/snapshot']=newer
    entered,release=Event(),Event()
    original=OpenerDirector.open
    def remote(opener,request,**kwargs):
        if urlsplit(request.full_url).path=='/api/kiosk/scope':
            entered.set()
            assert release.wait(3)
        return original(opener,request,**kwargs)
    monkeypatch.setattr(OpenerDirector,'open',remote)
    results=[]
    def apply_change():
        results.append(admin.post('/setup/scope',data={'csrf_token':token,'skpd_id':'9','scope_type':'parent'},
                                  headers={'Accept':'application/json'}))
    request=Thread(target=apply_change)
    request.start()
    try:
        assert entered.wait(3)
        assert client.post('/kiosk/camera/frame',headers={**auth,'X-Camera-Challenge':nonce},
                           data=jpeg(),content_type='image/jpeg').status_code in (409,410)
        assert client.post('/kiosk/camera/start',headers=camera_headers(client)).status_code==409
        # Another admin cannot clear the first request's hold or race its permission change.
        competing=change(client,skpd_id='9',scope_type='all_skpd')
        assert competing.status_code==409 and competing.json['capture_held'] is True
    finally:
        release.set()
        request.join(timeout=5)
    try:
        assert not request.is_alive()
        assert results[0].status_code==200
        assert old.closed and app.extensions['browser_camera'] is not old
        assert client.post('/kiosk/camera/challenge',headers=auth).status_code==410
        assert client.get('/setup/readiness').json['ready'] is False
        fresh=camera_headers(client)
        started=client.post('/kiosk/camera/start',headers=fresh)
        assert started.status_code==200
        fresh['X-Camera-Owner']=started.json['owner']
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
        scoped=[call for call in calls if urlsplit(call.full_url).path.endswith('/snapshot')
                and 'scope' in parse_qs(urlsplit(call.full_url).query)]
        assert json.loads(parse_qs(urlsplit(scoped[-1].full_url).query)['scope'][0])=={
            'skpd_id':9,'scope_type':'parent','subunit_id':None}
        assert not any(urlsplit(call.full_url).path.endswith('/events') for call in calls)
    finally:
        app.extensions['browser_camera'].close()


def test_native_cli_observes_hold_during_capture_and_releases_ownership_cleanly(
        wizard,tmp_path,model_sources,inference_providers,monkeypatch):
    import signal
    from kiosk.lock import SingleInstanceLock
    from test_camera import FakeCapture
    app,client,replies,calls=wizard
    app.config.update(CAMERA_SOURCE='native',KIOSK_LOCK_FILE=str(tmp_path/'camera.lock'),
                      KIOSK_STATE_FILE=str(tmp_path/'camera.json'))
    snapshot=copy.deepcopy(SNAPSHOT)
    for model in snapshot['models'].values():
        model['weights_sha256']=DIGEST
    replies['/api/kiosk/snapshot']=snapshot
    connect_and_select(client)
    assert prepare_models(app,client,model_sources).json['ready'] is True
    handlers,captures={},[]
    class Capture(FakeCapture):
        def read(self):
            if signal.SIGINT in handlers:
                (tmp_path/'camera.lock.pause').write_text('scope\n')
            return super().read()
    def camera(index,backend):
        capture=Capture()
        captures.append(capture)
        return capture
    monkeypatch.setattr('cv2.VideoCapture',camera)
    assert client.post('/setup/camera/check',headers=camera_headers(client)).status_code==200
    assert finish(client).status_code==200
    captures.clear()
    monkeypatch.setattr(signal,'signal',lambda sig,handler:handlers.update({sig:handler}))
    result=app.test_cli_runner().invoke(args=['kiosk-run','--no-preview'])
    assert result.exit_code==0,result.output
    assert captures and all(capture.released for capture in captures)
    with SingleInstanceLock(tmp_path/'camera.lock'):
        pass
    opened=len(captures)
    held=app.test_cli_runner().invoke(args=['kiosk-run','--no-preview'])
    assert held.exit_code!=0 and 'pengaturan' in held.output.lower()
    assert len(captures)==opened
    assert not any(call.full_url.endswith('/events') for call in calls)
