import json
from pathlib import Path
import sqlite3
from contextlib import closing

import pytest

from maintenance.distribution import atomic_json, backup_instance, restore_instance, startup_allowed
from services.update_package import UpdateError


def test_backup_restore_keeps_secrets_and_queue_bytes(tmp_path):
    instance = tmp_path / 'instance'
    instance.mkdir()
    (instance / 'secret_key').write_bytes(b'original secret')
    with closing(sqlite3.connect(instance / 'eoffice.sqlite3')) as conn, conn:
        conn.execute('CREATE TABLE events (id TEXT, body BLOB)')
        conn.execute('INSERT INTO events VALUES (?,?)', ('original-uuid', b'original encrypted bytes'))
    backup = backup_instance(tmp_path, 'test')
    (instance / 'secret_key').write_bytes(b'wrong secret')
    with closing(sqlite3.connect(instance / 'eoffice.sqlite3')) as conn, conn:
        conn.execute('DELETE FROM events')
    (instance / 'app.sqlite3').write_bytes(b'partial migration')
    restore_instance(tmp_path, backup)
    assert (instance / 'secret_key').read_bytes() == b'original secret'
    assert not (instance / 'app.sqlite3').exists()
    with closing(sqlite3.connect(instance / 'eoffice.sqlite3')) as conn, conn:
        assert conn.execute('SELECT * FROM events').fetchall() == [('original-uuid', b'original encrypted bytes')]


def test_corrupt_backup_never_overwrites_current_data(tmp_path):
    (tmp_path / 'instance').mkdir()
    source = tmp_path / 'instance/secret_key'
    source.write_bytes(b'secret')
    backup = backup_instance(tmp_path, 'test')
    (backup / 'secret_key').write_bytes(b'corrupt')
    source.write_bytes(b'keep current')
    with pytest.raises(UpdateError):
        restore_instance(tmp_path, backup)
    assert source.read_bytes() == b'keep current'


@pytest.mark.parametrize('stage', ['stopping', 'backing_up', 'applying', 'restarting', 'corrupt'])
def test_interrupted_transaction_blocks_normal_startup(tmp_path, stage):
    (tmp_path / 'updates').mkdir()
    if stage == 'corrupt':
        (tmp_path / 'updates/journal.json').write_text('{')
    else:
        atomic_json(tmp_path / 'updates/journal.json', {'stage': stage, 'recovery_required': True})
    with pytest.raises(UpdateError):
        startup_allowed(tmp_path)


def test_atomic_json_never_leaves_partial_target(tmp_path):
    path = tmp_path / 'current.json'
    atomic_json(path, {'version': '1.0.0'})
    assert json.loads(path.read_text()) == {'version': '1.0.0'}
    assert not list(tmp_path.glob('*.tmp'))


@pytest.fixture(autouse=True, params=['windows-x64', 'macos-arm64', 'macos-x64', 'linux-x64'])
def transaction_platform(request, monkeypatch):
    from maintenance import platforms
    monkeypatch.setattr(platforms, 'platform_id', lambda: request.param)


def transaction_root(tmp_path, monkeypatch):
    import maintenance.windows_update as update
    from test_update_package import signed
    monkeypatch.setattr(update, 'VERSION', '1.1.0')
    instance = tmp_path / 'instance'
    instance.mkdir()
    (instance / 'secret_key').write_bytes(b'keep-secret')
    atomic_json(tmp_path / 'current.json', {'version': '1.0.0'})
    transaction = 'a' * 32
    work = tmp_path / 'updates' / transaction
    work.mkdir(parents=True)
    from maintenance import platforms
    raw, signature, key = signed(platform=platforms.platform_id())
    (work / 'update.json').write_bytes(raw)
    (work / 'update.sig').write_bytes(signature)
    for version in ('1.0.0', '1.1.0'):
        from maintenance.distribution import executable
        path = executable(tmp_path, version)
        path.parent.mkdir(parents=True)
        path.write_bytes(b'exe')
    atomic_json(tmp_path / 'updates/journal.json', dict(stage='stopping', transaction=transaction,
                previous='1.0.0', target='1.1.0', recovery_required=True, activated=False))
    return update, transaction, key


def test_failed_migration_restores_secret_and_old_version(tmp_path, monkeypatch):
    update, transaction, key = transaction_root(tmp_path, monkeypatch)
    def fail(*args, **kwargs):
        (tmp_path / 'instance/secret_key').write_bytes(b'migration damaged secret')
        raise RuntimeError('failed')
    monkeypatch.setattr(update.subprocess, 'run', fail)
    launched = []
    monkeypatch.setattr(update, 'start_version', lambda root, version, *args: launched.append(version))
    with pytest.raises(RuntimeError):
        update.apply_update(tmp_path, transaction, key)
    assert (tmp_path / 'instance/secret_key').read_bytes() == b'keep-secret'
    assert json.loads((tmp_path / 'current.json').read_text())['version'] == '1.0.0'
    assert launched == ['1.0.0']


def test_success_commits_before_capture_and_never_rolls_back_new_data(tmp_path, monkeypatch):
    update, transaction, key = transaction_root(tmp_path, monkeypatch)
    monkeypatch.setattr(update.subprocess, 'run', lambda *a, **kw: None)
    def launch(root, version, tx):
        update.activate(root, tx)
        (tmp_path / 'instance/secret_key').write_bytes(b'new data after activation')
        return object()
    monkeypatch.setattr(update, 'start_version', launch)
    update.apply_update(tmp_path, transaction, key)
    assert json.loads((tmp_path / 'current.json').read_text())['version'] == '1.1.0'
    with pytest.raises(UpdateError):
        update.recover(tmp_path)
    assert (tmp_path / 'instance/secret_key').read_bytes() == b'new data after activation'


def test_shutdown_timeout_does_not_backup_or_restore_live_data(tmp_path, monkeypatch):
    from contextlib import contextmanager
    update, transaction, key = transaction_root(tmp_path, monkeypatch)
    original = update.wait_lock
    @contextmanager
    def timeout(path, seconds=60):
        if Path(path).name == 'launcher.lock':
            raise UpdateError('still running')
        with original(path, seconds) as lock:
            yield lock
    monkeypatch.setattr(update, 'wait_lock', timeout)
    from kiosk.lock import SingleInstanceLock
    with SingleInstanceLock(tmp_path / 'instance/launcher.lock'):
        with pytest.raises(UpdateError):
            update.apply_update(tmp_path, transaction, key)
    state = json.loads((tmp_path / 'updates/journal.json').read_text())
    assert state['recovery_required']
    assert not (tmp_path / 'backups').exists()
    assert (tmp_path / 'instance/secret_key').read_bytes() == b'keep-secret'


def test_restarted_executable_gets_independent_frozen_environment(tmp_path, monkeypatch):
    import maintenance.windows_update as update
    calls = []
    monkeypatch.setattr(update.subprocess, 'Popen', lambda args, **kwargs: calls.append((args, kwargs)))
    monkeypatch.setattr('shutil.which', lambda name: '/usr/bin/x-terminal-emulator')
    update.start_version(tmp_path, '1.0.0')
    assert calls[0][1]['env']['PYINSTALLER_RESET_ENVIRONMENT'] == '1'


def test_windows_metadata_replace_requests_write_through(monkeypatch):
    import ctypes
    from types import SimpleNamespace
    import maintenance.distribution as distribution
    calls = []
    class Move:
        def __call__(self, source, destination, flags):
            calls.append((source, destination, flags))
            return True
    monkeypatch.setattr(distribution, 'os', SimpleNamespace(name='nt'))
    monkeypatch.setattr(ctypes, 'WinDLL', lambda *args, **kwargs: SimpleNamespace(MoveFileExW=Move()), raising=False)
    distribution.replace_file('old', 'new')
    assert calls == [('old', 'new', 0x1 | 0x8)]


@pytest.mark.skipif(__import__('os').name == 'nt', reason='POSIX terminal wrapper can exit before its child')
def test_terminal_wrapper_exit_does_not_abort_candidate_activation(tmp_path, monkeypatch):
    from types import SimpleNamespace
    update, transaction, key = transaction_root(tmp_path, monkeypatch)
    monkeypatch.setattr(update.subprocess, 'run', lambda *args, **kwargs: None)
    monkeypatch.setattr(update, 'start_version', lambda *args: SimpleNamespace(poll=lambda: 0, returncode=0))
    monkeypatch.setattr(update.time, 'sleep', lambda seconds: update.activate(tmp_path, transaction))
    update.apply_update(tmp_path, transaction, key)
    state = json.loads((tmp_path / 'updates/journal.json').read_text())
    assert state['stage'] == 'completed'
    assert state['activated']
    assert not state.get('rolled_back')
