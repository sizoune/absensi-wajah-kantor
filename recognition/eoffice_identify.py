"""Snapshot-bound recognition. Physical model/liveness acceptance is separate."""
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass, replace
import math
from typing import Protocol

import cv2
import numpy as np
from numpy.typing import NDArray

from integration.protocol import JSONValue, MODELS, validate_snapshot
from recognition.detector import FaceDetection, QualitySettings, assess
from recognition.eoffice_models import ModelMetadata, _align_auraface, _eye_crop
from recognition.liveness import LivenessGate, LivenessSettings
from recognition.matcher import AMBIGUOUS, NO_TEMPLATES, MatchResult, Matcher
from tracking.face_tracker import FaceTracker, Track, TrackerSettings

Image = NDArray[np.uint8]


class Embedder(Protocol):
    manifest: ModelMetadata
    def embed(self, frame: Image, landmarks: NDArray) -> NDArray[np.float32]: ...


class Detector(Protocol):
    def detect(self, frame: Image) -> list[FaceDetection]: ...


class PassiveModel(Protocol):
    def score(self, frame: Image, detection: FaceDetection) -> float: ...


@dataclass
class EofficeModels:
    embedders: Mapping[str, Embedder]
    liveness: PassiveModel


@dataclass(frozen=True)
class EofficeEvent:
    track_id: int
    pegawai_id: int
    enrollment_id: int
    enrollment_revision: str
    verification_type: str
    embedding_version: str
    snapshot_id: str
    first_name: str
    score: float
    second_score: float
    samples: int
    capture_monotonic: float
    in_cooldown: bool = False


class EofficeGallery:
    def __init__(self, snapshot: dict[str, JSONValue], models: EofficeModels) -> None:
        snapshot = deepcopy(validate_snapshot(snapshot))
        if set(models.embedders) != set(MODELS) or models.liveness is None:
            raise ValueError('Both e-office models and passive liveness are required')
        for version, (_, dimension) in MODELS.items():
            local = models.embedders[version].manifest
            policy = snapshot['models'][version]
            if local != {'version': version, 'dimension': dimension, 'sha256': policy['weights_sha256']}:
                raise ValueError('Local model does not match authenticated policy')
        self.snapshot_id: str = snapshot['snapshot_id']
        self.policies: dict = snapshot['models']
        self.employees: dict = {(e['embedding_version'], e['pegawai_id']): e for e in snapshot['employees']}
        self.matchers: dict[str, Matcher] = {}
        for version, policy in self.policies.items():
            templates = [(e['pegawai_id'], sample) for e in snapshot['employees']
                         if e['embedding_version'] == version for sample in e['samples']]
            self.matchers[version] = Matcher(templates, policy['threshold'], policy['margin'])


class EofficeTracker(FaceTracker):
    def __init__(self, settings: TrackerSettings | None = None) -> None:
        super().__init__(replace(settings or TrackerSettings()))
        self.snapshot_id: str | None = None
        self.provenance: dict[int, tuple] = {}
        self.gates: dict[str, LivenessGate] = {}

    def reset_gallery(self, gallery: EofficeGallery) -> None:
        self.tracks.clear()
        self.provenance.clear()
        self.snapshot_id = gallery.snapshot_id
        self.gates = {v: LivenessGate(LivenessSettings(mode='passive', **p['liveness']))
                      for v, p in gallery.policies.items()}


def merge_model_matches(face: MatchResult, eye: MatchResult) -> MatchResult:
    if face.status == AMBIGUOUS or eye.status == AMBIGUOUS:
        return MatchResult(AMBIGUOUS)
    if face.is_match and eye.is_match and face.employee_id != eye.employee_id:
        return MatchResult(AMBIGUOUS)
    return face if face.is_match else eye


def _quality_ok(frame: Image, det: FaceDetection, version: str, policy: dict,
                quality: QualitySettings) -> bool:
    if not math.isfinite(det.score) or not math.isfinite(det.size):
        return False
    settings = replace(quality, **policy['quality'])
    if version == 'auraface_512':
        if not np.isfinite(det.landmarks).all():
            return False
        return assess(frame, det, settings, crop=_align_auraface(frame, det.landmarks)).ok
    eyes = det.landmarks[:2]
    if not np.isfinite(eyes).all() or (eyes < 0).any():
        return False
    if (eyes[:, 0] >= frame.shape[1]).any() or (eyes[:, 1] >= frame.shape[0]).any():
        return False
    crop = _eye_crop(frame, eyes)
    dx, dy = eyes[1] - eyes[0]
    gray = cv2.cvtColor(cv2.resize(crop, (112, 112)), cv2.COLOR_BGR2GRAY)
    return (det.score >= settings.min_score and min(crop.shape[:2]) >= settings.min_size
            and abs(math.degrees(math.atan2(float(dy), float(dx)))) <= settings.max_roll_deg
            and settings.min_brightness <= gray.mean() <= settings.max_brightness
            and cv2.Laplacian(gray, cv2.CV_64F).var() >= settings.min_sharpness)


def recognize_eoffice_frame(frame: Image, detector: Detector, models: EofficeModels,
                            gallery: EofficeGallery | None, quality: QualitySettings,
                            tracker: EofficeTracker, now: float) -> tuple[list[Track], list[EofficeEvent]]:
    if gallery is None or models.liveness is None:
        tracker.tracks.clear()
        tracker.provenance.clear()
        return [], []
    if tracker.snapshot_id != gallery.snapshot_id:
        tracker.reset_gallery(gallery)
    detections = detector.detect(frame)
    observations, selected = [], []
    for det in detections:
        matches = {}
        for version, matcher in gallery.matchers.items():
            result = MatchResult(NO_TEMPLATES)
            try:
                if matcher.template_count and _quality_ok(frame, det, version, gallery.policies[version], quality):
                    landmarks = det.landmarks if version == 'auraface_512' else det.landmarks[:2]
                    embedding = np.asarray(models.embedders[version].embed(frame, landmarks))
                    if (embedding.shape != (MODELS[version][1],) or not np.isfinite(embedding).all()
                            or not 0 < np.linalg.norm(embedding) < float("inf")):
                        raise ValueError('Invalid model vector')
                    result = matcher.match(embedding)
            except (ValueError, cv2.error):
                pass
            matches[version] = result
        merged = merge_model_matches(matches['auraface_512'], matches['mobilefacenet_v1_periocular'])
        version = next((v for v, match in matches.items() if match is merged and merged.is_match), None)
        candidate, extras = None, {}
        if version:
            try:
                score = float(models.liveness.score(frame, det))
            except (ValueError, cv2.error):
                score = float('nan')
            if math.isfinite(score) and 0 <= score <= 1:
                employee = gallery.employees[version, merged.employee_id]
                candidate = (version, employee, merged)
                extras['live'] = score
        observations.append((det.box, merged if candidate else None, '', extras))
        selected.append(candidate)

    # Reset on the first incompatible observation, before FaceTracker can retain majority votes.
    tracker.tracks = [t for t in tracker.tracks if now - t.last_seen <= tracker.s.max_missing_seconds]
    associations = tracker._associate([d.box for d in detections])
    for index, track in associations.items():
        candidate = selected[index]
        key = None if candidate is None else (candidate[0], candidate[1]['pegawai_id'],
              candidate[1]['enrollment_id'], candidate[1]['enrollment_revision'], gallery.snapshot_id)
        if tracker.provenance.get(track.id) != key:
            # Preserve association geometry/order so another person cannot inherit this observation.
            tracker.tracks[tracker.tracks.index(track)] = tracker._new_track(track.box, now)
            for gate in tracker.gates.values():
                gate.pending.pop(track.id, None)
    tracks, confirmations = tracker.update(observations, now)
    current = {}
    for track, candidate in zip(tracks, selected):
        if candidate:
            version, employee, match = candidate
            tracker.provenance[track.id] = (version, employee['pegawai_id'], employee['enrollment_id'],
                                            employee['enrollment_revision'], gallery.snapshot_id)
            current[track.id] = candidate
    tracker.provenance = {t.id: tracker.provenance[t.id] for t in tracker.tracks if t.id in tracker.provenance}
    accepted = []
    for version, gate in tracker.gates.items():
        present = {t.id: t for t in tracks if t.id in current and current[t.id][0] == version}
        gate.rejected_tracks.intersection_update(present)
        result = gate.update(present, [e for e in confirmations if e.track_id in present], now)
        for event in result.accepted:
            track = present[event.track_id]
            _, employee, match = current[track.id]
            # Evidence is from this same accepted identity, enrollment and processed frame.
            accepted.append(EofficeEvent(track.id, employee['pegawai_id'], employee['enrollment_id'],
                employee['enrollment_revision'], employee['verification_type'], version, gallery.snapshot_id,
                employee['display_name'].split()[0], min(1., max(-1., match.score)),
                min(1., max(-1., match.second_score)), len(track.live_scores), now, event.in_cooldown))
    return tracks, accepted
