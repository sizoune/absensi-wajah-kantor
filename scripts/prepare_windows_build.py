"""Prepare public trust material, synthetic fixtures and dependency license inventory."""
import base64
from importlib.metadata import distributions
import json
import os
from pathlib import Path
import shutil
import sys

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def prepare(*, test_key=False):
    from scripts.make_inference_fixtures import generate
    from maintenance.platforms import platform_id
    if test_key:
        key = Ed25519PrivateKey.generate().public_key().public_bytes_raw()
    else:
        key = base64.b64decode(os.environ['UPDATE_PUBLIC_KEY'], validate=True)
    if len(key) != 32 or key == bytes(32):
        raise ValueError('A nonzero 32-byte update public key is required')
    (ROOT / 'distribution/update-public-key.bin').write_bytes(key)
    output = ROOT / 'build/licenses'
    output.mkdir(parents=True, exist_ok=True)
    inventory = []
    for package in distributions():
        name = package.metadata['Name']
        declared = package.metadata.get('License-Expression') or package.metadata.get('License')
        folder = output / name
        folder.mkdir(exist_ok=True)
        copied = []
        for file in package.files or []:
            if any(word in file.name.lower() for word in ('license', 'copying', 'notice')):
                source = package.locate_file(file)
                if source.is_file():
                    target = folder / str(len(copied))
                    shutil.copyfile(source, target)
                    copied.append(str(target.relative_to(output)))
        if not copied and (not declared or declared == 'UNKNOWN'):
            raise RuntimeError(f'License metadata missing: {name}')
        inventory.append({'name': name, 'version': package.version, 'license': declared, 'files': copied})
    (output / 'inventory.json').write_text(json.dumps(inventory, ensure_ascii=False, indent=2), encoding='utf-8')
    generate(ROOT / 'build/inference')
    (ROOT / 'build/build-info.json').write_text(json.dumps({'test_key': test_key, 'platform': platform_id()}), encoding='utf-8')


if __name__ == '__main__':
    prepare(test_key='--test-key' in sys.argv)
