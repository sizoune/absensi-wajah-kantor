"""Per-user frozen installation; the original shortcut always selects current.json."""
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile

from app_version import VERSION
from kiosk.lock import SingleInstanceLock
from maintenance.distribution import active_version, executable, initialize, startup_allowed
from maintenance import platforms
from services.update_package import UpdateError


def external_environment():
    environment = {**os.environ, 'PYINSTALLER_RESET_ENVIRONMENT': '1'}
    if getattr(sys, 'frozen', False):
        original = environment.pop('LD_LIBRARY_PATH_ORIG', None)
        if original is None:
            environment.pop('LD_LIBRARY_PATH', None)
        else:
            environment['LD_LIBRARY_PATH'] = original
    return environment


def open_browser(url):
    try:
        return subprocess.run(['xdg-open', url], env=external_environment(),
                              timeout=15, check=False).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def launch_terminal(command, root):
    environment = external_environment()
    if sys.platform == 'darwin':
        script = Path(root) / 'updates/launch.command'
        script.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd, temporary = tempfile.mkstemp(dir=script.parent, suffix='.command')
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as output:
                output.write('#!/bin/sh\nexport PYINSTALLER_RESET_ENVIRONMENT=1\nexec ' + shlex.join(command) + '\n')
            Path(temporary).chmod(0o700)
            os.replace(temporary, script)
        finally:
            Path(temporary).unlink(missing_ok=True)
        command = ['/usr/bin/open', '-a', 'Terminal', str(script)]
    else:
        terminal = shutil.which('x-terminal-emulator')
        if not terminal:
            raise UpdateError('Pasang terminal desktop (x-terminal-emulator) untuk membuka aplikasi.')
        command = [terminal, '-e', *command]
    return subprocess.Popen(command, cwd=root, env=environment, start_new_session=True,
                            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def finder_launch():
    script = Path(sys._MEIPASS) / 'distribution/launch.command'
    subprocess.run(['/usr/bin/open', '-a', 'Terminal', str(script)], check=True,
                   env=external_environment())


def install(root):
    target = platforms.platform_id()
    if target == 'windows-x64' or not getattr(sys, 'frozen', False):
        raise UpdateError('Gunakan installer paket resmi untuk platform ini.')
    root = Path(root).absolute()
    if root.is_symlink():
        raise UpdateError('Folder instalasi tidak boleh berupa tautan.')
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    if any((root / name).is_symlink() for name in ('instance', 'updates', 'backups', 'versions', 'current.json')):
        raise UpdateError('Penyimpanan instalasi tidak boleh berupa tautan.')
    if root.stat().st_uid != os.getuid():
        raise UpdateError('Folder instalasi harus dimiliki pengguna kios.')
    root.chmod(0o700)
    previous_mask = os.umask(0o077)
    try:
        with SingleInstanceLock(root / 'updates/update.lock'):
            if ((root / 'current.json').exists()
                    or any((root / name).exists() for name in ('.env', '.venv', '.runtime', 'model_files'))
                    or ((root / 'instance').exists() and any((root / 'instance').iterdir()))):
                raise UpdateError('Instalasi/data lama ditemukan. Gunakan pembaruan melalui admin lokal.')
            from maintenance.inference_check import check_inference
            check_inference(Path(sys._MEIPASS) / 'inference')
            version = root / 'versions' / VERSION
            if version.exists():
                raise UpdateError('Folder versi sudah ada. Periksa pemasangan yang terputus.')
            version.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            staging = Path(tempfile.mkdtemp(prefix='.install-', dir=version.parent))
            try:
                source = Path(sys.executable).resolve()
                if target.startswith('macos-'):
                    shutil.copytree(source.parents[2], staging / 'Absensi.app', symlinks=True)
                else:
                    shutil.copytree(source.parent, staging, symlinks=True, dirs_exist_ok=True)
                staging.rename(version)
            finally:
                if staging.exists():
                    shutil.rmtree(staging)
            initialize(root, VERSION)
            if target == 'linux-x64':
                launcher = root / 'launch.sh'
                command = [str(executable(root, VERSION)), '--root', str(root)]
                launcher.write_text('#!/bin/sh\nexec ' + shlex.join(command) + ' "${1:-open}"\n', encoding='utf-8')
                launcher.chmod(0o700)
                data = platforms.default_root().parent
                shortcuts = data / 'applications'
                shortcuts.mkdir(parents=True, exist_ok=True)
                # Desktop Exec has its own quoting rules; never interpolate through a shell.
                quoted = str(launcher).replace('\\', '\\\\').replace('"', '\\"').replace('`', '\\`').replace('$', '\\$').replace('%', '%%')
                (shortcuts / 'kioska.desktop').write_text(
                    '[Desktop Entry]\nType=Application\nName=Kioska Absensi Kantor\n'
                    f'Exec="{quoted}"\nTerminal=true\nCategories=Office;\n', encoding='utf-8')
            print('Aplikasi terpasang. Buka shortcut Kioska atau jalankan peluncur kembali.', flush=True)
    finally:
        os.umask(previous_mask)


def open_active(root, mode='launch'):
    root = Path(root).absolute()
    if not (root / 'current.json').exists():
        if mode != 'launch':
            raise UpdateError('Versi aktif belum tersedia untuk pemulihan.')
        install(root)
    if mode != 'recover':
        startup_allowed(root)
    program = executable(root, active_version(root))
    os.execve(program, [str(program), '--root', str(root), mode],
              {**os.environ, 'PYINSTALLER_RESET_ENVIRONMENT': '1'})
