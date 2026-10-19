from threading import Event

import pytest

from kiosk.lock import SingleInstanceLock
from maintenance.distribution import atomic_json
from services.app_updates import UpdateManager
from services.update_package import UpdateError
from app_version import VERSION
from test_update_package import signed


def manager(app, tmp_path):
    root = tmp_path / 'distribution'
    (root / 'updates').mkdir(parents=True)
    atomic_json(root / 'current.json', {'version': '1.0.0'})
    return UpdateManager(app, root, b'k' * 32, Event())


def test_signed_offer_and_up_to_date_check(app, tmp_path, monkeypatch):
    import services.app_updates as updates
    updater = manager(app, tmp_path)
    raw, signature, key = signed()
    updater.public_key = key
    monkeypatch.setattr(updates, 'fetch_metadata', lambda url, *args: signature if url.endswith('.sig') else raw)
    updater._check()
    status = updater.status()
    assert status['stage'] == 'available'
    assert status['offered_version'] == '1.1.0'
    assert 'url' not in status and 'key' not in status
    raw, signature, key = signed(version='1.0.0')
    updater.public_key = key
    updater._check()
    assert updater.status()['stage'] == 'idle'


def test_other_process_lock_never_overwrites_transaction(app, tmp_path):
    updater = manager(app, tmp_path)
    updater.stage('applying', recovery_required=True)
    before = (updater.root / 'updates/journal.json').read_bytes()
    with SingleInstanceLock(updater.root / 'updates/update.lock'):
        updater._busy.acquire()
        updater._job('check')
    assert (updater.root / 'updates/journal.json').read_bytes() == before


def test_network_failure_does_not_stop_camera_or_expose_exception(app, tmp_path, monkeypatch):
    import services.app_updates as updates
    updater = manager(app, tmp_path)
    def offline(*args):
        raise OSError('SECRET DEVICE TOKEN')
    monkeypatch.setattr(updates, 'fetch_metadata', offline)
    updater._busy.acquire()
    updater._job('check')
    assert updater.status()['stage'] == 'failed'
    assert not updater.stop_event.is_set()
    assert 'SECRET' not in str(updater.status())


def test_one_job_and_interrupted_transaction_rejected(app, tmp_path):
    updater = manager(app, tmp_path)
    updater._busy.acquire()
    with pytest.raises(UpdateError):
        updater.start('check')
    updater._busy.release()
    updater.stage('failed', recovery_required=True)
    with pytest.raises(UpdateError):
        updater.start('install')


@pytest.fixture
def update_app(tmp_path):
    from test_setup import installation
    from extensions import db
    from models import SetupState, User
    app = installation(tmp_path / 'instance')
    with app.app_context():
        state = db.session.get(SetupState, 1)
        state.complete = True
        user = User(name='Admin', username='admin', role='admin', is_active=True)
        user.set_password('PasswordUji12345')
        db.session.add(user)
        db.session.commit()
    app.config['SETUP_REQUIRED'] = False
    app.extensions['app_updates'] = manager(app, tmp_path)
    return app


def test_update_routes_require_admin_and_csrf(update_app, monkeypatch):
    from test_setup import csrf
    from extensions import db
    from models import User
    client = update_app.test_client()
    assert client.get('/setup/updates').status_code == 302
    client.post('/login', data={'username': 'admin', 'password': 'PasswordUji12345',
                               'csrf_token': csrf(client, '/login')})
    assert client.get('/setup/updates').status_code == 200
    assert client.get('/setup/updates/status').json['version'] == VERSION
    assert client.post('/setup/updates/install').status_code == 302
    calls = []
    monkeypatch.setattr(update_app.extensions['app_updates'], 'start', calls.append)
    response = client.post('/setup/updates/check', data={'csrf_token': csrf(client, '/setup/updates'),
                                                       'url': 'https://evil.example', 'command': 'bad'})
    assert response.status_code == 202
    assert calls == ['check']
    with update_app.app_context():
        db.session.get(User, 1).role = 'operator'
        db.session.commit()
    assert client.get('/setup/updates/status').status_code == 403


def test_update_guard_blocks_mutation_during_install(update_app):
    updater = update_app.extensions['app_updates']
    updater.stage('stopping', recovery_required=True)
    response = update_app.test_client().post('/setup/scope')
    assert response.status_code == 503
    assert response.json['error'] == 'update_busy'


def test_insufficient_disk_does_not_request_shutdown(app, tmp_path, monkeypatch):
    from types import SimpleNamespace
    import services.app_updates as updates
    updater = manager(app, tmp_path)
    raw, signature, key = signed()
    updater.public_key = key
    (updater.root / 'updates/offer.json').write_bytes(raw)
    (updater.root / 'updates/offer.sig').write_bytes(signature)
    monkeypatch.setattr(updates.shutil, 'disk_usage', lambda path: SimpleNamespace(free=0))
    updater._busy.acquire()
    updater._job('install')
    assert updater.status()['stage'] == 'failed'
    assert not updater.status()['recovery_required']
    assert not updater.stop_event.is_set()
    assert 'penyimpanan' in updater.status()['message']
