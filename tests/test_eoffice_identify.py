"""Synthetic recognition contracts; these do not certify physical liveness."""
import copy
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from recognition.detector import FaceDetection, QualitySettings
from recognition.matcher import AMBIGUOUS, MATCH, MatchResult
from tracking.face_tracker import TrackerSettings

SNAPSHOT = json.loads((Path(__file__).parent / 'fixtures/eoffice_snapshot.json').read_text())
FACE, EYE = 'auraface_512', 'mobilefacenet_v1_periocular'


def setup_scene(kind=FACE):
    from recognition.eoffice_identify import EofficeGallery, EofficeModels, EofficeTracker
    snap = copy.deepcopy(SNAPSHOT)
    snap['employees'] = [e for e in snap['employees'] if e['embedding_version'] == kind]
    for policy in snap['models'].values():
        policy['quality']['min_sharpness'] = 0
        policy['quality']['min_size'] = 30
    class Model:
        def __init__(self, version):
            self.manifest = {'version': version, 'sha256': snap['models'][version]['weights_sha256'],
                             'dimension': snap['models'][version]['dimension']}
            self.vector = np.eye(1, self.manifest['dimension'], dtype=np.float32)[0]
        def embed(self, frame, landmarks):
            return self.vector
    live = SimpleNamespace(score=lambda frame, det: .99)
    models = EofficeModels({v: Model(v) for v in (FACE, EYE)}, live)
    gallery = EofficeGallery(snap, models)
    tracker = EofficeTracker(TrackerSettings(confirm_votes=3))
    det = FaceDetection(np.array([30, 30, 160, 170, 70, 80, 150, 80, 110, 120,
                                  80, 155, 140, 155, .99], dtype=np.float32))
    detector = SimpleNamespace(detect=lambda frame: [det])
    frame = np.full((240, 240, 3), 128, np.uint8)
    return snap, models, gallery, tracker, detector, frame


def frames(scene, count=3, start=0):
    from recognition.eoffice_identify import recognize_eoffice_frame
    _, models, gallery, tracker, detector, frame = scene
    events = []
    for n in range(start, start + count):
        tracks, new = recognize_eoffice_frame(frame, detector, models, gallery,
                                              QualitySettings(), tracker, n * .1)
        events.extend(new)
    return tracks, events


def test_model_identity_conflict_is_rejected():
    from recognition.eoffice_identify import merge_model_matches
    assert merge_model_matches(MatchResult(MATCH, 101, .9, .1),
                               MatchResult(MATCH, 202, .95, .2)).status == AMBIGUOUS


@pytest.mark.parametrize('kind', [FACE, EYE])
def test_stable_identity_has_same_model_enrollment_and_frame_time(kind):
    scene = setup_scene(kind)
    assert frames(scene, 2)[1] == []
    _, events = frames(scene, 1, 2)
    event, = events
    expected = scene[0]['employees'][0]
    assert event.pegawai_id == expected['pegawai_id']
    assert event.enrollment_id == expected['enrollment_id']
    assert event.embedding_version == kind and event.samples == 3
    assert event.capture_monotonic == .2
    assert frames(scene, 4, 3)[1] == []


@pytest.mark.parametrize('score', [0., .5, float('nan')])
def test_spoof_uncertain_invalid_liveness_never_emit(score):
    scene = setup_scene()
    scene[1].liveness.score = lambda *args: score
    assert frames(scene, 40)[1] == []


def test_both_manifests_and_complete_policy_required():
    from recognition.eoffice_identify import EofficeGallery
    scene = setup_scene()
    scene[1].embedders[EYE].manifest['sha256'] = 'd' * 64
    with pytest.raises(ValueError):
        EofficeGallery(scene[0], scene[1])
    scene[0]['models'][EYE].pop('liveness')
    with pytest.raises(ValueError):
        EofficeGallery(scene[0], scene[1])


def test_eye_quality_does_not_require_nose_or_mouth():
    scene = setup_scene(EYE)
    det = scene[4].detect(None)[0]
    det.raw[8:14] = np.nan
    assert len(frames(scene)[1]) == 1


def test_crossing_resets_identity_evidence_before_confirmation():
    from recognition.eoffice_identify import EofficeGallery
    scene = list(setup_scene())
    first = scene[0]['employees'][0]
    other = copy.deepcopy(first)
    other.update(pegawai_id=909, enrollment_id=90, enrollment_revision='f' * 64)
    other['samples'] = [[0.] * 4 + [1.] + [0.] * 507]
    scene[0]['employees'].append(other)
    scene[2] = EofficeGallery(scene[0], scene[1])
    assert frames(scene, 2)[1] == []
    scene[1].embedders[FACE].vector = np.asarray(other['samples'][0], dtype=np.float32)
    assert frames(scene, 2, 2)[1] == []
    event, = frames(scene, 1, 4)[1]
    assert (event.pegawai_id, event.enrollment_id, event.enrollment_revision) == (909, 90, 'f' * 64)
    assert event.samples == 3 and event.score == 1


def test_snapshot_refresh_discards_votes():
    from recognition.eoffice_identify import EofficeGallery
    from uuid import uuid4
    scene = list(setup_scene())
    frames(scene, 2)
    scene[0]['snapshot_id'] = scene[0]['clock_anchor_id'] = str(uuid4())
    scene[2] = EofficeGallery(scene[0], scene[1])
    assert frames(scene, 1, 2)[1] == []
    assert len(frames(scene, 2, 3)[1]) == 1


def test_close_second_candidate_never_emits():
    from recognition.eoffice_identify import EofficeGallery
    scene = list(setup_scene())
    second = copy.deepcopy(scene[0]['employees'][0])
    second.update(pegawai_id=909, enrollment_id=90)
    scene[0]['employees'].append(second)
    scene[2] = EofficeGallery(scene[0], scene[1])
    assert frames(scene, 10)[1] == []


def test_two_people_crossing_boxes_keep_winning_identity_provenance():
    from recognition.eoffice_identify import EofficeGallery
    scene = list(setup_scene())
    original = scene[0]['employees'][0]
    other = copy.deepcopy(original)
    other.update(pegawai_id=909, enrollment_id=90, enrollment_revision='f' * 64,
                 samples=[[0.] * 4 + [1.] + [0.] * 507])
    scene[0]['employees'].append(other)
    scene[2] = EofficeGallery(scene[0], scene[1])
    left = scene[4].detect(None)[0]
    right = FaceDetection(left.raw.copy())
    right.raw[0] += 220
    right.raw[4:14:2] += 220
    scene[4].detect = lambda frame: [left, right]
    scene[5] = np.full((240, 480, 3), 128, np.uint8)
    crossed = [False]
    def embedding(frame, landmarks):
        is_left = landmarks[0][0] < 220
        employee = original if is_left != crossed[0] else other
        return np.asarray(employee['samples'][0], np.float32)
    scene[1].embedders[FACE].embed = embedding
    assert frames(scene, 2)[1] == []
    crossed[0] = True
    assert frames(scene, 2, 2)[1] == []
    events = frames(scene, 1, 4)[1]
    assert {(e.pegawai_id, e.enrollment_id, e.enrollment_revision, e.samples) for e in events} == {
        (101, 12, 'a' * 64, 3), (909, 90, 'f' * 64, 3)}


def test_face_eye_conflict_in_actual_pipeline_never_emits():
    from recognition.eoffice_identify import EofficeGallery
    scene = list(setup_scene())
    scene[0]['employees'] = copy.deepcopy(SNAPSHOT['employees'])
    scene[2] = EofficeGallery(scene[0], scene[1])
    assert frames(scene, 10)[1] == []


def test_eye_policy_does_not_borrow_face_liveness_threshold():
    from recognition.eoffice_identify import EofficeGallery
    scene = list(setup_scene(EYE))
    scene[0]['models'][FACE]['liveness']['real_threshold'] = .4
    scene[0]['models'][EYE]['liveness']['real_threshold'] = .99
    scene[1].liveness.score = lambda *a: .9
    scene[2] = EofficeGallery(scene[0], scene[1])
    assert frames(scene, 40)[1] == []


def test_missing_gallery_discards_votes():
    from recognition.eoffice_identify import recognize_eoffice_frame
    scene = setup_scene()
    frames(scene, 2)
    _, models, gallery, tracker, detector, frame = scene
    assert recognize_eoffice_frame(frame, detector, models, None, QualitySettings(), tracker, .3) == ([], [])
    assert frames(scene, 1, 4)[1] == []


def test_reset_does_not_reassociate_other_person_to_replaced_track():
    from recognition.eoffice_identify import EofficeGallery
    scene = list(setup_scene())
    first = scene[0]['employees'][0]
    second = copy.deepcopy(first)
    second.update(pegawai_id=909, enrollment_id=90, samples=[[0.] * 4 + [1.] + [0.] * 507])
    third = copy.deepcopy(first)
    third.update(pegawai_id=808, enrollment_id=80, samples=[[0.] * 5 + [1.] + [0.] * 506])
    scene[0]['employees'] = [first, second, third]
    scene[2] = EofficeGallery(scene[0], scene[1])
    left = scene[4].detect(None)[0]
    right = FaceDetection(left.raw.copy())
    right.raw[0] += 20
    right.raw[4:14:2] += 20
    scene[4].detect = lambda frame: [left, right]
    scene[5] = np.full((240, 400, 3), 128, np.uint8)
    chosen = [first, second]
    scene[1].embedders[FACE].embed = lambda frame, lm: np.asarray(
        chosen[0 if lm[0][0] == 70 else 1]['samples'][0], np.float32)
    tracks, _ = frames(scene, 2)
    second_track_id = tracks[1].id
    chosen[0] = third
    right.raw[0] += 50
    right.raw[4:14:2] += 50
    tracks, events = frames(scene, 1, 2)
    assert tracks[1].id == second_track_id
    assert tracks[0].confirmed_id is None
    assert all(e.pegawai_id != 808 for e in events)


def test_nonfinite_detection_confidence_fails_quality():
    scene = setup_scene()
    scene[4].detect(None)[0].raw[14] = np.nan
    assert frames(scene, 4)[1] == []


def test_minimum_live_samples_are_specific_to_selected_model():
    from recognition.eoffice_identify import EofficeGallery
    face = list(setup_scene(FACE))
    face[0]['models'][EYE]['liveness']['min_samples'] = 5
    face[2] = EofficeGallery(face[0], face[1])
    assert len(frames(face, 3)[1]) == 1
    eye = list(setup_scene(EYE))
    eye[0]['models'][EYE]['liveness']['min_samples'] = 5
    eye[2] = EofficeGallery(eye[0], eye[1])
    assert frames(eye, 3)[1] == []
    event, = frames(eye, 2, 3)[1]
    assert event.samples == 5 and event.capture_monotonic == .4


@pytest.mark.parametrize('kind', [FACE, EYE])
@pytest.mark.parametrize('final_time,accepted', [(.6, True), (.625, False), (.75, False)])
def test_first_sufficient_live_sample_must_precede_selected_deadline(kind, final_time, accepted):
    from recognition.eoffice_identify import EofficeGallery, recognize_eoffice_frame
    scene = list(setup_scene(kind))
    scene[0]['models'][kind]['liveness'].update(min_samples=6, decide_seconds=.375)
    scene[2] = EofficeGallery(scene[0], scene[1])
    _, models, gallery, tracker, detector, frame = scene
    for now in (0., .125, .25, .375, .5):
        assert recognize_eoffice_frame(frame, detector, models, gallery, QualitySettings(), tracker, now)[1] == []
    # Confirmation at .25 gives an exact .625 deadline; sixth sample is first sufficient LIVE.
    tracks, events = recognize_eoffice_frame(frame, detector, models, gallery, QualitySettings(), tracker, final_time)
    assert bool(events) is accepted
    gate = tracker.gates[kind]
    assert not gate.pending
    if accepted:
        assert events[0].samples == 6 and events[0].capture_monotonic == final_time
    else:
        assert tracks[0].id in gate.rejected_tracks
        assert recognize_eoffice_frame(frame, detector, models, gallery, QualitySettings(), tracker, final_time + .125)[1] == []


@pytest.mark.parametrize('kind', [FACE, EYE])
def test_liveness_deadlines_are_independent_between_models(kind):
    from recognition.eoffice_identify import EofficeGallery, recognize_eoffice_frame
    other = EYE if kind == FACE else FACE
    outcomes = []
    for selected_seconds, other_seconds in ((.25, .5), (.5, .25)):
        scene = list(setup_scene(kind))
        scene[0]['models'][kind]['liveness'].update(min_samples=6, decide_seconds=selected_seconds)
        scene[0]['models'][other]['liveness']['decide_seconds'] = other_seconds
        scene[2] = EofficeGallery(scene[0], scene[1])
        _, models, gallery, tracker, detector, frame = scene
        events = []
        for now in (0., .125, .25, .375, .4375, .625):
            events.extend(recognize_eoffice_frame(frame, detector, models, gallery, QualitySettings(), tracker, now)[1])
        outcomes.append(bool(events))
    assert outcomes == [False, True]
