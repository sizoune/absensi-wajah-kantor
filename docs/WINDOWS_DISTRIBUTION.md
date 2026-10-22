# Distribusi Windows dan pembaruan

## Spesifikasi yang disepakati

Windows 10/11 x64, kios eOffice, installer lengkap PyInstaller onedir + Inno
Setup. Tidak memerlukan Python, Git, Docker atau pip pada perangkat. Model
resmi tetap disiapkan melalui wizard. Dukungan Mac/Linux dijelaskan di
[distribusi desktop](DESKTOP_DISTRIBUTION.md). Standalone, MySQL, migrasi ZIP lama
dan rollout per kantor di luar cakupan installer.

Program disimpan di `%LOCALAPPDATA%\AbsensiWajahKantor\versions\<versi>`;
`instance`, `backups`, dan `updates` persisten dan privat. `current.json`
menentukan versi aktif. Shortcut tetap membuka peluncur lokal. Uninstall
mempertahankan data. Instalasi ZIP lama ditolak tanpa modifikasi.

GitHub Releases menyediakan installer, ZIP lengkap, update.json dan tanda
tangan Ed25519. Public key dibundel; private key hanya di lingkungan rilis.
Manifest memuat format_version, version, platform, url, size, sha256,
min_updater_version dan notes. Sumber tetap, HTTPS, versi stabil lebih tinggi,
hash/ukuran diverifikasi, arsip berbahaya ditolak. Token perangkat tidak
dikirim ke GitHub. Periksa saat siap dan setiap 24 jam, pasang atas tindakan
admin lokal dengan CSRF. Tidak ada URL update yang dapat diisi browser.

Endpoint: GET /setup/updates, GET /setup/updates/status,
POST /setup/updates/check, POST /setup/updates/install.
Tahap: idle, checking, available, downloading, verifying, stopping,
backing_up, applying, restarting, completed, failed.

Satu transaksi update. Unduh/verifikasi sebelum downtime. Updater terpisah
menunggu penghentian HTTP, kamera, retry dan sync selama paling lama 60 detik;
tidak force kill. Tolak benturan perubahan pengaturan/model. Cadangkan dan
verifikasi seluruh SQLite dan rahasia setelah semua penulis berhenti. Antrean
boleh berisi event, UUID/byte/response harus utuh. Skema kios memiliki versi
sendiri, tidak menjalankan rantai Alembic standalone. Kandidat menjalankan
migrasi dan self-check tanpa capture/sync sebelum diaktifkan.

Jurnal atomik menahan startup saat transaksi terputus. Sebelum layanan baru
diaktifkan, kegagalan mengembalikan database dan versi lama. Setelah aktivasi
layanan normal, jangan rollback database otomatis. Simpan versi sebelumnya
dan cadangan terakhir. Skema asing/lebih baru ditolak.

Build Windows dari dependency/tool pin; uji inferensi fixture OpenCV, ONNX
Runtime dan LiteRT dari executable. Regresi penuh berjalan pada PR, sedangkan
build produksi melewatinya; self-check dan inferensi executable tetap wajib.
PR membangun paket uji; push tag rilis membangun keempat target, menandatangani
metadata, lalu otomatis mempublikasikan release setelah seluruh aset terunggah.
Selesaikan smoke test Windows bersih, kamera fisik, login/restart dan kegagalan
update sebelum mendorong tag produksi. Sertifikat Authenticode aplikasi
terpisah dari signature updater.

## Catatan pelaksanaan

- Implementasi dilakukan dalam checkout saat ini; perubahan `.codegraph/`
  milik pengguna tidak disertakan.
- Baseline proyek belum menyediakan virtualenv atau pytest lokal. Lingkungan
  pengujian dibuat terpisah; build Windows dan kamera fisik tetap merupakan
  gerbang rilis yang harus dilakukan pada Windows.
- Review terpisah menemukan dan memperbaiki path kunci pada restart, flush
  cadangan pada Windows, benturan pemulihan password dengan updater, serta
  kepemilikan launcher selama native sync berhenti. Semuanya memiliki tes
  regresi. Kontrol browser juga diuji untuk sesi/CSRF kedaluwarsa dan perubahan
  versi setelah restart.

## Build pertama

1. Buat pasangan kunci Ed25519 satu kali pada mesin pengelola, di luar repo.
   Simpan private seed 32 byte dan public key 32 byte dalam bentuk base64.
   Backup private key secara aman; jangan menggunakan key uji dari build PR.
   Contoh berikut membuat dua berkas baru dengan izin privat tanpa mencetak
   isinya. Jangan jalankan pada runner PR.

   ```python
   import base64, os
   from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
   key = Ed25519PrivateKey.generate()
   for name, value in [('update-private.txt', key.private_bytes_raw()),
                       ('update-public.txt', key.public_key().public_bytes_raw())]:
       with os.fdopen(os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'wb') as out:
           out.write(base64.b64encode(value))
   ```

2. Isi repository variable `UPDATE_PUBLIC_KEY` dengan isi berkas public key.
   Buat GitHub Environment `windows-release`, batasi ke tag rilis tanpa
   required reviewers agar publikasi otomatis. Isi secret `UPDATE_SIGNING_KEY` dengan
   private seed base64. Private key tidak dibutuhkan job build atau PR.
3. Pada Windows x64 dengan CPython 3.11.17, jalankan:

   ```powershell
   python -m venv .build-venv
   .build-venv\Scripts\Activate.ps1
   python -m pip install --require-hashes --only-binary=:all: -r requirements-windows.lock
   $env:UPDATE_PUBLIC_KEY = Get-Content <berkas-public-key> -Raw
   .\scripts\build_windows.ps1
   ```

   `dist\release\AbsensiSetup.exe` adalah installer pertama;
   `windows-x64.zip` adalah payload pembaruan lengkap. Build menyertakan lisensi,
   metadata paket, dan fixture inferensi sintetis; tidak menyertakan token,
   database, rahasia perangkat atau model biometrik produksi. Build gagal bila
   lisensi, hash tool, signature tool atau self-check tidak lolos. Compiler
   Inno Setup 6.7.3 dan VC Redistributable dipin pada `distribution/build-tools.json`.
   Pengelola perlu meninjau ketentuan lisensi Inno Setup untuk penggunaannya.
4. Ubah `app_version.VERSION` dan `distribution/RELEASE_NOTES.md` untuk rilis
   berikutnya. Push tag `v<VERSION>` pada commit yang sudah ditinjau untuk
   memulai build produksi; workflow juga dapat dijalankan manual dengan tag
   tersebut. Workflow membangun keempat target desktop, mengunggah aset ke draft
   sementara, lalu otomatis mempublikasikannya sebagai **Latest**. Key uji PR tidak dapat ditandatangani
   oleh proses rilis. Jangan mengganti public key antarversi tanpa rancangan
   rotasi kepercayaan; updater versi 1 menggunakan pasangan kunci yang sama.

Lock dependency diperbarui dengan:

```sh
uv pip compile --python-platform x86_64-pc-windows-msvc --python-version 3.11 requirements-eoffice.txt requirements-windows-build.in --generate-hashes -o requirements-windows.lock
```

## Operasional pengguna

- Jalankan installer dengan akun Windows yang akan memakai kios. Runtime VC
  dapat meminta UAC; aplikasi sendiri berjalan sebagai pengguna biasa. Jika
  diminta restart, restart Windows dan jalankan installer kembali.
- Buka shortcut **Absensi Wajah Kantor**, lalu selesaikan wizard. Internet tetap
  diperlukan untuk token perangkat, snapshot dan model resmi. Model tidak ikut
  ditaruh di GitHub Releases. Server tetap hanya pada `127.0.0.1:5000`.
- Masuk sebagai admin lokal dan buka **Pembaruan aplikasi**. Versi diperiksa
  saat aplikasi siap dan setiap 24 jam; kegagalan jaringan tidak menghentikan
  absensi. Tombol **Unduh & pasang update** memulai satu transaksi. Tunggu
  halaman tersambung kembali; jika sesi/token CSRF kedaluwarsa, muat ulang dan
  masuk kembali.
- Pada versi 1, sumber unduhan hanya GitHub/CDN GitHub melalui HTTPS langsung;
  proxy berautentikasi dan URL kustom tidak didukung. Paket penuh diunduh,
  bukan delta. Gagal unduh dapat dicoba lagi setelah memeriksa versi.
- Uninstall memerlukan aplikasi/updater sudah berhenti dan mempertahankan
  `instance`, `backups`, serta jurnal `updates`. Jangan menghapus rahasia untuk
  memaksa startup. Installer menolak data yang tidak dikenalnya; pemindahan
  instalasi lama bukan bagian dari installer ini.

## Pemulihan lokal

Jurnal transaksi berada di `updates\journal.json`. Rahasia dan token tidak
dicatat di sana. Unduhan/verifikasi gagal tidak mengubah data aktif. Berkas
kandidat dan cadangan tidak dibersihkan otomatis pada versi 1 agar bukti
pemulihan tetap tersedia; pengelola dapat mengarsipkan transaksi lama setelah
memastikan bukan versi aktif/sebelumnya atau cadangan yang sedang dibutuhkan.

Jika startup menolak transaksi terputus, tutup semua peluncur lalu jalankan:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$env:LOCALAPPDATA\AbsensiWajahKantor\launch.ps1" -Recover
```

Sebelum titik aktivasi layanan baru, perintah memulihkan cadangan yang
terverifikasi dan penunjuk versi sebelumnya. Setelah titik aktivasi, perintah
hanya menjalankan self-check versi aktif, mempertahankan database terbaru,
dan membuka blokir jika pemeriksaan lolos. Tidak memaksa proses lain berhenti.
Setelah selesai, buka shortcut kembali. Jurnal/backup rusak atau kunci hilang
memerlukan pengelola dan cadangan asli; jangan mengedit penunjuk versi atau
menurunkan skema secara manual.

Pemulihan password admin lokal tersedia melalui `launch.ps1 -ResetAdminPassword`.
Tutup peluncur sebelum menjalankannya. Kode setup dan prompt password hanya
ditampilkan di konsol lokal, bukan log.

## Validasi sebelum tag rilis

Catat hasil pemeriksaan pada issue #13, beserta versi Windows, arsitektur,
kamera, versi paket dan hash installer. Gunakan artefak build uji dan
selesaikan pemeriksaan sebelum mendorong tag produksi:

- Windows 10/11 bersih tanpa Python/Git/Docker; nama akun/path dengan spasi dan
  Unicode; UAC ditolak, runtime perlu restart, port 5000 dipakai, startup ganda.
- Kamera USB native dan kamera browser: wizard, preview, capture, cabut/pasang
  kamera, login ulang dan restart Windows. Kalibrasi model/perangkat tetap
  mengikuti panduan model; fixture ReLU hanya memeriksa backend native.
- Pasang versi A, selesaikan setup, sisakan antrean offline, lalu update ke B.
  Periksa admin, token perangkat, kunci, snapshot, konfigurasi, model, UUID,
  byte event dan response sebelum/sesudah; sinkronisasi ulang tidak menggandakan
  event.
- Putuskan jaringan dan isi disk saat mengunduh; kunci berkas; lambatkan sync;
  gagalkan migrasi/self-check. Pastikan tidak ada force kill dan aplikasi/data
  lama dapat dipulihkan sebelum aktivasi.
- Restart Windows saat stopping, backing_up, applying dan restarting. Startup
  harus tertahan sampai pemulihan eksplisit. Setelah layanan baru aktif, data
  baru tidak boleh dikembalikan ke snapshot lama.
- Paket/manifest/signature diubah harus ditolak. Uji admin kedaluwarsa,
  non-admin, CSRF salah dan dua permintaan update bersamaan.

Tag rilis memicu publikasi otomatis setelah build/signing/unggahan lulus.
Build CI dan tes unit tidak menggantikan pengujian kamera fisik atau sertifikat Authenticode
Windows. Tanpa sertifikat Authenticode aplikasi, SmartScreen dapat menampilkan
peringatan walaupun signature updater valid.
