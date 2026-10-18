"""Admin scope changes reuse the existing organization and encrypted gallery flow."""
from flask import current_app

from kiosk.control import CaptureBusy, capture_fence, capture_paused, reload_capture_settings
from services import setup_organization as organization
from services.setup_service import load_settings, save_settings


class ScopeChangeError(ValueError):
    def __init__(self, code, message, status=409):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


def scope_status():
    return {**organization.organization_status(), 'capture_held': capture_paused(current_app),
            'pending_scope': load_settings().get('SCOPE_CHANGE')}


def refresh_permissions():
    info = organization._client().organizations()
    previous = current_app.config.get('EOFFICE_DEVICE_ID')
    if previous is not None and previous != info['device']['device_id']:
        raise ScopeChangeError('device_mismatch',
                               'Identitas perangkat tidak cocok. Pulihkan token perangkat yang sama.', 403)
    save_settings({'EOFFICE_ORGANIZATIONS': info['organizations'],
                   'EOFFICE_SERVER_SCOPE': {key: info['device'][key]
                                            for key in ('skpd_id', 'scope_type', 'subunit_id')}})
    return info


def change_scope(skpd_id, scope_type, subunit_id=None):
    scope, _, _ = organization.scope_selection(skpd_id, scope_type, subunit_id)
    try:
        with capture_fence(current_app._get_current_object(), reason='scope') as fence:
            reload_capture_settings(current_app)
            if not organization._store_path().is_file():
                raise ScopeChangeError('storage_unavailable',
                                       'Penyimpanan lama tidak tersedia. Pulihkan data dan kunci asli sebelum melanjutkan.', 503)
            store = organization._store()
            try:
                if store.pending():
                    if not fence.was_paused or (fence.reason == 'scope' and not load_settings().get('SCOPE_CHANGE')):
                        fence.resume()
                    raise ScopeChangeError('queue_pending',
                                           'Antrean lama belum seluruhnya terkirim. Tunggu sinkronisasi lalu coba lagi.')
            finally:
                store.close()
            info = refresh_permissions()
            try:
                organization.scope_selection(skpd_id, scope_type, subunit_id, organizations=info['organizations'])
            except ValueError as exc:
                unchanged = all(info['device'][key] == value for key, value in organization._selected_scope().items())
                if unchanged and not fence.was_paused:
                    fence.resume()
                raise ScopeChangeError('scope_forbidden',
                                       'Pilihan cakupan sudah tidak diizinkan server. Pilih dari daftar terbaru.', 403) from exc
            if current_app.config.get('EOFFICE_DEVICE_ID') is None:
                save_settings({'EOFFICE_DEVICE_ID': info['device']['device_id']})
            save_settings({'SCOPE_CHANGE': scope})
            organization.select_organization(skpd_id, scope_type, subunit_id)
            save_settings({'SCOPE_CHANGE': None})
            fence.resume()
    except CaptureBusy as exc:
        raise ScopeChangeError('camera_draining', str(exc)) from exc
    finally:
        if not capture_paused(current_app):
            from services.setup_camera import activate_browser
            activate_browser()
