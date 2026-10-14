import copy
import io
import json
import sqlite3
import threading
from datetime import datetime, timezone
from email.message import Message
from pathlib import Path
from urllib.error import HTTPError
from uuid import uuid4

import pytest

from integration.clock import ClockUnavailable, TrustedClock
from integration.store import IntegrationStore

# Task 1's synthetic event, kept local so transport tests need no sibling API checkout.
EVENT = {'protocol_version': 1, 'event_id': '85f42b66-6a77-4c56-9efc-4231ff6eab42', 'pegawai_id': 101,
    'occurred_at': '2026-10-14T07:25:00+08:00', 'snapshot_id': '90d6f137-d36e-47be-88c1-6c15cd9ad25e',
    'enrollment_id': 12, 'enrollment_revision': 'a' * 64, 'verification_type': 'face', 'embedding_version': 'auraface_512',
    'evidence': {'score': .8, 'second_score': .2, 'quality_ok': True, 'live': True, 'samples': 3},
    'clock_anchor_id': '90d6f137-d36e-47be-88c1-6c15cd9ad25e', 'elapsed_ms': 1000}
SNAPSHOT = json.loads((Path(__file__).parent / 'fixtures/eoffice_snapshot.json').read_text())
DATE = datetime(2026, 10, 15, 0, 0, tzinfo=timezone.utc)


def terminal(event_id, outcome='recorded'):
    return {'status': True, 'data': {'event_id': event_id, 'outcome': outcome, 'reason': None,
        'ad_id': 1, 'work_date': '2026-10-14', 'session': 'check_in', 'received_at': '2026-10-15T08:00:00+08:00'}}


@pytest.fixture
def store(tmp_path):
    value = IntegrationStore(tmp_path / 'eoffice.sqlite3', bytes(range(32)))
    value.enqueue(json.dumps(EVENT).encode())
    yield value
    value.close()


class Transport:
    snapshot_server_time = DATE
    def __init__(self, replies=(), snapshot=None):
        self.replies = list(replies)
        self.snap = snapshot if snapshot is not None else TimeoutError()
        self.sent = []
        self.snap_calls = 0
    def snapshot(self):
        self.snap_calls += 1
        if isinstance(self.snap, Exception):
            raise self.snap
        return copy.deepcopy(self.snap)
    def send(self, body):
        self.sent.append(body)
        response = self.replies.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def worker(store, client, clock=None):
    from integration.sync import SyncWorker
    value = SyncWorker(store, client, clock)
    value._monotonic = lambda: 10.0
    return value


def test_timeout_keeps_same_event_pending(store):
    client = Transport([TimeoutError()])
    original = store.pending()[0].body
    status = worker(store, client).run_once()
    assert store.pending()[0].body == original
    assert status.connection == 'offline' and status.retry_after == 1


def test_backoff_is_capped_and_no_busy_network_loop(store):
    client = Transport([TimeoutError()] * 8)
    value = worker(store, client)
    now = [10.0]
    value._monotonic = lambda: now[0]
    for expected in [1, 2, 4, 8, 16, 32, 60, 60]:
        status = value.run_once()
        assert status.retry_after == expected
        sent = len(client.sent)
        value.run_once()
        assert len(client.sent) == sent
        now[0] += expected


@pytest.mark.parametrize('code', [429, 503])
def test_retryable_http_preserves_event(store, code):
    from integration.client import RequestFailure
    client = Transport([RequestFailure(code)])
    status = worker(store, client).run_once()
    assert len(store.pending()) == 1 and status.connection == 'offline'


@pytest.mark.parametrize('code', [400, 409])
def test_terminal_http_errors_are_persisted(store, code):
    response = {'status_code': code, 'event_id': EVENT['event_id'], 'body': {'status': False, 'message': 'invalid'}}
    assert worker(store, Transport([response])).run_once().events_sent == 1
    assert store.pending() == []


@pytest.mark.parametrize('code', [401, 403])
@pytest.mark.parametrize('during_snapshot', [False, True])
def test_auth_pause_requires_explicit_repair_and_does_not_discard(store, code, during_snapshot):
    from integration.client import RequestFailure
    client = Transport([RequestFailure(code)])
    if during_snapshot:
        client.snap = RequestFailure(code)
    value = worker(store, client)
    assert value.run_once().auth == 'paused'
    sent, refresh = len(client.sent), client.snap_calls
    value._monotonic = lambda: 1000.0
    assert value.run_once().auth == 'paused'
    assert (len(client.sent), client.snap_calls) == (sent, refresh)
    assert len(store.pending()) == 1
    repaired = Transport([terminal(EVENT['event_id'])], SNAPSHOT)
    value.resume(repaired)
    assert value.run_once().events_sent == 1
    assert store.pending() == []


@pytest.mark.parametrize('response', [terminal(str(uuid4())), terminal(EVENT['event_id'], 'invented'), {'status': True}])
def test_wrong_or_partial_response_never_finishes_event(store, response):
    status = worker(store, Transport([response])).run_once()
    assert len(store.pending()) == 1 and status.connection == 'error'


def test_lost_response_after_commit_retries_identical_bytes_and_first_result_wins(store, tmp_path):
    persisted = terminal(EVENT['event_id'])
    client = Transport([TimeoutError(), persisted])
    value = worker(store, client)
    original = store.pending()[0].body
    value.run_once()
    value._monotonic = lambda: 11.0
    assert value.run_once().events_sent == 1
    assert client.sent == [original, original]
    # A different worker's late valid reply cannot replace the first stored result.
    late = IntegrationStore(tmp_path / 'eoffice.sqlite3', bytes(range(32)))
    before = late._db.execute('SELECT response FROM events').fetchone()[0]
    late.finish(EVENT['event_id'], terminal(EVENT['event_id'], 'duplicate'))
    assert late._db.execute('SELECT response FROM events').fetchone()[0] == before
    assert late.pending() == []
    late.close()


def test_events_are_oldest_first_with_uuid_tiebreak_and_reordered_response_is_rejected(store):
    later = {**EVENT, 'event_id': str(uuid4()), 'occurred_at': '2026-10-14T09:00:00+08:00'}
    earlier = {**EVENT, 'event_id': str(uuid4()), 'occurred_at': '2026-10-14T06:00:00+08:00'}
    store.enqueue(json.dumps(later).encode()); store.enqueue(json.dumps(earlier).encode())
    client = Transport([terminal(EVENT['event_id'])])
    worker(store, client).run_once()
    assert json.loads(client.sent[0])['event_id'] == earlier['event_id']
    assert len(store.pending()) == 3


def test_failed_refresh_does_not_starve_events_and_reconnection_refreshes(store):
    client = Transport([terminal(EVENT['event_id'])])
    value = worker(store, client)
    assert value.run_once().events_sent == 1
    client.snap = SNAPSHOT
    value._monotonic = lambda: 10.01
    assert value.run_once().connection == 'online'
    assert store.load_snapshot() == SNAPSHOT


def test_reused_snapshot_refreshes_clock_without_republishing_gallery(store):
    client = Transport([terminal(EVENT['event_id'])], SNAPSHOT)
    clock = TrustedClock(wall_time=lambda: 1000.0)
    value = worker(store, client, clock)
    first = value.run_once()
    assert first.gallery_changed and first.clock == 'ok'
    assert clock.event_time(10.0)['occurred_at'] == '2026-10-15T08:00:00.000+08:00'
    assert clock.event_time(10.0)['clock_anchor_id'] == SNAPSHOT['clock_anchor_id']
    assert clock.event_time(10.0)['elapsed_ms'] == int((DATE - datetime.fromisoformat(SNAPSHOT['issued_at'])).total_seconds() * 1000)
    client.snapshot_server_time = datetime(2026, 10, 15, 0, 1, tzinfo=timezone.utc)
    value._monotonic = lambda: 70.0
    second = value.run_once()
    assert not second.gallery_changed and second.clock == 'ok'
    assert clock.event_time(70.0)['occurred_at'] == '2026-10-15T08:01:00.000+08:00'
    reboot = TrustedClock(store, wall_time=lambda: 1000.0)
    with pytest.raises(ClockUnavailable):
        reboot.event_time(70.0)
    worker(store, client, reboot).run_once()
    assert reboot.event_time(10.0)['occurred_at'] == '2026-10-15T08:01:00.000+08:00'


def test_missing_date_cannot_authorize_capture_but_pending_events_still_send(store):
    client = Transport([terminal(EVENT['event_id'])], SNAPSHOT)
    client.snapshot_server_time = None
    clock = TrustedClock(wall_time=lambda: 1000.0)
    status = worker(store, client, clock).run_once()
    assert status.clock == 'unavailable' and status.events_sent == 1
    with pytest.raises(ClockUnavailable):
        clock.event_time(10.0)


def test_storage_error_is_distinct_and_preserves_rows_on_reopen(store, tmp_path):
    store.close()
    status = worker(store, Transport([])).run_once()
    assert status.storage == 'error'
    reopened = IntegrationStore(tmp_path / 'eoffice.sqlite3', bytes(range(32)))
    assert len(reopened.pending()) == 1
    reopened.close()


def test_background_worker_owns_separate_connection_bounded_queue_and_shutdown(store, tmp_path):
    from integration.sync import SyncWorker
    dedicated = IntegrationStore(tmp_path / 'eoffice.sqlite3', bytes(range(32)))
    assert dedicated._db is not store._db
    complete = threading.Event()
    class Client(Transport):
        def send(self, body):
            complete.set()
            return super().send(body)
    value = SyncWorker(dedicated, Client([terminal(EVENT['event_id'])]))
    value.start()
    assert complete.wait(2)
    assert value.stop(2)
    assert store.pending() == []
    with pytest.raises(sqlite3.ProgrammingError):
        dedicated.pending()
    for _ in range(100):
        value._publish(value.status)
    assert value.results.qsize() <= value.results.maxsize == 32


class Response(io.BytesIO):
    def __init__(self, body, code=200, headers=None):
        super().__init__(body)
        self.status = code
        self.headers = Message()
        for key, val in (headers if headers is not None else {'Content-Type': 'application/json', 'Date': 'Thu, 15 Oct 2026 00:00:00 GMT'}).items():
            self.headers[key] = val


def transport_client(monkeypatch, body, code=200, headers=None):
    from integration.client import EofficeClient
    client = EofficeClient('https://example.test/base', 'a' * 64)
    requests = []
    def open_request(request, timeout):
        requests.append((request, timeout))
        if code >= 300:
            raise HTTPError(request.full_url, code, 'error', Response(b'', headers=headers).headers, io.BytesIO(body))
        return Response(body, code, headers)
    monkeypatch.setattr(client._opener, 'open', open_request)
    return client, requests


def test_transport_uses_verified_tls_explicit_timeout_and_original_bytes(monkeypatch):
    from integration.client import EofficeClient
    client, requests = transport_client(monkeypatch, json.dumps(terminal(EVENT['event_id'])).encode())
    original = b'{ "event_id": "' + EVENT['event_id'].encode() + b'" }'
    assert client.send(original) == terminal(EVENT['event_id'])
    request, timeout = requests[0]
    assert timeout == 5 and request.data is original
    assert request.full_url == 'https://example.test/base/api/kiosk/events'
    assert request.get_header('Authorization') == 'Bearer ' + 'a' * 64
    handler = next(h for h in client._opener.handlers if hasattr(h, '_context'))
    import ssl
    assert handler._context.verify_mode == ssl.CERT_REQUIRED and handler._context.check_hostname
    for url in ['http://example.test', 'https://user:pass@example.test', 'https://example.test?q=x', 'https://example.test#x']:
        with pytest.raises(ValueError):
            EofficeClient(url, 'a' * 64)


@pytest.mark.parametrize('code', [301, 302, 303, 307, 308, 401, 403, 429, 503])
def test_transport_refuses_redirects_and_classifies_nonterminal_http(monkeypatch, code):
    from integration.client import RequestFailure, RejectRedirects
    client, requests = transport_client(monkeypatch, b'{}', code)
    with pytest.raises(RequestFailure) as error:
        client.send(json.dumps(EVENT).encode())
    assert error.value.status_code == code and len(requests) == 1
    assert RejectRedirects().redirect_request(None, None, code, '', {}, 'https://elsewhere.test') is None


@pytest.mark.parametrize('code', [400, 409])
def test_transport_binds_legacy_error_text_to_original_request_id(monkeypatch, code):
    client, _ = transport_client(monkeypatch, b'{"status":false,"text":"invalid"}', code)
    assert client.send(json.dumps(EVENT).encode()) == {'status_code': code, 'event_id': EVENT['event_id'], 'body': {'status': False, 'message': 'invalid'}}


@pytest.mark.parametrize('body', [b'{', b'\xff', b'[]', b'{"status":true}', b'{"status":true,"status":false}', b'{"score":NaN}'])
def test_partial_invalid_or_ambiguous_json_is_rejected(monkeypatch, body):
    client, _ = transport_client(monkeypatch, body)
    with pytest.raises(ValueError):
        client.send(json.dumps(EVENT).encode())


@pytest.mark.parametrize('date', [None, 'bad', 'Wed, 14 Oct 2026 00:00:00', 'Wed, 14 Oct 2026 00:00:00 +0800'])
def test_snapshot_requires_same_response_standard_date(monkeypatch, date):
    headers = {'Content-Type': 'application/json'}
    if date is not None:
        headers['Date'] = date
    client, _ = transport_client(monkeypatch, json.dumps(SNAPSHOT).encode(), headers=headers)
    with pytest.raises(ValueError):
        client.snapshot()
    assert client.snapshot_server_time is None


def test_snapshot_current_date_is_separate_from_original_issuance(monkeypatch):
    client, _ = transport_client(monkeypatch, json.dumps(SNAPSHOT).encode())
    assert client.snapshot() == SNAPSHOT
    assert client.snapshot_server_time == DATE


@pytest.mark.parametrize('headers,body', [({'Content-Type':'text/html'}, b'{}'),
    ({'Content-Type':'application/json','Content-Length':'500'}, b'{}'),
    ({'Content-Type':'application/json','Content-Encoding':'gzip'}, b'{}'),
    ({'Content-Type':'application/json','Content-Length':str(16*1024*1024+1)}, b'{}'),
    ({'Content-Type':'application/json'}, b' ' * (16*1024*1024+1))], ids=['content-type','truncated','encoded','oversize-length','oversize-body'])
def test_oversize_partial_and_encoded_snapshot_never_replaces_cache(monkeypatch, headers, body):
    client, _ = transport_client(monkeypatch, body, headers=headers)
    with pytest.raises(ValueError):
        client.snapshot()
    assert client.snapshot_server_time is None


def test_two_worker_attempts_send_identical_body_and_late_response_keeps_first_terminal(store, tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    original = store.pending()[0].body
    committed = terminal(EVENT['event_id'])
    arrived = threading.Barrier(2)
    sent = []
    class Server(Transport):
        def send(self, body):
            sent.append(body)
            arrived.wait(2)
            return copy.deepcopy(committed)
    other = IntegrationStore(tmp_path / 'eoffice.sqlite3', bytes(range(32)))
    first, second = worker(store, Server()), worker(other, Server())
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(value.run_once) for value in (first, second)]
            assert [future.result().events_sent for future in futures] == [1, 1]
        assert sent == [original, original] and store.pending() == []
    finally:
        other.close()


def test_pending_history_is_authenticated_once_per_batch_before_network(store):
    called = []
    pending = store.pending
    def read():
        called.append(True)
        return pending()
    store.pending = read
    for _ in range(3):
        event = {**EVENT, 'event_id': str(uuid4())}
        store.enqueue(json.dumps(event).encode())
    class Server(Transport):
        def send(self, body):
            assert len(called) == 1
            assert not store._db.in_transaction
            return terminal(json.loads(body)['event_id'])
    assert worker(store, Server()).run_once().events_sent == 4
    assert len(called) == 1


def test_uuid_breaks_equal_event_time_ties(store):
    other = {**EVENT, 'event_id': '15f42b66-6a77-4c56-9efc-4231ff6eab42'}
    store.enqueue(json.dumps(other).encode())
    class Server(Transport):
        def send(self, body):
            self.sent.append(body)
            return terminal(json.loads(body)['event_id'])
    server = Server()
    worker(store, server).run_once()
    assert [json.loads(body)['event_id'] for body in server.sent] == sorted([EVENT['event_id'], other['event_id']])


def test_failed_snapshot_clock_and_finish_writes_preserve_queue_and_cache(store, monkeypatch):
    store.replace_snapshot(SNAPSHOT)
    original = store.pending()[0].body
    clock = TrustedClock(store, wall_time=lambda: 1000.0)
    def fail(*args):
        raise sqlite3.OperationalError('disk full')
    monkeypatch.setattr(store, 'save_clock_anchor', fail)
    status = worker(store, Transport([terminal(EVENT['event_id'])], SNAPSHOT), clock).run_once()
    assert status.storage == 'error' and status.clock == 'unavailable'
    assert store.pending()[0].body == original and store.load_snapshot() == SNAPSHOT
    with pytest.raises(ClockUnavailable):
        clock.event_time(10.0)
    monkeypatch.setattr(store, 'finish', fail)
    status = worker(store, Transport([terminal(EVENT['event_id'])])).run_once()
    assert status.storage == 'error' and store.pending()[0].body == original


def test_timed_out_stop_does_not_close_live_connection_or_start_second_request(store, tmp_path):
    from integration.sync import SyncWorker
    dedicated = IntegrationStore(tmp_path / 'eoffice.sqlite3', bytes(range(32)))
    entered, release = threading.Event(), threading.Event()
    class Slow(Transport):
        def snapshot(self):
            entered.set()
            assert release.wait(2)
            return copy.deepcopy(SNAPSHOT)
    client = Slow([terminal(EVENT['event_id'])])
    value = SyncWorker(dedicated, client)
    value.start()
    assert entered.wait(2)
    assert not value.stop(0.001)
    assert dedicated.pending()[0].event_id == EVENT['event_id']
    release.set()
    assert value.stop(2)
    assert not client.sent and len(store.pending()) == 1


@pytest.mark.parametrize('body', [
    {'status': False, 'text': 'invalid', 'event_id': '15f42b66-6a77-4c56-9efc-4231ff6eab42'},
    {'status': True, 'text': 'invalid'}, {'status': False, 'text': ''},
])
def test_http_error_cannot_finish_wrong_id_or_invalid_envelope(monkeypatch, body):
    client, _ = transport_client(monkeypatch, json.dumps(body).encode(), 400)
    with pytest.raises(ValueError):
        client.send(json.dumps(EVENT).encode())


def test_snapshot_auth_pause_never_retains_reconnect_busy_poll_delay(store):
    from integration.client import RequestFailure
    client = Transport([terminal(EVENT['event_id'])])
    value = worker(store, client)
    assert value.run_once().retry_after == .01
    client.snap = RequestFailure(401)
    status = value.run_once()
    assert status.auth == 'paused' and status.retry_after == 60
    assert value.run_once().retry_after == 60
