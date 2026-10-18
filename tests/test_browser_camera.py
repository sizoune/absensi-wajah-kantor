"""Browser JPEG transport is local-only preview unless e-office is configured."""
import struct
import threading
import time

import cv2
import numpy as np
import pytest
from flask import template_rendered
from itsdangerous import URLSafeTimedSerializer

from app import create_app
from kiosk.lock import AlreadyRunning, SingleInstanceLock


@pytest.fixture
def browser_app(tmp_path):
    app = create_app('testing', overrides={
        'CAMERA_SOURCE': 'browser', 'ATTENDANCE_MODE': 'standalone',
        'KIOSK_STATE_FILE': str(tmp_path / 'status.json'),
        'KIOSK_LOCK_FILE': str(tmp_path / 'camera.lock'),
    })
    yield app
    runtime = app.extensions.get('browser_camera')
    if runtime:
        runtime.close()


def headers(client, *, origin='http://localhost'):
    client.get('/kiosk/')
    with client.session_transaction() as cookie:
        cookie.setdefault('csrf_token', 'test-token-' + str(id(client)))
        token = cookie['csrf_token']
    signed = URLSafeTimedSerializer(client.application.secret_key, salt='wtf-csrf-token').dumps(token)
    return {'Origin': origin, 'X-CSRFToken': signed}


def start(client):
    auth = headers(client)
    response = client.post('/kiosk/camera/start', headers=auth)
    assert response.status_code == 200, response.get_data(as_text=True)
    auth['X-Camera-Owner'] = response.json['owner']
    return auth


def challenge(client, auth):
    response = client.post('/kiosk/camera/challenge', headers=auth)
    assert response.status_code == 200, response.get_data(as_text=True)
    return dict(auth, **{'X-Camera-Challenge': response.json['challenge']})


def jpeg(width=32, height=24):
    ok, data = cv2.imencode('.jpg', np.zeros((height, width, 3), dtype=np.uint8))
    assert ok
    return data.tobytes()


def frame(client, auth, data=None, **kwargs):
    return client.post('/kiosk/camera/frame', headers=auth, data=jpeg() if data is None else data,
                       content_type='image/jpeg', **kwargs)


def test_native_source_exposes_no_browser_controls_or_transport(app, client):
    seen = []
    def rendered(sender, template, context, **extra):
        seen.append(context.get('browser_camera_enabled'))
    with template_rendered.connected_to(rendered, app):
        assert client.get('/kiosk/').status_code == 200
    assert seen == [False]
    assert client.post('/kiosk/camera/start', headers=headers(client)).status_code == 404
    assert 'browser_camera' not in app.extensions


def test_preview_transport_never_loads_recognition_or_writes_attendance(browser_app, monkeypatch):
    monkeypatch.setattr('kiosk.cli.load_eoffice', lambda *a: pytest.fail('models loaded in preview'))
    monkeypatch.setattr('kiosk.worker.KioskWorker', lambda *a, **k: pytest.fail('standalone worker created'))
    monkeypatch.setattr('services.attendance_service.record', lambda *a, **k: pytest.fail('attendance recorded'))
    client = browser_app.test_client()
    auth = start(client)
    response = frame(client, challenge(client, auth))
    assert response.status_code == 200
    assert response.json == {'ok': True, 'mode': 'preview'}
    status = client.get('/kiosk/status').json
    assert status['online'] is True
    assert 'tidak mencatat absensi' in status['hint'].lower()
    assert not {'owner', 'challenge'} & status.keys()
    assert client.post('/kiosk/camera/stop', headers=auth).json == {'ok': True}


def test_camera_mutations_require_csrf_and_exact_same_origin(browser_app):
    client = browser_app.test_client()
    auth = headers(client)
    for action in ('start', 'challenge', 'frame', 'stop'):
        url = '/kiosk/camera/' + action
        for bad in ({'Origin': 'http://localhost'},
                    dict(auth, Origin='https://localhost'),
                    dict(auth, Origin='http://localhost:9999'),
                    dict(auth, Origin='http://localhost.evil.test'),
                    {'X-CSRFToken': auth['X-CSRFToken']}):
            assert client.post(url, headers=bad).status_code == 403


def test_only_owning_cookie_and_token_can_capture_or_stop(browser_app):
    first, other = browser_app.test_client(), browser_app.test_client()
    auth = start(first)
    outsider = headers(other)
    assert other.post('/kiosk/camera/start', headers=outsider).status_code == 409
    for action in ('challenge', 'frame', 'stop'):
        assert first.post('/kiosk/camera/' + action, headers=headers(first)).status_code == 403
        assert other.post('/kiosk/camera/' + action,
                          headers=dict(outsider, **{'X-Camera-Owner': auth['X-Camera-Owner']})).status_code == 403
    assert first.post('/kiosk/camera/stop', headers=auth).status_code == 200
    assert other.post('/kiosk/camera/start', headers=outsider).status_code == 200


def test_challenge_is_one_use_and_client_has_no_id_or_time_authority(browser_app):
    client = browser_app.test_client()
    auth = start(client)
    assert frame(client, auth).status_code == 410
    capture = challenge(client, auth)
    assert frame(client, capture, data=b'not a jpeg').status_code == 400
    assert frame(client, capture).status_code == 410
    capture = challenge(client, auth)
    assert frame(client, capture).status_code == 200
    assert frame(client, capture).status_code == 410
    capture = challenge(client, auth)
    assert client.post('/kiosk/camera/frame', headers=capture,
                       json={'pegawai_id': 1, 'captured_at': '2000-01-01'}).status_code == 415
    assert frame(client, capture).status_code == 410
    assert frame(client, challenge(client, auth), query_string={'captured_at': '2000-01-01'}).status_code == 400


def test_byte_dimension_and_malformed_limits_precede_decode(browser_app, monkeypatch):
    client = browser_app.test_client()
    auth = start(client)
    assert frame(client, challenge(client, auth), data=b'x' * (512 * 1024 + 1)).status_code == 413
    data = bytearray(jpeg())
    sof = data.index(b'\xff\xc0')
    data[sof + 5:sof + 9] = struct.pack('>HH', 65535, 65535)
    monkeypatch.setattr(cv2, 'imdecode', lambda *a: pytest.fail('unsafe frame reached image decoder'))
    assert frame(client, challenge(client, auth), data=bytes(data)).status_code == 400
    assert frame(client, challenge(client, auth), data=b'\xff\xd8\xff\xc0\x00\x01\xff\xd9').status_code == 400


def test_expired_challenge_and_session_release_without_another_request(browser_app):
    client = browser_app.test_client()
    auth = start(client)
    runtime = browser_app.extensions['browser_camera']
    now = [runtime.clock()]
    runtime.clock = lambda: now[0]
    capture = challenge(client, auth)
    now[0] += 3.1
    assert frame(client, capture).status_code == 410
    now[0] += 16
    deadline = time.monotonic() + 2
    while runtime.active and time.monotonic() < deadline:
        time.sleep(.02)
    assert not runtime.active
    assert client.post('/kiosk/camera/challenge', headers=auth).status_code == 410
    assert browser_app.test_client().post('/kiosk/camera/start',
        headers=headers(browser_app.test_client())).status_code == 403  # CSRF belongs to its cookie.
    other = browser_app.test_client()
    assert other.post('/kiosk/camera/start', headers=headers(other)).status_code == 200


def test_inflight_frames_and_native_camera_owner_are_bounded(browser_app, monkeypatch):
    lock = SingleInstanceLock(browser_app.config['KIOSK_LOCK_FILE']).acquire()
    client = browser_app.test_client()
    assert client.post('/kiosk/camera/start', headers=headers(client)).status_code == 409
    lock.release()
    auth = start(client)
    entered, release = threading.Event(), threading.Event()
    original = cv2.imdecode
    def delayed(*args):
        entered.set()
        assert release.wait(2)
        return original(*args)
    monkeypatch.setattr(cv2, 'imdecode', delayed)
    capture = challenge(client, auth)
    result = []
    request = threading.Thread(target=lambda: result.append(frame(client, capture)))
    request.start()
    try:
        assert entered.wait(2)
        assert client.post('/kiosk/camera/challenge', headers=auth).status_code == 409
        assert frame(client, capture).status_code == 409
        with pytest.raises(AlreadyRunning):
            SingleInstanceLock(browser_app.config['KIOSK_LOCK_FILE']).acquire()
        assert client.post('/kiosk/camera/stop', headers=auth).status_code == 200
    finally:
        release.set()
        request.join(3)
    assert result[0].status_code == 410
    assert client.post('/kiosk/camera/start', headers=headers(client)).status_code == 200


def eoffice_runtime(app, tmp_path, monkeypatch):
    """Use real queue/clock/worker with synthetic models and an offline remote service."""
    from integration.clock import TrustedClock
    from integration.store import IntegrationStore
    from integration.sync import SyncWorker
    from kiosk.browser import BrowserCamera
    from recognition.detector import QualitySettings
    from recognition.liveness import LivenessSettings
    from test_eoffice_identify import setup_scene
    scene = setup_scene()
    snapshot, models, _, _, detector, _ = scene
    now, resources, closed = [10.], [], []
    path = tmp_path / 'integration.sqlite3'
    app.extensions['browser_camera'].close()
    app.config['ATTENDANCE_MODE'] = 'eoffice'
    runtime = app.extensions['browser_camera'] = BrowserCamera(app)
    runtime.clock = lambda: now[0]
    class Offline:
        def snapshot(self):
            raise TimeoutError('test remote unavailable')
        def send(self, data):
            raise TimeoutError('test remote unavailable')
    def load(app):
        assert threading.current_thread().name == 'browser-camera'
        frame_store, sync_store = IntegrationStore(path, bytes(32)), IntegrationStore(path, bytes(32))
        frame_store.replace_snapshot(snapshot)
        trusted = TrustedClock(sync_store, wall_time=lambda: now[0])
        trusted.anchor(snapshot['issued_at'], snapshot['snapshot_id'], now[0])
        sync = SyncWorker(sync_store, Offline(), trusted)
        for store in (frame_store, sync_store):
            original = store.close
            def close(original=original):
                closed.append(threading.current_thread().name)
                original()
            store.close = close
        resources.append((frame_store, sync))
        return detector, QualitySettings(), models.liveness, LivenessSettings(mode='passive'), {
            'integration_store': frame_store, 'sync_worker': sync, 'trusted_clock': trusted, 'eoffice_models': models}
    monkeypatch.setattr('kiosk.cli.load_eoffice', load)
    monkeypatch.setattr('services.attendance_service.record', lambda *a, **k: pytest.fail('standalone attendance'))
    def pending():
        store = IntegrationStore(path, bytes(32))
        try:
            return store.pending()
        finally:
            store.close()
    return runtime, now, scene, pending, resources, closed


def test_eoffice_reuses_worker_and_timestamps_challenge_before_decode(browser_app, tmp_path, monkeypatch):
    import json
    runtime, now, scene, pending, resources, closed = eoffice_runtime(browser_app, tmp_path, monkeypatch)
    client = browser_app.test_client()
    auth = start(client)
    original = cv2.imdecode
    def delayed(*args):
        now[0] += .25
        return original(*args)
    monkeypatch.setattr(cv2, 'imdecode', delayed)
    for acquired in (10.1, 10.5, 10.9):
        now[0] = acquired
        capture = challenge(client, auth)
        now[0] += .1
        assert frame(client, capture, data=cv2.imencode('.jpg', scene[-1])[1].tobytes()).status_code == 200
    event, = pending()
    body = json.loads(event.body)
    assert body['pegawai_id'] == 101
    assert body['occurred_at'] == '2026-10-14T07:24:59.900+08:00'
    assert body['elapsed_ms'] == 900
    assert body['evidence']['samples'] == 3
    assert client.post('/kiosk/camera/stop', headers=auth).status_code == 200
    # Stopping browser capture preserves the durable offline event and background sync.
    assert len(pending()) == 1
    assert resources[0][1]._thread.is_alive()
    runtime.close()
    assert not resources[0][1]._thread.is_alive()
    assert sorted(closed) == ['browser-camera', 'eoffice-sync']
    SingleInstanceLock(browser_app.config['KIOSK_LOCK_FILE']).acquire().release()


def test_inference_expiring_after_liveness_never_enqueues(browser_app, tmp_path, monkeypatch):
    runtime, now, scene, pending, _, _ = eoffice_runtime(browser_app, tmp_path, monkeypatch)
    client = browser_app.test_client()
    auth = start(client)
    data = cv2.imencode('.jpg', scene[-1])[1].tobytes()
    for stamp in (10.1, 10.2):
        now[0] = stamp
        assert frame(client, challenge(client, auth), data=data).status_code == 200
    original = scene[1].liveness.score
    def slow(*args):
        now[0] += 3.1
        return original(*args)
    scene[1].liveness.score = slow
    now[0] = 10.3
    assert frame(client, challenge(client, auth), data=data).status_code == 410
    assert pending() == []
    scene[1].liveness.score = original
    assert frame(client, challenge(client, auth), data=data).status_code == 200
    assert pending() == []  # Expired inference cannot donate identity/liveness votes.


@pytest.mark.parametrize('boundary', ['stop', 'gap', 'expiry'])
def test_identity_votes_do_not_cross_capture_boundaries(browser_app, tmp_path, monkeypatch, boundary):
    runtime, now, scene, pending, _, _ = eoffice_runtime(browser_app, tmp_path, monkeypatch)
    client = browser_app.test_client()
    auth = start(client)
    data = cv2.imencode('.jpg', scene[-1])[1].tobytes()
    for stamp in (10.1, 10.2):
        now[0] = stamp
        assert frame(client, challenge(client, auth), data=data).status_code == 200
    if boundary == 'stop':
        assert client.post('/kiosk/camera/stop', headers=auth).status_code == 200
        auth = start(client)
    elif boundary == 'gap':
        now[0] += 2.1
    else:
        now[0] += 16
        auth = start(client)
    assert frame(client, challenge(client, auth), data=data).status_code == 200
    assert pending() == []
    for _ in range(2):
        now[0] += .1
        assert frame(client, challenge(client, auth), data=data).status_code == 200
    assert len(pending()) == 1


def test_loopback_host_allowlist_rejects_matching_rebound_origin(browser_app):
    client = browser_app.test_client()
    auth = headers(client)
    client.set_cookie('session', client.get_cookie('session').value, domain='evil.test')
    response = client.post('/kiosk/camera/start', base_url='http://evil.test',
        headers=dict(auth, Origin='http://evil.test'))
    assert response.status_code == 403
    assert response.json['error'] == 'origin'


def test_status_write_failure_still_releases_runtime_lock(browser_app, monkeypatch):
    from kiosk.state import KioskState
    def fail(*args, **kwargs):
        raise OSError('test volume unavailable')
    monkeypatch.setattr(KioskState, 'publish', fail)
    client = browser_app.test_client()
    client.post('/kiosk/camera/start', headers=headers(client))
    runtime = browser_app.extensions['browser_camera']
    runtime._thread.join(2)
    assert not runtime._thread.is_alive()
    assert client.post('/kiosk/camera/start', headers=headers(client)).status_code == 503
    SingleInstanceLock(browser_app.config['KIOSK_LOCK_FILE']).acquire().release()


@pytest.mark.parametrize('field', ['X-Camera-Owner', 'X-Camera-Challenge'])
def test_untrusted_non_ascii_headers_fail_closed(browser_app, field):
    client = browser_app.test_client()
    capture = challenge(client, start(client))
    response = frame(client, dict(capture, **{field: 'é'}))
    assert response.status_code == (403 if field == 'X-Camera-Owner' else 410)
