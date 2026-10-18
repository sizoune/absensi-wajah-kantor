# Sistem Absensi Wajah — Diskominfo Kabupaten Tabalong

Absensi otomatis dengan pengenalan wajah: karyawan cukup berjalan melewati
kamera di pintu masuk; sistem mengenali wajah, memastikan bukan foto, lalu
mencatat jam masuk/pulang sesuai jadwal kerja.

## Fitur

- Pendaftaran wajah (5 sampel) dengan pemeriksaan kualitas, konsistensi, dan
  pencegahan data ganda; templat wajah terenkripsi AES-256-GCM.
- Kios: deteksi YuNet, pengenalan SFace, pelacakan lintas frame, anti-foto
  MiniFASNet + tantangan menoleh, pesan di layar kios.
- Aturan absensi: toleransi terlambat, pulang cepat, shift lintas tengah
  malam, pencegahan absensi ganda di aplikasi dan database.
- Admin: dashboard kehadiran, karyawan, jadwal per hari, koreksi dan
  pembatalan beraudit, laporan dan ekspor CSV.
- Operasional: backup/pemulihan teruji, uji performa, daftar periksa keamanan.
- API JSON baca-saja bertoken untuk aplikasi lain (tanpa data wajah).

## Teknologi

Python 3.11, Flask, MySQL/MariaDB (XAMPP), OpenCV 4 (`cv2.dnn`), Waitress.
Model: YuNet (MIT), SFace (Apache-2.0), MiniFASNet (Apache-2.0). Alasan
pemilihan dan lisensi: [docs/MODEL_DECISION.md](docs/MODEL_DECISION.md).

## Dokumentasi

| Dokumen | Isi |
|---|---|
| [docs/INSTALASI.md](docs/INSTALASI.md) | Memasang sistem di komputer kios |
| [docs/OPERASIONAL.md](docs/OPERASIONAL.md) | Rutinitas admin, karyawan baru, koreksi, penanganan gangguan |
| [docs/KEAMANAN.md](docs/KEAMANAN.md) | Daftar periksa sebelum dipakai resmi, penanganan insiden |
| [docs/API.md](docs/API.md) | API baca-saja untuk aplikasi lain: token, endpoint, contoh |
| [docs/BACKUP.md](docs/BACKUP.md) | Backup harian, uji pemulihan, pemulihan bencana, pengosongan data uji |
| [docs/MODEL_DECISION.md](docs/MODEL_DECISION.md) | Pemilihan model, lisensi, kalibrasi ambang dan anti-foto |
| [docs/PERFORMANCE.md](docs/PERFORMANCE.md) | Hasil uji performa |
| [docs/DOCKER.md](docs/DOCKER.md) | Wizard Docker, penyimpanan persisten, kamera browser dan reverse proxy HTTPS |

## Memasang dengan Docker

Jalankan `docker compose up -d --build`, lalu
`docker compose exec camera flask --app docker.start:create_container_app setup-code`.
Buka [setup localhost](http://localhost:5000/setup) untuk membuat admin lokal,
memasukkan token perangkat dan menyiapkan model/kamera. Docker menyiapkan
runtime dan volume persisten; browser tetap perlu dibuka dan diberi izin kamera.
Akses jaringan menggunakan reverse proxy HTTPS instansi. Langkah lengkap,
pemulihan dan pratinjau terpisah ada di [docs/DOCKER.md](docs/DOCKER.md).

## Mulai cepat (pengembangan)

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env      # lalu isi
flask --app app db upgrade
flask --app app models-download
pytest
```
Untuk pemakaian resmi ikuti [docs/INSTALASI.md](docs/INSTALASI.md); jangan
memakai `flask run`.
