"""Release identifiers describe the running process, including Rosetta x64."""
import os
from pathlib import Path
import platform
import sys

from services.update_package import UpdateError

TARGETS = ('windows-x64', 'macos-arm64', 'macos-x64', 'linux-x64')


def platform_id():
    machine = platform.machine().lower()
    if sys.platform == 'darwin' and machine == 'arm64':
        return 'macos-arm64'
    if machine in ('amd64', 'x86_64'):
        target = {'win32': 'windows-x64', 'darwin': 'macos-x64', 'linux': 'linux-x64'}.get(sys.platform)
        if target:
            return target
    raise UpdateError('Platform aplikasi belum didukung.')


def executable_path(target):
    if target not in TARGETS:
        raise UpdateError('Platform rilis tidak valid.')
    if target.startswith('macos-'):
        return Path('Absensi.app/Contents/MacOS/Absensi')
    return Path('Absensi.exe' if target == 'windows-x64' else 'Absensi')


def metadata_name(target):
    executable_path(target)
    return 'update.json' if target == 'windows-x64' else f'update-{target}.json'


def package_name(target):
    executable_path(target)
    return target + ('.zip' if target == 'windows-x64' else '.tar.gz')


def default_root():
    target = platform_id()
    if target.startswith('macos-'):
        return Path.home() / 'Library/Application Support/AbsensiWajahKantor'
    if target == 'linux-x64':
        base = Path(os.environ.get('XDG_DATA_HOME') or Path.home() / '.local/share')
        if not base.is_absolute():
            raise UpdateError('XDG_DATA_HOME harus berupa path absolut.')
        return base / 'AbsensiWajahKantor'
    return Path(os.environ['LOCALAPPDATA']) / 'AbsensiWajahKantor'
