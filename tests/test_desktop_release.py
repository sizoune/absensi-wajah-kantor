import base64
import json

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app_version import VERSION
from maintenance.platforms import TARGETS, metadata_name, package_name
from scripts import release_desktop
from services.update_package import verify_manifest


def artifacts(tmp_path, monkeypatch):
    key = Ed25519PrivateKey.generate()
    monkeypatch.setenv('UPDATE_SIGNING_KEY', base64.b64encode(key.private_bytes_raw()).decode())
    monkeypatch.setenv('RELEASE_TAG', 'v' + VERSION)
    for target in TARGETS:
        root = tmp_path / (target + '-distribution')
        for name in ('build', 'distribution', 'dist/release'):
            (root / name).mkdir(parents=True)
        (root / 'build/build-info.json').write_text(json.dumps({'test_key': False, 'platform': target}))
        (root / 'distribution/update-public-key.bin').write_bytes(key.public_key().public_bytes_raw())
        (root / 'dist/release' / package_name(target)).write_bytes(target.encode())
        if target.startswith('macos-'):
            (root / f'dist/release/Kioska-{target}.dmg').write_bytes(b'installer')
        elif target == 'windows-x64':
            (root / 'dist/release/AbsensiSetup.exe').write_bytes(b'installer')
    return key


def test_one_release_signs_every_target_and_keeps_legacy_windows_names(tmp_path, monkeypatch):
    key = artifacts(tmp_path, monkeypatch)
    release_desktop.sign_all(tmp_path)
    for target in TARGETS:
        output = tmp_path / (target + '-distribution') / 'dist/release'
        name = metadata_name(target)
        manifest = verify_manifest((output / name).read_bytes(), (output / (name + '.sig')).read_bytes(),
                                   key.public_key().public_bytes_raw(), '0.0.0', expected_platform=target)
        assert manifest['version'] == VERSION
        assert manifest['url'].endswith('/' + package_name(target))
    assert (tmp_path / 'windows-x64-distribution/dist/release/update.json').is_file()


@pytest.mark.parametrize('failure', ['missing', 'wrong_target', 'test_key', 'wrong_key', 'installer'])
def test_release_requires_all_production_artifacts(tmp_path, monkeypatch, failure):
    artifacts(tmp_path, monkeypatch)
    root = tmp_path / 'macos-x64-distribution'
    if failure == 'missing':
        root.rename(tmp_path / 'missing')
    elif failure == 'wrong_target':
        (root / 'build/build-info.json').write_text(json.dumps({'test_key': False, 'platform': 'macos-arm64'}))
    elif failure == 'test_key':
        (root / 'build/build-info.json').write_text(json.dumps({'test_key': True, 'platform': 'macos-x64'}))
    elif failure == 'wrong_key':
        (root / 'distribution/update-public-key.bin').write_bytes(b'x' * 32)
    else:
        (root / 'dist/release/Kioska-macos-x64.dmg').unlink()
    with pytest.raises((ValueError, FileNotFoundError)):
        release_desktop.sign_all(tmp_path)
