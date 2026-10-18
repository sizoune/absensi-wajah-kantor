"""Camera repair and readiness stay available only to setup owners or local admins."""
from flask import current_app, make_response, redirect, render_template, request, session, url_for

from routes.setup import bp, setup_access_required
from routes.setup_models import model_access_required as setup_or_admin_required
from services.setup_readiness import readiness_status
from services.setup_service import finish_setup, record_progress, save_settings, setup_required


@bp.get('/readiness')
@setup_or_admin_required
def readiness():
    return readiness_status()


@bp.get('/readiness/manage')
@bp.get('/camera/manage')
@setup_or_admin_required
def readiness_page():
    return render_template('setup_readiness_page.html', status=readiness_status())


@bp.app_context_processor
def camera_template_helpers():
    from services.setup_camera import native_camera_status
    return {'native_camera_status': native_camera_status}


@bp.get('/camera')
@setup_or_admin_required
def camera_status():
    from services.setup_camera import native_camera_status
    return native_camera_status()


@bp.post('/camera/select')
@setup_or_admin_required
def camera_select():
    from kiosk.lock import AlreadyRunning
    from services.setup_camera import select_native_camera
    if current_app.config.get('CAMERA_SOURCE') != 'native':
        return {'ok': False, 'error': 'disabled'}, 404
    try:
        select_native_camera(request.form.get('index'))
        return {'ok': True}
    except ValueError:
        return {'ok': False, 'error': 'invalid_camera', 'message': 'Pilih kamera yang tersedia.'}, 400
    except AlreadyRunning:
        return {'ok': False, 'error': 'busy', 'message': 'Hentikan layanan kamera sebelum mengganti perangkat.'}, 409


@bp.post('/camera/check')
@setup_or_admin_required
def camera_check():
    import cv2
    from camera.webcam import CameraError
    from kiosk.lock import AlreadyRunning
    from routes.kiosk import validate_camera_request
    from services.setup_camera import check_native_camera
    if current_app.config.get('CAMERA_SOURCE') != 'native':
        return {'ok': False, 'error': 'disabled'}, 404
    denied = validate_camera_request(browser=False)
    if denied is not None:
        return denied
    try:
        return check_native_camera()
    except AlreadyRunning:
        return {'ok': False, 'error': 'busy', 'message': 'Kamera sedang digunakan layanan lain.'}, 409
    except (CameraError, OSError, ValueError, cv2.error):
        return {'ok': False, 'error': 'camera_unavailable',
                'message': 'Kamera belum mengirim gambar. Periksa izin, kabel, dan aplikasi lain lalu coba lagi.'}, 503


@bp.post('/camera/<action>')
@setup_or_admin_required
def browser_preview(action):
    from kiosk.browser import CameraRequestError
    from routes import kiosk
    from services.setup_camera import activate_browser, browser_runtime, record_camera_check
    actions = {'start': kiosk.camera_start, 'challenge': kiosk.camera_challenge,
               'frame': kiosk.camera_frame, 'stop': kiosk.camera_stop}
    if action not in actions:
        return {'ok': False, 'error': 'not_found'}, 404
    denied = kiosk.validate_camera_request()
    if denied is not None:
        return denied
    try:
        if action == 'start':
            runtime = browser_runtime(preview=True)
            return runtime.start(session['csrf_token'], request.headers['Origin'])
        runtime = current_app.extensions['browser_camera']
        if runtime.mode != 'preview' or runtime.closed:
            raise CameraRequestError('stale', 410)
        response = make_response(actions[action]())
        if action == 'frame' and response.status_code == 200:
            record_camera_check()
        if action == 'stop' and response.status_code == 200 and not setup_required():
            activate_browser()
        return response
    except CameraRequestError as exc:
        return {'ok': False, 'error': exc.code}, exc.status


@bp.post('/finish')
@setup_access_required
def finish():
    status = readiness_status()
    if not status['can_finish']:
        return {'ok': False, 'error': 'setup_incomplete', **status}, 409
    from kiosk.browser import CameraRequestError
    from services.setup_camera import activate_browser, browser_runtime
    if current_app.config.get('CAMERA_SOURCE') == 'browser':
        try:
            browser_runtime(preview=False)
        except CameraRequestError:
            return {'ok': False, 'error': 'camera_draining'}, 409
    record_progress('models', status['checks']['models'])
    save_settings({'SETUP_MANAGED': True})
    finish_setup()
    session.pop('setup_grant', None)
    activate_browser()
    status = readiness_status()
    if request.accept_mimetypes.best == 'application/json':
        return status
    return redirect(url_for('kiosk.page' if all(status['checks'].values()) else 'setup.readiness_page'))
