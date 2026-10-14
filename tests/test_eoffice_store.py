import copy
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from integration.store import IntegrationStore, StoreIntegrityError


def test_pending_bytes_survive_reopen(tmp_path):
    path = tmp_path / 'eoffice.sqlite3'
    key = bytes(range(32))
    first = IntegrationStore(path, key)
    payload = b'{"event_id":"85f42b66-6a77-4c56-9efc-4231ff6eab42"}'
    first.enqueue(payload)
    first.close()
    second = IntegrationStore(path, key)
    assert second.pending()[0].body == payload
    second.close()

KEY = bytes(range(32))
EVENT_ID = '85f42b66-6a77-4c56-9efc-4231ff6eab42'
BODY = json.dumps({'event_id': EVENT_ID}, separators=(',', ':')).encode()


@pytest.fixture
def snapshot():
    return json.loads((Path(__file__).parent / 'fixtures/eoffice_snapshot.json').read_text())


@pytest.fixture
def store(tmp_path):
    value = IntegrationStore(tmp_path / 'eoffice.sqlite3', KEY)
    yield value
    value.close()


def terminal(outcome='recorded'):
    return {'status': True, 'data': {'event_id': EVENT_ID, 'outcome': outcome,
            'reason': None, 'ad_id': 42, 'work_date': '2026-10-14',
            'session': 'check_in', 'received_at': '2026-10-14T07:25:00+08:00'}}


def test_snapshot_replace_validates_full_policy_and_is_atomic(store, snapshot):
    assert store.load_snapshot() is None
    store.replace_snapshot(snapshot)
    old = copy.deepcopy(snapshot)
    snapshot['employees'][0]['display_name'] = 'Caller mutation'
    assert store.load_snapshot() == old
    for bad in [None, b'{"employees":', {'employees': []}, {**old, 'unknown': True}]:
        with pytest.raises(ValueError):
            store.replace_snapshot(bad)
        assert store.load_snapshot() == old
    bad = copy.deepcopy(old)
    del bad['models']['auraface_512']['liveness']
    with pytest.raises(ValueError):
        store.replace_snapshot(bad)
    assert store.load_snapshot() == old
    next_snapshot = {**old, 'snapshot_id': 'a8812b33-4c8a-457e-9dda-379108ca2134', 'employees': []}
    store.replace_snapshot(next_snapshot)
    assert store.load_snapshot() == next_snapshot


def test_snapshot_reopen_encrypts_names_and_templates(tmp_path, snapshot):
    path = tmp_path / 'eoffice.sqlite3'
    first = IntegrationStore(path, KEY)
    first.replace_snapshot(snapshot)
    first.enqueue(BODY)
    first.close()
    raw = path.read_bytes()
    assert snapshot['employees'][0]['display_name'].encode() not in raw
    assert b'"samples"' not in raw
    assert BODY not in raw
    second = IntegrationStore(path, KEY)
    assert second.load_snapshot() == snapshot
    second.close()


def test_same_uuid_never_changes_bytes_even_after_finish(store):
    store.enqueue(BODY)
    store.enqueue(BODY)
    assert len(store.pending()) == 1
    with pytest.raises(ValueError):
        store.enqueue(BODY + b' ')
    store.finish(EVENT_ID, terminal())
    assert store.pending() == []
    store.enqueue(BODY)
    assert store.pending() == []
    with pytest.raises(ValueError):
        store.enqueue(BODY + b' ')


def test_pending_entries_and_bodies_are_immutable(store):
    store.enqueue(BODY)
    pending = store.pending()[0]
    with pytest.raises(FrozenInstanceError):
        pending.body = b'changed'
    assert type(pending.body) is bytes
    assert store.pending()[0].body == BODY


def test_wrong_key_is_an_operator_error_even_when_database_is_empty(tmp_path):
    path = tmp_path / 'eoffice.sqlite3'
    IntegrationStore(path, KEY).close()
    with pytest.raises(StoreIntegrityError):
        IntegrationStore(path, b'x' * 32)


@pytest.mark.parametrize('kind', ['event', 'snapshot'])
def test_tampered_ciphertext_fails_closed(tmp_path, snapshot, kind):
    path = tmp_path / 'eoffice.sqlite3'
    first = IntegrationStore(path, KEY)
    first.replace_snapshot(snapshot)
    first.enqueue(BODY)
    with sqlite3.connect(path) as db:
        if kind == 'event':
            db.execute("UPDATE events SET body=randomblob(length(body))")
        else:
            db.execute("UPDATE snapshot SET payload=randomblob(length(payload))")
    with pytest.raises(StoreIntegrityError):
        first.pending() if kind == 'event' else first.load_snapshot()
    first.close()


def test_two_senders_can_read_identical_rows_without_consuming_them(tmp_path):
    path = tmp_path / 'eoffice.sqlite3'
    first, second = IntegrationStore(path, KEY), IntegrationStore(path, KEY)
    first.enqueue(BODY)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda store: store.pending(), [first, second]))
    assert results[0] == results[1]
    assert results[0][0].body == BODY
    first.close()
    second.close()


def test_one_store_can_be_used_by_frame_and_worker_threads(store):
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda _: store.enqueue(BODY), range(20)))
        results = list(pool.map(lambda _: store.pending(), range(20)))
    assert all(rows[0].body == BODY for rows in results)


def test_database_full_preserves_pending_and_snapshot(tmp_path, snapshot):
    path = tmp_path / 'eoffice.sqlite3'
    store = IntegrationStore(path, KEY)
    store.replace_snapshot(snapshot)
    store.enqueue(BODY)
    # max_page_count is connection-local: force real SQLITE_FULL on the store connection.
    page_count = store._db.execute('PRAGMA page_count').fetchone()[0]
    store._db.execute(f'PRAGMA max_page_count={page_count}')
    large = json.dumps({'event_id': 'a8812b33-4c8a-457e-9dda-379108ca2134', 'padding': 'x' * 100000}).encode()
    with pytest.raises(sqlite3.OperationalError, match='full'):
        store.enqueue(large)
    assert [row.body for row in store.pending()] == [BODY]
    assert store.load_snapshot() == snapshot
    replacement = copy.deepcopy(snapshot)
    replacement['snapshot_id'] = 'a8812b33-4c8a-457e-9dda-379108ca2134'
    replacement['employees'][0]['samples'] = replacement['employees'][0]['samples'][:1] * 10
    with pytest.raises(sqlite3.OperationalError, match='full'):
        store.replace_snapshot(replacement)
    assert store.load_snapshot() == snapshot
    response = terminal()
    response['data']['reason'] = 'x' * 100000
    with pytest.raises(sqlite3.OperationalError, match='full'):
        store.finish(EVENT_ID, response)
    assert [row.body for row in store.pending()] == [BODY]
    store.close()


@pytest.mark.parametrize('response', [None, {}, {'status': True}, {'status': False, 'message': 'no'},
    {'status_code': 503, 'event_id': EVENT_ID, 'body': {'status': False, 'message': 'retry'}}])
def test_unvalidated_response_keeps_event_pending(store, response):
    store.enqueue(BODY)
    with pytest.raises(ValueError):
        store.finish(EVENT_ID, response)
    assert store.pending()[0].body == BODY


@pytest.mark.parametrize('outcome', ['recorded', 'duplicate', 'outside_schedule', 'rejected'])
def test_terminal_outcomes_are_durable(tmp_path, outcome):
    path = tmp_path / 'eoffice.sqlite3'
    store = IntegrationStore(path, KEY)
    store.enqueue(BODY)
    response = terminal(outcome)
    if outcome in ('outside_schedule', 'rejected'):
        response['data'].update(ad_id=None, work_date=None, session=None, reason='outside_schedule')
    store.finish(EVENT_ID, response)
    store.close()
    reopened = IntegrationStore(path, KEY)
    assert reopened.pending() == []
    reopened.close()


@pytest.mark.parametrize('status_code', [400, 409])
def test_terminal_http_errors_are_bound_to_original_event(store, status_code):
    store.enqueue(BODY)
    response = {'status_code': status_code, 'event_id': EVENT_ID,
                'body': {'status': False, 'message': 'Request rejected'}}
    bad = copy.deepcopy(response)
    bad['event_id'] = 'a8812b33-4c8a-457e-9dda-379108ca2134'
    with pytest.raises(ValueError):
        store.finish(EVENT_ID, bad)
    assert store.pending()[0].body == BODY
    store.finish(EVENT_ID, response)
    assert store.pending() == []


def test_eoffice_configuration_rejects_incomplete_startup_before_database_connect(monkeypatch):
    from app import create_app
    from extensions import db
    monkeypatch.setattr(db, 'init_app', lambda _: pytest.fail('Configuration must fail before DB initialization'))
    with pytest.raises(ValueError, match='EOFFICE_BASE_URL'):
        create_app('testing', overrides={'ATTENDANCE_MODE': 'eoffice', 'EOFFICE_BASE_URL': ''})


def test_standalone_configuration_defaults_remain_unchanged():
    from config import Config
    assert Config.ATTENDANCE_MODE == 'standalone'
    assert Config.LIVENESS_MODE == 'passive+challenge'


@pytest.fixture
def integration_config(tmp_path):
    import base64
    key_path = tmp_path / 'keys.json'
    key_path.write_text(json.dumps({'active_version': 1, 'keys': {'1': base64.b64encode(KEY).decode()}}))
    auraface, periocular = tmp_path / 'auraface.onnx', tmp_path / 'periocular.tflite'
    auraface.write_bytes(b'model-validation-is-task-9')
    periocular.write_bytes(b'model-validation-is-task-9')
    return {'ATTENDANCE_MODE': 'eoffice', 'EOFFICE_BASE_URL': 'https://eoffice.example',
            'EOFFICE_DEVICE_TOKEN': 'operator-provisioned-token', 'EOFFICE_KEY_FILE': str(key_path),
            'EOFFICE_AURAFACE_PATH': str(auraface), 'EOFFICE_PERIOCULAR_PATH': str(periocular),
            'LIVENESS_MODE': 'passive'}


@pytest.mark.parametrize('field,bad', [
    ('ATTENDANCE_MODE', 'other'), ('ATTENDANCE_MODE', None),
    ('EOFFICE_BASE_URL', 'http://eoffice.example'), ('EOFFICE_BASE_URL', 'https://'),
    ('EOFFICE_BASE_URL', 'https://user:password@eoffice.example'),
    ('EOFFICE_BASE_URL', 'https://eoffice.example?token=secret'),
    ('EOFFICE_BASE_URL', 'https://eoffice.example#fragment'),
    ('EOFFICE_BASE_URL', 'https://eoffice.example:wrong'),
    ('EOFFICE_DEVICE_TOKEN', ''), ('EOFFICE_DEVICE_TOKEN', 'token\r\nInjected: yes'),
    ('EOFFICE_AURAFACE_PATH', ''), ('EOFFICE_PERIOCULAR_PATH', '/nonexistent/model.tflite'),
    ('EOFFICE_KEY_FILE', ''), ('EOFFICE_KEY_FILE', '/nonexistent/key.json'),
    ('LIVENESS_MODE', 'passive+challenge'), ('LIVENESS_MODE', 'off'),
])
def test_eoffice_configuration_requires_secure_complete_inputs(integration_config, field, bad):
    from config import validate_attendance_config
    integration_config[field] = bad
    with pytest.raises(ValueError, match=field):
        validate_attendance_config(integration_config)


def test_eoffice_configuration_reuses_existing_key_file_validation(integration_config):
    from config import validate_attendance_config
    validate_attendance_config(integration_config)
    key_path = Path(integration_config['EOFFICE_KEY_FILE'])
    key_path.write_text('corrupt-keyring')
    with pytest.raises(ValueError, match='EOFFICE_KEY_FILE'):
        validate_attendance_config(integration_config)
    assert key_path.read_text() == 'corrupt-keyring'


def test_snapshot_uuid_cannot_replace_original_contents(store, snapshot):
    store.replace_snapshot(snapshot)
    changed = copy.deepcopy(snapshot)
    changed['employees'] = []
    with pytest.raises(ValueError, match='UUID'):
        store.replace_snapshot(changed)
    assert store.load_snapshot() == snapshot


@pytest.mark.parametrize('field,bad', [
    ('event_id', 'a8812b33-4c8a-457e-9dda-379108ca2134'),
    ('outcome', 'retry'), ('outcome', []), ('ad_id', True), ('ad_id', -1),
    ('session', 'break'), ('work_date', '2026-02-30'),
    ('received_at', '2026-10-14T07:25:00Z'), ('reason', []),
])
def test_malformed_terminal_data_preserves_pending(store, field, bad):
    store.enqueue(BODY)
    response = terminal()
    response['data'][field] = bad
    with pytest.raises(ValueError):
        store.finish(EVENT_ID, response)
    assert store.pending()[0].body == BODY


def test_http_error_with_mismatched_echo_cannot_finish(store):
    store.enqueue(BODY)
    response = {'status_code': 400, 'event_id': EVENT_ID, 'body': {
        'status': False, 'message': 'Rejected', 'event_id': 'a8812b33-4c8a-457e-9dda-379108ca2134'}}
    with pytest.raises(ValueError):
        store.finish(EVENT_ID, response)
    assert store.pending()[0].body == BODY


def test_durability_and_busy_timeout_with_real_writer_lock(tmp_path):
    path = tmp_path / 'eoffice.sqlite3'
    store = IntegrationStore(path, KEY)
    store.enqueue(BODY)
    assert store._db.execute('PRAGMA synchronous').fetchone()[0] == 2
    assert store._db.execute('PRAGMA busy_timeout').fetchone()[0] == 5000
    store._db.execute('PRAGMA busy_timeout=25')
    with sqlite3.connect(path) as competing:
        competing.execute('BEGIN IMMEDIATE')
        with pytest.raises(sqlite3.OperationalError, match='locked'):
            store.finish(EVENT_ID, terminal())
    assert store.pending()[0].body == BODY
    store.close()


def test_different_event_rows_cannot_swap_ciphertext(tmp_path):
    path = tmp_path / 'eoffice.sqlite3'
    store = IntegrationStore(path, KEY)
    other = 'a8812b33-4c8a-457e-9dda-379108ca2134'
    store.enqueue(BODY)
    store.enqueue(json.dumps({'event_id': other}).encode())
    with sqlite3.connect(path) as db:
        db.execute('UPDATE events SET body=(SELECT body FROM events WHERE event_id=?) WHERE event_id=?',
                   (EVENT_ID, other))
    with pytest.raises(StoreIntegrityError):
        store.pending()
    store.close()


@pytest.mark.parametrize('key', [b'', b'x' * 16, b'x' * 31, bytearray(32), 'x' * 32, None])
def test_store_requires_explicit_256_bit_key(tmp_path, key):
    with pytest.raises(ValueError):
        IntegrationStore(tmp_path / 'eoffice.sqlite3', key)
    assert not (tmp_path / 'eoffice.sqlite3').exists()


@pytest.mark.parametrize('body', [b'bad-json', b'[]', b'{}', b'{"event_id":"invalid"}',
                                 b'\xff', bytearray(BODY), {'event_id': EVENT_ID}])
def test_bad_event_input_cannot_enter_queue(store, body):
    with pytest.raises(ValueError):
        store.enqueue(body)
    assert store.pending() == []


@pytest.mark.parametrize('reopen', [False, True])
def test_unauthenticated_terminal_state_raises_instead_of_hiding_pending(tmp_path, reopen):
    path = tmp_path / 'eoffice.sqlite3'
    store = IntegrationStore(path, KEY)
    store.enqueue(BODY)
    assert store.pending()[0].body == BODY
    if reopen:
        store.close()
    with sqlite3.connect(path) as db:
        original = db.execute('SELECT body FROM events WHERE event_id=?', (EVENT_ID,)).fetchone()[0]
        db.execute('UPDATE events SET response=? WHERE event_id=?',
                   (b'not-authenticated-terminal-response', EVENT_ID))
    if reopen:
        store = IntegrationStore(path, KEY)
    with pytest.raises(StoreIntegrityError):
        store.pending()
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT body FROM events WHERE event_id=?', (EVENT_ID,)).fetchone()[0] == original
    store.close()


@pytest.mark.parametrize('reopen', [False, True])
def test_terminal_ciphertext_cannot_be_copied_to_another_event(tmp_path, reopen):
    path = tmp_path / 'eoffice.sqlite3'
    store = IntegrationStore(path, KEY)
    other_id = 'a8812b33-4c8a-457e-9dda-379108ca2134'
    other_body = json.dumps({'event_id': other_id}).encode()
    store.enqueue(BODY)
    store.enqueue(other_body)
    store.finish(EVENT_ID, terminal())
    assert [row.body for row in store.pending()] == [other_body]
    if reopen:
        store.close()
    with sqlite3.connect(path) as db:
        db.execute('UPDATE events SET response=(SELECT response FROM events WHERE event_id=?) WHERE event_id=?',
                   (EVENT_ID, other_id))
    if reopen:
        store = IntegrationStore(path, KEY)
    with pytest.raises(StoreIntegrityError):
        store.pending()
    store.close()


@pytest.mark.parametrize('invalid', ['not-json', 'wrong-event-id', 'not-terminal'])
def test_authenticated_response_still_requires_valid_uuid_bound_terminal_envelope(tmp_path, invalid):
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    path = tmp_path / 'eoffice.sqlite3'
    store = IntegrationStore(path, KEY)
    store.enqueue(BODY)
    response = terminal()
    if invalid == 'wrong-event-id':
        response['data']['event_id'] = 'a8812b33-4c8a-457e-9dda-379108ca2134'
    elif invalid == 'not-terminal':
        response = {'status': False, 'message': 'Infrastructure failure'}
    plaintext = b'not-json' if invalid == 'not-json' else json.dumps(response).encode()
    nonce = bytes(range(12))
    ciphertext = nonce + AESGCM(KEY).encrypt(nonce, plaintext, f'response:{EVENT_ID}'.encode())
    with sqlite3.connect(path) as db:
        db.execute('UPDATE events SET response=? WHERE event_id=?', (ciphertext, EVENT_ID))
    store.close()
    reopened = IntegrationStore(path, KEY)
    with pytest.raises(StoreIntegrityError):
        reopened.pending()
    with pytest.raises(StoreIntegrityError):
        reopened.finish(EVENT_ID, terminal())
    reopened.close()


def test_two_instances_preserve_first_terminal_response_and_original_bytes(tmp_path):
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    path = tmp_path / 'eoffice.sqlite3'
    first, second = IntegrationStore(path, KEY), IntegrationStore(path, KEY)
    first.enqueue(BODY)
    response = terminal()
    first.finish(EVENT_ID, response)
    with sqlite3.connect(path) as db:
        saved = db.execute('SELECT body,response FROM events WHERE event_id=?', (EVENT_ID,)).fetchone()
    later = terminal('rejected')
    later['data'].update(reason='enrollment_not_authorized', ad_id=None, work_date=None, session=None)
    second.finish(EVENT_ID, later)
    assert first.pending() == second.pending() == []
    first.close()
    second.close()
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT body,response FROM events WHERE event_id=?', (EVENT_ID,)).fetchone() == saved
    event_ciphertext, response_ciphertext = saved
    assert AESGCM(KEY).decrypt(event_ciphertext[:12], event_ciphertext[12:], f'event:{EVENT_ID}'.encode()) == BODY
    assert json.loads(AESGCM(KEY).decrypt(response_ciphertext[:12], response_ciphertext[12:],
                                        f'response:{EVENT_ID}'.encode())) == response
    reopened = IntegrationStore(path, KEY)
    assert reopened.pending() == []
    reopened.close()
