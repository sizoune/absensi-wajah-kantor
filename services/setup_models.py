"""Pinned model preparation; failed downloads never become active files."""
from dataclasses import replace
from pathlib import Path
import sqlite3
import urllib.request

from flask import current_app

from integration.client import EofficeClient, RejectRedirects
from integration.store import IntegrationStore, StoreIntegrityError
from kiosk.lock import SingleInstanceLock
from recognition import model_files as mf
from services.biometric_service import BiometricKeyError, load_keyring
from services.setup_service import load_settings, record_progress, save_settings


LABELS = {
    'yunet': 'Deteksi wajah YuNet', 'minifas_v1se': 'Anti-foto MiniFASNet V1SE',
    'minifas_v2': 'Anti-foto MiniFASNet V2', 'auraface_512': 'Pengenalan wajah AuraFace',
    'mobilefacenet_v1_periocular': 'Pengenalan area mata MobileFaceNet',
}
REASONS = {
    'missing': 'Model belum disiapkan.',
    'downloading': 'Unduhan belum selesai. Model belum digunakan.',
    'unavailable': 'Sumber model belum tersedia atau koneksi terputus.',
    'hash_mismatch': 'Sidik jari model tidak cocok. File tidak digunakan.',
    'partial': 'Unduhan tidak lengkap. File tidak digunakan.',
    'test_distribution_unavailable': 'Distribusi model untuk token uji belum tersedia. Token uji tetap terbatas pada mode uji.',
}


def _models():
    return (mf.YUNET, mf.MINIFAS_V1SE, mf.MINIFAS_V2, mf.AURAFACE, mf.MOBILEFACENET)


def _directory():
    return Path(current_app.instance_path) / 'models'


def _reuse_existing(model):
    setting = {mf.AURAFACE.key: 'EOFFICE_AURAFACE_PATH',
               mf.MOBILEFACENET.key: 'EOFFICE_PERIOCULAR_PATH'}.get(model.key)
    candidate = (Path(current_app.config.get(setting) or '') if setting else
                 mf.model_path(current_app.config['MODEL_DIR'], model))
    try:
        if (candidate.is_file() and candidate.stat().st_size == model.size
                and mf.sha256_of(candidate) == model.sha256):
            mf.download(_directory(), replace(model, urls=(candidate.resolve().as_uri(),)))
            return True
    except (OSError, mf.ModelFileError):
        pass
    return False


def _saved_snapshot():
    test_mode = current_app.config.get('EOFFICE_TEST_MODE', False)
    path = Path(current_app.instance_path) / ('eoffice-test.sqlite3' if test_mode else 'eoffice.sqlite3')
    if not path.exists():
        return None
    active, keys = load_keyring(current_app.config['EOFFICE_KEY_FILE'])
    store = IntegrationStore(path, keys[active], test_mode=test_mode, initialize=False)
    try:
        return store.load_snapshot()
    finally:
        store.close()


def model_setup_status(snapshot=None):
    results = load_settings().get('SETUP_MODEL_RESULTS', {})
    models = []
    for model in _models():
        result = results.get(model.key, {})
        try:
            mf.verify(_directory(), model)
            state, reason, downloaded = 'verified', '', model.size
        except (mf.ModelFileError, OSError):
            exists = mf.model_path(_directory(), model).exists()
            reason = 'hash_mismatch' if exists else result.get('reason', 'missing')
            state = 'downloading' if reason == 'downloading' else 'missing' if reason == 'missing' else 'failed'
            downloaded = result.get('downloaded_bytes', 0)
        recovery = '' if state == 'verified' else 'Periksa koneksi, lalu pilih coba lagi.'
        if model.key == mf.MOBILEFACENET.key and reason in ('unavailable', 'test_distribution_unavailable'):
            recovery = 'Minta admin server menyiapkan artefak MobileFaceNet resmi, lalu coba lagi.'
        models.append({'key': model.key, 'label': LABELS[model.key], 'state': state,
                       'downloaded_bytes': downloaded, 'size': model.size,
                       'reason': REASONS.get(reason, ''), 'recovery': recovery})
    completed = sum(model['state'] == 'verified' for model in models)
    verified, compatible = completed == len(models), None
    compatibility_reason = 'Kesesuaian model diperiksa setelah snapshot enrollment tersedia.'
    try:
        snapshot = snapshot if snapshot is not None else _saved_snapshot()
        if snapshot is not None and verified:
            from recognition.eoffice_models import model_manifest, validate_model_compatibility
            manifest = model_manifest({model.key: mf.model_path(_directory(), model)
                                       for model in (mf.AURAFACE, mf.MOBILEFACENET)})
            validate_model_compatibility(manifest, snapshot['models'])
            compatible, compatibility_reason = True, 'Model cocok dengan metadata enrollment server.'
    except (ValueError, KeyError, TypeError, OSError, sqlite3.Error, mf.ModelFileError,
            StoreIntegrityError, BiometricKeyError):
        compatible = False
        compatibility_reason = 'Model atau snapshot tidak cocok dengan metadata enrollment. Hubungi admin server, lalu ambil ulang enrollment.'
    return {'verified': verified, 'compatible': compatible, 'ready': verified and compatible is True,
            'compatibility_reason': compatibility_reason, 'completed': completed, 'total': len(models),
            'models': models}


def prepare_models(model_key=None):
    models = _models()
    if model_key is not None:
        models = tuple(model for model in models if model.key == model_key)
        if not models:
            raise ValueError('Model tidak dikenal.')
    with SingleInstanceLock(Path(current_app.instance_path) / 'setup-models.lock'):
        for model in models:
            try:
                mf.verify(_directory(), model)
                continue
            except (mf.ModelFileError, OSError):
                pass
            if _reuse_existing(model):
                continue
            results = load_settings().get('SETUP_MODEL_RESULTS', {})
            results[model.key] = {'reason': 'downloading', 'downloaded_bytes': 0}
            save_settings({'SETUP_MODEL_RESULTS': results})
            try:
                authenticated = model.key == mf.MOBILEFACENET.key
                if authenticated:
                    base = current_app.config['EOFFICE_BASE_URL']
                    token = current_app.config['EOFFICE_DEVICE_TOKEN']
                    EofficeClient(base, token, test_mode=current_app.config.get('EOFFICE_TEST_MODE', False))
                    if current_app.config.get('EOFFICE_TEST_MODE', False):
                        raise mf.ModelDownloadError('Test distribution unavailable', 'test_distribution_unavailable')
                    model = replace(model, urls=(base.rstrip('/') + '/api/kiosk/models/' + model.key,))
                open_source = current_app.extensions.get('setup_model_opener')
                if open_source is None:
                    open_source = (urllib.request.build_opener(RejectRedirects()).open if authenticated
                                   else urllib.request.urlopen)

                def opener(url, timeout):
                    headers = {'User-Agent': 'EofficeCamera/1.0', 'Accept-Encoding': 'identity'}
                    if authenticated:
                        headers['Authorization'] = 'Bearer ' + token
                    return open_source(urllib.request.Request(url, headers=headers), timeout=timeout)

                def progress(received):
                    results[model.key]['downloaded_bytes'] = received
                    save_settings({'SETUP_MODEL_RESULTS': results})

                mf.download(_directory(), model, opener=opener, progress=progress)
                results[model.key] = {'reason': '', 'downloaded_bytes': model.size}
            except (mf.ModelFileError, OSError, ValueError) as error:
                results[model.key] = {'reason': getattr(error, 'reason', 'unavailable'),
                                      'downloaded_bytes': getattr(error, 'downloaded_bytes', 0)}
            save_settings({'SETUP_MODEL_RESULTS': results})
        status = model_setup_status()
        if status['verified']:
            save_settings({'MODEL_DIR': str(_directory()),
                           'EOFFICE_AURAFACE_PATH': str(mf.model_path(_directory(), mf.AURAFACE)),
                           'EOFFICE_PERIOCULAR_PATH': str(mf.model_path(_directory(), mf.MOBILEFACENET))})
        record_progress('models', status['ready'])
        return status
