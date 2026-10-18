# Wizard first setup perangkat absensi eOffice

Status: spesifikasi hasil kesepakatan Q1–Q23. Batas pengujian telah dikonfirmasi pengguna. Implementasi tersedia pada branch `integration/first-setup-wizard`; penerimaan fisik dan deployment masih menunggu verifikasi.

## Problem Statement

Operator pemasangan perangkat absensi harus mengisi konfigurasi teknis secara manual sebelum perangkat bisa digunakan. Pemilihan organisasi dan pengambilan enrollment belum menjadi satu alur pemasangan yang mudah digunakan. Hal ini menyulitkan pemasangan di banyak SKPD/subunit, meningkatkan risiko salah cakupan pegawai, dan membuat pemulihan proses yang terputus bergantung pada pengetahuan teknis.

Operator membutuhkan pemasangan pertama yang cukup meminta kredensial dan pilihan organisasi, sambil memastikan perangkat hanya menerima enrollment yang diizinkan server dan tidak merusak data instalasi lama.

## Solution

Sediakan wizard browser pada pemasangan pertama untuk Windows langsung dan Docker dengan kamera browser. Installer/aplikasi menyiapkan kebutuhan teknis otomatis. Operator menggunakan kode setup lokal sekali pakai, membuat admin lokal, memasukkan token perangkat dari admin server, memilih SKPD dan cakupan perangkat, lalu melihat kemajuan penyiapan model, pengambilan enrollment, dan pemeriksaan kamera.

URL produksi terisi otomatis dan dapat diganti melalui opsi lanjutan. Data organisasi berasal dari API eOffice dan dibatasi izin token. Server memvalidasi cakupan seluruh SKPD, induk/non-subunit, atau subunit tertentu. Gunakan mekanisme snapshot enrollment dan sinkronisasi yang sudah ada.

Perangkat operasional langsung aktif setelah seluruh pemeriksaan kesiapan lolos. Setup boleh selesai dengan snapshot kosong, tetapi status siap absensi harus membedakan ketersediaan enrollment, model, kamera, dan prasyarat integrasi yang valid. Kemajuan dapat dilanjutkan setelah gangguan; konfigurasi, kunci, dan antrean lama dipertahankan.

## User Stories

1. As an operator pemasangan, I want a wizard browser saat aplikasi pertama dijalankan, so that saya dapat menyiapkan perangkat tanpa mengedit konfigurasi teknis manual.
2. As an operator Windows, I want a pemasangan yang menyiapkan seluruh kebutuhan teknis otomatis, so that saya tidak perlu memasang komponen satu per satu.
3. As an operator Docker, I want a layanan dengan penyimpanan persisten yang disiapkan otomatis, so that konfigurasi dan antrean tetap tersedia setelah container diganti atau host restart.
4. As an operator pemasangan, I want a kode setup sekali pakai yang diperoleh secara lokal, so that hanya pihak yang memiliki akses pemasangan dapat memulai setup.
5. As an operator Windows, I want a kode setup pada jendela peluncur, so that saya bisa membukanya tanpa mencari rahasia di log.
6. As an operator Docker, I want a perintah lokal untuk memperoleh kode setup, so that saya dapat menyiapkan perangkat tanpa membuka kredensial pada URL atau log rutin.
7. As an operator pemasangan, I want a kode setup pengganti setelah restart sebelum setup selesai, so that saya dapat melanjutkan pemasangan tanpa kehilangan kemajuan.
8. As an admin lokal pertama, I want a formulir username dan password tanpa password bawaan, so that akses administrasi terlindungi sejak pemasangan pertama.
9. As an operator instalasi lama, I want a penggunaan kembali akun admin yang ada, so that saya tidak perlu membuat akun pengganti.
10. As an operator pemasangan, I want a formulir token perangkat dari admin server, so that perangkat menggunakan kredensialnya sendiri untuk mengakses API.
11. As an operator pemasangan, I want a URL produksi yang sudah terisi, so that saya tidak perlu mengetahui alamat API secara manual.
12. As an pengembang atau pengelola, I want a opsi lanjutan untuk mengganti URL API, so that saya dapat memakai server yang sesuai tanpa mengubah kode aplikasi.
13. As an operator pemasangan, I want a daftar SKPD dari API sesuai izin token, so that saya hanya memilih organisasi yang diizinkan.
14. As an operator pemasangan, I want a pilihan cakupan seluruh SKPD, so that perangkat dapat melayani pegawai induk dan seluruh subunit SKPD tersebut.
15. As an operator pemasangan, I want a pilihan cakupan induk/non-subunit, so that perangkat hanya melayani pegawai SKPD yang tidak memiliki subunit.
16. As an operator pemasangan, I want a pilihan cakupan subunit tertentu, so that perangkat hanya melayani pegawai pada subunit yang dipilih.
17. As an operator pemasangan, I want a daftar subunit yang sesuai SKPD dan izin token, so that saya tidak memasangkan organisasi yang tidak berhubungan.
18. As an admin server, I want a validasi cakupan pada server, so that perubahan pilihan dari klien tidak dapat memperluas izin perangkat.
19. As an operator pemasangan, I want a penyiapan penyimpanan dan kunci enkripsi awal secara otomatis, so that saya tidak perlu memasukkan path teknis.
20. As an operator instalasi lama, I want a pemakaian kembali kunci yang sudah ada, so that data terenkripsi dan antrean tetap dapat dibaca.
21. As an operator pemasangan, I want a unduhan model otomatis dengan versi dan hash terverifikasi, so that saya tidak menggunakan model yang salah atau rusak.
22. As an operator pemasangan, I want a pesan bila model belum tersedia atau tidak kompatibel, so that saya mengetahui alasan perangkat belum siap dan dapat melanjutkan setelah masalah diperbaiki.
23. As an operator pemasangan, I want a pengambilan enrollment sesuai cakupan perangkat, so that perangkat hanya mengenali pegawai yang seharusnya dilayani.
24. As an operator pemasangan, I want a penyelesaian setup dengan snapshot valid yang kosong, so that saya dapat menyiapkan perangkat sebelum enrollment pegawai tersedia.
25. As an operator, I want a status belum siap absensi saat enrollment kosong, so that saya tidak mengira perangkat sudah dapat mengenali pegawai.
26. As an operator pemasangan, I want a kemajuan setup tersimpan, so that gangguan jaringan atau restart tidak memaksa saya mengulang seluruh proses.
27. As an operator pemasangan, I want a tindakan coba lagi untuk langkah yang gagal, so that saya dapat melanjutkan setelah jaringan atau server pulih.
28. As an operator, I want a snapshot parsial ditolak tanpa menggantikan snapshot valid sebelumnya, so that kegagalan unduhan tidak merusak galeri.
29. As an operator Windows, I want a pilihan kamera dari perangkat yang terdeteksi, so that saya dapat memakai kamera yang tepat.
30. As an operator, I want a preview dan pemeriksaan kamera, so that saya mengetahui kamera berfungsi sebelum perangkat dinyatakan siap absensi.
31. As an operator, I want a konfigurasi tersimpan meskipun kamera belum tersedia, so that pemasangan dapat dilanjutkan tanpa mengulang langkah sebelumnya.
32. As an operator operasional, I want a perangkat langsung aktif setelah seluruh pemeriksaan kesiapan lolos, so that saya tidak memerlukan persetujuan aktivasi tambahan.
33. As an pengembang, I want a mode uji untuk lingkungan pengembang yang tetap mengikuti izin server, so that pengujian tidak mengubah token uji menjadi akses absensi resmi.
34. As an operator, I want a pembaruan enrollment berkala, so that perubahan enrollment yang diizinkan server diterima setelah setup.
35. As an operator instalasi lama, I want a aplikasi langsung berjalan jika konfigurasi lengkap, so that penambahan wizard tidak mengganggu operasi yang ada.
36. As an operator instalasi lama, I want a wizard terisi nilai lama jika konfigurasi belum lengkap, so that saya hanya melengkapi kebutuhan yang kurang.
37. As an admin lokal, I want a perubahan cakupan melalui pengaturan setelah antrean lama selesai dikirim, so that pemindahan perangkat tidak mencampur konteks absensi.
38. As an admin lokal, I want a snapshot cakupan baru tervalidasi sebelum capture dilanjutkan, so that perangkat tidak memakai galeri lama untuk cakupan baru.
39. As an operator, I want a penghentian capture baru ketika token kedaluwarsa atau dicabut, so that perangkat tidak terus mengumpulkan absensi tanpa akses yang sah.
40. As an admin server, I want a token pengganti yang mempertahankan identitas perangkat untuk melanjutkan antrean lama, so that antrean tidak diambil alih perangkat lain.
41. As an admin lokal, I want a pemulihan password melalui perintah lokal, so that akses dapat dipulihkan tanpa menghapus konfigurasi, kunci, atau antrean.
42. As an operator Windows, I want a aplikasi berjalan otomatis saat saya masuk ke Windows setelah restart, so that perangkat tidak perlu dijalankan manual setiap hari.
43. As an operator Docker, I want a layanan berjalan kembali saat host hidup, so that perangkat pulih setelah restart host.
44. As an operator Docker, I want a akses browser lokal maupun jaringan HTTPS, so that kamera dapat digunakan pada komputer yang sama atau komputer kantor lain.
45. As an pengelola infrastruktur, I want a integrasi dengan reverse proxy HTTPS instansi, so that domain dan sertifikat dikelola mengikuti lingkungan instansi.
46. As an operator kamera browser, I want a penjelasan bahwa browser harus terbuka dan izin kamera harus diberikan, so that startup layanan tidak disalahartikan sebagai kamera yang sudah aktif.
47. As an operator, I want a satu sesi kamera aktif per instalasi, so that dua browser tidak melakukan capture bersamaan pada perangkat yang sama.
48. As an operator browser kedua, I want a pesan bahwa sesi kamera sedang digunakan, so that saya mengetahui alasan kamera tidak dapat dimulai.
49. As an admin lokal, I want a penghentian sesi lama sebelum sesi pengganti dimulai, so that saya dapat memulihkan penggunaan kamera yang tertinggal.
50. As an admin lokal, I want a wizard pertama tetap tertutup setelah setup selesai, so that kode pemasangan tidak menjadi jalur perubahan konfigurasi tanpa login admin.

## Implementation Decisions

- Bootstrap harus dapat membuka wizard ketika konfigurasi eOffice belum lengkap, tanpa memulai capture atau integrasi operasional yang belum siap. Saat ini validasi konfigurasi penuh dilakukan pada pembuatan aplikasi; pisahkan kesiapan setup dari kesiapan operasional dengan perubahan terkecil yang sesuai struktur aplikasi.
- Cakupan perubahan meliputi bootstrap/konfigurasi, halaman dan endpoint wizard, autentikasi admin, kemajuan setup, penyimpanan rahasia, distribusi model, klien organisasi eOffice, integrasi snapshot, kamera, perintah pemulihan, dan packaging Windows/Docker.
- Gunakan autentikasi, validasi, CSRF, penyimpanan terenkripsi, penguncian kamera, dan mekanisme sinkronisasi yang sudah ada. Tidak membuat sistem admin, galeri, atau antrean paralel.
- Akses pertama memakai kode setup sekali pakai. Setelah kode ditukar menjadi akses setup, akses tersebut tetap terbatas pada pemasangan yang belum selesai. Kode yang telah dipakai tidak dapat ditukar ulang. Restart sebelum selesai dapat menerbitkan kode baru tanpa menghapus kemajuan; kode lama yang diganti tidak berlaku.
- Kode setup hanya tersedia melalui jendela peluncur Windows atau perintah lokal Docker, tidak pada URL dan log rutin. Token perangkat, password, kunci, dan isi enrollment tidak boleh terekspos melalui log, pesan kesalahan, atau respons status.
- Admin pertama dibuat tanpa password bawaan menggunakan mekanisme password aplikasi. Instalasi lama mempertahankan admin yang sudah ada. Penyelesaian setup menutup jalur setup pertama; perubahan berikutnya membutuhkan admin lokal.
- URL produksi disediakan sebagai bawaan distribusi dan dapat diganti melalui opsi lanjutan. Pertahankan validasi HTTPS dan verifikasi sertifikat pada transport API; opsi lanjutan tidak menjadi bypass TLS.
- Kontrak API organisasi harus mengembalikan identitas/nama SKPD dan subunit serta pilihan yang diizinkan bagi token perangkat. Penamaan endpoint dan bentuk respons konkret ditetapkan setelah memeriksa API yang sudah ada, bukan diasumsikan tersedia.
- Representasikan cakupan secara eksplisit sebagai seluruh SKPD, induk/non-subunit, atau subunit tertentu. Seluruh SKPD mencakup induk dan seluruh subunitnya; induk/non-subunit hanya pegawai tanpa subunit; subunit tertentu hanya pegawai pada satu subunit dalam SKPD terpilih. Jangan memakai subunit kosong untuk membedakan beberapa arti tersebut.
- Server menjadi sumber kebenaran izin token, hubungan SKPD/subunit, status pegawai, cakupan snapshot, dan penerimaan kejadian. Pilihan lokal tidak dapat memperluas izin. Perubahan SKPD tidak boleh mempertahankan pilihan subunit yang tidak lagi sesuai.
- Perluas provisioning/kontrak server hanya bila diperlukan agar token dapat membatasi pilihan cakupan dan token pengganti dapat diverifikasi sebagai identitas perangkat yang sama. Jangan memperlakukan token uji yang dibatasi SKPD/masa berlaku tertentu sebagai kredensial umum.
- Penyiapan teknis Windows dan Docker mencakup runtime/dependensi yang diperlukan, penyimpanan aplikasi, rahasia awal, dan model. Gunakan packaging serta bootstrap proyek yang tersedia; operator tidak diminta mengisi path teknis. Pilihan mesin penyimpanan dan rincian paket mengikuti kebutuhan aplikasi yang sudah ada, bukan migrasi arsitektur baru dalam spec ini.
- Buat kunci awal hanya untuk penyimpanan baru. Jangan membuat ulang atau mengganti kunci pada instalasi yang memiliki data. Konfigurasi, kunci, dan data Docker harus bertahan di luar umur container.
- Distribusi model memakai sumber yang ditetapkan pengelola dengan versi/hash tepercaya. Model pengenalan harus kompatibel dengan metadata model enrollment server. Unduhan parsial, hash salah, sumber tidak tersedia, dan ketidakcocokan tidak mengaktifkan model atau memicu fallback ke model standalone.
- Kemajuan setup disimpan secara persisten agar proses dapat dilanjutkan, dengan pemisahan rahasia dari data status yang ditampilkan. Langkah gagal dapat diulang tanpa menimpa kunci, menduplikasi admin, atau merusak konfigurasi yang sudah valid. Tidak memerlukan sistem workflow umum.
- Ambil enrollment melalui snapshot integrasi yang sudah ada, validasi sebelum aktivasi, dan simpan terenkripsi. Gunakan mekanisme pembaruan berkala yang sudah ada; kegagalan unduhan tidak mencampur snapshot parsial dengan snapshot lama.
- Bedakan setup belum selesai, setup selesai tetapi belum siap absensi, dan siap absensi. Snapshot awal valid yang kosong dapat menyelesaikan setup. Enrollment kosong, model belum siap, atau kamera belum lolos pemeriksaan ditampilkan sebagai penyebab belum siap.
- Tidak memerlukan kecocokan wajah berhasil untuk menyelesaikan pemeriksaan kamera ketika enrollment kosong. Preview kamera adalah pemeriksaan perangkat, bukan bukti akurasi pengenalan atau tindakan pencatatan absensi.
- Status siap absensi memerlukan konfigurasi dan cakupan valid, model kompatibel, enrollment tersedia, kamera lolos pemeriksaan, serta prasyarat akses dan waktu integrasi yang sudah diwajibkan aplikasi. Wizard tidak menurunkan validasi model, liveness, clock, atau penyimpanan agar status tampak siap.
- Instalasi operasional yang siap langsung aktif tanpa persetujuan aktivasi tambahan. Mode uji hanya digunakan di lingkungan pengembang dan tetap terpisah mengikuti izin server. Setup tidak mengubah kredensial uji menjadi hak absensi resmi.
- Instalasi lama dengan konfigurasi lengkap tidak dipaksa mengulang wizard. Instalasi belum lengkap memulai wizard dengan nilai lama yang dapat dipakai, sambil mempertahankan admin, kunci, snapshot, serta antrean.
- Perubahan cakupan melalui admin lokal hanya diterapkan setelah antrean lama selesai dikirim. Cegah capture baru selama penerapan perubahan dan pastikan antrean tidak bertambah di sela pemeriksaan serta penerapan. Snapshot cakupan baru harus valid sebelum capture dilanjutkan; kegagalan tidak membiarkan galeri lama digunakan sebagai galeri cakupan baru.
- Token kedaluwarsa/dicabut menahan capture baru dan mempertahankan antrean. Pengiriman antrean dengan token pengganti hanya dilanjutkan setelah identitas perangkat yang sama dipastikan server. Jangan menghapus atau mengubah UUID/body kejadian lama untuk mengakomodasi token baru.
- Pemulihan password dijalankan lokal pada Windows atau host Docker dengan mekanisme CLI aplikasi; tidak membuka endpoint reset anonim atau wizard pertama kembali. Data perangkat tidak dihapus.
- Windows menjalankan aplikasi otomatis saat operator masuk, bukan mengasumsikan kamera interaktif dapat berjalan pada layanan sebelum login. Docker menjalankan layanan kembali saat host hidup. Browser kamera tetap memerlukan browser terbuka dan izin kamera.
- Akses kamera browser Docker mendukung localhost dan jaringan HTTPS melalui reverse proxy instansi. Instansi menyediakan domain/sertifikat; gunakan konfigurasi proxy yang benar tanpa mempercayai sembarang forwarded header.
- Hanya satu sesi kamera aktif per instalasi, termasuk percobaan start bersamaan. Gunakan penguncian yang ada; browser kedua menerima status sedang digunakan. Admin lokal dapat menghentikan sesi lama sebelum sesi baru dimulai; sesi lama tidak boleh melanjutkan capture setelah dihentikan.
- Perubahan persisten/schema, jika diperlukan, terbatas pada metadata setup/cakupan dan dukungan izin perangkat. Gunakan mekanisme migrasi yang ada dan pertahankan data instalasi lama. Bentuk schema konkret ditentukan setelah inspeksi modul yang diubah.

## Testing Decisions

- Batas pengujian ini telah dikonfirmasi pengguna: utamakan alur eksternal melalui HTTP aplikasi Flask dengan API eOffice dan unduhan model terkendali; tambahkan pengujian kontrak izin/cakupan pada API, pengujian browser kamera, serta smoke test Windows/Docker.
- Pengujian yang baik membuktikan keluaran yang terlihat operator, penolakan akses, kesiapan perangkat, dan integritas data setelah kegagalan/restart. Hindari pengujian struktur internal, urutan pemanggilan helper, atau penyalinan logika implementasi.
- Gunakan application factory, Flask test client, database/direktori sementara, fixture snapshot, dan transport terkendali yang sudah tersedia. Pengujian autentikasi/CSRF, kepemilikan sesi kamera, penyimpanan terenkripsi, dan sinkronisasi dengan gangguan jaringan merupakan prior art yang dipakai kembali.
- Pengujian HTTP utama mencakup pemasangan baru sampai selesai; kode setup salah/terpakai/diganti; admin pertama dan admin lama; validasi input; tiga jenis cakupan; pemalsuan subunit/izin; URL API tidak valid; serta penutupan wizard setelah selesai.
- Pengujian status membedakan snapshot kosong valid dari unduhan gagal, model tidak tersedia/tidak kompatibel, izin kamera ditolak, dan seluruh pemeriksaan lolos. Preview tidak mencatat attendance; perangkat siap dapat langsung menggunakan alur operasional yang diizinkan server.
- Pengujian pemulihan membuka ulang aplikasi dengan penyimpanan yang sama setelah gangguan pada langkah relevan. Pastikan kemajuan berlanjut, admin/kunci tidak dibuat ulang, snapshot parsial tidak aktif, serta rahasia tidak muncul pada status/log.
- Pengujian regresi instalasi lama membuktikan konfigurasi lengkap melewati wizard dan konfigurasi belum lengkap mempertahankan nilai serta data lama. Bandingkan identitas kunci dan byte/UUID antrean sebelum dan sesudah setup/pemulihan.
- Pengujian kontrak server membuktikan daftar organisasi mengikuti izin token; validasi tiga cakupan konsisten pada snapshot dan kejadian; token kedaluwarsa/dicabut ditolak; token pengganti identitas sama dapat memulihkan akses tanpa memberi perangkat lain akses ke antrean lama. Gunakan server/database uji terisolasi, bukan produksi.
- Pengujian perubahan cakupan mencakup antrean belum kosong, enqueue bersamaan dengan perubahan, snapshot baru gagal, dan snapshot baru valid. Capture hanya kembali berjalan pada cakupan/snapshot yang sesuai.
- Gunakan seam HTTP kamera yang sudah ada untuk dua klien dan permintaan start bersamaan; pastikan hanya satu pemilik, admin dapat menghentikan sesi lama, dan capture sesi lama ditolak sesudah penghentian. Pengujian browser membuktikan preview, pesan izin kamera, status sibuk, dan kontrol yang ditampilkan kepada operator.
- Smoke test Windows memeriksa pemasangan pada lingkungan bersih, penyiapan kebutuhan teknis, kode lokal, kamera nyata, pemulihan password, serta startup setelah login/restart. Smoke test Docker memeriksa bootstrap, persistence setelah container dibuat ulang, restart layanan, localhost, serta akses melalui reverse proxy HTTPS. Hasil simulasi tidak boleh dilaporkan sebagai bukti kamera fisik Windows.
- Pertahankan pemeriksaan integrasi clock, model, enkripsi, antrean, dan idempotensi yang sudah ada pada jalur yang berubah. Tidak perlu menjalankan benchmark atau menambah suite baru untuk komponen yang tidak terpengaruh.

## Out of Scope

- Enrollment wajah lokal atau pendaftaran ulang pegawai melalui wizard.
- Sinkronisasi otomatis catatan standalone menjadi attendance eOffice.
- Penerbitan atau pembaruan sertifikat HTTPS otomatis; domain dan sertifikat dikelola instansi.
- Rotasi/rekey data biometrik atau antrean yang sudah berisi data, penghapusan antrean untuk mengganti cakupan, dan pengambilalihan antrean oleh perangkat lain.
- Beberapa sesi kamera aktif sekaligus dalam satu instalasi.
- Penambahan jadwal, jenis attendance, alur istirahat/event, perubahan threshold/liveness, atau sertifikasi akurasi pengenalan.
- Sistem pengelolaan armada perangkat, workflow setup umum, dan opsi konfigurasi tambahan yang tidak dibutuhkan alur ini.
- Persetujuan aktivasi operasional tambahan setelah seluruh pemeriksaan kesiapan lolos.

## Further Notes

- Seluruh keputusan produk berasal dari wawancara Q1–Q23; pengguna melanjutkan ke penyusunan spec dan menyetujui batas pengujian. Tidak ada wawancara produk tambahan yang diperlukan untuk spec ini.
- Ketersediaan endpoint daftar organisasi, metadata izin token, dukungan tiga cakupan, dan penggantian token dengan identitas perangkat tetap masih harus diverifikasi pada implementasi. Klien lokal saja tidak cukup memenuhi batas izin.
- Jalur uji API yang telah diperiksa saat ini membatasi perangkat ke SKPD 79 dengan masa berlaku 24 jam. Batas tersebut bukan kontrak perangkat operasional umum dan tidak boleh dilepas hanya agar wizard tampak berfungsi.
- Temukan URL produksi dan sumber distribusi resmi seluruh model dari konfigurasi serta dokumentasi proyek. Jika artefak/model atau kontrak API belum tersedia, laporkan ketergantungan yang konkret; jangan mengarang URL, hash, izin, atau mengganti model dengan bobot lain.
- Dokumentasi integrasi yang ada masih mencatat pekerjaan penerimaan teknis. Keputusan langsung aktif menghilangkan langkah persetujuan tambahan dalam produk, tetapi tidak menyatakan bahwa kompatibilitas model, clock, perangkat fisik, dan deployment telah teruji.
- Pengguna menetapkan GitHub Issues di `sizoune/absensi-wajah-kantor` sebagai issue tracker dan meminta spec dipublikasikan dengan label `ready-for-agent` tanpa triage tambahan.
