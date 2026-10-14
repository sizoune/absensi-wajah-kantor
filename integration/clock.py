"""Capture time advances from authenticated server time within this process only."""

import math
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from threading import RLock
from typing import TypedDict
from uuid import uuid4

from integration.protocol import MAX_SAFE_INTEGER, UUID
from integration.store import ClockAnchorMetadata, IntegrationStore

WITA = timezone(timedelta(hours=8))
MAX_WALL_JUMP_SECONDS = 30
MAX_ANCHOR_UNCERTAINTY_SECONDS = 2


class ClockUnavailable(RuntimeError):
    """A fresh authenticated anchor is required before another capture."""


class EventTime(TypedDict):
    occurred_at: str
    clock_anchor_id: str
    elapsed_ms: int


@dataclass(frozen=True)
class _Anchor:
    server_time: datetime
    issued_at: datetime
    anchor_id: str
    monotonic: float
    wall_time: float


def _seconds(value: object) -> float:
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
        raise ValueError('Expected finite nonnegative clock seconds')
    return float(value)


def _timestamp(value: datetime | str) -> datetime:
    if type(value) is str:
        try:
            value = datetime.fromisoformat(value)
        except ValueError as exc:
            raise ValueError('Expected authenticated ISO server time') from exc
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise ValueError('Expected timezone-aware authenticated server time')
    return value.astimezone(WITA)


def _audit_time(value: datetime) -> str:
    return value.isoformat(timespec='microseconds' if value.microsecond % 1000 else 'milliseconds')


class TrustedClock:
    def __init__(self, store: IntegrationStore | None = None, *, wall_time: Callable[[], float] = time.time) -> None:
        if store is not None and not isinstance(store, IntegrationStore):
            raise ValueError('Expected an integration store')
        if not callable(wall_time):
            raise ValueError('Expected a wall-clock reader')
        self._store = store
        self._wall_time = wall_time
        self._capture_boot_id = str(uuid4())
        self._lock = RLock()
        self._anchor: _Anchor | None = None

    def anchor(self, server_time: datetime | str, anchor_id: str, monotonic_now: float,
               issued_at: datetime | str | None = None, *,
               wall_time_at_anchor: float | None = None, uncertainty_seconds: float = 0) -> None:
        """Only call with time from an authenticated response, never cached state."""
        with self._lock:
            previous = self._anchor
            self._anchor = None
            server = _timestamp(server_time)
            origin = server if issued_at is None else _timestamp(issued_at)
            monotonic = _seconds(monotonic_now)
            wall = _seconds(self._wall_time() if wall_time_at_anchor is None else wall_time_at_anchor)
            if type(anchor_id) is not str or UUID.fullmatch(anchor_id) is None:
                raise ValueError('Invalid integration clock anchor ID')
            current = server
            if server < origin:
                # HTTP Date floors to seconds; only that initial same-second floor is valid.
                if server.microsecond != 0 or origin - server >= timedelta(seconds=1):
                    raise ValueError('Server clock precedes snapshot issuance')
                current = origin
            uncertainty = _seconds(uncertainty_seconds)
            if uncertainty > MAX_ANCHOR_UNCERTAINTY_SECONDS:
                raise ValueError('Server time uncertainty exceeds clock tolerance')
            if previous is not None:
                delta = monotonic - previous.monotonic
                predicted = previous.server_time + timedelta(seconds=delta)
                if delta < 0 or predicted > server + timedelta(seconds=uncertainty):
                    raise ValueError('Authenticated server time moved backwards')
                # Both are lower bounds; retain elapsed time when Date floors a refresh.
                current = max(current, predicted)
            metadata: ClockAnchorMetadata = {
                'server_time': _audit_time(server), 'issued_at': _audit_time(origin),
                'clock_anchor_id': anchor_id, 'capture_boot_id': self._capture_boot_id,
            }
            if self._store is not None:
                self._store.save_clock_anchor(metadata)
            self._anchor = _Anchor(current, origin, anchor_id, monotonic, wall)

    def invalidate(self) -> None:
        with self._lock:
            self._anchor = None

    def event_time(self, monotonic_now: float) -> EventTime:
        with self._lock:
            anchor = self._anchor
            if anchor is None:
                raise ClockUnavailable('Authenticated server time is required')
            try:
                delta = _seconds(monotonic_now) - anchor.monotonic
                wall_delta = _seconds(self._wall_time()) - anchor.wall_time
                if delta < 0 or abs(wall_delta - delta) > MAX_WALL_JUMP_SECONDS:
                    raise ValueError('Clock changed since authenticated anchor')
                capture = anchor.server_time + timedelta(seconds=delta)
                elapsed_ms = (capture - anchor.issued_at) // timedelta(milliseconds=1)
                if elapsed_ms > MAX_SAFE_INTEGER:
                    raise ValueError('Clock elapsed time exceeds protocol limit')
            except (ValueError, OverflowError) as exc:
                self._anchor = None
                raise ClockUnavailable('Clock changed; authenticated refresh required') from exc
            return {'occurred_at': capture.isoformat(timespec='milliseconds'),
                    'clock_anchor_id': anchor.anchor_id, 'elapsed_ms': elapsed_ms}
