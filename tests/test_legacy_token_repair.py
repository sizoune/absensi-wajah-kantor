"""Legacy token recovery binds old queue ownership through authenticated server history."""
import copy
import json
from urllib.parse import urlsplit
from uuid import uuid4

import pytest

from test_eoffice_sync import EVENT, Response, SNAPSHOT, terminal
from test_scope_change import store_at
from test_setup import csrf, installation, legacy_installation
from test_setup_organization import IDENTITY, ORGANIZATIONS, TOKEN
from test_token_repair import REPLACEMENT, repair


@pytest.fixture
def legacy_device(tmp_path, monkeypatch):
    body = json.dumps(EVENT).encode()
    key_file, _, _, _ = legacy_installation(tmp_path, event_bytes=body)
    replies = {'/api/kiosk/organizations': copy.deepcopy(ORGANIZATIONS),
               '/api/kiosk/recovery/snapshots': {'device': dict(IDENTITY), 'snapshot_ids': [SNAPSHOT['snapshot_id']]},
               '/api/kiosk/snapshot': copy.deepcopy(SNAPSHOT), '/api/kiosk/events': terminal(EVENT['event_id'])}
    calls = []

    def remote(opener, request, **kwargs):
        calls.append(request)
        value = replies[urlsplit(request.full_url).path]
        if callable(value):
            value = value(request)
        if isinstance(value, Exception):
            raise value
        return Response(value if isinstance(value, bytes) else json.dumps(value).encode())

    monkeypatch.setattr('urllib.request.OpenerDirector.open', remote)
    models = {}
    for name in ('EOFFICE_AURAFACE_PATH', 'EOFFICE_PERIOCULAR_PATH'):
        path = tmp_path / name
        path.write_bytes(b'existing-legacy-model')
        models[name] = str(path)
    app = installation(tmp_path, EOFFICE_BASE_URL='https://legacy.example', EOFFICE_DEVICE_TOKEN=TOKEN,
                       KIOSK_LOCK_FILE=str(tmp_path / 'camera.lock'), KIOSK_STATE_FILE=str(tmp_path / 'camera.json'), **models)
    client = app.test_client()
    assert client.get('/setup').status_code == 404
    assert app.config.get('EOFFICE_DEVICE_ID') is None and app.config.get('EOFFICE_SCOPE_TYPE') is None
    assert client.post('/login', data={'csrf_token': csrf(client, '/login'),
        'username': 'legacy_admin', 'password': 'PasswordLama123'}).status_code == 302
    yield app, client, replies, calls, key_file, body
    app.extensions['browser_camera'].close()


def test_legacy_replacement_proves_history_before_binding_and_sending_original_queue(legacy_device, tmp_path):
    app, client, _, calls, key_file, body = legacy_device
    key = key_file.read_bytes()
    response = repair(client)
    assert response.status_code == 200
    assert response.json['device_id'] == IDENTITY['device_id'] and response.json['capture_held'] is False
    assert app.config['EOFFICE_DEVICE_TOKEN'] == REPLACEMENT
    assert app.config['EOFFICE_SCOPE_TYPE'] == 'all_skpd' and app.config['EOFFICE_SUBUNIT_ID'] is None
    assert key_file.read_bytes() == key
    proof = [call for call in calls if urlsplit(call.full_url).path.endswith('/recovery/snapshots')]
    assert len(proof) == 1 and json.loads(proof[0].data) == {'snapshot_ids': [SNAPSHOT['snapshot_id']]}
    assert [call.data for call in calls if urlsplit(call.full_url).path.endswith('/events')] == [body]
    assert all(call.get_header('Authorization') == 'Bearer ' + REPLACEMENT for call in calls)
    store = store_at(tmp_path)
    try:
        assert store.pending() == [] and store.terminal(EVENT['event_id']) == terminal(EVENT['event_id'])
    finally:
        store.close()


@pytest.mark.parametrize('rejection', ['foreign', 'partial', 'scope_changed', 'device_changed'])
def test_legacy_unproven_history_preserves_old_secret_identity_key_and_ciphertext(legacy_device, tmp_path, rejection):
    from integration.client import RequestFailure
    app, client, replies, calls, key_file, _ = legacy_device
    store = store_at(tmp_path)
    other = str(uuid4())
    extra = {**EVENT, 'event_id': str(uuid4()), 'snapshot_id': other, 'clock_anchor_id': other}
    store.enqueue(json.dumps(extra).encode())
    store.close()
    encrypted = (tmp_path / 'eoffice.sqlite3').read_bytes()
    key = key_file.read_bytes()
    ids = sorted([SNAPSHOT['snapshot_id'], other])
    device = dict(IDENTITY)
    if rejection == 'foreign':
        result = RequestFailure(403)
    elif rejection == 'partial':
        result = {'device': device, 'snapshot_ids': ids[:1]}
    else:
        device['scope_type' if rejection == 'scope_changed' else 'device_id'] = 'parent' if rejection == 'scope_changed' else str(uuid4())
        result = {'device': device, 'snapshot_ids': ids}
    replies['/api/kiosk/recovery/snapshots'] = result
    response = repair(client)
    assert response.status_code in (400, 403, 409) and response.json['capture_held'] is True
    assert app.config['EOFFICE_DEVICE_TOKEN'] == TOKEN and app.config.get('EOFFICE_DEVICE_ID') is None
    assert app.config.get('EOFFICE_SCOPE_TYPE') is None
    assert key_file.read_bytes() == key and (tmp_path / 'eoffice.sqlite3').read_bytes() == encrypted
    proof = [call for call in calls if urlsplit(call.full_url).path.endswith('/recovery/snapshots')]
    assert len(proof) == 1 and json.loads(proof[0].data) == {'snapshot_ids': ids}
    assert not any(urlsplit(call.full_url).path.endswith(('/snapshot', '/events')) for call in calls)


def test_legacy_pending_without_snapshot_reference_cannot_be_claimed_by_candidate_identity(legacy_device, tmp_path):
    app, client, _, calls, key_file, _ = legacy_device
    store = store_at(tmp_path)
    store.enqueue(json.dumps({'event_id': str(uuid4())}).encode())
    store.close()
    original = (tmp_path / 'eoffice.sqlite3').read_bytes()
    key = key_file.read_bytes()
    response = repair(client)
    assert response.status_code == 409 and response.json['error'] == 'identity_evidence_unavailable'
    assert response.json['capture_held'] is True and calls == []
    assert app.config['EOFFICE_DEVICE_TOKEN'] == TOKEN and app.config.get('EOFFICE_DEVICE_ID') is None
    assert (tmp_path / 'eoffice.sqlite3').read_bytes() == original and key_file.read_bytes() == key


@pytest.mark.parametrize('change', ['device', 'scope', 'credential'])
def test_legacy_batched_proof_requires_one_unchanged_identity_and_context_before_any_binding(
        legacy_device, tmp_path, change):
    from integration.client import RequestFailure
    app, client, replies, calls, key_file, _ = legacy_device
    references = [SNAPSHOT['snapshot_id']]
    store = store_at(tmp_path)
    for _ in range(256):
        reference = str(uuid4())
        references.append(reference)
        event = {**EVENT, 'event_id': str(uuid4()), 'snapshot_id': reference, 'clock_anchor_id': reference}
        store.enqueue(json.dumps(event).encode())
    store.close()
    expected = sorted(references)
    original = (tmp_path / 'eoffice.sqlite3').read_bytes()
    key = key_file.read_bytes()
    batches = []

    def proof(request):
        ids = json.loads(request.data)['snapshot_ids']
        batches.append(ids)
        index = len(batches) - 1
        assert ids == expected[index * 256:(index + 1) * 256]
        if index and change == 'credential':
            return RequestFailure(401)
        device = dict(IDENTITY)
        if index:
            device['device_id' if change == 'device' else 'scope_type'] = str(uuid4()) if change == 'device' else 'parent'
        return {'device': device, 'snapshot_ids': ids}

    replies['/api/kiosk/recovery/snapshots'] = proof
    response = repair(client)
    assert response.status_code in (403, 409) and response.json['capture_held'] is True
    assert [len(batch) for batch in batches] == [256, 1]
    assert app.config['EOFFICE_DEVICE_TOKEN'] == TOKEN and app.config.get('EOFFICE_DEVICE_ID') is None
    assert app.config.get('EOFFICE_SCOPE_TYPE') is None
    assert (tmp_path / 'eoffice.sqlite3').read_bytes() == original and key_file.read_bytes() == key
    assert not any(urlsplit(call.full_url).path.endswith(('/snapshot', '/events')) for call in calls)


def test_legacy_owned_history_keeps_original_whole_skpd_scope_when_server_is_narrower(legacy_device, tmp_path):
    app, client, replies, calls, _, _ = legacy_device
    original = (tmp_path / 'eoffice.sqlite3').read_bytes()
    identity = {**IDENTITY, 'scope_type': 'parent'}
    replies['/api/kiosk/organizations']['device'] = identity
    replies['/api/kiosk/recovery/snapshots']['device'] = identity
    response = repair(client)
    assert response.status_code == 409 and response.json['error'] == 'scope_mismatch'
    assert response.json['capture_held'] is True
    assert app.config['EOFFICE_DEVICE_ID'] == IDENTITY['device_id'] and app.config['EOFFICE_SCOPE_TYPE'] == 'all_skpd'
    assert (tmp_path / 'eoffice.sqlite3').read_bytes() == original
    assert not any(urlsplit(call.full_url).path.endswith(('/snapshot', '/events')) for call in calls)
