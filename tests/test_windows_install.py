"""Installer filesystem/CLI checks; external Windows installers are controlled."""
from pathlib import Path
import subprocess

import pytest

from test_setup import csrf, installation, legacy_installation


def test_reinstall_updates_code_without_replacing_existing_installation_data(tmp_path, monkeypatch):
    from maintenance.windows_install import install

    source, target = tmp_path / 'release', tmp_path / 'installed app'
    source.mkdir()
    target.mkdir()
    key_file, _, _, _ = legacy_installation(target)
    (source / 'app.py').write_text('# new release\n')
    (source / 'scripts').mkdir()
    (source / 'scripts' / 'launcher.bat').write_bytes(b'@echo off\necho ready\n')
    for name in ('requirements.txt', 'requirements-eoffice.txt'):
        (source / name).write_text('example==1.0\n')
    (target / '.env').write_text('UNCHANGED=local-secret\n')
    model = target / 'model_files' / 'valid.onnx'
    model.parent.mkdir()
    model.write_bytes(b'valid-model')
    python = target / '.venv' / 'Scripts' / 'python.exe'
    python.parent.mkdir(parents=True)
    python.write_bytes(b'existing-compatible-runtime')
    preserved = {path: path.read_bytes() for path in (
        target / '.env', key_file, target / 'secret_key', target / 'app.sqlite3',
        target / 'eoffice.sqlite3', model, python)}
    monkeypatch.setattr(subprocess, 'run', lambda args, **kwargs:
                        subprocess.CompletedProcess(args, 0, stdout='', stderr=''))

    install(source, target)
    install(source, target)

    assert (target / 'app.py').read_text() == '# new release\n'
    assert (target / 'scripts' / 'launcher.bat').read_bytes() == b'@echo off\r\necho ready\r\n'
    assert {path: path.read_bytes() for path in preserved} == preserved
    app = installation(target)
    client = app.test_client()
    assert client.post('/login', data={'csrf_token': csrf(client, '/login'),
        'username': 'legacy_admin', 'password': 'PasswordLama123'}).status_code == 302


@pytest.mark.parametrize('existing', ['.env', 'instance', '.venv', 'model_files'])
def test_installer_never_relocates_an_existing_installation(tmp_path, existing):
    from maintenance.windows_install import install

    source, target = tmp_path / 'old install', tmp_path / 'new folder'
    source.mkdir()
    if existing == '.env':
        (source / existing).write_text('PRESERVE=old\n')
    else:
        (source / existing).mkdir()
    with pytest.raises(ValueError, match='instalasi lama'):
        install(source, target)
    assert not target.exists()


@pytest.mark.parametrize('failure', ['runtime', 'pip-bootstrap', 'install', 'check', 'imports', 'storage'])
def test_interrupted_or_failed_install_keeps_existing_data_and_resumes(tmp_path, monkeypatch, failure):
    from maintenance.windows_install import install

    target = tmp_path / 'existing install'
    target.mkdir()
    legacy_installation(target)
    python = target / '.venv' / 'Scripts' / 'python.exe'
    python.parent.mkdir(parents=True)
    python.write_bytes(b'preserved-runtime')
    preserved = {path: path.read_bytes() for path in target.rglob('*') if path.is_file()}

    def program(args, **kwargs):
        if args[1:4] == ['-m', 'pip', 'install']:
            stage = 'install'
            assert '--only-binary=:all:' in args
        elif args[1:4] == ['-m', 'pip', 'check']:
            stage = 'check'
        elif args[1:3] == ['-m', 'ensurepip']:
            stage = 'pip-bootstrap'
        elif args[-1].startswith('import platform'):
            stage = 'runtime'
        elif args[-1].startswith('import cv2'):
            stage = 'imports'
        else:
            stage = 'storage'
        assert args[0] == str(python)
        if stage == failure:
            raise subprocess.CalledProcessError(1, args)
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr(subprocess, 'run', program)
    with pytest.raises(subprocess.CalledProcessError):
        install(target, target)
    assert {path: path.read_bytes() for path in preserved} == preserved
    failure = None
    install(target, target)
    assert {path: path.read_bytes() for path in preserved} == preserved


def test_partial_virtualenv_is_completed_without_deleting_its_files(tmp_path, monkeypatch):
    from maintenance.windows_install import install

    target = tmp_path / 'partial install'
    partial = target / '.venv' / 'partial-package'
    partial.parent.mkdir(parents=True)
    partial.write_bytes(b'keep-during-resume')
    python = target / '.venv' / 'Scripts' / 'python.exe'

    def program(args, **kwargs):
        if args[1:3] == ['-m', 'venv']:
            python.parent.mkdir()
            python.write_bytes(b'new-runtime')
        else:
            assert args[0] == str(python)
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr(subprocess, 'run', program)
    assert install(target, target) == python
    assert partial.read_bytes() == b'keep-during-resume'


def test_interrupted_virtualenv_bootstraps_missing_pip_without_replacing_python(tmp_path, monkeypatch):
    from maintenance.windows_install import install

    target = tmp_path / 'interrupted virtualenv'
    python = target / '.venv' / 'Scripts' / 'python.exe'
    python.parent.mkdir(parents=True)
    python.write_bytes(b'preserved-python-from-interrupted-venv')
    pip_ready = False

    def program(args, **kwargs):
        nonlocal pip_ready
        assert args[0] == str(python)
        if args[1:3] == ['-m', 'ensurepip']:
            pip_ready = True
        if args[1:4] == ['-m', 'pip', 'install'] and not pip_ready:
            raise subprocess.CalledProcessError(1, args, stderr='No module named pip')
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr(subprocess, 'run', program)
    assert install(target, target) == python
    assert python.read_bytes() == b'preserved-python-from-interrupted-venv'
