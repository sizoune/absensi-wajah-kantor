# Operasi kios e-office

**Status: belum diterima untuk aktivasi resmi.** Integrasi hanya masuk pagi dan pulang. Enrollment berasal dari e-office untuk pegawai Diskominfo aktif yang diizinkan; tidak ada enrollment ulang, sesi istirahat/event, atau perbandingan vektor beda model. Catatan sah yang commit pertama tetap berlaku. Lihat [QA penerimaan](qa/2026-10-14-eoffice-camera-acceptance.md) sebelum menyetujui penggunaan resmi.

## Persiapan dan model

Ikuti `docs/kiosk/operations.md` di repo API untuk audit skema, urutan migrasi kiosk lalu sidecar, barrier revision/coverage, clean-day/remediasi skeleton legacy, ID instansi terverifikasi, TLS, NTP dan credential. Skema/ID dari fixture bukan konfigurasi produksi. Uji fisik boleh memakai e-office staging berizin dalam dua mode yang sudah ada (`standalone` dan `eoffice`); tidak ada mode dry-run tambahan.

Pada Windows kios, konfirmasi arsitektur Python 3.11 x64 terlebih dahulu. Dalam virtualenv terisolasi:

```powershell
python -m pip install -r requirements-eoffice.txt
python -m pip check
```

Resolusi wheel Windows di macOS bukan bukti instalasi/inferensi Windows. Pasang YuNet dan kedua MiniFASNet sesuai manifest [MODEL_DECISION](MODEL_DECISION.md), serta AuraFace `glintr100.onnx` dan MobileFaceNet yang identik dengan deployment. CLI yang sudah ada:

```powershell
flask --app app models-download
flask --app app models-check
```

Perintah tersebut juga memuat SFace untuk standalone; ia **tidak** memasang/membuktikan AuraFace atau periocular. Integrasi memverifikasi kedua manifest model/dimensi/hash terhadap snapshot autentik. Jangan mengedit hash untuk menerima bobot lain. Hash rujukan dan pipeline ada di MODEL_DECISION; bobot AuraFace deployment dan golden mobile berizin masih harus diuji. Perbedaan crop/resize Android vs iOS dan landmark detektor kios tetap dapat membatalkan kompatibilitas meskipun hash bobot sama.

Siapkan kunci baru hanya saat layanan berhenti, sebelum konfigurasi eoffice diaktifkan. CLI `biometric-keygen` memakai `BIOMETRIC_KEY_FILE`, bukan `EOFFICE_KEY_FILE`: untuk pembuatan awal pilih standalone, set `BIOMETRIC_KEY_FILE` ke lokasi kunci integrasi baru di luar repo, lalu jalankan `flask --app app biometric-keygen`. Jangan menjalankan kamera/enrollment dalam langkah persiapan ini. CLI menolak menimpa file yang ada. Setelah dibuat, set `EOFFICE_KEY_FILE` ke file tersebut dan kembalikan konfigurasi key standalone sebelumnya bila ada. Simpan backup kunci pada media terpisah dengan ACL hanya operator/service. SQLite saat ini memakai active key; **jangan rotasi active key atau membuat ulang key** pada antrean yang sudah ada: belum ada migrasi/rekey antrean.

Konfigurasi yang diperlukan pada kios:

| Variabel | Nilai operator |
| --- | --- |
| `ATTENDANCE_MODE` | `eoffice` hanya untuk staging trial berizin atau aktivasi yang sudah disetujui |
| `EOFFICE_BASE_URL` | URL HTTPS server, tanpa userinfo/query/fragment; chain/hostname valid |
| `EOFFICE_DEVICE_TOKEN` | secret perangkat yang diprovision server, bukan login pegawai |
| `EOFFICE_AURAFACE_PATH` | path absolut bobot AuraFace terverifikasi |
| `EOFFICE_PERIOCULAR_PATH` | path absolut MobileFaceNet terverifikasi |
| `EOFFICE_KEY_FILE` | keyring AES lokal, terpisah dari repo/database |
| `LIVENESS_MODE` | `passive`; tanpa challenge berhenti/kedip terarah |

Threshold/margin/quality/passive deadline dan jumlah sampel **per verification_type** berasal dari policy server terkalibrasi. Jangan menyalin threshold SFace atau menurunkan batas demi cadar/CPU. Dengan izin staging dan prasyarat selesai, `flask --app app kiosk-run --preview` menjalankan kios; `--no-preview` adalah opsi yang sudah ada. Ini dapat menulis attendance di server yang dikonfigurasi. `face-check` masih diagnostik SFace standalone, bukan pengujian model integrasi atau sertifikasi cadar; jangan menggunakannya sebagai bukti kompatibilitas. Enrollment lokal ditolak di mode eoffice.

## Status dan pemulihan

Enqueue terenkripsi harus commit sebelum tampilan pending. Pesan masuk/pulang hanya dari respons resmi; duplicate mempertahankan catatan pertama. Angka “baru tercatat” adalah jumlah respons recorded yang baru diakui worker pada run ini, **bukan rekap harian**. Respons lama yang baru sampai boleh berbeda waktunya dari capture. Hasil terminal tersimpan tetap menjadi sumber kebenaran walaupun antrean notifikasi 32 item penuh; layar merekonsiliasi hasil aktif dan galeri paling sering 2 Hz.

| Status/kondisi | Langkah |
| --- | --- |
| Offline/429/5xx/timeout | Perbaiki jaringan/server; retry otomatis 1,2,4,8,16,32,60 detik, byte dan UUID sama. Jangan flush pending. |
| Operator/401/403 | Periksa revoke/secret/URL/TLS. Setelah perbaikan eksplisit, restart layanan untuk melanjutkan. Token perangkat lain tidak boleh dipakai untuk mengirim antrean lama yang snapshot-nya terikat perangkat. |
| Clock unavailable/restart/jam meloncat | NTP, server/proxy Date dan HTTPS harus benar; tunggu snapshot autentik baru sebelum capture baru. Pending yang sudah ada tetap tersimpan/dapat dikirim. Cache anchor tidak memulihkan kepercayaan monotonic lintas boot. |
| Storage/disk penuh/key salah/tamper | Hentikan capture, pertahankan DB/WAL/key untuk investigasi, kosongkan ruang dari data lain. Pulihkan backup terverifikasi. Jangan hapus pending/history atau membuat ulang DB/kunci. |
| Galeri/model tidak cocok | Periksa kedua file/hash/dimensi dan policy/enrollment yang diizinkan. Jangan fallback SFace/enrollment lokal. |
| Rejected/outside schedule | Audit alasan server, capture time dan interval histori. Jam datang request tidak mengganti waktu kejadian; histori hilang tidak direkayasa. |

Sync thread memiliki koneksi SQLite sendiri, frame thread koneksi terpisah; clock audit memakai worker store. Penghentian menunggu maksimal enam detik per join; jika belum selesai, **jangan tutup koneksi hidup atau mulai backup**. Tunggu proses keluar bersih; investigasi request menggantung. Idle worker menunggu 60 detik dan dapat dibangunkan enqueue. `pending()` memvalidasi semua histori tersimpan, O(total row): pengukuran host 5.000 baris sekitar 32,8 ms, lookup terminal terindeks sekitar 0,014 ms. Waktu Windows aktual belum diukur; jangan mengatasi pertumbuhan dengan menghapus histori/pending tanpa desain retensi yang ditinjau.

Clock mengikat HTTP Date ke monotonic dan jam sistem saat header respons diterima, sebelum body, validasi, dan penyimpanan. Durasi request sampai header ditambah presisi Date satu detik harus tidak lebih dari dua detik (header paling lambat satu detik); seluruh durasi dihitung, tanpa asumsi jaringan simetris. Respons yang melampaui batas atau waktu server mundur tidak konsisten menahan capture sampai refresh autentik valid. Timeout jaringan biasa tetap mengizinkan anchor valid pada boot yang sama. Pemrosesan lambat tidak mengurangi waktu capture; refresh tidak memundurkan waktu. `issued_at` asli tetap menjadi asal bukti, bukan waktu refresh. Batas dua detik ini ketidakpastian transport, bukan toleransi jadwal; proxy Date/NTP dan drift perangkat tetap wajib diverifikasi. API tetap memeriksa bukti elapsed secara tepat dan menolak timestamp masa depan.

Halaman kios dan dashboard admin menampilkan kesehatan e-office terpisah dari kamera dan pesan pegawai: koneksi, penyimpanan, akses perangkat, waktu capture, dan tindakan operator. Status ini tidak hilang saat pesan pegawai kedaluwarsa. Pada Windows dengan sound scheme/perangkat yang tersedia, bunyi sistem asinkron membedakan hasil resmi berhasil dari kegagalan; pending dan hasil lama/kedaluwarsa tidak membunyikan sukses. Bunyi mengikuti pengaturan Windows, tanpa konfigurasi/browser autoplay baru; platform tanpa audio tetap memakai teks. Audio Windows fisik belum diuji.

## Backup, pemeriksaan dan restore

Hentikan semua proses kios/worker dengan bersih. Database ada pada `instance/eoffice.sqlite3` (instance path aplikasi), terpisah dari DB standalone. `backup-db`/`backup-key` standalone tidak otomatis mencakup antrean/kunci integrasi. Gunakan SQLite backup API untuk menyertakan data WAL yang mungkin tersisa; jangan copy file `.sqlite3` hidup tanpa WAL. Simpan database terenkripsi dan keyring pada **dua lokasi/media berbeda**. Backup juga konfigurasi nonrahasia/hashes dan metadata perangkat server secara berizin; token masuk secret store, bukan laporan.

Contoh inspeksi dan backup dijalankan dari root CAM setelah shutdown, sesuaikan path pada kode. Output hanya UUID/hash byte dan jumlah; tidak mencetak template/body/kunci. Simpan output audit di lokasi operator terbatas. Untuk restore jalankan audit yang sama pada salinan database dengan kunci backup terpisah dan bandingkan daftar `(event_id, sha256)` persis.

```python
from pathlib import Path
from contextlib import closing
from hashlib import sha256
import sqlite3
from integration.store import IntegrationStore
from services.biometric_service import load_keyring

source = Path('instance/eoffice.sqlite3')
key_path = Path(r'D:\absensi-secrets\eoffice-key.json')
backup = Path(r'E:\backup-queue\eoffice.sqlite3')
assert source.is_file() and key_path.is_file()
active, keys = load_keyring(key_path)
store = IntegrationStore(source, keys[active])
try:
    manifest = [(row.event_id, sha256(row.body).hexdigest()) for row in store.pending()]
    print({'pending': len(manifest), 'events': manifest})
finally:
    store.close()
assert not backup.exists(), 'Pilih nama backup baru; jangan timpa backup terdahulu'
backup.parent.mkdir(parents=True, exist_ok=True)
with closing(sqlite3.connect(source)) as src, closing(sqlite3.connect(backup)) as dst:
    src.backup(dst)
# Salin key_path ke media kunci LAIN memakai prosedur ACL operator.
```

Untuk restore: hentikan layanan, simpan direktori gagal termasuk WAL/SHM sebagai bukti, tempatkan backup pada direktori pemulihan bersih dengan nama/path sesuai konfigurasi. Jangan menimpa database baru dengan WAL lama. Validasi dekripsi dan kesamaan UUID/hash pending menggunakan kunci backup, lalu uji di lingkungan staging yang disetujui sebelum penggunaan resmi; key salah harus gagal. Jangan menjalankan dua kios pada salinan antrean yang sama. Setelah restart, anchor waktu baru wajib untuk capture; event lama dikirim ulang tanpa rekonstruksi body atau perubahan UUID. Uji otomatis Task 10 membandingkan byte asli, UUID, salah-key ditolak, reboot/worker shutdown, dan satu catatan resmi setelah replay.

Rollback: revoke/block endpoint server, hentikan integrasi, pertahankan semua antrean/event/snapshot/provenance. Pilih standalone secara eksplisit dengan `ATTENDANCE_MODE=standalone` setelah operator memahami hasilnya hanya lokal dan pending e-office belum terselesaikan. Tidak ada sinkronisasi otomatis dari database standalone; jangan menghapus data untuk membuat status tampak bersih.
