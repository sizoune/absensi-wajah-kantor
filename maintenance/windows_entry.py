"""Frozen executable entry point; maintenance modes never start camera or sync."""
import argparse
import importlib
import os
from pathlib import Path
import subprocess
import sys
import signal
from threading import Event
import time

from app_version import VERSION
from maintenance import platforms
from maintenance.distribution import active_version, initialize, read_json, startup_allowed
from maintenance.kiosk_schema import migrate_instance
from services.update_package import UpdateError


def public_key():
    base = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parents[1]))
    key = (base / 'distribution/update-public-key.bin').read_bytes()
    if len(key) != 32 or key == bytes(32):
        raise UpdateError('Public key pembaruan belum disiapkan.')
    return key


def packaged_app(root):
    from app import create_app
    root = Path(root).resolve()
    instance = root / 'instance'
    instance.mkdir(mode=0o700, parents=True, exist_ok=True)
    migrate_instance(instance)
    app = create_app('production', instance_path=str(instance), overrides={
        'FIRST_SETUP_ENABLED': True, 'ATTENDANCE_MODE': 'eoffice', 'ATTENDANCE_MODE_CONFIGURED': True,
        'DATABASE_CONFIGURED': False, 'SQLALCHEMY_DATABASE_URI': f'sqlite:///{(instance / "app.sqlite3").as_posix()}',
        'SQLALCHEMY_ENGINE_OPTIONS': {}, 'SECRET_KEY': None,
        'EOFFICE_KEY_FILE': str(instance / 'biometric_keys.json'),
        'BIOMETRIC_KEY_FILE': str(instance / 'biometric_keys.json'),
        'KIOSK_LOCK_FILE': str(instance / 'kiosk.lock'),
        'KIOSK_STATE_FILE': str(instance / 'kiosk_state.json'),
        'BACKUP_DIR': str(root / 'backups'), 'MODEL_DIR': str(instance / 'models'),
        'UPDATE_INSTALL_ROOT': str(root), 'LIVENESS_MODE': 'passive',
        'KIOSK_PUBLIC_ORIGIN': '', 'KIOSK_TRUSTED_PROXY_IP': '',
    })
    if app.config['ATTENDANCE_MODE'] != 'eoffice':
        raise UpdateError('Installer hanya mendukung kios eOffice.')
    for name in ('EOFFICE_KEY_FILE', 'BIOMETRIC_KEY_FILE', 'MODEL_DIR', 'EOFFICE_AURAFACE_PATH', 'EOFFICE_PERIOCULAR_PATH'):
        value = app.config.get(name)
        if value and not Path(value).resolve().is_relative_to(instance):
            raise UpdateError('Paket aplikasi memerlukan data dan kunci di penyimpanan privat perangkat.')
    migrate_instance(instance)
    return app


def self_check(root, existing=False, fixtures=None):
    public_key()
    if existing and not (Path(root) / 'instance/app.sqlite3').is_file():
        raise UpdateError('Database perangkat tidak tersedia.')
    app = packaged_app(root)
    try:
        with app.app_context():
            from extensions import db
            from sqlalchemy import text
            assert db.session.execute(text('PRAGMA integrity_check')).scalar() == 'ok'
            assert app.jinja_env.get_template('settings_updates.html')
            assert (Path(app.static_folder) / 'js/app_updates.js').is_file()
            from services.biometric_service import load_keyring
            active, keys = load_keyring(app.config['EOFFICE_KEY_FILE'])
            from integration.store import IntegrationStore
            for name, test_mode in [('eoffice.sqlite3', False), ('eoffice-test.sqlite3', True)]:
                path = Path(root) / 'instance' / name
                if path.exists():
                    store = IntegrationStore(path, keys[active], test_mode=test_mode, initialize=False)
                    try:
                        store.load_snapshot()
                        store.pending()
                    finally:
                        store.close()
        for module in ('cv2', 'numpy', 'onnxruntime', 'ai_edge_litert.interpreter'):
            importlib.import_module(module)
        if os.name == 'nt':
            for module in ('pygrabber.dshow_graph', 'comtypes'):
                importlib.import_module(module)
        if fixtures is None and getattr(sys, 'frozen', False):
            fixtures = Path(sys._MEIPASS) / 'inference'
        if fixtures:
            from maintenance.inference_check import check_inference
            check_inference(fixtures)
    finally:
        with app.app_context():
            from extensions import db
            db.session.remove()
            db.engine.dispose()


def launch(root, transaction=None):
    from maintenance.windows_launcher import run
    from services.app_updates import UpdateManager
    root = Path(root).resolve()
    def factory():
        if active_version(root) != VERSION:
            raise UpdateError('Executable bukan versi aktif.')
        if transaction:
            state = read_json(root / 'updates/journal.json', {})
            if state.get('transaction') != transaction or state.get('stage') != 'restarting':
                raise UpdateError('Percobaan startup tidak diizinkan.')
        else:
            startup_allowed(root)
        app = packaged_app(root)
        stop = app.extensions['launcher_stop'] = app_stop if os.name != 'nt' else Event()
        app.extensions['app_updates'] = UpdateManager(app, root, public_key(), stop)
        if transaction:
            from maintenance.windows_update import activate
            app.extensions['before_capture'] = lambda: activate(root, transaction)
        return app
    previous_handlers = {}
    if os.name != 'nt':
        def stop_service(signum, frame):
            # Keep ownership until HTTP, capture and synchronization have drained.
            app_stop.set()
        app_stop = Event()
        for signum in (signal.SIGTERM, signal.SIGHUP):
            previous_handlers[signum] = signal.signal(signum, stop_service)
    try:
        run(instance_path=root / 'instance', app_factory=factory)
    finally:
        for signum, handler in previous_handlers.items():
            signal.signal(signum, handler)


def kiosk(root, once=False):
    root = Path(root).resolve()
    stop = root / 'instance/launcher.stop'
    if once:
        startup_allowed(root)
        app = packaged_app(root)
        with app.app_context():
            app.cli.main(args=['kiosk-run', '--no-preview'], standalone_mode=False)
        return
    while not stop.exists():
        subprocess.run([sys.executable, '--root', str(root), 'kiosk-once'], cwd=root, check=False)
        for _ in range(100):
            if stop.exists():
                return
            time.sleep(.1)


def main(argv=None):
    if argv is None and sys.platform == 'darwin' and getattr(sys, 'frozen', False) and (
            not sys.argv[1:] or sys.argv[1].startswith('-psn_')):
        from maintenance.posix_install import finder_launch
        finder_launch()
        return 0
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path)
    commands = parser.add_subparsers(dest='mode', required=True)
    launch_command = commands.add_parser('launch')
    launch_command.add_argument('--transaction')
    for command in ('kiosk', 'kiosk-once', 'initialize', 'recover', 'reset-admin-password', 'install', 'open', 'recover-active', 'reset-admin-password-active'):
        commands.add_parser(command)
    check = commands.add_parser('self-check')
    check.add_argument('--existing', action='store_true')
    check.add_argument('--fixtures', type=Path)
    update = commands.add_parser('apply-update')
    update.add_argument('--transaction', required=True)
    args = parser.parse_args(argv)
    if args.root is None and args.mode not in ('install', 'open', 'recover-active', 'reset-admin-password-active'):
        parser.error('--root diperlukan untuk operasi ini.')
    try:
        platforms.platform_id()
        if args.mode in ('install', 'open', 'recover-active', 'reset-admin-password-active'):
            from maintenance.posix_install import install, open_active
            root = args.root or platforms.default_root()
            if args.mode == 'install':
                install(root)
            else:
                mode = args.mode.removesuffix('-active') if args.mode.endswith('-active') else 'launch'
                open_active(root, mode)
        elif args.mode == 'launch':
            launch(args.root, args.transaction)
        elif args.mode in ('kiosk', 'kiosk-once'):
            kiosk(args.root, once=args.mode == 'kiosk-once')
        elif args.mode == 'self-check':
            self_check(args.root, args.existing, args.fixtures)
        elif args.mode == 'initialize':
            initialize(args.root, VERSION)
        elif args.mode == 'apply-update':
            from maintenance.windows_update import apply_update
            apply_update(args.root, args.transaction, public_key())
        elif args.mode == 'recover':
            from maintenance.windows_update import recover
            recover(args.root)
        elif args.mode == 'reset-admin-password':
            from kiosk.lock import SingleInstanceLock
            with SingleInstanceLock(args.root / 'updates/update.lock'), SingleInstanceLock(args.root / 'instance/launcher.lock'):
                startup_allowed(args.root)
                app = packaged_app(args.root)
                with app.app_context():
                    from extensions import db
                    try:
                        app.cli.main(args=['reset-admin-password'], standalone_mode=False)
                    finally:
                        db.session.remove()
                        db.engine.dispose()
    except KeyboardInterrupt:
        return 0
    except Exception:
        print('Operasi belum selesai. Data perangkat dipertahankan. Periksa koneksi, ruang disk, '
              'izin folder, aplikasi lain, atau pemulihan lokal sesuai panduan distribusi.', file=sys.stderr)
        if args.mode == 'launch' and sys.stdin.isatty():
            input('Tekan Enter untuk menutup...')
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
