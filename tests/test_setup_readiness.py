"""Setup completion and capture readiness through HTTP and the native CLI."""
import copy
import time
from types import SimpleNamespace
from uuid import uuid4

import numpy as np

import pytest

from test_setup import csrf, installation
from test_setup_organization import connect_and_select, post, wizard
from test_eoffice_sync import SNAPSHOT
from test_browser_camera import jpeg
from test_setup_models import DIGEST, model_sources


def finish(client):
    return client.post('/setup/finish', data={'csrf_token': csrf(client)},
                       headers={'Accept': 'application/json'})


def test_new_eoffice_setup_prepares_required_passive_liveness_automatically(tmp_path):
    app = installation(tmp_path, LIVENESS_MODE='passive+challenge')
    assert app.config['LIVENESS_MODE'] == 'passive'


def test_valid_empty_snapshot_finishes_setup_without_camera_or_models(wizard, tmp_path):
    app, client, replies, _ = wizard
    replies['/api/kiosk/snapshot'] = {**copy.deepcopy(SNAPSHOT), 'employees': []}
    connect_and_select(client)
    key = (tmp_path / 'biometric_keys.json').read_bytes()
    result = finish(client)
    assert result.status_code == 200
    assert result.json['complete'] is True
    assert result.json['ready'] is False
    assert {'enrollment', 'models', 'camera'} <= set(result.json['blocked'])
    assert client.get('/setup').status_code == 404
    assert client.get('/setup/status').status_code == 404
    assert client.post('/setup/admin', data={'csrf_token': csrf(client, '/setup/readiness/manage')}).status_code == 404
    assert app.test_cli_runner().invoke(args=['setup-code']).exit_code != 0
    assert client.get('/setup/models/manage').status_code == 200
    assert client.get('/setup/camera/manage').status_code == 200
    assert (tmp_path / 'biometric_keys.json').read_bytes() == key
    assert app.extensions['browser_camera']._thread is None
    from app import create_app
    restarted = create_app('testing', overrides={
        'FIRST_SETUP_ENABLED': True, 'ATTENDANCE_MODE': 'eoffice', 'SECRET_KEY': None,
        'SQLALCHEMY_DATABASE_URI': f'sqlite:///{tmp_path}/app.sqlite3',
        'SQLALCHEMY_ENGINE_OPTIONS': {}, 'WTF_CSRF_ENABLED': True,
    }, instance_path=str(tmp_path))
    assert restarted.test_client().get('/setup').status_code == 404
    assert restarted.config['SETUP_MANAGED'] is True
    held = restarted.test_cli_runner().invoke(args=['kiosk-run', '--no-preview'])
    assert held.exit_code != 0 and 'belum siap' in held.output
    assert (tmp_path / 'biometric_keys.json').read_bytes() == key


def camera_headers(client):
    path = '/setup' if client.get('/setup').status_code == 200 else '/setup/readiness/manage'
    return {'Origin': 'http://localhost', 'X-CSRFToken': csrf(client, path)}


@pytest.fixture
def inference_providers(monkeypatch):
    """Replace external inference engines, while keeping loaders, models, clock and sync real."""
    class Detector:
        def setInputSize(self, size):
            pass
        def detect(self, frame):
            return None, None
    class Net:
        def setInput(self, tensor):
            pass
        def forward(self):
            return np.array([[0., 10.]], dtype=np.float32)
    class Session:
        def get_inputs(self):
            return [SimpleNamespace(name='image', shape=[1, 3, 112, 112], type='tensor(float)')]
        def get_outputs(self):
            return [SimpleNamespace(name='embedding', shape=[1, 512], type='tensor(float)')]
        def run(self, *args):
            return [np.ones((1, 512), dtype=np.float32)]
    class Interpreter:
        def __init__(self, **kwargs):
            pass
        def allocate_tensors(self):
            pass
        def get_input_details(self):
            return [{'shape': [1, 112, 112, 3], 'dtype': np.float32, 'index': 0}]
        def get_output_details(self):
            return [{'shape': [1, 192], 'dtype': np.float32, 'index': 1}]
        def set_tensor(self, *args):
            pass
        def invoke(self):
            pass
        def get_tensor(self, index):
            return np.ones((1, 192), dtype=np.float32)
    monkeypatch.setattr('cv2.FaceDetectorYN.create', lambda *args: Detector())
    monkeypatch.setattr('cv2.dnn.readNetFromONNX', lambda *args: Net())
    monkeypatch.setattr('recognition.eoffice_models.ort.InferenceSession', lambda *args, **kwargs: Session())
    monkeypatch.setattr('recognition.eoffice_models.Interpreter', Interpreter)


def prepare_models(app, client, model_sources):
    app.extensions['setup_model_opener'] = model_sources[1]
    return client.post('/setup/models', data={'csrf_token': camera_headers(client)['X-CSRFToken']},
                       headers={'Accept': 'application/json'})


def preview(client):
    auth = camera_headers(client)
    result = client.post('/setup/camera/start', headers=auth)
    assert result.status_code == 200
    auth['X-Camera-Owner'] = result.json['owner']
    challenge = client.post('/setup/camera/challenge', headers=auth)
    response = client.post('/setup/camera/frame', headers={
        **auth, 'X-Camera-Challenge': challenge.json['challenge']}, data=jpeg(), content_type='image/jpeg')
    assert response.status_code == 200
    return auth


def test_setup_browser_preview_checks_camera_without_models_or_attendance(wizard, tmp_path):
    app, client, _, calls = wizard
    app.config.update(KIOSK_LOCK_FILE=str(tmp_path / 'camera.lock'),
                      KIOSK_STATE_FILE=str(tmp_path / 'camera.json'))
    auth = camera_headers(client)
    try:
        result = client.post('/setup/camera/start', headers=auth)
        assert result.status_code == 200
        assert result.json['mode'] == 'preview'
        auth['X-Camera-Owner'] = result.json['owner']
        challenge = client.post('/setup/camera/challenge', headers=auth)
        assert challenge.status_code == 200
        response = client.post('/setup/camera/frame', headers={
            **auth, 'X-Camera-Challenge': challenge.json['challenge']},
            data=jpeg(), content_type='image/jpeg')
        assert response.status_code == 200
        assert response.json['mode'] == 'preview'
        assert client.get('/setup/status').json['checks']['camera'] is True
        assert not (tmp_path / 'eoffice.sqlite3').exists()
        assert calls == []
        assert client.post('/kiosk/camera/start', headers=auth).status_code == 409
    finally:
        app.extensions['browser_camera'].close()


def test_completed_but_unready_setup_cannot_open_native_capture(wizard, monkeypatch):
    app, client, _, _ = wizard
    connect_and_select(client)
    assert finish(client).status_code == 200
    attempted = []
    monkeypatch.setattr('cv2.VideoCapture', lambda *args: attempted.append(args))
    result = app.test_cli_runner().invoke(args=['kiosk-run', '--no-preview'])
    assert result.exit_code != 0
    assert 'belum siap' in result.output
    assert attempted == []


def test_native_selection_and_preview_are_hardware_only_and_invalidate_old_check(wizard, tmp_path, monkeypatch):
    from test_camera import FakeCapture
    app, client, _, calls = wizard
    app.config.update(CAMERA_SOURCE='native', KIOSK_LOCK_FILE=str(tmp_path / 'camera.lock'))
    monkeypatch.setattr('camera.devices.list_cameras', lambda: ['Integrated', 'USB camera'])
    captures = []
    def capture(index, backend):
        item = FakeCapture()
        captures.append((index, item))
        return item
    monkeypatch.setattr('cv2.VideoCapture', capture)
    assert client.get('/setup/camera').json['devices'] == [
        {'index': 0, 'name': 'Integrated'}, {'index': 1, 'name': 'USB camera'}]
    assert post(client, '/setup/camera/select', index='9').status_code == 400
    assert post(client, '/setup/camera/select', index='1').status_code == 200
    response = client.post('/setup/camera/check', headers=camera_headers(client))
    assert response.status_code == 200
    assert response.json['preview'].startswith('data:image/jpeg;base64,')
    assert all(index == 1 and item.released for index, item in captures)
    assert client.get('/setup/status').json['checks']['camera'] is True
    assert not (tmp_path / 'eoffice.sqlite3').exists()
    assert calls == []
    assert post(client, '/setup/camera/select', index='0').status_code == 200
    assert client.get('/setup/status').json['checks']['camera'] is False
    assert client.post('/setup/camera/check', headers={
        **camera_headers(client), 'Origin': 'https://evil.example'}).status_code == 403


def test_ready_browser_drains_preview_and_uses_a_fresh_authenticated_clock(
        wizard, tmp_path, model_sources, inference_providers):
    app, client, replies, calls = wizard
    app.config.update(KIOSK_LOCK_FILE=str(tmp_path / 'camera.lock'), KIOSK_STATE_FILE=str(tmp_path / 'camera.json'))
    snapshot = copy.deepcopy(SNAPSHOT)
    for model in snapshot['models'].values():
        model['weights_sha256'] = DIGEST
    replies['/api/kiosk/snapshot'] = snapshot
    connect_and_select(client)
    assert prepare_models(app, client, model_sources).json['ready'] is True
    old_auth = preview(client)
    old_runtime = app.extensions['browser_camera']
    snapshots_before_finish = sum('/snapshot' in call.full_url for call in calls)
    try:
        result = finish(client)
        assert result.status_code == 200
        assert result.json['complete'] is True
        assert result.json['ready'] is False
        assert old_runtime.closed and not old_runtime.active
        assert app.extensions['browser_camera'] is not old_runtime
        assert client.post('/kiosk/camera/challenge', headers=old_auth).status_code == 410
        assert client.post('/setup/camera/challenge', headers=old_auth).status_code == 410
        auth = camera_headers(client)
        started = client.post('/kiosk/camera/start', headers=auth)
        assert started.status_code == 200
        assert started.json['mode'] == 'eoffice'
        assert client.get('/setup/readiness').json['ready'] is False
        auth['X-Camera-Owner'] = started.json['owner']
        challenge = client.post('/kiosk/camera/challenge', headers=auth)
        assert client.post('/kiosk/camera/frame', headers={
            **auth, 'X-Camera-Challenge': challenge.json['challenge']},
            data=jpeg(), content_type='image/jpeg').status_code == 200
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            status = client.get('/setup/readiness').json
            if status['ready']:
                break
            time.sleep(.02)
        assert status['ready'] is True, status['blocked']
        assert sum('/snapshot' in call.full_url for call in calls) > snapshots_before_finish
        assert not any('/events' in call.full_url for call in calls)
        assert client.post('/kiosk/camera/stop', headers=auth).status_code == 200
        assert client.get('/setup/readiness').json['ready'] is False
        assert client.post('/kiosk/camera/start', headers=camera_headers(client)).status_code == 200
        assert client.get('/setup/readiness').json['ready'] is False
    finally:
        app.extensions['browser_camera'].close()


def test_failed_initial_snapshot_cannot_be_confused_with_a_valid_empty_snapshot(wizard):
    _, client, replies, _ = wizard
    replies['/api/kiosk/snapshot'] = b'{"protocol_version":1,'
    from test_setup_organization import TOKEN
    assert post(client, '/setup/credentials', token=TOKEN, base_url='https://server.example').status_code == 302
    assert post(client, '/setup/organization', skpd_id='9', scope_type='all_skpd').status_code == 400
    result = finish(client)
    assert result.status_code == 409
    assert result.json['complete'] is False
    assert result.json['snapshot']['available'] is False
    assert client.get('/setup').status_code == 200


def test_dynamic_model_files_and_metadata_override_stale_progress(wizard, tmp_path, model_sources):
    app, client, replies, _ = wizard
    snapshot = copy.deepcopy(SNAPSHOT)
    for model in snapshot['models'].values():
        model['weights_sha256'] = DIGEST
    replies['/api/kiosk/snapshot'] = snapshot
    connect_and_select(client)
    assert prepare_models(app, client, model_sources).json['ready'] is True
    assert client.get('/setup/status').json['checks']['models'] is True
    (tmp_path / 'models/glintr100.onnx').write_bytes(b'corrupt')
    assert client.get('/setup/status').json['checks']['models'] is False
    assert prepare_models(app, client, model_sources).json['ready'] is True
    incompatible = copy.deepcopy(snapshot)
    incompatible['snapshot_id'] = incompatible['clock_anchor_id'] = str(uuid4())
    incompatible['models']['auraface_512']['weights_sha256'] = 'b' * 64
    replies['/api/kiosk/snapshot'] = incompatible
    assert post(client, '/setup/snapshot').status_code == 302
    status = client.get('/setup/status').json
    assert status['models']['compatible'] is False
    assert status['can_finish'] is True
    assert finish(client).json['ready'] is False


def test_readiness_poll_reuses_unchanged_model_verification_and_rechecks_changed_files(
        wizard, tmp_path, model_sources, monkeypatch):
    import builtins
    from pathlib import Path
    app, client, replies, _ = wizard
    snapshot = copy.deepcopy(SNAPSHOT)
    for model in snapshot['models'].values():
        model['weights_sha256'] = DIGEST
    replies['/api/kiosk/snapshot'] = snapshot
    connect_and_select(client)
    assert prepare_models(app, client, model_sources).json['ready'] is True
    reads = []
    original = builtins.open
    def observe(path, mode='r', *args, **kwargs):
        if isinstance(path, (str, Path)) and Path(path).parent == tmp_path / 'models' and mode == 'rb':
            reads.append(Path(path).name)
        return original(path, mode, *args, **kwargs)
    monkeypatch.setattr(builtins, 'open', observe)
    assert client.get('/setup/readiness').json['checks']['models'] is True
    count = len(reads)
    assert count > 0
    assert client.get('/setup/readiness').json['checks']['models'] is True
    assert len(reads) == count
    (tmp_path / 'models/glintr100.onnx').write_bytes(b'corrupt')
    assert client.get('/setup/readiness').json['checks']['models'] is False
    assert len(reads) > count


@pytest.mark.parametrize('blocked', ['empty', 'revoked', 'clock'])
def test_native_retry_requires_enrollment_access_and_fresh_clock_before_camera_open(
        wizard, tmp_path, monkeypatch, model_sources, inference_providers, blocked):
    import signal
    from urllib.request import OpenerDirector
    from integration.client import RequestFailure
    from test_camera import FakeCapture
    app, client, replies, calls = wizard
    app.config.update(CAMERA_SOURCE='native', KIOSK_LOCK_FILE=str(tmp_path / 'camera.lock'),
                      KIOSK_STATE_FILE=str(tmp_path / 'camera.json'))
    snapshot = copy.deepcopy(SNAPSHOT)
    for model in snapshot['models'].values():
        model['weights_sha256'] = DIGEST
    empty = {**copy.deepcopy(snapshot), 'employees': []}
    replies['/api/kiosk/snapshot'] = empty
    connect_and_select(client)
    assert prepare_models(app, client, model_sources).json['ready'] is True
    opened, handlers = [], {}
    class Capture(FakeCapture):
        def read(self):
            if signal.SIGINT in handlers:
                handlers[signal.SIGINT]()
            return super().read()
    def capture(index, backend):
        item = Capture()
        opened.append(item)
        return item
    monkeypatch.setattr('cv2.VideoCapture', capture)
    assert client.post('/setup/camera/check', headers=camera_headers(client)).status_code == 200
    opened.clear()
    assert finish(client).status_code == 200
    if blocked == 'revoked':
        replies['/api/kiosk/snapshot'] = RequestFailure(401)
    missing_date = [blocked == 'clock']
    original_open = OpenerDirector.open
    def without_date(opener, request, **kwargs):
        response = original_open(opener, request, **kwargs)
        if missing_date[0] and '/snapshot' in request.full_url:
            del response.headers['Date']
        return response
    monkeypatch.setattr(OpenerDirector, 'open', without_date)
    before = len(calls)
    held = app.test_cli_runner().invoke(args=['kiosk-run', '--no-preview'])
    assert held.exit_code != 0
    assert ('Enrollment belum tersedia' if blocked == 'empty' else 'Capture ditahan') in held.output
    assert opened == []
    assert len(calls) > before
    snapshot['snapshot_id'] = snapshot['clock_anchor_id'] = str(uuid4())
    replies['/api/kiosk/snapshot'] = snapshot
    missing_date[0] = False
    monkeypatch.setattr(signal, 'signal', lambda sig, handler: handlers.update({sig: handler}))
    running = app.test_cli_runner().invoke(args=['kiosk-run', '--no-preview'])
    assert running.exit_code == 0, running.output
    assert opened and all(item.released for item in opened)
    assert 'MENCATAT ABSENSI' in running.output
    assert not any('/events' in call.full_url for call in calls)


def test_preview_requires_grant_csrf_and_origin_and_completion_requires_admin_repair(wizard, tmp_path):
    app, client, _, calls = wizard
    app.config.update(KIOSK_LOCK_FILE=str(tmp_path / 'camera.lock'), KIOSK_STATE_FILE=str(tmp_path / 'camera.json'))
    anonymous = app.test_client()
    assert anonymous.get('/setup/camera').status_code == 403
    assert anonymous.post('/setup/camera/start', headers=camera_headers(anonymous)).status_code == 403
    assert client.post('/setup/camera/start', headers={'Origin': 'http://localhost'}).status_code == 302
    assert client.post('/setup/camera/start', headers={
        **camera_headers(client), 'Origin': 'https://evil.example'}).status_code == 403
    assert calls == []
    connect_and_select(client)
    assert finish(client).status_code == 200
    assert anonymous.get('/setup/camera/manage').status_code == 302
    assert anonymous.get('/setup/readiness').status_code == 302
    assert client.get('/setup/camera/manage').status_code == 200
    try:
        assert preview(client)
        assert client.get('/setup/readiness').json['checks']['camera'] is True
        assert client.get('/setup').status_code == 404
    finally:
        app.extensions['browser_camera'].close()


def test_trial_identity_cannot_gain_official_attendance_from_setup(wizard):
    from test_setup_organization import IDENTITY, ORGANIZATIONS, TOKEN
    app, client, replies, _ = wizard
    replies['/api/kiosk/organizations'] = {**ORGANIZATIONS, 'device': {**IDENTITY, 'purpose': 'camera_test'}}
    result = post(client, '/setup/credentials', token=TOKEN, base_url='https://server.example')
    assert result.status_code == 400
    status = client.get('/setup/status').json
    assert status['can_finish'] is False
    assert status['ready'] is False
    assert app.config['EOFFICE_DEVICE_TOKEN'] == ''
    assert app.config['EOFFICE_TEST_MODE'] is False




