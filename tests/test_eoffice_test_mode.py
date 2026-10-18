"""The camera trial cannot read, drain or acknowledge official attendance state."""
import copy
import json
import time

import pytest

from config import Config, validate_attendance_config
from integration.client import EofficeClient
from integration.clock import TrustedClock
from integration.protocol import validate_snapshot, validate_terminal_response
from integration.store import IntegrationStore, StoreIntegrityError
from integration.sync import SyncWorker
from kiosk.state import KioskState, public_view
from kiosk.worker import KioskWorker
from recognition.detector import QualitySettings
from recognition.liveness import LivenessSettings
from test_eoffice_identify import FACE, EYE, setup_scene
from test_eoffice_store import integration_config
from test_eoffice_sync import EVENT, SNAPSHOT, Response, Transport, terminal
from tracking.face_tracker import TrackerSettings

KEY = bytes(range(32))


def trial_result(event_id=EVENT['event_id']):
    return {'status': True, 'data': {'event_id': event_id, 'outcome': 'test_recorded',
        'reason': None, 'test_id': 1, 'official_attendance': False, 'purpose': 'camera_test',
        'received_at': '2026-10-18T23:00:00.000+08:00'}}


def test_snapshot_requires_explicit_test_context():
    trial = dict(SNAPSHOT, purpose='camera_test')
    assert validate_snapshot(trial, test_mode=True) is trial
    with pytest.raises(ValueError):
        validate_snapshot(trial)
    with pytest.raises(ValueError):
        validate_snapshot(SNAPSHOT, test_mode=True)
    for purpose in (None, 'official', True, ['camera_test']):
        with pytest.raises(ValueError):
            validate_snapshot(dict(trial, purpose=purpose), test_mode=True)


def test_terminal_test_contract_is_incompatible_with_official_contract():
    trial = trial_result()
    assert validate_terminal_response(EVENT['event_id'], trial, test_mode=True) is trial
    with pytest.raises(ValueError):
        validate_terminal_response(EVENT['event_id'], trial)
    with pytest.raises(ValueError):
        validate_terminal_response(EVENT['event_id'], terminal(EVENT['event_id']), test_mode=True)
    rejected = copy.deepcopy(trial)
    rejected['data'].update(outcome='rejected', test_id=None, reason='recognition_evidence_failed')
    assert validate_terminal_response(EVENT['event_id'], rejected, test_mode=True) is rejected


@pytest.mark.parametrize('field', ['event_id', 'outcome', 'reason', 'test_id', 'official_attendance', 'purpose', 'received_at'])
def test_terminal_test_result_requires_all_seven_fields(field):
    result = trial_result()
    del result['data'][field]
    with pytest.raises(ValueError):
        validate_terminal_response(EVENT['event_id'], result, test_mode=True)


@pytest.mark.parametrize('change', [
    {'event_id': '15f42b66-6a77-4c56-9efc-4231ff6eab42'}, {'outcome': 'recorded'},
    {'outcome': 'duplicate'}, {'test_id': True}, {'test_id': 0}, {'test_id': None},
    {'official_attendance': True}, {'official_attendance': 0}, {'purpose': 'official'},
    {'reason': 'unexpected'}, {'received_at': '2026-02-30T23:00:00+08:00'},
    {'session': 'check_in'}, {'ad_id': 1}, {'outcome': 'rejected', 'test_id': 1},
    {'outcome': 'rejected', 'test_id': None, 'reason': None},
    {'outcome': 'rejected', 'test_id': None, 'reason': 'invented'},
])
def test_malformed_test_result_never_becomes_terminal(change):
    result = trial_result()
    result['data'].update(change)
    with pytest.raises(ValueError):
        validate_terminal_response(EVENT['event_id'], result, test_mode=True)


@pytest.mark.parametrize('test_mode', [False, True])
def test_client_routes_only_matching_event_purpose_and_identifies_itself(monkeypatch, test_mode):
    client = EofficeClient('https://synthetic.invalid', 'test-token', test_mode=test_mode)
    snapshot = dict(SNAPSHOT, **({'purpose': 'camera_test'} if test_mode else {}))
    event = dict(EVENT, **({'purpose': 'camera_test'} if test_mode else {}))
    result = trial_result() if test_mode else terminal(EVENT['event_id'])
    requests = []
    def open_request(request, timeout):
        requests.append(request)
        return Response(json.dumps(snapshot if request.data is None else result).encode())
    monkeypatch.setattr(client._opener, 'open', open_request)
    assert client.snapshot() == snapshot
    original = json.dumps(event).encode()
    assert client.send(original) == result
    base = 'https://synthetic.invalid/api/kiosk-test' if test_mode else 'https://synthetic.invalid/api/kiosk'
    assert [request.full_url for request in requests] == [base + '/snapshot', base + '/events']
    assert all(request.get_header('User-agent') == 'EofficeCamera/1.0' for request in requests)
    assert requests[1].data is original
    crossed = dict(EVENT) if test_mode else dict(EVENT, purpose='camera_test')
    with pytest.raises(ValueError):
        client.send(json.dumps(crossed).encode())
    assert len(requests) == 2


@pytest.mark.parametrize('test_mode', [False, True])
def test_store_binds_reopen_snapshot_event_and_terminal_to_purpose(tmp_path, test_mode):
    path = tmp_path / 'queue.sqlite3'
    store = IntegrationStore(path, KEY, test_mode=test_mode)
    event = dict(EVENT, **({'purpose': 'camera_test'} if test_mode else {}))
    snapshot = dict(SNAPSHOT, **({'purpose': 'camera_test'} if test_mode else {}))
    result = trial_result() if test_mode else terminal(EVENT['event_id'])
    crossed_snapshot = dict(SNAPSHOT) if test_mode else dict(SNAPSHOT, purpose='camera_test')
    crossed_event = dict(EVENT) if test_mode else dict(EVENT, purpose='camera_test')
    crossed_result = terminal(EVENT['event_id']) if test_mode else trial_result()
    store.replace_snapshot(snapshot)
    original = json.dumps(event).encode()
    store.enqueue(original)
    with pytest.raises(ValueError):
        store.replace_snapshot(crossed_snapshot)
    with pytest.raises(ValueError):
        store.enqueue(json.dumps(crossed_event).encode())
    with pytest.raises(ValueError):
        store.finish(EVENT['event_id'], crossed_result)
    assert store.pending()[0].body == original
    store.finish(EVENT['event_id'], result)
    store.close()
    with pytest.raises(StoreIntegrityError):
        IntegrationStore(path, KEY, test_mode=not test_mode)
    restored = IntegrationStore(path, KEY, test_mode=test_mode)
    try:
        assert restored.load_snapshot() == snapshot
        assert restored.terminal(EVENT['event_id']) == result
        assert restored.pending() == []
        assert original not in path.read_bytes()
    finally:
        restored.close()


def test_cross_purpose_retained_event_is_never_sent(tmp_path):
    store = IntegrationStore(tmp_path / 'queue.sqlite3', KEY, test_mode=True)
    try:
        event_id = EVENT['event_id']
        # Authenticated old bytes still need purpose validation before replay.
        store._db.execute('INSERT INTO events(event_id,body) VALUES (?,?)',
                          (event_id, store._encrypt(json.dumps(EVENT).encode(), f'event:{event_id}')))
        store._db.commit()
        client = Transport([trial_result()])
        sync = SyncWorker(store, client)
        assert sync.run_once().storage == 'error'
        assert client.sent == []
    finally:
        store.close()


@pytest.mark.parametrize('kind', [FACE, EYE])
def test_real_recognition_queue_retry_and_test_result_ui(app, tmp_path, monkeypatch, kind):
    snap, models, _, _, detector, frame = setup_scene(kind)
    snap['purpose'] = 'camera_test'
    app.config.update(ATTENDANCE_MODE='eoffice', EOFFICE_TEST_MODE=True)
    path = tmp_path / 'eoffice-test.sqlite3'
    fs, ws = IntegrationStore(path, KEY, test_mode=True), IntegrationStore(path, KEY, test_mode=True)
    now = [0.]
    clock = TrustedClock(ws, wall_time=lambda: now[0])
    clock.anchor(snap['issued_at'], snap['snapshot_id'], 0.)
    fs.replace_snapshot(snap)
    transport = Transport()
    sync = SyncWorker(ws, transport, clock)
    sync._monotonic = lambda: now[0]
    worker = KioskWorker(app, None, detector, None, QualitySettings(), None,
        TrackerSettings(confirm_votes=3), None, KioskState(tmp_path / 'kiosk.json'),
        clock=lambda: now[0], wall_clock=lambda: now[0],
        liveness_settings=LivenessSettings(mode='passive'), integration_store=fs,
        sync_worker=sync, trusted_clock=clock, eoffice_models=models)
    monkeypatch.setattr('kiosk.worker.att.record', lambda *a, **k: pytest.fail('official attendance used'))
    try:
        worker.reload_templates(force=True)
        for at in (.1, .2):
            now[0] = at
            worker.step(frame)
            assert fs.pending() == []
        now[0] = .3
        worker.step(frame)
        event, = fs.pending()
        body = json.loads(event.body)
        assert body['purpose'] == 'camera_test'
        assert body['embedding_version'] == kind
        assert body['evidence'] == {'score': 1., 'second_score': -1., 'quality_ok': True, 'live': True, 'samples': 3}
        assert body['occurred_at'] == '2026-10-14T07:24:59.300+08:00'
        assert body['elapsed_ms'] == 300
        assert 'session' not in body and 'work_date' not in body
        assert worker.message['status'] == 'pending'
        transport.replies = [TimeoutError(), trial_result(event.event_id)]
        assert sync.run_once().events_sent == 0
        worker.reconcile_eoffice(force=True)
        assert worker.message['status'] == 'pending'
        now[0] = 1.3
        assert sync.run_once().events_sent == 1
        worker.reconcile_eoffice(force=True)
        worker.publish(force=True)
        view = public_view(worker.state.read(), now=now[0])
        assert worker.message['status'] == 'test_recorded'
        assert view['message']['text'].endswith('Uji tersimpan')
        assert view['message']['kind'] == 'info'
        assert view['purpose'] == 'camera_test'
        assert transport.sent == [event.body, event.body]
        assert fs.pending() == []
        assert fs.terminal(event.event_id) == trial_result(event.event_id)
        assert not (tmp_path / 'eoffice.sqlite3').exists()
    finally:
        fs.close()
        ws.close()


@pytest.mark.parametrize('bad', ['true', 'false', 1, None])
def test_test_flag_is_explicit_boolean(integration_config, bad):
    integration_config['EOFFICE_TEST_MODE'] = bad
    with pytest.raises(ValueError, match='EOFFICE_TEST_MODE'):
        validate_attendance_config(integration_config)


def test_test_flag_defaults_off_and_cannot_enable_standalone():
    assert Config.EOFFICE_TEST_MODE is False
    with pytest.raises(ValueError, match='EOFFICE_TEST_MODE'):
        validate_attendance_config({'ATTENDANCE_MODE': 'standalone', 'EOFFICE_TEST_MODE': True})


@pytest.mark.parametrize('status', ['check_in', 'check_out', 'duplicate', 'outside_schedule'])
def test_trial_public_view_never_displays_official_success(status):
    view = public_view({'mode': 'eoffice', 'purpose': 'camera_test', 'heartbeat': 1.,
        'message': {'status': status, 'at': 1., 'id': 'synthetic', 'first_name': ''}}, now=1.)
    assert view['message'] == {'kind': 'error', 'text': 'Hubungi operator', 'id': 'synthetic'}


@pytest.mark.parametrize('test_mode', [False, True])
def test_status_does_not_replay_recent_result_from_previous_mode(app, client, test_mode):
    app.config.update(ATTENDANCE_MODE='eoffice', EOFFICE_TEST_MODE=test_mode)
    state = {'mode': 'eoffice', 'camera': 'ok', 'message': {'at': time.time(), 'id': EVENT['event_id'],
        'first_name': 'Synthetic', 'status': 'check_in' if test_mode else 'test_recorded'}}
    if not test_mode:
        state['purpose'] = 'camera_test'
    KioskState(app.config['KIOSK_STATE_FILE']).publish(state)
    view = client.get('/kiosk/status').json
    assert view['message'] is None
    assert view['online'] is False


@pytest.mark.parametrize('test_mode,expected', [
    (False, 'Sejak layanan dimulai: 7 hasil tersimpan.'),
    (True, 'Uji sejak layanan dimulai: 7 hasil tersimpan.'),
])
def test_dashboard_labels_saved_count_with_its_official_or_trial_scope(app, auth_client, test_mode, expected):
    app.config.update(ATTENDANCE_MODE='eoffice', EOFFICE_TEST_MODE=test_mode)
    state = {'mode': 'eoffice', 'camera': 'ok', 'recorded': 7, 'employees': 1}
    if test_mode:
        state['purpose'] = 'camera_test'
    KioskState(app.config['KIOSK_STATE_FILE']).publish(state)
    response = auth_client.get('/admin')
    assert response.status_code == 200
    assert f'<p>{expected}</p>' in response.get_data(as_text=True)
