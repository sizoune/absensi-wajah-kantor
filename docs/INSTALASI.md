# Memasang aplikasi eOffice pada Windows

Untuk Mac Apple Silicon/Intel dan Linux x64, ikuti
[panduan distribusi desktop](DESKTOP_DISTRIBUTION.md).

Untuk distribusi installer lengkap dan update GitHub, ikuti
[panduan distribusi Windows](WINDOWS_DISTRIBUTION.md). Installer baru tidak
menimpa instalasi ZIP lama. Bagian di bawah menjelaskan jalur bootstrap ZIP
yang tetap tersedia untuk pengembangan dan pemulihan instalasi lama.

Gunakan Windows 10/11 x64, Microsoft Edge/browser modern, kamera USB, dan
koneksi internet untuk unduhan resmi serta API eOffice. Instalasi baru memakai
SQLite lokal; Python, Git, XAMPP, file `.env`, dan path teknis tidak perlu
disiapkan operator. Bootstrap memakai **Windows PowerShell 5.1 64-bit** bawaan
Windows, dengan skrip ASCII dan kebijakan eksekusi hanya pada proses pemasangan.

1. Ekstrak ZIP rilis yang diberikan pengelola, lalu klik dua kali
   **`scripts\pasang_windows.bat`**. Tunggu jendela pemasangan tetap terbuka.
2. Bootstrap memilih folder privat `%LOCALAPPDATA%\AbsensiWajahKantor`,
   menyiapkan CPython **3.11.17 x64** privat dan virtualenv, memasang dependensi
   yang dikunci beserta pemeriksaan impor DLL, lalu menyiapkan penyimpanan dan
   rahasia melalui bootstrap aplikasi yang sudah ada. Tidak mengubah PATH atau
   mendaftarkan Python pada registri global.
3. Jika runtime Visual C++ belum cukup baru, installer resmi Microsoft dapat
   meminta **izin administrator/UAC**. Pembatalan, kegagalan, atau permintaan
   restart menghentikan pemasangan. Bila diminta, restart Windows lalu jalankan
   `pasang_windows.bat` kembali. Tidak ada restart paksa atau kelanjutan ke capture
   setelah instalasi gagal.
4. Peluncur membuka browser dan menampilkan **kode setup hanya pada jendela
   lokalnya**. Salin ke formulir setup; kode tidak ada dalam URL, shortcut, atau
   log rutin. Jangan mengalihkan keluaran peluncur ke file/log.
5. Buat admin lokal, masukkan token perangkat dari admin server, lalu pilih SKPD
   dan cakupan perangkat. Ikuti penyiapan model dan snapshot. Pilih nama kamera
   yang benar dari daftar perangkat terdeteksi dan jalankan preview/pemeriksaan.
   Preview tidak mencatat absensi dan tidak memerlukan kecocokan wajah.
6. Selesaikan wizard. Snapshot valid yang kosong dapat menyelesaikan setup,
   tetapi capture tetap ditahan sampai enrollment, model, kamera, izin integrasi,
   dan waktu tepercaya siap. Model tidak tersedia/tidak cocok memerlukan sumber
   distribusi resmi dari pengelola; jangan memakai bobot pengganti atau fallback
   standalone. Tidak ada persetujuan aktivasi tambahan setelah seluruh syarat siap.

Biarkan jendela peluncur terbuka selama aplikasi dipakai. **Ctrl+C** meminta
penghentian HTTP, kamera, dan sinkronisasi dengan bersih; tunggu selesai sebelum
backup atau reinstall. Jangan memaksa penghentian proses saat menulis/mengirim
antrean. Server hanya mendengarkan `127.0.0.1:5000`. Port yang sedang dipakai
aplikasi lain adalah kegagalan startup; respons dari server lain tidak dianggap
berhasil.

## Login Windows, restart, dan pemulihan

Pemasangan berhasil membuat satu shortcut **Absensi Wajah Kantor.lnk** pada
folder Startup pengguna saat ini. Shortcut menunjuk runtime privat dan peluncur
yang sama, dengan folder kerja instalasi. Aplikasi dimulai **setelah pengguna
login Windows**, sehingga kamera interaktif tersedia; bukan service sebelum
login. Menjalankan installer lagi memperbarui shortcut yang sama.

- Untuk membuka manual, gunakan `scripts\jalankan_aplikasi.bat` di folder
  instalasi. Kunci peluncur mencegah dua pohon server/kamera dari peluncur aktif.
  Native capture tetap memakai `jalankan_kios.bat` dengan retry 10 detik dan
  penguncian kamera yang ada. Hold perubahan cakupan/kredensial tetap dihormati.
- Untuk menonaktifkan startup, buka **Win+R → `shell:startup`** dan hapus shortcut
  bernama di atas. Pada instalasi lama, hapus tiga shortcut manual ke
  `jalankan_server.bat`, `jalankan_kios.bat`, dan `buka_kios.bat` agar tidak
  menjalankan jalur lama bersamaan.
- Reinstall memakai folder lama bila `.env`, `instance`, `.venv`, atau
  `model_files` sudah ada di folder sumber. ZIP rilis baru juga dapat memperbarui
  instalasi pada folder privat bawaan. Konfigurasi, admin, kunci, model valid,
  backup, serta byte/UUID antrean tidak dihapus atau dibuat ulang. Path eksternal
  yang tersimpan tetap dipakai. Tutup peluncur lama dahulu.
- Unduhan/dependensi yang terputus dapat dilanjutkan dengan menjalankan installer
  lagi. Virtualenv kompatibel dipakai kembali. Runtime lama yang bukan CPython
  3.11.17+ x64 ditolak tanpa dihapus; pengelola perlu menyiapkan peningkatan runtime
  terpisah dengan backup asli, bukan menghapus `.venv`, database, atau kunci.
- Setelah setup selesai, peluncur tidak menerbitkan kode setup lagi. Password
  admin dipulihkan melalui terminal lokal dengan
  `.venv\Scripts\python.exe -m flask --app app reset-admin-password`; lihat
  [pemulihan admin](KEAMANAN.md#pemulihan-password-admin). Rahasia/kunci yang hilang
  atau rusak memerlukan cadangan asli; jangan membuat ulang untuk memaksa startup.

Penyimpanan bawaan dibatasi ACL ke pengguna pemasang dan SYSTEM. Pertahankan
ACL privat pada backup dan lokasi kunci/data eksternal yang dikelola sebelumnya.
Ikuti [operasi eOffice](EOFFICE.md) untuk antrean/kunci integrasi; enrollment lokal
ulang tidak diizinkan.

## Verifikasi rilis dan batas lingkungan

Bootstrap memverifikasi SHA256 arsip **uv 0.12.24** yang dipin; uv memverifikasi
hash build CPython terpilih sebelum memasangnya. Runtime Visual C++ diambil dari
[tautan resmi Microsoft](https://learn.microsoft.com/en-us/cpp/windows/latest-supported-vc-redist)
dan memerlukan tanda tangan Authenticode Microsoft yang valid sebelum dieksekusi.
Versi terpasang dibandingkan dengan versi installer resmi; versi sama/lebih baru
tidak meminta UAC berulang. Python tidak dipasang memakai installer PSF lama.
Pembaruan pin runtime perlu pemeriksaan ulang hash/artifact resmi, resolusi wheel
Windows, regresi, dan smoke test Windows bersih.

Pengujian otomatis mencakup HTTP wizard/login, kode lokal, lock peluncur, kegagalan
port, drain native, hold pengaturan, integritas reinstall/restart, checksum,
publisher/UAC, shortcut, dan grammar PowerShell. Batas Windows/network/process
tertentu dikendalikan pada macOS; ini **bukan bukti eksekusi installer Windows,
ACL/UAC/Startup nyata, impor DLL Windows, atau kamera fisik**. Sebelum rilis
operasional, uji Windows bersih dengan unduhan/runtime/DLL sebenarnya, pilih dan
preview kamera USB nyata, selesaikan wizard, pulihkan password, restart/login,
reinstall, serta bandingkan kunci dan byte/UUID antrean. Bobot deployment,
kalibrasi/liveness, TLS/API berizin, dan penerimaan fisik tetap perlu validasi
lapangan. Jangan melaporkan simulasi sebagai kelulusan hardware.

## Referensi instalasi standalone lama

Bagian berikut dipertahankan untuk instalasi MySQL/standalone yang sudah ada.
Gunakan alur wizard di atas untuk perangkat eOffice baru; langkah keygen,
enrollment, dan backup standalone tidak berlaku untuk antrean integrasi.

## 1. Perangkat

| Komponen | Kebutuhan |
|---|---|
| Komputer | Windows 10/11 64-bit, CPU 4 inti, RAM 8 GB, SSD |
| Kamera | Logitech C270 HD (USB), dipasang setinggi wajah, ±0,7–1 m dari posisi karyawan |
| Pencahayaan | Cahaya merata dari depan wajah; hindari jendela terang tepat di belakang karyawan |
| Media terpisah | Flashdisk/hard disk eksternal khusus untuk salinan kunci biometrik |

## 2. Program yang dipasang

1. **CPython 3.11.17+ x64** yang telah diverifikasi pengelola. Instalasi baru
   menggunakan bootstrap privat di atas, tanpa perubahan PATH global.
2. **XAMPP** (cukup komponen MySQL/MariaDB dan phpMyAdmin).
3. **Git for Windows**.
4. **Microsoft Edge** (sudah ada di Windows) untuk tampilan kios.

## 3. Mengambil kode

```powershell
cd D:\
git clone https://github.com/HaiqalGITuru/absensi-wajah-kantor.git
cd absensi-wajah-kantor
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```
Jika aktivasi PowerShell ditolak, gunakan `.venv\Scripts\python.exe -m pip`
atau `.venv\Scripts\python.exe -m flask` langsung, tanpa mengubah kebijakan global.

## 4. Database

1. XAMPP Control Panel → **Start** MySQL. Jalankan XAMPP Control Panel sebagai administrator, lalu centang **Svc** (jalankan sebagai
   service) agar MySQL otomatis menyala saat Windows menyala.
2. **Isi password root MySQL** (bawaan XAMPP kosong): phpMyAdmin → User
   accounts → `root` (localhost) → Change password.
3. Buat database: phpMyAdmin → **New** → nama `absensi_wajah`, collation
   `utf8mb4_unicode_ci`.
4. Buat pengguna khusus: User accounts → **Add user account** → nama
   `absensi_app`, host `localhost`, password acak minimal 16 karakter.
   Jangan beri hak global; lalu buka tab **Database** pengguna itu dan beri
   **ALL PRIVILEGES** hanya untuk `absensi_wajah`.

## 5. File .env

Salin `.env.example` menjadi `.env`, lalu isi. Yang wajib:

| Kunci | Isi |
|---|---|
| `APP_ENV` | `production` |
| `SECRET_KEY` | hasil `python -c "import secrets; print(secrets.token_hex(32))"` |
| `DB_USER` / `DB_PASSWORD` | `absensi_app` dan password langkah 4 |
| `OFFICE_TIMEZONE` | `Asia/Makassar` |
| `BIOMETRIC_KEY_FILE` | di luar folder proyek, mis. `D:\absensi-secrets\biometric_keys.json` |
| `BACKUP_DIR` | di luar folder proyek, mis. `D:\absensi-backup` |
| `CAMERA_NAME` | `C270` (lihat langkah 7) |
| `FACE_MATCH_THRESHOLD` | `0.60` (hasil kalibrasi; lihat docs/MODEL_DECISION.md) |
| `LIVENESS_MODE` | `passive+challenge` |

`.env` berisi rahasia: tidak boleh dikirim ke siapa pun dan tidak ikut ke Git.

## 6. Menyiapkan aplikasi

```powershell
flask --app app db upgrade              # membuat tabel
flask --app app models-download         # model YuNet, SFace, MiniFASNet (±41 MB)
flask --app app models-check            # harus 4 baris OK
flask --app app biometric-keygen        # kunci enkripsi templat wajah (sekali)
flask --app app backup-key E:\kunci-absensi   # salin kunci ke media terpisah
flask --app app create-admin            # akun admin pertama
pytest                                  # semua tes harus lulus
```

## 7. Kamera

```powershell
flask --app app camera-scan             # daftar kamera beserta namanya
flask --app app camera-check --seconds 10
```
- Pastikan baris *Kamera:* menunjukkan C270, bukan kamera laptop/virtual.
- Jika `camera-check` menyarankan `CAMERA_FORMAT_STRATEGY=...`, tambahkan ke
  `.env` agar kamera terbuka lebih cepat.
- `flask --app app face-check` untuk memeriksa wajah terdeteksi dan
  kualitasnya cukup pada posisi kios.

## 8. Data awal

1. Login di `http://127.0.0.1:5000/login` (jalankan server dulu, langkah 9).
2. **Jadwal kerja**: buat jadwal resmi (nama jelas, jam masuk/pulang tiap hari,
   termasuk Jumat, toleransi terlambat).
3. **Karyawan**: tambahkan dengan **NIP sebagai kode karyawan**.
4. Daftarkan wajah tiap karyawan (lihat docs/OPERASIONAL.md bagian
   *Karyawan baru*), lalu `flask --app app face-similarity-report`.

## 9. Menjalankan sistem

| Skrip (folder `scripts`) | Fungsi |
|---|---|
| `jalankan_server.bat` | Server web produksi (Waitress) di `http://127.0.0.1:5000` |
| `jalankan_kios.bat` | Layanan kamera yang mencatat absensi; menyala ulang otomatis jika berhenti |
| `buka_kios.bat` | Halaman kios layar penuh di Edge (keluar: Alt+F4) |
| `backup_harian.bat` | Backup harian (dijadwalkan lewat Task Scheduler) |

**Jangan memakai `flask run` untuk pemakaian resmi**: itu server
pengembangan. Server hanya mendengarkan `127.0.0.1` sehingga halaman admin
tidak bisa dibuka dari komputer lain di jaringan.

### Menyala otomatis saat login Windows

Gunakan satu peluncur dan shortcut dari bootstrap di atas, termasuk untuk
instalasi lama yang kompatibel. Jadwal `backup_harian.bat` tetap terpisah
(docs/BACKUP.md). Jangan menambahkan tiga shortcut server/kamera/browser lagi.

## 10. Pemeriksaan akhir

```powershell
flask --app app security-check
```
Semua butir otomatis harus **OK**, lalu selesaikan daftar periksa manual
(docs/KEAMANAN.md). Setelah itu jalankan uji performa singkat:
`flask --app app perf-check --camera-seconds 30`.
