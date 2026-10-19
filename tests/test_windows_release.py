import base64
import json

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from scripts import release_windows
from services.update_package import verify_manifest
from app_version import VERSION


def release_tree(tmp_path, monkeypatch, *, test_key=False):
    key = Ed25519PrivateKey.generate()
    for directory in ('build', 'distribution', 'dist/release'):
        (tmp_path / directory).mkdir(parents=True)
    (tmp_path / 'build/build-info.json').write_text(json.dumps({'test_key': test_key}))
    (tmp_path / 'distribution/update-public-key.bin').write_bytes(key.public_key().public_bytes_raw())
    (tmp_path / 'distribution/RELEASE_NOTES.md').write_text('Release notes')
    (tmp_path / 'dist/release/windows-x64.zip').write_bytes(b'complete payload')
    monkeypatch.setattr(release_windows, 'ROOT', tmp_path)
    monkeypatch.setenv('UPDATE_SIGNING_KEY', base64.b64encode(key.private_bytes_raw()).decode())
    monkeypatch.setenv('RELEASE_TAG', 'v' + VERSION)
    return key


def test_release_signature_and_version_agree(tmp_path, monkeypatch):
    key = release_tree(tmp_path, monkeypatch)
    release_windows.sign_release()
    output = tmp_path / 'dist/release'
    manifest = verify_manifest((output / 'update.json').read_bytes(), (output / 'update.json.sig').read_bytes(),
                               key.public_key().public_bytes_raw(), '0.0.0')
    assert manifest['version'] == VERSION
    assert manifest['size'] == len(b'complete payload')


@pytest.mark.parametrize('failure', ['test_key', 'wrong_tag', 'wrong_key'])
def test_release_rejects_untrusted_configuration(tmp_path, monkeypatch, failure):
    release_tree(tmp_path, monkeypatch, test_key=failure == 'test_key')
    if failure == 'wrong_tag':
        monkeypatch.setenv('RELEASE_TAG', 'v9.0.0')
    elif failure == 'wrong_key':
        monkeypatch.setenv('UPDATE_SIGNING_KEY', base64.b64encode(Ed25519PrivateKey.generate().private_bytes_raw()).decode())
    with pytest.raises(ValueError):
        release_windows.sign_release()
    assert not (tmp_path / 'dist/release/update.json').exists()
