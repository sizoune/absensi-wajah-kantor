"""Install the release into a private Windows folder without replacing user data."""
import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys

from kiosk.lock import AlreadyRunning, SingleInstanceLock


RUNTIME_CHECK = (
    "import platform,struct,sys; sys.exit(not (sys.implementation.name == 'cpython' "
    "and sys.version_info[:2] == (3,11) and sys.version_info[:3] >= (3,11,17) "
    "and struct.calcsize('P') == 8 and platform.machine().lower() in ('amd64','x86_64')))"
)
IMPORT_CHECK = ('import cv2, onnxruntime, ai_edge_litert.interpreter, '
                'pygrabber.dshow_graph, comtypes, cryptography, flask, waitress')


def install(source, target):
    source, target = Path(source).resolve(), Path(target).resolve()
    if source != target and any((source / name).exists() for name in
                                ('.env', 'instance', '.venv', 'model_files')):
        raise ValueError('Gunakan folder instalasi lama; jangan pindahkan data atau path tersimpan.')
    target.mkdir(parents=True, exist_ok=True)
    with SingleInstanceLock(target / 'instance' / 'launcher.lock'):
        if source != target:
            shutil.copytree(source, target, dirs_exist_ok=True, ignore=shutil.ignore_patterns(
                '.git', '.codegraph', '.venv', '.runtime', '.bootstrap-cache', '.env*',
                'instance', 'model_files', 'backups', '__pycache__', '.pytest_cache',
                '*.sqlite3*', '*.onnx', '*.tflite', '*.key', '*.pem', 'biometric_keys.json'))
        for path in (target / 'scripts').glob('*.bat'):
            data = path.read_bytes()
            crlf = data.replace(b'\r\n', b'\n').replace(b'\n', b'\r\n')
            if data != crlf:
                path.write_bytes(crlf)
        python = target / '.venv' / 'Scripts' / 'python.exe'
        if not python.exists():
            subprocess.run([sys.executable, '-m', 'venv', str(target / '.venv')], check=True)
        subprocess.run([str(python), '-c', RUNTIME_CHECK], check=True)
        subprocess.run([str(python), '-m', 'ensurepip', '--upgrade'], cwd=target, check=True)
        print('Memasang dependensi aplikasi; pemasangan terputus dapat dijalankan ulang.', flush=True)
        subprocess.run([str(python), '-m', 'pip', 'install', '--only-binary=:all:',
                        '-r', 'requirements-eoffice.txt'], cwd=target, check=True)
        subprocess.run([str(python), '-m', 'pip', 'check'], cwd=target, check=True)
        subprocess.run([str(python), '-c', IMPORT_CHECK], cwd=target, check=True)
        subprocess.run([str(python), '-c', 'from app import create_app; create_app()'],
                       cwd=target, check=True)
    return python


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('target', type=Path)
    args = parser.parse_args()
    if os.name != 'nt':
        parser.error('Bootstrap ini memerlukan Windows 10/11 x64.')
    try:
        install(Path(__file__).resolve().parents[1], args.target)
    except (AlreadyRunning, OSError, ValueError, subprocess.CalledProcessError) as exc:
        print('Pemasangan belum selesai. Tutup aplikasi yang berjalan dan periksa runtime, '
              'izin folder, koneksi, serta pesan dependensi di atas. Data lama dipertahankan.',
              file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == '__main__':
    main()
