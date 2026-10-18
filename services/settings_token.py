"""Verified replacement credentials reuse the same encrypted queue and sync protocol."""
import json
import re
import sqlite3

from flask import current_app

from integration.client import EofficeClient
from integration.clock import TrustedClock
from integration.sync import SyncWorker
from integration.store import StoreIntegrityError
from integration.protocol import UUID
from kiosk.control import CaptureBusy, capture_fence, capture_paused, reload_capture_settings
from kiosk.state import KioskState, public_view
from services import setup_organization as organization
from services.biometric_service import BiometricKeyError
from services.setup_service import load_settings, record_progress, save_settings


class TokenRepairError(ValueError):
    def __init__(self, code, message, status=409):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


def token_status():
    config = current_app.config
    saved = load_settings()
    live = public_view(KioskState(config['KIOSK_STATE_FILE']).read(),
                       test_mode=config.get('EOFFICE_TEST_MODE', False))
    server_scope = saved.get('EOFFICE_SERVER_SCOPE')
    return {'device_id': config.get('EOFFICE_DEVICE_ID'),
            'capture_held': capture_paused(current_app),
            'recovery_pending': bool(saved.get('CREDENTIAL_REPAIR_PENDING')),
            'scope_change_pending': bool(saved.get('SCOPE_CHANGE')),
            'scope_mismatch': server_scope is not None and server_scope != organization._selected_scope(),
            'access': live.get('integration', {}).get('auth', 'unavailable')}


def _bind_legacy_identity(client, store, pending):
    if load_settings().get('SETUP_MANAGED') or current_app.config.get('EOFFICE_SCOPE_TYPE') is not None:
        raise TokenRepairError('identity_unavailable', 'Identitas perangkat lama belum tersedia. Pulihkan konfigurasi asli.')
    references = set()
    snapshot = store.load_snapshot()
    if snapshot is not None:
        references.add(snapshot['snapshot_id'].lower())
    for entry in pending:
        reference = json.loads(entry.body).get('snapshot_id')
        if type(reference) is not str or UUID.fullmatch(reference) is None:
            raise TokenRepairError('identity_evidence_unavailable',
                'Referensi snapshot antrean lama tidak lengkap. Pulihkan antrean dan riwayat server asli; data tidak diubah.')
        references.add(reference.lower())
    if not references:
        raise TokenRepairError('identity_evidence_unavailable',
            'Bukti snapshot perangkat lama tidak tersedia. Pulihkan data dan riwayat server asli; data tidak diubah.')
    info = client.organizations()
    ordered = sorted(references)
    for offset in range(0, len(ordered), 256):
        proof = client.verify_snapshot_ownership(ordered[offset:offset + 256])
        if proof['device'] != info['device']:
            raise TokenRepairError('identity_evidence_changed',
                'Identitas atau cakupan server berubah selama pemeriksaan. Capture ditahan; coba lagi.')
    # Pre-wizard installations always served the whole fixed SKPD; server proof binds its missing UUID.
    save_settings({'EOFFICE_DEVICE_ID': info['device']['device_id'], 'EOFFICE_SKPD_ID': info['device']['skpd_id'],
        'EOFFICE_SCOPE_TYPE': 'all_skpd', 'EOFFICE_SUBUNIT_ID': None,
        'EOFFICE_ORGANIZATION_NAME': info['organizations'][0]['name'], 'EOFFICE_SUBUNIT_NAME': None})
    record_progress('organization')
    return info


def repair_token(token):
    if current_app.config.get('EOFFICE_TEST_MODE'):
        raise TokenRepairError('test_mode', 'Token uji tetap memakai jalur uji yang terpisah.', 403)
    if type(token) is not str or re.fullmatch(r'[0-9a-f]{64}', token.strip()) is None:
        raise TokenRepairError('invalid_token', 'Masukkan token perangkat yang disiapkan admin server.', 400)
    token = token.strip()
    try:
        with capture_fence(current_app._get_current_object(), reason='credentials') as fence:
            reload_capture_settings(current_app)
            if not organization._store_path().is_file():
                raise TokenRepairError('storage_unavailable', 'Penyimpanan lama tidak tersedia. Pulihkan data dan kunci asli.', 503)
            try:
                store = organization._store(initialize=False)
            except (OSError, sqlite3.Error, StoreIntegrityError, BiometricKeyError) as exc:
                raise TokenRepairError('storage_unavailable', 'Penyimpanan lama tidak valid. Pulihkan data dan kunci asli.', 503) from exc
            try:
                pending = store.pending()
                info = None
                if not current_app.config.get('EOFFICE_DEVICE_ID'):
                    info = _bind_legacy_identity(EofficeClient(current_app.config['EOFFICE_BASE_URL'], token), store, pending)
                scope = EofficeClient.validate_scope(organization._selected_scope())
                client = EofficeClient(current_app.config['EOFFICE_BASE_URL'], token, scope=scope)
                info = info if info is not None else client.organizations()
                if info['device']['device_id'] != current_app.config['EOFFICE_DEVICE_ID']:
                    raise TokenRepairError('device_mismatch', 'Token berasal dari perangkat berbeda. Minta token pengganti perangkat yang sama.', 403)
                server_scope = {key: info['device'][key] for key in ('skpd_id', 'scope_type', 'subunit_id')}
                save_settings({'EOFFICE_DEVICE_TOKEN': token, 'EOFFICE_ORGANIZATIONS': info['organizations'],
                               'EOFFICE_SERVER_SCOPE': server_scope, 'CREDENTIAL_REPAIR_PENDING': True})
                if server_scope != scope:
                    raise TokenRepairError('scope_mismatch', 'Cakupan server berbeda. Pulihkan cakupan lama pada server atau selesaikan perubahan cakupan sebelum melanjutkan.')
                worker = SyncWorker(store, client, TrustedClock(store))
                status = worker.run_once(require_fresh_snapshot=True)
                if status.auth != 'ok' or status.clock != 'ok' or status.storage != 'ok':
                    raise TokenRepairError('refresh_unverified', 'Akses, snapshot, atau waktu server belum dapat diverifikasi. Capture ditahan; coba lagi.')
                save_settings({'EOFFICE_SNAPSHOT_CONTEXT': organization._snapshot_context(),
                               'CREDENTIAL_REPAIR_PENDING': False})
                record_progress('snapshot')
                if not load_settings().get('SCOPE_CHANGE') and (not fence.was_paused or fence.reason == 'credentials'):
                    fence.resume()
            finally:
                store.close()
    except CaptureBusy as exc:
        raise TokenRepairError('camera_draining', str(exc)) from exc
    finally:
        if not capture_paused(current_app):
            from services.setup_camera import activate_browser
            activate_browser()


def retry_token():
    saved = load_settings()
    if not saved.get('CREDENTIAL_REPAIR_PENDING'):
        raise TokenRepairError('no_repair_pending', 'Masukkan token pengganti dari admin server.', 400)
    repair_token(saved.get('EOFFICE_DEVICE_TOKEN'))
