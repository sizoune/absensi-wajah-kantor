"""Synthetic contracts; no employee images, templates, or real camera inputs."""
from types import SimpleNamespace

import numpy as np
import pytest

from recognition import eoffice_models as models


@pytest.fixture
def frame():
    image = np.empty((112, 112, 3), dtype=np.uint8)
    image[:] = [0, 128, 255]
    return image


def test_periocular_preprocessing_matches_mobile_constants():
    tensor = models.periocular_tensor(np.zeros((112, 112, 3), dtype=np.uint8))
    assert tensor.shape == (1, 112, 112, 3)
    assert tensor.dtype == np.float32
    assert np.all(tensor == np.float32(-127.5 / 128.0))


def test_periocular_bgr_to_rgb_and_nhwc(frame):
    tensor = models.periocular_tensor(frame)
    np.testing.assert_array_equal(tensor[0, 17, 42], np.float32([0.99609375, 0.00390625, -0.99609375]))
    assert tensor.flags.c_contiguous


def test_periocular_resizes_a_colored_crop():
    tensor = models.periocular_tensor(np.full((3, 7, 3), [255, 0, 127], dtype=np.uint8))
    assert tensor.shape == (1, 112, 112, 3)
    np.testing.assert_array_equal(tensor[0, 0, 0], np.float32([-0.00390625, -0.99609375, 0.99609375]))


def test_auraface_rgb_nchw_normalization(frame):
    tensor = models._auraface_tensor(frame)
    assert tensor.shape == (1, 3, 112, 112)
    assert tensor.dtype == np.float32
    np.testing.assert_array_equal(tensor[0, :, 5, 10], np.float32([1, 1 / 255, -1]))
    assert tensor.flags.c_contiguous


def test_auraface_fixed_identity_landmarks_preserve_pixels(frame):
    aligned = models._align_auraface(frame, [[38.2946, 51.6963], [73.5318, 51.5014], [56.0252, 71.7366], [41.5493, 92.3655], [70.7299, 92.2041]])
    np.testing.assert_array_equal(aligned, frame)


@pytest.mark.parametrize('image', [np.zeros((0, 2, 3), dtype=np.uint8), np.zeros((2, 2)), np.zeros((2, 2, 4), dtype=np.uint8), np.full((2, 2, 3), np.nan), np.zeros((2, 2, 3), dtype=np.float32)])
def test_invalid_images_fail_closed(image):
    with pytest.raises(ValueError):
        models.periocular_tensor(image)


@pytest.mark.parametrize('landmarks', [np.zeros((5, 2)), np.full((5, 2), np.nan), np.zeros((2, 5)), np.full((5, 2), np.inf)])
def test_invalid_face_landmarks_fail_closed(frame, landmarks):
    with pytest.raises(ValueError):
        models._align_auraface(frame, landmarks)


def test_eye_crop_uses_round_half_up_not_bankers_rounding():
    image = np.arange(40 * 40 * 3, dtype=np.uint8).reshape(40, 40, 3)
    # Distance 10, center (15.5, 15.5): bounds 7.5, 7.5, 23.5, 20.5 -> 8,8,24,21.
    crop = models._eye_crop(image, [[10.5, 15.5], [20.5, 15.5]])
    np.testing.assert_array_equal(crop, image[8:21, 8:24])


def test_eye_crop_clamps_to_image_bounds():
    image = np.arange(12 * 12 * 3, dtype=np.uint8).reshape(12, 12, 3)
    crop = models._eye_crop(image, [[0, 1], [10, 1]])
    np.testing.assert_array_equal(crop, image[0:6, 0:12])


def test_eye_crop_uses_euclidean_distance_for_tilt():
    image = np.arange(40 * 40 * 3, dtype=np.uint8).reshape(40, 40, 3)
    # Eyes (10,10),(16,18), distance 10, center(13,14), bounds(5,6,21,19).
    np.testing.assert_array_equal(models._eye_crop(image, [[10, 10], [16, 18]]), image[6:19, 5:21])


@pytest.mark.parametrize('eyes', [[[10, 10], [19.99, 10]], [[0, 0], [0, 0]], [[-100, -100], [-90, -100]], [[200, 200], [210, 200]], [[np.nan, 0], [20, 0]], [[0, 0, 0], [20, 0, 0]]])
def test_invalid_eye_crops_fail_closed(frame, eyes):
    with pytest.raises(ValueError):
        models._eye_crop(frame, eyes)


def test_manifest_missing_or_wrong_asset_fails(tmp_path):
    path = tmp_path / 'mobilefacenet.tflite'
    with pytest.raises(models.ModelFileError):
        models.model_manifest({'mobilefacenet_v1_periocular': path})
    path.write_bytes(b'not the enrollment model')
    with pytest.raises(models.ModelFileError):
        models.PeriocularEmbedder(path)
    with pytest.raises(models.ModelFileError):
        models.AuraFaceEmbedder(path)
    with pytest.raises(ValueError):
        models.model_manifest({'sface': path})
    with pytest.raises(ValueError):
        models.model_manifest({})


@pytest.fixture
def model_paths(tmp_path, monkeypatch):
    path = tmp_path / 'synthetic-test-model'
    path.write_bytes(b'synthetic runtime boundary only')
    # Only runtime stubs use test hashes; real hash rejection is tested above.
    digest = '6d54ef7d215a3505f4ef3324f237fc9ef05f5587959b9f8d796188f743c74aef'
    monkeypatch.setitem(models.MODEL_HASHES, 'auraface_512', digest)
    monkeypatch.setitem(models.MODEL_HASHES, 'mobilefacenet_v1_periocular', digest)
    return path, digest


def test_manifest_reports_verified_version_hash_and_dimension(model_paths):
    path, digest = model_paths
    assert models.model_manifest({'auraface_512': path, 'mobilefacenet_v1_periocular': path}) == {
        'auraface_512': {'version': 'auraface_512', 'sha256': digest, 'dimension': 512},
        'mobilefacenet_v1_periocular': {'version': 'mobilefacenet_v1_periocular', 'sha256': digest, 'dimension': 192},
    }


class FakeOnnx:
    input_shape = [1, 3, 112, 112]
    output_shape = [1, 512]
    output = np.full((1, 512), 2, dtype=np.float32)

    def __init__(self, path, *, sess_options, providers):
        assert providers == ['CPUExecutionProvider']

    def get_inputs(self):
        return [SimpleNamespace(name='input', shape=self.input_shape, type='tensor(float)')]

    def get_outputs(self):
        return [SimpleNamespace(name='output', shape=self.output_shape, type='tensor(float)')]

    def run(self, names, inputs):
        assert names == ['output']
        assert inputs['input'].shape == (1, 3, 112, 112)
        return [self.output]


class FakeLiteRT:
    input_shape = [1, 112, 112, 3]
    output_shape = [1, 192]
    output = np.full((1, 192), 2, dtype=np.float32)

    def __init__(self, *, model_path, num_threads):
        assert num_threads == 2

    def allocate_tensors(self):
        pass

    def get_input_details(self):
        return [{'shape': np.array(self.input_shape), 'dtype': np.float32, 'index': 0}]

    def get_output_details(self):
        return [{'shape': np.array(self.output_shape), 'dtype': np.float32, 'index': 1}]

    def set_tensor(self, index, tensor):
        assert index == 0
        assert tensor.shape == (1, 112, 112, 3)

    def invoke(self):
        pass

    def get_tensor(self, index):
        assert index == 1
        return self.output


@pytest.fixture(params=[('AuraFaceEmbedder', FakeOnnx, 512), ('PeriocularEmbedder', FakeLiteRT, 192)])
def runtime(request, monkeypatch, model_paths):
    name, fake, dimension = request.param
    if name == 'AuraFaceEmbedder':
        monkeypatch.setattr(models.ort, 'InferenceSession', fake)
    else:
        monkeypatch.setattr(models, 'Interpreter', fake)
    return getattr(models, name), fake, dimension, model_paths[0]


def test_model_outputs_are_float32_unit_vectors(runtime, frame):
    constructor, fake, dimension, path = runtime
    embedder = constructor(path)
    landmarks = models.ARCFACE_DST if dimension == 512 else [[30, 40], [70, 40]]
    result = embedder.embed(frame, landmarks)
    assert result.shape == (dimension,)
    assert result.dtype == np.float32
    assert np.linalg.norm(result) == pytest.approx(1, abs=1e-6)
    assert result[0] == pytest.approx(1 / np.sqrt(dimension), abs=1e-7)


@pytest.mark.parametrize('bad', ['zero', 'nan', 'inf', 'shape', 'overflow'])
def test_bad_model_outputs_fail_at_startup(runtime, monkeypatch, bad):
    constructor, fake, dimension, path = runtime
    raw = np.full((1, dimension), {'zero': 0, 'nan': np.nan, 'inf': np.inf, 'shape': 1, 'overflow': np.finfo(np.float32).max}[bad], dtype=np.float32)
    if bad == 'shape':
        raw = raw.reshape(dimension, 1)
    monkeypatch.setattr(fake, 'output', raw)
    with pytest.raises(ValueError):
        constructor(path)


@pytest.mark.parametrize('side', ['input_shape', 'output_shape'])
def test_wrong_model_tensor_shapes_fail_at_startup(runtime, monkeypatch, side):
    constructor, fake, dimension, path = runtime
    monkeypatch.setattr(fake, side, [1, 128])
    with pytest.raises(ValueError):
        constructor(path)


def test_bad_model_outputs_also_fail_after_startup(runtime, monkeypatch, frame):
    constructor, fake, dimension, path = runtime
    embedder = constructor(path)
    monkeypatch.setattr(fake, 'output', np.full((1, dimension), np.nan, dtype=np.float32))
    with pytest.raises(ValueError):
        embedder.embed(frame, models.ARCFACE_DST if dimension == 512 else [[30, 40], [70, 40]])


@pytest.mark.parametrize('batch', [None, 'batch_size'])
def test_auraface_accepts_dynamic_batch_metadata_with_real_batch_one_check(model_paths, monkeypatch, frame, batch):
    monkeypatch.setattr(models.ort, 'InferenceSession', FakeOnnx)
    monkeypatch.setattr(FakeOnnx, 'input_shape', [batch, 3, 112, 112])
    monkeypatch.setattr(FakeOnnx, 'output_shape', [batch, 512])
    result = models.AuraFaceEmbedder(model_paths[0]).embed(frame, models.ARCFACE_DST)
    assert result.shape == (512,)
    assert np.linalg.norm(result) == pytest.approx(1, abs=1e-6)


@pytest.mark.parametrize('shape', [[2, 512], ['batch', 128], ['batch', 512, 1]])
def test_auraface_rejects_wrong_dimensions_with_dynamic_metadata(model_paths, monkeypatch, shape):
    monkeypatch.setattr(models.ort, 'InferenceSession', FakeOnnx)
    monkeypatch.setattr(FakeOnnx, 'output_shape', shape)
    with pytest.raises(ValueError):
        models.AuraFaceEmbedder(model_paths[0])
