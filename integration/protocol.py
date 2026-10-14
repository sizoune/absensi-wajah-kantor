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


def validate_snapshot(value):
    """Return the validated snapshot unchanged; invalid snapshots raise ValueError."""
    _object(value, 'protocol_version snapshot_id issued_at sync_after_seconds clock_anchor_id policy_revision models employees', 'root fields')
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
