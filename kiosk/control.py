"""A durable capture hold and the existing camera lock fence configuration changes."""
import os
from contextlib import contextmanager
from pathlib import Path
from threading import RLock, Thread
import time

from kiosk.lock import AlreadyRunning, SingleInstanceLock


class CaptureBusy(Exception):
    pass


def _pause_path(app):
    return Path(str(app.config['KIOSK_LOCK_FILE']) + '.pause')


def capture_paused(app):
    try:
        _pause_path(app).stat()
    except FileNotFoundError:
        return False
    except OSError:
        return True
    return True


@contextmanager
def native_capture_guard(app):
    def validate():
        if capture_paused(app):
            raise ValueError('Capture ditahan selama perubahan pengaturan.')
    validate()
    yield validate
    validate()


def reload_capture_settings(app):
    """A CLI factory may have loaded its scope immediately before an HTTP change."""
    if app.config.get('SETUP_ENABLED'):
        from extensions import db
        from services.setup_service import load_settings
        db.session.expire_all()
        saved = load_settings()
        context = ('EOFFICE_BASE_URL', 'EOFFICE_DEVICE_TOKEN', 'EOFFICE_DEVICE_ID',
                   'EOFFICE_SKPD_ID', 'EOFFICE_SCOPE_TYPE', 'EOFFICE_SUBUNIT_ID')
        if any(saved.get(key) != app.config.get(key) for key in context):
            app.config.update(saved)


def release_after_sync(sync, camera_lock):
    """A non-daemon drain retains native process/ownership while its old request finishes."""
    def drain():
        while not sync.stop(timeout=.25):
            pass
        camera_lock.release()
    thread = Thread(target=drain, name='native-camera-drain', daemon=False)
    try:
        thread.start()
    except RuntimeError:
        drain()


def _sync_directory(path):
    if os.name != 'nt':
        descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


class CaptureFence:
    def __init__(self, was_paused, reason):
        self.was_paused = was_paused
        self.reason = reason
        self.resuming = False

    def resume(self):
        """Caller has validated the current scope/snapshot or refused an unchanged request."""
        self.resuming = True


@contextmanager
def capture_fence(app, timeout=8, *, reason='settings'):
    """Hold across queue check, remote selection and snapshot; failed changes stay held."""
    if reason not in ('settings', 'scope', 'credentials', 'launcher'):
        raise ValueError('Alasan penahanan capture tidak valid.')
    settings = SingleInstanceLock(str(app.config['KIOSK_LOCK_FILE']) + '.settings')
    try:
        settings.acquire()
    except AlreadyRunning as exc:
        raise CaptureBusy('Perubahan pengaturan lain masih berjalan. Tunggu lalu coba lagi.') from exc
    camera = SingleInstanceLock(app.config['KIOSK_LOCK_FILE'])
    pause = _pause_path(app)
    fence = CaptureFence(capture_paused(app), reason)
    try:
        pause.parent.mkdir(parents=True, exist_ok=True)
        try:
            descriptor = os.open(pause, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            fence.was_paused = True
            try:
                with pause.open(encoding='ascii') as file:
                    saved = file.read(32).strip()
                fence.reason = saved if saved in ('settings', 'scope', 'credentials', 'launcher') else None
            except (OSError, UnicodeError):
                fence.reason = None
        else:
            with os.fdopen(descriptor, 'w', encoding='ascii') as file:
                file.write(reason + '\n')
                file.flush()
                os.fsync(file.fileno())
            _sync_directory(pause.parent)
        deadline = time.monotonic() + timeout
        with app.extensions.setdefault('setup_camera_lock', RLock()):
            runtime = app.extensions.get('browser_camera')
            if runtime is not None and not runtime.close():
                raise CaptureBusy('Kamera atau sinkronisasi lama masih berhenti. Capture ditahan; coba lagi.')
            while True:
                try:
                    camera.acquire()
                    break
                except AlreadyRunning as exc:
                    if time.monotonic() >= deadline:
                        raise CaptureBusy('Layanan kamera lama belum selesai berhenti. Capture ditahan; coba lagi.') from exc
                    time.sleep(.05)
            yield fence
    finally:
        try:
            if fence.resuming:
                pause.unlink(missing_ok=True)
                _sync_directory(pause.parent)
        finally:
            camera.release()
            settings.release()
