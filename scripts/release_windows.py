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


def sign_release():
    from app_version import VERSION, UPDATER_VERSION
    from services.update_package import verify_manifest
    if os.environ['RELEASE_TAG'] != 'v' + VERSION:
        raise ValueError('Release tag must match app_version.VERSION')
    if json.loads((ROOT / 'build/build-info.json').read_text())['test_key']:
        raise ValueError('A test-key build cannot be published')
    key = Ed25519PrivateKey.from_private_bytes(base64.b64decode(os.environ['UPDATE_SIGNING_KEY'], validate=True))
    public_key = (ROOT / 'distribution/update-public-key.bin').read_bytes()
    if key.public_key().public_bytes_raw() != public_key:
        raise ValueError('Signing key does not match bundled public key')
    package = ROOT / 'dist/release/windows-x64.zip'
    with package.open('rb') as source:
        digest = hashlib.file_digest(source, 'sha256').hexdigest()
    metadata = {'format_version': 1, 'version': VERSION, 'platform': 'windows-x64',
        'url': f'https://github.com/sizoune/absensi-wajah-kantor/releases/download/v{VERSION}/windows-x64.zip',
        'size': package.stat().st_size, 'sha256': digest, 'min_updater_version': UPDATER_VERSION,
        'notes': (ROOT / 'distribution/RELEASE_NOTES.md').read_text(encoding='utf-8')}
    raw = json.dumps(metadata, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    signature = key.sign(raw)
    verify_manifest(raw, signature, public_key, VERSION, allow_equal=True)
    (ROOT / 'dist/release/update.json').write_bytes(raw)
    (ROOT / 'dist/release/update.json.sig').write_bytes(signature)


if __name__ == '__main__':
    sign_release()
