import sqlite3
from contextlib import closing

import pytest

from maintenance.kiosk_schema import SchemaError, migrate_database


def test_newer_and_unknown_schemas_rejected_without_modification(tmp_path):
    path = tmp_path / 'app.sqlite3'
    with closing(sqlite3.connect(path)) as conn, conn:
        conn.execute('CREATE TABLE foreign_data (id INTEGER)')
    before = path.read_bytes()
    with pytest.raises(SchemaError):
        migrate_database(path, 'app')
    assert path.read_bytes() == before
    with closing(sqlite3.connect(path)) as conn, conn:
        conn.execute('PRAGMA user_version=99')
    before = path.read_bytes()
    with pytest.raises(SchemaError):
        migrate_database(path, 'app')
    assert path.read_bytes() == before


def test_baseline_integration_keeps_encrypted_events(tmp_path):
    from integration.store import IntegrationStore
    path = tmp_path / 'eoffice.sqlite3'
    key = bytes(range(32))
    body = b'{"event_id":"85f42b66-6a77-4c56-9efc-4231ff6eab42"}'
    store = IntegrationStore(path, key)
    store.enqueue(body)
    store.close()
    with closing(sqlite3.connect(path)) as conn, conn:
        encrypted = conn.execute('SELECT * FROM events').fetchall()
    migrate_database(path, 'integration')
    migrate_database(path, 'integration')
    with closing(sqlite3.connect(path)) as conn, conn:
        assert conn.execute('PRAGMA user_version').fetchone()[0] == 1
        assert conn.execute('SELECT * FROM events').fetchall() == encrypted
    reopened = IntegrationStore(path, key)
    assert reopened.pending()[0].body == body
    reopened.close()


def test_failed_forward_migration_does_not_stamp_or_commit(tmp_path, monkeypatch):
    from integration.store import IntegrationStore
    import maintenance.kiosk_schema as schema
    path = tmp_path / 'eoffice.sqlite3'
    IntegrationStore(path, bytes(range(32))).close()
    migrate_database(path, 'integration')
    def broken(conn):
        conn.execute('CREATE TABLE temporary_change (id INTEGER)')
        raise RuntimeError('migration failed')
    monkeypatch.setitem(schema.MIGRATIONS['integration'], 2, broken)
    with pytest.raises(RuntimeError):
        migrate_database(path, 'integration')
    with closing(sqlite3.connect(path)) as conn, conn:
        assert conn.execute('PRAGMA user_version').fetchone()[0] == 1
        assert not conn.execute("SELECT name FROM sqlite_master WHERE name='temporary_change'").fetchall()


def test_known_column_names_with_wrong_types_are_not_adopted(tmp_path):
    from maintenance.kiosk_schema import BASELINE
    path = tmp_path / 'bad.sqlite3'
    with closing(sqlite3.connect(path)) as conn, conn:
        for table, columns in BASELINE['integration'].items():
            conn.execute(f'CREATE TABLE {table} (' + ','.join(f'{name} INTEGER' for name in columns) + ')')
    before = path.read_bytes()
    with pytest.raises(SchemaError):
        migrate_database(path, 'integration')
    assert path.read_bytes() == before
