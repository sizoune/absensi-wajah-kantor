"""Validate the version-one e-office gallery before it can replace local state."""

import json
import re
from datetime import datetime

MAX_SAFE_INTEGER = 9007199254740991
MAX_SNAPSHOT_BYTES = 16 * 1024 * 1024
MODELS = {'auraface_512': ('face', 512), 'mobilefacenet_v1_periocular': ('periocular', 192)}
UUID = re.compile(r'[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}', re.IGNORECASE)
SHA256 = re.compile(r'[0-9a-f]{64}')
WITA = re.compile(r'[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,9})?\+08:00')


def _require(condition, field):
    if not condition:
        raise ValueError(f'Invalid e-office snapshot: {field}')


def _object(value, fields, name):
    _require(type(value) is dict and set(value) == set(fields.split()), name)


def _integer(value, minimum=1, maximum=MAX_SAFE_INTEGER):
    return type(value) is int and minimum <= value <= maximum


def _number(value, minimum, maximum):
    return type(value) in (int, float) and minimum <= value <= maximum


def _uuid(value):
    return type(value) is str and UUID.fullmatch(value) is not None


def _sha256(value):
    return type(value) is str and SHA256.fullmatch(value) is not None


def _wita(value):
    if type(value) is not str or not WITA.fullmatch(value):
        return False
    try:
        datetime.fromisoformat(value)
    except ValueError:
        return False
    return True


def _model(model, version, dimension):
    _object(model, 'embedding_version dimension weights_sha256 threshold margin quality liveness', 'model fields')
    _require(model['embedding_version'] == version, 'model version')
    _require(_integer(model['dimension']) and model['dimension'] == dimension, 'model dimension')
    _require(_sha256(model['weights_sha256']), 'model weights_sha256')
    _require(_number(model['threshold'], 0, 1) and model['threshold'] > 0, 'model threshold')
    _require(_number(model['margin'], 0, 2) and model['margin'] > 0, 'model margin')
    quality = model['quality']
    _object(quality, 'min_size min_sharpness', 'quality fields')
    _require(_integer(quality['min_size'], 1, 10000), 'quality min_size')
    _require(_number(quality['min_sharpness'], 0, 1000000), 'quality min_sharpness')
    live = model['liveness']
    _object(live, 'real_threshold spoof_threshold min_samples decide_seconds', 'liveness fields')
    _require(_number(live['real_threshold'], 0, 1) and _number(live['spoof_threshold'], 0, 1), 'liveness thresholds')
    _require(live['spoof_threshold'] < live['real_threshold'], 'liveness threshold ordering')
    _require(_integer(live['min_samples'], 1, 10), 'liveness min_samples')
    _require(_number(live['decide_seconds'], 0, 300) and live['decide_seconds'] > 0, 'liveness decide_seconds')


def validate_snapshot(value, *, test_mode=False):
    """Return the validated snapshot unchanged; invalid snapshots raise ValueError."""
    fields = 'protocol_version snapshot_id issued_at sync_after_seconds clock_anchor_id policy_revision models employees'
    _object(value, fields + (' purpose' if test_mode else ''), 'root fields')
    if test_mode:
        _require(value['purpose'] == 'camera_test', 'purpose')
    _require(type(value['protocol_version']) is int and value['protocol_version'] == 1, 'protocol_version')
    for field in ('snapshot_id', 'clock_anchor_id'):
        _require(_uuid(value[field]), field)
    _require(_wita(value['issued_at']), 'issued_at')
    _require(_integer(value['sync_after_seconds'], 1, 86400), 'sync_after_seconds')
    _require(_integer(value['policy_revision']), 'policy_revision')
    models = value['models']
    _object(models, ' '.join(MODELS), 'models')
    for version, (_, dimension) in MODELS.items():
        _model(models[version], version, dimension)
    employees = value['employees']
    _require(type(employees) is list and len(employees) <= 10000, 'employees')
    identities, enrollments = set(), set()
    for employee in employees:
        _object(employee, 'pegawai_id display_name verification_type enrollment_id enrollment_revision embedding_version samples', 'employee fields')
        for field in ('pegawai_id', 'enrollment_id'):
            _require(_integer(employee[field]), field)
        name = employee['display_name']
        _require(type(name) is str and 0 < len(name) <= 255 and bool(name.strip()), 'display_name')
        _require(_sha256(employee['enrollment_revision']), 'enrollment_revision')
        version = employee['embedding_version']
        _require(type(version) is str and version in MODELS, 'embedding_version')
        verification_type, dimension = MODELS[version]
        _require(employee['verification_type'] == verification_type, 'model pair')
        identity = (employee['pegawai_id'], verification_type)
        _require(identity not in identities and employee['enrollment_id'] not in enrollments, 'duplicate enrollment')
        identities.add(identity)
        enrollments.add(employee['enrollment_id'])
        samples = employee['samples']
        _require(type(samples) is list and 1 <= len(samples) <= 10, 'samples')
        for vector in samples:
            _require(type(vector) is list and len(vector) == dimension, 'sample dimension')
            _require(all(_number(component, -1, 1) for component in vector) and any(component != 0 for component in vector), 'sample components')
    encoded = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode('utf-8')
    _require(len(encoded) <= MAX_SNAPSHOT_BYTES, 'snapshot exceeds 16 MiB')
    return value


JSONValue = None | bool | int | float | str | list['JSONValue'] | dict[str, 'JSONValue']


def validate_event_purpose(event: object, *, test_mode: bool = False) -> None:
    if (type(event) is not dict
            or (event.get('purpose') != 'camera_test' if test_mode else 'purpose' in event)):
        raise ValueError('Invalid e-office event purpose')


def validate_terminal_response(event_id: str, response: dict[str, JSONValue], *, test_mode: bool = False) -> dict[str, JSONValue]:
    """Validate server outcomes or locally bound terminal HTTP 400/409 errors."""
    if not _uuid(event_id) or type(response) is not dict:
        raise ValueError('Invalid terminal e-office response')
    if set(response) == {'status_code', 'event_id', 'body'}:
        body = response['body']
        if (type(response['status_code']) is not int or response['status_code'] not in (400, 409)
                or response['event_id'] != event_id or type(body) is not dict
                or set(body) not in ({'status', 'message'}, {'status', 'message', 'event_id'})
                or body.get('status') is not False or type(body.get('message')) is not str
                or not body['message'].strip() or body.get('event_id', event_id) != event_id):
            raise ValueError('Invalid terminal e-office HTTP error')
        return response
    if set(response) != {'status', 'data'} or response['status'] is not True:
        raise ValueError('Invalid terminal e-office response envelope')
    data = response['data']
    if test_mode:
        reasons = ('snapshot_not_authorized', 'enrollment_not_authorized', 'invalid_clock_proof', 'recognition_evidence_failed')
        if (type(data) is not dict
                or set(data) != {'event_id', 'outcome', 'reason', 'test_id', 'official_attendance', 'purpose', 'received_at'}
                or data['event_id'] != event_id or data['official_attendance'] is not False
                or data['purpose'] != 'camera_test' or not _wita(data['received_at'])
                or not (data['outcome'] == 'test_recorded' and data['reason'] is None and _integer(data['test_id'])
                        or data['outcome'] == 'rejected' and data['test_id'] is None and data['reason'] in reasons)):
            raise ValueError('Invalid terminal camera test response data')
        return response
    if (type(data) is not dict
            or set(data) != {'event_id', 'outcome', 'reason', 'ad_id', 'work_date', 'session', 'received_at'}
            or data['event_id'] != event_id or type(data['outcome']) is not str
            or data['outcome'] not in ('recorded', 'duplicate', 'outside_schedule', 'rejected')
            or not (data['reason'] is None or type(data['reason']) is str and bool(data['reason'].strip()))
            or not (data['ad_id'] is None or _integer(data['ad_id']))
            or data['session'] not in ('check_in', 'check_out', None)
            or not _wita(data['received_at'])):
        raise ValueError('Invalid terminal e-office response data')
    if data['work_date'] is not None:
        if type(data['work_date']) is not str or not re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}', data['work_date']):
            raise ValueError('Invalid terminal e-office work date')
        try:
            datetime.strptime(data['work_date'], '%Y-%m-%d')
        except ValueError as exc:
            raise ValueError('Invalid terminal e-office work date') from exc
    return response
