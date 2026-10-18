"""Setup model endpoints also remain available to a local admin after setup."""
from functools import wraps

from flask import abort, current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user

from extensions import login_manager
from kiosk.lock import AlreadyRunning
from routes.setup import bp, setup_access_required
from services.setup_models import model_setup_status, prepare_models
from services.setup_service import setup_required


def model_access_required(view):
    setup_view = setup_access_required(view)

    @wraps(view)
    def protected(*args, **kwargs):
        if not current_app.config.get('SETUP_ENABLED'):
            abort(404)
        if setup_required():
            return setup_view(*args, **kwargs)
        if not current_user.is_authenticated:
            return login_manager.unauthorized()
        if not current_user.is_active or current_user.role != 'admin':
            abort(403)
        return view(*args, **kwargs)
    return protected


@bp.app_context_processor
def model_template_helpers():
    return {'model_setup_status': model_setup_status}


@bp.get('/models/manage')
@model_access_required
def models_page():
    return render_template('setup_models_page.html')


@bp.route('/models', methods=['GET', 'POST'])
@model_access_required
def models():
    if request.method == 'GET':
        return model_setup_status()
    try:
        selected = request.form.get('model') or None
        status = prepare_models(selected)
        from services.setup_camera import activate_browser
        activate_browser()
        prepared = status['verified'] if selected is None else any(
            model['key'] == selected and model['state'] == 'verified' for model in status['models'])
        code = 200 if prepared else 503
    except ValueError:
        return {'error': 'unknown_model'}, 400
    except AlreadyRunning:
        return {'error': 'model_download_busy', 'message': 'Unduhan model sedang berjalan. Tunggu hasilnya.'}, 409
    if request.accept_mimetypes.best == 'application/json':
        return status, code
    flash('Model terverifikasi.' if status['verified'] else 'Sebagian model belum siap. Periksa keterangan lalu coba lagi.',
          'success' if status['verified'] else 'error')
    return redirect(url_for('setup.models_page'))
