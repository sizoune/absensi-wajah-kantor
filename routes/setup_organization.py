"""Organization setup shares the existing setup grant and application CSRF."""
import sqlite3
from http.client import HTTPException

from flask import flash, redirect, render_template, request, url_for

from integration.client import RequestFailure
from integration.store import StoreIntegrityError
from routes.setup import bp, setup_access_required
from services import setup_organization as organization
from services.biometric_service import BiometricKeyError
from services.setup_service import setup_status

FAILURES = (OSError, ValueError, RequestFailure, HTTPException, sqlite3.Error, StoreIntegrityError, BiometricKeyError)


def _failed(error):
    status = 403 if isinstance(error, RequestFailure) and error.status_code in (401, 403) else 400
    flash('Konfigurasi atau izin perangkat belum dapat diverifikasi. Periksa token, URL HTTPS, dan koneksi lalu coba lagi.', 'error')
    return render_template('setup.html', access=True, status=setup_status()), status


@bp.context_processor
def organization_context():
    return {'organization_setup': organization.organization_status()}


@bp.post('/credentials')
@setup_access_required
def credentials():
    try:
        organization.connect_device(request.form.get('token', ''), request.form.get('base_url', ''))
    except FAILURES as error:
        return _failed(error)
    return redirect(url_for('setup.index'))


@bp.get('/organization')
@setup_access_required
def organization_status():
    return organization.organization_status()


@bp.post('/organization')
@setup_access_required
def organization_select():
    try:
        if request.form.get('subunit_id') not in (None, ''):
            raise ValueError('Seluruh SKPD tidak menggunakan subunit tertentu.')
        organization.select_organization(request.form.get('skpd_id'), request.form.get('scope_type'))
    except FAILURES as error:
        return _failed(error)
    return redirect(url_for('setup.index'))


@bp.post('/snapshot')
@setup_access_required
def snapshot_retry():
    try:
        organization.download_snapshot()
    except FAILURES as error:
        return _failed(error)
    return redirect(url_for('setup.index'))
