"""Device credential recovery stays behind the completed-device admin policy."""
from flask import flash, redirect, render_template, request, url_for

from routes.setup import bp
from routes.settings_scope import admin_settings_required
from routes.setup_organization import FAILURES
from integration.client import RequestFailure
from services import settings_token


@bp.get('/token/manage')
@admin_settings_required
def token_page():
    return render_template('settings_token.html', credential=settings_token.token_status())


@bp.route('/token', methods=['GET', 'POST'])
@admin_settings_required
def token_repair():
    if request.method == 'GET':
        return settings_token.token_status()
    return _apply_repair(lambda: settings_token.repair_token(request.form.get('token')))


@bp.post('/token/retry')
@admin_settings_required
def token_retry():
    return _apply_repair(settings_token.retry_token)


def _apply_repair(action):
    try:
        action()
    except FAILURES as error:
        expected = isinstance(error, settings_token.TokenRepairError)
        code = error.code if expected else 'credential_unverified'
        message = error.message if expected else 'Token atau izin perangkat belum dapat diverifikasi. Periksa koneksi dan token lalu coba lagi.'
        status = error.status if expected else 403 if isinstance(error, RequestFailure) and error.status_code in (401, 403) else 400
        if request.accept_mimetypes.best == 'application/json':
            return {'ok': False, 'error': code, 'message': message, **settings_token.token_status()}, status
        flash(message, 'error')
        return redirect(url_for('setup.token_page'))
    if request.accept_mimetypes.best == 'application/json':
        return {'ok': True, **settings_token.token_status()}
    flash('Token perangkat yang sama terverifikasi. Periksa kesiapan sebelum membuka sesi kamera baru.', 'success')
    return redirect(url_for('setup.token_page'))
