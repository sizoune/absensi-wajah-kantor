"""Durable, encrypted e-office state independent of standalone employee tables."""

import json
import secrets
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime
from os import PathLike
from pathlib import Path
from threading import RLock
from typing import TypedDict

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from integration.protocol import JSONValue, UUID, validate_snapshot, validate_terminal_response


class ClockAnchorMetadata(TypedDict):
    server_time: str
    issued_at: str
    clock_anchor_id: str
    capture_boot_id: str


def _validate_clock_metadata(value: ClockAnchorMetadata) -> None:
    if (type(value) is not dict
            or set(value) != {'server_time', 'issued_at', 'clock_anchor_id', 'capture_boot_id'}):
        raise ValueError('Invalid integration clock metadata')
    for field in ('clock_anchor_id', 'capture_boot_id'):
        _event_id(value[field])
    for field in ('server_time', 'issued_at'):
        if type(value[field]) is not str:
            raise ValueError('Invalid integration clock timestamp')
        timestamp = datetime.fromisoformat(value[field])
        if timestamp.utcoffset() is None:
            raise ValueError('Expected timezone-aware integration clock timestamp')


class StoreIntegrityError(RuntimeError):
    """The operator must restore the correct key or repair encrypted state."""


@dataclass(frozen=True)
class PendingEvent:
    event_id: str
    body: bytes


def _event_id(value: object) -> str:
    if type(value) is not str or UUID.fullmatch(value) is None:
        raise ValueError('Invalid e-office event ID')
    return value


class IntegrationStore:
    def __init__(self, path: str | PathLike[str], key: bytes) -> None:
        if type(key) is not bytes or len(key) != 32:
            raise ValueError('Integration encryption requires a 32-byte key')
        self._cipher = AESGCM(key)
        self._lock = RLock()
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path, timeout=5.0, check_same_thread=False)
        try:
            self._db.execute('PRAGMA synchronous=FULL')
            with self._db:
                self._db.execute('BEGIN IMMEDIATE')
                self._db.execute('CREATE TABLE IF NOT EXISTS snapshot (singleton INTEGER PRIMARY KEY CHECK(singleton=1), snapshot_id TEXT NOT NULL, payload BLOB NOT NULL)')
                self._db.execute('CREATE TABLE IF NOT EXISTS metadata (name TEXT PRIMARY KEY, payload BLOB NOT NULL)')
                self._db.execute('CREATE TABLE IF NOT EXISTS events (event_id TEXT PRIMARY KEY, body BLOB NOT NULL, response BLOB)')
                row = self._db.execute("SELECT payload FROM metadata WHERE name='key_check'").fetchone()
                if row is None:
                    if self._db.execute('SELECT 1 FROM events UNION ALL SELECT 1 FROM snapshot LIMIT 1').fetchone():
                        raise StoreIntegrityError('Integration key check is missing')
                    self._db.execute('INSERT INTO metadata VALUES (?, ?)',
                                     ('key_check', self._encrypt(b'eoffice-state-v1', 'metadata:key_check')))
                elif self._decrypt(row[0], 'metadata:key_check') != b'eoffice-state-v1':
                    raise StoreIntegrityError('Invalid integration key check')
        except BaseException:
            self._db.close()
            raise

    def _encrypt(self, body: bytes, identity: str) -> bytes:
        nonce = secrets.token_bytes(12)
        return nonce + self._cipher.encrypt(nonce, body, identity.encode('utf-8'))

    def _decrypt(self, payload: bytes, identity: str) -> bytes:
        try:
            return self._cipher.decrypt(payload[:12], payload[12:], identity.encode('utf-8'))
        except (InvalidTag, ValueError, TypeError) as exc:
            raise StoreIntegrityError('Integration state authentication failed; check key and database') from exc

    def enqueue(self, event_bytes: bytes) -> None:
        if type(event_bytes) is not bytes:
            raise ValueError('Expected immutable UTF-8 event bytes')
        try:
            event = json.loads(event_bytes.decode('utf-8'))
        except (ValueError, UnicodeError) as exc:
            raise ValueError('Invalid event JSON') from exc
        if type(event) is not dict:
            raise ValueError('Expected an event object')
        event_id = _event_id(event.get('event_id'))
        identity = f'event:{event_id}'
        with self._lock, self._db:
            self._db.execute('BEGIN IMMEDIATE')
            row = self._db.execute('SELECT body FROM events WHERE event_id=?', (event_id,)).fetchone()
            if row is not None:
                if self._decrypt(row[0], identity) != event_bytes:
                    raise ValueError('Event UUID already has different bytes')
                return
            self._db.execute('INSERT INTO events(event_id,body) VALUES (?,?)',
                             (event_id, self._encrypt(event_bytes, identity)))

    def _validate_stored_response(self, event_id: str, response: bytes) -> None:
        try:
            decoded = json.loads(self._decrypt(response, f'response:{event_id}'))
            validate_terminal_response(event_id, decoded)
        except ValueError as exc:
            raise StoreIntegrityError('Invalid integration terminal response') from exc

    def pending(self) -> list[PendingEvent]:
        with self._lock, closing(self._db.execute('SELECT event_id,body,response FROM events ORDER BY rowid')) as rows:
            pending = []
            for event_id, body, response in rows:
                if response is not None:
                    self._validate_stored_response(event_id, response)
                else:
                    pending.append(PendingEvent(event_id, self._decrypt(body, f'event:{event_id}')))
            return pending

    def replace_snapshot(self, snapshot: dict[str, JSONValue]) -> None:
        validate_snapshot(snapshot)
        body = json.dumps(snapshot, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(',', ':')).encode('utf-8')
        # Detach the validated envelope from the caller before taking the write lock.
        snapshot_id = json.loads(body)['snapshot_id']
        with self._lock, self._db:
            self._db.execute('BEGIN IMMEDIATE')
            row = self._db.execute('SELECT snapshot_id,payload FROM snapshot').fetchone()
            if row is not None:
                old = self._decrypt(row[1], f'snapshot:{row[0]}')
                if row[0] == snapshot_id:
                    if old != body:
                        raise ValueError('Snapshot UUID already has different contents')
                    return
            self._db.execute('INSERT OR REPLACE INTO snapshot VALUES (1,?,?)',
                             (snapshot_id, self._encrypt(body, f'snapshot:{snapshot_id}')))

    def load_snapshot(self) -> dict[str, JSONValue] | None:
        with self._lock:
            row = self._db.execute('SELECT snapshot_id,payload FROM snapshot').fetchone()
            if row is None:
                return None
            snapshot = json.loads(self._decrypt(row[1], f'snapshot:{row[0]}'))
            validate_snapshot(snapshot)
            if snapshot['snapshot_id'] != row[0]:
                raise StoreIntegrityError('Integration snapshot identity mismatch')
            return snapshot

    def finish(self, event_id: str, response: dict[str, JSONValue]) -> None:
        event_id = _event_id(event_id)
        validate_terminal_response(event_id, response)
        body = json.dumps(response, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(',', ':')).encode('utf-8')
        with self._lock, self._db:
            self._db.execute('BEGIN IMMEDIATE')
            row = self._db.execute('SELECT body,response FROM events WHERE event_id=?', (event_id,)).fetchone()
            if row is None:
                raise ValueError('Unknown integration event ID')
            self._decrypt(row[0], f'event:{event_id}')
            if row[1] is not None:
                self._validate_stored_response(event_id, row[1])
                return
            self._db.execute('UPDATE events SET response=? WHERE event_id=?',
                             (self._encrypt(body, f'response:{event_id}'), event_id))

    def terminal(self, event_id: str) -> dict[str, JSONValue] | None:
        """Indexed, authenticated lookup for the current kiosk message after queue overflow."""
        event_id = _event_id(event_id)
        with self._lock:
            row = self._db.execute('SELECT response FROM events WHERE event_id=?', (event_id,)).fetchone()
            if row is None or row[0] is None:
                return None
            self._validate_stored_response(event_id, row[0])
            return json.loads(self._decrypt(row[0], f'response:{event_id}'))

    def save_clock_anchor(self, anchor: ClockAnchorMetadata) -> None:
        _validate_clock_metadata(anchor)
        body = json.dumps(anchor, sort_keys=True, separators=(',', ':')).encode('utf-8')
        with self._lock, self._db:
            self._db.execute('INSERT OR REPLACE INTO metadata VALUES (?,?)',
                             ('clock_anchor', self._encrypt(body, 'metadata:clock_anchor')))

    def load_clock_anchor(self) -> ClockAnchorMetadata | None:
        """Return audit metadata; persisted anchors never authorize new captures."""
        with self._lock:
            row = self._db.execute("SELECT payload FROM metadata WHERE name='clock_anchor'").fetchone()
            if row is None:
                return None
            anchor = json.loads(self._decrypt(row[0], 'metadata:clock_anchor'))
            _validate_clock_metadata(anchor)
            return anchor

    def close(self) -> None:
        with self._lock:
            self._db.close()
