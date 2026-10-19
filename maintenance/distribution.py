"""Persistent install layout, durable journal and offline SQLite snapshots."""
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import tempfile

from services.update_package import UpdateError, version_tuple

TRANSACTION_STAGES = {'stopping', 'backing_up', 'applying', 'restarting'}


def replace_file(source, destination):
    if os.name == 'nt':
        import ctypes
        from ctypes import wintypes
        move = ctypes.WinDLL('kernel32', use_last_error=True).MoveFileExW
        move.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD]
        move.restype = wintypes.BOOL
        # Persist the rename before an activation journal permits new database writers.
        if not move(str(source), str(destination), 0x1 | 0x8):
            raise ctypes.WinError(ctypes.get_last_error())
    else:
        os.replace(source, destination)


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=path.name, suffix='.tmp', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as output:
            json.dump(value, output, ensure_ascii=False, allow_nan=False)
            output.flush()
            os.fsync(output.fileno())
        replace_file(temporary, path)
        if os.name != 'nt':
            directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    finally:
        Path(temporary).unlink(missing_ok=True)


def read_json(path, default=None):
    try:
        with Path(path).open('rb') as source:
            raw = source.read(128 * 1024 + 1)
        if len(raw) > 128 * 1024:
            raise ValueError
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise ValueError
        return value
    except FileNotFoundError:
        return default
    except (ValueError, UnicodeError) as exc:
        raise UpdateError('Metadata lokal rusak. Gunakan pemulihan lokal.') from exc


def startup_allowed(root):
    journal = read_json(Path(root) / 'updates/journal.json', {})
    if journal.get('stage') in TRANSACTION_STAGES or journal.get('recovery_required'):
        raise UpdateError('Pembaruan terputus. Jalankan pemulihan lokal sebelum membuka kios.')


def executable(root, version):
    version_tuple(version)
    return Path(root) / 'versions' / version / 'Absensi.exe'


def active_version(root):
    current = read_json(Path(root) / 'current.json')
    if not current or set(current) != {'version'}:
        raise UpdateError('Versi aktif belum tersedia.')
    version_tuple(current['version'])
    return current['version']


def initialize(root, version):
    root = Path(root)
    version_tuple(version)
    if any((root / name).exists() for name in ('.env', '.venv', '.runtime', 'model_files')):
        raise UpdateError('Instalasi ZIP lama ditemukan. Gunakan folder baru tanpa memindahkan data lama.')
    if (root / 'current.json').exists():
        raise UpdateError('Aplikasi sudah terpasang. Gunakan pembaruan melalui admin lokal.')
    if (root / 'instance').exists() and any((root / 'instance').iterdir()):
        raise UpdateError('Data lama ditemukan. Pemasangan baru tidak menimpa data.')
    for name in ('instance', 'updates', 'backups', 'versions'):
        (root / name).mkdir(parents=True, exist_ok=True, mode=0o700)
    if not executable(root, version).is_file():
        raise UpdateError('Executable rilis belum tersedia.')
    atomic_json(root / 'current.json', {'version': version})


def instance_files(instance):
    for path in Path(instance).rglob('*'):
        relative = path.relative_to(instance)
        if path.is_symlink():
            raise UpdateError('Penyimpanan perangkat tidak boleh berupa tautan.')
        if ('models' in relative.parts or path.name.endswith(('.lock', '.settings', '.pause', '.stop', '-wal', '-shm', '-journal'))):
            continue
        if path.is_file():
            yield relative


def digest(path):
    with Path(path).open('rb') as source:
        return hashlib.file_digest(source, 'sha256').hexdigest()


def backup_instance(root, transaction):
    root = Path(root)
    if not transaction.isalnum():
        raise UpdateError('Identitas transaksi tidak valid.')
    destination = root / 'backups' / ('update-' + transaction)
    destination.mkdir(parents=True, mode=0o700)
    manifest = {}
    for relative in instance_files(root / 'instance'):
        source, target = root / 'instance' / relative, destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if source.suffix == '.sqlite3':
            with closing(sqlite3.connect(source.resolve().as_uri() + '?mode=ro', uri=True)) as conn:
                with closing(sqlite3.connect(target)) as backup:
                    conn.backup(backup)
                    if backup.execute('PRAGMA integrity_check').fetchone() != ('ok',):
                        raise UpdateError('Cadangan database tidak valid.')
        else:
            shutil.copyfile(source, target)
        with target.open('rb+') as saved:
            os.fsync(saved.fileno())
        manifest[relative.as_posix()] = digest(target)
    atomic_json(destination / 'backup.json', manifest)
    verify_backup(destination)
    return destination


def verify_backup(backup):
    manifest = read_json(Path(backup) / 'backup.json')
    if manifest is None:
        raise UpdateError('Manifest cadangan tidak tersedia.')
    for name, expected in manifest.items():
        relative = Path(name)
        if relative.is_absolute() or '..' in relative.parts or '\\' in name:
            raise UpdateError('Path cadangan tidak valid.')
        if digest(Path(backup) / relative) != expected:
            raise UpdateError('Cadangan rusak. Data sekarang tidak diubah.')
    return manifest


def restore_instance(root, backup):
    root, backup = Path(root), Path(backup)
    manifest = verify_backup(backup)
    instance = root / 'instance'
    for name in manifest:
        target = instance / name
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(target.name + '.restore')
        shutil.copyfile(backup / name, temporary)
        with temporary.open('rb+') as saved:
            os.fsync(saved.fileno())
        replace_file(temporary, target)
    for relative in list(instance_files(instance)):
        if relative.as_posix() not in manifest:
            (instance / relative).unlink()
    # A stopped writer may have left SQLite sidecars; they must not replay into a restored database.
    for suffix in ('*-wal', '*-shm', '*-journal'):
        for path in instance.rglob(suffix):
            path.unlink()
