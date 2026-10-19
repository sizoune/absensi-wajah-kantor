import json
from pathlib import Path
from uuid import uuid4

from integration.store import IntegrationStore
from integration.sync import SyncStatus, SyncWorker
from test_eoffice_sync import EVENT, terminal, Transport


def test_terminal_lookup_is_durable_and_validated(tmp_path):
    from integration.store import StoreIntegrityError
    import pytest
    store = IntegrationStore(tmp_path / 'state.sqlite3', bytes(32))
    store.enqueue(json.dumps(EVENT).encode())
    assert store.terminal(EVENT['event_id']) is None
    result = terminal(EVENT['event_id'])
    store.finish(EVENT['event_id'], result)
    assert store.terminal(EVENT['event_id']) == result
    store._db.execute('UPDATE events SET response=?', (b'bad',))
    store._db.commit()
    with pytest.raises(StoreIntegrityError):
        store.terminal(EVENT['event_id'])
    store.close()


def test_overflow_retains_unique_fresh_recorded_count(tmp_path):
    store = IntegrationStore(tmp_path / 'state.sqlite3', bytes(32))
    ids = [str(uuid4()) for _ in range(40)]
    for event_id in ids:
        store.enqueue(json.dumps(dict(EVENT, event_id=event_id)).encode())
    worker = SyncWorker(store, Transport([terminal(i) for i in sorted(ids)]))
    worker.run_once()
    assert worker.results.qsize() == 32
    assert worker.status.recorded_count == 40
    worker.run_once()
    assert worker.status.recorded_count == 40
    store.close()


import copy
from dataclasses import replace
from datetime import datetime
from types import SimpleNamespace

import pytest

from integration.clock import TrustedClock
from kiosk.state import KioskState, public_view
from kiosk.worker import KioskWorker
from recognition.detector import QualitySettings
from recognition.liveness import LivenessSettings
from test_eoffice_identify import setup_scene
from tracking.face_tracker import TrackerSettings


@pytest.fixture
def kiosk(app, tmp_path, monkeypatch):
    scene = setup_scene()
    snap, models, gallery, tracker, detector, frame = scene
    app.config['ATTENDANCE_MODE'] = 'eoffice'
    path = tmp_path / 'queue.sqlite3'
    fs, ws = IntegrationStore(path, bytes(32)), IntegrationStore(path, bytes(32))
    fs.replace_snapshot(snap)
    now = [0.]
    clock = TrustedClock(store=ws, wall_time=lambda: now[0])
    clock.anchor(snap['issued_at'], snap['snapshot_id'], 0.)
    sync = SyncWorker(ws, Transport(), clock)
    cam = SimpleNamespace(read=lambda: frame)
    worker = KioskWorker(app, cam, detector, None, QualitySettings(),
        lambda: pytest.fail('local gallery accessed'), TrackerSettings(confirm_votes=3), None,
        KioskState(tmp_path / 'kiosk.json'), clock=lambda: now[0], wall_clock=lambda: now[0],
        liveness_settings=LivenessSettings(mode='passive'), integration_store=fs,
        sync_worker=sync, trusted_clock=clock, eoffice_models=models)
    monkeypatch.setattr('kiosk.worker.att.record', lambda *a, **k: pytest.fail('standalone record called'))
    monkeypatch.setattr('kiosk.worker.fe.claim_next', lambda: pytest.fail('local enrollment claimed'))
    worker.reload_templates(force=True)
    yield worker, now, scene
    fs.close()
    if not sync.started:
        ws.close()


def capture(kiosk):
    worker, now, scene = kiosk
    for t in (.1, .2, .3):
        now[0] = t
        worker.run_once()
    return worker.integration_store.pending()


def test_queue_commits_before_pending_and_only_once(kiosk, monkeypatch):
    worker, now, scene = kiosk
    seen = []
    monkeypatch.setattr(worker.sync_worker, 'wake', lambda: seen.append(len(worker.integration_store.pending())))
    events = capture(kiosk)
    assert len(events) == 1 and seen == [1]
    body = json.loads(events[0].body)
    assert body['pegawai_id'] == 101 and body['enrollment_id'] == 12
    assert body['occurred_at'] == '2026-10-14T07:24:59.300+08:00'
    assert worker.message['status'] == 'pending' and worker.recorded == 0
    now[0] = .4
    worker.run_once()
    assert len(worker.integration_store.pending()) == 1 and seen == [1]
    assert worker.check_enrollment() is False
    with pytest.raises(ValueError):
        worker.run_enrollment(None)


def test_store_failure_never_shows_pending(kiosk, monkeypatch):
    import sqlite3
    worker, _, _ = kiosk
    def fail(body):
        raise sqlite3.OperationalError('disk full sensitive detail')
    monkeypatch.setattr(worker.integration_store, 'enqueue', fail)
    assert capture(kiosk) == []
    worker.publish(force=True)
    view = public_view(worker.state.read(), now=.3)
    assert view['message']['text'] == 'Penyimpanan bermasalah'
    assert 'sensitive' not in json.dumps(view)
    assert view['integration']['storage'] == 'error'


def test_no_snapshot_no_anchor_or_proof_mismatch_never_enqueues(kiosk):
    worker, now, scene = kiosk
    worker.trusted_clock = TrustedClock(wall_time=lambda: now[0])
    assert capture(kiosk) == []
    worker.trusted_clock.anchor(scene[0]['issued_at'], str(uuid4()), now[0])
    assert capture(kiosk) == []
    worker.gallery = None
    worker.integration_store._db.execute('DELETE FROM snapshot')
    worker.integration_store._db.commit()
    assert capture(kiosk) == []


def test_overflow_recovers_current_terminal_and_gallery(kiosk):
    from recognition.eoffice_identify import EofficeGallery
    worker, now, scene = kiosk
    event, = capture(kiosk)
    snap = copy.deepcopy(scene[0])
    snap['snapshot_id'] = snap['clock_anchor_id'] = str(uuid4())
    worker.sync_worker.store.replace_snapshot(snap)
    worker.sync_worker.store.finish(event.event_id, terminal(event.event_id))
    worker.sync_worker._publish(SyncStatus(event_id=event.event_id, response=terminal(event.event_id),
                                         snapshot_id=snap['snapshot_id'], gallery_changed=True, recorded_count=1))
    for _ in range(40):
        worker.sync_worker._publish(SyncStatus(snapshot_id=snap['snapshot_id'], recorded_count=1))
    now[0] = .8
    worker.reconcile_eoffice()
    assert worker.message['status'] == 'check_in'
    assert worker.message['id'] == event.event_id and worker.recorded == 1
    assert worker.gallery.snapshot_id == snap['snapshot_id']
    worker.reconcile_eoffice(force=True)
    assert worker.recorded == 1


@pytest.mark.parametrize('outcome,expected', [('recorded', 'check_in'), ('duplicate', 'duplicate'),
                                            ('outside_schedule', 'outside_schedule'), ('rejected', 'rejected')])
def test_terminal_display_outcomes(kiosk, outcome, expected):
    worker, now, _ = kiosk
    event, = capture(kiosk)
    worker.sync_worker.store.finish(event.event_id, terminal(event.event_id, outcome))
    now[0] = .8
    worker.reconcile_eoffice()
    assert worker.message['status'] == expected


def test_old_person_or_expired_result_cannot_replace_current_display(kiosk):
    worker, now, _ = kiosk
    old, = capture(kiosk)
    worker._say_eoffice('uncertain')  # A new observed person replaces the old pending display.
    worker.sync_worker.store.finish(old.event_id, terminal(old.event_id))
    now[0] = .8
    worker.reconcile_eoffice()
    assert worker.message['status'] == 'uncertain'
    worker._say_eoffice('pending', 'Synthetic', old.event_id)
    now[0] = 10.
    worker.reconcile_eoffice()
    assert worker.message['status'] == 'pending'
    assert public_view({'mode': 'eoffice', 'heartbeat': 10., 'message': worker.message}, now=10.)['message'] is None


def test_auth_fault_safe_and_blocks_capture(kiosk):
    worker, now, _ = kiosk
    worker.sync_worker._publish(SyncStatus(auth='paused'))
    assert capture(kiosk) == []
    worker.publish(force=True)
    assert public_view(worker.state.read(), now=.3)['message']['text'] == 'Hubungi operator'


def test_failed_refresh_status_does_not_discard_valid_offline_anchor(kiosk):
    worker, now, _ = kiosk
    worker.sync_worker._publish(SyncStatus(connection='offline', clock='unavailable'))
    assert len(capture(kiosk)) == 1


def test_liveness_off_rejected_before_runtime(kiosk):
    worker, _, _ = kiosk
    with pytest.raises(ValueError):
        KioskWorker(worker.app, worker.camera, worker.detector, None, worker.quality, None,
            TrackerSettings(), None, worker.state, integration_store=worker.integration_store,
            sync_worker=worker.sync_worker, trusted_clock=worker.trusted_clock,
            eoffice_models=worker.eoffice_models, liveness_settings=LivenessSettings(mode='off'))


def test_capture_timestamp_precedes_inference_and_network_refresh(kiosk):
    worker, now, scene = kiosk
    original = worker.detector.detect
    def slow(frame):
        now[0] += .2
        return original(frame)
    worker.detector.detect = slow
    for t in (.1, .4, .7):
        now[0] = t
        worker.run_once()
    body = json.loads(worker.integration_store.pending()[0].body)
    assert body['occurred_at'] == '2026-10-14T07:24:59.700+08:00'


def test_local_model_loader_rejects_eoffice_before_sface(app, monkeypatch):
    from recognition.cli import _load_models
    app.config['ATTENDANCE_MODE'] = 'eoffice'
    import click
    with pytest.raises(click.ClickException, match='tidak tersedia'):
        _load_models(app)


@pytest.fixture(params=[False, True])
def startup(app, tmp_path, monkeypatch, biometric_keys, request):
    import kiosk.cli as cli
    import recognition.eoffice_models as assets
    from camera import webcam
    scene = setup_scene()
    snap, models, _, _, detector, frame = scene
    test_mode = request.param
    if test_mode:
        snap['purpose'] = 'camera_test'
    app.instance_path = str(tmp_path / 'instance')
    app.config.update(ATTENDANCE_MODE='eoffice', EOFFICE_TEST_MODE=test_mode, LIVENESS_MODE='passive',
                      EOFFICE_KEY_FILE=biometric_keys, EOFFICE_AURAFACE_PATH='synthetic-face',
                      EOFFICE_PERIOCULAR_PATH='synthetic-eye', EOFFICE_BASE_URL='https://synthetic.invalid',
                      EOFFICE_DEVICE_TOKEN='synthetic-test-token')
    monkeypatch.setattr('recognition.model_files.verify', lambda *a: 'synthetic-detector')
    monkeypatch.setattr('recognition.detector.YuNetDetector', lambda *a, **k: detector)
    monkeypatch.setattr(assets, 'AuraFaceEmbedder', lambda path: models.embedders['auraface_512'])
    monkeypatch.setattr(assets, 'PeriocularEmbedder', lambda path: models.embedders['mobilefacenet_v1_periocular'])
    monkeypatch.setattr('recognition.cli._load_liveness', lambda app: (models.liveness, LivenessSettings(mode='passive')))
    monkeypatch.setattr('recognition.cli._load_models', lambda app: pytest.fail('SFace loader called'))
    monkeypatch.setattr('services.biometric_service.load_active_templates', lambda *a: pytest.fail('local gallery called'))
    monkeypatch.setattr('kiosk.worker.fe.claim_next', lambda: pytest.fail('local enrollment called'))
    transport = Transport(snapshot=snap)
    from test_eoffice_test_mode import trial_result
    transport.send = lambda body: (trial_result if test_mode else terminal)(json.loads(body)['event_id'])
    def client(*args, **kwargs):
        assert kwargs == {'test_mode': test_mode}
        return transport
    monkeypatch.setattr('integration.client.EofficeClient', client)
    released = []
    cam = SimpleNamespace(actual={'index': 0, 'width': 240, 'height': 240}, open=lambda: None,
                          read=lambda: frame, release=lambda: released.append(True))
    monkeypatch.setattr(webcam, 'Webcam', lambda settings: cam)
    monkeypatch.setattr('signal.signal', lambda *a: None)
    resources = []
    original_load = cli.load_eoffice
    def load(app):
        result = original_load(app)
        resources.append(result[-1])
        return result
    monkeypatch.setattr(cli, 'load_eoffice', load)
    return resources, released, transport


def test_cli_starts_stops_separate_stores_and_releases_camera(app, startup, monkeypatch):
    import sqlite3
    resources, released, transport = startup
    original_run = KioskWorker.run
    def bounded(self, **kwargs):
        count = [0]
        def stop():
            count[0] += 1
            return count[0] > 4
        original_run(self, should_stop=stop)
    monkeypatch.setattr(KioskWorker, 'run', bounded)
    result = app.test_cli_runner().invoke(args=['kiosk-run', '--no-preview'])
    assert result.exit_code == 0, result.output
    test_mode = app.config['EOFFICE_TEST_MODE']
    assert (Path(app.instance_path) / ('eoffice-test.sqlite3' if test_mode else 'eoffice.sqlite3')).exists()
    assert not (Path(app.instance_path) / ('eoffice.sqlite3' if test_mode else 'eoffice-test.sqlite3')).exists()
    if test_mode:
        assert 'tidak mencatat absensi resmi' in result.output
        assert 'MENCATAT ABSENSI' not in result.output
    resource, = resources
    sync = resource['sync_worker']
    assert resource['integration_store'] is not sync.store
    assert resource['trusted_clock']._store is sync.store
    assert released == [True] and not sync._thread.is_alive()
    for store in (resource['integration_store'], sync.store):
        with pytest.raises(sqlite3.ProgrammingError):
            store.pending()


def test_cli_failure_does_not_close_store_owned_by_live_sync_thread(app, startup, monkeypatch):
    import threading
    import sqlite3
    resources, released, transport = startup
    entered, release = threading.Event(), threading.Event()
    def blocked():
        entered.set()
        release.wait(5)
        raise TimeoutError()
    transport.snapshot = blocked
    def fail(self, **kwargs):
        self.sync_worker.start()
        assert entered.wait(2)
        raise RuntimeError('synthetic startup fault')
    monkeypatch.setattr(KioskWorker, 'run', fail)
    try:
        result = app.test_cli_runner().invoke(args=['kiosk-run', '--no-preview'])
        assert result.exit_code != 0
        resource, = resources
        assert released == [True]
        assert resource['sync_worker']._thread.is_alive()
        assert resource['sync_worker'].store.pending() == []
        with pytest.raises(sqlite3.ProgrammingError):
            resource['integration_store'].pending()
    finally:
        release.set()
        resources[0]['sync_worker'].stop(timeout=2.)


def test_startup_failure_before_thread_closes_both_stores_and_camera(app, startup, monkeypatch):
    import sqlite3
    resources, released, transport = startup
    monkeypatch.setattr(KioskWorker, 'run', lambda *a, **k: (_ for _ in ()).throw(RuntimeError('test')))
    result = app.test_cli_runner().invoke(args=['kiosk-run', '--no-preview'])
    assert result.exit_code != 0 and released == [True]
    resource, = resources
    for store in (resource['integration_store'], resource['sync_worker'].store):
        with pytest.raises(sqlite3.ProgrammingError):
            store.pending()


def test_duplicates_and_pending_do_not_count_as_recorded(tmp_path):
    store = IntegrationStore(tmp_path / 'state.sqlite3', bytes(32))
    store.enqueue(json.dumps(EVENT).encode())
    worker = SyncWorker(store, Transport([terminal(EVENT['event_id'], 'duplicate')]))
    assert worker.status.recorded_count == 0
    worker.run_once()
    assert worker.status.recorded_count == 0
    store.close()


@pytest.mark.parametrize('field,value', [('samples', True), ('samples', 0), ('samples', 11),
                                        ('score', True), ('second_score', False), ('score', float('nan')), ('score', 1.1), ('second_score', -1.1),
                                        ('second_score', .99), ('enrollment_revision', 'd' * 64)])
def test_invalid_event_evidence_or_enrollment_never_queues(kiosk, field, value):
    from recognition.eoffice_identify import EofficeEvent
    worker, now, scene = kiosk
    now[0] = .3
    worker._capture_at = .3
    worker._frame_time = worker.trusted_clock.event_time(.3)
    event = EofficeEvent(1, 101, 12, 'a' * 64, 'face', 'auraface_512', scene[0]['snapshot_id'],
                        'Synthetic', 1., 0., 3, .3)
    assert worker.handle_event(replace(event, **{field: value})) is None
    assert worker.integration_store.pending() == []


def test_recorded_without_session_does_not_guess_check_out(kiosk):
    worker, now, _ = kiosk
    event, = capture(kiosk)
    response = terminal(event.event_id)
    response['data']['session'] = None
    worker.sync_worker.store.finish(event.event_id, response)
    now[0] = .8
    worker.reconcile_eoffice()
    assert worker.message['status'] == 'operator'


def test_idle_history_scan_waits_for_snapshot_or_capture_wake(tmp_path):
    from test_eoffice_sync import SNAPSHOT
    store = IntegrationStore(tmp_path / 'state.sqlite3', bytes(32))
    sync = SyncWorker(store, Transport([terminal(EVENT['event_id'])], snapshot=SNAPSHOT))
    sync._monotonic = lambda: 10.
    delays, scans = [], []
    original = store.pending
    def pending():
        scans.append(True)
        return original()
    store.pending = pending
    class Wake:
        def clear(self):
            pass
        def set(self):
            pass
        def wait(self, seconds):
            delays.append(seconds)
            if len(delays) == 1:
                store.enqueue(json.dumps(EVENT).encode())
                sync.wake()  # Simulate a capture interrupting the requested 60-second idle wait.
            else:
                sync._stop.set()
    sync._wake = Wake()
    sync._run()
    assert delays == [60., 60.]
    assert len(scans) == 2 and sync.status.recorded_count == 1


@pytest.mark.parametrize('final_time', [.625, .75])
def test_expired_first_live_sample_never_reaches_queue(kiosk, final_time):
    from recognition.eoffice_identify import EofficeGallery
    worker, now, scene = kiosk
    snapshot = copy.deepcopy(scene[0])
    snapshot['snapshot_id'] = snapshot['clock_anchor_id'] = str(uuid4())
    snapshot['models']['auraface_512']['liveness'].update(min_samples=6, decide_seconds=.375)
    worker.integration_store.replace_snapshot(snapshot)
    worker.gallery = EofficeGallery(snapshot, worker.eoffice_models)
    worker.trusted_clock.anchor(snapshot['issued_at'], snapshot['clock_anchor_id'], 0.)
    for capture_time in (0., .125, .25, .375, .5, final_time):
        now[0] = capture_time
        worker.run_once()
    assert worker.integration_store.pending() == []
    assert worker.message['status'] == 'uncertain'
    assert worker.recorded == 0


@pytest.fixture
def desktop_sound(monkeypatch):
    import sys
    calls = []
    monkeypatch.setitem(sys.modules, 'winsound', SimpleNamespace(
        SND_ALIAS=1, SND_ASYNC=2, SND_NODEFAULT=4,
        PlaySound=lambda sound, flags: calls.append((sound, flags))))
    return calls


def test_desktop_sound_only_confirms_current_durable_result_once(kiosk, desktop_sound):
    worker, now, _ = kiosk
    event, = capture(kiosk)
    assert all(sound != 'SystemAsterisk' for sound, flags in desktop_sound)
    desktop_sound.clear()
    worker._say_eoffice('pending', 'Synthetic', event.event_id)
    assert desktop_sound == []
    worker.sync_worker.store.finish(event.event_id, terminal(event.event_id))
    now[0] = .8
    worker.reconcile_eoffice(force=True)
    worker.reconcile_eoffice(force=True)
    assert desktop_sound == [('SystemAsterisk', 7)]
    worker._say_eoffice('uncertain')
    worker._say_eoffice('uncertain')
    assert desktop_sound == [('SystemAsterisk', 7), ('SystemHand', 7)]


@pytest.mark.parametrize('stale', ['other_person', 'expired'])
def test_old_result_never_plays_official_success(kiosk, desktop_sound, stale):
    worker, now, _ = kiosk
    event, = capture(kiosk)
    if stale == 'other_person':
        worker._say_eoffice('uncertain')
    else:
        now[0] = 10
    worker.sync_worker.store.finish(event.event_id, terminal(event.event_id))
    worker.reconcile_eoffice(force=True)
    assert all(sound != 'SystemAsterisk' for sound, flags in desktop_sound)


@pytest.mark.parametrize('unsupported', [True, False])
def test_unavailable_desktop_audio_never_interrupts_visual_result(kiosk, monkeypatch, unsupported):
    import sys
    def fail(*a):
        raise RuntimeError('no sound device')
    monkeypatch.setitem(sys.modules, 'winsound', None if unsupported else SimpleNamespace(
        SND_ALIAS=1, SND_ASYNC=2, SND_NODEFAULT=4, PlaySound=fail))
    worker, now, _ = kiosk
    event, = capture(kiosk)
    worker.sync_worker.store.finish(event.event_id, terminal(event.event_id))
    now[0] = .8
    worker.reconcile_eoffice(force=True)
    assert worker.message['status'] == 'check_in'


@pytest.mark.parametrize('fault,action', [
    ({'connection': 'offline'}, 'Periksa koneksi jaringan; antrean tersimpan akan dicoba kembali.'),
    ({'connection': 'error'}, 'Hubungi operator untuk memeriksa layanan e-office.'),
    ({'storage': 'error'}, 'Hubungi operator untuk memeriksa penyimpanan; jangan hapus antrean.'),
    ({'auth': 'paused'}, 'Hubungi operator untuk memulihkan akses perangkat.'),
    ({'clock': 'unavailable'}, 'Hubungi operator untuk memeriksa waktu dan sinkronisasi e-office.'),
])
def test_integration_health_persists_after_person_message_expires(kiosk, fault, action):
    from kiosk.state import admin_view
    worker, now, _ = kiosk
    worker.sync_worker._publish(replace(SyncStatus(connection='online', clock='ok'), **fault))
    if fault.get('clock') == 'unavailable':
        worker.trusted_clock.invalidate()
    worker.reconcile_eoffice(force=True)
    worker._say_eoffice('pending', 'Synthetic')
    now[0] = 10
    worker.publish(force=True)
    raw = worker.state.read()
    for view in (public_view(raw, now=10), admin_view(raw, now=10)):
        assert view['integration']['action'] == action
        for key, value in fault.items():
            assert view['integration'][key] == value
    assert public_view(raw, now=10)['message'] is None


def test_offline_health_distinguishes_usable_current_boot_clock(kiosk):
    worker, _, _ = kiosk
    worker.sync_worker._publish(SyncStatus(connection='offline', clock='unavailable'))
    worker.publish(force=True)
    health = public_view(worker.state.read(), now=0)['integration']
    assert health['connection'] == 'offline' and health['clock'] == 'ok'
    worker.trusted_clock.invalidate()
    worker.publish(force=True)
    assert public_view(worker.state.read(), now=0)['integration']['clock'] == 'unavailable'


def test_health_views_allowlist_fields_and_values():
    from kiosk.state import admin_view
    raw = {'heartbeat': 10, 'mode': 'eoffice', 'integration': {
        'connection': 'secret-token', 'storage': 'raw error', 'auth': 'pegawai_id=101',
        'clock': 'score=.9', 'action': 'credential secret', 'templates': [1, 2, 3]}}
    for view in (public_view(raw, now=10), admin_view(raw, now=10)):
        health = view['integration']
        assert health['connection'] == 'error' and health['storage'] == 'error'
        assert health['auth'] == 'paused' and health['clock'] == 'unavailable'
        assert not any(value in json.dumps(health) for value in ('secret', 'raw error', '101', 'score', 'templates'))
    assert 'integration' not in public_view(dict(raw, mode='standalone'), now=10)

@pytest.mark.parametrize('reason', ['automatic_attendance_exception', 'invalid_clock_proof',
                                   'enrollment_not_authorized', 'historical_schedule_unavailable'])
def test_server_rejection_is_distinct_from_local_identity_uncertainty(kiosk, reason):
    worker, now, _ = kiosk
    event, = capture(kiosk)
    response = terminal(event.event_id, 'rejected')
    response['data'].update(reason=reason, ad_id=None, session=None)
    worker.sync_worker.store.finish(event.event_id, response)
    now[0] = .8
    worker.reconcile_eoffice()
    assert worker.message['status'] == 'rejected'
    worker.publish(force=True)
    message = public_view(worker.state.read(), now=now[0])['message']
    assert 'Presensi ditolak server' in message['text']
    assert reason not in message['text']


def test_stale_revision_recovers_with_fresh_frames_and_releases_only_failed_cooldown(kiosk):
    worker, now, scene = kiosk
    old, = capture(kiosk)
    rejected = terminal(old.event_id, 'rejected')
    rejected['data'].update(reason='historical_schedule_unavailable', ad_id=None, session=None)
    worker.sync_worker.store.finish(old.event_id, rejected)
    new = copy.deepcopy(scene[0])
    new['snapshot_id'] = new['clock_anchor_id'] = str(uuid4())
    new['policy_revision'] += 1
    worker.sync_worker.store.replace_snapshot(new)
    worker.sync_worker._publish(SyncStatus(snapshot_id=new['snapshot_id']))
    from datetime import timedelta
    server_time = datetime.fromisoformat(new['issued_at']) + timedelta(seconds=.8)
    worker.trusted_clock.anchor(server_time, new['snapshot_id'], .8, issued_at=new['issued_at'])
    now[0] = .8
    worker.reconcile_eoffice(force=True)
    assert worker.message['status'] == 'syncing'
    assert not worker.tracker._last_confirmed
    for t in (.9, 1., 1.1):
        now[0] = t
        worker.run_once()
    fresh, = worker.integration_store.pending()
    body = json.loads(fresh.body)
    assert fresh.event_id != old.event_id
    assert body['snapshot_id'] == new['snapshot_id']
    assert body['evidence']['samples'] >= 3
    assert worker.integration_store.terminal(old.event_id) == rejected


def test_refresh_holds_new_capture_without_revoking_browser_ownership(kiosk):
    worker, now, scene = kiosk
    worker.sync_worker._refreshing.set()
    assert not capture(kiosk)
    assert worker.message['status'] == 'syncing'
    worker.sync_worker._refreshing.clear()
    for t in (.4, .5, .6):
        now[0] = t
        worker.run_once()
    assert len(worker.integration_store.pending()) == 1


def test_revision_refresh_starting_during_inference_does_not_consume_confirmation(kiosk, monkeypatch):
    worker, now, scene = kiosk
    handle = worker.handle_event
    def hold(event, **kwargs):
        worker.sync_worker._refreshing.set()
        return handle(event, **kwargs)
    monkeypatch.setattr(worker, 'handle_event', hold)
    assert not capture(kiosk)
    assert not worker.tracker._last_confirmed
    worker.sync_worker._refreshing.clear()
    monkeypatch.setattr(worker, 'handle_event', handle)
    for t in (.4, .5, .6):
        now[0] = t
        worker.run_once()
    assert len(worker.integration_store.pending()) == 1


def test_successful_receipt_keeps_cooldown_across_snapshot_refresh(kiosk):
    worker, now, scene = kiosk
    old, = capture(kiosk)
    worker.sync_worker.store.finish(old.event_id, terminal(old.event_id))
    original = dict(worker.tracker._last_confirmed)
    new = copy.deepcopy(scene[0])
    new['snapshot_id'] = new['clock_anchor_id'] = str(uuid4())
    worker.sync_worker.store.replace_snapshot(new)
    worker.sync_worker._publish(SyncStatus(snapshot_id=new['snapshot_id']))
    from datetime import timedelta
    worker.trusted_clock.anchor(datetime.fromisoformat(new['issued_at']) + timedelta(seconds=.8),
                                new['snapshot_id'], .8, issued_at=new['issued_at'])
    now[0] = .8
    worker.reconcile_eoffice(force=True)
    assert worker.message['status'] == 'check_in'
    for t in (.9, 1., 1.1):
        now[0] = t
        worker.run_once()
    assert not worker.integration_store.pending()
    assert worker.tracker._last_confirmed == original
