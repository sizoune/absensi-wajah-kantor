"""Real kiosk HTTP/SQL + encrypted queue + worker proof, with synthetic frame events.

Opt in only with KIOSK_TEST_DATABASE_URL=kiosk_test_*; sibling API's fixture owns
its DB guard/mutex. The opener maps HTTPS to the trusted-loopback fixture only:
this suite does not certify deployed TLS, biometric inference or physical liveness.
"""
from __future__ import annotations

from contextlib import suppress
import json
import os
from pathlib import Path
import shutil
from queue import Queue
import subprocess
import threading
from datetime import datetime
from types import SimpleNamespace
from typing import Any
from urllib.error import URLError
from urllib.request import Request, urlopen

import pytest

from integration.client import EofficeClient
from integration.clock import ClockUnavailable, TrustedClock
from integration.store import IntegrationStore, StoreIntegrityError
from integration.sync import SyncWorker
from kiosk.state import KioskState
from kiosk.worker import KioskWorker
from recognition.detector import QualitySettings
from recognition.eoffice_identify import EofficeEvent, EofficeModels
from recognition.liveness import LivenessSettings
from tracking.face_tracker import TrackerSettings

pytestmark = pytest.mark.skipif(not os.getenv('KIOSK_TEST_DATABASE_URL'), reason='Disposable kiosk_test_ DB required')
API = Path(__file__).resolve().parents[2] / 'api'


class LiveAPI:
    def __init__(self, log: Any) -> None:
        self.process = subprocess.Popen(['node', 'tests/fixtures/kiosk/e2e.js'], cwd=API,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=log, text=True)
        self.lines: Queue[str] = Queue()
        def read_lines() -> None:
            for line in self.process.stdout:
                self.lines.put(line)
            self.lines.put('')
        self.reader = threading.Thread(target=read_lines, daemon=True)
        self.reader.start()
        try:
            ready = self._read()
            self.base, self.secret = ready['base'], ready['secret']
        except BaseException:
            self._cleanup()
            raise
        self.offline = False
        self.lose_reply = False
        self.sent: list[bytes] = []
        self.responded = threading.Event()

    def _read(self) -> Any:
        line = self.lines.get(timeout=60)
        assert line, 'Fixture exited; inspect api.log'
        return json.loads(line)

    def command(self, command: str, **args: Any) -> Any:
        self.process.stdin.write(json.dumps(dict(command=command, **args)) + '\n')
        self.process.stdin.flush()
        reply = self._read()
        assert 'error' not in reply, reply.get('error')
        return reply['result']

    def open(self, request: Request, timeout: float) -> Any:
        if self.offline:
            raise URLError('Synthetic outage before network request')
        if request.data is not None:
            self.sent.append(request.data)
        # Keep real client encoding/auth/validation, with a test-only loopback seam.
        route = request.full_url.removeprefix('https://fixture.test')
        headers = dict(request.header_items(), **{'X-Forwarded-Proto': 'https'})
        response = urlopen(Request(self.base + route, data=request.data, headers=headers), timeout=timeout)
        if request.data is not None:
            self.responded.set()
            if self.lose_reply:
                self.lose_reply = False
                response.read()  # Actual HTTP 200 follows transaction COMMIT.
                response.close()
                raise TimeoutError('Synthetic lost response after server commit')
        return response

    def _cleanup(self) -> None:
        try:
            if self.process.poll() is None:
                with suppress(ProcessLookupError):
                    self.process.kill()
            self.process.wait()
        finally:
            self.reader.join(timeout=2)
            for pipe in (self.process.stdin, self.process.stdout):
                with suppress(OSError):
                    pipe.close()

    def close(self) -> None:
        try:
            if self.process.poll() is None:
                self.command('close')
                self.process.stdin.close()
            assert self.process.wait(timeout=10) == 0
        finally:
            self._cleanup()


@pytest.fixture
def live_api(tmp_path: Path):
    with (tmp_path / 'api.log').open('w') as log:
        api = LiveAPI(log)
        try:
            yield api
        finally:
            api.close()


class Journey:
    def __init__(self, api: LiveAPI, app: Any, tmp: Path, monkeypatch: Any,
                 issued: str = '2026-10-14T07:24:59+08:00') -> None:
        self.api, self.tmp = api, tmp
        self.snapshot = api.command('snapshot', at=issued)
        self.origin = datetime.fromisoformat(issued)
        self.now = 100.0
        self.key = bytes(range(32))
        self.path = tmp / 'eoffice.sqlite3'
        self.frame_store = IntegrationStore(self.path, self.key)
        self.worker_store = IntegrationStore(self.path, self.key)
        self.clock = TrustedClock(self.worker_store, wall_time=lambda: self.now)
        self.client = EofficeClient('https://fixture.test', api.secret)
        self.client._monotonic = lambda: self.now
        self.client._wall_time = lambda: self.now
        monkeypatch.setattr(self.client._opener, 'open', api.open)
        self.sync = SyncWorker(self.worker_store, self.client, self.clock)
        self.sync._monotonic = lambda: self.now
        assert self.sync.run_once().clock == 'ok'
        assert self.frame_store.load_snapshot()['snapshot_id'] == self.snapshot['snapshot_id']
        models = EofficeModels({version: SimpleNamespace(manifest={
            'version': version, 'dimension': policy['dimension'], 'sha256': policy['weights_sha256']})
            for version, policy in self.snapshot['models'].items()}, SimpleNamespace())
        app.config['ATTENDANCE_MODE'] = 'eoffice'
        self.worker = KioskWorker(app, None, None, None, QualitySettings(),
            lambda: pytest.fail('Standalone gallery accessed'), TrackerSettings(), None,
            KioskState(tmp / 'kiosk.json'), clock=lambda: self.now, wall_clock=lambda: self.now,
            liveness_settings=LivenessSettings(mode='passive'), integration_store=self.frame_store,
            sync_worker=self.sync, trusted_clock=self.clock, eoffice_models=models)
        self.worker.reload_templates(force=True)
        self.next_event: EofficeEvent | None = None
        monkeypatch.setattr('recognition.eoffice_identify.recognize_eoffice_frame',
            lambda *args: ([], [self.next_event] if self.next_event else []))
        monkeypatch.setattr('kiosk.worker.att.record', lambda *a, **k: pytest.fail('Standalone record called'))
        self.track = 0

    def at(self, value: str) -> None:
        self.now = 100 + (datetime.fromisoformat(value) - self.origin).total_seconds()

    def capture(self, at: str) -> Any:
        self.at(at)
        self.track += 1
        employee = self.snapshot['employees'][0]
        self.next_event = EofficeEvent(self.track, employee['pegawai_id'], employee['enrollment_id'],
            employee['enrollment_revision'], employee['verification_type'], employee['embedding_version'],
            self.snapshot['snapshot_id'], 'Synthetic', .9, .1, 3, self.now)
        self.worker._step_eoffice(None, captured_at=self.now)
        event = self.frame_store.pending()[-1]
        assert self.worker.message['status'] == 'pending'
        assert self.frame_store.terminal(event.event_id) is None
        assert json.loads(event.body)['elapsed_ms'] == int((datetime.fromisoformat(at) -
            datetime.fromisoformat(self.snapshot['issued_at'])).total_seconds() * 1000)
        return event

    def send(self, at: str) -> Any:
        self.api.command('clock', at=at)
        self.at(at)
        status = self.sync.run_once()
        self.worker.reconcile_eoffice(force=True)
        return status

    def result(self, event: Any) -> dict[str, Any]:
        return self.frame_store.terminal(event.event_id)['data']

    def close(self) -> None:
        assert self.sync.stop()
        self.frame_store.close()
        if not self.sync.started:
            self.worker_store.close()


@pytest.fixture
def journey(live_api: LiveAPI, app: Any, tmp_path: Path, monkeypatch: Any):
    value = Journey(live_api, app, tmp_path, monkeypatch)
    try:
        yield value
    finally:
        value.close()


def official(state: dict[str, Any], hour: int) -> dict[str, Any]:
    rows = [row for row in state['details'] if row['ah_id'] == hour]
    assert len(rows) == 1
    assert len(state['parents']) == 1
    return rows[0]


def test_offline_camera_does_not_replace_mobile(journey: Journey) -> None:
    j = journey
    event = j.capture('2026-10-14T07:25:00+08:00')
    j.api.offline = True
    assert j.send('2026-10-14T07:25:01+08:00').connection == 'offline'
    assert j.api.command('mobile', at='2026-10-14T07:30:00+08:00')['status'] is True
    j.api.offline = False
    j.send('2026-10-14T07:40:00+08:00')
    assert j.result(event)['outcome'] == 'duplicate'
    state = j.api.command('state')
    assert official(state, 1)['ad_time'] == '07:30:00'
    assert len(state['events']) == 1
    assert j.frame_store.pending() == []


@pytest.mark.parametrize('camera_first', [True, False])
def test_first_channel_wins_and_opposite_channel_checkout(journey: Journey, camera_first: bool) -> None:
    j = journey
    if camera_first:
        event = j.capture('2026-10-14T07:25:00+08:00')
        j.send('2026-10-14T07:25:01+08:00')
        assert j.result(event)['outcome'] == 'recorded'
        assert j.worker.message['status'] == 'check_in'
        assert j.api.command('mobile')['status'] is False
        assert j.api.command('mobile', at='2026-10-14T17:00:00+08:00', session='check_out')['status'] is True
    else:
        assert j.api.command('mobile')['status'] is True
        event = j.capture('2026-10-14T17:00:00+08:00')
        j.send('2026-10-14T17:00:01+08:00')
        assert j.result(event)['session'] == 'check_out'
        assert j.result(event)['outcome'] == 'recorded'
    state = j.api.command('state')
    assert official(state, 1)['ad_time'] == ('07:25:00' if camera_first else '07:30:00')
    assert official(state, 2)['ad_time'] == '17:00:00'
    assert {row['source'] for row in state['sources']} == {'mobile', 'kamera_kantor'}


def test_lost_reply_after_commit_retries_exact_bytes(journey: Journey) -> None:
    j = journey
    event = j.capture('2026-10-14T07:25:00+08:00')
    j.api.lose_reply = True
    j.send('2026-10-14T07:25:01+08:00')
    committed = j.api.command('state')
    assert official(committed, 1)['ad_time'] == '07:25:00'
    assert len(committed['events']) == 1
    assert j.frame_store.pending()[0].body == event.body
    j.send('2026-10-14T07:25:03+08:00')
    assert j.api.sent == [event.body, event.body]
    assert j.result(event)['outcome'] == 'recorded'
    assert j.api.command('state')['details'] == committed['details']
    assert j.sync.status.recorded_count == 1


def test_reboot_and_separate_key_backup_restore(journey: Journey, tmp_path: Path) -> None:
    j = journey
    event = j.capture('2026-10-14T07:25:00+08:00')
    j.frame_store.close()
    j.worker_store.close()
    # Closed SQLite checkpoint, DB and key kept in different backup locations.
    backup = tmp_path / 'backup' / 'eoffice.sqlite3'
    backup.parent.mkdir()
    shutil.copy2(j.path, backup)
    keyfile = tmp_path / 'separate-key-store' / 'key'
    keyfile.parent.mkdir()
    keyfile.write_bytes(j.key)
    restored = tmp_path / 'restore' / 'eoffice.sqlite3'
    restored.parent.mkdir()
    shutil.copy2(backup, restored)
    with pytest.raises(StoreIntegrityError):
        IntegrationStore(restored, bytes(32))
    j.frame_store = IntegrationStore(restored, keyfile.read_bytes())
    j.worker_store = IntegrationStore(restored, keyfile.read_bytes())
    recovered, = j.frame_store.pending()
    assert (recovered.event_id, recovered.body) == (event.event_id, event.body)
    assert event.body not in backup.read_bytes()
    j.clock = TrustedClock(j.worker_store, wall_time=lambda: j.now)
    with pytest.raises(ClockUnavailable):
        j.clock.event_time(j.now)
    j.api.command('clock', at='2026-10-14T07:40:00+08:00')
    j.sync = SyncWorker(j.worker_store, j.client, j.clock)
    j.sync.start()
    assert j.api.responded.wait(5)
    assert j.sync.stop(6)
    assert j.frame_store.pending() == []
    assert j.result(event)['outcome'] == 'recorded'
    assert official(j.api.command('state'), 1)['ad_time'] == '07:25:00'


def test_enrollment_revoked_during_outage_never_records(journey: Journey) -> None:
    j = journey
    event = j.capture('2026-10-14T07:25:00+08:00')
    j.api.command('revoke_enrollment')
    j.send('2026-10-14T07:40:00+08:00')
    assert j.result(event)['reason'] == 'enrollment_not_authorized'
    assert j.api.command('state')['details'] == []
    assert j.frame_store.pending() == []


def test_policy_change_preserves_before_boundary_and_rejects_uncovered_capture(journey: Journey) -> None:
    j = journey
    before = j.capture('2026-10-14T07:25:00+08:00')
    after = j.capture('2026-10-14T07:27:00+08:00')
    j.api.command('policy', boundary='2026-10-13 23:26:00')
    j.send('2026-10-14T07:40:00+08:00')
    assert j.result(before)['outcome'] == 'recorded'
    assert j.result(after)['outcome'] == 'rejected'
    assert official(j.api.command('state'), 1)['ad_is_late'] == 0
    assert j.frame_store.load_snapshot()['snapshot_id'] != j.snapshot['snapshot_id']


@pytest.mark.parametrize('capture,receipt,month', [
    ('2026-10-31T07:25:00+08:00', '2026-11-01T07:40:00+08:00', '10'),
    ('2026-12-31T07:25:00+08:00', '2027-01-01T07:40:00+08:00', '12'),
])
def test_rollover_uses_capture_partition(live_api: LiveAPI, app: Any, tmp_path: Path,
                                       monkeypatch: Any, capture: str, receipt: str, month: str) -> None:
    j = Journey(live_api, app, tmp_path, monkeypatch, issued=capture.replace('07:25:00', '07:24:59'))
    try:
        event = j.capture(capture)
        j.send(receipt)
        assert j.result(event)['outcome'] == 'recorded'
        assert j.result(event)['work_date'] == capture[:10]
        state = live_api.command('state', month=month)
        assert official(state, 1)['ad_time'] == '07:25:00'
        assert set(state['partitions']) == {'eoffice_absen.abs_absen_2026-' + month}
        assert live_api.command('state', month='11')['details'] == []
    finally:
        j.close()


def test_failed_transaction_rolls_back_then_retries_queue_bytes(journey: Journey) -> None:
    j = journey
    event = j.capture('2026-10-14T07:25:00+08:00')
    j.api.command('fail_write', enabled=True)
    assert j.send('2026-10-14T07:25:01+08:00').connection == 'offline'
    state = j.api.command('state')
    assert state['parents'] == state['details'] == state['sources'] == state['events'] == []
    assert j.frame_store.pending()[0].body == event.body
    j.api.command('fail_write', enabled=False)
    j.send('2026-10-14T07:25:03+08:00')
    assert j.api.sent == [event.body, event.body]
    assert j.result(event)['outcome'] == 'recorded'
    assert official(j.api.command('state'), 1)['ad_time'] == '07:25:00'


def test_slow_snapshot_crosses_lateness_boundary_using_physical_capture_time(journey: Journey, monkeypatch: Any) -> None:
    j = journey
    j.api.command('clock', at='2026-10-14T07:59:59+08:00')
    j.at('2026-10-14T07:59:59+08:00')
    def slow_snapshot(request, timeout):
        response = j.api.open(request, timeout)
        if request.full_url.endswith('/api/kiosk/snapshot'):
            read = response.read
            def slow_read(*args):
                j.now += 90
                return read(*args)
            response.read = slow_read
        return response
    monkeypatch.setattr(j.client._opener, 'open', slow_snapshot)
    assert j.sync.run_once().clock == 'ok'
    monkeypatch.setattr(j.client._opener, 'open', j.api.open)
    event = j.capture('2026-10-14T08:01:30+08:00')
    assert json.loads(event.body)['occurred_at'] == '2026-10-14T08:01:30.000+08:00'
    assert json.loads(event.body)['snapshot_id'] == j.snapshot['snapshot_id']
    j.send('2026-10-14T08:01:31+08:00')
    assert j.result(event)['outcome'] == 'recorded'
    row = official(j.api.command('state'), 1)
    assert row['ad_time'] == '08:01:30'
    assert row['ad_is_late'] == 1
    assert row['ad_late_length'] == 90
