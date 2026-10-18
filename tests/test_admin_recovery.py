"""Local admin recovery through the CLI and normal HTTP authentication."""
import pytest

from app import create_app
from conftest import ADMIN_PASSWORD, ADMIN_USERNAME, login
from extensions import db
from integration.store import IntegrationStore
from test_eoffice_store import terminal
from test_setup import csrf, exchange, installation, legacy_installation, local_code


NEW_PASSWORD = 'PasswordPulih123'


def recover(app, username=ADMIN_USERNAME, password=NEW_PASSWORD, confirmation=None):
    return app.test_cli_runner().invoke(
        args=['reset-admin-password', '--username', username],
        input=f'{password}\n{password if confirmation is None else confirmation}\n',
    )


def test_local_recovery_replaces_password_without_disclosing_it(app, admin_user, caplog):
    result = recover(app)
    assert result.exit_code == 0, result.output
    assert NEW_PASSWORD not in result.output + caplog.text
    assert ADMIN_PASSWORD not in result.output + caplog.text
    assert login(app.test_client(), password=ADMIN_PASSWORD).status_code == 401
    client = app.test_client()
    assert client.get('/admin').status_code == 302
    response = login(client, password=NEW_PASSWORD, follow_redirects=True)
    assert response.status_code == 200
    assert 'Halo, Admin Uji' in response.get_data(as_text=True)


def test_recovery_rejects_short_password_and_retains_current_login(app, admin_user, caplog):
    result = recover(app, password='Sembilan9')
    assert result.exit_code != 0
    assert 'Error: Password minimal 10 karakter.' in result.output
    assert 'Sembilan9' not in result.output + caplog.text
    assert login(app.test_client(), password='Sembilan9').status_code == 401
    assert login(app.test_client()).status_code == 302


@pytest.mark.parametrize(('username', 'role', 'active'), [
    ('unknown_admin', 'admin', True),
    ('bad username', 'admin', True),
    ('', 'admin', True),
    (ADMIN_USERNAME, 'employee', True),
    (ADMIN_USERNAME, 'admin', False),
])
def test_recovery_rejects_invalid_or_inactive_admin_without_changing_access(
        app, admin_user, username, role, active):
    admin_user.role = role
    admin_user.is_active = active
    db.session.commit()
    result = recover(app, username=username)
    assert result.exit_code != 0
    assert 'Akun admin aktif tidak ditemukan.' in result.output
    assert NEW_PASSWORD not in result.output
    assert login(app.test_client(), password=NEW_PASSWORD).status_code == 401
    assert login(app.test_client()).status_code == (302 if active else 401)


def test_legacy_admin_recovery_keeps_existing_username_and_name(app, admin_user):
    admin_user.username = 'a'
    db.session.commit()
    result = recover(app, username=' a ')
    assert result.exit_code == 0, result.output
    assert login(app.test_client(), username='a').status_code == 401
    response = login(app.test_client(), username='a', password=NEW_PASSWORD,
                     follow_redirects=True)
    assert response.status_code == 200
    assert 'Halo, Admin Uji' in response.get_data(as_text=True)


@pytest.mark.parametrize('prompt_input', ['', '\n\n', f'{NEW_PASSWORD}\nDifferentPassword123\n'])
def test_cancelled_empty_or_mismatched_prompt_keeps_existing_password(
        app, admin_user, prompt_input, caplog):
    result = app.test_cli_runner().invoke(
        args=['reset-admin-password', '--username', ADMIN_USERNAME], input=prompt_input)
    assert result.exit_code != 0
    assert NEW_PASSWORD not in result.output + caplog.text
    assert 'DifferentPassword123' not in result.output + caplog.text
    assert login(app.test_client(), password=NEW_PASSWORD).status_code == 401
    assert login(app.test_client()).status_code == 302


def test_recovery_rejects_password_arguments_and_anonymous_http_reset(app, admin_user):
    result = app.test_cli_runner().invoke(args=[
        'reset-admin-password', '--username', ADMIN_USERNAME, '--password', NEW_PASSWORD])
    assert result.exit_code != 0
    assert 'No such option' in result.output
    assert '--password' in result.output
    assert NEW_PASSWORD not in result.output
    client = app.test_client()
    assert client.post('/reset-admin-password', data={
        'username': ADMIN_USERNAME, 'password': NEW_PASSWORD}).status_code == 404
    assert login(client, password=NEW_PASSWORD).status_code == 401
    assert client.get('/admin').status_code == 302
    assert login(client).status_code == 302


def test_recovery_prompts_for_username_and_accepts_minimum_password(app, admin_user):
    password = 'Pulih12345'
    result = app.test_cli_runner().invoke(args=['reset-admin-password'],
        input=f'{ADMIN_USERNAME}\n{password}\n{password}\n')
    assert result.exit_code == 0, result.output
    assert password not in result.output
    assert login(app.test_client()).status_code == 401
    assert login(app.test_client(), password=password).status_code == 302


@pytest.mark.parametrize('complete', [False, True])
def test_recovery_preserves_setup_and_device_data_across_restart(tmp_path, complete, caplog):
    key_file, key, body, snapshot = legacy_installation(tmp_path)
    queue_file = tmp_path / 'eoffice.sqlite3'
    history = terminal()
    history_id = '85f42b66-6a77-4c56-9efc-4231ff6eab43'
    history['data']['event_id'] = history_id
    store = IntegrationStore(queue_file, key)
    store.enqueue(b'{"event_id":"85f42b66-6a77-4c56-9efc-4231ff6eab43"}')
    store.finish(history_id, history)
    store.close()
    models = {}
    if complete:
        for name in ('EOFFICE_AURAFACE_PATH', 'EOFFICE_PERIOCULAR_PATH'):
            path = tmp_path / name
            path.write_bytes(b'legacy-model')
            models[name] = str(path)
    app = installation(tmp_path, EOFFICE_BASE_URL='https://old.example',
                       EOFFICE_DEVICE_TOKEN='retained-device-token', **models)
    setup_client = app.test_client()
    if complete:
        assert setup_client.get('/setup').status_code == 404
    else:
        exchange(setup_client, local_code(app))
        previous_status = setup_client.get('/setup/status').json
    original_files = {path: path.read_bytes() for path in
                      (key_file, tmp_path / 'secret_key', queue_file)}

    result = recover(app, username='legacy_admin')
    assert result.exit_code == 0, result.output
    if not complete:
        assert setup_client.get('/setup/status').json == previous_status
    restarted = create_app('testing', instance_path=str(tmp_path), overrides={
        'FIRST_SETUP_ENABLED': True, 'ATTENDANCE_MODE': 'eoffice',
        'SECRET_KEY': None, 'SQLALCHEMY_DATABASE_URI': f'sqlite:///{tmp_path}/app.sqlite3',
        'SQLALCHEMY_ENGINE_OPTIONS': {}, 'WTF_CSRF_ENABLED': True,
    })
    assert restarted.config['EOFFICE_BASE_URL'] == 'https://old.example'
    assert restarted.config['EOFFICE_DEVICE_TOKEN'] == 'retained-device-token'
    client = restarted.test_client()
    assert client.get('/admin').status_code == 302
    assert client.post('/login', data={'csrf_token': csrf(client, '/login'),
        'username': 'legacy_admin', 'password': 'PasswordLama123'}).status_code == 401
    response = client.post('/login', data={'csrf_token': csrf(client, '/login'),
        'username': 'legacy_admin', 'password': NEW_PASSWORD})
    assert response.status_code == 302
    if complete:
        assert client.get('/setup').status_code == 404
        assert restarted.test_cli_runner().invoke(args=['setup-code']).exit_code != 0
        dashboard = client.get('/admin').get_data(as_text=True)
        assert 'Halo, Admin Lama' in dashboard
        assert 'Admin dibuat melalui CLI: legacy_admin' in dashboard
        assert 'admin_password_reset' in dashboard
        assert NEW_PASSWORD not in dashboard
    else:
        assert client.get('/admin').headers['Location'].endswith('/setup')
        assert setup_client.get('/setup/status').json == previous_status
    assert NEW_PASSWORD not in result.output + caplog.text
    assert NEW_PASSWORD.encode() not in (tmp_path / 'app.sqlite3').read_bytes()
    assert {path: path.read_bytes() for path in original_files} == original_files
    store = IntegrationStore(queue_file, key)
    assert store.pending()[0].body == body
    assert store.pending()[0].event_id == '85f42b66-6a77-4c56-9efc-4231ff6eab42'
    assert len(store.pending()) == 1
    assert store.load_snapshot() == snapshot
    assert store.terminal(history_id) == history
    store.close()
