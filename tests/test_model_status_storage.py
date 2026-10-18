"""Model status HTTP must preserve damaged existing gallery/queue bytes."""
import pytest

from test_setup_organization import connect_and_select, wizard
from test_setup_readiness import finish


@pytest.mark.parametrize('path', ['/setup/models', '/setup/models/manage'])
def test_model_status_preserves_empty_old_storage_and_reports_unready(wizard, tmp_path, path):
    _, client, _, calls = wizard
    connect_and_select(client)
    assert finish(client).status_code == 200
    storage = tmp_path / 'eoffice.sqlite3'
    storage.write_bytes(b'')
    key = (tmp_path / 'biometric_keys.json').read_bytes()
    calls.clear()
    response = client.get(path)
    assert response.status_code == 200
    assert storage.read_bytes() == b''
    assert (tmp_path / 'biometric_keys.json').read_bytes() == key
    assert calls == []
    status = client.get('/setup/models').json
    assert status['ready'] is False and status['compatible'] is False
