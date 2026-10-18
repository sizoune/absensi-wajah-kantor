"""Single-process container entry point; camera ownership stays in this process."""
import logging
from pathlib import Path
import signal

from services.setup_service import load_secret


def create_container_app(instance_dir=None):
    from app import create_app
    from extensions import db
    from models import AuditLog, User

    instance_dir = Path(instance_dir or Path(__file__).resolve().parents[1] / 'instance').resolve()
    instance_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    secret_path = instance_dir / 'secret_key'
    if not secret_path.exists() and any((instance_dir / name).exists()
                                        for name in ('app.sqlite3', 'eoffice.sqlite3', 'eoffice-test.sqlite3')):
        raise RuntimeError(f'{secret_path}: secret_key hilang; pulihkan cadangan, jangan buat ulang.')
    app = create_app('production', instance_path=str(instance_dir), overrides={
        'SECRET_KEY': load_secret(secret_path),
        'CAMERA_SOURCE': 'browser',
        'SQLALCHEMY_DATABASE_URI': f'sqlite:///{instance_dir}/app.sqlite3',
        'SQLALCHEMY_ENGINE_OPTIONS': {},
        'KIOSK_STATE_FILE': str(instance_dir / 'kiosk_state.json'),
        'KIOSK_LOCK_FILE': str(instance_dir / 'kiosk.lock'),
    })
    app.instance_path = str(instance_dir)
    with app.app_context():
        # Only web metadata is needed; no standalone attendance schema or migration.
        db.metadata.create_all(db.engine, tables=[User.__table__, AuditLog.__table__])
    return app


def create_http_server(app):
    from waitress import create_server

    return create_server(app, host='0.0.0.0', port=5000, threads=4,
                         max_request_body_size=524288, inbuf_overflow=1048576,
                         max_request_header_size=16384, connection_limit=32,
                         channel_timeout=15, cleanup_interval=5)


def main():
    logging.basicConfig(level=logging.INFO)
    app = create_container_app()
    server = None

    def stop(*_):
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        if app.config['ATTENDANCE_MODE'] == 'eoffice' and not app.config.get('SETUP_REQUIRED', False):
            app.extensions['browser_camera'].start_service()
        server = create_http_server(app)
        logging.info('Browser kiosk listening on port 5000; mode=%s', app.config['ATTENDANCE_MODE'])
        server.run()
    finally:
        try:
            app.extensions['browser_camera'].close()
            logging.info('Browser camera closed.')
        finally:
            if server is not None:
                server.close()


if __name__ == '__main__':
    main()
