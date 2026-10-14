# QA integrasi kamera e-office — 14 Oktober 2026

**Keputusan: NOT ACCEPTED untuk aktivasi resmi.** Implementasi Tasks 1–10 dan tes konsistensi otomatis bukan penerimaan model/hardware. Tidak ada production deployment, enrollment ulang, perubahan threshold untuk memaksa lulus, atau uji fisik yang dilakukan di sesi ini. Catatan ini dapat dibaca tanpa log workspace privat.

## Lingkungan dan batas pembuktian

Host macOS, Python 3.11.17 dalam `.venv`, Node test runner dan MariaDB 11.4 container `eoffice-camera-mysql` pada `127.0.0.1:33317`, database `kiosk_test_camera`. Fixture memvalidasi prefix sebelum koneksi dan memakai advisory mutex schema. Semua pegawai/vector/secret/kunci fixture sintetis. Application `.env` dan production DB tidak diimpor. Repo API harus menjadi sibling `../api` dari CAM; instal dependency API sesuai lockfile terlebih dahulu.

Tes `test_eoffice_end_to_end.py` memakai `EofficeClient` (encoding/validasi respons asli) dengan seam opener HTTPS→HTTP loopback tepercaya, actual `GET /api/kiosk/snapshot`/`POST /api/kiosk/events`, SQL transaksi/shared writer, `IntegrationStore`, `TrustedClock`, `SyncWorker`, serta jalur frame `_step_eoffice`→`KioskWorker.handle_event`→enqueue asli. Hanya hasil frame/model/proof biometrik dibuat sintetis; tes ini bukan pengujian inferensi atau anti-spoof fisik. Lifecycle background/restart memakai thread/store asli. TLS deployment belum dibuktikan oleh loopback.

Mobile menjalankan seluruh `APIabsensi.post(absen_v2)` dan `AbsensiQuery.record_mobile_attendance` asli. Test-only VM memberi waktu/Moment deterministik, query DB disposable, provider identitas/lokasi/biometric proof/foto eksternal terisolasi. Pembentukan skeleton memakai protected creator asli. Auth HTTP mobile, Play Integrity provider, UI HP dan kamera fisik tidak diklaim oleh fixture ini; suite API yang ada menguji guards secara terpisah. Clock dan batas histori hanya dinormalisasi dalam fixture; bukan prosedur pemulihan produksi.

## Matriks otomatis

| Skenario | Hasil | Invariant yang diperiksa |
| --- | --- | --- |
| Offline kamera 07.25, HP commit 07.30, reconnect 07.40 | PASS | response duplicate, satu parent dan tepat satu detail check-in 07.30, satu ledger event; pending selesai |
| Kamera dahulu, HP kedua | PASS | HP menolak duplicate, waktu resmi kamera 07.25 tetap |
| Check-in kamera/check-out HP dan arah sebaliknya | PASS | satu detail tiap sesi, check-out 17.00, sidecar sumber mobile/kamera_kantor sesuai |
| Respons hilang setelah commit | PASS | SQL sudah berisi attendance/ledger saat local masih pending; retry byte+UUID identik, respons recorded yang tersimpan dipakai, satu acknowledgment count |
| Reboot dan backup terpisah | PASS | UUID/body bytes pulih persis, body tidak plaintext di backup, key salah gagal; capture clock baru belum dipercaya; worker start/stop kirim satu kali secara idempoten |
| Revoke enrollment selama outage | PASS | `enrollment_not_authorized`, tanpa attendance, hasil terminal lokal tersimpan |
| Perubahan kebijakan | PASS | capture sebelum batas memakai histori semula tanpa penalti terlambat; capture sesudah batas memakai snapshot lama ditolak; refresh mengganti snapshot |
| Oktober→November dan Desember→Januari | PASS | partisi yang dipilih persis `eoffice_absen.abs_absen_2026-10`/`2026-12`, tanggal capture tetap, November kosong |
| Gagal SQL setelah attendance write | PASS | parent/detail/provenance/event semuanya rollback; antrean byte sama; retry berikutnya commit satu sesi |
| Kontensi, UUID body conflict, coverage, auth revoke, snapshot ownership | PASS | suite HTTP/SQL API memverifikasi transaksi, lock dan gates asli; angka final di bawah |
| Model MobileFaceNet asli, input sintetis | PASS pada host | asset repo Android dibaca, tidak diubah; bukan golden crop/enrollment atau hardware acceptance |
| AuraFace deployment, native Android/iOS golden, Windows/C270/field | NOTRUN | menghalangi aktivasi, lihat matriks lapangan |

Initial validation: tambahan API langsung 16/16 pass. Sembilan assertion journey Python pertama pass, tetapi sembilan teardown error karena stdin subprocess fixture belum ditutup; perbaikan hanya harness. Setelah cleanup dan tambahan skenario rollback, focused 10/10 pass dalam 9,88 detik. Tidak ada product RED yang dibuat-buat dan tidak ada perubahan guard produksi. Semua behavior utama sudah diimplementasikan Tasks 1–9.

## Perintah reproduksi dan hasil final

Jalankan dari root masing-masing repo. URL berikut khusus container disposable; fixture akan recreate tabel sintetis. Jangan mengganti dengan DB produksi/staging berisi data nyata.

```bash
# API
KIOSK_TEST_DATABASE_URL=mysql://root:kiosk-test-only@127.0.0.1:33317/kiosk_test_camera npm test
# CAM focused
KIOSK_TEST_DATABASE_URL=mysql://root:kiosk-test-only@127.0.0.1:33317/kiosk_test_camera .venv/bin/python -m pytest tests/test_eoffice_end_to_end.py -q --tb=short
# CAM full; path asset adalah file nonproduksi yang memang tersedia pada host ini
KIOSK_TEST_DATABASE_URL=mysql://root:kiosk-test-only@127.0.0.1:33317/kiosk_test_camera EOFFICE_MOBILEFACENET_PATH=/Users/muhammadwildaniskandar/Projects/E-office/e-office-android-2.0-master/app/src/main/assets/mobilefacenet.tflite .venv/bin/python -m pytest -q --junitxml=.superpowers/sdd/2026-10-14-eoffice-camera-attendance/task-10-logs/cam-full.xml
.venv/bin/python -m pip check
```

`pip check`: **PASS**, “No broken requirements found.” Log asli/JUnit disimpan lokal di `.superpowers/sdd/2026-10-14-eoffice-camera-attendance/task-10-logs/`; file ini memuat hasil dan scope tanpa bergantung pada direktori tersebut. Path asset berbeda pada host lain harus menunjuk bobot yang terverifikasi, bukan menghapus tes.

### Hasil final yang direkam

| Pemeriksaan | Pass | Fail | Skip/NOTRUN | Durasi / catatan |
| --- | --- | --- | --- | --- |
| API seluruh `npm test` | 914 | 0 | 0 | 21,369805 detik; termasuk contention dan real HTTP/SQL |
| CAM seluruh pytest | 868 | 4 | 10 | 134,51 detik; 882 collected |
| CAM E2E focused | 10 | 0 | 0 | 9,88 detik, lalu lulus lagi dalam full suite dan final stability run |
| Dependency `pip check` | PASS | — | — | No broken requirements found |

**CAM full berstatus FAIL**, bukan full green: empat kegagalan baseline berada di
`tests/test_security_check.py::test_skrip_windows_ada_dan_berakhir_crlf` untuk
`jalankan_server.bat`, `jalankan_kios.bat`, `buka_kios.bat`, dan `backup_harian.bat`.
Berkas tersebut tidak diubah. Terdapat **7 PytestWarning** dari existing
`record_property` yang tidak kompatibel dengan default JUnit `xunit2`; ini warning
format artefak, bukan hasil model. Sepuluh skip adalah empat paritas wajib
(AuraFace nyata/consented serta golden Android/iOS), dua anti-foto yang memerlukan
model/contoh, dan empat model/foto standalone yang belum disediakan. Skip tidak
dihitung sebagai pass. Tes inferensi sintetis MobileFaceNet asli tidak di-skip.

Final focused stability command (seluruh 10 skenario berhasil pada tiga eksekusi
bersih: focused, full, final; bukan klaim flake rate populasi):

```bash
KIOSK_TEST_DATABASE_URL=mysql://root:kiosk-test-only@127.0.0.1:33317/kiosk_test_camera .venv/bin/python -m pytest tests/test_eoffice_end_to_end.py -q --tb=short -o junit_family=legacy --junitxml=.superpowers/sdd/2026-10-14-eoffice-camera-attendance/task-10-logs/e2e-final.xml
```

Log lokal: `task-10-logs/api-full.log`, `cam-full.log`, `cam-full.xml`,
`cam-focused.log`, `e2e-final.log`, `e2e-final.xml`, `pip-check.log` di direktori
workspace yang disebutkan di atas. Initial validation/harness cleanup disimpan
sebagai `api-initial.log` dan `cam-initial.log`. Tidak ada kegagalan baseline atau
warning yang disembunyikan/diquarantine untuk memperbaiki angka hasil.

## Penerimaan fisik yang masih harus dilaksanakan

Gunakan staging e-office berizin dengan existing enrollment peserta yang sudah memberi persetujuan. Simpan ID peserta pseudonim, asal enrollment (Android/iOS), model/hash, build HP/kios, OS/arsitektur/CPU, kamera C270/penempatan, cahaya/jarak/kecepatan berjalan, policy hash/threshold per tipe dan waktu NTP. Jangan commit frame/template berizin ke Git; simpan bukti dengan kontrol akses/retensi yang disetujui.

Untuk **setiap peserta** lakukan 20 lintasan berjalan normal pada tipe enrollment yang berlaku, mencakup minimal 10 pagi dan 10 sore; catat kondisi kacamata/cadar yang berlaku. Tambahkan lintasan kondisi kacamata/cadar, dua orang bersamaan serta serangan cetak dan layar, dengan jumlah rencana ditetapkan sebelum trial. Peserta periocular/cadar tidak boleh dikeluarkan dari scope. Catat setiap upaya, termasuk no-detection, abstain, timeout, identitas salah dan spoof diterima; N/A tidak boleh dipakai untuk menyembunyikan jalur wajib yang belum diuji.

| Tipe / kondisi | Peserta dan asal template | Benar tanpa berhenti / total | Gagal / total | Identitas salah / total | Spoof diterima / serangan | Latensi keputusan p50 / p95 / maks | Status |
| --- | --- | --- | --- | --- | --- | --- | --- |
| AuraFace, 20 lintasan/peserta, pagi+sore | BELUM DIISI | — / — | — / — | — / — | n/a | — / — / — ms | NOTRUN |
| Periocular/cadar, 20 lintasan/peserta, pagi+sore | BELUM DIISI | — / — | — / — | — / — | n/a | — / — / — ms | NOTRUN |
| Kacamata dan perubahan pencahayaan, kedua tipe | BELUM DIISI | — / — | — / — | — / — | n/a | — / — / — ms | NOTRUN |
| Dua orang melewati kamera bersama, kedua tipe | BELUM DIISI | — / — | — / — | — / — | n/a | — / — / — ms | NOTRUN |
| Foto cetak, kedua tipe | BELUM DIISI | n/a | — / — penolakan | — / — | — / — | — / — / — ms | NOTRUN |
| Layar ponsel, kedua tipe | BELUM DIISI | n/a | — / — penolakan | — / — | — / — | — / — / — ms | NOTRUN |

Kolom “—” berarti belum diukur, bukan nol error. Penerimaan menuntut semua peserta menunjukkan keberhasilan tanpa berhenti dengan enrollment existing dan tidak ada identitas salah/spoof diterima dalam set ini. Jumlah trial ini latihan penerimaan awal, bukan jaminan statistik. Jika deteksi/liveness periocular/cadar atau CPU gagal, tetap nonaktifkan attendance resmi, catat sebab/latensi terukur dan minta penyesuaian desain ditinjau. Jangan mengganti persyaratan menjadi harus berhenti, re-enroll atau menurunkan ambang.

| Prasyarat lain | Status dan bukti yang harus dilampirkan |
| --- | --- |
| AuraFace local/deployed weights dan paritas server T6-I03 | NOTRUN: hash deployment, frame/landmark berizin, cosine/max difference |
| Golden native Android dan iOS T6-I04 | NOTRUN: tensor crop dan embedding native berizin; OpenCV dan native/CoreGraphics resize/crop belum terbukti setara |
| Windows aktual T6-I05 | NOTRUN: konfirmasi arsitektur, install, pip check, inferensi kedua model, runtime kamera/liveness |
| TLS/proxy Date/NTP deployment | NOTRUN: certificate hostname/chain, tidak redirect, satu Date baru, recovery time jump/reboot |
| DDL/engine/grants/duplicates/legacy skeleton | NOTRUN: audit clone staging dan rollout terkoordinasi semua writer; skeleton tanpa provenance dapat memblokir HP |
| Job eksternal dan koreksi admin produksi | NOTRUN: inventaris deployment, sumber audit, transaksi dan izin |
| Timing queue di Windows | NOTRUN: host hanya menunjukkan pending O(total history) 32,8 ms/5.000 row, indexed terminal 0,014 ms; idle 60 detik+wake |

Penanggung jawab lapangan: BELUM DIISI. Tanggal trial: BELUM DIISI. Lampiran pengukuran: BELUM DIISI. Persetujuan aktivasi: **belum ada**. [Prosedur operasi/restore/rollback](../EOFFICE.md) wajib diverifikasi sebelum persetujuan tersebut.

## Perbaikan review T10-PY-I01

Fixture subprocess kini selalu menjalankan cleanup saat handshake shutdown gagal
atau readiness startup kehilangan field. Delapan regresi fault injection tanpa DB
awalnya **8 fail** (cleanup tidak terpanggil/lengkap), lalu **8 pass** sesudah fix.
Gabungan delapan regresi dan sepuluh journey HTTP/SQL: **18 pass, 0 fail, 0 skip,
tanpa warning**, 16,76 detik. Tidak ada perubahan produksi atau API. Full-suite
historis di atas tidak diulang untuk perubahan fixture ini; seluruh NOTRUN tetap.

```bash
.venv/bin/python -m pytest tests/test_eoffice_fixture.py -q --tb=short
KIOSK_TEST_DATABASE_URL=mysql://root:kiosk-test-only@127.0.0.1:33317/kiosk_test_camera .venv/bin/python -m pytest tests/test_eoffice_fixture.py tests/test_eoffice_end_to_end.py -q --tb=short -o junit_family=legacy --junitxml=.superpowers/sdd/2026-10-14-eoffice-camera-attendance/task-10-logs/fix1-focused.xml
```

Log aktual dalam direktori workspace yang sama: `fix1-red.log`,
`fix1-unit-green.log`, `fix1-focused.log`, dan `fix1-focused.xml`.
