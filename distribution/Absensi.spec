# Build natively with the lock matching this platform.
from pathlib import Path
import sys
from PyInstaller.utils.hooks import collect_all, copy_metadata
from importlib.metadata import distributions

root = Path(SPECPATH).parent
sys.path.insert(0, str(root))
from app_version import VERSION
datas = [(str(root / 'templates'), 'templates'), (str(root / 'static'), 'static'),
         (str(root / 'distribution/update-public-key.bin'), 'distribution'),
         (str(root / 'build/inference'), 'inference'), (str(root / 'build/licenses'), 'licenses')]
if sys.platform == 'darwin':
    datas.append((str(root / 'distribution/launch.command'), 'distribution'))
binaries, hidden = [], []
packages = ['onnxruntime', 'ai_edge_litert', 'skimage']
if sys.platform == 'win32':
    packages += ['pygrabber', 'comtypes']
for package in packages:
    package_data, package_binaries, package_hidden = collect_all(package)
    datas += package_data
    binaries += package_binaries
    hidden += package_hidden
for package in distributions():
    datas += copy_metadata(package.metadata['Name'])

a = Analysis([str(root / 'maintenance/windows_entry.py')], pathex=[str(root)],
             binaries=binaries, datas=datas, hiddenimports=hidden,
             excludes=['pytest', 'onnx', 'tflite', 'PyInstaller'])
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='Absensi', debug=False,
          bootloader_ignore_signals=False, strip=False, upx=False, console=True)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='Absensi')

if sys.platform == 'darwin':
    app = BUNDLE(coll, name='Absensi.app', bundle_identifier='id.go.tabalong.kioska',
                 info_plist={'CFBundleName': 'Kioska Absensi Kantor',
                             'CFBundleShortVersionString': VERSION,
                             'LSMinimumSystemVersion': '14.0',
                             'NSCameraUsageDescription': 'Kamera digunakan untuk verifikasi absensi wajah di kantor.'})
