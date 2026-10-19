"""Launcher checks use real Flask/HTTP and controlled desktop process boundaries."""
import io
from pathlib import Path
import re
import socket
import subprocess
from types import SimpleNamespace
from urllib.request import urlopen

import pytest
import waitress
import webbrowser

from kiosk.lock import AlreadyRunning, SingleInstanceLock
from test_setup import csrf, exchange, installation, legacy_installation, local_code


class LocalConsole(io.StringIO):
    def isatty(self):
        return True


@pytest.fixture
def local_http(monkeypatch):
    from maintenance import windows_launcher

    class EphemeralSocket(socket.socket):
        def bind(self, address):
            assert address == ('127.0.0.1', 5000)
            super().bind((address[0], 0))

    monkeypatch.setattr(windows_launcher, 'socket', SimpleNamespace(
        socket=EphemeralSocket, AF_INET=socket.AF_INET, SOCK_STREAM=socket.SOCK_STREAM,
        SOL_SOCKET=socket.SOL_SOCKET, SO_EXCLUSIVEADDRUSE=getattr(socket, 'SO_EXCLUSIVEADDRUSE', -5)))


def test_launcher_shows_setup_code_only_locally_and_serves_wizard(tmp_path, monkeypatch, caplog, local_http):
    from maintenance.windows_launcher import run

    app = installation(tmp_path)
    console = LocalConsole()

    def browser(url):
        code = re.search(r'Kode setup: (\S+)', console.getvalue())[1]
        assert code not in url
        assert url.endswith('/setup')
        with urlopen(url, timeout=2) as response:
            assert response.status == 200
            assert code not in response.read().decode()
        client = app.test_client()
        assert exchange(client, code).status_code == 302
        assert client.get('/setup/status').status_code == 200
        assert code not in caplog.text
        raise KeyboardInterrupt

    monkeypatch.setattr(webbrowser, 'open', browser)
    run(app, console=console)
    assert 'Kode setup:' in console.getvalue()


@pytest.mark.parametrize(('hold', 'pending_scope', 'pending_credentials'), [
    (None, False, False), ('scope', False, False), ('credentials', False, False),
    ('launcher', False, False), ('launcher', True, False), ('launcher', False, True)])
def test_native_retry_stops_cleanly_and_preserves_settings_holds(
        tmp_path, monkeypatch, hold, pending_scope, pending_credentials, local_http):
    from maintenance.windows_launcher import run

    lock = tmp_path / 'camera.lock'
    pause = Path(str(lock) + '.pause')
    app = installation(tmp_path, CAMERA_SOURCE='native', KIOSK_LOCK_FILE=str(lock))
    if hold:
        pause.write_text(hold + '\n')
    if pending_scope:
        app.config['SCOPE_CHANGE'] = {'skpd_id': 79, 'scope_type': 'parent', 'subunit_id': None}
    app.config['CREDENTIAL_REPAIR_PENDING'] = pending_credentials
    held = hold in ('scope', 'credentials') or pending_scope or pending_credentials
    children = []

    class NativeRetry:
        def __init__(self, args, **kwargs):
            assert args[-1] == r'scripts\jalankan_kios.bat'
            self.stop = Path(kwargs['env']['ABSENSI_LAUNCHER_STOP_FILE'])
            assert not self.stop.exists()
            self.exited = False
            children.append(self)

        def poll(self):
            return None

        def wait(self, timeout):
            assert self.stop.exists()
            assert pause.exists(), 'Native drain must be fenced before retry exits'
            self.exited = True
            return 0

        def terminate(self):
            raise AssertionError('Native worker must drain without being killed')

        kill = terminate

    def browser(url):
        assert len(children) == 1
        assert pause.exists() == held
        raise KeyboardInterrupt

    monkeypatch.setattr(subprocess, 'Popen', NativeRetry)
    monkeypatch.setattr(webbrowser, 'open', browser)
    run(app, console=LocalConsole())
    assert children[0].exited
    assert pause.exists() == held
    if pause.exists():
        assert pause.read_text() == hold + '\n'


def test_packaged_shutdown_keeps_launcher_owned_until_native_drain(tmp_path, monkeypatch, local_http):
    from threading import Event
    from maintenance.windows_launcher import run
    app = installation(tmp_path, CAMERA_SOURCE='native', KIOSK_LOCK_FILE=str(tmp_path / 'kiosk.lock'))
    app.config['UPDATE_INSTALL_ROOT'] = str(tmp_path)
    app.extensions['launcher_stop'] = stop = Event()
    waits = []
    class Native:
        def __init__(self, args, **kwargs):
            assert args[-1] == 'kiosk'
        def poll(self):
            return None
        def wait(self, timeout):
            with pytest.raises(AlreadyRunning):
                SingleInstanceLock(tmp_path / 'launcher.lock').acquire()
            waits.append(timeout)
            if len(waits) == 1:
                raise subprocess.TimeoutExpired('native drain', timeout)
            return 0
    def browser(url):
        stop.set()
        return True
    monkeypatch.setattr(subprocess, 'Popen', Native)
    monkeypatch.setattr(webbrowser, 'open', browser)
    run(app, console=LocalConsole())
    assert len(waits) == 2
    assert not (tmp_path / 'kiosk.lock.pause').exists()


@pytest.mark.parametrize('blocked', ['duplicate', 'redirected', 'port'])
def test_failed_or_duplicate_launcher_never_rotates_setup_access(tmp_path, monkeypatch, blocked, local_http):
    from maintenance.windows_launcher import run

    app = installation(tmp_path)
    code = local_code(app)
    console = io.StringIO() if blocked == 'redirected' else LocalConsole()
    if blocked == 'duplicate':
        with SingleInstanceLock(tmp_path / 'launcher.lock'):
            with pytest.raises(AlreadyRunning):
                run(app, console=console)
    elif blocked == 'redirected':
        with pytest.raises(RuntimeError, match='terminal lokal'):
            run(app, console=console)
    else:
        def busy(*args, **kwargs):
            raise OSError('Address already in use')
        monkeypatch.setattr(waitress, 'create_server', busy)
        with pytest.raises(OSError):
            run(app, console=console)
    assert 'Kode setup:' not in console.getvalue()
    client = app.test_client()
    assert exchange(client, code).status_code == 302
    assert client.get('/setup/status').status_code == 200


def test_completed_installation_launches_without_setup_or_changing_device_data(tmp_path, monkeypatch, local_http):
    from maintenance.windows_launcher import run

    key_file, _, _, _ = legacy_installation(tmp_path)
    models = {}
    for name in ('EOFFICE_AURAFACE_PATH', 'EOFFICE_PERIOCULAR_PATH'):
        path = tmp_path / name
        path.write_bytes(b'existing-model')
        models[name] = str(path)
    app = installation(tmp_path, EOFFICE_BASE_URL='https://old.example',
                       EOFFICE_DEVICE_TOKEN='retained-device-token', **models)
    preserved = {path: path.read_bytes() for path in (
        key_file, tmp_path / 'secret_key', tmp_path / 'eoffice.sqlite3',
        *(Path(path) for path in models.values()))}

    def browser(url):
        assert url.endswith('/kiosk/')
        client = app.test_client()
        assert client.get('/setup').status_code == 404
        assert app.test_cli_runner().invoke(args=['setup-code']).exit_code != 0
        assert client.post('/login', data={'csrf_token': csrf(client, '/login'),
            'username': 'legacy_admin', 'password': 'PasswordLama123'}).status_code == 302
        raise KeyboardInterrupt

    monkeypatch.setattr(webbrowser, 'open', browser)
    console = io.StringIO()
    run(app, console=console)
    assert 'Kode setup:' not in console.getvalue()
    assert {path: path.read_bytes() for path in preserved} == preserved


def test_launcher_entry_keeps_startup_failure_visible_without_printing_secrets(monkeypatch, capsys):
    from sqlalchemy.exc import SQLAlchemyError
    from maintenance import windows_launcher

    def unavailable():
        raise SQLAlchemyError('database password: must-not-be-printed')

    monkeypatch.setattr(windows_launcher, 'os', SimpleNamespace(name='nt', chdir=lambda path: None))
    monkeypatch.setattr(windows_launcher, 'run', unavailable)
    monkeypatch.setattr(windows_launcher.sys, 'stdin', io.StringIO())
    with pytest.raises(SystemExit) as failure:
        windows_launcher.main()
    assert failure.value.code == 1
    output = capsys.readouterr()
    assert 'Aplikasi belum dapat dijalankan' in output.err
    assert 'must-not-be-printed' not in output.out + output.err


def test_windows_port_conflict_fails_exclusively_before_setup_access_changes(tmp_path, monkeypatch):
    from maintenance import windows_launcher

    app = installation(tmp_path)
    code = local_code(app)
    console = LocalConsole()
    exclusive = -5  # Windows SO_EXCLUSIVEADDRUSE; controlled Winsock boundary.

    class WindowsSocket:
        def __init__(self, family, kind):
            self.exclusive = False
            self.closed = False

        def setsockopt(self, level, option, value):
            assert option == exclusive and value == 1
            self.exclusive = True

        def bind(self, address):
            assert address == ('127.0.0.1', 5000)
            assert self.exclusive, 'Reusable Windows bind could accept another server on this port'
            raise OSError('Address already occupied by another Waitress installation')

        def close(self):
            self.closed = True

    listener = WindowsSocket(2, 1)

    def unsafe_waitress(*args, **kwargs):
        raise AssertionError('The exclusive bind must fail before starting Waitress')

    monkeypatch.setattr(windows_launcher, 'os', SimpleNamespace(name='nt'))
    monkeypatch.setattr(windows_launcher, 'socket', SimpleNamespace(
        socket=lambda family, kind: listener, AF_INET=2, SOCK_STREAM=1,
        SOL_SOCKET=65535, SO_EXCLUSIVEADDRUSE=exclusive), raising=False)
    monkeypatch.setattr(waitress, 'create_server', unsafe_waitress)
    with pytest.raises(OSError):
        windows_launcher.run(app, console=console)
    assert listener.closed
    assert 'Kode setup:' not in console.getvalue()
    assert exchange(app.test_client(), code).status_code == 302
