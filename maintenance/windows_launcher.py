"""One visible launcher owns loopback HTTP and the existing native retry script."""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
from threading import Event, Thread
import time
from urllib.request import urlopen
import webbrowser

from kiosk.lock import AlreadyRunning, SingleInstanceLock
from kiosk.control import CaptureBusy


def shutdown_notice(console, message):
    try:
        print(message, file=console, flush=True)
    except OSError:
        pass  # A closed terminal must not release ownership before writers drain.


def run(app=None, console=None, *, instance_path=None, app_factory=None):
    import waitress
    from waitress import wasyncore
    from services.setup_service import issue_setup_code, setup_required

    console = console or sys.stdout
    instance = Path(app.instance_path) if app is not None else Path(instance_path or Path(__file__).resolve().parents[1] / 'instance')
    with SingleInstanceLock(instance / 'launcher.lock'):
        if app is None:
            from app import create_app
            app = app_factory() if app_factory else create_app()
        with app.app_context():
            required = setup_required()
        if required and not console.isatty():
            raise RuntimeError('Buka peluncur pada terminal lokal untuk memperoleh kode setup.')
        connections = {}
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            if os.name == 'nt':
                # Winsock SO_REUSEADDR allows two servers on the same loopback port.
                listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            listener.bind(('127.0.0.1', 5000))
            server = waitress.create_server(app, sockets=[listener], threads=8, map=connections)
        except BaseException:
            listener.close()
            raise
        stopping = Event()

        def serve():
            try:
                server.run()
            except OSError:
                if not stopping.is_set():
                    raise

        thread = Thread(target=serve, daemon=True)
        url = f'http://127.0.0.1:{server.effective_port}'
        native = None
        stop_file = Path(app.instance_path) / 'launcher.stop'
        requested_stop = app.extensions.get('launcher_stop')
        try:
            thread.start()
            deadline = time.monotonic() + 15
            while True:
                try:
                    with urlopen(url + '/health', timeout=1) as response:
                        healthy = json.load(response).get('database') == 'ok'
                    if healthy:
                        break
                except (OSError, ValueError):
                    pass
                if not thread.is_alive() or time.monotonic() >= deadline:
                    raise RuntimeError('Server lokal belum siap. Periksa database dan aplikasi pada port 5000.')
                time.sleep(.1)
            if required:
                with app.app_context():
                    code = issue_setup_code()
                print(f'Kode setup: {code}', file=console, flush=True)
                print('Masukkan kode pada browser lokal; Ctrl+C menghentikan aplikasi.', file=console, flush=True)
            before_capture = app.extensions.get('before_capture')
            if before_capture is not None:
                before_capture()
            updater = app.extensions.get('app_updates')
            if updater is not None:
                updater.schedule()
            if app.config.get('CAMERA_SOURCE') == 'native':
                from kiosk.control import capture_fence, capture_paused
                if capture_paused(app):
                    with app.app_context(), capture_fence(app, reason='launcher') as fence:
                        if (fence.reason == 'launcher' and not app.config.get('SCOPE_CHANGE')
                                and not app.config.get('CREDENTIAL_REPAIR_PENDING')):
                            fence.resume()
                stop_file.unlink(missing_ok=True)
                command = ([sys.executable, '--root', app.config['UPDATE_INSTALL_ROOT'], 'kiosk']
                           if app.config.get('UPDATE_INSTALL_ROOT') else
                           [os.environ.get('COMSPEC', 'cmd.exe'), '/d', '/c', r'scripts\jalankan_kios.bat'])
                native = subprocess.Popen(command,
                    cwd=app.root_path,
                    env={**os.environ, 'ABSENSI_LAUNCHER_STOP_FILE': str(stop_file)},
                    creationflags=getattr(subprocess, 'CREATE_NEW_PROCESS_GROUP', 0),
                    start_new_session=os.name != 'nt')
            open_browser = webbrowser.open
            if sys.platform == 'linux' and getattr(sys, 'frozen', False):
                from maintenance.posix_install import open_browser
            if not open_browser(url + ('/setup' if required else '/kiosk/')):
                raise RuntimeError('Browser tidak dapat dibuka. Periksa browser bawaan sistem.')
            while thread.is_alive():
                if requested_stop is not None and requested_stop.is_set():
                    break
                thread.join(.5)
                if native is not None and native.poll() is not None:
                    raise RuntimeError('Proses pengulangan kamera berhenti; jalankan peluncur kembali.')
            if requested_stop is None or not requested_stop.is_set():
                raise RuntimeError('Server web berhenti; jalankan peluncur kembali.')
        except KeyboardInterrupt:
            shutdown_notice(console, 'Menghentikan aplikasi dengan bersih...')
        finally:
            updater = app.extensions.get('app_updates')
            if updater is not None:
                updater.closed.set()
            if native is not None:
                stop_file.write_text('stop\n', encoding='ascii')
            stopping.set()
            server.close()
            while server.task_dispatcher.threads:
                server.task_dispatcher.shutdown(timeout=8)
            wasyncore.close_all(map=connections)
            thread.join(2)
            if native is not None:
                try:
                    if app.config.get('UPDATE_INSTALL_ROOT'):
                        was_paused = capture_paused(app)
                        while True:
                            try:
                                with app.app_context(), capture_fence(app, timeout=1, reason='launcher') as fence:
                                    native.wait(timeout=1)
                                    if (not was_paused and not app.config.get('SCOPE_CHANGE')
                                            and not app.config.get('CREDENTIAL_REPAIR_PENDING')):
                                        fence.resume()
                                break
                            except (CaptureBusy, subprocess.TimeoutExpired):
                                shutdown_notice(console, 'Menunggu kamera dan sinkronisasi berhenti...')
                                time.sleep(.1)
                    else:
                        with app.app_context(), capture_fence(app, reason='launcher') as fence:
                            native.wait(timeout=12)
                            if (not fence.was_paused and not app.config.get('SCOPE_CHANGE')
                                    and not app.config.get('CREDENTIAL_REPAIR_PENDING')):
                                fence.resume()
                except (CaptureBusy, subprocess.TimeoutExpired) as exc:
                    raise RuntimeError('Kamera lama masih berhenti. Capture tetap ditahan; tunggu lalu coba lagi.') from exc
            else:
                runtime = app.extensions.get('browser_camera')
                if runtime is not None:
                    while not runtime.close():
                        shutdown_notice(console, 'Menunggu kamera dan sinkronisasi berhenti...')
            with app.app_context():
                from extensions import db
                db.session.remove()
                db.engine.dispose()


def main():
    if os.name != 'nt':
        raise SystemExit('Peluncur ini memerlukan Windows 10/11 x64.')
    root = Path(__file__).resolve().parents[1]
    os.chdir(root)
    try:
        run()
    except AlreadyRunning:
        print('Peluncur sudah berjalan. Gunakan jendela aplikasi yang lama.')
    except Exception:
        print('Aplikasi belum dapat dijalankan. Periksa port 5000, konfigurasi, '
              'izin penyimpanan, serta cadangan rahasia asli. Data lama tidak dihapus.', file=sys.stderr)
        if sys.stdin.isatty():
            input('Tekan Enter untuk menutup...')
        raise SystemExit(1)


if __name__ == '__main__':
    main()
