"""Kiosk SQLite versions are independent from standalone Alembic migrations."""
from contextlib import closing
from functools import lru_cache
from pathlib import Path
import sqlite3


class SchemaError(RuntimeError):
    pass


BASELINE = {
    'app': {
        'users': {'id', 'name', 'username', 'password_hash', 'role', 'is_active', 'created_at', 'updated_at'},
        'audit_logs': {'id', 'user_id', 'action', 'entity_type', 'entity_id', 'description', 'created_at'},
        'setup_state': {'id', 'complete', 'code_hash', 'session_hash', 'progress', 'settings'},
    },
    'integration': {
        'snapshot': {'singleton', 'snapshot_id', 'payload'},
        'metadata': {'name', 'payload'},
        'events': {'event_id', 'body', 'response'},
    },
}
APPLICATION_IDS = {'app': 0x41574131, 'integration': 0x41574931}
# Future numbered migrations must include a fixture from the previous version.
MIGRATIONS = {'app': {}, 'integration': {}}


def table_shapes(conn, tables):
    return {table: sorted(tuple(row[1:]) for row in conn.execute(f'PRAGMA table_info("{table}")'))
            for table in tables}


@lru_cache(maxsize=2)
def baseline_shapes(kind):
    with closing(sqlite3.connect(':memory:')) as conn:
        if kind == 'integration':
            conn.execute('CREATE TABLE snapshot (singleton INTEGER PRIMARY KEY CHECK(singleton=1), snapshot_id TEXT NOT NULL, payload BLOB NOT NULL)')
            conn.execute('CREATE TABLE metadata (name TEXT PRIMARY KEY, payload BLOB NOT NULL)')
            conn.execute('CREATE TABLE events (event_id TEXT PRIMARY KEY, body BLOB NOT NULL, response BLOB)')
        else:
            from models import User, AuditLog, SetupState
            from sqlalchemy import create_engine
            from extensions import db
            engine = create_engine('sqlite://', creator=lambda: conn)
            db.metadata.create_all(engine, tables=[User.__table__, AuditLog.__table__, SetupState.__table__])
        return table_shapes(conn, BASELINE[kind])


def migrate_database(path, kind):
    path = Path(path)
    if not path.exists():
        return
    with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=rw', uri=True)) as conn:
        revision = conn.execute('PRAGMA user_version').fetchone()[0]
        app_id = conn.execute('PRAGMA application_id').fetchone()[0]
        latest = max([1, *MIGRATIONS[kind]])
        if revision > latest or app_id not in (0, APPLICATION_IDS[kind]):
            raise SchemaError('Versi database tidak didukung. Data tidak diubah.')
        if revision <= 1:
            tables = {row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
            if tables != set(BASELINE[kind]) or table_shapes(conn, tables) != baseline_shapes(kind):
                raise SchemaError('Skema database tidak dikenal. Pulihkan cadangan asli.')
        try:
            conn.execute('BEGIN IMMEDIATE')
            if revision == 0:
                conn.execute(f'PRAGMA application_id={APPLICATION_IDS[kind]}')
                conn.execute('PRAGMA user_version=1')
                revision = 1
            for target in range(revision + 1, latest + 1):
                MIGRATIONS[kind][target](conn)
                conn.execute(f'PRAGMA user_version={target}')
            conn.commit()
        except BaseException:
            conn.rollback()
            raise


def migrate_instance(instance):
    for name, kind in [('app.sqlite3', 'app'), ('eoffice.sqlite3', 'integration'),
                       ('eoffice-test.sqlite3', 'integration')]:
        migrate_database(Path(instance) / name, kind)
