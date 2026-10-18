"""First setup through the local CLI and Flask HTTP, with persistent storage."""
import re
import base64
import json
from pathlib import Path

import pytest

from app import create_app


def installation(path, **values):
    return create_app('testing', overrides={
        'FIRST_SETUP_ENABLED': True,
        'ATTENDANCE_MODE': 'eoffice',
        'SECRET_KEY': None,
        'SQLALCHEMY_DATABASE_URI': f'sqlite:///{path}/app.sqlite3',
        'SQLALCHEMY_ENGINE_OPTIONS': {},
        'CAMERA_SOURCE': 'browser',
        'LIVENESS_MODE': 'passive',
        'EOFFICE_BASE_URL': '',
        'EOFFICE_DEVICE_TOKEN': '',
        'EOFFICE_AURAFACE_PATH': '',
        'EOFFICE_PERIOCULAR_PATH': '',
        'EOFFICE_KEY_FILE': '',
        'WTF_CSRF_ENABLED': True,
        **values,
    }, instance_path=str(path))


def csrf(client, path='/setup'):
    response = client.get(path)
    match = re.search(r'name="csrf_token" value="([^"]+)"', response.get_data(as_text=True))
    assert match is not None, response.get_data(as_text=True)
    return match[1]


def test_incomplete_installation_opens_setup_and_holds_operational_access(tmp_path):
    app = installation(tmp_path)
    client = app.test_client()
    assert client.get('/').headers['Location'].endswith('/setup')
    page = client.get('/setup')
    assert page.status_code == 200
    assert 'Kode setup' in page.get_data(as_text=True)
    assert client.get('/setup/status').status_code == 403
    assert client.get('/kiosk/').headers['Location'].endswith('/setup')
    capture = client.post('/kiosk/camera/start')
    assert capture.status_code == 409
    assert capture.json['error'] == 'setup_required'
    result = app.test_cli_runner().invoke(args=['kiosk-run', '--no-preview'])
    assert result.exit_code != 0
    assert 'setup' in result.output.lower()
    assert app.extensions['browser_camera']._thread is None


def local_code(app):
    result = app.test_cli_runner().invoke(args=['setup-code'])
    assert result.exit_code == 0, result.output
    return result.output.strip()


def exchange(client, code):
    return client.post('/setup', data={'csrf_token': csrf(client), 'code': code})


def test_setup_code_is_local_single_use_and_requires_csrf(tmp_path, caplog):
    app = installation(tmp_path)
    code = local_code(app)
    assert len(code) >= 24
    client = app.test_client()
    assert client.post('/setup', data={'code': code}).status_code == 302
    assert client.get('/setup/status').status_code == 403
    assert exchange(client, 'wrong-setup-code').status_code == 403
    response = exchange(client, code)
    assert response.status_code == 302
    assert code not in response.headers['Location']
    status = client.get('/setup/status')
    assert status.status_code == 200
    assert status.json['steps']['storage'] is True
    assert status.json['pending'] == ['admin', 'organization', 'models', 'snapshot', 'camera']
    assert code not in status.get_data(as_text=True)
    assert code not in client.get('/setup').get_data(as_text=True)
    assert code not in caplog.text
    other = app.test_client()
    assert exchange(other, code).status_code == 403
    assert other.get('/setup/status').status_code == 403


def test_first_admin_uses_application_login_and_cannot_be_duplicated(tmp_path, caplog):
    app = installation(tmp_path)
    client = app.test_client()
    exchange(client, local_code(app))
    values = {'username': 'operator', 'password': 'AmanSekali123'}
    assert client.post('/setup/admin', data=values).status_code == 302
    assert client.get('/setup/status').json['steps']['admin'] is False
    values['csrf_token'] = csrf(client)
    bad = client.post('/setup/admin', data={**values, 'password': 'pendek'})
    assert bad.status_code == 400
    assert client.post('/setup/admin', data=values).status_code == 302
    assert client.get('/setup/status').json['steps']['admin'] is True
    page = client.get('/setup').get_data(as_text=True)
    assert 'operator' in page
    assert values['password'] not in page
    assert values['password'] not in caplog.text
    # A retry with another username cannot create another first admin.
    assert client.post('/setup/admin', data={**values, 'username': 'duplicate'}).status_code == 302
    other = app.test_client()
    response = other.post('/login', data={'csrf_token': csrf(other, '/login'), **{
        'username': 'operator', 'password': values['password']}})
    assert response.status_code == 302
    other = app.test_client()
    response = other.post('/login', data={'csrf_token': csrf(other, '/login'),
                                         'username': 'duplicate', 'password': values['password']})
    assert response.status_code == 401


def test_no_env_installation_resumes_and_replacement_code_revokes_old_access(tmp_path, monkeypatch):
    from config import ProductionConfig
    for key, value in {'ATTENDANCE_MODE': 'standalone', 'ATTENDANCE_MODE_CONFIGURED': False,
                       'DATABASE_CONFIGURED': False, 'SECRET_KEY': None}.items():
        monkeypatch.setattr(ProductionConfig, key, value)
    app = create_app('production', instance_path=str(tmp_path))
    client = app.test_client()
    exchange(client, local_code(app))
    assert client.post('/setup/admin', data={'csrf_token': csrf(client), 'username': 'kept_admin',
                                            'password': 'TidakDiubah123'}).status_code == 302
    pending_code = local_code(app)
    restarted = create_app('production', instance_path=str(tmp_path))
    assert restarted.config['ATTENDANCE_MODE'] == 'eoffice'
    resumed = restarted.test_client()
    replacement = local_code(restarted)
    assert exchange(resumed, pending_code).status_code == 403
    assert client.get('/setup/status').status_code == 403
    assert exchange(resumed, replacement).status_code == 302
    assert resumed.get('/setup/status').json['steps']['admin'] is True
    assert 'Buat admin' not in resumed.get('/setup').get_data(as_text=True)


def test_configuration_survives_restart_without_exposing_device_token(tmp_path, caplog):
    secret = 'token-that-must-remain-encrypted'
    app = installation(tmp_path, EOFFICE_BASE_URL='https://retained.example', EOFFICE_DEVICE_TOKEN=secret)
    local_code(app)
    restarted = create_app('testing', overrides={
        'FIRST_SETUP_ENABLED': True, 'ATTENDANCE_MODE': 'eoffice',
        'SQLALCHEMY_DATABASE_URI': f'sqlite:///{tmp_path}/app.sqlite3',
        'SQLALCHEMY_ENGINE_OPTIONS': {}, 'SECRET_KEY': None, 'WTF_CSRF_ENABLED': True,
    }, instance_path=str(tmp_path))
    client = restarted.test_client()
    exchange(client, local_code(restarted))
    status = client.get('/setup/status')
    assert status.json['configuration']['base_url'] == 'https://retained.example'
    assert restarted.config['EOFFICE_DEVICE_TOKEN'] == secret
    assert secret not in status.get_data(as_text=True)
    assert secret not in client.get('/setup').get_data(as_text=True)
    assert secret not in caplog.text
    assert secret.encode() not in (tmp_path / 'app.sqlite3').read_bytes()


def legacy_installation(path, *, event_bytes=None):
    from extensions import db
    from integration.store import IntegrationStore
    key = bytes(range(32))
    key_file = path / 'biometric_keys.json'
    key_file.write_text(json.dumps({'active_version': 1, 'keys': {'1': base64.b64encode(key).decode()}}))
    (path / 'secret_key').write_text('d' * 64)
    app = create_app('testing', overrides={
        'SQLALCHEMY_DATABASE_URI': f'sqlite:///{path}/app.sqlite3',
        'SQLALCHEMY_ENGINE_OPTIONS': {},
    }, instance_path=str(path))
    with app.app_context():
        db.create_all()
    result = app.test_cli_runner().invoke(args=['create-admin', '--username', 'legacy_admin',
        '--name', 'Admin Lama', '--password', 'PasswordLama123'])
    assert result.exit_code == 0, result.output
    snapshot = json.loads((Path(__file__).parent / 'fixtures/eoffice_snapshot.json').read_text())
    queue = IntegrationStore(path / 'eoffice.sqlite3', key)
    body = event_bytes if event_bytes is not None else b'{"event_id":"85f42b66-6a77-4c56-9efc-4231ff6eab42"}'
    queue.replace_snapshot(snapshot)
    queue.enqueue(body)
    queue.close()
    return key_file, key, body, snapshot


@pytest.mark.parametrize('complete', [False, True])
def test_legacy_installation_preserves_admin_key_snapshot_and_queue(tmp_path, complete):
    key_file, key, body, snapshot = legacy_installation(tmp_path)
    original_key = key_file.read_bytes()
    original_queue = (tmp_path / 'eoffice.sqlite3').read_bytes()
    models = {}
    if complete:
        for name in ('EOFFICE_AURAFACE_PATH', 'EOFFICE_PERIOCULAR_PATH'):
            path = tmp_path / name
            path.write_bytes(b'legacy-model')
            models[name] = str(path)
    app = installation(tmp_path, EOFFICE_BASE_URL='https://old.example',
                       EOFFICE_DEVICE_TOKEN='retained-device-token', **models)
    client = app.test_client()
    if complete:
        assert client.get('/').status_code == 200
        assert client.get('/setup').status_code == 404
        assert app.test_cli_runner().invoke(args=['setup-code']).exit_code != 0
    else:
        exchange(client, local_code(app))
        status = client.get('/setup/status').json
        assert status['steps']['admin'] is True
        assert status['configuration']['base_url'] == 'https://old.example'
        assert 'Buat admin' not in client.get('/setup').get_data(as_text=True)
    login_client = app.test_client()
    response = login_client.post('/login', data={'csrf_token': csrf(login_client, '/login'),
                                                'username': 'legacy_admin', 'password': 'PasswordLama123'})
    assert response.status_code == 302
    assert key_file.read_bytes() == original_key
    assert (tmp_path / 'eoffice.sqlite3').read_bytes() == original_queue
    from integration.store import IntegrationStore
    queue = IntegrationStore(tmp_path / 'eoffice.sqlite3', key)
    assert queue.pending()[0].body == body
    assert queue.pending()[0].event_id == '85f42b66-6a77-4c56-9efc-4231ff6eab42'
    assert queue.load_snapshot() == snapshot
    queue.close()


@pytest.mark.parametrize('key_contents', [None, b'damaged-old-key'])
def test_existing_storage_never_gets_replacement_biometric_key(tmp_path, key_contents):
    key_file, _, _, _ = legacy_installation(tmp_path)
    original_queue = (tmp_path / 'eoffice.sqlite3').read_bytes()
    if key_contents is None:
        key_file.unlink()
    else:
        key_file.write_bytes(key_contents)
    app = installation(tmp_path)
    client = app.test_client()
    exchange(client, local_code(app))
    assert client.get('/setup/status').json['steps']['storage'] is False
    assert (tmp_path / 'eoffice.sqlite3').read_bytes() == original_queue
    if key_contents is None:
        assert not key_file.exists()
    else:
        assert key_file.read_bytes() == key_contents
