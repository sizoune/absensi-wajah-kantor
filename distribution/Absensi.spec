# Build on Windows using requirements-windows.lock.
from pathlib import Path
from PyInstaller.utils.hooks import collect_all, copy_metadata
from importlib.metadata import distributions

root = Path(SPECPATH).parent
datas = [(str(root / 'templates'), 'templates'), (str(root / 'static'), 'static'),
         (str(root / 'distribution/update-public-key.bin'), 'distribution'),
         (str(root / 'build/inference'), 'inference'), (str(root / 'build/licenses'), 'licenses')]
binaries, hidden = [], []
for package in ('onnxruntime', 'ai_edge_litert', 'skimage', 'pygrabber', 'comtypes'):
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
