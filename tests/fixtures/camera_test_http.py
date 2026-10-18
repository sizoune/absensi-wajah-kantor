"""Loopback-only assert check for the built image; synthetic models and gallery.

Run with Python from CAM, or mount tests/ read-only in the image at /app/tests.
The HTTP bridge below is test-only; the production client still requires TLS.
"""
from datetime import datetime, timedelta, timezone
from http.cookiejar import CookieJar
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import logging
from pathlib import Path
import re
import runpy
import socket
import sys
from tempfile import TemporaryDirectory
from threading import Event, Thread
import time
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import urlsplit
from urllib.request import HTTPCookieProcessor, Request, build_opener, urlopen

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from integration.client import EofficeClient
from integration.clock import TrustedClock
from integration.store import IntegrationStore
from integration.sync import SyncWorker
from kiosk.browser import BrowserCamera
from recognition.detector import FaceDetection, QualitySettings
from recognition.eoffice_identify import EofficeModels
from recognition.liveness import LivenessSettings
from waitress import create_server


def check_trial(kind):
    snapshot = json.loads(Path(__file__).with_name('eoffice_snapshot.json').read_text())
    snapshot['purpose'] = 'camera_test'
    snapshot['issued_at'] = (datetime.now(timezone(timedelta(hours=8))) - timedelta(seconds=1)).replace(microsecond=0).isoformat()
    snapshot['employees'] = [employee for employee in snapshot['employees'] if employee['embedding_version'] == kind]
    # Blank synthetic pixels only: hardware quality thresholds are never changed.
    for policy in snapshot['models'].values():
        policy['quality'].update(min_size=30, min_sharpness=0)
    sent, records, results, paths = [], {}, {}, []

    class API(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def authorized(self):
            assert self.headers['Authorization'] == 'Bearer synthetic-test-device'
            assert self.headers['User-Agent'] == 'EofficeCamera/1.0'
            paths.append(self.path)

        def reply(self, value):
            body = json.dumps(value).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            self.authorized()
            assert self.path == '/api/kiosk-test/snapshot'
            self.reply(snapshot)

        def do_POST(self):
            self.authorized()
            assert self.path == '/api/kiosk-test/events'
            body = self.rfile.read(int(self.headers['Content-Length']))
            event = json.loads(body)
            assert set(event) == {'protocol_version', 'purpose', 'event_id', 'pegawai_id', 'occurred_at',
                'snapshot_id', 'enrollment_id', 'enrollment_revision', 'verification_type', 'embedding_version',
                'evidence', 'clock_anchor_id', 'elapsed_ms'}
            assert event['purpose'] == 'camera_test' and event['embedding_version'] == kind
            assert event['clock_anchor_id'] == snapshot['snapshot_id']
            occurred = datetime.fromisoformat(snapshot['issued_at']) + timedelta(milliseconds=event['elapsed_ms'])
            assert occurred.isoformat(timespec='milliseconds') == event['occurred_at']
            assert occurred <= datetime.now(timezone(timedelta(hours=8)))
            assert event['evidence'] == {'score': 1., 'second_score': -1., 'quality_ok': True, 'live': True, 'samples': 3}
            event_id = event['event_id']
            sent.append(body)
            if event_id not in records:
                records[event_id] = body
                results[event_id] = {'status': True, 'data': {'event_id': event_id, 'outcome': 'test_recorded',
                    'reason': None, 'test_id': 1, 'official_attendance': False, 'purpose': 'camera_test',
                    'received_at': datetime.now(timezone(timedelta(hours=8))).isoformat(timespec='milliseconds')}}
                # Persist first, then lose the response: retry must carry identical bytes.
                self.connection.shutdown(socket.SHUT_RDWR)
                self.connection.close()
            else:
                assert records[event_id] == body
                self.reply(results[event_id])

    api = ThreadingHTTPServer(('127.0.0.1', 0), API)
    api_thread = Thread(target=api.serve_forever, daemon=True)
    api_thread.start()
    with TemporaryDirectory(prefix='camera-test-http-') as directory:
        temp = Path(directory)
        app = runpy.run_path(str(ROOT / 'docker/start.py'))['create_container_app'](temp)
        app.extensions['browser_camera'].close()
        app.config.update(ATTENDANCE_MODE='eoffice', EOFFICE_TEST_MODE=True)
        runtime = app.extensions['browser_camera'] = BrowserCamera(app)
        models = EofficeModels({version: SimpleNamespace(
            manifest={'version': version, 'dimension': policy['dimension'], 'sha256': policy['weights_sha256']},
            embed=lambda frame, landmarks, dimension=policy['dimension']: np.eye(1, dimension, dtype=np.float32)[0])
            for version, policy in snapshot['models'].items()}, SimpleNamespace(score=lambda *args: .99))
        detection = FaceDetection(np.array([30, 30, 160, 170, 70, 80, 150, 80, 110, 120,
                                           80, 155, 140, 155, .99], dtype=np.float32))
        key = bytes(range(32))
        queue = temp / 'eoffice-test.sqlite3'

        def load(_app):
            frame_store = IntegrationStore(queue, key, test_mode=True)
            sync_store = IntegrationStore(queue, key, test_mode=True)
            clock = TrustedClock(sync_store)
            client = EofficeClient('https://synthetic.invalid', 'synthetic-test-device', test_mode=True)
            def bridge(request, timeout):
                assert request.full_url.startswith('https://synthetic.invalid/api/kiosk-test/')
                forwarded = Request(f'http://127.0.0.1:{api.server_port}' + urlsplit(request.full_url).path,
                                    data=request.data, headers=dict(request.header_items()))
                return urlopen(forwarded, timeout=timeout)
            client._opener.open = bridge
            return SimpleNamespace(detect=lambda frame: [detection]), QualitySettings(), models.liveness, LivenessSettings(mode='passive'), {
                'integration_store': frame_store, 'sync_worker': SyncWorker(sync_store, client, clock),
                'trusted_clock': clock, 'eoffice_models': models}

        server = create_server(app, host='127.0.0.1', port=0, threads=4,
                               max_request_body_size=524288, inbuf_overflow=1048576)
        web_stop = Event()
        def serve():
            while not web_stop.is_set():
                server.asyncore.loop(timeout=.05, count=1, map=server._map)
        web_thread = Thread(target=serve, daemon=True)
        web_thread.start()
        base = f'http://127.0.0.1:{server.effective_port}'
        browser = build_opener(HTTPCookieProcessor(CookieJar()))
        with patch('kiosk.cli.load_eoffice', load):
            try:
                runtime.start_service()
                assert not runtime.active
                page = browser.open(base + '/kiosk/').read().decode()
                assert 'Uji pengenalan kamera — tidak mencatat absensi resmi' in page
                csrf = re.search(r'data-csrf-token="([^"]+)"', page).group(1)
                headers = {'Origin': base, 'X-CSRFToken': csrf}
                def post(action, body=b'', extra=None):
                    request = Request(base + '/kiosk/camera/' + action, data=body, headers={**headers, **(extra or {})})
                    with browser.open(request, timeout=8) as response:
                        assert response.headers['Cache-Control'] == 'no-store'
                        return json.load(response)
                start = post('start')
                assert start['mode'] == 'camera_test'
                headers['X-Camera-Owner'] = start['owner']
                ready_deadline = time.monotonic() + 3
                while time.monotonic() < ready_deadline:
                    view = json.load(browser.open(base + '/kiosk/status'))
                    if view.get('integration', {}).get('clock') == 'ok':
                        break
                    time.sleep(.05)
                else:
                    raise AssertionError('Authenticated gallery and clock were not ready')
                jpeg = cv2.imencode('.jpg', np.full((240, 240, 3), 128, np.uint8))[1].tobytes()
                for _ in range(3):
                    challenge = post('challenge')
                    ack = post('frame', jpeg, {'Content-Type': 'image/jpeg', 'X-Camera-Challenge': challenge['challenge']})
                    assert ack == {'ok': True, 'mode': 'camera_test'}
                deadline = time.monotonic() + 6
                while time.monotonic() < deadline:
                    view = json.load(browser.open(base + '/kiosk/status'))
                    if view.get('message') and view['message']['text'].endswith('Uji tersimpan'):
                        break
                    time.sleep(.05)
                else:
                    state = json.loads((temp / 'kiosk_state.json').read_text())
                    raise AssertionError(f'No terminal test UI: paths={paths}, records={len(records)}, sends={len(sent)}, '
                        f'message={state.get("message", {}).get("status")}, integration={state.get("integration")}')
                assert view['purpose'] == 'camera_test'
                assert len(records) == 1 and len(sent) == 2 and sent[0] == sent[1]
                assert set(paths) == {'/api/kiosk-test/snapshot', '/api/kiosk-test/events'}
                with_store = IntegrationStore(queue, key, test_mode=True)
                try:
                    assert with_store.pending() == []
                    event_id, = records
                    assert with_store.terminal(event_id) == results[event_id]
                    assert records[event_id] not in queue.read_bytes()
                finally:
                    with_store.close()
                assert not (temp / 'eoffice.sqlite3').exists()
                assert post('stop')['ok']
                assert not runtime.active
                print(f'PASS {kind}: loopback HTTP browser→real worker→encrypted trial queue→lost-reply retry→Uji tersimpan', flush=True)
            finally:
                runtime.close()
                web_stop.set()
                web_thread.join(timeout=2)
                assert not web_thread.is_alive()
                server.close()
                server.task_dispatcher.shutdown()
                api.shutdown()
                api.server_close()
                api_thread.join(timeout=2)


if __name__ == '__main__':
    logging.basicConfig(level=logging.ERROR)
    for model in ('auraface_512', 'mobilefacenet_v1_periocular'):
        check_trial(model)
