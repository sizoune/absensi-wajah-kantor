"""Local administrator update jobs; release downloads never use eOffice credentials."""
from contextlib import ExitStack
from pathlib import Path
import os
import shutil
import subprocess
from threading import Event, Lock, RLock, Thread
import time
import uuid

from app_version import VERSION
from maintenance import platforms
from kiosk.control import capture_paused
from kiosk.lock import AlreadyRunning, SingleInstanceLock
from maintenance.distribution import active_version, atomic_json, executable, read_json, TRANSACTION_STAGES
from services.update_package import (UpdateError, RELEASE_BASE, download_package,
    extract_package, fetch_metadata, verify_manifest)

MESSAGES = {
    'idle': 'Aplikasi sudah menggunakan versi terbaru.',
    'checking': 'Memeriksa versi terbaru.', 'available': 'Pembaruan tersedia.',
    'downloading': 'Mengunduh paket pembaruan.', 'verifying': 'Memeriksa paket pembaruan.',
    'stopping': 'Menunggu layanan absensi berhenti dengan bersih.',
    'backing_up': 'Membuat cadangan data perangkat.', 'applying': 'Memeriksa aplikasi baru.',
    'restarting': 'Membuka kembali aplikasi.', 'completed': 'Pembaruan berhasil dipasang.',
    'failed': 'Pembaruan belum selesai. Periksa koneksi, ruang disk, dan pemulihan lokal.',
}


class UpdateManager:
    def __init__(self, app, root, public_key, stop_event):
        self.app, self.root = app, Path(root)
        self.public_key, self.stop_event = public_key, stop_event
        self.target = platforms.platform_id()
        self.closed = Event()
        self._busy = Lock()

    def status(self):
        journal = read_json(self.root / 'updates/journal.json', {'stage': 'idle'})
        stage = journal.get('stage', 'failed')
        return {'version': VERSION, 'stage': stage,
                'message': journal.get('failure_message', MESSAGES['failed']) if stage == 'failed' else MESSAGES.get(stage, MESSAGES['failed']),
                'offered_version': journal.get('target'), 'notes': journal.get('notes', ''),
                'received': journal.get('received', 0), 'size': journal.get('size', 0),
                'recovery_required': journal.get('recovery_required', False),
                'busy': self._busy.locked() or stage in TRANSACTION_STAGES}

    def stage(self, stage, **values):
        journal = read_json(self.root / 'updates/journal.json', {})
        atomic_json(self.root / 'updates/journal.json', {**journal, **values, 'stage': stage})

    def start(self, action):
        if action not in ('check', 'install') or not self._busy.acquire(blocking=False):
            raise UpdateError('Pekerjaan pembaruan masih berjalan.')
        try:
            status = self.status()
            if status['stage'] in TRANSACTION_STAGES or status['recovery_required']:
                raise UpdateError('Pembaruan memerlukan pemulihan lokal.')
            thread = Thread(target=self._job, args=(action,), name='app-update', daemon=False)
            thread.start()
        except BaseException:
            self._busy.release()
            raise

    def _job(self, action):
        handed_off = False
        try:
            with SingleInstanceLock(self.root / 'updates/update.lock'):
                try:
                    if action == 'check':
                        self._check()
                    else:
                        self._install()
                        handed_off = True
                except Exception as exc:
                    # A download URL, platform exception or credential must never reach the browser/log.
                    recovery = self.status()['stage'] in TRANSACTION_STAGES
                    self.stage('failed', recovery_required=recovery,
                               failure_message=str(exc) if isinstance(exc, UpdateError) else MESSAGES['failed'])
        except AlreadyRunning:
            pass  # Another process owns the journal; do not overwrite its transaction.
        finally:
            self._busy.release()
            if handed_off:
                self.stop_event.set()

    def _check(self):
        self.stage('checking', recovery_required=False, target=None, notes='', received=0, size=0,
                   activated=False, backup=None, transaction=None, previous=None, rolled_back=False)
        atomic_json(self.root / 'updates/check.json', {'checked_at': time.time()})
        name = platforms.metadata_name(self.target)
        raw = fetch_metadata(RELEASE_BASE + name)
        signature = fetch_metadata(RELEASE_BASE + name + '.sig', 64)
        manifest = verify_manifest(raw, signature, self.public_key, active_version(self.root), allow_equal=True, expected_platform=self.target)
        if manifest['version'] == active_version(self.root):
            self.stage('idle')
            return
        offer = self.root / 'updates'
        (offer / 'offer.json').write_bytes(raw)
        (offer / 'offer.sig').write_bytes(signature)
        self.stage('available', target=manifest['version'], notes=manifest['notes'], size=manifest['size'])

    def _install(self):
        offer = self.root / 'updates'
        raw, signature = (offer / 'offer.json').read_bytes(), (offer / 'offer.sig').read_bytes()
        previous = active_version(self.root)
        manifest = verify_manifest(raw, signature, self.public_key, previous, expected_platform=self.target)
        with ExitStack() as stack:
            stack.enter_context(SingleInstanceLock(str(self.app.config['KIOSK_LOCK_FILE']) + '.settings'))
            stack.enter_context(SingleInstanceLock(Path(self.app.instance_path) / 'setup-models.lock'))
            camera_lock = self.app.extensions.setdefault('setup_camera_lock', RLock())
            if not camera_lock.acquire(blocking=False):
                raise UpdateError('Pemeriksaan kamera masih berjalan.')
            stack.callback(camera_lock.release)
            if capture_paused(self.app) or self.app.config.get('SCOPE_CHANGE') or self.app.config.get('CREDENTIAL_REPAIR_PENDING'):
                raise UpdateError('Selesaikan pemulihan pengaturan terlebih dahulu.')
            required = manifest['size'] * 4 + sum(p.stat().st_size for p in Path(self.app.instance_path).rglob('*') if p.is_file())
            if shutil.disk_usage(self.root).free < required:
                raise UpdateError('Ruang penyimpanan belum cukup.')
            transaction = uuid.uuid4().hex
            work = offer / transaction
            work.mkdir()
            (work / 'update.json').write_bytes(raw)
            (work / 'update.sig').write_bytes(signature)
            self.stage('downloading', target=manifest['version'], previous=previous,
                       transaction=transaction, received=0, size=manifest['size'], recovery_required=False,
                       activated=False, backup=None, rolled_back=False)
            archive = work / platforms.package_name(self.target)
            download_package(manifest, archive, lambda received: self.stage('downloading', received=received))
            self.stage('verifying')
            candidate = self.root / 'versions' / manifest['version']
            candidate_executable = executable(self.root, manifest['version'])
            if candidate.exists():
                # Only inactive candidates can be discarded; the previous active version is retained.
                if candidate.name == previous:
                    raise UpdateError('Kandidat adalah versi aktif.')
                shutil.rmtree(candidate)
            extract_package(archive, candidate, manifest)
            if not candidate_executable.is_file() or (os.name != 'nt' and not os.access(candidate_executable, os.X_OK)):
                raise UpdateError('Executable kandidat tidak tersedia.')
            self.stage('stopping', recovery_required=True)
            try:
                subprocess.Popen([str(candidate_executable), '--root', str(self.root),
                                  'apply-update', '--transaction', transaction],
                                 cwd=self.root, env={**os.environ, 'PYINSTALLER_RESET_ENVIRONMENT': '1'},
                                 creationflags=getattr(subprocess, 'CREATE_NEW_PROCESS_GROUP', 0),
                                 start_new_session=os.name != 'nt',
                                 stdin=subprocess.DEVNULL if os.name != 'nt' else None,
                                 stdout=subprocess.DEVNULL if os.name != 'nt' else None,
                                 stderr=subprocess.DEVNULL if os.name != 'nt' else None)
            except BaseException:
                self.stage('failed', recovery_required=False)
                raise

    def schedule(self):
        def check_periodically():
            while not self.closed.is_set():
                try:
                    last = read_json(self.root / 'updates/check.json', {}).get('checked_at', 0)
                    if time.time() - last >= 86400:
                        self.start('check')
                except (OSError, UpdateError):
                    pass
                self.closed.wait(60)
        Thread(target=check_periodically, name='update-schedule', daemon=True).start()


def update_guard():
    from flask import current_app, request
    manager = current_app.extensions.get('app_updates')
    if manager is None:
        return
    if request.endpoint in ('setup.updates_page', 'setup.updates_status', 'health', 'static'):
        return
    status = manager.status()
    if (status['stage'] in TRANSACTION_STAGES
            or manager._busy.locked() and request.method != 'GET' and request.blueprint == 'setup'
            and request.endpoint not in ('setup.updates_check', 'setup.updates_install')):
        return {'error': 'update_busy', 'message': 'Pembaruan aplikasi sedang berlangsung.'}, 503
