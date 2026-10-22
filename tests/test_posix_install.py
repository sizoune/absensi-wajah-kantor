import json
import os
from pathlib import Path
import shlex
import subprocess

import pytest

from app_version import VERSION
from maintenance import platforms, posix_install
from maintenance.distribution import atomic_json, executable
from services.update_package import UpdateError


@pytest.fixture
def frozen_source(tmp_path, monkeypatch):
    target = 'linux-x64'
    source = tmp_path / 'source'
    source.mkdir()
    program = source / 'Absensi'
    program.write_bytes(b'program')
    program.chmod(0o755)
    (source / 'library').write_bytes(b'library')
    monkeypatch.setattr(platforms, 'platform_id', lambda: target)
    monkeypatch.setattr(posix_install.sys, 'executable', str(program))
    monkeypatch.setattr(posix_install.sys, 'frozen', True, raising=False)
    monkeypatch.setattr(posix_install.sys, '_MEIPASS', str(source), raising=False)
    monkeypatch.setenv('XDG_DATA_HOME', str(tmp_path / 'desktop data'))
    monkeypatch.setattr('maintenance.inference_check.check_inference', lambda path: None)
    return source


@pytest.mark.skipif(os.name == 'nt', reason='POSIX installer uses user ownership and permissions')
def test_linux_install_and_shortcut_preserve_data_on_reinstall(tmp_path, frozen_source):
    root = tmp_path / 'private data ü'
    posix_install.install(root)
    assert json.loads((root / 'current.json').read_text()) == {'version': VERSION}
    assert executable(root, VERSION).read_bytes() == b'program'
    assert root.stat().st_mode & 0o777 == 0o700
    assert subprocess.run(['sh', '-n', root / 'launch.sh']).returncode == 0
    shortcut = (tmp_path / 'desktop data/applications/kioska.desktop').read_text()
    assert 'Terminal=true' in shortcut
    assert str(root / 'launch.sh') in shortcut
    (root / 'instance/device-token').write_bytes(b'keep secret')
    with pytest.raises(UpdateError):
        posix_install.install(root)
    assert (root / 'instance/device-token').read_bytes() == b'keep secret'


@pytest.mark.skipif(os.name == 'nt', reason='POSIX installer')
@pytest.mark.parametrize('legacy', ['.env', 'instance/device-token', 'current.json'])
def test_install_never_overwrites_existing_data(tmp_path, frozen_source, legacy):
    root = tmp_path / 'installed'
    path = root / legacy
    path.parent.mkdir(parents=True)
    path.write_bytes(b'preserve')
    with pytest.raises(UpdateError):
        posix_install.install(root)
    assert path.read_bytes() == b'preserve'
    assert not (root / 'versions').exists()


@pytest.mark.skipif(os.name == 'nt', reason='POSIX app bundle')
def test_mac_install_preserves_bundle_links(tmp_path, frozen_source, monkeypatch):
    source = tmp_path / 'Absensi.app'
    program = source / 'Contents/MacOS/Absensi'
    program.parent.mkdir(parents=True)
    program.write_bytes(b'mac binary')
    program.chmod(0o755)
    (source / 'Contents/Resources').mkdir()
    (source / 'Contents/Resources/link').symlink_to('../MacOS/Absensi')
    monkeypatch.setattr(platforms, 'platform_id', lambda: 'macos-arm64')
    monkeypatch.setattr(posix_install.sys, 'executable', str(program))
    root = tmp_path / 'private'
    posix_install.install(root)
    copied = root / 'versions' / VERSION / 'Absensi.app/Contents/Resources/link'
    assert copied.is_symlink()
    assert copied.read_bytes() == b'mac binary'


def test_open_selects_current_version_without_reinitializing(tmp_path, monkeypatch):
    monkeypatch.setattr(platforms, 'platform_id', lambda: 'linux-x64')
    atomic_json(tmp_path / 'current.json', {'version': '2.0.0'})
    calls = []
    monkeypatch.setattr(posix_install.os, 'execve', lambda *args: calls.append(args))
    monkeypatch.setattr(posix_install, 'install', lambda root: pytest.fail('must not reinstall'))
    posix_install.open_active(tmp_path)
    assert calls[0][0] == executable(tmp_path, '2.0.0')
    assert calls[0][1][-1] == 'launch'
    assert calls[0][2]['PYINSTALLER_RESET_ENVIRONMENT'] == '1'
    atomic_json(tmp_path / 'updates/journal.json', {'stage': 'applying'})
    with pytest.raises(UpdateError):
        posix_install.open_active(tmp_path)
    assert len(calls) == 1


@pytest.mark.parametrize('original', [None, '/system/lib'])
def test_external_commands_do_not_inherit_bundled_libraries(monkeypatch, original):
    monkeypatch.setattr(posix_install.sys, 'frozen', True, raising=False)
    monkeypatch.setenv('LD_LIBRARY_PATH', '/frozen/_internal')
    monkeypatch.delenv('LD_LIBRARY_PATH_ORIG', raising=False)
    if original:
        monkeypatch.setenv('LD_LIBRARY_PATH_ORIG', original)
    environment = posix_install.external_environment()
    assert environment.get('LD_LIBRARY_PATH') == original
    assert 'LD_LIBRARY_PATH_ORIG' not in environment
    assert os.environ['LD_LIBRARY_PATH'] == '/frozen/_internal'


@pytest.mark.skipif(os.name == 'nt', reason='POSIX command file permissions')
def test_mac_update_launch_uses_private_command_and_literal_arguments(tmp_path, monkeypatch):
    monkeypatch.setattr(posix_install.sys, 'platform', 'darwin')
    calls = []
    monkeypatch.setattr(posix_install.subprocess, 'Popen', lambda *args, **kw: calls.append((args, kw)))
    command = ['/path with spaces/Absensi', '--root', str(tmp_path / "root's data"),
               'launch', '--transaction', 'a' * 32]
    posix_install.launch_terminal(command, tmp_path)
    script = tmp_path / 'updates/launch.command'
    assert script.stat().st_mode & 0o777 == 0o700
    assert shlex.split(script.read_text().split('exec ', 1)[1]) == command
    assert calls[0][0][0] == ['/usr/bin/open', '-a', 'Terminal', str(script)]
    assert calls[0][1]['start_new_session']


def test_linux_update_launch_requires_terminal(tmp_path, monkeypatch):
    monkeypatch.setattr(posix_install.sys, 'platform', 'linux')
    monkeypatch.setattr(posix_install.shutil, 'which', lambda name: None)
    with pytest.raises(UpdateError, match='terminal'):
        posix_install.launch_terminal(['/program'], tmp_path)


@pytest.mark.skipif(os.name == 'nt', reason='POSIX symlinks')
@pytest.mark.parametrize('name', ['instance', 'updates', 'backups', 'versions', 'current.json'])
def test_installer_rejects_symlink_storage_before_writing(tmp_path, frozen_source, name):
    root = tmp_path / 'private'
    root.mkdir()
    outside = tmp_path / 'outside'
    outside.mkdir()
    (root / name).symlink_to(outside, target_is_directory=True)
    with pytest.raises(UpdateError, match='tautan'):
        posix_install.install(root)
    assert not list(outside.iterdir())
    assert not (root / 'current.json').is_file()


def test_recovery_selects_active_runtime_even_when_startup_is_blocked(tmp_path, monkeypatch):
    monkeypatch.setattr(platforms, 'platform_id', lambda: 'linux-x64')
    atomic_json(tmp_path / 'current.json', {'version': '2.0.0'})
    atomic_json(tmp_path / 'updates/journal.json', {'stage': 'applying'})
    calls = []
    monkeypatch.setattr(posix_install.os, 'execve', lambda *args: calls.append(args))
    posix_install.open_active(tmp_path, 'recover')
    assert calls[0][1][-1] == 'recover'
    assert calls[0][0] == executable(tmp_path, '2.0.0')


def test_recovery_never_initializes_a_missing_install(tmp_path, monkeypatch):
    monkeypatch.setattr(posix_install, 'install', lambda root: pytest.fail('must not install'))
    with pytest.raises(UpdateError):
        posix_install.open_active(tmp_path, 'recover')
