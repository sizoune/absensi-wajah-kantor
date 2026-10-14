"""Bounded HTTPS transport for immutable kiosk events and gallery snapshots."""

import json
import math
import ssl
import time
from datetime import datetime
from email.utils import format_datetime, parsedate_to_datetime
from typing import Any, cast
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import HTTPSHandler, HTTPRedirectHandler, Request, build_opener

from integration.clock import MAX_ANCHOR_UNCERTAINTY_SECONDS
from integration.protocol import JSONValue, MAX_SNAPSHOT_BYTES, UUID, validate_snapshot, validate_terminal_response


class RequestFailure(RuntimeError):
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code
        super().__init__(f'E-office HTTP status {status_code}')


class RejectRedirects(HTTPRedirectHandler):
    def redirect_request(self, req: Any, fp: Any, code: int, msg: str,
                         headers: Any, newurl: str) -> None:
        return None


def _object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate JSON field')
        result[key] = value
    return result


def _invalid_constant(value: str) -> None:
    raise ValueError('Non-finite JSON number')


def _json(body: bytes) -> dict[str, JSONValue]:
    try:
        result = json.loads(body.decode('utf-8'), object_pairs_hook=_object, parse_constant=_invalid_constant)
    except RecursionError as exc:
        raise ValueError('Invalid e-office JSON nesting') from exc
    if type(result) is not dict:
        raise ValueError('Expected e-office JSON object')
    return cast(dict[str, JSONValue], result)


class EofficeClient:
    def __init__(self, base_url: str, token: str) -> None:
        if type(base_url) is not str:
            raise ValueError('Expected HTTPS e-office URL')
        url = urlsplit(base_url)
        if (url.scheme != 'https' or not url.hostname or url.username is not None
                or url.password is not None or url.query or url.fragment
                or any(char.isspace() for char in base_url)):
            raise ValueError('Expected HTTPS e-office URL without credentials, query or fragment')
        if type(token) is not str or not token or any(char.isspace() for char in token):
            raise ValueError('Expected e-office device token')
        self._base_url = base_url.rstrip('/')
        self._token = token
        self._opener = build_opener(HTTPSHandler(context=ssl.create_default_context()), RejectRedirects())
        self.snapshot_server_time: datetime | None = None
        self.snapshot_received_monotonic: float | None = None
        self.snapshot_received_wall_time: float | None = None
        self.snapshot_uncertainty_seconds: float | None = None
        self._monotonic = time.monotonic
        self._wall_time = time.time

    def _request(self, path: str, body: bytes | None, limit: int) -> tuple[int, dict[str, JSONValue], Any, float, float, float]:
        request = Request(self._base_url + path, data=body, headers={
            'Authorization': 'Bearer ' + self._token, 'Accept': 'application/json',
            'Accept-Encoding': 'identity', 'Cache-Control': 'no-cache', 'Content-Type': 'application/json; charset=utf-8'})
        started = self._monotonic()
        try:
            response = self._opener.open(request, timeout=5)
        except HTTPError as error:
            if error.code not in (400, 409):
                error.close()
                raise RequestFailure(error.code) from None
            response = error
        received_monotonic, received_wall = self._monotonic(), self._wall_time()
        uncertainty = 1 + received_monotonic - started
        with response:
            # Date floors to a second; the full round trip bounds unknown one-way delay.
            if body is None and (not math.isfinite(uncertainty)
                                 or not 1 <= uncertainty <= MAX_ANCHOR_UNCERTAINTY_SECONDS):
                raise ValueError('Authenticated server Date uncertainty exceeds clock tolerance')
            code = response.status if hasattr(response, 'status') else response.code
            if code not in (200, 400, 409):
                raise RequestFailure(code)
            headers = response.headers
            if (headers.get_content_type() != 'application/json'
                    or headers.get('Content-Encoding', 'identity').lower() != 'identity'):
                raise ValueError('Unexpected e-office response encoding')
            length = headers.get('Content-Length')
            if length is not None and (not length.isascii() or not length.isdecimal() or int(length) > limit):
                raise ValueError('Invalid or oversized e-office response length')
            payload = response.read(limit + 1)
            if len(payload) > limit or length is not None and len(payload) != int(length):
                raise ValueError('Oversized or partial e-office response')
            return code, _json(payload), headers, received_monotonic, received_wall, uncertainty

    def snapshot(self) -> dict[str, JSONValue]:
        self.snapshot_server_time = None
        self.snapshot_received_monotonic = self.snapshot_received_wall_time = self.snapshot_uncertainty_seconds = None
        code, snapshot, headers, received_monotonic, received_wall, uncertainty = self._request('/api/kiosk/snapshot', None, MAX_SNAPSHOT_BYTES)
        if code != 200:
            raise RequestFailure(code)
        validate_snapshot(snapshot)
        dates = headers.get_all('Date', [])
        if len(dates) != 1:
            raise ValueError('Missing or ambiguous authenticated server Date')
        try:
            server_time = parsedate_to_datetime(dates[0])
            if server_time.utcoffset() is None or format_datetime(server_time, usegmt=True) != dates[0]:
                raise ValueError('Invalid authenticated server Date')
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError('Invalid authenticated server Date') from exc
        self.snapshot_server_time = server_time
        self.snapshot_received_monotonic = received_monotonic
        self.snapshot_received_wall_time = received_wall
        self.snapshot_uncertainty_seconds = uncertainty
        return snapshot

    def send(self, event_bytes: bytes) -> dict[str, JSONValue]:
        if type(event_bytes) is not bytes or len(event_bytes) > 16 * 1024:
            raise ValueError('Expected immutable bounded event bytes')
        event_id = _json(event_bytes).get('event_id')
        if type(event_id) is not str or UUID.fullmatch(event_id) is None:
            raise ValueError('Invalid event ID')
        code, result, _, _, _, _ = self._request('/api/kiosk/events', event_bytes, 16 * 1024)
        if code in (400, 409):
            # Existing API errors use text; the encrypted local terminal contract uses message.
            if (set(result) not in ({'status', 'text'}, {'status', 'text', 'event_id'},
                                    {'status', 'message'}, {'status', 'message', 'event_id'})
                    or result.get('event_id', event_id) != event_id):
                raise ValueError('Invalid e-office terminal HTTP error')
            message = result.get('message', result.get('text'))
            body: dict[str, JSONValue] = {'status': result.get('status'), 'message': message}
            if 'event_id' in result:
                body['event_id'] = result['event_id']
            result = {'status_code': code, 'event_id': event_id, 'body': body}
        return validate_terminal_response(event_id, result)
