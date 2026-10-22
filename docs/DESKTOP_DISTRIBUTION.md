# Distribusi Kioska untuk macOS dan Linux

Rilis 1.0.2 menambahkan paket native kios eOffice pada macOS 14+ Apple Silicon
(`macos-arm64`) dan Intel (`macos-x64`), serta Ubuntu 22.04/24.04 dan Debian
12/13 Intel/AMD 64-bit (`linux-x64`). Windows tetap mengikuti
[distribusi Windows](WINDOWS_DISTRIBUTION.md). Semua paket menyertakan Python
dan dependensi; model resmi tetap diunduh melalui wizard. Database, token,
kunci perangkat dan model produksi tidak masuk paket rilis.

## Pemasangan

### Mac

1. Unduh `Kioska-macos-arm64.dmg` untuk Apple Silicon atau
   `Kioska-macos-x64.dmg` untuk Intel dari GitHub Releases.
2. Buka DMG, buat folder `Applications` di folder pengguna jika belum ada,
   lalu salin `Absensi.app` ke `~/Applications`. Eject DMG sebelum membuka app.
3. Build memakai ad-hoc signing, belum Developer ID/notarization. Jika
   Gatekeeper menolak, gunakan **System Settings → Privacy & Security → Open
   Anyway** hanya untuk paket resmi yang sudah diverifikasi pengelola.
   Jangan menonaktifkan Gatekeeper secara global.
4. Buka app. Terminal menampilkan kode setup lokal dan browser membuka wizard
   pada `http://127.0.0.1:5000`. Pertahankan Terminal selama kios digunakan.
5. Izinkan kamera pada permintaan macOS/browser. Kamera native memakai indeks
   kamera melalui backend OpenCV bawaan; pemilihan nama DirectShow khusus Windows.
   Izin ditolak dapat diperbaiki melalui Privacy & Security → Camera.

Program dan seluruh data privat berada di
`~/Library/Application Support/AbsensiWajahKantor`; app di `~/Applications`
adalah titik masuk tetap yang memilih versi aktif. Menghapus app tidak
menghapus data privat.

### Linux

Gunakan sesi desktop pengguna biasa dengan browser dan terminal. Runtime
OpenCV tetap membutuhkan library sistem; pada Ubuntu/Debian:

```sh
sudo apt-get update
sudo apt-get install libgl1 libglib2.0-0 libgomp1 libxcb-xinerama0 libxcb-cursor0 xdg-utils xterm
```

Ekstrak `linux-x64.tar.gz` ke folder kosong, lalu jalankan `./install.sh` dari
terminal, tanpa `sudo`. Buka **Kioska Absensi Kantor** dari menu aplikasi.
Installer memeriksa inferensi sebelum memasang, menolak instalasi/data lama,
dan tidak mengubah konfigurasi sistem. Kamera native membutuhkan izin akses
perangkat kamera milik pengguna; gunakan pengaturan perangkat/kelompok `video`
distro jika diperlukan, bukan menjalankan kios sebagai root.

Folder privat default adalah `~/.local/share/AbsensiWajahKantor`, atau
`$XDG_DATA_HOME/AbsensiWajahKantor` bila XDG_DATA_HOME absolut ditentukan sebelum
instalasi. Shortcut disimpan di direktori `applications` pada XDG data pengguna.
`launch.sh` pada folder privat selalu memilih versi aktif. Jangan mengubah
XDG_DATA_HOME atau memindahkan folder setelah setup; path model/kunci tersimpan
mengacu ke instalasi tersebut.

## Pembaruan dan pemulihan

Admin lokal memakai **Pembaruan aplikasi**, sama dengan Windows. Pemeriksaan
berjalan saat startup dan setiap 24 jam; pemasangan hanya atas tindakan admin.
Manifest dan signature baru bernama `update-macos-arm64.json`,
`update-macos-x64.json`, dan `update-linux-x64.json` beserta `.sig`.
Windows tetap memakai `update.json`, `update.json.sig`, dan `windows-x64.zip`.
Platform yang salah ditolak meskipun signature valid. Tidak ada URL update
kustom, kredensial perangkat tidak dikirim ke GitHub.

Payload POSIX berupa `<platform>.tar.gz`, mempertahankan izin executable dan
symlink internal. Ekstraksi membatasi ukuran/jumlah entri dan menolak traversal,
link keluar paket, hardlink, device serta nama yang beralias case/Unicode.
Simpan folder privat pada filesystem yang mendukung symlink dan permission POSIX.

Unduhan diverifikasi sebelum downtime. Updater menunggu HTTP, kamera dan sync
berhenti, mencadangkan SQLite/rahasia, lalu memeriksa/memigrasikan kandidat.
Terminal baru membuka versi aktif. Tidak ada force kill. Kegagalan sebelum
aktivasi memulihkan versi/data sebelumnya; setelah aktivasi tidak ada rollback
database otomatis. Update terputus menahan startup sampai pemulihan lokal.

Untuk pemulihan, tutup peluncur dan tunggu sync berhenti. Pada Terminal:

```sh
# Mac: app titik masuk tetap; perintah recover memilih versi aktif lewat current.json
~/Applications/Absensi.app/Contents/MacOS/Absensi recover-active

# Linux: peluncur tetap menerima operasi pemulihan yang sama
~/.local/share/AbsensiWajahKantor/launch.sh recover-active
```

Operasi `reset-admin-password-active` memulihkan password admin melalui prompt
lokal. Keduanya memilih runtime aktif tanpa memasang ulang atau membuka browser.
Jika XDG_DATA_HOME kustom dipakai, gunakan `launch.sh` di folder instalasi itu.
Jangan mengedit `current.json` atau menurunkan versi skema secara manual.
Uninstall manual menghapus shortcut/app/program jika aplikasi sudah berhenti;
data `instance`, `backups` dan `updates` harus dipertahankan untuk pemulihan.

## Build dan rilis

Workflow **Desktop distribution** membangun Windows, macOS ARM64, macOS Intel,
dan Linux x64 pada runner native. PR menjalankan regresi dan build dengan key
uji; tag atau workflow_dispatch membangun produksi dari tag yang sama dengan
`app_version.VERSION`. Satu job rilis menunggu semua build, memverifikasi key
publik setiap artefak terhadap private key, menandatangani manifest tiap platform,
lalu membuat satu draft. Environment/key yang sudah ada, `windows-release`,
`UPDATE_PUBLIC_KEY` dan `UPDATE_SIGNING_KEY`, tetap digunakan. Tidak ada publikasi
otomatis; jangan memasukkan private key ke job build atau PR.

Build POSIX memakai CPython 3.11.17 native, environment terisolasi dan lock hash:

```sh
# Apple Silicon; Intel memakai cpython-3.11.17-macos-x86_64-none,
# Linux memakai cpython-3.11.17-linux-x86_64-gnu.
uv python install cpython-3.11.17-macos-aarch64-none --no-bin --no-registry
uv venv --python cpython-3.11.17-macos-aarch64-none --managed-python .build-venv
# Ganti macos-arm64 dengan macos-x64 atau linux-x64 sesuai proses Python.
uv pip install --python .build-venv/bin/python --require-hashes --only-binary=:all: -r requirements-macos-arm64.lock
# Key produksi dipasok melalui UPDATE_PUBLIC_KEY, bukan flag key uji.
.build-venv/bin/python scripts/build_posix.py --test-key
```

Build menjalankan self-check/inferensi ketiga backend dari **paket yang sudah
diekstrak**, bukan hanya dari source. Mac juga memverifikasi code signature
bundel sebelum membuat DMG. Key uji tidak dapat ditandatangani untuk rilis.

Regenerasi lock menggunakan input bersama yang sudah ada:

```sh
MACOSX_DEPLOYMENT_TARGET=14.0 uv pip compile --python-platform aarch64-apple-darwin --python-version 3.11 requirements-eoffice.txt requirements-windows-build.in --constraint requirements-posix-constraints.in --generate-hashes -o requirements-macos-arm64.lock
MACOSX_DEPLOYMENT_TARGET=14.0 uv pip compile --python-platform x86_64-apple-darwin --python-version 3.11 requirements-eoffice.txt requirements-windows-build.in --override requirements-macos-x64-overrides.in --generate-hashes -o requirements-macos-x64.lock
uv pip compile --python-platform x86_64-manylinux_2_35 --python-version 3.11 requirements-eoffice.txt requirements-windows-build.in --constraint requirements-posix-constraints.in --generate-hashes -o requirements-linux-x64.lock
```

Intel memakai ONNX Runtime 1.23.2, LiteRT 1.0.1, NumPy 1.26.4 dan OpenCV
4.11.0.86 karena wheel resmi Intel untuk runtime baru tidak tersedia dan LiteRT
lama memakai ABI NumPy 1.x. Model, hash, praproses dan ambang tetap sama.
Runtime lama harus melewati pengujian model nyata/paritas, bukan sekadar impor.
Dependency build `ml-dtypes` Intel juga dikunci ke 0.5.4 untuk ABI NumPy 1.x.
Constraint POSIX menahan backports-strenum pada 1.2.8 karena 1.3.1 menyatakan
Python <3.11 meskipun diminta LiteRT secara unconditional.

## Gerbang publikasi stabil

Catat hasil per OS/arsitektur dan hash paket dari draft yang sama:

- Instalasi bersih tanpa Python/Git/Docker; path spasi/Unicode; port bentrok;
  dua peluncur; izin kamera ditolak, dicabut, lalu dipulihkan; restart/login ulang.
- Wizard/token/snapshot/model, kamera native dan browser, antrean offline,
  update A→B, serta keutuhan admin, kunci, konfigurasi, UUID dan byte antrean.
- Jaringan/disk gagal, signature/hash/platform salah, shutdown sync lambat,
  migrasi gagal dan restart pada tiap tahap transaksi; pemulihan tidak
  menimpa data baru setelah aktivasi. Tutup terminal saat sync masih berjalan.
- Inferensi model deployment dan paritas embedding pada setiap target,
  khususnya Intel; uji kamera fisik dan syarat model di [EOFFICE.md](EOFFICE.md).

CI/simulasi Rosetta bukan pengganti Mac Intel fisik, versi OS minimum atau
pengujian kamera. DEB, Linux ARM64, universal2, autostart baru dan notarization
Apple tidak termasuk rilis ini.

## Catatan validasi implementasi — 22 Oktober 2026

- Regresi final pada macOS ARM64: **1.366 lulus, 42 dilewati**. Tes yang
  membutuhkan PowerShell/Windows atau asset model/golden khusus tetap dilewati;
  hasil tersebut tidak dianggap sebagai kelulusan gerbang perangkat/model.
- Kontrol UI JavaScript dan pemeriksaan workflow `actionlint` lulus.
- Paket ARM64 dan Intel memakai CPython privat 3.11.17; keduanya berhasil
  dibangun, diekstrak, menjalankan self-check/inferensi dan lolos verifikasi
  code signature. Instalasi privat, path Unicode/spasi dan penolakan instalasi
  ulang yang mempertahankan data diuji dari executable frozen.
- Intel dijalankan lewat Rosetta pada Apple Silicon, bukan Mac Intel fisik.
- Linux x64 dibangun dan menjalankan inferensi pada container Debian 12 x64;
  installer juga diuji sebagai pengguna biasa, termasuk shortcut, permission
  privat dan perlindungan data saat instalasi ulang. Ini belum menguji sesi
  desktop atau kamera fisik.
- Paket lokal memakai **key uji** dan belum dipublikasikan. Build Windows CI,
  versi OS minimum, kamera fisik, update operasional A→B dan penerimaan model
  tetap harus diselesaikan sebelum publikasi stabil.
