"""Sign metadata only when the configured public/private keys and release tag agree."""
import base64
import hashlib
import json
import os
from pathlib import Path
import sys

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def sign_release(target='windows-x64', *, artifact_root=None):
    from app_version import VERSION, UPDATER_VERSION
    from services.update_package import verify_manifest
    from maintenance.platforms import metadata_name, package_name
    artifact_root = Path(artifact_root) if artifact_root is not None else ROOT
    metadata_file = metadata_name(target)
    if os.environ['RELEASE_TAG'] != 'v' + VERSION:
        raise ValueError('Release tag must match app_version.VERSION')
    info = json.loads((artifact_root / 'build/build-info.json').read_text())
    if info.get('platform', 'windows-x64') != target:
        raise ValueError('Build platform does not match release target')
    if info['test_key'] is not False:
        raise ValueError('A test-key build cannot be published')
    key = Ed25519PrivateKey.from_private_bytes(base64.b64decode(os.environ['UPDATE_SIGNING_KEY'], validate=True))
    public_key = (artifact_root / 'distribution/update-public-key.bin').read_bytes()
    if key.public_key().public_bytes_raw() != public_key:
        raise ValueError('Signing key does not match bundled public key')
    package = artifact_root / 'dist/release' / package_name(target)
    with package.open('rb') as source:
        digest = hashlib.file_digest(source, 'sha256').hexdigest()
    metadata = {'format_version': 1, 'version': VERSION, 'platform': target,
        'url': f'https://github.com/sizoune/absensi-wajah-kantor/releases/download/v{VERSION}/{package.name}',
        'size': package.stat().st_size, 'sha256': digest, 'min_updater_version': UPDATER_VERSION,
        'notes': (ROOT / 'distribution/RELEASE_NOTES.md').read_text(encoding='utf-8')}
    raw = json.dumps(metadata, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    signature = key.sign(raw)
    verify_manifest(raw, signature, public_key, VERSION, allow_equal=True, expected_platform=target)
    (package.parent / metadata_file).write_bytes(raw)
    (package.parent / (metadata_file + '.sig')).write_bytes(signature)


if __name__ == '__main__':
    sign_release()
