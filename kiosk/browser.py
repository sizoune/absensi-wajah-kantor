"""Bounded browser JPEG transport; preview never creates an attendance worker."""
import atexit
import secrets
import struct
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from queue import Empty, Full, Queue
from threading import Event, RLock, Thread

import cv2
import numpy as np

from kiosk.lock import AlreadyRunning, SingleInstanceLock
from kiosk.control import capture_paused
from kiosk.state import KioskState

MAX_BYTES = 512 * 1024
MAX_WIDTH, MAX_HEIGHT = 1280, 720
CHALLENGE_SECONDS, SESSION_SECONDS, MAX_SESSION_SECONDS = 3.0, 15.0, 1800.0
TRACK_GAP_SECONDS = 2.0
PREVIEW_HINT = 'Uji pratinjau kamera — tidak mencatat absensi.'


class CameraRequestError(Exception):
    def __init__(self, code, status):
        self.code, self.status = code, status
        super().__init__(code)


def decode_jpeg(data):
    """Validate baseline JPEG dimensions before OpenCV can allocate image memory."""
    if not data or len(data) > MAX_BYTES:
        raise CameraRequestError('frame_too_large' if data else 'invalid_frame', 413 if data else 400)
    if not data.startswith(b'\xff\xd8') or not data.endswith(b'\xff\xd9'):
        raise CameraRequestError('invalid_frame', 400)
    pos, size, scanned = 2, None, False
    try:
        while pos < len(data):
            if scanned:
                pos = data.index(b'\xff', pos)
            if data[pos] != 255:
                raise ValueError
            while data[pos] == 255:
                pos += 1
            marker = data[pos]
            pos += 1
            if scanned and (marker == 0 or 0xd0 <= marker <= 0xd7):
                continue
            if marker == 0xd9:
                if pos != len(data) or size is None or not scanned:
                    raise ValueError
                break
            if marker in (0, 0xd8) or 0xd0 <= marker <= 0xd7:
                raise ValueError
            if scanned or marker not in (0xc0, 0xc4, 0xda, 0xdb, 0xdd, 0xfe) and not 0xe0 <= marker <= 0xef:
                raise ValueError
            length, = struct.unpack_from('>H', data, pos)
            end = pos + length
            if length < 2 or end > len(data):
                raise ValueError
            if marker == 0xc0:
                if size is not None or scanned or length != 17 or data[pos + 2] != 8 or data[pos + 7] != 3:
                    raise ValueError
                height, width = struct.unpack_from('>HH', data, pos + 3)
                if not 1 <= width <= MAX_WIDTH or not 1 <= height <= MAX_HEIGHT:
                    raise ValueError
                size = height, width
            if marker == 0xda:
                if size is None or scanned:
                    raise ValueError
                scanned = True
            pos = end
        else:
            raise ValueError
        frame = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR)
        if frame is None or frame.shape != (*size, 3):
            raise ValueError
        return frame
    except (ValueError, IndexError, struct.error, cv2.error) as exc:
        raise CameraRequestError('invalid_frame', 400) from exc


@dataclass
class Capture:
    owner: str
    generation: int
    acquired_at: float
    deadline: float
    done: Event = field(default_factory=Event)
    data: bytes | None = None
    error: CameraRequestError | None = None
    submitted: bool = False


class BrowserCamera:
    def __init__(self, app, *, preview=False):
        self.app = app
        self.mode = 'eoffice' if app.config['ATTENDANCE_MODE'] == 'eoffice' else 'preview'
        if self.mode == 'eoffice' and app.config.get('EOFFICE_TEST_MODE', False):
            self.mode = 'camera_test'
        if preview:
            self.mode = 'preview'
        self.clock = time.monotonic
        self._lock = RLock()
        self._queue = Queue(maxsize=1)
        self._shutdown, self._ready = Event(), Event()
        self._thread = None
        self._process_lock = SingleInstanceLock(app.config['KIOSK_LOCK_FILE'])
        self._owner = self._cookie = self._origin = self._challenge = None
        self._busy = False
        self._generation = 0
        self._started_at = self._last_activity = 0.0
        self._fatal = False
        self._capture = None
        self._sync = None
        self._last_frame_at = None
        atexit.register(self.close)

    @property
    def active(self):
        with self._lock:
            return self._owner is not None

    @property
    def closed(self):
        return self._shutdown.is_set() or self._fatal

    @property
    def capturing(self):
        with self._lock:
            self._expire()
            return (self._owner is not None and not self.closed and self._last_frame_at is not None
                    and 0 <= self.clock() - self._last_frame_at <= TRACK_GAP_SECONDS)

    def _release(self):
        self._owner = self._cookie = self._origin = self._challenge = None
        self._last_frame_at = None
        self._generation += 1

    def _expire(self):
        now = self.clock()
        if self._owner and (now - self._last_activity > SESSION_SECONDS
                            or now - self._started_at > MAX_SESSION_SECONDS):
            self._release()

    def _authorize(self, cookie, owner, origin):
        if capture_paused(self.app):
            raise CameraRequestError('configuration_held', 409)
        self._expire()
        if self._owner is None:
            raise CameraRequestError('expired', 410)
        if (not owner or not owner.isascii() or not secrets.compare_digest(owner, self._owner)
                or cookie != self._cookie or origin != self._origin):
            raise CameraRequestError('owner', 403)

    def _ensure_started(self):
        if self._shutdown.is_set() or self._fatal:
            raise CameraRequestError('unavailable', 503)
        if self._thread is None:
            try:
                self._process_lock.acquire()
            except AlreadyRunning as exc:
                raise CameraRequestError('busy', 409) from exc
            self._thread = Thread(target=self._run, name='browser-camera', daemon=True)
            try:
                self._thread.start()
            except BaseException:
                self._thread = None
                self._process_lock.release()
                raise

    def start_service(self):
        """Resume configured integration sync without granting camera ownership."""
        with self._lock:
            self._ensure_started()
        if not self._ready.wait(30) or self._fatal:
            raise CameraRequestError('unavailable', 503)

    def start(self, cookie, origin):
        with self._lock:
            if capture_paused(self.app):
                raise CameraRequestError('configuration_held', 409)
            self._expire()
            if self._owner or self._busy:
                raise CameraRequestError('busy', 409)
            self._ensure_started()
            self._owner = secrets.token_urlsafe(32)
            self._cookie, self._origin = cookie, origin
            self._started_at = self._last_activity = self.clock()
            owner = self._owner
        if not self._ready.wait(30) or self._fatal:
            with self._lock:
                self._release()
            raise CameraRequestError('unavailable', 503)
        with self._lock:
            self._authorize(cookie, owner, origin)
            self._last_activity = self.clock()
        return {'ok': True, 'owner': owner, 'mode': self.mode, 'session_seconds': SESSION_SECONDS,
                'challenge_seconds': CHALLENGE_SECONDS, 'max_width': MAX_WIDTH, 'max_height': MAX_HEIGHT}

    def challenge(self, cookie, owner, origin):
        with self._lock:
            self._authorize(cookie, owner, origin)
            if self._busy:
                raise CameraRequestError('in_flight', 409)
            nonce = secrets.token_urlsafe(32)
            self._last_activity = self.clock()
            self._challenge = nonce, self._last_activity
            return {'ok': True, 'challenge': nonce, 'expires_in': CHALLENGE_SECONDS}

    def reserve(self, cookie, owner, origin, nonce):
        with self._lock:
            self._authorize(cookie, owner, origin)
            if self._busy:
                raise CameraRequestError('in_flight', 409)
            challenge, self._challenge = self._challenge, None
            if (not nonce or not nonce.isascii() or challenge is None or not secrets.compare_digest(nonce, challenge[0])
                    or self.clock() - challenge[1] > CHALLENGE_SECONDS):
                self._generation += 1
                raise CameraRequestError('stale', 410)
            self._last_activity = self.clock()
            self._busy = True
            return Capture(owner, self._generation, challenge[1], challenge[1] + CHALLENGE_SECONDS)

    def abort(self, capture):
        with self._lock:
            if not capture.submitted:
                self._busy = False
                self._generation += 1

    def submit(self, capture, data):
        with self._lock:
            self._check_capture(capture)
            capture.data, capture.submitted = data, True
            try:
                self._queue.put_nowait(capture)
            except Full as exc:
                capture.data, capture.submitted = None, False
                raise CameraRequestError('in_flight', 409) from exc
        if not capture.done.wait(CHALLENGE_SECONDS + 1):
            with self._lock:
                self._release()
            raise CameraRequestError('processing_timeout', 504)
        if capture.error:
            raise capture.error
        return {'ok': True, 'mode': self.mode}

    def stop(self, cookie, owner, origin):
        with self._lock:
            self._authorize(cookie, owner, origin)
            self._release()
        return {'ok': True}

    def stop_session(self):
        """Revoke ownership; in-flight frames still hold busy until safely discarded."""
        with self._lock:
            self._release()
            return {'ok': True, 'draining': self._busy}

    def _check_capture(self, capture):
        self._expire()
        if (capture_paused(self.app) or self._shutdown.is_set() or capture is None or capture.owner != self._owner
                or capture.generation != self._generation or self.clock() > capture.deadline):
            raise CameraRequestError('stale', 410)

    @contextmanager
    def _frame_guard(self):
        with self._lock:
            self._check_capture(self._capture)
            yield lambda: self._check_capture(self._capture)

    def _reset_worker(self, worker):
        if worker is not None:
            from recognition.eoffice_identify import EofficeTracker
            worker.tracker = EofficeTracker(worker.tracker_settings)
            worker._queued_tracks.clear()
            worker._frame_time = worker._current_event = worker._current_capture = worker.message = worker.hint = None

    def _run(self):
        worker, integration = None, {}
        state = KioskState(self.app.config['KIOSK_STATE_FILE'])
        generation, last_capture, last_publish = -1, None, -1e9
        with self.app.app_context():
            try:
                if self.mode != 'preview':
                    from kiosk.cli import load_eoffice
                    from kiosk.worker import KioskWorker
                    from tracking.face_tracker import TrackerSettings
                    detector, quality, liveness, settings, integration = load_eoffice(self.app)
                    self._sync = integration['sync_worker']
                    worker = KioskWorker(self.app, None, detector, None, quality, None,
                        TrackerSettings.from_config(self.app.config), None, state,
                        liveness=liveness, liveness_settings=settings, frame_guard=self._frame_guard,
                        clock=lambda: self.clock(), **integration)
                    integration['sync_worker'].start()
                self._ready.set()
                while not self._shutdown.is_set():
                    with self._lock:
                        self._expire()
                        current_generation, active = self._generation, self._owner is not None
                    if generation != current_generation:
                        self._reset_worker(worker)
                        generation, last_capture = current_generation, None
                    if worker is not None:
                        worker.reload_templates()
                        worker.camera_status = 'ok' if self.capturing else 'stopped'
                        worker.publish()
                    elif self.clock() - last_publish >= .5:
                        state.publish({'camera': 'ok' if active else 'stopped', 'message': None,
                                       'hint': PREVIEW_HINT, 'mode': 'preview'})
                        last_publish = self.clock()
                    try:
                        capture = self._queue.get(timeout=.1)
                    except Empty:
                        continue
                    self._capture = capture
                    try:
                        with self._frame_guard():
                            pass
                        frame = decode_jpeg(capture.data)
                        with self._frame_guard():
                            pass
                        if (capture.generation != generation or last_capture is not None
                                and capture.acquired_at - last_capture > TRACK_GAP_SECONDS):
                            self._reset_worker(worker)
                        generation, last_capture = capture.generation, capture.acquired_at
                        if worker is not None:
                            worker.reload_templates()
                            worker.step(frame, captured_at=capture.acquired_at)
                        with self._frame_guard():
                            self._last_frame_at = self.clock()
                        if worker is not None:
                            worker.camera_status = 'ok'
                            worker.publish(force=True)
                    except CameraRequestError as exc:
                        capture.error = exc
                        with self._lock:
                            self._last_frame_at = None
                        self._reset_worker(worker)
                        last_capture = None
                    except Exception:
                        self.app.logger.exception('Browser camera frame failed')
                        capture.error = CameraRequestError('unavailable', 503)
                        with self._lock:
                            self._last_frame_at = None
                        self._reset_worker(worker)
                        last_capture = None
                    finally:
                        frame = None
                        capture.data = self._capture = None
                        with self._lock:
                            self._busy = False
                        capture.done.set()
            except Exception:
                self.app.logger.exception('Browser camera runtime failed')
                self._fatal = True
            finally:
                self._ready.set()
                try:
                    if integration:
                        sync = integration['sync_worker']
                        sync.stop(timeout=6)
                        if not sync.started:
                            sync.store.close()
                        integration['integration_store'].close()
                    state.publish({'camera': 'stopped', 'message': None,
                                   'mode': 'eoffice' if worker is not None else 'preview',
                                   **({'purpose': 'camera_test'} if self.mode == 'camera_test' else {}),
                                   'hint': PREVIEW_HINT if self.mode == 'preview' else None})
                except Exception:
                    self.app.logger.exception('Browser camera cleanup failed')
                finally:
                    with self._lock:
                        self._release()
                    try:
                        abandoned = self._queue.get_nowait()
                    except Empty:
                        pass
                    else:
                        abandoned.data = None
                        abandoned.error = CameraRequestError('unavailable', 503)
                        abandoned.done.set()
                    self._process_lock.release()

    def close(self):
        self._shutdown.set()
        with self._lock:
            self._release()
        if self._thread is not None:
            self._thread.join(timeout=7)
        drained = ((self._thread is None or not self._thread.is_alive())
                   and (self._sync is None or self._sync.stop(timeout=0)))
        if drained:
            atexit.unregister(self.close)
        return drained
