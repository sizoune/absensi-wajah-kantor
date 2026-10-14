"""Clock proof through the real transport, validation and encrypted persistence."""
import json
from datetime import datetime, timedelta
from email.utils import format_datetime

import pytest

from integration.client import EofficeClient
from integration.clock import ClockUnavailable, TrustedClock
from integration.store import IntegrationStore
from integration.sync import SyncWorker
from test_eoffice_sync import DATE, SNAPSHOT, Response


@pytest.fixture
def timed_sync(tmp_path, monkeypatch):
    now = [10.0]
    store = IntegrationStore(tmp_path / 'timing.sqlite3', bytes(32))
    client = EofficeClient('https://example.test', 'test-secret')
    client._monotonic = lambda: now[0]
    client._wall_time = lambda: 1000 + now[0]
    clock = TrustedClock(store, wall_time=client._wall_time)
    sync = SyncWorker(store, client, clock)
    sync._monotonic = client._monotonic
    response = Response(json.dumps(SNAPSHOT).encode())
    monkeypatch.setattr(client._opener, 'open', lambda *a, **k: response)
    yield now, store, client, clock, sync, response
    store.close()


def test_slow_body_preserves_ninety_seconds_at_schedule_boundary(timed_sync, monkeypatch):
    now, store, client, clock, sync, response = timed_sync
    read = response.read
    def slow_read(*args):
        now[0] += 90
        return read(*args)
    monkeypatch.setattr(response, 'read', slow_read)
    assert sync.run_once().clock == 'ok'
    now[0] += 1
    proof = clock.event_time(now[0])
    assert proof['occurred_at'] == '2026-10-15T08:01:31.000+08:00'
    assert datetime.fromisoformat(proof['occurred_at']).time().isoformat() > '08:01:00'
    assert proof['clock_anchor_id'] == SNAPSHOT['snapshot_id']
    assert proof['elapsed_ms'] == int((DATE + timedelta(seconds=91) -
        datetime.fromisoformat(SNAPSHOT['issued_at'])).total_seconds() * 1000)
    assert datetime.fromisoformat(store.load_clock_anchor()['issued_at']) == datetime.fromisoformat(SNAPSHOT['issued_at'])


@pytest.mark.parametrize('delay', [1.001, 5, 90, float('nan'), float('inf'), -1])
def test_delayed_or_invalid_header_timing_never_authorizes_capture(timed_sync, monkeypatch, delay):
    now, store, client, clock, sync, response = timed_sync
    def delayed_headers(*a, **k):
        now[0] += delay
        return response
    monkeypatch.setattr(client._opener, 'open', delayed_headers)
    assert sync.run_once().clock == 'unavailable'
    assert store.load_snapshot() is None
    with pytest.raises(ClockUnavailable):
        clock.event_time(now[0])


@pytest.mark.parametrize('delay', [0, .2, 1])
def test_header_uncertainty_includes_entire_round_trip_and_date_precision(timed_sync, monkeypatch, delay):
    now, store, client, clock, sync, response = timed_sync
    def headers(*a, **k):
        now[0] += delay
        return response
    monkeypatch.setattr(client._opener, 'open', headers)
    assert sync.run_once().clock == 'ok'
    assert clock.event_time(now[0])['occurred_at'] == '2026-10-15T08:00:00.000+08:00'
    assert client.snapshot_uncertainty_seconds == pytest.approx(1 + delay)


@pytest.mark.parametrize('stage', ['client_validation', 'sync_validation', 'load_snapshot', 'replace_snapshot', 'save_clock_anchor'])
def test_processing_and_persistence_preserve_header_time(timed_sync, monkeypatch, stage):
    now, store, client, clock, sync, response = timed_sync
    import integration.client
    import integration.sync
    owner, name = ((integration.client, 'validate_snapshot') if stage == 'client_validation' else
                   (integration.sync, 'validate_snapshot') if stage == 'sync_validation' else (store, stage))
    original = getattr(owner, name)
    def delayed(*args, **kwargs):
        now[0] += 90
        return original(*args, **kwargs)
    monkeypatch.setattr(owner, name, delayed)
    assert sync.run_once().clock == 'ok'
    assert clock.event_time(now[0])['occurred_at'] == '2026-10-15T08:01:30.000+08:00'


def test_same_gallery_refresh_never_rewinds_subsecond_capture(timed_sync, monkeypatch):
    now, store, client, clock, sync, response = timed_sync
    assert sync.run_once().clock == 'ok'
    now[0] += 60.9
    before = clock.event_time(now[0])
    headers = {'Content-Type': 'application/json', 'Date': 'Thu, 15 Oct 2026 00:01:00 GMT'}
    monkeypatch.setattr(client._opener, 'open', lambda *a, **k: Response(json.dumps(SNAPSHOT).encode(), headers=headers))
    assert sync.run_once().clock == 'ok'
    assert clock.event_time(now[0]) == before
    assert store.load_snapshot()['snapshot_id'] == SNAPSHOT['snapshot_id']


def test_inconsistent_backward_server_time_revokes_capture(timed_sync, monkeypatch):
    now, store, client, clock, sync, response = timed_sync
    assert sync.run_once().clock == 'ok'
    now[0] += 90
    monkeypatch.setattr(client._opener, 'open', lambda *a, **k: Response(json.dumps(SNAPSHOT).encode()))
    assert sync.run_once().clock == 'unavailable'
    with pytest.raises(ClockUnavailable):
        clock.event_time(now[0])


def test_uncertain_refresh_revokes_anchor_but_network_outage_keeps_current_boot_anchor(timed_sync, monkeypatch):
    now, store, client, clock, sync, response = timed_sync
    assert sync.run_once().clock == 'ok'
    now[0] += 60
    def offline(*a, **k):
        raise TimeoutError()
    monkeypatch.setattr(client._opener, 'open', offline)
    assert sync.run_once().connection == 'offline'
    assert clock.event_time(now[0])['occurred_at'] == '2026-10-15T08:01:00.000+08:00'
    now[0] += 1
    def late(*a, **k):
        now[0] += 2
        return Response(json.dumps(SNAPSHOT).encode())
    monkeypatch.setattr(client._opener, 'open', late)
    assert sync.run_once().clock == 'unavailable'
    with pytest.raises(ClockUnavailable):
        clock.event_time(now[0])


def test_refresh_cannot_rewind_frames_captured_while_body_is_read(timed_sync, monkeypatch):
    now, store, client, clock, sync, response = timed_sync
    assert sync.run_once().clock == 'ok'
    now[0] += 60.9004
    response = Response(json.dumps(SNAPSHOT).encode(), headers={
        'Content-Type': 'application/json', 'Date': 'Thu, 15 Oct 2026 00:01:00 GMT'})
    read = response.read
    before = []
    def slow_body(*a):
        now[0] += 90.0006
        before.append(clock.event_time(now[0]))
        return read(*a)
    monkeypatch.setattr(response, 'read', slow_body)
    monkeypatch.setattr(client._opener, 'open', lambda *a, **k: response)
    assert sync.run_once().clock == 'ok'
    assert clock.event_time(now[0]) == before[0]
