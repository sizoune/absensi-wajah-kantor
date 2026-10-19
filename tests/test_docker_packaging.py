"""Container defaults and startup checks; no daemon or real assets required."""
import json
import os
from pathlib import Path
import runpy
import shutil
import signal
import stat
import subprocess
from types import SimpleNamespace
from unittest.mock import Mock

import pytest


ROOT = Path(__file__).resolve().parents[1]


def startup():
    return runpy.run_path(str(ROOT / 'docker' / 'start.py'))


@pytest.mark.skipif(os.name == 'nt', reason='Container POSIX permissions are verified on Unix; Windows uses installer ACLs.')
def test_secret_is_created_once_with_private_permissions(tmp_path):
    path = tmp_path / 'secret_key'
    load_secret = startup()['load_secret']
    first = load_secret(path)
    assert len(first) == 64 and int(first, 16) >= 0
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    original = path.read_bytes()
    assert load_secret(path) == first
    assert path.read_bytes() == original


@pytest.mark.parametrize('existing', ['', 'damaged-key', 'f' * 63])
def test_existing_invalid_secret_fails_without_rekeying(tmp_path, existing):
    path = tmp_path / 'secret_key'
    path.write_text(existing)
    with pytest.raises(RuntimeError, match='secret_key'):
        startup()['load_secret'](path)
    assert path.read_text() == existing


def test_container_uses_local_metadata_without_attendance_or_enrollment(tmp_path, monkeypatch):
    from config import ProductionConfig
    from sqlalchemy import inspect, text
    from extensions import db

    monkeypatch.setattr(ProductionConfig, 'ATTENDANCE_MODE', 'standalone')
    monkeypatch.setattr(ProductionConfig, 'ATTENDANCE_MODE_CONFIGURED', True)
    app = startup()['create_container_app'](tmp_path)
    try:
        assert app.config['CAMERA_SOURCE'] == 'browser'
        assert app.config['ATTENDANCE_MODE'] == 'standalone'
        assert app.extensions['browser_camera']._thread is None
        assert app.config['SQLALCHEMY_DATABASE_URI'] == f'sqlite:///{tmp_path}/app.sqlite3'
        assert app.instance_path == str(tmp_path)
        with app.app_context():
            assert set(inspect(db.engine).get_table_names()) == {'users', 'audit_logs'}
            assert db.session.execute(text('SELECT count(*) FROM users')).scalar() == 0
            assert db.session.execute(text('SELECT count(*) FROM audit_logs')).scalar() == 0
        assert app.test_client().get('/health').json['database'] == 'ok'
        assert not (tmp_path / 'eoffice.sqlite3').exists()
    finally:
        app.extensions['browser_camera'].close()


def test_missing_secret_with_existing_database_fails_without_regeneration(tmp_path):
    app = startup()['create_container_app'](tmp_path)
    app.extensions['browser_camera'].close()
    original = (tmp_path / 'app.sqlite3').read_bytes()
    (tmp_path / 'secret_key').unlink()
    with pytest.raises(RuntimeError, match='secret_key'):
        startup()['create_container_app'](tmp_path)
    assert not (tmp_path / 'secret_key').exists()
    assert (tmp_path / 'app.sqlite3').read_bytes() == original


def test_missing_secret_with_existing_test_queue_fails_without_regeneration(tmp_path):
    queue = tmp_path / 'eoffice-test.sqlite3'
    queue.write_bytes(b'existing encrypted trial queue')
    with pytest.raises(RuntimeError, match='secret_key'):
        startup()['create_container_app'](tmp_path)
    assert not (tmp_path / 'secret_key').exists()
    assert queue.read_bytes() == b'existing encrypted trial queue'


@pytest.mark.parametrize('origin_ip', [None, '100.64.0.10'])
@pytest.mark.skipif(os.name == 'nt', reason='Docker shell wrappers require a POSIX host.')
def test_trial_wrapper_clears_ambient_settings_and_selects_test_queue_context(tmp_path, origin_ip):
    if not shutil.which('docker'):
        pytest.skip('Docker Compose CLI is unavailable')
    project = tmp_path / 'camera trial'
    (project / 'docker').mkdir(parents=True)
    for name in ('compose.yaml', 'docker/compose.eoffice.yaml', 'docker/compose.test.yaml',
                 'docker/test-compose.sh'):
        shutil.copy2(ROOT / name, project / name)
    selected = (ROOT / 'docker/compose.test.env.example').read_text()
    if origin_ip is None:
        selected = '\n'.join(line for line in selected.splitlines()
                             if not line.startswith('EOFFICE_PRODUCTION_ORIGIN_IP='))
    else:
        selected = selected.replace('replace-with-private-tailscale-origin-ip', origin_ip)
    (project / 'docker/compose.test.env').write_text(selected)
    environment = dict(os.environ, EOFFICE_TEST_MODE='false', ATTENDANCE_MODE='standalone',
        EOFFICE_BASE_URL='https://ambient.invalid', EOFFICE_DEVICE_TOKEN='ambient-token',
        EOFFICE_HOST_MODEL_DIR='/ambient/models', EOFFICE_HOST_KEY_FILE='/ambient/key.json',
        EOFFICE_PRODUCTION_ORIGIN_IP='100.64.0.99')
    result = subprocess.run([str(project / 'docker/test-compose.sh'), 'config', '--format', 'json'],
                            cwd=tmp_path, env=environment, capture_output=True, text=True)
    if origin_ip is None:
        assert result.returncode != 0
        assert 'EOFFICE_PRODUCTION_ORIGIN_IP' in result.stderr
        return
    result.check_returncode()
    service = json.loads(result.stdout)['services']['camera']
    assert service['environment']['ATTENDANCE_MODE'] == 'eoffice'
    assert service['environment']['EOFFICE_TEST_MODE'] == 'true'
    assert service['environment']['LIVENESS_MODE'] == 'passive'
    assert service['environment']['SSL_CERT_FILE'] == '/run/models/eoffice-test-ca-bundle.pem'
    assert service['environment']['EOFFICE_BASE_URL'] == 'https://api-e-office.tabalongkab.go.id'
    assert service['extra_hosts'] == ['api-e-office.tabalongkab.go.id=100.64.0.10']
    assert 'EOFFICE_PRODUCTION_ORIGIN_IP' not in service['environment']
    assert service['environment']['EOFFICE_DEVICE_TOKEN'] == 'replace-with-dedicated-expiring-test-device-token'
    assert all(mount['source'].startswith('/absolute/') for mount in service['volumes'] if mount['type'] == 'bind')


def test_waitress_does_not_spill_allowed_frames_to_disk(monkeypatch):
    import waitress
    from waitress.adjustments import Adjustments
    from waitress.buffers import TempfileBasedBuffer
    from waitress.parser import HTTPRequestParser

    create_server = Mock()
    monkeypatch.setattr(waitress, 'create_server', create_server)
    app = SimpleNamespace(config={})
    startup()['create_http_server'](app)
    assert create_server.call_args.args == (app,)
    settings = create_server.call_args.kwargs
    assert settings['host'] == '0.0.0.0' and settings['port'] == 5000
    assert settings['max_request_body_size'] == 524288
    assert settings['inbuf_overflow'] > settings['max_request_body_size']
    spills = Mock(side_effect=AssertionError('raw request spilled to a temporary file'))
    monkeypatch.setattr(TempfileBasedBuffer, 'newfile', spills)
    for chunked, size, rejected in ((False, 524287, False), (False, 524288, True),
                                     (True, 500000, False), (True, 524288, True)):
        parser = HTTPRequestParser(Adjustments(**settings))
        body = b'\0' * size
        if chunked:
            header = b'Transfer-Encoding: chunked\r\n'
            body = f'{size:x}\r\n'.encode() + body + b'\r\n0\r\n\r\n'
        else:
            header = f'Content-Length: {size}\r\n'.encode()
        try:
            parser.received(b'POST /kiosk/camera/frame HTTP/1.1\r\nHost: localhost\r\n' + header + b'\r\n')
            for offset in range(0, len(body), 8192):
                if parser.completed:
                    break
                parser.received(body[offset:offset + 8192])
            assert parser.completed
            assert (parser.error is not None) == rejected
            if rejected:
                assert parser.error.code == 413
            else:
                assert parser.get_body_stream().read() == b'\0' * size
        finally:
            parser.close()
    spills.assert_not_called()


@pytest.mark.parametrize('stop_signal', [signal.SIGTERM, signal.SIGINT])
def test_shutdown_signals_close_browser_runtime(stop_signal):
    module = startup()
    calls = []
    camera = SimpleNamespace(close=lambda: calls.append('camera'))
    app = SimpleNamespace(extensions={'browser_camera': camera}, config={'ATTENDANCE_MODE': 'standalone'})

    def run():
        signal.getsignal(stop_signal)(stop_signal, None)

    server = SimpleNamespace(run=run, close=lambda: calls.append('server'))
    module['main'].__globals__.update(
        create_container_app=lambda: app,
        create_http_server=lambda _: server,
    )
    previous = {value: signal.getsignal(value) for value in (signal.SIGTERM, signal.SIGINT)}
    try:
        with pytest.raises(SystemExit) as exc:
            module['main']()
        assert exc.value.code == 0
    finally:
        for value, handler in previous.items():
            signal.signal(value, handler)
    assert calls == ['camera', 'server']


@pytest.mark.parametrize('setup_required', [False, True])
def test_eoffice_container_resumes_service_before_serving(setup_required):
    module = startup()
    calls = []
    camera = SimpleNamespace(start_service=lambda: calls.append('service'),
                             close=lambda: calls.append('camera'))
    app = SimpleNamespace(extensions={'browser_camera': camera},
                          config={'ATTENDANCE_MODE': 'eoffice', 'SETUP_REQUIRED': setup_required})
    server = SimpleNamespace(run=lambda: calls.append('http'), close=lambda: calls.append('server'))
    module['main'].__globals__.update(create_container_app=lambda: app, create_http_server=lambda _: server)
    previous = {value: signal.getsignal(value) for value in (signal.SIGTERM, signal.SIGINT)}
    try:
        module['main']()
    finally:
        for value, handler in previous.items():
            signal.signal(value, handler)
    assert calls == (['http', 'camera', 'server'] if setup_required else ['service', 'http', 'camera', 'server'])


def test_default_compose_ignores_ambient_integration_settings():
    if not shutil.which('docker'):
        pytest.skip('Docker Compose CLI is unavailable')
    environment = dict(os.environ, ATTENDANCE_MODE='eoffice', CAMERA_SOURCE='native',
                       EOFFICE_TEST_MODE='true',
                       EOFFICE_DEVICE_TOKEN='ambient-must-not-enter-container',
                       DATABASE_URL='mysql://ambient', SECRET_KEY='ambient-secret')
    result = subprocess.run(['docker', 'compose', '-f', 'compose.yaml', 'config', '--format', 'json'],
                            cwd=ROOT, env=environment, capture_output=True, text=True, check=True)
    config = json.loads(result.stdout)
    service = config['services']['camera']
    assert service['environment']['ATTENDANCE_MODE'] == 'eoffice'
    assert service['environment']['EOFFICE_TEST_MODE'] == 'false'
    assert service['environment']['CAMERA_SOURCE'] == 'browser'
    assert 'EOFFICE_DEVICE_TOKEN' not in service['environment']
    assert 'SECRET_KEY' not in service['environment']
    assert 'DATABASE_URL' not in service['environment']
    assert not service.get('extra_hosts')
    assert 'SSL_CERT_FILE' not in service['environment']
    assert service['ports'][0]['host_ip'] == '127.0.0.1'
    assert service['ports'][0]['target'] == 5000
    assert service['volumes'][0]['type'] == 'volume'
    assert service['volumes'][0]['target'] == '/app/instance'
    assert service['restart'] == 'unless-stopped'
    assert not service.get('devices') and not service.get('privileged')


@pytest.mark.skipif(os.name == 'nt', reason='Docker shell wrappers require a POSIX host.')
def test_integration_wrapper_uses_selected_file_over_ambient_settings(tmp_path):
    if not shutil.which('docker'):
        pytest.skip('Docker Compose CLI is unavailable')
    project = tmp_path / 'camera trial'
    (project / 'docker').mkdir(parents=True)
    for name in ('compose.yaml', 'docker/compose.eoffice.yaml', 'docker/eoffice-compose.sh'):
        shutil.copy2(ROOT / name, project / name)
    shutil.copy2(ROOT / 'docker/compose.env.example', project / 'docker/compose.env')
    environment = dict(os.environ,
                       EOFFICE_TEST_MODE='true',
                       EOFFICE_BASE_URL='https://ambient-production.example.invalid',
                       EOFFICE_DEVICE_TOKEN='ambient-production-token',
                       EOFFICE_HOST_MODEL_DIR='/ambient-production/models',
                       EOFFICE_HOST_KEY_FILE='/ambient-production/key.json')
    # Run outside the copied CAM root to check wrapper path resolution too.
    result = subprocess.run([str(project / 'docker/eoffice-compose.sh'), 'config', '--format', 'json'],
                            cwd=tmp_path, env=environment, capture_output=True, text=True, check=True)
    service = json.loads(result.stdout)['services']['camera']
    assert service['environment']['ATTENDANCE_MODE'] == 'eoffice'
    assert service['environment']['EOFFICE_TEST_MODE'] == 'false'
    assert service['environment']['LIVENESS_MODE'] == 'passive'
    assert service['environment']['EOFFICE_BASE_URL'] == 'https://staging.example.invalid'
    assert not service.get('extra_hosts')
    assert 'SSL_CERT_FILE' not in service['environment']
    assert service['environment']['EOFFICE_DEVICE_TOKEN'] == 'replace-with-authorized-staging-device-token'
    assets = [mount for mount in service['volumes'] if mount['type'] == 'bind']
    assert {mount['target'] for mount in assets} == {'/run/models', '/run/keys/eoffice-key.json'}
    assert {mount['source'] for mount in assets} == {
        '/absolute/path/outside-project/eoffice-models',
        '/absolute/path/outside-project/eoffice-secrets/eoffice-key.json',
    }
    assert all(mount['read_only'] and not mount['bind']['create_host_path'] for mount in assets)
    assert any(mount['type'] == 'volume' and mount['target'] == '/app/instance'
               for mount in service['volumes'])


def test_compose_host_port_override_stays_on_loopback():
    if not shutil.which('docker'):
        pytest.skip('Docker Compose CLI is unavailable')
    result = subprocess.run(['docker', 'compose', '-f', 'compose.yaml', 'config', '--format', 'json'],
                            cwd=ROOT, env=dict(os.environ, CAMERA_HOST_PORT='5107'),
                            capture_output=True, text=True, check=True)
    port = json.loads(result.stdout)['services']['camera']['ports'][0]
    assert port['host_ip'] == '127.0.0.1' and str(port['published']) == '5107'


def test_docker_context_starts_with_source_allowlist():
    patterns = [line.strip() for line in (ROOT / '.dockerignore').read_text().splitlines()
                if line.strip() and not line.startswith('#')]
    assert patterns[0] == '**'
    assert '!app.py' in patterns and '!app_version.py' in patterns and '!docker/start.py' in patterns
    assert '!docker/eoffice-compose.sh' not in patterns
    assert not any(line in patterns for line in ('!.env', '!instance/', '!model_files/', '!tests/'))
    for package in ('camera', 'integration', 'kiosk', 'maintenance', 'perf', 'recognition',
                    'routes', 'services', 'tracking'):
        # Re-exclude a permitted directory's contents before allowing only source.
        assert patterns.index(f'!{package}/') < patterns.index(f'{package}/**')
        assert patterns.index(f'{package}/**') < patterns.index(f'!{package}/*.py')
