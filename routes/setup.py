"""First setup pages. Cookie sessions and CSRF use the existing Flask extensions."""
from functools import wraps

from flask import Blueprint, abort, flash, redirect, render_template, request, session, url_for
from flask_login import login_user
from sqlalchemy.exc import SQLAlchemyError

from extensions import db
from services import ValidationError
from services.setup_service import create_first_admin, redeem_setup_code, setup_required, setup_status, valid_setup_grant

bp = Blueprint('setup', __name__, url_prefix='/setup')


def setup_access_required(view):
    @wraps(view)
    def protected(*args, **kwargs):
        if not setup_required():
            abort(404)
        if not valid_setup_grant(session.get('setup_grant')):
            abort(403)
        return view(*args, **kwargs)
    return protected


@bp.after_request
def no_store(response):
    response.headers['Cache-Control'] = 'no-store'
    response.headers['Referrer-Policy'] = 'no-referrer'
    return response


@bp.route('', methods=['GET', 'POST'])
def index():
    if not setup_required():
        abort(404)
    if request.method == 'POST':
        grant = redeem_setup_code(request.form.get('code'))
        if grant is None:
            flash('Kode setup salah, sudah dipakai, atau telah diganti.', 'error')
            return render_template('setup.html', access=False), 403
        session.clear()
        session['setup_grant'] = grant
        return redirect(url_for('setup.index'))
    access = valid_setup_grant(session.get('setup_grant'))
    return render_template('setup.html', access=access, status=setup_status() if access else None)


@bp.get('/status')
@setup_access_required
def status():
    return setup_status()


@bp.post('/admin')
@setup_access_required
def first_admin():
    try:
        user = create_first_admin(request.form.get('username', ''), request.form.get('password', ''))
        if user is not None:
            login_user(user)
    except ValidationError as exc:
        db.session.rollback()
        for error in exc.errors.values():
            flash(error, 'error')
        return render_template('setup.html', access=True, status=setup_status()), 400
    except SQLAlchemyError:
        db.session.rollback()
        flash('Admin belum tersimpan. Silakan coba lagi.', 'error')
        return render_template('setup.html', access=True, status=setup_status()), 503
    return redirect(url_for('setup.index'))
