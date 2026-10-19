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
    def policy_revision(self) -> int: ...
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
        self._next_snapshot = self._next_events = self._next_revision = 0.0
        self._revision_failures = 0
        self._refreshing = Event()
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

    @property
    def refreshing(self) -> bool:
        return self._refreshing.is_set()

    def _refresh(self, status: SyncStatus) -> SyncStatus:
        self._refreshing.set()
        try:
            status = self._download_snapshot(status)
            self.status = status
            self._next_revision = self._monotonic() + 1
            return status
        finally:
            self._refreshing.clear()

    def _check_revision(self, status: SyncStatus) -> SyncStatus:
        try:
            revision = self.client.policy_revision()
            snapshot = self.store.load_snapshot()
            if snapshot is not None and revision < snapshot['policy_revision']:
                if self.clock is not None:
                    self.clock.invalidate()
                raise ValueError('Server policy revision moved backwards')
            self._revision_failures = 0
            self._next_revision = self._monotonic() + 1
            if (snapshot is None or revision != snapshot['policy_revision']
                    or self.clock is not None and status.clock != 'ok'):
                if self.clock is not None:
                    self.clock.invalidate()
                return self._refresh(status)
            return replace(status, connection='online')
        except (OSError, sqlite3.Error, HTTPException, ValueError, RequestFailure, StoreIntegrityError) as error:
            if self.clock is not None and isinstance(error, ValueError):
                self.clock.invalidate()
                status = replace(status, clock='unavailable')
            self._revision_failures += 1
            self._next_revision = self._monotonic() + min(2 ** min(self._revision_failures - 1, 6), 60)
            return self._failure(status, error)

    def _download_snapshot(self, status: SyncStatus) -> SyncStatus:
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
            probe = not self.store.test_mode and callable(getattr(self.client, 'policy_revision', None))
            if require_fresh_snapshot or now >= self._next_snapshot:
                status = self._refresh(status)
            elif probe and now >= self._next_revision:
                status = self._check_revision(status)
            if (status.auth == 'paused' or self._stop.is_set()
                    or require_fresh_snapshot and status.clock != 'ok'):
                status = replace(status, retry_after=60.0)
                self._publish(status)
                return status
            pending: list[PendingEvent] = []
            recovered_snapshots: set[str] = set()
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
                            stale_policy = (result.get('data', {}).get('outcome') == 'rejected'
                                            and result.get('data', {}).get('reason') == 'historical_schedule_unavailable'
                                            and json.loads(entry.body)['snapshot_id'] == status.snapshot_id
                                            and status.snapshot_id not in recovered_snapshots)
                            if stale_policy:
                                recovered_snapshots.add(status.snapshot_id)
                                self._refreshing.set()
                            try:
                                self.store.finish(entry.event_id, result)
                            except BaseException:
                                self._refreshing.clear()
                                raise
                            self._event_failures = 0
                            self._next_events = self._monotonic()
                            status = replace(status, connection='online', events_sent=status.events_sent + 1,
                                             event_id=entry.event_id, response=result,
                                             recorded_count=status.recorded_count + int(result.get('data', {}).get('outcome') ==
                                                 ('test_recorded' if self.store.test_mode else 'recorded')))
                            if stale_policy:
                                status = self._refresh(status)
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
            if probe:
                delays.append(self._next_revision - now)
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
