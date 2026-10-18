"""
Daftar model AI yang dipakai, beserta sumber, lisensi, dan sidik jari SHA-256.

Keputusan pemilihan model (Tahap 3B): PRD awalnya menyebut YOLO untuk deteksi.
Setelah pemeriksaan lisensi, dipakai YuNet (MIT) dan SFace (Apache-2.0) dari
OpenCV Zoo karena lisensinya longgar untuk instansi, tidak membutuhkan pustaka
tambahan, dan ringan di CPU. Lihat docs/MODEL_DECISION.md.

Setiap file model WAJIB cocok dengan SHA-256 di bawah. File yang tidak cocok
ditolak, sehingga model yang rusak atau diubah pihak lain tidak akan dipakai.
"""
import hashlib
import os
import urllib.request
from urllib.error import HTTPError
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ModelFile:
    key: str
    filename: str
    sha256: str
    size: int
    license: str
    source: str
    urls: tuple


YUNET = ModelFile(
    key="yunet",
    filename="face_detection_yunet_2023mar.onnx",
    sha256="8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4",
    size=232589,
    license="MIT",
    source="https://github.com/opencv/opencv_zoo/tree/main/models/face_detection_yunet",
    urls=(
        "https://media.githubusercontent.com/media/opencv/opencv_zoo/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx",
        # Cermin cadangan. Aman karena isi tetap diverifikasi dengan SHA-256 resmi.
        "https://raw.githubusercontent.com/WasanthaSampath/face-verification-dotnet/HEAD/face_detection_yunet_2023mar.onnx",
    ),
)

SFACE = ModelFile(
    key="sface",
    filename="face_recognition_sface_2021dec.onnx",
    sha256="0ba9fbfa01b5270c96627c4ef784da859931e02f04419c829e83484087c34e79",
    size=38696353,
    license="Apache-2.0",
    source="https://github.com/opencv/opencv_zoo/tree/main/models/face_recognition_sface",
    urls=(
        "https://media.githubusercontent.com/media/opencv/opencv_zoo/main/models/face_recognition_sface/face_recognition_sface_2021dec.onnx",
        "https://raw.githubusercontent.com/WasanthaSampath/face-verification-dotnet/HEAD/face_recognition_sface_2021dec.onnx",
    ),
)

# Model anti-foto pasif (Tahap 4D). Arsitektur dan bobot asli dari
# Silent-Face-Anti-Spoofing (Minivision, Apache-2.0); file ONNX dari
# yakhyo/face-anti-spoofing (Apache-2.0).
MINIFAS_V2 = ModelFile(
    key="minifas_v2",
    filename="MiniFASNetV2.onnx",
    sha256="b32929adc2d9c34b9486f8c4c7bc97c1b69bc0ea9befefc380e4faae4e463907",
    size=1743581,
    license="Apache-2.0",
    source="https://github.com/minivision-ai/Silent-Face-Anti-Spoofing",
    urls=("https://github.com/yakhyo/face-anti-spoofing/releases/download/weights/MiniFASNetV2.onnx",),
)

MINIFAS_V1SE = ModelFile(
    key="minifas_v1se",
    filename="MiniFASNetV1SE.onnx",
    sha256="ebab7f90c7833fbccd46d3a555410e78d969db5438e169b6524be444862b3676",
    size=1742335,
    license="Apache-2.0",
    source="https://github.com/minivision-ai/Silent-Face-Anti-Spoofing",
    urls=("https://github.com/yakhyo/face-anti-spoofing/releases/download/weights/MiniFASNetV1SE.onnx",),
)

AURAFACE = ModelFile(
    key="auraface_512", filename="glintr100.onnx",
    sha256="a7933ea5330113b01c9b60351d8f4c33003f145d8470ac5f0e52ee2effe25c60",
    size=260694151, license="Apache-2.0",
    source="https://huggingface.co/fal/AuraFace-v1/tree/94ee6ceb788a98a88807db884ae1f00d7e070d23",
    urls=("https://huggingface.co/fal/AuraFace-v1/resolve/94ee6ceb788a98a88807db884ae1f00d7e070d23/glintr100.onnx",),
)

MOBILEFACENET = ModelFile(
    key="mobilefacenet_v1_periocular", filename="mobilefacenet.tflite",
    sha256="be4bc7cfc53f7bc336d0f28b1ab92535f618c913a422b683210750f6b5354854",
    size=5233552, license="",
    source="eOffice authenticated device distribution", urls=(),
)

FACE_MODELS = (YUNET, SFACE)
LIVENESS_MODELS = (MINIFAS_V2, MINIFAS_V1SE)
ALL_MODELS = FACE_MODELS + LIVENESS_MODELS


class ModelFileError(Exception):
    """File model tidak ada, rusak, atau sidik jarinya tidak cocok."""


class ModelDownloadError(ModelFileError):
    def __init__(self, message, reason, downloaded_bytes=0):
        super().__init__(message)
        self.reason = reason
        self.downloaded_bytes = downloaded_bytes


def sha256_of(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def model_path(model_dir, model):
    return Path(model_dir) / model.filename


def verify(model_dir, model):
    """Mengembalikan path jika file ada dan sidik jarinya cocok."""
    path = model_path(model_dir, model)
    if not path.exists():
        raise ModelFileError(
            f"File model {model.filename} belum ada di {model_dir}. "
            "Jalankan: flask --app app models-download"
        )
    digest = sha256_of(path)
    if digest != model.sha256:
        raise ModelFileError(
            f"Sidik jari {model.filename} tidak cocok (file rusak atau bukan file "
            "resmi). Hapus file tersebut lalu jalankan models-download lagi."
        )
    return path


def download(model_dir, model, opener=urllib.request.urlopen, timeout=60, progress=None):
    """Mengunduh model dari daftar URL, berhenti di URL pertama yang sidik jarinya cocok."""
    target = model_path(model_dir, model)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".part")
    errors = []
    reason, received = 'unavailable', 0
    for url in model.urls:
        try:
            h = hashlib.sha256()
            received = 0
            with opener(url, timeout=timeout) as resp, open(tmp, "wb") as out:
                for chunk in iter(lambda: resp.read(min(1024 * 1024, model.size - received + 1)), b""):
                    received += len(chunk)
                    if received > model.size:
                        raise ModelDownloadError('sidik jari atau ukuran tidak cocok', 'hash_mismatch')
                    h.update(chunk)
                    out.write(chunk)
                    if progress is not None:
                        progress(received)
            if h.hexdigest() != model.sha256 or received != model.size:
                reason = 'partial' if received < model.size else 'hash_mismatch'
                errors.append(f"{url}: sidik jari tidak cocok")
                continue
            os.replace(tmp, target)
            return target
        except Exception as exc:  # jaringan gagal, URL tidak tersedia, dll.
            if isinstance(exc, HTTPError):
                exc.close()
            reason = getattr(exc, 'reason', None)
            if reason not in ('partial', 'hash_mismatch'):
                reason = 'partial' if received else 'unavailable'
            detail = 'sidik jari tidak cocok' if isinstance(exc, ModelDownloadError) else exc.__class__.__name__
            errors.append(f"{url}: {detail}")
        finally:
            if tmp.exists():
                tmp.unlink()
    raise ModelDownloadError(
        f"Gagal mengunduh {model.filename}. " + "; ".join(errors), reason, min(received, model.size)
    )
