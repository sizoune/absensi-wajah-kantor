# Uji kamera browser di Mac dengan Docker

Jalankan dari folder `camera` ini, dengan Docker Engine dan Docker Compose yang
aktif. Python di host dan pemetaan perangkat USB tidak diperlukan. Kamera
dibuka oleh browser Mac setelah tombol **Aktifkan kamera** ditekan.

## Pratinjau lokal

```sh
docker compose up
```

Buka [kios localhost](http://localhost:5000/kiosk/), tekan **Aktifkan kamera**,
lalu izinkan kamera di browser/macOS. Tekan **Matikan kamera** untuk melepas
kamera. Default menampilkan **Uji pratinjau kamera — tidak mencatat absensi**:
JPEG diuji melalui browser → Flask, tanpa pengenalan, enrollment atau pencatatan
absensi. Tidak perlu file env, model, kunci biometrik, token atau server e-office.
Browser tidak meminta izin kamera sebelum tombol ditekan.

Build pertama mengunduh image Python 3.11 dan seluruh dependensi yang dikunci di
`requirements-eoffice.txt` (termasuk `requirements.txt`). Bobot model dan data
tidak diunduh/dimasukkan ke image. Sesudah mengubah sumber, gunakan
`docker compose up --build`. Versi paket tidak diganti untuk membuat build lolos.

Jika port 5000 terpakai:

```sh
CAMERA_HOST_PORT=5001 docker compose up
```

Buka `http://localhost:5001/kiosk/`. Publish selalu pada `127.0.0.1`; gunakan
browser di Mac yang sama, bukan alamat LAN. Izin kamera/HTTPS untuk perangkat
lain tidak termasuk jalur uji ini.

Pemeriksaan dan penghentian:

```sh
docker compose ps
curl --fail http://localhost:5000/health
docker compose logs --tail=50 camera
docker compose down
```

Ctrl+C pada `up` juga menghentikan layanan. Startup memakai satu proses
Waitress sebagai PID 1. SIGTERM/SIGINT menjalankan cleanup `BrowserCamera.close`;
batas join Waitress dan kamera berjumlah paling lama sekitar 12 detik, dengan
grace period Compose 20 detik. Tidak ada proses `kiosk-run` kedua.

Volume `eoffice-camera_instance` menyimpan `secret_key`, SQLite `app.sqlite3`,
status/kunci proses kios, dan nanti antrean `eoffice.sqlite3` bila integrasi
diaktifkan. `secret_key` dibuat sekali dengan izin 0600 dan dipakai lagi setelah
restart/recreate. File yang kosong/rusak menghentikan startup; jangan hapus untuk
memaksa startup berhasil. Pulihkan cadangan yang benar. Tidak ada reset, rekey,
rotasi atau migrasi destruktif otomatis. Startup hanya membuat metadata
`users`/`audit_logs` yang belum ada; akun admin dan tabel absensi standalone tidak
dibuat. Ini bukan instalasi aplikasi admin standalone lengkap.

`docker compose down` mempertahankan volume. **Jangan tambahkan `-v` dan jangan
hapus volume** bila berisi data/antrean. Backup antrean dan kunci integrasi
mengikuti [EOFFICE.md](EOFFICE.md), saat layanan berhenti bersih.

## Integrasi e-office staging: persiapan satu kali

Jalur ini dapat mencatat absensi pada server yang dipilih. Selesaikan prasyarat
di [EOFFICE.md](EOFFICE.md) dengan operator sebelum mengaktifkannya: API HTTPS
staging eksternal yang sudah dimigrasikan dan diizinkan, perangkat/token staging,
ID instansi serta policy/enrollment autentik, TLS/NTP, model deployment yang
sesuai hash, dan keyring enkripsi lokal. Compose tidak membuat API/database
e-office baru atau melakukan enrollment ulang.

1. Sediakan folder model **di luar proyek**, berisi file dengan nama berikut:
   `face_detection_yunet_2023mar.onnx`, `MiniFASNetV2.onnx`,
   `MiniFASNetV1SE.onnx`, `glintr100.onnx`, dan `mobilefacenet.tflite`.
   Hash/dimensi tetap diperiksa oleh kode yang sudah ada; jangan mengedit hash,
   mengganti model, atau menurunkan ambang. SFace tidak dipakai oleh integrasi ini.
2. Sediakan keyring lokal valid **di luar proyek**. Untuk antrean yang sudah ada,
   gunakan kunci aslinya. Pembuatan awal keyring baru mengikuti prosedur eksplisit
   di EOFFICE.md saat layanan berhenti; startup tidak pernah membuat keyring
   biometrik. Simpan backup kunci terpisah dari backup antrean. Pastikan UID 1000
   di container dapat membaca model/keyring dengan hak akses operator yang tepat.
3. Buat konfigurasi khusus Docker, lalu edit nilainya:

   ```sh
   cp -n docker/compose.env.example docker/compose.env
   chmod 600 docker/compose.env
   ```

   Isi `EOFFICE_BASE_URL` dengan URL HTTPS staging dan `EOFFICE_DEVICE_TOKEN`
   dengan token perangkat berizin. Isi `EOFFICE_HOST_MODEL_DIR` dan
   `EOFFICE_HOST_KEY_FILE` dengan path absolut yang benar. Contoh domain `.invalid`
   dan token contoh bukan credential yang dapat dipakai.

`docker/compose.env` diabaikan Git dan konteks build. Jangan menyalin
`production.env`, menaruh kunci dalam checkout, atau mencetak konfigurasi berisi
token ke log/laporan. Default Compose tidak memuat file ini maupun meneruskan
variabel integrasi/SECRET_KEY/DATABASE_URL dari shell. Root `.env`,
`production.env`, model, instance, dataset, cache dan fixture tidak masuk image
karena konteks build memakai allowlist sumber.

Validasi tanpa mencetak token, lalu jalankan konfigurasi integrasi secara sengaja:

```sh
docker compose --env-file docker/compose.env -f compose.yaml -f docker/compose.eoffice.yaml config --quiet
docker compose --env-file docker/compose.env -f compose.yaml -f docker/compose.eoffice.yaml up --build
```

Override memasang model dan **satu file** keyring secara read-only. Path sumber
yang tidak ada tidak dibuat otomatis. Layanan memakai `ATTENDANCE_MODE=eoffice`
dan `LIVENESS_MODE=passive`. Prasyarat konfigurasi/model/kunci yang hilang atau
tidak cocok menghentikan startup; tidak ada fallback pengenalan palsu atau
penurunan ke mode pratinjau. Sync antrean integrasi dimulai saat layanan start,
termasuk retry pending setelah restart, sebelum kamera diaktifkan. Tidak ada
owner/challenge/capture sebelum tombol ditekan. Putus koneksi API mempertahankan
antrean terenkripsi; putus browser menghentikan capture tanpa menyimpan frame
untuk dikirim ulang.

Gunakan flag integrasi yang sama saat menghentikan:

```sh
docker compose --env-file docker/compose.env -f compose.yaml -f docker/compose.eoffice.yaml down
```

`docker compose up` berikutnya kembali ke pratinjau. Antrean integrasi tetap ada
tetapi tidak disinkronkan sampai konfigurasi integrasi dinyalakan lagi. Jangan
mengganti perangkat/token/kunci untuk mengirim antrean lama milik perangkat lain.

## Batas uji dan penanganan gangguan

- Kamera tidak ditemukan/sedang dipakai/izin ditolak: tutup aplikasi lain yang
  memakai kamera, periksa izin browser/macOS, lalu tekan aktivasi lagi. Tab yang
  disembunyikan, koneksi browser gagal, atau track berakhir menghentikan capture;
  owner yang kehilangan respons dilepas otomatis setelah idle sekitar 15 detik.
- Health `/health` memeriksa aplikasi dan SQLite lokal. Status koneksi, model,
  penyimpanan antrean dan waktu e-office terlihat terpisah di kios; health 200
  bukan bukti keberhasilan attendance atau kelayakan model.
- Satu browser menjadi owner. JPEG baseline dibatasi sampai 1280×720, satu frame
  dalam proses dan maksimal 5 fps. Waitress memakai `max_request_body_size=524288`
  dan `inbuf_overflow=1048576`, sehingga buffer input tidak melimpah ke file.
  Waitress 3.0.2 menolak ukuran **mulai 524288 byte** (dan overhead chunked ikut
  dihitung); gunakan JPEG lebih kecil dari batas itu. Lihat
  [argumen Waitress](https://docs.pylonsproject.org/projects/waitress/en/stable/arguments.html).
- Challenge harus diambil sebelum capture, sekali pakai dan berlaku 3 detik.
  Timestamp transport adalah perkiraan akuisisi, bukan bukti clock sensor kamera.
  Respons frame hanya mengakui transport, bukan sukses absensi. CPU lambat dapat
  membuat frame ditolak stale; jangan memperpanjang deadline atau menurunkan policy
  untuk membuat trial tampak berhasil.

Build ARM64, HTTP, codec/stream sintetis Chromium, restart dan shutdown diuji pada
Mac ini. Izin/perangkat kamera Mac fisik, bobot deployment, credential/API staging,
kalibrasi pencahayaan dan penerimaan fisik/spoof tetap perlu uji berizin.
**Belum ada penerimaan untuk penggunaan resmi**; lihat
[catatan QA](qa/2026-10-14-eoffice-camera-acceptance.md).
