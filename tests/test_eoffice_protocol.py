import copy
import json
from pathlib import Path

import pytest

from integration.protocol import validate_snapshot


@pytest.fixture
def snapshot():
    return json.loads((Path(__file__).parent / 'fixtures/eoffice_snapshot.json').read_text())


def test_documented_snapshot_is_accepted_without_mutation(snapshot):
    original = copy.deepcopy(snapshot)
    assert validate_snapshot(snapshot) == original
    assert snapshot == original
    assert snapshot['protocol_version'] == 1
    assert snapshot['policy_revision'] == 1
    assert snapshot['sync_after_seconds'] == 60
    assert snapshot['snapshot_id'] == '90d6f137-d36e-47be-88c1-6c15cd9ad25e'
    assert [(e['pegawai_id'], e['embedding_version'], len(e['samples'][0])) for e in snapshot['employees']] == [(101, 'auraface_512', 512), (102, 'mobilefacenet_v1_periocular', 192)]


@pytest.mark.parametrize('index,dimension', [(0, 128), (0, 513), (1, 128), (1, 512), (1, 193)])
def test_bad_dimension_never_enters_gallery(snapshot, index, dimension):
    snapshot['employees'][index]['samples'][0] = [1.0] * dimension
    with pytest.raises(ValueError):
        validate_snapshot(snapshot)


@pytest.mark.parametrize('field', ['protocol_version', 'snapshot_id', 'issued_at', 'sync_after_seconds', 'clock_anchor_id', 'policy_revision', 'models', 'employees'])
@pytest.mark.parametrize('missing', [True, False])
def test_snapshot_requires_every_field(snapshot, field, missing):
    if missing:
        del snapshot[field]
    else:
        snapshot[field] = None
    with pytest.raises(ValueError):
        validate_snapshot(snapshot)


@pytest.mark.parametrize('field,values', [
    ('protocol_version', [True, 0, 2, '1']),
    ('policy_revision', [True, 0, 1.5, 9007199254740992, '1']),
    ('sync_after_seconds', [True, 0, -1, 86401, '60']),
    ('snapshot_id', ['bad', '00000000-0000-0000-0000-000000000000']),
    ('clock_anchor_id', ['bad']),
    ('issued_at', ['2026-10-14T07:25:00Z', '2026-10-14T07:25:00+07:00', '2026-02-30T07:25:00+08:00', '2026-10-14T24:00:00+08:00', '2026-10-14 07:25:00+08:00', '2026-10-14T07:25:60+08:00']),
])
def test_rejects_invalid_snapshot_scalars(snapshot, field, values):
    for value in values:
        bad = copy.deepcopy(snapshot)
        bad[field] = value
        with pytest.raises(ValueError):
            validate_snapshot(bad)


@pytest.mark.parametrize('field', ['pegawai_id', 'display_name', 'verification_type', 'enrollment_id', 'enrollment_revision', 'embedding_version', 'samples'])
@pytest.mark.parametrize('missing', [True, False])
def test_employee_requires_every_field(snapshot, field, missing):
    if missing:
        del snapshot['employees'][0][field]
    else:
        snapshot['employees'][0][field] = None
    with pytest.raises(ValueError):
        validate_snapshot(snapshot)


@pytest.mark.parametrize('field,values', [
    ('pegawai_id', [True, 0, -1, 1.5, 9007199254740992, '101']),
    ('enrollment_id', [True, 0, -1, 1.5, 9007199254740992, '12']),
    ('enrollment_revision', ['a' * 63, 'z' * 64, 1]),
    ('display_name', ['', ' ' * 2, 'x' * 256, 1]),
    ('embedding_version', ['sface_128', 'mobilefacenet_v1_periocular']),
    ('verification_type', ['eye', 'periocular']),
    ('samples', [[], [[1.0] * 512] * 11, 'samples', [None]]),
])
def test_rejects_invalid_employee_scalars(snapshot, field, values):
    for value in values:
        bad = copy.deepcopy(snapshot)
        bad['employees'][0][field] = value
        with pytest.raises(ValueError):
            validate_snapshot(bad)


@pytest.mark.parametrize('value', [float('nan'), float('inf'), -float('inf'), True, '0.1', None, 1.01, -1.01])
def test_invalid_vector_component_never_enters_gallery(snapshot, value):
    snapshot['employees'][0]['samples'][0][0] = value
    with pytest.raises(ValueError):
        validate_snapshot(snapshot)


def test_rejects_zero_vectors_and_duplicate_enrollments(snapshot):
    bad = copy.deepcopy(snapshot)
    bad['employees'][0]['samples'][0] = [0.0] * 512
    with pytest.raises(ValueError):
        validate_snapshot(bad)
    bad = copy.deepcopy(snapshot)
    bad['employees'].append(copy.deepcopy(bad['employees'][0]))
    with pytest.raises(ValueError):
        validate_snapshot(bad)
    snapshot['employees'][1]['enrollment_id'] = snapshot['employees'][0]['enrollment_id']
    with pytest.raises(ValueError):
        validate_snapshot(snapshot)


@pytest.mark.parametrize('where', ['root', 'employee', 'model', 'quality', 'liveness'])
def test_rejects_unknown_fields(snapshot, where):
    model = snapshot['models']['auraface_512']
    target = {'root': snapshot, 'employee': snapshot['employees'][0], 'model': model, 'quality': model['quality'], 'liveness': model['liveness']}[where]
    target['unknown'] = True
    with pytest.raises(ValueError):
        validate_snapshot(snapshot)


@pytest.mark.parametrize('field', ['embedding_version', 'dimension', 'weights_sha256', 'threshold', 'margin', 'quality', 'liveness'])
@pytest.mark.parametrize('missing', [True, False])
def test_model_requires_every_field(snapshot, field, missing):
    model = snapshot['models']['auraface_512']
    if missing:
        del model[field]
    else:
        model[field] = None
    with pytest.raises(ValueError):
        validate_snapshot(snapshot)


@pytest.mark.parametrize('field,values', [
    ('embedding_version', ['sface_128', 'mobilefacenet_v1_periocular']),
    ('dimension', [True, '512', 128, 192]),
    ('weights_sha256', ['b' * 63, 'z' * 64, 1]),
    ('threshold', [True, '0.7', 0, -1, 1.01, float('nan'), float('inf')]),
    ('margin', [True, '0.1', 0, -1, 2.01, float('nan'), float('inf')]),
])
def test_rejects_invalid_model_policy(snapshot, field, values):
    for value in values:
        bad = copy.deepcopy(snapshot)
        bad['models']['auraface_512'][field] = value
        with pytest.raises(ValueError):
            validate_snapshot(bad)


@pytest.mark.parametrize('group,field,values', [
    ('quality', 'min_size', [True, 0, 1.5, 10001, '80']),
    ('quality', 'min_sharpness', [True, -1, 1000001, '50', float('nan'), float('inf')]),
    ('liveness', 'real_threshold', [True, 0.2, 0, 1.01, float('nan'), float('inf')]),
    ('liveness', 'spoof_threshold', [True, -0.01, 0.8, 1.01, float('nan'), float('inf')]),
    ('liveness', 'min_samples', [True, 0, 11, 1.5, '3']),
    ('liveness', 'decide_seconds', [True, 0, -1, 301, '2', float('nan'), float('inf')]),
])
def test_rejects_invalid_gate_policy(snapshot, group, field, values):
    for value in values + [None]:
        bad = copy.deepcopy(snapshot)
        bad['models']['auraface_512'][group][field] = value
        with pytest.raises(ValueError):
            validate_snapshot(bad)
    del snapshot['models']['auraface_512'][group][field]
    with pytest.raises(ValueError):
        validate_snapshot(snapshot)


def test_rejects_unknown_and_missing_models(snapshot):
    bad = copy.deepcopy(snapshot)
    bad['models']['sface_128'] = copy.deepcopy(bad['models']['auraface_512'])
    with pytest.raises(ValueError):
        validate_snapshot(bad)
    del snapshot['models']['mobilefacenet_v1_periocular']
    with pytest.raises(ValueError):
        validate_snapshot(snapshot)


def test_empty_gallery_can_revoke_every_employee(snapshot):
    snapshot['employees'] = []
    assert validate_snapshot(snapshot) == snapshot


def test_accepts_safe_integer_and_policy_boundaries(snapshot):
    snapshot['policy_revision'] = 9007199254740991
    snapshot['issued_at'] = '2024-02-29T07:25:00.123456+08:00'
    snapshot['employees'][0]['pegawai_id'] = 9007199254740991
    snapshot['employees'][0]['samples'] *= 5
    for model in snapshot['models'].values():
        model['threshold'] = 1
        model['margin'] = 2
        model['quality'] = {'min_size': 10000, 'min_sharpness': 0}
        model['liveness'] = {'real_threshold': 1, 'spoof_threshold': 0, 'min_samples': 10, 'decide_seconds': 300}
    assert validate_snapshot(snapshot) == snapshot


def test_rejects_oversized_employee_array(snapshot):
    snapshot['employees'] = snapshot['employees'][:1] * 10001
    with pytest.raises(ValueError):
        validate_snapshot(snapshot)


def test_rejects_snapshot_over_16_mib(snapshot):
    employee = snapshot['employees'][0]
    employee['samples'] = [[0.1234567890123456] * 512] * 10
    snapshot['employees'] = [{**employee, 'pegawai_id': i + 1, 'enrollment_id': i + 1} for i in range(400)]
    assert len(json.dumps(snapshot).encode('utf-8')) > 16 * 1024 * 1024
    with pytest.raises(ValueError):
        validate_snapshot(snapshot)


@pytest.mark.parametrize('value', [None, [], 'snapshot', 1])
def test_requires_json_object(value):
    with pytest.raises(ValueError):
        validate_snapshot(value)


@pytest.mark.parametrize('index', [0, 1])
def test_rejects_non_string_verification_types_without_coercion(snapshot, index):
    verification_type = snapshot['employees'][index]['verification_type']
    for value in [[verification_type], [[verification_type]], [], {}, True, False]:
        bad = copy.deepcopy(snapshot)
        bad['employees'][index]['verification_type'] = value
        with pytest.raises(ValueError):
            validate_snapshot(bad)


@pytest.mark.parametrize('outcome', ['recorded', 'duplicate', 'outside_schedule', 'rejected'])
def test_shared_terminal_validator_binds_every_known_outcome_to_original_uuid(outcome):
    from integration.protocol import validate_terminal_response
    event_id = '85f42b66-6a77-4c56-9efc-4231ff6eab42'
    response = {'status': True, 'data': {'event_id': event_id, 'outcome': outcome, 'reason': None,
        'ad_id': None, 'work_date': None, 'session': None, 'received_at': '2026-10-14T07:25:00+08:00'}}
    assert validate_terminal_response(event_id, response) is response
    response['data']['event_id'] = '15f42b66-6a77-4c56-9efc-4231ff6eab42'
    with pytest.raises(ValueError):
        validate_terminal_response(event_id, response)
