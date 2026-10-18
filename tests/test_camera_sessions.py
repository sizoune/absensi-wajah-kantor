"""Single camera ownership and admin recovery through Flask HTTP."""
from concurrent.futures import ThreadPoolExecutor
import copy
import json
import subprocess
import sys
from threading import Barrier, Event, Thread
import time

import cv2
import pytest

from extensions import db
from models import User
from test_browser_camera import browser_app, challenge, frame, headers, start
from test_setup_readiness import camera_headers, finish, inference_providers, model_sources, prepare_models, preview, wizard


@pytest.fixture
def session_app(browser_app):
    with browser_app.app_context():
        db.create_all()
        for username, role in (('camera_admin', 'admin'), ('camera_operator', 'operator')):
            user = User(name=username, username=username, role=role, is_active=True)
            user.set_password('CameraPassword123')
            db.session.add(user)
        db.session.commit()
    return browser_app


def login(app, username='camera_admin'):
    client = app.test_client()
    auth = headers(client)
    response = client.post('/login', data={'username': username, 'password': 'CameraPassword123',
                                          'csrf_token': auth['X-CSRFToken']})
    assert response.status_code == 302
    return client


def test_active_admin_stops_another_browser_session_before_replacement(session_app):
    old = session_app.test_client()
    old_auth = start(old)
    old_frame = challenge(old, old_auth)
    admin = login(session_app)
    admin_auth = headers(admin)
    assert admin.post('/kiosk/camera/start', headers=admin_auth).status_code == 409
    stopped = admin.post('/kiosk/camera/admin-stop', headers=admin_auth)
    assert stopped.status_code == 200
    assert stopped.json == {'ok': True, 'draining': False}
    assert frame(old, old_frame).status_code == 410
    replacement = start(admin)
    assert replacement['X-Camera-Owner'] != old_auth['X-Camera-Owner']
    assert old.post('/kiosk/camera/challenge', headers=old_auth).status_code == 403
    assert frame(old, old_frame).status_code == 403
    assert frame(admin, challenge(admin, replacement)).status_code == 200


@pytest.mark.parametrize('caller', ['anonymous', 'camera_operator', 'inactive_admin'])
def test_non_admin_cannot_revoke_another_camera_owner(session_app, caller):
    old = session_app.test_client()
    old_auth = start(old)
    if caller == 'anonymous':
        denied = session_app.test_client()
    else:
        denied = login(session_app, 'camera_admin' if caller == 'inactive_admin' else caller)
        if caller == 'inactive_admin':
            with session_app.app_context():
                db.session.execute(db.update(User).where(User.username == 'camera_admin').values(is_active=False))
                db.session.commit()
    response = denied.post('/kiosk/camera/admin-stop', headers=headers(denied))
    assert response.status_code == 403
    assert response.json['error'] == 'admin_required'
    assert denied.post('/kiosk/camera/start', headers=headers(denied)).status_code == 409
    assert frame(old, challenge(old, old_auth)).status_code == 200


def test_admin_recovery_retains_exact_origin_and_json_csrf_validation(session_app):
    old = session_app.test_client()
    old_auth = start(old)
    admin = login(session_app)
    auth = headers(admin)
    for bad in ({'Origin': 'http://localhost'},
                {**auth, 'Origin': 'https://evil.example'},
                {'X-CSRFToken': auth['X-CSRFToken']}):
        response = admin.post('/kiosk/camera/admin-stop', headers=bad)
        assert response.status_code == 403
    assert frame(old, challenge(old, old_auth)).status_code == 200


def test_admin_stop_discards_queued_frame_before_replacement_can_start(session_app, monkeypatch):
    old = session_app.test_client()
    old_auth = start(old)
    admin = login(session_app)
    entered, release = Event(), Event()
    original = cv2.imdecode
    def delayed(*args):
        entered.set()
        assert release.wait(3)
        return original(*args)
    monkeypatch.setattr(cv2, 'imdecode', delayed)
    capture = challenge(old, old_auth)
    responses = []
    processing = Thread(target=lambda: responses.append(frame(old, capture)))
    processing.start()
    try:
        assert entered.wait(2)
        response = admin.post('/kiosk/camera/admin-stop', headers=headers(admin))
        assert response.status_code == 200
        assert response.json['draining'] is True
        assert admin.post('/kiosk/camera/start', headers=headers(admin)).status_code == 409
    finally:
        release.set()
        processing.join(3)
    assert responses[0].status_code == 410
    replacement = start(admin)
    assert frame(admin, challenge(admin, replacement)).status_code == 200


def test_simultaneous_starts_grant_exactly_one_camera_owner(session_app):
    clients = [session_app.test_client(), session_app.test_client()]
    auth = [headers(client) for client in clients]
    barrier = Barrier(2)
    def begin(index):
        barrier.wait(timeout=3)
        return clients[index].post('/kiosk/camera/start', headers=auth[index])
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(begin, (0, 1)))
    assert sorted(response.status_code for response in responses) == [200, 409]
    winner = next(index for index, response in enumerate(responses) if response.status_code == 200)
    auth[winner]['X-Camera-Owner'] = responses[winner].json['owner']
    loser = 1 - winner
    copied = {**auth[loser], 'X-Camera-Owner': responses[winner].json['owner']}
    assert clients[loser].post('/kiosk/camera/challenge', headers=copied).status_code == 403
    assert frame(clients[winner], challenge(clients[winner], auth[winner])).status_code == 200


def test_native_process_and_browser_share_the_same_os_camera_lock(session_app):
    script = ('from kiosk.lock import SingleInstanceLock; import sys; '
              'lock=SingleInstanceLock(sys.argv[1]).acquire(); print("held", flush=True); sys.stdin.read()')
    child = subprocess.Popen([sys.executable, '-c', script, session_app.config['KIOSK_LOCK_FILE']],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    client = session_app.test_client()
    try:
        assert child.stdout.readline().strip() == 'held'
        response = client.post('/kiosk/camera/start', headers=headers(client))
        assert response.status_code == 409
        assert response.json['error'] == 'busy'
    finally:
        child.communicate('', timeout=3)
    assert start(client)


def test_only_an_active_admin_sees_camera_replacement_controls(session_app):
    from test_browser_camera_template import Elements
    anonymous = session_app.test_client()
    assert 'browser-camera-admin-stop' not in anonymous.get('/kiosk/').get_data(as_text=True)
    operator = login(session_app, 'camera_operator')
    assert 'browser-camera-admin-stop' not in operator.get('/kiosk/').get_data(as_text=True)
    admin = login(session_app)
    page = admin.get('/kiosk/')
    ids = Elements(page.get_data(as_text=True)).ids
    assert ids['browser-camera'][1]['data-admin-stop-url'] == '/kiosk/camera/admin-stop'
    assert ids['browser-camera-admin-stop'][1]['type'] == 'button'
    assert page.headers['Cache-Control'] == 'no-store'


def test_setup_grant_alone_cannot_stop_another_preview_but_active_admin_can(wizard, tmp_path):
    app, admin, _, _ = wizard
    app.config.update(KIOSK_LOCK_FILE=str(tmp_path / 'camera.lock'), KIOSK_STATE_FILE=str(tmp_path / 'camera.json'))
    installer = app.test_client()
    installer.set_cookie('session', admin.get_cookie('session').value)
    with installer.session_transaction() as session:
        session.pop('_user_id', None)
    try:
        old_auth = preview(installer)
        assert installer.post('/setup/camera/admin-stop', headers=camera_headers(installer)).status_code == 403
        assert admin.post('/setup/camera/admin-stop', headers=camera_headers(admin)).status_code == 200
        assert installer.post('/setup/camera/challenge', headers=old_auth).status_code == 410
        assert 'data-admin-stop-url="/setup/camera/admin-stop"' in admin.get('/setup').get_data(as_text=True)
    finally:
        app.extensions['browser_camera'].close()


def test_admin_stop_preserves_queued_bytes_and_background_authenticated_sync(
        wizard, tmp_path, model_sources, inference_providers):
    from integration.store import IntegrationStore
    from services.biometric_service import load_keyring
    from test_eoffice_sync import EVENT, SNAPSHOT
    from test_setup_models import DIGEST
    from test_setup_organization import connect_and_select
    app, admin, replies, calls = wizard
    app.config.update(KIOSK_LOCK_FILE=str(tmp_path / 'camera.lock'), KIOSK_STATE_FILE=str(tmp_path / 'camera.json'))
    snapshot = copy.deepcopy(SNAPSHOT)
    for model in snapshot['models'].values():
        model['weights_sha256'] = DIGEST
    replies['/api/kiosk/snapshot'] = snapshot
    replies['/api/kiosk/events'] = TimeoutError('controlled offline send')
    connect_and_select(admin)
    assert prepare_models(app, admin, model_sources).json['ready'] is True
    preview(admin)
    original_key = (tmp_path / 'biometric_keys.json').read_bytes()
    with app.app_context():
        active, keys = load_keyring(app.config['EOFFICE_KEY_FILE'])
    retained = json.dumps(EVENT).encode() + b'\n'
    store = IntegrationStore(tmp_path / 'eoffice.sqlite3', keys[active])
    store.enqueue(retained)
    try:
        assert finish(admin).status_code == 200
        old = app.test_client()
        old_auth = start(old)
        delayed = challenge(old, old_auth)
        assert admin.post('/kiosk/camera/admin-stop', headers=camera_headers(admin)).status_code == 200
        assert frame(old, delayed).status_code == 410
        replacement = start(admin)
        assert frame(admin, challenge(admin, replacement)).status_code == 200
        before = sum('/events' in call.full_url for call in calls)
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            if sum('/events' in call.full_url for call in calls) > before:
                break
            time.sleep(.02)
        sent = [call.data for call in calls if '/events' in call.full_url]
        assert len(sent) > before
        assert all(body == retained for body in sent)
        pending = store.pending()
        assert [(entry.event_id, entry.body) for entry in pending] == [(EVENT['event_id'], retained)]
        health = admin.get('/kiosk/status').json['integration']
        assert health['auth'] == 'ok' and health['clock'] == 'ok' and health['storage'] == 'ok'
        assert (tmp_path / 'biometric_keys.json').read_bytes() == original_key
    finally:
        app.extensions['browser_camera'].close()
        store.close()


def test_pending_operational_start_cannot_reclaim_an_admin_revoked_generation(
        wizard, tmp_path, model_sources, inference_providers, monkeypatch):
    from test_eoffice_sync import SNAPSHOT
    from test_setup_models import DIGEST
    from test_setup_organization import connect_and_select
    app, admin, replies, _ = wizard
    app.config.update(KIOSK_LOCK_FILE=str(tmp_path / 'camera.lock'), KIOSK_STATE_FILE=str(tmp_path / 'camera.json'))
    snapshot = copy.deepcopy(SNAPSHOT)
    for model in snapshot['models'].values():
        model['weights_sha256'] = DIGEST
    replies['/api/kiosk/snapshot'] = snapshot
    connect_and_select(admin)
    assert prepare_models(app, admin, model_sources).json['ready'] is True
    preview(admin)
    assert finish(admin).status_code == 200
    assert app.extensions['browser_camera'].close()
    entered, release = Event(), Event()
    original = cv2.FaceDetectorYN.create
    def delayed(*args):
        entered.set()
        assert release.wait(3)
        return original(*args)
    monkeypatch.setattr(cv2.FaceDetectorYN, 'create', delayed)
    old = app.test_client()
    old_auth = headers(old)
    responses = []
    pending = Thread(target=lambda: responses.append(old.post('/kiosk/camera/start', headers=old_auth)))
    pending.start()
    try:
        assert entered.wait(2)
        assert admin.post('/kiosk/camera/admin-stop', headers=camera_headers(admin)).status_code == 200
    finally:
        release.set()
        pending.join(3)
    try:
        assert responses[0].status_code == 410
        replacement = start(admin)
        assert frame(admin, challenge(admin, replacement)).status_code == 200
    finally:
        app.extensions['browser_camera'].close()
