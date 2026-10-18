"""Completion stores configuration; runtime readiness still requires live checks."""
import sqlite3
import time

from flask import current_app

from extensions import db
from integration.client import EofficeClient
from integration.store import StoreIntegrityError
from kiosk.state import KioskState, public_view
from models import User
from recognition import model_files as mf
from services.biometric_service import BiometricKeyError
from services.setup_models import _directory, _models, model_setup_status
from services.setup_organization import _store, organization_status
from services.setup_service import load_settings, setup_status


def camera_context():
    return {key: current_app.config.get(key) for key in (
        'CAMERA_SOURCE', 'CAMERA_INDEX', 'CAMERA_NAME', 'CAMERA_BACKEND',
        'CAMERA_WIDTH', 'CAMERA_HEIGHT', 'CAMERA_FPS', 'CAMERA_FORMAT_STRATEGY')}


def _model_status(snapshot, progress):
    files = []
    for model in _models():
        try:
            stat = mf.model_path(_directory(), model).stat()
            metadata = stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns
        except OSError:
            metadata = None
        files.append((model, metadata))
    signature = files, snapshot.get('models'), progress
    cached = current_app.extensions.get('readiness_models')
    now = time.monotonic()
    if cached is not None and cached[0] == signature and 0 <= now - cached[1] < 30:
        return cached[2]
    status = model_setup_status(snapshot)
    current_app.extensions['readiness_models'] = signature, now, status
    return status


def readiness_status():
    status = setup_status()
    organization = organization_status()
    snapshot, storage_error = None, organization['snapshot']['storage_error']
    if organization['snapshot']['available']:
        try:
            store = _store()
            try:
                snapshot = store.load_snapshot()
                store.pending()  # Authenticate retained queue bytes before claiming storage readiness.
            finally:
                store.close()
        except (BiometricKeyError, StoreIntegrityError, OSError, ValueError, sqlite3.Error):
            storage_error = True
    saved = load_settings()
    models = _model_status(snapshot if snapshot is not None else {}, saved.get('SETUP_MODEL_RESULTS'))
    config = current_app.config
    try:
        EofficeClient(config['EOFFICE_BASE_URL'], config['EOFFICE_DEVICE_TOKEN'],
                      test_mode=config.get('EOFFICE_TEST_MODE', False))
        configured = (config['ATTENDANCE_MODE'] == 'eoffice'
                      and status['steps']['organization'] and organization['device_id'] is not None
                      and organization['scope_type'] in ('all_skpd', 'parent', 'subunit'))
    except (KeyError, ValueError, TypeError):
        configured = False
    checks = {
        'configuration': bool(configured),
        'admin': db.session.execute(db.select(User.id).where(
            User.role == 'admin', User.is_active.is_(True))).first() is not None,
        'storage': status['steps']['storage'] and not storage_error,
        'snapshot': snapshot is not None and not storage_error,
        'models': models['ready'] and snapshot is not None,
        'camera': saved.get('SETUP_CAMERA_CHECK') == camera_context(),
        'enrollment': snapshot is not None and bool(snapshot['employees']),
        'liveness': config.get('LIVENESS_MODE') == 'passive',
    }
    completion = all(checks[key] for key in ('configuration', 'admin', 'storage', 'snapshot'))
    state = KioskState(config['KIOSK_STATE_FILE']).read()
    live = public_view(state,
                       test_mode=config.get('EOFFICE_TEST_MODE', False))
    health = live.get('integration', {}) if live.get('online') else {}
    runtime = {
        'access': health.get('auth') == 'ok',
        'clock': health.get('clock') == 'ok',
        'runtime_storage': health.get('storage') == 'ok',
        'capture': live.get('online', False) and live.get('camera') == 'ok'
                   and state.get('liveness') == 'passive',
    }
    if config.get('CAMERA_SOURCE') == 'browser':
        browser = current_app.extensions.get('browser_camera')
        if browser is None or browser.mode == 'preview' or not browser.capturing:
            runtime['capture'] = False
    blocked = [key for key, passed in {**checks, **runtime}.items() if not passed]
    from kiosk.control import capture_paused
    if capture_paused(current_app):
        blocked.append('capture_hold')
    status['steps'].update(models=checks['models'], snapshot=checks['snapshot'], camera=checks['camera'])
    status['pending'] = [key for key, passed in status['steps'].items() if not passed]
    return {**status, 'can_finish': completion, 'ready': status['complete'] and not blocked,
            'state': 'setup_required' if not status['complete'] else 'ready' if not blocked else 'not_ready',
            'blocked': blocked, 'checks': checks, 'models': models,
            'snapshot': organization['snapshot'],
            'purpose': 'camera_test' if config.get('EOFFICE_TEST_MODE') else 'attendance'}


def require_capture_configuration(*, enrollment=True):
    """Wizard installations must pass current checks before any camera opens."""
    from kiosk.control import capture_paused
    if capture_paused(current_app):
        raise ValueError('Capture ditahan selama pemulihan pengaturan. Buka pengaturan admin lalu coba lagi.')
    if not setup_managed():
        return
    status = readiness_status()
    needed = ('configuration', 'admin', 'storage', 'snapshot', 'models', 'camera', 'liveness')
    if not status['complete'] or not all(status['checks'][key] for key in needed):
        raise ValueError('Perangkat belum siap. Periksa kesiapan model, kamera, dan konfigurasi melalui admin lokal.')
    if enrollment and not status['checks']['enrollment']:
        raise ValueError('Enrollment belum tersedia. Capture ditahan sampai sinkronisasi berhasil.')


def setup_managed():
    return current_app.config.get('SETUP_ENABLED') and load_settings().get('SETUP_MANAGED', False)
