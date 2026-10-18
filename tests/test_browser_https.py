"""Browser transport accepts exact configured HTTPS origins without forwarding trust shortcuts."""
import json
from pathlib import Path
import re
import runpy
from threading import Event, Thread
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from test_browser_camera import browser_app

ORIGIN = 'https://kiosk.office.test'


def auth(client, origin):
    page = client.get('/kiosk/', base_url=origin)
    token = re.search(r'data-csrf-token="([^"]+)"', page.get_data(as_text=True)).group(1)
    return {'Origin': origin, 'Referer': origin + '/kiosk/', 'X-CSRFToken': token}


def test_configured_https_origin_supports_camera_and_secure_session_cookie(browser_app):
    browser_app.config.update(KIOSK_PUBLIC_ORIGIN=ORIGIN, KIOSK_TRUSTED_PROXY_IP='127.0.0.1')
    client = browser_app.test_client()
    page = client.get('/kiosk/', base_url=ORIGIN)
    assert 'Secure;' in page.headers['Set-Cookie']
    response = client.post('/kiosk/camera/start', base_url=ORIGIN, headers=auth(client, ORIGIN))
    assert response.status_code == 200
    assert response.json['mode'] == 'preview'


def test_localhost_keeps_http_setup_cookie_and_forwarding_cannot_enable_http_lan(browser_app):
    browser_app.config.update(KIOSK_PUBLIC_ORIGIN=ORIGIN, KIOSK_TRUSTED_PROXY_IP='127.0.0.1')
    local = browser_app.test_client()
    assert 'Secure;' not in local.get('/kiosk/').headers['Set-Cookie']
    assert local.post('/kiosk/camera/start', headers=auth(local, 'http://localhost')).status_code == 200
    other = browser_app.test_client()
    response = other.get('/kiosk/', base_url='http://kiosk.office.test', headers={
        'X-Forwarded-Proto': 'https', 'X-Forwarded-Host': 'kiosk.office.test', 'X-Forwarded-For': '127.0.0.1'})
    assert response.status_code == 403


def test_cookie_security_is_per_request_and_preserves_explicit_secure_policy(browser_app):
    browser_app.config.update(KIOSK_PUBLIC_ORIGIN=ORIGIN, KIOSK_TRUSTED_PROXY_IP='127.0.0.1')
    for origin, secure in [(ORIGIN, True), ('http://localhost', False), (ORIGIN, True)]:
        cookie = browser_app.test_client().get('/kiosk/', base_url=origin).headers['Set-Cookie']
        assert ('Secure;' in cookie) is secure
    assert browser_app.config['SESSION_COOKIE_SECURE'] is False
    browser_app.config['SESSION_COOKIE_SECURE'] = True
    cookie = browser_app.test_client().get('/kiosk/', base_url='http://localhost').headers['Set-Cookie']
    assert 'Secure;' in cookie


def test_https_setup_keeps_same_origin_referrer_for_strict_csrf(tmp_path):
    from test_setup import installation, local_code
    app = installation(tmp_path, KIOSK_PUBLIC_ORIGIN=ORIGIN, KIOSK_TRUSTED_PROXY_IP='127.0.0.1')
    client = app.test_client()
    page = client.get('/setup', base_url=ORIGIN)
    assert page.headers['Referrer-Policy'] == 'same-origin'
    token = re.search(r'name="csrf_token" value="([^"]+)"', page.get_data(as_text=True)).group(1)
    response = client.post('/setup', base_url=ORIGIN, headers={'Referer': ORIGIN + '/setup'},
                           data={'csrf_token': token, 'code': local_code(app)})
    assert response.status_code == 302
    assert client.get('/setup/status', base_url=ORIGIN).status_code == 200


@pytest.mark.parametrize('proxy, expected', [('127.0.0.1', 200), ('127.0.0.2', 403)])
def test_real_waitress_trusts_only_configured_peer_and_scheme(browser_app, monkeypatch, proxy, expected):
    import waitress
    browser_app.config.update(KIOSK_PUBLIC_ORIGIN=ORIGIN, KIOSK_TRUSTED_PROXY_IP=proxy, WTF_CSRF_ENABLED=True)
    original = waitress.create_server
    def bind(app, **kwargs):
        return original(app, **{**kwargs, 'host': '127.0.0.1', 'port': 0})
    monkeypatch.setattr(waitress, 'create_server', bind)
    startup = runpy.run_path(str(Path(__file__).resolve().parents[1] / 'docker/start.py'))
    server = startup['create_http_server'](browser_app)
    stopped = Event()
    def serve():
        while not stopped.is_set():
            server.asyncore.loop(timeout=0.05, map=server._map, count=1)
    thread = Thread(target=serve, daemon=True)
    thread.start()
    base = f'http://127.0.0.1:{server.effective_port}'
    forwarded = {'Host': 'kiosk.office.test', 'X-Forwarded-Proto': 'https',
                 'X-Forwarded-Host': 'evil.example', 'X-Forwarded-For': '127.0.0.1'}
    try:
        try:
            response = urlopen(Request(base + '/kiosk/', headers=forwarded), timeout=3)
        except HTTPError as error:
            assert error.code == expected
            error.close()
            return
        with response:
            assert response.status == expected
            cookie = response.headers['Set-Cookie']
            assert 'Secure;' in cookie
            csrf = re.search(r'data-csrf-token="([^"]+)"', response.read().decode()).group(1)
        headers = {**forwarded, 'Cookie': cookie.split(';')[0], 'Origin': ORIGIN,
                   'Referer': ORIGIN + '/kiosk/', 'X-CSRFToken': csrf}
        with urlopen(Request(base + '/kiosk/camera/start', headers=headers, data=b''), timeout=3) as started:
            assert json.load(started)['mode'] == 'preview'
        with pytest.raises(HTTPError) as wrong:
            urlopen(Request(base + '/kiosk/camera/start', headers={**headers, 'Origin': 'https://evil.example'}, data=b''), timeout=3)
        assert wrong.value.code == 403
        wrong.value.close()
    finally:
        browser_app.extensions['browser_camera'].close()
        stopped.set()
        thread.join(2)
        assert not thread.is_alive()
        server.close()
        server.task_dispatcher.shutdown()


@pytest.mark.parametrize('origin,proxy', [
    ('http://kiosk.office.test', '127.0.0.1'), ('https://kiosk.office.test/path', '127.0.0.1'),
    (ORIGIN, '*'), (ORIGIN, '127.0.0.0/8'), (ORIGIN, ''), ('', '127.0.0.1'),
])
def test_incomplete_or_broad_proxy_configuration_is_rejected(origin, proxy):
    from app import create_app
    with pytest.raises(ValueError, match='KIOSK_PUBLIC_ORIGIN'):
        create_app('testing', overrides={'KIOSK_PUBLIC_ORIGIN': origin, 'KIOSK_TRUSTED_PROXY_IP': proxy})
