# Integrasi absensi kamera kantor dengan e-office

Tanggal: 14 Oktober 2026 (WITA)
Status: desain percakapan disetujui; dokumen ini menunggu tinjauan pengguna.
Repositori terkait: `absensi-wajah-kantor`, `e-office-api-main`, serta aplikasi mobile sebagai acuan model periocular dan pengujian kompatibilitas.

## 1. Tujuan dan keputusan pengguna

Pegawai Diskominfo dapat absen masuk pagi dan pulang dengan berjalan sambil menghadap satu kamera eksternal, tanpa berhenti, memilih akun, atau mendaftar wajah lagi. Kamera menjadi alternatif aplikasi HP. Catatan resmi, jadwal, dan perhitungan absensi tetap dikelola e-office.

Keputusan yang sudah disetujui:

- Satu kamera untuk masuk dan pulang; sesi ditentukan dari waktu kejadian dan jadwal e-office.
- Tahap pertama hanya masuk pagi dan pulang, tanpa sesi istirahat atau kegiatan/event.
- Pengenalan berjalan di komputer kamera. E-office menerima kejadian, menentukan sesi, dan mencatat absensi.
- Enrollment berasal dari e-office: AuraFace untuk wajah penuh dan periocular untuk pegawai bercadar. Tidak ada enrollment ulang di kios.
- Sinkronisasi hanya mencakup pegawai Diskominfo yang aktif dan enrollment yang diizinkan e-office.
- Koneksi putus: simpan kejadian secara persisten dan kirim otomatis ketika koneksi kembali.
- Pengiriman tertunda tetap dinilai berdasarkan waktu kejadian.
- Catatan sah yang lebih dahulu diterima e-office dipertahankan. Kejadian berikutnya untuk sesi sama menjadi audit duplikat; waktu resmi tidak ditimpa.
- Hasil pengenalan ambigu atau pemeriksaan anti-foto gagal tidak boleh menghasilkan absensi.

Keberhasilan berarti kedua kelompok pegawai dapat memakai kamera tanpa enrollment ulang, hasil resmi muncul di rekap yang sama dengan HP, dan pengiriman ulang maupun benturan dengan HP tidak mengubah catatan sah yang sudah ada.

## 2. Temuan kode dan batas bukti

| Bagian | Kondisi yang diperiksa | Implikasi desain |
| --- | --- | --- |
| `absensi-wajah-kantor/recognition/embedder.py` | SFace, 128 dimensi | Ganti jalur pengenalan mode integrasi; templat SFace tidak cocok dengan AuraFace/periocular. |
| `absensi-wajah-kantor/recognition/liveness.py` | Pemeriksaan pasif dapat menerima tanpa tantangan; hasil ragu dapat memicu menoleh | Mode integrasi berjalan tanpa tantangan yang memaksa berhenti; hasil ragu ditolak. |
| `absensi-wajah-kantor/kiosk/worker.py` | Pelacakan, gerbang liveness, pendaftaran dan pencatatan lokal sudah ada | Pakai kembali alur kamera/pelacakan; pengesahan absensi mode integrasi diarahkan ke e-office. |
| `e-office-api-main/face-service/app/recognizer.py` | AuraFace `glintr100.onnx`, 512 dimensi | Gunakan bobot, alignment, preprocessing, dan normalisasi yang identik di kios. |
| `e-office-api-main/query/FaceQuery.js` | Templat server dapat berupa satu atau beberapa vektor 512; ada status enrollment dan dekripsi | Sinkronisasi harus membawa versi/model serta bentuk sampel secara eksplisit. |
| `e-office-api-main/query/BiometricQuery.js` | Periocular 192 dimensi, versi `mobilefacenet_v1_periocular` | Periocular merupakan jalur model tersendiri, bukan potongan mata yang dimasukkan ke AuraFace. |
| Android `util/FaceEmbeddingHelper.java` dan `util/PeriocularImagePreprocessor.java` | `mobilefacenet.tflite`, crop area mata 112×112, preprocessing khusus | Pakai bobot dan preprocessing yang sama; kesamaan nama model saja tidak cukup. |
| `e-office-api-main/api/APIabsensi.js` dan `query/AbsensiQuery.js` | Jadwal, tanggal kerja, sesi/detail, rekap dan penulisan absensi sudah ada | Integrasi harus memakai aturan dan penyimpanan ini, dengan pencegahan benturan bersama HP. |

Pengguna mengonfirmasi bahwa data wajah penuh yang digunakan sekarang seluruhnya AuraFace, kecuali periocular. Dukungan model wajah penuh lama tidak masuk fitur ini; kode lama tidak dihapus sebagai bagian integrasi.

Database produksi, bobot model yang sedang dipakai di produksi, kamera, kemampuan komputer kios, dan kualitas pengenalan sambil berjalan belum diuji. Temuan kode bukan bukti kelayakan perangkat atau akurasi lapangan.

## 3. Arsitektur dan tanggung jawab

### E-office sebagai sumber otoritatif

E-office menyediakan API khusus perangkat kios untuk sinkronisasi templat dan penerimaan kejadian. Perangkat memiliki identitas dan kredensial sendiri, dengan izin hanya untuk Diskominfo; kredensial pegawai mobile tidak dipakai untuk kios bersama.

E-office menentukan kelayakan pegawai, tipe verifikasi, enrollment yang boleh digunakan, jadwal, tanggal kerja, sesi, status kehadiran, keterlambatan, serta hasil akhir. Perangkat tidak boleh mengirim keputusan masuk/pulang atau perhitungan keterlambatan sebagai nilai otoritatif.

### Komputer kamera

Kios membaca kamera, mendeteksi dan melacak subjek, menghasilkan ciri wajah/mata, mencocokkan dengan templat tersinkron, memeriksa keaslian, lalu menyimpan kejadian sebelum mengirimnya. Kios menampilkan hasil kepada pegawai dan menangani antrean saat koneksi putus.

Mode integrasi tidak menjalankan aturan absensi lokal magang sebagai aturan resmi kedua. Fitur standalone yang sudah ada dipertahankan; mode integrasi dipilih secara eksplisit saat konfigurasi kios dan tidak boleh mencatat melalui dua jalur sekaligus.

### Batas kepercayaan

Kios adalah perangkat kantor yang dikelola, bukan endpoint publik yang menerima identitas pegawai tanpa autentikasi. Server memvalidasi kredensial, cakupan, payload, enrollment, dan bukti keputusan yang dilaporkan perangkat. Kredensial kios tidak memberikan hak enrollment, koreksi, pembatalan, atau akses seluruh SKPD.

Pencocokan lokal mempercayai perangkat yang dikelola. Perangkat yang sepenuhnya dikuasai penyerang tidak dapat dijamin kejujuran hasilnya hanya dengan tanda tangan payload; mitigasi tahap ini adalah pembatasan akses komputer, kredensial per perangkat, pencabutan akses, dan audit.

## 4. Sinkronisasi enrollment

API mengembalikan snapshot konsisten dan berversi yang hanya memuat data minimum: ID pegawai e-office, nama untuk konfirmasi layar, tipe verifikasi, ID/revisi enrollment, versi model, dimensi dan sampel templat yang valid. Status pegawai dan cakupan unit ditentukan server.

Untuk AuraFace, pertahankan seluruh sampel jika enrollment menggunakan bentuk multi-vektor; jangan salah menganggapnya satu vektor panjang. Untuk periocular, gunakan format 192 dimensi yang tersimpan. Vektor beda model tidak pernah dibandingkan.

Snapshot diambil saat startup dan diperbarui berkala serta setelah koneksi pulih. Respons sinkronisasi memuat interval pembaruan yang ditetapkan server. Snapshot lengkap baru menggantikan snapshot lama setelah unduhan dan validasi seluruh halaman berhasil. Kegagalan parsial tidak menghapus atau mencampur isi cache.

Perubahan tipe verifikasi, pencabutan, perpindahan unit, dan pegawai nonaktif menghapus templat dari cache pada sinkronisasi berikutnya. Sebelum mencatat, server tetap memeriksa bahwa revisi enrollment dalam kejadian masih diizinkan. Kejadian dari revisi yang sudah dicabut/diganti ditolak dan diaudit; cache offline bukan izin untuk melewati pencabutan.

Kebijakan “aktif dan disetujui” mengikuti kewenangan e-office untuk tiap tipe. Jalur periocular yang diperiksa menyimpan status `active` langsung setelah pendaftaran bagi akun yang diizinkan. Jangan mensyaratkan `verified_by` non-null secara seragam jika jalur periocular tidak memakai kolom tersebut sebagai persetujuan; keputusan kelayakan ada pada server dan harus diuji untuk kedua tipe.

Cache biometrik disimpan terenkripsi dengan kunci lokal dan izin file terbatas, menggunakan fasilitas enkripsi yang sudah ada bila sesuai. Transfer melalui TLS. Jangan menyalin kunci utama enkripsi e-office ke kios atau menulis templat/kredensial ke log. Foto dan video tidak diperlukan untuk sinkronisasi.

## 5. Pengenalan saat pegawai berjalan

Kios menjalankan jalur AuraFace dan periocular pada subjek yang terlacak, tanpa meminta pegawai memilih akun. Masing-masing jalur hanya mencari kandidat dalam kelompok enrollment yang sesuai. Penetapan periocular mengikuti data e-office, bukan inferensi pakaian atau fallback bebas untuk semua pegawai.

Identitas harus konsisten lintas frame, melewati ambang kualitas, ambang kecocokan, dan selisih terhadap kandidat kedua. Ambang dikalibrasi untuk pencarian satu-ke-banyak; nilai verifikasi satu-ke-satu dari HP tidak otomatis aman dipakai pada galeri pegawai. Bila kedua jalur menghasilkan identitas berbeda yang sama-sama lolos, tolak sebagai ambigu.

Liveness memakai pengamatan pasif lintas frame, tanpa tantangan yang memaksa berhenti, menoleh, berkedip atas perintah, atau membuka cadar. Hasil ragu, sampel kurang, atau subjek hilang sebelum keputusan berarti tidak mencatat. Jangan meloloskan hasil ragu dengan menurunkan ambang agar target berjalan terlihat tercapai.

Liveness wajah penuh tidak otomatis dianggap valid untuk area mata/bercadar. Jalur periocular harus diuji terhadap foto, layar, dan wajah asli dengan cadar sebelum dipakai resmi. Jika pendekatan pasif gagal memenuhi pengujian, pengaktifan produksi tertahan sampai pendekatan/perangkat diperbaiki dan hasilnya ditinjau pengguna; cakupan pegawai bercadar tidak dihapus diam-diam.

Pelacakan dan jeda lokal mengurangi kejadian berulang, tetapi bukan pengganti pemeriksaan duplikat server. Subjek berbeda tidak boleh digabungkan ketika beberapa pegawai berada di frame.

## 6. Kontrak kejadian dan hasil

Satu kejadian tersimpan memiliki UUID yang tetap untuk semua retry, ID kios, ID pegawai, `occurred_at` ber-offset WITA, ID/revisi snapshot dan enrollment, tipe/versi model, serta ringkasan kualitas, kecocokan, kandidat kedua, dan hasil liveness. Tidak perlu menyertakan video atau templat galeri dalam setiap kejadian.

Server membatasi ukuran dan tipe payload, menolak nilai numerik non-finite, versi/model tak dikenal, waktu tidak masuk akal, perangkat di luar cakupan, serta enrollment yang tidak layak. Payload untuk UUID yang sama harus identik; isi berbeda ditolak sebagai konflik, bukan dianggap retry sah.

Hasil terminal yang dibutuhkan: `recorded`, `duplicate`, `outside_schedule`, `rejected`. Hasil memuat alasan yang aman ditampilkan, ID kejadian, dan referensi catatan resmi bila ada. HTTP sementara/timeout/layanan tidak tersedia memicu retry; penolakan terminal tidak diulang tanpa batas. Kredensial ditolak menghentikan pengiriman dan memunculkan kebutuhan tindakan operator, sambil mempertahankan antrean.

Duplikat akibat UUID yang sama mengembalikan keputusan yang sudah tersimpan. Kejadian lain untuk sesi yang sudah sah mendapat `duplicate` dengan referensi catatan pemenang. Keputusan dan audit tidak boleh menyimpan templat biometrik dalam log umum.

## 7. Jadwal dan pencatatan bersama HP

Server memetakan `occurred_at` ke tanggal kerja dan jendela sesi masuk pagi atau pulang memakai aturan e-office. Hari libur, jadwal per hari, shift, toleransi, dan pengecualian menggunakan sumber yang sama dengan mobile. Tidak ada jam tetap baru yang ditanam di aplikasi kamera.

Di luar jendela sesi, server mengembalikan `outside_schedule`. Jika konfigurasi menghasilkan dua sesi kandidat yang tumpang tindih dan tidak memiliki prioritas tegas dalam aturan e-office, kejadian ditolak sebagai jadwal ambigu; jangan memilih sesi berdasarkan tebakan atau urutan array.

Untuk kejadian tertunda, gunakan jadwal yang berlaku pada tanggal/waktu kejadian. Jika pengeditan jadwal dapat menghilangkan konfigurasi historis, perubahan penerimaan kamera harus menyimpan/mengakses versi jadwal historis yang dibutuhkan. Bila versi otoritatif tidak dapat ditentukan, tolak dengan alasan eksplisit dan audit; jangan diam-diam memakai jadwal hari pengiriman atau percaya keputusan jadwal dari kios.

“Lebih dahulu diterima” didefinisikan sebagai catatan sah yang lebih dulu berhasil disahkan atomik di database e-office, bukan urutan timestamp jaringan. Unit keunikannya adalah pegawai + tanggal kerja + sesi resmi, memakai identitas sesi yang sama dengan detail absensi e-office.

Pemeriksaan sesi kosong dan penulisan harus menjadi satu operasi atomik untuk kamera DAN jalur mobile yang menulis sesi yang sama. Gunakan transaksi dan penguncian/constraint yang sesuai skema yang ada. Pemeriksaan lalu penulisan terpisah atau lock khusus kamera saja tidak memenuhi desain.

Hasil idempotensi, perubahan catatan resmi, dan audit keberhasilan/duplikat harus konsisten dalam transaksi. Kehilangan respons setelah commit aman: retry mengembalikan keputusan yang sama. Penulisan kamera memakai kolom dan aturan rekap resmi, dengan sumber `kamera_kantor` dan referensi perangkat/kejadian untuk audit; jangan memalsukan identitas device HP.

Contoh wajib:

| Keadaan | Hasil |
| --- | --- |
| Kamera offline menangkap 07.25, HP disahkan 07.30, kamera dikirim 07.40 | Waktu resmi tetap 07.30; kejadian 07.25 diaudit sebagai duplikat. |
| Kamera disahkan untuk masuk, HP mencoba sesi masuk sama | Kamera tetap berlaku; HP tidak menimpa catatan resmi. |
| Masuk lewat kamera, pulang lewat HP | Dua sesi berbeda sah, tanpa duplikasi. |
| Masuk lewat HP, pulang lewat kamera | Dua sesi berbeda sah, tanpa duplikasi. |
| Respons hilang setelah kamera berhasil disahkan | UUID sama mengembalikan hasil sebelumnya. |
| Dua kejadian kamera berbeda pada sesi sama | Satu catatan resmi; kejadian lain duplikat. |

Jika sesi sudah berisi status resmi seperti cuti/izin atau koreksi admin, kamera tidak menimpanya. Koreksi berikutnya tetap memakai alur admin yang ada, bukan sinkronisasi kios.

## 8. Antrean offline dan waktu

Kejadian yang lolos pengenalan harus di-commit ke penyimpanan lokal sebelum pengiriman. Gunakan database lokal yang tersedia dengan transaksi; antrean harus dapat bertahan setelah proses mati atau komputer restart. Pengiriman berjalan di luar loop frame agar koneksi lambat tidak menghentikan kamera.

Status lokal minimum: `pending` dan hasil terminal beserta respons server. Timeout mempertahankan `pending`. Retry memakai backoff terbatas dan UUID/payload tetap; antrean dikirim menurut waktu kejadian, dengan urutan stabil ketika waktunya sama. Urutan ini tidak mengalahkan catatan HP yang sudah disahkan server.

Hasil terminal disimpan sebagai bukti hasil dan tidak dikirim terus-menerus. Tidak ada penghapusan otomatis kejadian pending untuk mengatasi ruang disk. Jika penyimpanan gagal/penuh, kios menampilkan gangguan dan tidak menyatakan kejadian sudah tersimpan atau berhasil absen.

Waktu server dan sistem kios disinkronkan. Catat pemeriksaan selisih waktu saat terhubung serta deteksi perubahan jam selama proses berjalan. Jam masa depan, lompatan jam, atau waktu yang tidak dapat dipercaya menyebabkan penolakan/penahanan dengan alasan operator, bukan penggantian waktu kejadian dengan waktu penerimaan. Setelah restart offline, kejadian hanya boleh dibuat bila sistem dapat memastikan waktu perangkat tetap terpercaya.

Sebelum sinkronisasi pertama berhasil, kios tidak mengenali pegawai dari galeri kosong. Setelah pernah tersinkron, cache terakhir dapat digunakan saat offline; pencatatan resmi tetap menunggu pemeriksaan ulang server.

## 9. Tampilan dan operasi

Layar kios menampilkan konfirmasi singkat nama pegawai dan hasil, tanpa mengharuskan interaksi: berhasil masuk/pulang, sudah absen, di luar jadwal, gagal dikenali/verifikasi, atau menunggu sinkronisasi. Jangan menyatakan “berhasil absen” hanya karena pengenalan atau penyimpanan offline berhasil.

Keberhasilan visual disertai bunyi singkat yang berbeda dari kegagalan bila perangkat mendukung, agar konfirmasi tidak bergantung pada warna atau suara saja. Gangguan global menampilkan keadaan kamera, koneksi, penyimpanan, dan kebutuhan tindakan operator secara ringkas. Detail skor dan templat tidak ditampilkan kepada orang yang lewat.

Operator memiliki petunjuk konfigurasi perangkat, kredensial, unit Diskominfo, model, waktu, pencabutan akses, pengecekan antrean, dan pemulihan setelah gangguan. Kamera dipasang sehingga wajah/area mata terlihat cukup lama pada kecepatan berjalan normal. Satu kamera berbasis jadwal tidak membuktikan arah perjalanan; orang yang lewat dalam jendela pulang dapat tercatat pulang walaupun tidak benar-benar meninggalkan kantor. Pembatasan ini sudah disampaikan dalam percakapan.

## 10. Cakupan perubahan dan penggunaan kembali

Perubahan meliputi: pemuatan model dan templat di kios, jalur worker/gerbang liveness mode integrasi, antrean/pengiriman, konfigurasi dan dokumentasi operasional; API sinkronisasi dan penerimaan e-office, penyimpanan audit/idempotensi, akses jadwal historis jika diperlukan, serta jalur penulisan sesi mobile yang harus berbagi proteksi atomik.

Gunakan kembali pelacakan, penanganan kamera, status kios, enkripsi lokal yang sesuai, query pegawai/enrollment, dekripsi e-office, aturan jadwal, perhitungan absensi, serta format respons yang sudah ada. Perubahan mobile difokuskan pada server penulisannya; tidak menambah layar enrollment atau pilihan akun di kamera.

Pengujian dan fixture perlu mencakup templat AuraFace tunggal/multi-sampel, periocular, pegawai nonaktif/di luar cakupan, enrollment dicabut, sesi yang sudah diisi HP/admin, jadwal lintas tanggal, dan kejadian offline. Jangan memakai data biometrik produksi dalam fixture atau commit.

Tidak termasuk: sesi istirahat, integrasi event, perluasan ke SKPD lain, enrollment kios baru, penyatuan templat berbeda model, dashboard baru, perekaman video kontinu, atau perombakan fitur standalone dan model lama yang tidak diperlukan integrasi.

## 11. Validasi dan syarat pemakaian resmi

Pengujian otomatis harus membuktikan:

1. Snapshot dibatasi server, lengkap sebelum diganti, terenkripsi di disk, dan penghapusan/pencabutan terpropagasi.
2. Versi, dimensi, preprocessing dan bobot model kompatibel. Sampel uji yang sama memberi hasil sepadan antara jalur enrollment dan kios; perbedaan pembulatan/platform dinilai berdasarkan dampaknya pada kecocokan.
3. Identitas ambigu, dua kandidat dekat, konflik lintas model, kualitas rendah, sampel kurang, serta liveness gagal tidak menghasilkan kejadian absensi.
4. Kejadian pending bertahan setelah restart, retry stabil, respons hilang setelah commit aman, payload UUID yang berubah ditolak, dan galat terminal tidak berulang tanpa batas.
5. Permintaan HP dan kamera yang benar-benar bersamaan menghasilkan satu pemenang dan tidak menimpa catatan sah. Uji transaksi memakai database yang mendukung perilaku penguncian produksi, bukan mock semata.
6. Jadwal dinilai pada waktu kejadian, termasuk pengiriman hari/bulan berikutnya, perubahan jadwal, libur, batas jendela, dan waktu tidak terpercaya.
7. Penolakan perangkat/pegawai/enrollment, disk penuh, model gagal dimuat, kamera terputus, dan layanan e-office gagal menghasilkan status yang benar tanpa keberhasilan palsu.
8. Rekap dan perhitungan resmi tetap konsisten untuk catatan dari kamera, HP, serta koreksi admin.

Uji lapangan terpisah wajib dilakukan untuk AuraFace dan periocular pada kamera/komputer yang akan dipakai, dengan pegawai pemilik enrollment yang sudah ada: berjalan normal, pencahayaan kantor, jarak pemasangan, kacamata/cadar, dan beberapa orang lewat berdekatan. Catat jumlah percobaan, keberhasilan, penolakan sah, salah identitas, latensi, serta kegagalan anti-foto. Uji foto cetak dan layar untuk kedua jalur. Ambang dan hasil kalibrasi ditulis dalam laporan tanpa data biometrik mentah.

Pengaktifan resmi mensyaratkan kedua tipe bekerja tanpa enrollment ulang dan tanpa harus berhenti pada skenario operasional yang diuji, tidak ada salah identitas atau penerimaan spoof pada set uji, dan pengujian konsistensi data lulus. Nol kesalahan dalam set uji bukan jaminan nol kesalahan di lapangan; laporan hasil dan batas kondisi harus ditinjau pengguna sebelum pengaktifan resmi. Jika salah satu tipe gagal, perbaiki dan uji ulang sebelum mengklaim fitur lengkap.

## 12. Tinjauan dokumen dan tahap berikutnya

Dokumen ini merekam desain, bukan implementasi atau bukti pengujian. Lokasi dipilih di repositori aplikasi kamera agar spesifikasi lintas aplikasi dapat di-commit; folder induk workspace bukan repositori Git.

Setelah pengguna meninjau dan menyetujui dokumen ini, tahap berikutnya adalah rencana implementasi dengan skill `superpowers:writing-plans`. Rencana harus menentukan lokasi perubahan yang tepat, kebutuhan migrasi berdasarkan skema nyata, dependensi runtime model, dan urutan pengujian. Implementasi dimulai setelah rencana ditinjau dan metode eksekusinya dipilih pengguna, sesuai skill brainstorming yang diminta.
