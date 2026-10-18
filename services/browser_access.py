"""Local HTTP and explicitly configured institution HTTPS browser access."""
from urllib.parse import urlsplit

from flask import current_app, jsonify, request
from flask.sessions import SecureCookieSessionInterface


class BrowserSessionInterface(SecureCookieSessionInterface):
    def get_cookie_secure(self, app):
        return request.is_secure or super().get_cookie_secure(app)


def browser_origin_allowed():
    return (urlsplit(request.host_url).hostname in ('localhost', '127.0.0.1', '::1')
            or request.is_secure and request.host_url.rstrip('/') == current_app.config.get('KIOSK_PUBLIC_ORIGIN'))


def protect_browser_access():
    if current_app.config.get('CAMERA_SOURCE') == 'browser' and not browser_origin_allowed():
        return jsonify(ok=False, error='origin'), 403
