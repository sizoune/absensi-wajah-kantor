"""Persistent first setup, separate from operational integration and its queue."""
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
from urllib.parse import urlsplit

import click
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from flask import current_app, jsonify, redirect, request, url_for
from sqlalchemy import inspect
from sqlalchemy.engine import make_url

from config import validate_attendance_config, validate_bootstrap_config
from extensions import db
from models import AuditLog, SetupState, User
from services import ValidationError, biometric_service
from services import audit_service as audit
from services.auth_service import admin_values, recover_admin_password
from services.cli_errors import friendly_db_errors

STEPS = ('storage', 'admin', 'organization', 'models', 'snapshot', 'camera')


def _settings_key():
    secret = current_app.secret_key
    return hashlib.sha256(secret.encode('utf-8') if isinstance(secret, str) else secret).digest()


def load_settings():
    state = db.session.get(SetupState, 1)
    if state is None or state.settings is None:
        return {}
    try:
        nonce, encrypted = state.settings[:12], state.settings[12:]
        values = json.loads(AESGCM(_settings_key()).decrypt(nonce, encrypted, b'first-setup-settings-v1'))
        if not isinstance(values, dict):
            raise ValueError
        return values
    except (InvalidTag, ValueError, TypeError) as exc:
        raise RuntimeError('Konfigurasi setup tidak dapat dibaca; pulihkan rahasia dan cadangan asli.') from exc


def save_settings(values):
    state = db.session.get(SetupState, 1)
    db.session.execute(db.update(SetupState).where(SetupState.id == 1).values(complete=SetupState.complete))
    db.session.refresh(state)
    saved = {**load_settings(), **values}
    nonce = secrets.token_bytes(12)
    state.settings = nonce + AESGCM(_settings_key()).encrypt(
        nonce, json.dumps(saved).encode('utf-8'), b'first-setup-settings-v1')
    db.session.commit()
    current_app.config.update(values)


def record_progress(step, complete=True):
    if step not in STEPS or type(complete) is not bool:
        raise ValueError('Langkah setup tidak valid.')
    state = db.session.get(SetupState, 1)
    db.session.execute(db.update(SetupState).where(SetupState.id == 1).values(complete=SetupState.complete))
    db.session.refresh(state)
    state.progress = {**state.progress, step: complete}
    db.session.commit()


def finish_setup():
    state = db.session.get(SetupState, 1)
    state.complete, state.code_hash, state.session_hash = True, None, None
    db.session.commit()
    current_app.config['SETUP_REQUIRED'] = False


def load_secret(path):
    """Create a private session secret once; a damaged secret needs recovery."""
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        pass
    else:
        with os.fdopen(descriptor, 'w', encoding='ascii') as file:
            file.write(secrets.token_hex(32) + '\n')
            file.flush()
            os.fsync(file.fileno())
        if os.name != 'nt':
            directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    try:
        value = path.read_text(encoding='ascii').strip()
    except UnicodeError as exc:
        raise RuntimeError('secret_key rusak; pulihkan cadangan, jangan buat ulang.') from exc
    if not re.fullmatch(r'[0-9a-f]{64}', value):
        raise RuntimeError('secret_key rusak; pulihkan cadangan, jangan buat ulang.')
    return value


def prepare_storage(app, overrides):
    directory = Path(app.instance_path)
    existing = any((directory / name).exists() for name in
                   ('app.sqlite3', 'eoffice.sqlite3', 'eoffice-test.sqlite3'))
    explicit_mode = app.config.get('ATTENDANCE_MODE_CONFIGURED') or 'ATTENDANCE_MODE' in overrides
    resume = not explicit_mode and (directory / '.first-setup').exists()
    fresh = resume or not explicit_mode and not app.config.get('DATABASE_CONFIGURED') and not existing
    enabled = app.config.get('FIRST_SETUP_ENABLED') and (app.config['ATTENDANCE_MODE'] == 'eoffice' or fresh)
    app.config['SETUP_ENABLED'] = bool(enabled)
    if not enabled:
        validate_attendance_config(app.config)
        return
    if fresh:
        app.config.update(ATTENDANCE_MODE='eoffice', LIVENESS_MODE='passive')
    validate_bootstrap_config(app.config)
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    (directory / '.first-setup').touch(mode=0o600, exist_ok=True)
    if not app.config.get('DATABASE_CONFIGURED') and 'SQLALCHEMY_DATABASE_URI' not in overrides:
        app.config.update(SQLALCHEMY_DATABASE_URI=f'sqlite:///{directory}/app.sqlite3',
                          SQLALCHEMY_ENGINE_OPTIONS={})
    database = make_url(app.config['SQLALCHEMY_DATABASE_URI'])
    if database.drivername == 'sqlite' and database.database not in (None, ':memory:'):
        existing = existing or Path(database.database).exists()
    app.config['SETUP_NEW_STORAGE'] = not existing
    if not app.config.get('SECRET_KEY'):
        secret_path = directory / 'secret_key'
        if existing and not secret_path.exists():
            raise RuntimeError('secret_key hilang; pulihkan cadangan, jangan buat ulang.')
        app.config['SECRET_KEY'] = load_secret(secret_path)
    key_path = app.config.get('EOFFICE_KEY_FILE') or app.config.get('BIOMETRIC_KEY_FILE')
    app.config['EOFFICE_KEY_FILE'] = key_path or str(directory / 'biometric_keys.json')
    if not app.config.get('BIOMETRIC_KEY_FILE'):
        app.config['BIOMETRIC_KEY_FILE'] = app.config['EOFFICE_KEY_FILE']


def initialize_setup(overrides):
    app = current_app
    if not app.config.get('SETUP_ENABLED'):
        app.config['SETUP_REQUIRED'] = False
        return
    existing_tables = inspect(db.engine).get_table_names()
    new_storage = app.config['SETUP_NEW_STORAGE'] and not existing_tables
    key = Path(app.config['EOFFICE_KEY_FILE'])
    if new_storage and not key.exists():
        biometric_service.generate_keyring()
    db.metadata.create_all(db.engine, tables=[User.__table__, AuditLog.__table__, SetupState.__table__])
    state = db.session.get(SetupState, 1)
    if state is None:
        if new_storage:
            app.config['LIVENESS_MODE'] = 'passive'
        try:
            validate_attendance_config(app.config)
            complete = db.session.execute(db.select(User.id).where(User.role == 'admin')).first() is not None
        except ValueError:
            complete = False
        state = SetupState(id=1, complete=complete, progress={})
        db.session.add(state)
        db.session.commit()
        save_settings({key: value for key, value in app.config.items()
                       if key.startswith(('EOFFICE_', 'CAMERA_'))
                       or key in ('ATTENDANCE_MODE', 'LIVENESS_MODE', 'MODEL_DIR', 'BIOMETRIC_KEY_FILE')})
    else:
        saved = load_settings()
        app.config.update(saved)
        app.config.update({key: value for key, value in overrides.items() if key in saved})
        validate_bootstrap_config(app.config)
    app.config['SETUP_REQUIRED'] = not state.complete


def setup_required():
    if not current_app.config.get('SETUP_ENABLED'):
        return False
    return not db.session.get(SetupState, 1).complete


def _digest(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def issue_setup_code():
    if not setup_required():
        raise ValueError('Setup perangkat sudah selesai atau tidak diperlukan.')
    code = secrets.token_urlsafe(32)
    result = db.session.execute(db.update(SetupState).where(SetupState.id == 1, SetupState.complete.is_(False))
                                .values(code_hash=_digest(code), session_hash=None))
    db.session.commit()
    if result.rowcount != 1:
        raise ValueError('Setup perangkat sudah selesai atau tidak diperlukan.')
    return code


def redeem_setup_code(code):
    if not isinstance(code, str) or not 24 <= len(code) <= 128:
        return None
    grant = secrets.token_urlsafe(32)
    result = db.session.execute(db.update(SetupState).where(
        SetupState.id == 1, SetupState.complete.is_(False), SetupState.code_hash == _digest(code)
    ).values(code_hash=None, session_hash=_digest(grant)))
    db.session.commit()
    return grant if result.rowcount == 1 else None


def valid_setup_grant(grant):
    if not current_app.config.get('SETUP_ENABLED') or not isinstance(grant, str):
        return False
    state = db.session.get(SetupState, 1)
    return (not state.complete and state.session_hash is not None
            and secrets.compare_digest(_digest(grant), state.session_hash))


def create_first_admin(username, password):
    # Taking a write lock on the singleton serializes first-admin retries.
    db.session.execute(db.update(SetupState).where(SetupState.id == 1, SetupState.complete.is_(False))
                       .values(complete=False))
    if db.session.execute(db.select(User.id).where(User.role == 'admin')).first() is not None:
        db.session.rollback()
        return None
    values = admin_values(username, username, password)
    user = User(**values, role='admin', is_active=True)
    user.set_password(password)
    db.session.add(user)
    db.session.flush()
    audit.log_event(audit.ADMIN_CREATED, entity_type='user', entity_id=user.id,
                    description='Admin pertama dibuat melalui setup.', commit=False)
    db.session.commit()
    return user


def register_setup_commands(app):
    @app.cli.command('setup-code')
    def setup_code():
        """Terbitkan kode setup sekali pakai; kode lama dan sesi lama dibatalkan."""
        try:
            click.echo(issue_setup_code())
        except ValueError as exc:
            raise click.ClickException(str(exc)) from exc

    @app.cli.command('reset-admin-password')
    @click.option('--username', prompt='Username admin')
    @friendly_db_errors
    def reset_admin_password(username):
        """Pulihkan password admin aktif dari terminal lokal."""
        password = click.prompt('Password baru (minimal 10 karakter)', hide_input=True,
                                confirmation_prompt='Ulangi password baru')
        try:
            user = recover_admin_password(username, password)
        except ValidationError as exc:
            raise click.ClickException(next(iter(exc.errors.values()))) from exc
        click.echo(f"Password admin '{user.username}' berhasil dipulihkan.")


def setup_status():
    state = db.session.get(SetupState, 1)
    steps = {step: bool(state.progress.get(step)) for step in STEPS}
    try:
        biometric_service.load_keyring(current_app.config['EOFFICE_KEY_FILE'])
        steps['storage'] = True
    except biometric_service.BiometricKeyError:
        steps['storage'] = False
    steps['admin'] = db.session.execute(db.select(User.id).where(User.role == 'admin')).first() is not None
    base_url = current_app.config.get('EOFFICE_BASE_URL', '')
    try:
        url = urlsplit(base_url)
        if (url.scheme != 'https' or not url.hostname or url.username is not None
                or url.password is not None or url.query or url.fragment or any(c.isspace() for c in base_url)):
            base_url = ''
        url.port
    except (ValueError, TypeError):
        base_url = ''
    return {'complete': state.complete, 'steps': steps,
            'configuration': {'base_url': base_url},
            'pending': [step for step, complete in steps.items() if not complete]}


def hold_operational_requests():
    if not setup_required():
        return
    if request.blueprint == 'setup' or request.endpoint in ('auth.login', 'auth.logout', 'health', 'static'):
        return
    if request.method != 'GET' or request.path.startswith('/api/'):
        return jsonify(ok=False, error='setup_required'), 409
    return redirect(url_for('setup.index'))
