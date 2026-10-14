# Keputusan Pemilihan Model AI (Tahap 3B)

**Status:** diterapkan, menunggu tinjauan pembimbing
**Tanggal:** 6 Oktober 2026
**Mengubah PRD:** bagian 2.2 dan 7.2 ("Deteksi wajah: YOLO")

## Keputusan

| Fungsi | Model | File | Lisensi |
|---|---|---|---|
| Deteksi wajah | YuNet (OpenCV Zoo) | `face_detection_yunet_2023mar.onnx` | MIT |
| Pengenalan identitas | SFace (OpenCV Zoo) | `face_recognition_sface_2021dec.onnx` | Apache-2.0 |

Keduanya dijalankan dengan OpenCV (`cv2.FaceDetectorYN`, `cv2.FaceRecognizerSF`)
yang sudah menjadi dependensi proyek. Tidak ada pustaka AI tambahan.

## Alasan

PRD 2.2 menyatakan pemilihan model final mempertimbangkan akurasi, kecepatan,
kompatibilitas, dan ketentuan lisensi. Hasil pemeriksaan lisensi:

1. **YOLO dari Ultralytics** berlisensi AGPL-3.0. Menurut Ultralytics, kode,
   model, dan model hasil pelatihan/fine-tuning mereka mewajibkan seluruh
   proyek dirilis sebagai AGPL-3.0 atau membeli Enterprise License.
   Sumber: https://www.ultralytics.com/license
2. **Model InsightFace (ArcFace, mis. buffalo_l)**: kodenya MIT, tetapi model
   siap pakainya hanya untuk riset non-komersial.
   Sumber: https://github.com/deepinsight/insightface
3. **YuNet** (MIT) dan **SFace** (Apache-2.0) dari OpenCV Zoo berlisensi
   longgar, ringan untuk CPU, dan saling melengkapi (YuNet memberi 5 titik
   wajah yang dibutuhkan SFace untuk meluruskan wajah).
   Sumber: https://github.com/opencv/opencv_zoo

## Catatan yang belum terselesaikan

- **Dataset pelatihan.** Hampir semua detektor wajah (termasuk YuNet dan model
  "YOLO face") dilatih dengan dataset WIDER FACE. Ada sumber pihak ketiga yang
  mencatat lisensi dataset tersebut sebagai CC-BY-NC-ND-4.0 (non-komersial),
  tetapi belum dikonfirmasi dari sumber resmi. Persoalan ini sama untuk semua
  pilihan dan perlu ditinjau oleh pihak yang berwenang di Diskominfo.
- **Akurasi** belum diuji pada wajah karyawan dan kondisi kantor sebenarnya.
  Ambang kualitas (`FACE_MIN_SHARPNESS`) dan ambang kecocokan masih sementara
  dan harus dikalibrasi sesuai PRD 13.2.

## Integritas file model

File model diverifikasi dengan SHA-256 yang sama dengan penunjuk Git LFS resmi
OpenCV Zoo. File yang tidak cocok ditolak (`recognition/model_files.py`).

| File | SHA-256 |
|---|---|
| face_detection_yunet_2023mar.onnx | 8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4 |
| face_recognition_sface_2021dec.onnx | 0ba9fbfa01b5270c96627c4ef784da859931e02f04419c829e83484087c34e79 |

## Jika kelak ingin memakai YOLO

Lapisan deteksi memakai antarmuka `detect(frame) -> list[FaceDetection]`
(`recognition/detector.py`). Detektor lain dapat ditambahkan sebagai kelas baru
tanpa mengubah bagian sistem lain, asalkan menghasilkan kotak wajah dan 5 titik
wajah. Perhatikan bahwa mengganti detektor tidak mengubah embedding, tetapi
tetap harus diuji ulang akurasinya.

---

# Tambahan Tahap 4D: anti-foto (keaktifan wajah, FR-11)

**Status:** diterapkan dalam uji coba, menunggu kalibrasi di lokasi
**Tanggal:** 6 Oktober 2026

## Temuan sebelum anti-foto dipasang (TC-19)

Uji di laptop pengembang (kamera bawaan, 6 Oktober 2026 malam): foto wajah
karyawan di layar HP **berhasil dikenali** sebagai karyawan tersebut setelah
sekitar 8 detik (skor kecocokan 0,37–0,40, sedikit di atas ambang 0,363).
Kesimpulan: pengenalan wajah saja tidak dapat membedakan orang asli dan foto.

## Keputusan

Dua lapisan (`recognition/liveness.py`, `LIVENESS_MODE=passive+challenge`):

| Lapisan | Cara kerja | Komponen | Lisensi |
|---|---|---|---|
| A. Pasif (utama) | Model menilai tanda pemotretan ulang (layar, cetakan) | MiniFASNetV2 (skala 2.7) + MiniFASNetV1SE (skala 4.0) | Apache-2.0 |
| B. Aktif (cadangan) | Jika A ragu, kios meminta menoleh ke arah acak | 5 titik wajah YuNet (tanpa model tambahan) | — |

- Sumber arsitektur dan bobot: https://github.com/minivision-ai/Silent-Face-Anti-Spoofing
  (file LICENSE: Apache License 2.0).
- File ONNX: https://github.com/yakhyo/face-anti-spoofing (file LICENSE: Apache License 2.0).
- Model dijalankan dengan `cv2.dnn`; hasilnya identik dengan ONNX Runtime
  (selisih < 1e-5), sehingga tidak ada dependensi tambahan.
- Validasi praproses dengan contoh resmi: gambar asli P(asli)=1,00; gambar
  palsu P(asli)=0,075 dan 0,014.

| File | SHA-256 |
|---|---|
| MiniFASNetV2.onnx | b32929adc2d9c34b9486f8c4c7bc97c1b69bc0ea9befefc380e4faae4e463907 |
| MiniFASNetV1SE.onnx | ebab7f90c7833fbccd46d3a555410e78d969db5438e169b6524be444862b3676 |

## Keterbatasan yang diketahui

- Menurut penyedia model, deteksi keaktifan RGB **bergantung pada model kamera
  dan kondisi lokasi**, dan terpengaruh jika wajah tidak menghadap lurus.
  Ambang `LIVENESS_REAL_THRESHOLD=0.7` dan `LIVENESS_SPOOF_THRESHOLD=0.3` masih
  SEMENTARA dan wajib dikalibrasi dengan C270 di lokasi pemasangan.
- Tantangan menoleh dapat dikalahkan oleh video yang kebetulan menoleh ke arah
  yang sama; arah acak mengurangi, tetapi tidak menghilangkan, risiko ini.
- Topeng 3D berkualitas tinggi tidak termasuk ruang lingkup uji.
- Pendaftaran wajah (`face-enroll`) tidak memakai anti-foto karena dilakukan
  di bawah pengawasan admin.
- **Tidak ada anti-foto yang sempurna.** Pengawasan kios dan absensi alternatif
  tetap diperlukan (PRD 9.3); setiap penolakan tercatat di audit log
  (`liveness_rejected`) untuk ditinjau.

---

# Kalibrasi ambang kecocokan (uji kantor, 7 Oktober 2026)

**Kamera:** Logi C270 di ruang APTIKA. **Karyawan uji:** 7 orang.

| Jenis perbandingan | Skor kemiripan (kosinus SFace) |
|---|---|
| Orang SAMA, menghadap kamera | 0,76 – 0,92 |
| Orang SAMA, antar-sampel pendaftaran | 0,77 – 0,99 |
| Orang BERBEDA (konflik saat pendaftaran) | 0,39 – 0,49 |
| Foto di layar HP (orang sama) | 0,68 (ditolak anti-foto, asli 0,00) |

**Temuan:** ambang awal 0,363 (contoh OpenCV) berada DI BAWAH skor orang
berbeda, sehingga sistem dapat mengenali seseorang sebagai orang lain.

**Keputusan:** `FACE_MATCH_THRESHOLD=0.60`, memberi jarak 0,11 di atas skor
orang berbeda tertinggi dan tetap di bawah skor orang sama saat menghadap kamera.
Frame saat menoleh bisa turun di bawah 0,60; hal ini diterima karena pelacak
hanya membutuhkan 3 kecocokan dari 5 frame.

**Catatan:** SFace adalah model ringan dengan daya beda di bawah model besar
(mis. ArcFace) yang tidak dipakai karena lisensinya. Data di atas berasal dari
sedikit orang; ambang wajib diperiksa ulang dengan `flask --app app
face-similarity-report` setiap kali karyawan baru didaftarkan, dan diuji lagi
saat jumlah karyawan terdaftar bertambah banyak (PRD 13.2).

**Perbaikan pendaftaran:** sejumlah pendaftaran gagal dengan kemiripan
antar-sampel sangat rendah (hingga 0,006), kemungkinan karena wajah operator
ikut terambil tepat setelah Enter ditekan. Ditambahkan hitung mundur 3 detik
(`--delay`) dan jeda antar-sampel 0,6 detik.

---

# Integrasi e-office: pipeline enrollment lokal (14 Oktober 2026)

**Status: implementasi model tersedia; kompatibilitas enrollment dan kesiapan kios
Windows belum diterima.** Modul ini belum dihubungkan ke dispatcher pengenalan.
Keputusan SFace dan ambangnya di atas tetap berlaku untuk mode mandiri. Vektor
SFace, AuraFace, dan periocular tidak boleh dibandingkan satu sama lain.

| Versi embedding | Runtime | Dimensi | Asset operator | SHA-256 referensi |
|---|---|---|---|---|
| `auraface_512` | ONNX Runtime CPU 1.26.0 | 512 | `model_files/glintr100.onnx` | `a7933ea5330113b01c9b60351d8f4c33003f145d8470ac5f0e52ee2effe25c60` |
| `mobilefacenet_v1_periocular` | LiteRT Interpreter 2.3.0 | 192 | `model_files/mobilefacenet.tflite` | `be4bc7cfc53f7bc336d0f28b1ab92535f618c913a422b683210750f6b5354854` |

Referensi AuraFace berasal dari SHA-256 Git LFS
[fal/AuraFace-v1, commit 94ee6ceb](https://huggingface.co/fal/AuraFace-v1/blob/94ee6ceb788a98a88807db884ae1f00d7e070d23/glintr100.onnx).
Hash tersebut **belum dibandingkan dengan bobot server yang terpasang**. Hash
MobileFaceNet sudah dihitung atas asset Android dan iOS dalam repositori dan
keduanya sama; ini belum membuktikan hash aplikasi yang terpasang di perangkat.
Operator memasang bobot asli secara terpisah; tidak ada konversi TFLite,
pengunduhan otomatis, enrollment ulang, atau bobot baru dalam commit ini.

`recognition/eoffice_models.py` menyediakan `AuraFaceEmbedder(model_path)` dan
`PeriocularEmbedder(model_path)`. Keduanya menolak file hilang/hash salah sebelum
memuat runtime, memeriksa bentuk/tipe tensor, lalu menjalankan inferensi sintetis
saat startup. Keluaran nol, non-finite, bentuk salah, atau norm tidak valid
menggagalkan startup; setiap inferensi berikutnya juga diperiksa. AuraFace boleh
memiliki metadata batch simbolik/`None`, tetapi ukuran kanal/spasial/dimensi
wajib cocok dan keluaran nyata wajib `[1,512]`. Tidak ada fallback model.

`model_manifest(paths)` menerima mapping versi embedding ke path, lalu
mengembalikan mapping versi ke `{version, sha256, dimension}`. Dispatcher Task 9
wajib membandingkan hash manifest dengan `models[].weights_sha256` dari snapshot
server terautentikasi sebelum pengenalan diaktifkan. Hash referensi upstream saja
belum membuktikan kecocokan dengan deployment server. Ambang/margin harus berasal
dari kebijakan yang dikalibrasi untuk model masing-masing; jangan memakai ambang
SFace.

Praproses AuraFace mengikuti `face-service/app/recognizer.py`: similarity transform
ke lima landmark ArcFace, `cv2.warpAffine` 112×112 dengan border nol, BGR→RGB,
normalisasi `(pixel-127.5)/127.5`, NCHW float32, dan L2 keluaran. Periocular menerima
frame BGR uint8 yang sudah tegak serta koordinat mata dalam pixel frame yang sama.
Crop memakai aritmetika float32 Android: jarak Euclidean minimal 10 pixel, padding
atas 0.8, bawah 0.5, samping 0.3 dari jarak mata, `Math.round` (setengah ke +∞),
lalu clamp batas gambar. `periocular_tensor(crop_bgr)` menghasilkan RGB NHWC
float32 `[1,112,112,3]` dengan `(pixel-127.5)/128.0`.

**Perbedaan iOS:** `PeriocularImagePreprocessor.swift` menggunakan CGRect
fraksional/intersection, hanya menolak jarak nol, dan resize CoreGraphics;
Android memakai integer `Math.round`, batas minimal 10 pixel, dan resize Bitmap.
Kios mengikuti crop Android sesuai keputusan sementara. Resize OpenCV bilinear
belum dibuktikan identik pixel demi pixel dengan Bitmap/CoreGraphics. Kesamaan
hash dan konstanta normalisasi tidak membuktikan paritas crop iOS. Golden Android
**dan** iOS serta uji berjalan dengan template asal iOS wajib masuk penerimaan
sebelum aktivasi resmi; jangan mengatasinya dengan enrollment ulang atau mode
runtime terpisah. Paritas dengan landmark tetap juga tidak membuktikan paritas
landmark detektor kios/ML Kit maupun deteksi cadar.

Instalasi integrasi terisolasi: `python -m pip install -r requirements-eoffice.txt`,
lalu `python -m pip check`. Berkas ini menyertakan semua pin `requirements.txt`
serta ONNX Runtime, LiteRT, dan scikit-image 0.25.2, dengan GUI OpenCV tetap dipakai.
[LiteRT migration](https://developers.google.com/edge/litert/migration) mendokumentasikan
`from ai_edge_litert.interpreter import Interpreter`.
[PyPI LiteRT 2.3.0](https://pypi.org/project/ai-edge-litert/2.3.0/),
[ONNX Runtime 1.26.0](https://pypi.org/project/onnxruntime/1.26.0/), dan
[scikit-image 0.25.2](https://pypi.org/project/scikit-image/0.25.2/) menyediakan
wheel Windows CPython 3.11 x64. Resolusi wheel seluruh dependensi Windows x64
berhasil di macOS; instalasi, `pip check`, dan inferensi pada **kios Windows
sebenarnya belum dijalankan**. Arsitektur mesin kios harus dikonfirmasi operator.

Pengujian model terpisah dari kamera/transport:

- `python -m pytest tests/test_eoffice_models.py -q`: tensor sintetis, rounding,
  clamp, validasi input/hash/runtime, dan L2; tidak memakai data pegawai.
- `python -m pytest tests/test_eoffice_model_parity.py -q -rs -s`: tes yang
  membutuhkan asset akan melaporkan **NOT RUN** ketika input belum tersedia.
  Skip tidak dihitung sebagai keberhasilan paritas.
- `EOFFICE_MOBILEFACENET_PATH` dan `EOFFICE_AURAFACE_PATH` menunjuk bobot operator;
  default adalah path pada tabel di atas. Path eksplisit yang salah menggagalkan
  tes. `EOFFICE_SERVER_RECOGNIZER` menunjuk sumber recognizer server; default
  `../api/face-service/app/recognizer.py` relatif root CAM.
- `EOFFICE_CONSENTED_AURAFACE_NPZ` berisi `frame_bgr` uint8 dan `landmarks` 5×2
  dari sampel non-produksi berizin dengan orientasi yang sudah dinormalisasi.
- `EOFFICE_CONSENTED_ANDROID_GOLDEN_NPZ` dan `EOFFICE_CONSENTED_IOS_GOLDEN_NPZ`
  masing-masing berisi `frame_bgr`, `eye_landmarks` 2×2, `tensor` float32
  `[1,112,112,3]` hasil pipeline mobile, dan `embedding` float32 `[192]` hasil
  mobile untuk frame/landmark yang sama. Operator mengekspor masing-masing dari
  aplikasi asli; jangan memakai foto/template enrollment produksi.
- Paritas embedding menuntut cosine ≥0.999 serta mencatat max absolute difference
  melalui output dan properti pytest. Mobile diuji pada tensor tetap, kemudian
  pipeline crop lengkap; tensor hasil crop juga harus persis sama. AuraFace
  dibandingkan langsung dengan recognizer server. Sampel sintetis berwarna boleh
  menguji runtime/transform, tetapi bukan pengganti penerimaan sampel berizin.

Pada mesin pengembang ini, inferensi sintetis MobileFaceNet asli dan kesamaan
tensor AuraFace dengan sumber server lulus. AuraFace nyata, golden Android/iOS,
sampel berizin, deployment hash, kios Windows, kamera C270, dan uji berjalan/cadar
belum divalidasi. Tidak ada klaim kesiapan resmi atau aktivasi produksi.

Perbaikan tinjauan Task 6: referensi server selalu menambahkan nama
`glintr100.onnx`. Tes paritas kini menyalin asset operator yang dipilih ke direktori
sementara dengan nama tersebut, memverifikasi kembali manifest/hash yang sama,
lalu memuat referensi dari sana. Path operator bernama lain tidak akan dibandingkan
dengan file tetangga yang berbeda. Salinan sementara hanya digunakan saat tes,
dibersihkan sesudahnya, dan tidak masuk Git; sediakan ruang disk sementara sebesar
asset. Kontrak Python publik kini memakai tipe path/mapping/array dan metadata
manifest yang konkret, tanpa mengganti validasi runtime.

**Gate penerimaan Task 10 tetap terbuka:** T6-I03 (bobot deployment/paritas nyata),
T6-I04 (golden Android/iOS serta kamera/cadar/walking), dan T6-I05 (instalasi/check/
inferensi pada arsitektur kios Windows sebenarnya) adalah penghalang aktivasi resmi.
Perbaikan kode, tes sintetis, dan resolusi wheel belum memenuhi gate tersebut.
