"""
Perintah menjalankan layanan kamera kios:

    flask --app app kiosk-run              (dengan jendela pratinjau)
    flask --app app kiosk-run --no-preview

Jalankan di terminal terpisah dari `flask --app app run`. Hentikan dengan Ctrl+C.
"""
from __future__ import annotations

import logging
import signal
from pathlib import Path
from typing import TYPE_CHECKING, TypedDict

import click

from extensions import db
from models import Employee
from services.cli_errors import friendly_db_errors


if TYPE_CHECKING:
    from flask import Flask
    from integration.store import IntegrationStore
    from integration.sync import SyncWorker
    from integration.clock import TrustedClock
    from recognition.detector import QualitySettings, YuNetDetector
    from recognition.eoffice_identify import EofficeModels
    from recognition.liveness import LivenessSettings, PassiveLiveness


class EofficeDependencies(TypedDict):
    integration_store: IntegrationStore
    sync_worker: SyncWorker
    trusted_clock: TrustedClock
    eoffice_models: EofficeModels


def load_eoffice(app: Flask) -> tuple[YuNetDetector, QualitySettings, PassiveLiveness, LivenessSettings, EofficeDependencies]:
    """Load only e-office assets; two connections separate inference from sync I/O."""
    from integration.client import EofficeClient
    from integration.clock import TrustedClock
    from integration.store import IntegrationStore
    from integration.sync import SyncWorker
    from recognition import model_files as mf
    from recognition.detector import QualitySettings, YuNetDetector
    from recognition.eoffice_models import AuraFaceEmbedder, PeriocularEmbedder
    from recognition.eoffice_identify import EofficeModels
    from recognition.cli import _load_liveness
    from services.biometric_service import load_keyring

    detector = YuNetDetector(mf.verify(app.config['MODEL_DIR'], mf.YUNET),
                             detect_width=app.config['FACE_DETECT_WIDTH'])
    embedders = {'auraface_512': AuraFaceEmbedder(app.config['EOFFICE_AURAFACE_PATH']),
                 'mobilefacenet_v1_periocular': PeriocularEmbedder(app.config['EOFFICE_PERIOCULAR_PATH'])}
    liveness, settings = _load_liveness(app)
    if settings.mode != 'passive' or liveness is None:
        raise ValueError('E-office requires passive liveness')
    active, keys = load_keyring(app.config['EOFFICE_KEY_FILE'])
    test_mode = app.config.get('EOFFICE_TEST_MODE', False)
    client = EofficeClient(app.config['EOFFICE_BASE_URL'], app.config['EOFFICE_DEVICE_TOKEN'], test_mode=test_mode)
    path = Path(app.instance_path) / ('eoffice-test.sqlite3' if test_mode else 'eoffice.sqlite3')
    frame_store = IntegrationStore(path, keys[active], test_mode=test_mode)
    worker_store = None
    try:
        worker_store = IntegrationStore(path, keys[active], test_mode=test_mode)
        clock = TrustedClock(store=worker_store)
        sync = SyncWorker(worker_store, client, clock)
    except BaseException:
        if worker_store is not None:
            worker_store.close()
        frame_store.close()
        raise
    return detector, QualitySettings(), liveness, settings, {
        'integration_store': frame_store, 'sync_worker': sync, 'trusted_clock': clock,
        'eoffice_models': EofficeModels(embedders, liveness),
    }


def register_kiosk_commands(app):
    @app.cli.command("kiosk-run")
    @click.option("--preview/--no-preview", default=True, show_default=True,
                  help="Tampilkan jendela kamera di komputer kios.")
    @friendly_db_errors
    def kiosk_run(preview):
        """Menjalankan layanan kamera yang MENCATAT ABSENSI otomatis."""
        import cv2

        from camera import webcam
        from kiosk.lock import AlreadyRunning, SingleInstanceLock
        from kiosk.state import KioskState
        from kiosk.worker import KioskWorker
        from recognition import identify
        from recognition.cli import _load_liveness, _load_models
        from recognition.matcher import Matcher
        from services import attendance_service as att
        from services import biometric_service as biometric
        from tracking.face_tracker import TrackerSettings

        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
        # Pesan teknis pustaka pembaca nama kamera tidak perlu tampil.
        logging.getLogger("comtypes").setLevel(logging.WARNING)

        lock = SingleInstanceLock(app.config["KIOSK_LOCK_FILE"])
        try:
            lock.acquire()
        except AlreadyRunning as exc:
            raise click.ClickException(str(exc))

        cam = None
        integration = {}
        try:
            people = {}
            eoffice = app.config.get('ATTENDANCE_MODE', 'standalone') == 'eoffice'
            if eoffice:
                detector, quality, liveness, live_settings, integration = load_eoffice(app)
                embedder, matcher_factory = None, None
            else:
                detector, embedder, quality = _load_models(app)
                liveness, live_settings = _load_liveness(app)

                def matcher_factory():
                    templates, failed = biometric.load_active_templates(embedder.model_name)
                    if failed:
                        logging.warning("%s templat gagal diverifikasi dan dilewati.", failed)
                    people.clear()
                    people.update({
                        e.id: (e.employee_code, e.full_name)
                        for e in db.session.execute(db.select(Employee)).scalars()
                    })
                    return Matcher(templates, threshold=app.config["FACE_MATCH_THRESHOLD"],
                                   margin=app.config["FACE_MATCH_MARGIN"])

            cam = webcam.Webcam(webcam.CameraSettings.from_config(app.config))
            try:
                cam.open()
                a = cam.actual
                click.echo(f"Kamera: {a.get('device_name') or 'indeks ' + str(a['index'])}, "
                           f"{a['width']}x{a['height']}")
            except webcam.CameraError as exc:
                click.secho(f"Kamera belum siap: {exc} Akan dicoba lagi otomatis.", fg="yellow")

            test_mode = eoffice and app.config.get('EOFFICE_TEST_MODE', False)
            window = 'Uji pengenalan kamera' if test_mode else 'Kios absensi - kamera'

            def show(frame, tracks):
                header = ["MENCATAT ABSENSI", "Ctrl+C di terminal untuk berhenti"]
                gate_state = {"prompts": {}, "pending": set(worker.gate.pending),
                              "rejected": worker.gate.rejected_tracks,
                              "settings": worker.gate.s}
                if eoffice:
                    from kiosk.state import public_view
                    view = frame.copy()
                    message = public_view({'mode': 'eoffice', 'heartbeat': worker.wall_clock(),
                                           **({'purpose': 'camera_test'} if test_mode else {}),
                                           'message': worker.message}, now=worker.wall_clock())['message']
                    text = message['text'] if message else 'Silakan berjalan di depan kamera'
                    cv2.putText(view, text, (12, 30), cv2.FONT_HERSHEY_SIMPLEX, .7, (255, 255, 255), 2)
                    if test_mode:
                        cv2.putText(view, 'UJI PENGENALAN - BUKAN ABSENSI RESMI', (12, 60),
                                    cv2.FONT_HERSHEY_SIMPLEX, .6, (0, 255, 255), 2)
                else:
                    view = identify.annotate(frame, tracks, people, header, gate_state)
                cv2.imshow(window, view)
                cv2.waitKey(1)
                return True

            worker = KioskWorker(
                app, cam, detector, embedder, quality, matcher_factory,
                TrackerSettings.from_config(app.config),
                None if eoffice else att.AttendanceSettings.from_config(app.config),
                KioskState(app.config["KIOSK_STATE_FILE"]),
                template_reload_seconds=app.config["TEMPLATE_RELOAD_SECONDS"],
                on_frame=show if preview else None,
                liveness=liveness,
                liveness_settings=live_settings,
                match_threshold=app.config["FACE_MATCH_THRESHOLD"],
                match_margin=app.config["FACE_MATCH_MARGIN"],
                **integration,
            )

            stop = {"flag": False}

            def request_stop(*_):
                stop["flag"] = True

            signal.signal(signal.SIGINT, request_stop)
            click.secho(('Uji pengenalan kamera berjalan; tidak mencatat absensi resmi. Buka /kiosk di browser.'
                         if test_mode else 'Layanan kamera berjalan dan MENCATAT ABSENSI. Buka /kiosk di browser.'),
                        fg="green")
            if live_settings.mode == "off":
                click.secho("Peringatan: pemeriksaan anti-foto DIMATIKAN (LIVENESS_MODE=off).", fg="yellow")
            else:
                click.echo(f"Anti-foto aktif: {live_settings.mode}")
            worker.run(should_stop=lambda: stop["flag"])
        finally:
            if integration:
                sync = integration['sync_worker']
                sync.stop(timeout=0.0)
                if not sync.started:
                    sync.store.close()
                integration['integration_store'].close()
            if cam is not None:
                cam.release()
            if preview:
                try:
                    cv2.destroyAllWindows()
                except Exception:
                    pass
            lock.release()
            click.echo("Layanan kamera berhenti.")
