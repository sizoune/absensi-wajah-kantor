"""Detached updater: no database restore after normal services have been activated."""
from contextlib import ExitStack, contextmanager
from pathlib import Path
import os
import subprocess
import time

from app_version import VERSION
from maintenance import platforms
from kiosk.lock import AlreadyRunning, SingleInstanceLock
from maintenance.distribution import (active_version, atomic_json, backup_instance, executable,
                                      read_json, restore_instance)
from services.update_package import UpdateError, verify_manifest


def journal(root, stage, **values):
    path = Path(root) / 'updates/journal.json'
    atomic_json(path, {**read_json(path, {}), **values, 'stage': stage})


@contextmanager
def wait_lock(path, seconds=60):
    deadline = time.monotonic() + seconds
    while True:
        lock = SingleInstanceLock(path)
        try:
            lock.acquire()
            break
        except AlreadyRunning:
            if time.monotonic() >= deadline:
                raise UpdateError('Layanan belum berhenti. Tidak ada proses yang dipaksa berhenti.')
            time.sleep(.1)
    try:
        yield lock
    finally:
        lock.release()


def start_version(root, version, transaction=None):
    command = [str(executable(root, version)), '--root', str(root), 'launch']
    if transaction:
        command += ['--transaction', transaction]
    if os.name != 'nt':
        from maintenance.posix_install import launch_terminal
        return launch_terminal(command, root)
    return subprocess.Popen(command, cwd=root, env={**os.environ, 'PYINSTALLER_RESET_ENVIRONMENT': '1'},
                            creationflags=getattr(subprocess, 'CREATE_NEW_PROCESS_GROUP', 0))


def activate(root, transaction):
    state = read_json(Path(root) / 'updates/journal.json', {})
    if (state.get('transaction') != transaction or state.get('stage') != 'restarting'
            or state.get('target') != VERSION or active_version(root) != VERSION):
        raise UpdateError('Aktivasi kandidat tidak diizinkan.')
    # This durable point precedes every camera/sync writer; subsequent failures must retain new data.
    journal(root, 'completed', activated=True, recovery_required=False)


def apply_update(root, transaction, public_key):
    root = Path(root).resolve()
    if not transaction.isalnum() or len(transaction) != 32:
        raise UpdateError('Identitas transaksi tidak valid.')
    shutdown_deadline = time.monotonic() + 60
    with wait_lock(root / 'updates/update.lock'):
        state = read_json(root / 'updates/journal.json', {})
        if state.get('transaction') != transaction or state.get('stage') != 'stopping':
            raise UpdateError('Transaksi pembaruan tidak cocok.')
        work = root / 'updates' / transaction
        previous = active_version(root)
        manifest = verify_manifest((work / 'update.json').read_bytes(), (work / 'update.sig').read_bytes(),
                                   public_key, previous, expected_platform=platforms.platform_id())
        if manifest['version'] != VERSION or state.get('previous') != previous or state.get('target') != VERSION:
            raise UpdateError('Versi executable tidak cocok dengan transaksi.')
        backup = None
        activated = False
        try:
            with ExitStack() as locks:
                for path in ('launcher.lock', 'kiosk.lock', 'kiosk.lock.settings', 'setup-models.lock'):
                    locks.enter_context(wait_lock(root / 'instance' / path,
                        seconds=max(0, shutdown_deadline - time.monotonic())))
                journal(root, 'backing_up')
                backup = backup_instance(root, transaction)
                journal(root, 'applying', backup=backup.name)
                subprocess.run([str(executable(root, VERSION)), '--root', str(root), 'self-check', '--existing'],
                               cwd=root, check=True, timeout=60,
                               env={**os.environ, 'PYINSTALLER_RESET_ENVIRONMENT': '1'})
                atomic_json(root / 'current.json', {'version': VERSION})
                journal(root, 'restarting', activated=False)
            child = start_version(root, VERSION, transaction)
            deadline = time.monotonic() + 45
            while time.monotonic() < deadline:
                state = read_json(root / 'updates/journal.json', {})
                if state.get('activated'):
                    activated = True
                    return
                if child.poll() is not None and (os.name == 'nt' or child.returncode != 0):
                    break
                time.sleep(.1)
            if child.poll() is None:
                raise UpdateError('Kandidat belum memberi hasil startup. Pemulihan lokal diperlukan.')
            raise UpdateError('Kandidat gagal dimulai.')
        except Exception:
            state = read_json(root / 'updates/journal.json', {})
            if activated or state.get('activated'):
                journal(root, 'failed', recovery_required=True)
                raise
            try:
                with ExitStack() as locks:
                    locks.enter_context(SingleInstanceLock(root / 'instance/launcher.lock'))
                    locks.enter_context(SingleInstanceLock(root / 'instance/kiosk.lock'))
                    if backup is not None:
                        restore_instance(root, backup)
                    atomic_json(root / 'current.json', {'version': previous})
                    journal(root, 'failed', recovery_required=False, rolled_back=True)
                start_version(root, previous)
            except Exception:
                journal(root, 'failed', recovery_required=True)
            raise


def recover(root):
    """Explicit local recovery is only permitted before the activation commit point."""
    root = Path(root).resolve()
    with SingleInstanceLock(root / 'updates/update.lock'), ExitStack() as locks:
        locks.enter_context(SingleInstanceLock(root / 'instance/launcher.lock'))
        locks.enter_context(SingleInstanceLock(root / 'instance/kiosk.lock'))
        state = read_json(root / 'updates/journal.json', {})
        if state.get('activated'):
            if not state.get('recovery_required'):
                raise UpdateError('Tidak ada transaksi yang memerlukan pemulihan.')
            subprocess.run([str(executable(root, active_version(root))), '--root', str(root),
                            'self-check', '--existing'], cwd=root, check=True, timeout=60,
                           env={**os.environ, 'PYINSTALLER_RESET_ENVIRONMENT': '1'})
            journal(root, 'completed', recovery_required=False, recovered=True)
            return
        if not state.get('recovery_required'):
            raise UpdateError('Tidak ada transaksi yang memerlukan pemulihan.')
        previous = state.get('previous')
        if not executable(root, previous).is_file():
            raise UpdateError('Versi sebelumnya tidak tersedia.')
        backup_name = state.get('backup')
        if backup_name:
            if backup_name != 'update-' + state.get('transaction', ''):
                raise UpdateError('Cadangan transaksi tidak cocok.')
            restore_instance(root, root / 'backups' / backup_name)
        elif state.get('stage') not in ('stopping', 'backing_up', 'failed'):
            raise UpdateError('Cadangan belum dapat dipastikan. Pemulihan manual diperlukan.')
        atomic_json(root / 'current.json', {'version': previous})
        journal(root, 'failed', recovery_required=False, rolled_back=True)
