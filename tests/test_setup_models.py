"""Model preparation through protected Flask HTTP and controlled download sources."""
import io
import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
from pathlib import Path
import ssl
from threading import Event, Thread
from urllib.error import HTTPError

import pytest

from app import create_app
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from integration.store import IntegrationStore
from recognition import model_files as mf
from services.biometric_service import load_keyring
from services.setup_service import finish_setup
from test_setup import csrf, exchange, installation, local_code


CONTENT = b'synthetic runtime boundary only'
DIGEST = '6d54ef7d215a3505f4ef3324f237fc9ef05f5587959b9f8d796188f743c74aef'
TOKEN = 'a' * 64


@pytest.fixture
def model_sources(monkeypatch):
    # Synthetic source bytes stand in for large inference assets at the download boundary.
    names = ('YUNET', 'MINIFAS_V1SE', 'MINIFAS_V2', 'AURAFACE', 'MOBILEFACENET')
    for name in names:
        model = getattr(mf, name, mf.ModelFile(
            key='auraface_512' if name == 'AURAFACE' else 'mobilefacenet_v1_periocular',
            filename='glintr100.onnx' if name == 'AURAFACE' else 'mobilefacenet.tflite',
            sha256=DIGEST, size=len(CONTENT), license='', source='', urls=()))
        monkeypatch.setattr(mf, name, replace(model, sha256=DIGEST, size=len(CONTENT)), raising=False)
    from recognition import eoffice_models
    for version in eoffice_models.MODEL_HASHES:
        monkeypatch.setitem(eoffice_models.MODEL_HASHES, version, DIGEST)
    calls = []

    def open_source(request, timeout):
        calls.append(request)
        return io.BytesIO(CONTENT)

    return calls, open_source


def test_operator_prepares_verified_models_without_paths_or_exposed_credentials(tmp_path, model_sources, caplog):
    calls, open_source = model_sources
    app = installation(tmp_path, EOFFICE_BASE_URL='https://office.example', EOFFICE_DEVICE_TOKEN=TOKEN)
    app.extensions['setup_model_opener'] = open_source
    client = app.test_client()
    assert client.get('/setup/models').status_code == 403
    exchange(client, local_code(app))
    assert client.get('/setup/models').json['verified'] is False
    assert client.post('/setup/models', data={}).status_code == 302
    response = client.post('/setup/models', data={'csrf_token': csrf(client)}, headers={'Accept': 'application/json'})
    assert response.status_code == 200
    assert response.json['verified'] is True
    assert response.json['completed'] == response.json['total'] == 5
    assert response.json['ready'] is False
    assert response.json['compatible'] is None
    assert all(model['state'] == 'verified' for model in response.json['models'])
    assert app.config['MODEL_DIR'] == str(tmp_path / 'models')
    assert app.config['EOFFICE_AURAFACE_PATH'] == str(tmp_path / 'models/glintr100.onnx')
    assert app.config['EOFFICE_PERIOCULAR_PATH'] == str(tmp_path / 'models/mobilefacenet.tflite')
    assert len(calls) == 5
    authenticated = [request for request in calls if request.get_header('Authorization')]
    assert [request.full_url for request in authenticated] == [
        'https://office.example/api/kiosk/models/mobilefacenet_v1_periocular']
    assert authenticated[0].get_header('Authorization') == 'Bearer ' + TOKEN
    visible = response.get_data(as_text=True) + client.get('/setup').get_data(as_text=True) + caplog.text
    assert TOKEN not in visible
    assert str(tmp_path) not in visible
    assert app.config['ATTENDANCE_MODE'] == 'eoffice'


def test_wrong_hash_never_activates_a_model_or_changes_the_attendance_mode(tmp_path, model_sources):
    app = installation(tmp_path, EOFFICE_BASE_URL='https://office.example', EOFFICE_DEVICE_TOKEN=TOKEN)
    app.extensions['setup_model_opener'] = lambda request, timeout: io.BytesIO(b'x' * len(CONTENT))
    client = app.test_client()
    exchange(client, local_code(app))
    response = client.post('/setup/models', data={'csrf_token': csrf(client)}, headers={'Accept': 'application/json'})
    assert response.status_code == 503
    assert response.json['verified'] is False
    assert all('Sidik jari' in model['reason'] for model in response.json['models'])
    assert not list((tmp_path / 'models').glob('*.part'))
    assert not (tmp_path / 'models/mobilefacenet.tflite').exists()
    assert client.get('/setup/status').json['steps']['models'] is False
    assert app.config['ATTENDANCE_MODE'] == 'eoffice'


def test_existing_verified_models_are_reused_without_a_network_source(tmp_path, model_sources):
    directory = tmp_path / 'existing-models'
    directory.mkdir()
    for name in ('YUNET', 'MINIFAS_V1SE', 'MINIFAS_V2', 'AURAFACE', 'MOBILEFACENET'):
        (directory / getattr(mf, name).filename).write_bytes(CONTENT)
    app = installation(tmp_path, EOFFICE_BASE_URL='https://office.example', EOFFICE_DEVICE_TOKEN=TOKEN,
                       MODEL_DIR=str(directory), EOFFICE_AURAFACE_PATH=str(directory / 'glintr100.onnx'),
                       EOFFICE_PERIOCULAR_PATH=str(directory / 'mobilefacenet.tflite'))

    def unavailable(request, timeout):
        pytest.fail('Existing verified model should not use a network source')

    app.extensions['setup_model_opener'] = unavailable
    client = app.test_client()
    exchange(client, local_code(app))
    response = client.post('/setup/models', data={'csrf_token': csrf(client)}, headers={'Accept': 'application/json'})
    assert response.status_code == 200
    assert response.json['verified'] is True
    assert len(list(directory.iterdir())) == 5


def test_partial_download_resumes_after_restart_without_repeating_valid_models_or_admin(tmp_path, model_sources):
    app = installation(tmp_path, EOFFICE_BASE_URL='https://office.example', EOFFICE_DEVICE_TOKEN=TOKEN)

    def interrupted(request, timeout):
        return io.BytesIO(CONTENT[:6] if 'glintr100.onnx' in request.full_url else CONTENT)

    app.extensions['setup_model_opener'] = interrupted
    client = app.test_client()
    exchange(client, local_code(app))
    assert client.post('/setup/admin', data={'csrf_token': csrf(client), 'username': 'operator',
                                            'password': 'PasswordTetap123'}).status_code == 302
    original_key = (tmp_path / 'biometric_keys.json').read_bytes()
    original_secret = (tmp_path / 'secret_key').read_bytes()
    response = client.post('/setup/models', data={'csrf_token': csrf(client)}, headers={'Accept': 'application/json'})
    assert response.status_code == 503
    assert response.json['completed'] == 4
    failed = next(model for model in response.json['models'] if model['key'] == 'auraface_512')
    assert 'tidak lengkap' in failed['reason']
    assert failed['downloaded_bytes'] == 6
    assert not (tmp_path / 'models/glintr100.onnx').exists()
    assert not list((tmp_path / 'models').glob('*.part'))
    restarted = create_app('testing', overrides={
        'FIRST_SETUP_ENABLED': True, 'ATTENDANCE_MODE': 'eoffice', 'SECRET_KEY': None,
        'SQLALCHEMY_DATABASE_URI': f'sqlite:///{tmp_path}/app.sqlite3', 'SQLALCHEMY_ENGINE_OPTIONS': {},
        'WTF_CSRF_ENABLED': True,
    }, instance_path=str(tmp_path))
    calls, open_source = model_sources
    restarted.extensions['setup_model_opener'] = open_source
    resumed = restarted.test_client()
    exchange(resumed, local_code(restarted))
    assert resumed.get('/setup/status').json['steps']['admin'] is True
    assert resumed.get('/setup/models').json['completed'] == 4
    retried = resumed.post('/setup/models', data={'csrf_token': csrf(resumed)}, headers={'Accept': 'application/json'})
    assert retried.json['verified'] is True
    assert len(calls) == 1
    assert 'glintr100.onnx' in calls[0].full_url
    assert restarted.config['EOFFICE_DEVICE_TOKEN'] == TOKEN
    assert (tmp_path / 'biometric_keys.json').read_bytes() == original_key
    assert (tmp_path / 'secret_key').read_bytes() == original_secret


def test_missing_private_server_artifact_is_actionable_and_retry_preserves_other_models(tmp_path, model_sources):
    app = installation(tmp_path, EOFFICE_BASE_URL='https://office.example', EOFFICE_DEVICE_TOKEN=TOKEN)

    def unavailable(request, timeout):
        if '/api/kiosk/models/' in request.full_url:
            raise HTTPError(request.full_url, 503, 'private internal details ' + TOKEN, {}, None)
        return io.BytesIO(CONTENT)

    app.extensions['setup_model_opener'] = unavailable
    client = app.test_client()
    exchange(client, local_code(app))
    response = client.post('/setup/models', data={'csrf_token': csrf(client)}, headers={'Accept': 'application/json'})
    assert response.status_code == 503
    assert response.json['completed'] == 4
    missing = response.json['models'][-1]
    assert 'belum tersedia' in missing['reason']
    assert 'admin server' in missing['recovery'] and 'MobileFaceNet' in missing['recovery']
    assert TOKEN not in response.get_data(as_text=True)
    assert 'private internal details' not in response.get_data(as_text=True)
    calls, open_source = model_sources
    app.extensions['setup_model_opener'] = open_source
    retried = client.post('/setup/models', data={'csrf_token': csrf(client)}, headers={'Accept': 'application/json'})
    assert retried.json['verified'] is True
    assert [request.full_url for request in calls] == [
        'https://office.example/api/kiosk/models/mobilefacenet_v1_periocular']


def test_model_policy_mismatch_remains_unready_and_preserves_the_encrypted_queue(tmp_path, model_sources):
    app = installation(tmp_path, EOFFICE_BASE_URL='https://office.example', EOFFICE_DEVICE_TOKEN=TOKEN)
    app.extensions['setup_model_opener'] = model_sources[1]
    client = app.test_client()
    exchange(client, local_code(app))
    client.post('/setup/models', data={'csrf_token': csrf(client)}, headers={'Accept': 'application/json'})
    snapshot = json.loads((Path(__file__).parent / 'fixtures/eoffice_snapshot.json').read_text())
    for model in snapshot['models'].values():
        model['weights_sha256'] = DIGEST
    with app.app_context():
        active, keys = load_keyring(app.config['EOFFICE_KEY_FILE'])
    store = IntegrationStore(tmp_path / 'eoffice.sqlite3', keys[active])
    body = b'{"event_id":"85f42b66-6a77-4c56-9efc-4231ff6eab42"}'
    store.enqueue(body)
    store.replace_snapshot(snapshot)
    assert client.get('/setup/models').json['ready'] is True
    snapshot['models']['auraface_512']['weights_sha256'] = 'b' * 64
    snapshot['snapshot_id'] = snapshot['clock_anchor_id'] = 'f8ef07e0-9029-47d1-835d-1e9728bf9889'
    store.replace_snapshot(snapshot)
    response = client.get('/setup/models')
    assert response.json['verified'] is True
    assert response.json['compatible'] is False
    assert response.json['ready'] is False
    assert 'metadata enrollment' in response.json['compatibility_reason']
    assert app.config['ATTENDANCE_MODE'] == 'eoffice'
    assert [(event.event_id, event.body) for event in store.pending()] == [
        ('85f42b66-6a77-4c56-9efc-4231ff6eab42', body)]
    store.close()


def test_concurrent_preparation_is_rejected_and_in_progress_state_is_visible(tmp_path, model_sources):
    entered, resume = Event(), Event()
    app = installation(tmp_path, EOFFICE_BASE_URL='https://office.example', EOFFICE_DEVICE_TOKEN=TOKEN)

    def paused_source(request, timeout):
        entered.set()
        assert resume.wait(5)
        return io.BytesIO(CONTENT)

    app.extensions['setup_model_opener'] = paused_source
    client = app.test_client()
    exchange(client, local_code(app))
    token = csrf(client)
    other = app.test_client()
    other.set_cookie('session', client.get_cookie('session').value)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(client.post, '/setup/models', data={'csrf_token': token},
                             headers={'Accept': 'application/json'})
        try:
            assert entered.wait(5)
            assert other.get('/setup/models').json['models'][0]['state'] == 'downloading'
            response = other.post('/setup/models', data={'csrf_token': token}, headers={'Accept': 'application/json'})
            assert response.status_code == 409
            assert response.json['error'] == 'model_download_busy'
        finally:
            resume.set()
        assert future.result(timeout=5).json['verified'] is True


def test_after_setup_only_an_active_local_admin_can_retry_models(tmp_path, model_sources):
    app = installation(tmp_path, EOFFICE_BASE_URL='https://office.example', EOFFICE_DEVICE_TOKEN=TOKEN)
    app.extensions['setup_model_opener'] = model_sources[1]
    admin = app.test_client()
    exchange(admin, local_code(app))
    admin.post('/setup/admin', data={'csrf_token': csrf(admin), 'username': 'operator',
                                    'password': 'PasswordTetap123'})
    with app.app_context():
        finish_setup()
    assert admin.get('/setup').status_code == 404
    assert admin.get('/setup/models/manage').status_code == 200
    response = admin.post('/setup/models', data={'csrf_token': csrf(admin, '/setup/models/manage')},
                          headers={'Accept': 'application/json'})
    assert response.json['verified'] is True
    anonymous = app.test_client()
    assert anonymous.get('/setup/models').status_code == 302
    assert anonymous.post('/setup/models', data={'csrf_token': csrf(anonymous, '/login')}).status_code == 302


@pytest.fixture
def https_model_source(tmp_path, monkeypatch):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'localhost')])
    now = datetime.now(timezone.utc)
    certificate = (x509.CertificateBuilder().subject_name(subject).issuer_name(subject)
                   .public_key(key.public_key()).serial_number(x509.random_serial_number())
                   .not_valid_before(now - timedelta(minutes=1)).not_valid_after(now + timedelta(hours=1))
                   .add_extension(x509.SubjectAlternativeName([x509.DNSName('localhost'),
                       x509.IPAddress(ipaddress.ip_address('127.0.0.1'))]), critical=False)
                   .sign(key, hashes.SHA256()))
    cert_path, key_path = tmp_path / 'source-cert.pem', tmp_path / 'source-key.pem'
    cert_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                          serialization.NoEncryption()))
    requests = []

    class Source(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append((self.path, self.headers.get('Authorization')))
            if self.path.startswith('/api/kiosk/models/'):
                self.send_response(302)
                self.send_header('Location', f'https://localhost:{self.server.server_port}/leak')
                self.end_headers()
            else:
                self.send_response(200)
                self.send_header('Content-Length', str(len(CONTENT)))
                self.end_headers()
                self.wfile.write(CONTENT)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Source)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert_path, key_path)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv('SSL_CERT_FILE', str(cert_path))
    try:
        yield f'https://127.0.0.1:{server.server_port}', requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join(2)


def test_authenticated_model_download_never_follows_a_credential_redirect(
        tmp_path, model_sources, https_model_source, monkeypatch):
    base, requests = https_model_source
    for name in ('YUNET', 'MINIFAS_V1SE', 'MINIFAS_V2', 'AURAFACE'):
        monkeypatch.setattr(mf, name, replace(getattr(mf, name), urls=(base + '/public-model',)))
    app = installation(tmp_path, EOFFICE_BASE_URL=base, EOFFICE_DEVICE_TOKEN=TOKEN)
    client = app.test_client()
    exchange(client, local_code(app))
    response = client.post('/setup/models', data={'csrf_token': csrf(client)}, headers={'Accept': 'application/json'})
    assert response.status_code == 503
    assert response.json['completed'] == 4
    assert response.json['ready'] is False
    assert requests == [('/public-model', None)] * 4 + [
        ('/api/kiosk/models/mobilefacenet_v1_periocular', 'Bearer ' + TOKEN)]
    assert not (tmp_path / 'models/mobilefacenet.tflite').exists()


def test_streamed_model_is_bounded_by_the_pinned_size(tmp_path, model_sources):
    class Oversized(io.BytesIO):
        def read(self, size):
            assert size <= len(CONTENT) + 1
            return b'x' * size

    app = installation(tmp_path, EOFFICE_BASE_URL='https://office.example', EOFFICE_DEVICE_TOKEN=TOKEN)
    app.extensions['setup_model_opener'] = lambda request, timeout: Oversized()
    client = app.test_client()
    exchange(client, local_code(app))
    response = client.post('/setup/models', data={'csrf_token': csrf(client)}, headers={'Accept': 'application/json'})
    assert response.status_code == 503
    assert response.json['completed'] == 0
    assert not list((tmp_path / 'models').glob('*.part'))


def test_trial_credential_is_never_sent_to_operational_model_distribution(tmp_path, model_sources):
    calls, open_source = model_sources
    app = installation(tmp_path, EOFFICE_BASE_URL='https://office.example', EOFFICE_DEVICE_TOKEN=TOKEN,
                       EOFFICE_TEST_MODE=True)
    app.extensions['setup_model_opener'] = open_source
    client = app.test_client()
    exchange(client, local_code(app))
    response = client.post('/setup/models', data={'csrf_token': csrf(client)}, headers={'Accept': 'application/json'})
    assert response.status_code == 503
    assert response.json['completed'] == 4
    assert 'token uji' in response.json['models'][-1]['reason']
    assert len(calls) == 4
    assert all(request.get_header('Authorization') is None for request in calls)
    assert app.config['EOFFICE_TEST_MODE'] is True
