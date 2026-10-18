"""
Halaman kios untuk karyawan (PRD 7.6, 10.3). Tidak memerlukan login.

Kamera native tetap memakai status saja; sumber browser diaktifkan lewat konfigurasi.
"""
from urllib.parse import urlsplit

from flask import Blueprint, current_app, jsonify, render_template, request, session
from flask_wtf.csrf import validate_csrf
from werkzeug.exceptions import RequestEntityTooLarge
from wtforms import ValidationError

from extensions import csrf
from kiosk.state import KioskState, public_view
from models import WEEKDAY_NAMES, office_now

bp = Blueprint("kiosk", __name__, url_prefix="/kiosk")

MONTHS = ("Januari", "Februari", "Maret", "April", "Mei", "Juni", "Juli",
          "Agustus", "September", "Oktober", "November", "Desember")


def _date_text(now):
    return f"{WEEKDAY_NAMES[now.weekday()]}, {now.day} {MONTHS[now.month - 1]} {now.year}"


@bp.get("/")
def page():
    return render_template("kiosk.html", browser_camera_enabled=current_app.config.get('CAMERA_SOURCE') == 'browser')


@bp.get("/status")
def status():
    view = public_view(KioskState(current_app.config["KIOSK_STATE_FILE"]).read(),
                       test_mode=current_app.config.get('EOFFICE_TEST_MODE', False))
    now = office_now()
    view["time"] = now.strftime("%H:%M:%S")
    view["date"] = _date_text(now)
    response = jsonify(view)
    response.headers["Cache-Control"] = "no-store"
    return response


@bp.record_once
def register_browser_camera(state):
    if state.app.config.get('CAMERA_SOURCE') == 'browser':
        from kiosk.browser import BrowserCamera
        state.app.extensions['browser_camera'] = BrowserCamera(state.app)


def _camera_error(code, status):
    return jsonify(ok=False, error=code), status


@bp.before_request
def protect_camera():
    if request.endpoint not in ('kiosk.camera_start', 'kiosk.camera_challenge',
                                'kiosk.camera_frame', 'kiosk.camera_stop'):
        return None
    return validate_camera_request()


def validate_camera_request(*, browser=True):
    if browser and current_app.config.get('CAMERA_SOURCE') != 'browser':
        return _camera_error('disabled', 404)
    if (urlsplit(request.host_url).hostname not in ('localhost', '127.0.0.1', '::1')
            or request.headers.get('Origin') != request.host_url.rstrip('/')):
        return _camera_error('origin', 403)
    try:
        validate_csrf(request.headers.get('X-CSRFToken'))
    except ValidationError:
        return _camera_error('csrf', 403)


@bp.after_request
def camera_no_store(response):
    if request.path.startswith('/kiosk/camera/') or current_app.config.get('CAMERA_SOURCE') == 'browser':
        response.headers['Cache-Control'] = 'no-store'
    return response


def _camera_call(method):
    from kiosk.browser import CameraRequestError
    try:
        runtime = current_app.extensions['browser_camera']
        return jsonify(getattr(runtime, method)(session['csrf_token'], request.headers.get('X-Camera-Owner'),
                                                request.headers['Origin']))
    except CameraRequestError as exc:
        return _camera_error(exc.code, exc.status)


@bp.post('/camera/start')
@csrf.exempt  # Explicit JSON CSRF validation in protect_camera also applies in preview/testing.
def camera_start():
    from kiosk.browser import CameraRequestError
    try:
        if current_app.config.get('SETUP_ENABLED'):
            from services.setup_readiness import require_capture_configuration
            try:
                require_capture_configuration()
            except ValueError:
                return _camera_error('not_ready', 409)
            from services.setup_camera import browser_runtime
            browser_runtime(preview=False)
        return jsonify(current_app.extensions['browser_camera'].start(session['csrf_token'], request.headers['Origin']))
    except CameraRequestError as exc:
        return _camera_error(exc.code, exc.status)


@bp.post('/camera/challenge')
@csrf.exempt
def camera_challenge():
    return _camera_call('challenge')


@bp.post('/camera/frame')
@csrf.exempt
def camera_frame():
    from kiosk.browser import CameraRequestError, MAX_BYTES
    runtime, capture = current_app.extensions['browser_camera'], None
    try:
        capture = runtime.reserve(session['csrf_token'], request.headers.get('X-Camera-Owner'),
                                  request.headers['Origin'], request.headers.get('X-Camera-Challenge'))
        if request.args:
            raise CameraRequestError('invalid_frame', 400)
        if request.content_type != 'image/jpeg' or request.headers.get('Content-Encoding', 'identity') != 'identity':
            raise CameraRequestError('unsupported_media', 415)
        request.max_content_length = MAX_BYTES
        return jsonify(runtime.submit(capture, request.get_data(cache=False)))
    except RequestEntityTooLarge:
        return _camera_error('frame_too_large', 413)
    except CameraRequestError as exc:
        return _camera_error(exc.code, exc.status)
    finally:
        if capture is not None:
            runtime.abort(capture)


@bp.post('/camera/stop')
@csrf.exempt
def camera_stop():
    return _camera_call('stop')
