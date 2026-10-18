"""One network worker; frame processing only reads the persisted gallery and clock."""

import json
import sqlite3
import time
from dataclasses import dataclass, replace
from datetime import datetime
from http.client import HTTPException
from queue import Empty, Full, Queue
from threading import Event, RLock, Thread
from typing import Callable, Literal, Protocol

from integration.client import RequestFailure
from integration.clock import TrustedClock
from integration.protocol import JSONValue, validate_snapshot, validate_terminal_response
from integration.store import IntegrationStore, PendingEvent, StoreIntegrityError


class SyncClient(Protocol):
    snapshot_server_time: datetime | None
    snapshot_received_monotonic: float | None
    snapshot_received_wall_time: float | None
    snapshot_uncertainty_seconds: float | None
    def snapshot(self) -> dict[str, JSONValue]: ...
    def send(self, event_bytes: bytes) -> dict[str, JSONValue]: ...


@dataclass(frozen=True)
class SyncStatus:
    storage: Literal['ok', 'error'] = 'ok'
    connection: Literal['online', 'offline', 'error'] = 'offline'
    auth: Literal['ok', 'paused'] = 'ok'
    clock: Literal['ok', 'unavailable'] = 'unavailable'
    retry_after: float = 60.0
    recorded_count: int = 0  # Unique recorded acknowledgments during this worker run.
    events_sent: int = 0
    gallery_changed: bool = False
    snapshot_id: str | None = None
    event_id: str | None = None
    response: dict[str, JSONValue] | None = None


class SyncWorker:
    def __init__(self, store: IntegrationStore, client: SyncClient, clock: TrustedClock | None = None) -> None:
        """store is dedicated to this worker; start transfers close ownership to its thread."""
        self.store, self.client, self.clock = store, client, clock
        # Best-effort UI delivery: overflow can drop terminal notifications; reconcile from durable state.
        self.results: Queue[SyncStatus] = Queue(maxsize=32)
        self.status = SyncStatus()
        self._monotonic: Callable[[], float] = time.monotonic
        self._next_snapshot = self._next_events = 0.0
        self._snapshot_failures = self._event_failures = 0
        self._stop, self._wake = Event(), Event()
        self._lock = RLock()
        self._thread: Thread | None = None

    def _publish(self, status: SyncStatus) -> None:
        self.status = status
        try:
            self.results.put_nowait(status)
        except Full:
            try:
                self.results.get_nowait()
            except Empty:
                pass
            self.results.put_nowait(status)

    def _failure(self, status: SyncStatus, error: Exception) -> SyncStatus:
        if isinstance(error, (sqlite3.Error, StoreIntegrityError)):
            return replace(status, storage='error')
        if isinstance(error, RequestFailure) and error.status_code in (401, 403):
            return replace(status, auth='paused')
        return replace(status, connection='error' if isinstance(error, ValueError) else 'offline')

    def _refresh(self, status: SyncStatus) -> SyncStatus:
        try:
            snapshot = self.client.snapshot()
            validate_snapshot(snapshot, test_mode=self.store.test_mode)
            current = getattr(self.client, 'snapshot_server_time', None)
            if not isinstance(current, datetime) or current.utcoffset() is None:
                raise ValueError('Authenticated current server Date required')
            old = self.store.load_snapshot()
            self.store.replace_snapshot(snapshot)
            if self.clock is not None:
                self.clock.anchor(current, snapshot['clock_anchor_id'], self.client.snapshot_received_monotonic,
                                  issued_at=snapshot['issued_at'],
                                  wall_time_at_anchor=self.client.snapshot_received_wall_time,
                                  uncertainty_seconds=self.client.snapshot_uncertainty_seconds)
            self._snapshot_failures = 0
            self._next_snapshot = self._monotonic() + 60
            return replace(status, connection='online', clock='ok' if self.clock is not None else 'unavailable',
                           gallery_changed=old != snapshot, snapshot_id=snapshot['snapshot_id'])
        except (OSError, sqlite3.Error, HTTPException, ValueError, RequestFailure, StoreIntegrityError) as error:
            if self.clock is not None and isinstance(error, ValueError):
                self.clock.invalidate()
            self._snapshot_failures += 1
            self._next_snapshot = self._monotonic() + min(2 ** min(self._snapshot_failures - 1, 6), 60)
            return self._failure(replace(status, clock='unavailable'), error)

    @staticmethod
    def _order(entry: PendingEvent) -> tuple[datetime, str]:
        event = json.loads(entry.body)
        occurred = datetime.fromisoformat(event['occurred_at'])
        if occurred.utcoffset() is None:
            raise ValueError('Queued event requires timezone-aware occurrence')
        return occurred, entry.event_id

    def run_once(self, *, require_fresh_snapshot: bool = False) -> SyncStatus:
        """Repair can require a verified refresh before sending retained event bytes."""
        with self._lock:
            status = replace(self.status, events_sent=0, gallery_changed=False, event_id=None, response=None)
            if status.auth == 'paused' or self._stop.is_set():
                return replace(status, retry_after=60.0)
            status = replace(status, storage='ok')
            now = self._monotonic()
            if require_fresh_snapshot or now >= self._next_snapshot:
                status = self._refresh(status)
            if (status.auth == 'paused' or self._stop.is_set()
                    or require_fresh_snapshot and status.clock != 'ok'):
                status = replace(status, retry_after=60.0)
                self._publish(status)
                return status
            pending: list[PendingEvent] = []
            try:
                # pending authenticates retained history once; its cursor closes before network I/O.
                pending = sorted(self.store.pending(), key=self._order)
                if status.storage != 'error' and self._monotonic() >= self._next_events:
                    for entry in pending:
                        if self._stop.is_set():
                            break
                        was_offline = status.connection != 'online'
                        try:
                            result = self.client.send(entry.body)
                            validate_terminal_response(entry.event_id, result, test_mode=self.store.test_mode)
                            self.store.finish(entry.event_id, result)
                            self._event_failures = 0
                            self._next_events = self._monotonic()
                            status = replace(status, connection='online', events_sent=status.events_sent + 1,
                                             event_id=entry.event_id, response=result,
                                             recorded_count=status.recorded_count + int(result.get('data', {}).get('outcome') ==
                                                 ('test_recorded' if self.store.test_mode else 'recorded')))
                            self._publish(replace(status, gallery_changed=False))
                            if was_offline:
                                self._next_snapshot = self._monotonic()
                        except (OSError, sqlite3.Error, HTTPException, ValueError, RequestFailure, StoreIntegrityError) as error:
                            self._event_failures += 1
                            self._next_events = self._monotonic() + min(2 ** min(self._event_failures - 1, 6), 60)
                            status = self._failure(status, error)
                            break
            except (OSError, sqlite3.Error, ValueError, KeyError, TypeError, StoreIntegrityError) as error:
                status = replace(self._failure(status, error), storage='error')
            now = self._monotonic()
            delays = [self._next_snapshot - now]
            if pending and status.events_sent < len(pending):
                delays.append(self._next_events - now)
            if status.storage == 'error' or status.auth == 'paused':
                delays = [60.0]
            status = replace(status, retry_after=max(0.01, min(delays)), event_id=None, response=None)
            self._publish(status)
            return status

    def resume(self, client: SyncClient | None = None) -> None:
        """Call only after operator credential/config repair; never automatic on HTTP auth failure."""
        with self._lock:
            if client is not None:
                self.client = client
            self.status = replace(self.status, auth='ok')
            self._next_snapshot = self._next_events = 0
            self._snapshot_failures = self._event_failures = 0
        self.wake()

    def wake(self) -> None:
        self._wake.set()

    @property
    def started(self) -> bool:
        return self._thread is not None

    def start(self) -> None:
        if self._thread is not None:
            raise RuntimeError('Sync worker already started')
        self._thread = Thread(target=self._run, name='eoffice-sync', daemon=True)
        try:
            self._thread.start()
        except BaseException:
            self._thread = None
            raise

    def _run(self) -> None:
        try:
            while not self._stop.is_set():
                self._wake.clear()
                status = self.run_once()
                self._wake.wait(status.retry_after)
        finally:
            self.store.close()

    def stop(self, timeout: float = 6.0) -> bool:
        """False means the request is still finishing; its thread retains connection ownership."""
        self._stop.set()
        self.wake()
        if self._thread is None:
            return True
        self._thread.join(timeout)
        return not self._thread.is_alive()
