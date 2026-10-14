"""Operator-supplied assets only; skipped parity is NOT a passing compatibility gate."""
import importlib.util
import os
import shutil
from tempfile import TemporaryDirectory
from pathlib import Path

import numpy as np
import pytest

from recognition.eoffice_models import (
    ARCFACE_DST, AuraFaceEmbedder, PeriocularEmbedder,
    _align_auraface, _auraface_tensor, _eye_crop, model_manifest, periocular_tensor,
)


ROOT = Path(__file__).resolve().parents[1]


def _operator_path(variable, default=None):
    value = os.environ.get(variable)
    if value:
        path = Path(value)
        assert path.is_file(), f'{variable} was supplied but does not exist: {path}'
        return path
    if default is not None and default.is_file():
        return default
    pytest.skip(f'NOT RUN: provide {variable}; skipped parity is not acceptance')


def _server_recognizer():
    path = _operator_path('EOFFICE_SERVER_RECOGNIZER', ROOT.parent / 'api/face-service/app/recognizer.py')
    spec = importlib.util.spec_from_file_location('enrollment_server_recognizer', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _server_embedding(path, frame, landmarks):
    selected = model_manifest({'auraface_512': path})
    with TemporaryDirectory(prefix='eoffice-auraface-parity-') as directory:
        staged = Path(directory) / 'glintr100.onnx'
        shutil.copyfile(path, staged)
        assert model_manifest({'auraface_512': staged}) == selected
        return _server_recognizer().Recognizer(directory).embed(frame, landmarks)['embedding']


def _synthetic_frame():
    y, x = np.indices((140, 160))
    return np.stack((x % 256, y % 256, (x + y) % 256), axis=-1).astype(np.uint8)


def _compare(actual, expected, label, record_property):
    expected = np.asarray(expected, dtype=np.float32)
    assert actual.shape == expected.shape
    assert np.isfinite(expected).all()
    norm = float(np.linalg.norm(expected))
    assert np.isfinite(norm) and norm > 0
    expected = expected / norm
    cosine = float(np.dot(actual, expected) / np.linalg.norm(actual))
    difference = float(np.max(np.abs(actual - expected)))
    record_property(f'{label}_cosine', cosine)
    record_property(f'{label}_max_absolute_difference', difference)
    print(f'{label}: cosine={cosine:.9f}, max_absolute_difference={difference:.9g}')
    assert cosine >= 0.999


@pytest.mark.parametrize('offset', [0, 7])
def test_auraface_tensor_matches_server_on_synthetic_image(offset):
    server = _server_recognizer()
    frame, landmarks = _synthetic_frame(), ARCFACE_DST + np.float32(offset)
    expected = server._preprocess(server.norm_crop(frame, landmarks))
    np.testing.assert_array_equal(_auraface_tensor(_align_auraface(frame, landmarks)), expected)


def test_real_mobilefacenet_synthetic_inference(record_property):
    path = _operator_path('EOFFICE_MOBILEFACENET_PATH', ROOT / 'model_files/mobilefacenet.tflite')
    embedder = PeriocularEmbedder(path)
    result = embedder.embed(_synthetic_frame(), [[50.5, 60.5], [90.5, 60.5]])
    assert result.shape == (192,)
    assert result.dtype == np.float32
    assert np.isfinite(result).all()
    assert np.linalg.norm(result) == pytest.approx(1, abs=1e-6)
    record_property('synthetic_inference_weights_sha256', embedder.manifest['sha256'])


def test_real_auraface_matches_server_on_synthetic_image(record_property):
    path = _operator_path('EOFFICE_AURAFACE_PATH', ROOT / 'model_files/glintr100.onnx')
    frame, landmarks = _synthetic_frame(), ARCFACE_DST + np.float32(7)
    actual = AuraFaceEmbedder(path).embed(frame, landmarks)
    expected = _server_embedding(path, frame, landmarks)
    _compare(actual, expected, 'auraface_synthetic', record_property)


def test_consented_auraface_matches_server(record_property):
    sample = _operator_path('EOFFICE_CONSENTED_AURAFACE_NPZ')
    path = _operator_path('EOFFICE_AURAFACE_PATH', ROOT / 'model_files/glintr100.onnx')
    with np.load(sample, allow_pickle=False) as data:
        frame, landmarks = data['frame_bgr'], data['landmarks']
        actual = AuraFaceEmbedder(path).embed(frame, landmarks)
        expected = _server_embedding(path, frame, landmarks)
    _compare(actual, expected, 'auraface_consented', record_property)


@pytest.mark.parametrize('platform', ['ANDROID', 'IOS'])
def test_periocular_matches_consented_mobile_golden(platform, record_property):
    sample = _operator_path(f'EOFFICE_CONSENTED_{platform}_GOLDEN_NPZ')
    path = _operator_path('EOFFICE_MOBILEFACENET_PATH', ROOT / 'model_files/mobilefacenet.tflite')
    embedder = PeriocularEmbedder(path)
    with np.load(sample, allow_pickle=False) as data:
        frame, eyes = data['frame_bgr'], data['eye_landmarks']
        expected_tensor = data['tensor']
        assert expected_tensor.shape == (1, 112, 112, 3)
        assert expected_tensor.dtype == np.float32
        assert np.isfinite(expected_tensor).all()
        expected = data['embedding']
        # Isolate runtime parity from crop/resampling parity with the exact mobile tensor.
        embedder.interpreter.set_tensor(embedder.input_index, expected_tensor)
        embedder.interpreter.invoke()
        raw = embedder.interpreter.get_tensor(embedder.output_index)
        assert raw.shape == (1, 192) and np.isfinite(raw).all()
        norm = float(np.linalg.norm(raw))
        assert np.isfinite(norm) and norm > 0
        fixed_tensor_embedding = raw[0] / norm
        _compare(fixed_tensor_embedding, expected, f'{platform}_fixed_tensor', record_property)
        actual = embedder.embed(frame, eyes)
        _compare(actual, expected, f'{platform}_crop_pipeline', record_property)
        tensor = periocular_tensor(_eye_crop(frame, eyes))
        max_difference = float(np.max(np.abs(tensor - expected_tensor)))
        record_property(f'{platform}_tensor_max_absolute_difference', max_difference)
        print(f'{platform} tensor max_absolute_difference={max_difference:.9g}')
        np.testing.assert_array_equal(tensor, expected_tensor)


@pytest.mark.parametrize('canonical_neighbor', [False, True])
def test_auraface_reference_uses_selected_noncanonical_asset(tmp_path, monkeypatch, record_property, canonical_neighbor):
    from types import SimpleNamespace
    from recognition import eoffice_models as models

    selected = tmp_path / 'selected.onnx'
    selected.write_bytes(b'synthetic runtime boundary only')
    monkeypatch.setitem(models.MODEL_HASHES, 'auraface_512', '6d54ef7d215a3505f4ef3324f237fc9ef05f5587959b9f8d796188f743c74aef')
    if canonical_neighbor:
        (tmp_path / 'glintr100.onnx').write_bytes(b'different reference weights')
    monkeypatch.setenv('EOFFICE_AURAFACE_PATH', str(selected))
    expected = np.zeros(512, dtype=np.float32)
    expected[0] = 1

    class SelectedEmbedder:
        def __init__(self, path):
            models.model_manifest({'auraface_512': path})

        def embed(self, frame, landmarks):
            return expected.copy()

    class ServerRecognizer:
        def __init__(self, model_dir):
            # Reproduce the authoritative server's filename resolution.
            self.bytes = (Path(model_dir) / 'glintr100.onnx').read_bytes()

        def embed(self, frame, landmarks):
            result = np.zeros(512, dtype=np.float32)
            result[0 if self.bytes == b'synthetic runtime boundary only' else 1] = 1
            return {'embedding': result}

    monkeypatch.setitem(globals(), 'AuraFaceEmbedder', SelectedEmbedder)
    monkeypatch.setitem(globals(), '_server_recognizer', lambda: SimpleNamespace(Recognizer=ServerRecognizer))
    print('asset-selection regression with runtime doubles; not real model parity')
    test_real_auraface_matches_server_on_synthetic_image(record_property)
