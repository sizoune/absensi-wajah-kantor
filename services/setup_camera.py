"""Hardware-only setup preview and a clean browser preview-to-capture handoff."""
import base64
from threading import RLock

import cv2
from flask import current_app

from camera import devices, webcam
from kiosk.browser import BrowserCamera, CameraRequestError
from kiosk.lock import SingleInstanceLock
from services.setup_readiness import camera_context, readiness_status
from services.setup_service import load_settings, record_progress, save_settings, setup_required


def browser_runtime(*, preview):
    app = current_app._get_current_object()
    with app.extensions.setdefault('setup_camera_lock', RLock()):
        runtime = app.extensions['browser_camera']
        desired = 'preview' if preview else 'camera_test' if app.config.get('EOFFICE_TEST_MODE') else 'eoffice'
        if runtime.mode != desired or runtime.closed:
            if not runtime.close():
                raise CameraRequestError('busy', 409)
            runtime = app.extensions['browser_camera'] = BrowserCamera(app, preview=preview)
        return runtime


def record_camera_check():
    context = camera_context()
    if load_settings().get('SETUP_CAMERA_CHECK') != context:
        save_settings({'SETUP_CAMERA_CHECK': context})
        record_progress('camera')


def activate_browser():
    """Existing sync runs without camera permission; actual capture needs a fresh owner."""
    if current_app.config.get('CAMERA_SOURCE') != 'browser' or setup_required():
        return
    status = readiness_status()
    needed = ('configuration', 'admin', 'storage', 'snapshot', 'models', 'liveness')
    if status['complete'] and all(status['checks'][key] for key in needed):
        try:
            browser_runtime(preview=False).start_service()
        except CameraRequestError:
            pass  # Readiness exposes the held runtime; a later admin repair/start retries it.


def select_native_camera(index):
    if not isinstance(index, str) or not index.isascii() or not index.isdecimal() or not 0 <= int(index) <= 16:
        raise ValueError('Pilih indeks kamera yang valid.')
    cameras = devices.list_cameras()
    if cameras is not None and int(index) >= len(cameras):
        raise ValueError('Kamera yang dipilih tidak ditemukan. Muat ulang daftar kamera.')
    with SingleInstanceLock(current_app.config['KIOSK_LOCK_FILE']):
        save_settings({'CAMERA_INDEX': int(index), 'CAMERA_NAME': '', 'SETUP_CAMERA_CHECK': None})
        record_progress('camera', False)


def native_camera_status():
    cameras = devices.list_cameras()
    return {'source': current_app.config['CAMERA_SOURCE'],
            'selected': current_app.config['CAMERA_INDEX'],
            'devices': None if cameras is None else [
                {'index': index, 'name': name} for index, name in enumerate(cameras)]}


def check_native_camera():
    with SingleInstanceLock(current_app.config['KIOSK_LOCK_FILE']):
        save_settings({'SETUP_CAMERA_CHECK': None})
        record_progress('camera', False)
        camera = webcam.Webcam(webcam.CameraSettings.from_config(current_app.config))
        try:
            camera.open()
            frame = camera.read()
            if frame is None:
                raise webcam.CameraError('Kamera tidak mengirim gambar. Periksa perangkat lalu coba lagi.')
            brightness, _ = webcam.frame_quality(frame)
            scale = min(1, 1280 / frame.shape[1], 720 / frame.shape[0])
            image = cv2.resize(frame, (max(1, int(frame.shape[1] * scale)), max(1, int(frame.shape[0] * scale))))
            ok, encoded = cv2.imencode('.jpg', image, [cv2.IMWRITE_JPEG_QUALITY, 75])
            if not ok:
                raise webcam.CameraError('Gambar kamera tidak dapat dibaca. Coba lagi.')
            result = {'ok': True, 'preview': 'data:image/jpeg;base64,' + base64.b64encode(encoded).decode('ascii'),
                      'lighting': webcam.lighting_advice(brightness)}
        finally:
            camera.release()
        record_camera_check()
    return result
