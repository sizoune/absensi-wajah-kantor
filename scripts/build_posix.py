"""Build and exercise native macOS/Linux frozen packages without production models."""
import argparse
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from maintenance.platforms import executable_path, package_name, platform_id
from scripts.prepare_windows_build import prepare
from services.update_package import extract_package


def run(*command):
    subprocess.run([str(part) for part in command], cwd=ROOT, check=True)


def build(*, test_key=False):
    target = platform_id()
    if target == 'windows-x64' or sys.version_info[:3] != (3, 11, 17):
        raise RuntimeError('Build memerlukan CPython 3.11.17 native pada macOS/Linux yang didukung.')
    os.chdir(ROOT)
    os.environ['PYINSTALLER_CONFIG_DIR'] = str(ROOT / 'build/pyinstaller-cache')
    run(sys.executable, '-m', 'pip', 'check')
    prepare(test_key=test_key)
    run(sys.executable, '-m', 'PyInstaller', '--clean', '--noconfirm', 'distribution/Absensi.spec')
    release = ROOT / 'dist/release'
    release.mkdir(parents=True, exist_ok=True)
    if target == 'linux-x64':
        bundle = ROOT / 'dist/Absensi'
        shutil.copy2(ROOT / 'distribution/install_linux.sh', bundle / 'install.sh')
        (bundle / 'install.sh').chmod(0o755)
        contents = sorted(bundle.iterdir())
    else:
        bundle = ROOT / 'dist/Absensi.app'
        contents = [bundle]
    package = release / package_name(target)
    with tarfile.open(package, 'w:gz', dereference=False) as archive:
        for path in contents:
            archive.add(path, arcname=path.name)
    with package.open('rb') as source:
        digest = hashlib.file_digest(source, 'sha256').hexdigest()
    unpacked = ROOT / 'build/extracted-package'
    scratch = ROOT / 'build/packaged smoke ü'
    for path in (unpacked, scratch):
        if path.exists():
            shutil.rmtree(path)
    extract_package(package, unpacked, {'platform': target, 'size': package.stat().st_size, 'sha256': digest})
    program = unpacked / executable_path(target)
    run(program, '--root', scratch, 'self-check')
    if target.startswith('macos-'):
        run('/usr/bin/codesign', '--verify', '--deep', '--strict', unpacked / 'Absensi.app')
        image = ROOT / 'build/dmg'
        if image.exists():
            shutil.rmtree(image)
        image.mkdir()
        shutil.copytree(bundle, image / 'Absensi.app', symlinks=True)
        (image / 'BACA-SAYA.txt').write_text(
            'Salin Absensi.app ke ~/Applications, lalu buka aplikasi.\n'
            'Terminal menampilkan kode setup; browser membuka wizard kios.\n'
            'Build ad-hoc tanpa notarization: ikuti panduan Gatekeeper di dokumentasi rilis.\n', encoding='utf-8')
        run('/usr/bin/hdiutil', 'create', '-ov', '-format', 'UDZO', '-volname', 'Kioska Absensi Kantor',
            '-srcfolder', image, release / f'Kioska-{target}.dmg')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--test-key', action='store_true')
    build(test_key=parser.parse_args().test_key)
