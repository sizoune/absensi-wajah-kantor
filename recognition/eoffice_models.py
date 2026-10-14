"""Enrollment-compatible tensors and local inference; never mix model versions."""
from collections.abc import Mapping, Sequence
from os import PathLike
from pathlib import Path
from typing import TypedDict

import cv2
import numpy as np
from numpy.typing import NDArray
import onnxruntime as ort
from ai_edge_litert.interpreter import Interpreter
from skimage import transform as sk_transform

from recognition.model_files import ModelFileError, sha256_of

ARCFACE_DST = np.array([
    [38.2946, 51.6963], [73.5318, 51.5014], [56.0252, 71.7366],
    [41.5493, 92.3655], [70.7299, 92.2041]], dtype=np.float32)
MODEL_HASHES = {
    'auraface_512': 'a7933ea5330113b01c9b60351d8f4c33003f145d8470ac5f0e52ee2effe25c60',
    'mobilefacenet_v1_periocular': 'be4bc7cfc53f7bc336d0f28b1ab92535f618c913a422b683210750f6b5354854',
}
DIMENSIONS = {'auraface_512': 512, 'mobilefacenet_v1_periocular': 192}
Landmarks = NDArray[np.float32] | NDArray[np.float64] | Sequence[Sequence[float]]


class ModelMetadata(TypedDict):
    version: str
    sha256: str
    dimension: int


def model_manifest(paths: Mapping[str, str | PathLike[str]]) -> dict[str, ModelMetadata]:
    """Map embedding versions to verified asset metadata for snapshot comparison."""
    if not paths or not set(paths).issubset(MODEL_HASHES):
        raise ValueError('Expected paths keyed by supported embedding versions')
    manifest: dict[str, ModelMetadata] = {}
    for version, path in paths.items():
        path = Path(path)
        if not path.is_file():
            raise ModelFileError(f'Model asset missing: {path}')
        digest = sha256_of(path)
        if digest != MODEL_HASHES[version]:
            raise ModelFileError(f'Model SHA-256 mismatch: {version}')
        manifest[version] = {'version': version, 'sha256': digest, 'dimension': DIMENSIONS[version]}
    return manifest


def _image(frame):
    frame = np.asarray(frame)
    if frame.dtype != np.uint8 or frame.ndim != 3 or frame.shape[2] != 3 or min(frame.shape[:2]) == 0:
        raise ValueError('Expected nonempty uint8 BGR image')
    return frame


def _landmarks(points, count):
    points = np.asarray(points, dtype=np.float32)
    if points.shape != (count, 2) or not np.isfinite(points).all():
        raise ValueError(f'Expected finite {count}x2 pixel landmarks')
    return points


def _align_auraface(frame, landmarks):
    frame = _image(frame)
    landmarks = _landmarks(landmarks, 5)
    tform = sk_transform.SimilarityTransform()
    if not tform.estimate(landmarks, ARCFACE_DST) or not np.isfinite(tform.params).all():
        raise ValueError('Degenerate face landmarks')
    return cv2.warpAffine(frame, tform.params[0:2, :], (112, 112), borderValue=0.0)


def _auraface_tensor(aligned_bgr):
    rgb = cv2.cvtColor(_image(aligned_bgr), cv2.COLOR_BGR2RGB)
    blob = (rgb.astype(np.float32) - 127.5) / 127.5
    return np.ascontiguousarray(np.expand_dims(np.transpose(blob, (2, 0, 1)), 0), dtype=np.float32)


def _eye_crop(frame, eye_landmarks):
    frame = _image(frame)
    eyes = _landmarks(eye_landmarks, 2)
    with np.errstate(over='ignore', invalid='ignore'):
        dx, dy = eyes[1] - eyes[0]
        distance = np.float32(np.sqrt(dx * dx + dy * dy))
        center_x, center_y = (eyes[0] + eyes[1]) / np.float32(2)
    if not np.isfinite(distance) or distance < 10 or not np.isfinite([center_x, center_y]).all():
        raise ValueError('Inter-eye distance must be at least 10 pixels')
    above = distance * np.float32(0.8)
    below = distance * np.float32(0.5)
    half_width = distance / np.float32(2) + distance * np.float32(0.3)
    coordinates = np.array([center_x - half_width, center_y - above,
                            center_x + half_width, center_y + below], dtype=np.float32)
    # Java Math.round rounds half toward +infinity, unlike Python round.
    left, top, right, bottom = [int(np.floor(float(value) + 0.5)) for value in coordinates]
    left, top = max(0, left), max(0, top)
    right, bottom = min(frame.shape[1], right), min(frame.shape[0], bottom)
    if right <= left or bottom <= top:
        raise ValueError('Eye crop lies outside the image')
    return frame[top:bottom, left:right]


def periocular_tensor(crop_bgr: NDArray[np.uint8]) -> NDArray[np.float32]:
    """MobileFaceNet RGB NHWC float32 with (pixel - 127.5) / 128."""
    # shortcut: OpenCV bilinear parity is unverified, replace if mobile goldens differ.
    resized = cv2.resize(_image(crop_bgr), (112, 112), interpolation=cv2.INTER_LINEAR)
    rgb = cv2.cvtColor(resized, cv2.COLOR_BGR2RGB)
    return np.ascontiguousarray(((rgb.astype(np.float32) - 127.5) / 128.0)[None, ...])


def _unit_embedding(output, dimension):
    raw = np.asarray(output)
    if raw.shape != (1, dimension) or raw.dtype != np.float32 or not np.isfinite(raw).all():
        raise ValueError(f'Expected finite float32 model output [1,{dimension}]')
    with np.errstate(over='ignore', invalid='ignore'):
        norm = float(np.linalg.norm(raw))
    if not np.isfinite(norm) or norm <= 0:
        raise ValueError('Model output has zero or non-finite norm')
    return (raw[0] / norm).astype(np.float32)


class AuraFaceEmbedder:
    version = 'auraface_512'

    def __init__(self, model_path: str | PathLike[str]) -> None:
        self.manifest = model_manifest({self.version: model_path})[self.version]
        options = ort.SessionOptions()
        options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        self.session = ort.InferenceSession(str(model_path), sess_options=options, providers=['CPUExecutionProvider'])
        inputs, outputs = self.session.get_inputs(), self.session.get_outputs()
        if len(inputs) != 1 or len(outputs) != 1:
            raise ValueError('Expected one AuraFace input and output')
        for tensor, dimensions in ((inputs[0], [3, 112, 112]), (outputs[0], [512])):
            shape = tensor.shape
            if (not isinstance(shape, (list, tuple)) or len(shape) != len(dimensions) + 1
                    or list(shape[1:]) != dimensions or tensor.type != 'tensor(float)'
                    or not (shape[0] == 1 or shape[0] is None or isinstance(shape[0], str))):
                raise ValueError('Unexpected AuraFace tensor shape or dtype')
        self.input_name, self.output_name = inputs[0].name, outputs[0].name
        self.embed(np.zeros((112, 112, 3), dtype=np.uint8), ARCFACE_DST)

    def embed(self, frame: NDArray[np.uint8], landmarks: Landmarks) -> NDArray[np.float32]:
        blob = _auraface_tensor(_align_auraface(frame, landmarks))
        output = self.session.run([self.output_name], {self.input_name: blob})[0]
        return _unit_embedding(output, 512)


class PeriocularEmbedder:
    version = 'mobilefacenet_v1_periocular'

    def __init__(self, model_path: str | PathLike[str]) -> None:
        self.manifest = model_manifest({self.version: model_path})[self.version]
        self.interpreter = Interpreter(model_path=str(model_path), num_threads=2)
        self.interpreter.allocate_tensors()
        inputs, outputs = self.interpreter.get_input_details(), self.interpreter.get_output_details()
        if (len(inputs) != 1 or len(outputs) != 1
                or list(inputs[0]['shape']) != [1, 112, 112, 3] or inputs[0]['dtype'] != np.float32
                or list(outputs[0]['shape']) != [1, 192] or outputs[0]['dtype'] != np.float32):
            raise ValueError('Unexpected MobileFaceNet tensor shape or dtype')
        self.input_index, self.output_index = inputs[0]['index'], outputs[0]['index']
        self.embed(np.zeros((112, 112, 3), dtype=np.uint8), [[30, 40], [70, 40]])

    def embed(self, frame: NDArray[np.uint8], eye_landmarks: Landmarks) -> NDArray[np.float32]:
        tensor = periocular_tensor(_eye_crop(frame, eye_landmarks))
        self.interpreter.set_tensor(self.input_index, tensor)
        self.interpreter.invoke()
        return _unit_embedding(self.interpreter.get_tensor(self.output_index), 192)
