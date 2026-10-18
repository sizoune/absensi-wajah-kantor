# Wizard first setup

Status: wawancara desain selesai; menunggu konfirmasi kesepahaman sebelum implementasi.

## Tujuan

Operator melengkapi konfigurasi yang diperlukan saat pemasangan pertama, memilih cakupan organisasi menggunakan data API eOffice, lalu mengunduh enrollment sesuai cakupan tersebut.

## Keputusan disepakati

- Wizard tersedia lewat browser saat aplikasi pertama kali dijalankan.
- Cakupan dipilih eksplisit: seluruh SKPD, induk/non-subunit, atau subunit tertentu. Tidak menggunakan subunit kosong sebagai pilihan yang ambigu.
- Token perangkat disiapkan admin terlebih dahulu.
- Operator memilih cakupan dalam batas izin token; server memvalidasi pilihan.
- Akses pemasangan pertama memakai kode setup sekali pakai.
- URL produksi terisi otomatis dan dapat diubah melalui opsi lanjutan.
- Seluruh kebutuhan teknis disiapkan otomatis, termasuk model, penyimpanan, dan kunci enkripsi awal. Tidak membuat ulang kunci instalasi yang sudah memiliki data.
- Setup boleh selesai setelah snapshot valid diterima meskipun enrollment kosong; kesiapan pengenalan wajah ditampilkan terpisah.
- Kemajuan setup disimpan agar proses yang terputus dapat dilanjutkan. Model diverifikasi sebelum digunakan dan snapshot parsial tidak diaktifkan.
- Instalasi lama dengan konfigurasi lengkap tetap langsung berjalan; konfigurasi belum lengkap diarahkan ke wizard dengan nilai lama terisi. Kunci enkripsi, snapshot, dan antrean absensi dipertahankan.
- Perubahan cakupan setelah setup melalui admin lokal, setelah antrean lama selesai dikirim. Snapshot cakupan baru harus valid sebelum absensi dilanjutkan.
- Mendukung Windows langsung dan Docker dengan kamera browser sejak versi pertama.
- Admin lokal pertama dibuat lewat wizard dengan username dan password tanpa password bawaan. Instalasi lama mempertahankan akun admin yang ada.
- Kode setup ditampilkan lokal: jendela peluncur Windows atau perintah lokal khusus Docker; tidak disisipkan ke URL atau log rutin. Sebelum setup selesai, kode baru dapat diterbitkan setelah restart tanpa menghapus kemajuan.
- Docker mendukung browser lokal dan akses jaringan HTTPS. Akses lokal menjadi bawaan; alamat HTTPS dan sertifikat dikonfigurasi melalui opsi lanjutan.
- Preview dan pemeriksaan kamera wajib sebelum status siap absensi. Konfigurasi dapat disimpan dengan status kamera belum siap. Windows menyediakan pilihan kamera yang terdeteksi.
- Model diunduh otomatis dari sumber distribusi yang ditetapkan pengelola, dengan versi dan hash terverifikasi serta kompatibilitas terhadap enrollment server. Model belum tersedia atau tidak cocok menahan status siap absensi dan tidak menghapus kemajuan.
- Instalasi operasional langsung aktif setelah seluruh pemeriksaan kesiapan lolos, tanpa persetujuan aktivasi tambahan. Pengujian hanya dilakukan pada laptop/PC pengembang. Mode dan akses tetap mengikuti izin server; keberhasilan wizard tidak menaikkan hak token uji menjadi hak absensi resmi.
- Token kedaluwarsa/dicabut menghentikan capture baru dan mempertahankan data. Antrean lama hanya dilanjutkan setelah server memastikan token pengganti mewakili perangkat yang sama; token perangkat lain tidak mengambil alih antrean.
- Pemulihan password admin melalui perintah lokal pada Windows atau host Docker tanpa menghapus konfigurasi, kunci, atau antrean. Wizard pertama tidak dibuka kembali setelah setup selesai.
- HTTPS jaringan Docker diterminasi oleh reverse proxy yang dikelola instansi. Pengelola menyediakan domain dan sertifikat; versi pertama tidak menerbitkan sertifikat otomatis.

## Fakta integrasi saat ini

- Klien aplikasi mengambil snapshot enrollment melalui API dan menyimpannya terenkripsi; mekanisme ini dapat digunakan kembali.
- Konfigurasi eOffice saat ini membutuhkan URL HTTPS, token perangkat, model wajah lokal, dan kunci enkripsi yang valid.
- Desain integrasi yang ada menetapkan server sebagai penentu cakupan unit dan status pegawai.
- Jalur uji pada `e-office-api-main` saat ini membatasi token ke SKPD 79 dan masa berlaku 24 jam. Perluasan wizard ke SKPD/subunit lain membutuhkan pemeriksaan dan penyesuaian kontrak server; pilihan lokal saja tidak cukup.

## Keputusan startup dan sesi kamera

- Windows menjalankan aplikasi otomatis saat operator masuk ke Windows; layanan Docker berjalan kembali saat host hidup. Setup selesai tidak diulang. Kamera browser tetap membutuhkan browser terbuka dan izin kamera.
- Satu sesi kamera aktif per instalasi. Browser kedua menampilkan sesi sedang digunakan; admin lokal dapat menghentikan sesi lama sebelum memulai sesi pengganti.

## Alur yang disepakati

1. Installer menyiapkan kebutuhan teknis Windows langsung atau Docker dengan kamera browser.
2. Pada pemasangan pertama, operator membuka wizard browser menggunakan kode setup lokal sekali pakai.
3. Operator membuat admin lokal pertama; instalasi lama menggunakan admin yang ada.
4. Operator memasukkan token perangkat. URL produksi terisi otomatis dan bisa diubah melalui opsi lanjutan.
5. Wizard mengambil data organisasi dari API sesuai izin token. Operator memilih SKPD dan salah satu dari tiga cakupan, serta subunit jika diperlukan. Server memvalidasi pilihan tersebut.
6. Aplikasi menyiapkan penyimpanan/kunci awal dan mengunduh serta memverifikasi model tanpa menimpa kunci instalasi yang sudah berisi data.
7. Aplikasi mengunduh snapshot enrollment sesuai cakupan melalui mekanisme integrasi yang sudah ada. Snapshot kosong sah; snapshot parsial tidak diaktifkan.
8. Operator memeriksa preview kamera. Setelah seluruh pemeriksaan kesiapan lolos, instalasi operasional langsung aktif; sinkronisasi enrollment berlanjut berkala.

Urutan persiapan teknis dapat disesuaikan dengan kebutuhan bootstrap; hasil dan batas akses di atas tetap berlaku.

## Status yang dibedakan

- **Setup belum selesai**: konfigurasi atau snapshot awal yang valid belum tersedia; kemajuan dapat dilanjutkan.
- **Setup selesai, belum siap absensi**: konfigurasi dan snapshot awal valid, tetapi enrollment kosong, model belum siap, atau pemeriksaan kamera belum lolos. Tampilkan penyebab dan langkah pemulihan.
- **Siap absensi**: seluruh pemeriksaan kesiapan lolos; instalasi operasional langsung aktif dalam izin server.

## Batas perubahan

- Aplikasi: bootstrap/konfigurasi, akses wizard/admin, penyimpanan kemajuan dan rahasia, unduhan model, integrasi snapshot, pemeriksaan kamera, penguncian sesi, pemulihan lokal, startup Windows/Docker, serta dokumentasi instalasi.
- API eOffice: periksa dan lengkapi kontrak daftar organisasi, izin token, validasi cakupan snapshot/kejadian, dan penggantian token dengan identitas perangkat tetap.
- Gunakan komponen yang sudah ada. Verifikasi bagian yang berubah dengan pengujian batas izin, kode setup, pemulihan proses, integritas data, dan sesi kamera tunggal.
- Tidak menambahkan enrollment lokal, sinkronisasi otomatis data standalone ke eOffice, penerbitan sertifikat otomatis, atau penggantian kunci pada instalasi berisi data.

## Konfirmasi

Seluruh pertanyaan Q1–Q23 telah dijawab. Menunggu konfirmasi pengguna atas rangkuman sebelum implementasi sesuai skill grill-with-docs/grilling. Desain ini tidak menyatakan bahwa endpoint server dan artefak model yang dibutuhkan sudah tersedia.

## Pekerjaan penelusuran sebelum implementasi

- Temukan URL produksi dan sumber distribusi seluruh model yang sah dari konfigurasi/dokumentasi proyek; catat komponen yang belum tersedia.
- Periksa endpoint daftar SKPD/subunit, batas izin token, dan perubahan server yang diperlukan untuk tiga jenis cakupan.
- Periksa bootstrap Windows/Docker, akun admin yang ada, penyimpanan persisten, dan penanganan HTTPS yang bisa digunakan kembali.
