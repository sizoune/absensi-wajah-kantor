from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

import pytest

from integration.clock import ClockUnavailable, TrustedClock
from integration.store import IntegrationStore


def test_unanchored_clock_pauses_capture():
    with pytest.raises(ClockUnavailable):
        TrustedClock().event_time(100.0)


ANCHOR_ID = '90d6f137-d36e-47be-88c1-6c15cd9ad25e'
ISSUED = '2026-10-14T07:25:00.125+08:00'


def test_monotonic_capture_uses_wita_milliseconds():
    wall = [1000.0]
    clock = TrustedClock(wall_time=lambda: wall[0])
    clock.anchor(ISSUED, ANCHOR_ID, 10.0)
    wall[0] += 1.2349
    assert clock.event_time(11.2349) == {'occurred_at': '2026-10-14T07:25:01.359+08:00',
                                      'clock_anchor_id': ANCHOR_ID, 'elapsed_ms': 1234}


def test_fresh_server_time_can_reanchor_original_reused_snapshot():
    wall = [1000.0]
    clock = TrustedClock(wall_time=lambda: wall[0])
    clock.anchor('2026-10-15T07:25:00+08:00', ANCHOR_ID, 10.0, issued_at=ISSUED)
    wall[0] += 2.5
    assert clock.event_time(12.5) == {'occurred_at': '2026-10-15T07:25:02.500+08:00',
                                    'clock_anchor_id': ANCHOR_ID, 'elapsed_ms': 86402375}


def test_http_date_floor_at_first_issuance_does_not_make_negative_proof():
    clock = TrustedClock(wall_time=lambda: 1000.0)
    clock.anchor(datetime.fromisoformat('2026-10-14T07:25:00+08:00'), ANCHOR_ID, 10.0, issued_at=ISSUED)
    assert clock.event_time(10.0) == {'occurred_at': ISSUED,
                                    'clock_anchor_id': ANCHOR_ID, 'elapsed_ms': 0}


@pytest.mark.parametrize('server_time', ['2026-10-14T07:24:59+08:00', '2026-10-14T07:25:00.100+08:00'])
def test_only_same_second_date_floor_can_precede_snapshot_issuance(server_time):
    clock = TrustedClock(wall_time=lambda: 1000.0)
    with pytest.raises(ValueError):
        clock.anchor(server_time, ANCHOR_ID, 10.0, issued_at=ISSUED)
    with pytest.raises(ClockUnavailable):
        clock.event_time(10.0)


@pytest.mark.parametrize('jump', [30.001, -30.001, 120.1, -3600.0])
def test_wall_clock_jump_pauses_capture_until_new_authenticated_anchor(jump):
    wall = [1000.0]
    clock = TrustedClock(wall_time=lambda: wall[0])
    clock.anchor(ISSUED, ANCHOR_ID, 10.0)
    wall[0] += 1 + jump
    with pytest.raises(ClockUnavailable):
        clock.event_time(11.0)
    wall[0] = 1001.0
    with pytest.raises(ClockUnavailable):
        clock.event_time(11.0)
    clock.anchor('2026-10-14T07:25:01.125+08:00', ANCHOR_ID, 11.0, issued_at=ISSUED)
    assert clock.event_time(11.0)['elapsed_ms'] == 1000


@pytest.mark.parametrize('jump', [30.0, -30.0])
def test_wall_clock_guard_boundary_is_not_an_attendance_tolerance(jump):
    wall = [1000.0]
    clock = TrustedClock(wall_time=lambda: wall[0])
    clock.anchor(ISSUED, ANCHOR_ID, 10.0)
    wall[0] += 1 + jump
    assert clock.event_time(11.0)['elapsed_ms'] == 1000


@pytest.mark.parametrize('bad', [True, -1, float('nan'), float('inf'), '10', None])
def test_invalid_monotonic_inputs_fail_closed(bad):
    clock = TrustedClock(wall_time=lambda: 1000.0)
    with pytest.raises(ValueError):
        clock.anchor(ISSUED, ANCHOR_ID, bad)
    clock.anchor(ISSUED, ANCHOR_ID, 10.0)
    with pytest.raises(ClockUnavailable):
        clock.event_time(bad)


def test_backward_monotonic_time_cannot_capture():
    clock = TrustedClock(wall_time=lambda: 1000.0)
    clock.anchor(ISSUED, ANCHOR_ID, 10.0)
    with pytest.raises(ClockUnavailable):
        clock.event_time(9.0)


@pytest.mark.parametrize('bad_time', [None, '', 'bad', '2026-10-14T07:25:00', datetime(2026, 10, 14), True])
def test_missing_or_untrusted_time_cannot_authorize_capture(bad_time):
    clock = TrustedClock(wall_time=lambda: 1000.0)
    with pytest.raises(ValueError):
        clock.anchor(bad_time, ANCHOR_ID, 10.0)
    with pytest.raises(ClockUnavailable):
        clock.event_time(10.0)


def test_persisted_anchor_is_audit_only_after_process_restart(tmp_path):
    path, key = tmp_path / 'eoffice.sqlite3', bytes(range(32))
    store = IntegrationStore(path, key)
    first = TrustedClock(store, wall_time=lambda: 1000.0)
    first.anchor(ISSUED, ANCHOR_ID, 10.0)
    audit = store.load_clock_anchor()
    assert audit['clock_anchor_id'] == ANCHOR_ID
    assert audit['issued_at'] == ISSUED
    assert audit['server_time'] == ISSUED
    assert audit['capture_boot_id']
    store.close()
    store = IntegrationStore(path, key)
    second = TrustedClock(store, wall_time=lambda: 1000.0)
    assert store.load_clock_anchor() == audit
    with pytest.raises(ClockUnavailable):
        second.event_time(200.0)
    second.anchor('2026-10-15T07:25:00+08:00', ANCHOR_ID, 200.0, issued_at=ISSUED)
    assert store.load_clock_anchor()['capture_boot_id'] != audit['capture_boot_id']
    assert second.event_time(200.0)['elapsed_ms'] == 86399875
    store.close()


def test_anchor_refresh_is_atomic_with_frame_reads():
    clock = TrustedClock(wall_time=lambda: 1000.0)
    clock.anchor(ISSUED, ANCHOR_ID, 10.0)
    other = 'a8812b33-4c8a-457e-9dda-379108ca2134'
    def refresh():
        for _ in range(100):
            clock.anchor(ISSUED, other, 10.0)
            clock.anchor(ISSUED, ANCHOR_ID, 10.0)
    def capture():
        return [clock.event_time(10.0) for _ in range(100)]
    with ThreadPoolExecutor(max_workers=2) as pool:
        update = pool.submit(refresh)
        reads = pool.submit(capture).result()
        update.result()
    valid = {(ANCHOR_ID, ISSUED), (other, ISSUED)}
    assert all((row['clock_anchor_id'], row['occurred_at']) in valid and row['elapsed_ms'] == 0 for row in reads)


def test_failed_clock_audit_write_keeps_queue_and_pauses_capture(tmp_path):
    import sqlite3
    path, key = tmp_path / 'eoffice.sqlite3', bytes(range(32))
    store = IntegrationStore(path, key)
    body = b'{"event_id":"85f42b66-6a77-4c56-9efc-4231ff6eab42"}'
    store.enqueue(body)
    clock = TrustedClock(store, wall_time=lambda: 1000.0)
    clock.anchor(ISSUED, ANCHOR_ID, 10.0)
    store.close()
    with pytest.raises(sqlite3.ProgrammingError):
        clock.anchor(ISSUED, ANCHOR_ID, 10.0)
    with pytest.raises(ClockUnavailable):
        clock.event_time(10.0)
    reopened = IntegrationStore(path, key)
    assert reopened.pending()[0].body == body
    reopened.close()


def test_event_timestamp_never_has_nonzero_submillisecond_fraction():
    clock = TrustedClock(wall_time=lambda: 1000.0)
    clock.anchor('2026-10-14T07:25:00.125678+08:00', ANCHOR_ID, 10.0)
    event = clock.event_time(10.0)
    assert event['occurred_at'] == '2026-10-14T07:25:00.125+08:00'
    assert event['elapsed_ms'] == 0
