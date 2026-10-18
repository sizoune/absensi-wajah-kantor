"""Local eOffice admin sessions use device settings with metadata-only storage."""
import re

from sqlalchemy import inspect

from extensions import db
from test_setup import csrf
from test_setup_organization import connect_and_select, wizard
from test_setup_readiness import finish


def login(client, next_url=''):
    return client.post('/login' + next_url, data={'csrf_token': csrf(client, '/login'),
        'username': 'operator', 'password': 'PasswordUji123'}, follow_redirects=True)


def test_completed_eoffice_login_uses_device_settings_without_attendance_tables(wizard):
    app, setup, _, _ = wizard
    connect_and_select(setup)
    assert finish(setup).json['complete'] is True
    with app.app_context():
        assert 'attendance_sessions' not in inspect(db.engine).get_table_names()
    client = app.test_client()
    response = login(client)
    assert response.status_code == 200
    assert response.request.path == '/setup/camera/manage'
    page = response.get_data(as_text=True)
    navigation = re.search(r'<nav[^>]+aria-label="Menu admin"[^>]*>(.*?)</nav>', page, re.S).group(1)
    assert '/setup/camera/manage' in navigation
    assert '/setup/scope/manage' in navigation
    assert '/setup/models/manage' in navigation
    assert not any(f'href="{path}' in navigation for path in (
        '/admin', '/attendance', '/reports', '/employees', '/schedules'))
    assert client.get('/admin', follow_redirects=True).status_code == 200
    # Existing safe internal next destinations are retained.
    other = app.test_client()
    assert login(other, '?next=/setup/models/manage').request.path == '/setup/models/manage'


def test_unfinished_eoffice_login_stays_in_setup(wizard):
    app, _, _, _ = wizard
    response = login(app.test_client())
    assert response.status_code == 200
    assert response.request.path == '/setup'
    assert 'Kode setup' in response.get_data(as_text=True)
