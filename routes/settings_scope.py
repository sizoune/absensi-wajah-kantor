"""Post-setup scope changes use existing local admin authentication and CSRF."""
from functools import wraps

from flask import abort, flash, redirect, render_template, request, url_for

from routes.setup import bp
from routes.setup_models import model_access_required
from routes.setup_organization import FAILURES
from services import settings_scope
from services.setup_service import setup_required, setup_status


def admin_settings_required(view):
    @wraps(view)
    @model_access_required
    def protected(*args, **kwargs):
        if setup_required():
            abort(404)
        return view(*args, **kwargs)
    return protected


@bp.get('/scope/manage')
@admin_settings_required
def scope_page():
    return render_template('settings_scope.html', status=setup_status(), scope_change=settings_scope.scope_status())


@bp.post('/scope/organizations')
@admin_settings_required
def scope_organizations():
    try:
        settings_scope.refresh_permissions()
    except FAILURES:
        flash('Daftar organisasi belum dapat diverifikasi. Periksa akses perangkat dan koneksi lalu coba lagi.', 'error')
    return redirect(url_for('setup.scope_page'))


@bp.route('/scope', methods=['GET', 'POST'])
@admin_settings_required
def scope_change():
    if request.method == 'GET':
        return settings_scope.scope_status()
    try:
        settings_scope.change_scope(request.form.get('skpd_id'), request.form.get('scope_type'),
                                    request.form.get('subunit_id'))
    except FAILURES as error:
        expected = isinstance(error, settings_scope.ScopeChangeError)
        message = error.message if expected else 'Cakupan belum dapat diterapkan. Periksa izin server dan koneksi, lalu coba lagi.'
        code = error.code if expected else 'scope_unverified'
        status = error.status if expected else 400
        if request.accept_mimetypes.best == 'application/json':
            return {'ok': False, 'error': code, 'message': message, **settings_scope.scope_status()}, status
        flash(message, 'error')
        return redirect(url_for('setup.scope_page'))
    if request.accept_mimetypes.best == 'application/json':
        return {'ok': True, **settings_scope.scope_status()}
    flash('Cakupan dan snapshot baru tersimpan. Buka kembali kamera untuk melanjutkan.', 'success')
    return redirect(url_for('setup.scope_page'))
