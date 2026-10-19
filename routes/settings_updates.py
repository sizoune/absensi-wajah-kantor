"""Update controls share the existing local administrator and CSRF boundary."""
from flask import abort, current_app, render_template

from routes.setup import bp
from routes.settings_scope import admin_settings_required
from services.update_package import UpdateError


def manager():
    value = current_app.extensions.get('app_updates')
    if value is None:
        abort(404)
    return value


@bp.get('/updates')
@admin_settings_required
def updates_page():
    return render_template('settings_updates.html', update=manager().status())


@bp.get('/updates/status')
@admin_settings_required
def updates_status():
    return manager().status()


def start(action):
    updater = manager()
    try:
        updater.start(action)
    except UpdateError as exc:
        return {'error': 'update_busy', 'message': str(exc)}, 409
    return updater.status(), 202


@bp.post('/updates/check')
@admin_settings_required
def updates_check():
    return start('check')


@bp.post('/updates/install')
@admin_settings_required
def updates_install():
    return start('install')
