"""Server-authorized organization selection and the existing encrypted snapshot."""
from pathlib import Path
import sqlite3

from flask import current_app

from config import EOFFICE_PRODUCTION_URL
from integration.client import EofficeClient
from integration.store import IntegrationStore, StoreIntegrityError
from services.biometric_service import BiometricKeyError, load_keyring
from services.setup_service import record_progress, save_settings, setup_status


def _client():
    scope = _selected_scope() if current_app.config.get('EOFFICE_SCOPE_TYPE') is not None else None
    return EofficeClient(current_app.config['EOFFICE_BASE_URL'], current_app.config['EOFFICE_DEVICE_TOKEN'],
                         test_mode=current_app.config.get('EOFFICE_TEST_MODE', False), scope=scope)


def _selected_scope():
    return {key: current_app.config.get('EOFFICE_' + key.upper()) for key in
            ('skpd_id', 'scope_type', 'subunit_id')}


def _store_path():
    return Path(current_app.instance_path) / ('eoffice-test.sqlite3' if current_app.config.get('EOFFICE_TEST_MODE') else 'eoffice.sqlite3')


def _store(*, initialize=True):
    active, keys = load_keyring(current_app.config['EOFFICE_KEY_FILE'])
    return IntegrationStore(_store_path(), keys[active], test_mode=current_app.config.get('EOFFICE_TEST_MODE', False),
                            initialize=initialize)


def connect_device(token, base_url):
    if not setup_status()['steps']['admin']:
        raise ValueError('Admin lokal diperlukan.')
    base_url = base_url.strip() or EOFFICE_PRODUCTION_URL
    token = token.strip()
    client = EofficeClient(base_url, token,
                           test_mode=current_app.config.get('EOFFICE_TEST_MODE', False))
    info = client.organizations()
    config = current_app.config
    changed = (config.get('EOFFICE_DEVICE_ID') != info['device']['device_id']
               or config.get('EOFFICE_BASE_URL') != base_url
               or config.get('EOFFICE_DEVICE_TOKEN') != token)
    if changed and _store_path().exists():
        store = _store()
        try:
            same_identity = (config.get('EOFFICE_DEVICE_ID') == info['device']['device_id']
                             or not config.get('EOFFICE_DEVICE_ID') and config.get('EOFFICE_DEVICE_TOKEN') == token)
            if store.pending() and (not same_identity or config.get('EOFFICE_BASE_URL') != base_url):
                raise ValueError('Antrean lama masih terikat pada perangkat sebelumnya.')
        finally:
            store.close()
    values = {'EOFFICE_BASE_URL': base_url, 'EOFFICE_DEVICE_TOKEN': token,
              'EOFFICE_DEVICE_ID': info['device']['device_id'], 'EOFFICE_ORGANIZATIONS': info['organizations'],
              'EOFFICE_SERVER_SCOPE': {key: info['device'][key] for key in ('skpd_id', 'scope_type', 'subunit_id')}}
    if changed:
        values.update(EOFFICE_SKPD_ID=None, EOFFICE_SCOPE_TYPE=None, EOFFICE_SUBUNIT_ID=None,
                      EOFFICE_ORGANIZATION_NAME=None, EOFFICE_SUBUNIT_NAME=None)
    save_settings(values)
    if changed:
        record_progress('organization', False)
        record_progress('snapshot', False)


def scope_selection(skpd_id, scope_type, subunit_id=None, *, organizations=None):
    if not isinstance(skpd_id, str) or not skpd_id.isascii() or not skpd_id.isdecimal():
        raise ValueError('SKPD tidak valid.')
    if scope_type == 'subunit':
        if type(subunit_id) is not str or not subunit_id.isascii() or not subunit_id.isdecimal():
            raise ValueError('Pilih subunit tertentu.')
        subunit_id = int(subunit_id)
    elif subunit_id in (None, ''):
        subunit_id = None
    scope = EofficeClient.validate_scope({'skpd_id': int(skpd_id), 'scope_type': scope_type, 'subunit_id': subunit_id})
    allowed = current_app.config.get('EOFFICE_ORGANIZATIONS', []) if organizations is None else organizations
    organization = next((row for row in allowed
                         if row['skpd_id'] == scope['skpd_id']), None)
    if organization is None or scope_type not in organization['scope_types']:
        raise ValueError('Cakupan belum disetujui server.')
    subunit = next((row for row in organization.get('subunits', []) if row['subunit_id'] == subunit_id), None)
    if scope_type == 'subunit' and subunit is None:
        raise ValueError('Subunit belum disetujui server untuk SKPD ini.')
    return scope, organization, subunit


def select_organization(skpd_id, scope_type, subunit_id=None):
    scope, organization, subunit = scope_selection(skpd_id, scope_type, subunit_id)
    previous = _selected_scope() if current_app.config.get('EOFFICE_SCOPE_TYPE') else current_app.config.get('EOFFICE_SERVER_SCOPE')
    if previous != scope and _store_path().exists():
        store = _store()
        try:
            if store.pending():
                raise ValueError('Selesaikan antrean lama sebelum mengubah cakupan perangkat.')
        finally:
            store.close()
    identity = _client().select_scope(scope)
    if (identity['device_id'] != current_app.config.get('EOFFICE_DEVICE_ID')
            or any(identity[key] != value for key, value in scope.items())):
        raise ValueError('Cakupan perangkat belum disetujui server.')
    save_settings({'EOFFICE_SKPD_ID': identity['skpd_id'], 'EOFFICE_SCOPE_TYPE': identity['scope_type'],
                   'EOFFICE_SUBUNIT_ID': identity['subunit_id'], 'EOFFICE_ORGANIZATION_NAME': organization['name'],
                   'EOFFICE_SUBUNIT_NAME': subunit['name'] if subunit is not None else None,
                   'EOFFICE_SERVER_SCOPE': scope})
    if previous != scope:
        record_progress('snapshot', False)
    record_progress('organization')
    download_snapshot()


def download_snapshot():
    if not setup_status()['steps']['organization']:
        raise ValueError('Pilih cakupan perangkat lebih dahulu.')
    EofficeClient.validate_scope(_selected_scope())
    snapshot = _client().snapshot()
    store = _store()
    try:
        store.replace_snapshot(snapshot)
    finally:
        store.close()
    save_settings({'EOFFICE_SNAPSHOT_CONTEXT': _snapshot_context()})
    record_progress('snapshot')


def _snapshot_context():
    config = current_app.config
    return {key: config.get('EOFFICE_' + key.upper()) for key in
            ('base_url', 'device_id', 'skpd_id', 'scope_type', 'subunit_id')}


def organization_status():
    config = current_app.config
    snapshot = None
    storage_error = False
    current = (setup_status()['steps']['snapshot']
               and config.get('EOFFICE_SNAPSHOT_CONTEXT') == _snapshot_context())
    if current and _store_path().exists():
        try:
            store = _store(initialize=False)
            try:
                snapshot = store.load_snapshot()
            finally:
                store.close()
        except (BiometricKeyError, StoreIntegrityError, OSError, ValueError, sqlite3.Error):
            storage_error = True
    count = len(snapshot['employees']) if snapshot else 0
    return {'device_id': config.get('EOFFICE_DEVICE_ID'), 'skpd_id': config.get('EOFFICE_SKPD_ID'),
            'scope_type': config.get('EOFFICE_SCOPE_TYPE'), 'subunit_id': config.get('EOFFICE_SUBUNIT_ID'),
            'name': config.get('EOFFICE_ORGANIZATION_NAME'),
            'subunit_name': config.get('EOFFICE_SUBUNIT_NAME'),
            'organizations': config.get('EOFFICE_ORGANIZATIONS', []),
            'base_url': setup_status()['configuration']['base_url'] or EOFFICE_PRODUCTION_URL,
            'snapshot': {'available': snapshot is not None, 'enrollment_count': count,
                         'empty': snapshot is not None and count == 0, 'storage_error': storage_error}}
