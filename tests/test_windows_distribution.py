from pathlib import Path

import pytest

from maintenance.distribution import initialize
from maintenance.windows_entry import packaged_app
from services.update_package import UpdateError


def test_repeated_packaged_start_keeps_original_keys_and_setup(tmp_path):
    from extensions import db
    from models import SetupState
    from services.biometric_service import load_keyring
    first = packaged_app(tmp_path)
    key = Path(first.config['EOFFICE_KEY_FILE']).read_bytes()
    with first.app_context():
        db.session.get(SetupState, 1).progress = {'admin': True}
        db.session.commit()
        db.session.remove()
        db.engine.dispose()
    second = packaged_app(tmp_path)
    assert Path(second.config['EOFFICE_KEY_FILE']).read_bytes() == key
    with second.app_context():
        assert load_keyring(second.config['EOFFICE_KEY_FILE'])
        assert db.session.get(SetupState, 1).progress == {'admin': True}
        db.session.remove()
        db.engine.dispose()


@pytest.mark.parametrize('legacy', ['.env', '.venv', '.runtime', 'model_files', 'instance'])
def test_initializer_rejects_legacy_without_creating_active_pointer(tmp_path, legacy):
    if legacy == '.env':
        (tmp_path / legacy).write_text('SECRET')
    else:
        (tmp_path / legacy).mkdir()
        (tmp_path / legacy / 'keep').write_bytes(b'keep')
    with pytest.raises(UpdateError):
        initialize(tmp_path, '1.0.0')
    assert not (tmp_path / 'current.json').exists()


@pytest.mark.parametrize('blocked', ['updating', 'recovery'])
def test_password_recovery_never_opens_database_during_update(tmp_path, monkeypatch, blocked):
    from contextlib import nullcontext
    from kiosk.lock import SingleInstanceLock
    from maintenance.distribution import atomic_json
    import maintenance.windows_entry as entry
    opened = []
    monkeypatch.setattr(entry, 'packaged_app', lambda root: opened.append(root))
    if blocked == 'recovery':
        atomic_json(tmp_path / 'updates/journal.json', {'stage': 'applying', 'recovery_required': True})
    lock = SingleInstanceLock(tmp_path / 'updates/update.lock') if blocked == 'updating' else nullcontext()
    with lock:
        assert entry.main(['--root', str(tmp_path), 'reset-admin-password']) == 1
    assert opened == []
