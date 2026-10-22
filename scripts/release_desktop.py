"""Require all four native artifacts before signing a single desktop release."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from maintenance.platforms import TARGETS, package_name
from scripts.release_windows import sign_release


def sign_all(artifacts):
    artifacts = Path(artifacts)
    for target in TARGETS:
        release = artifacts / (target + '-distribution') / 'dist/release'
        required = [package_name(target)]
        if target.startswith('macos-'):
            required.append(f'Kioska-{target}.dmg')
        elif target == 'windows-x64':
            required.append('AbsensiSetup.exe')
        if any(not (release / name).is_file() for name in required):
            raise ValueError(f'Missing release files: {target}')
    for target in TARGETS:
        artifact = artifacts / (target + '-distribution')
        if not artifact.is_dir():
            raise ValueError(f'Missing native artifact: {target}')
        sign_release(target, artifact_root=artifact)


if __name__ == '__main__':
    sign_all(sys.argv[1])
