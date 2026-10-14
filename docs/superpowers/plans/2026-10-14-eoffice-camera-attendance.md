# E-office Camera Attendance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Pegawai Diskominfo, termasuk pegawai bercadar, dapat absen masuk/pulang sambil melewati satu kamera, memakai enrollment e-office yang sudah ada dan rekap yang sama dengan HP.

**Architecture:** Komputer kios mengenali identitas menggunakan AuraFace/periocular dan menyimpan kejadian dalam antrean lokal. API khusus perangkat menyinkronkan templat, menentukan sesi berdasarkan waktu kejadian dan jadwal otoritatif, lalu mencatat secara atomik bersama jalur mobile. Kejadian tertunda tidak menimpa catatan sah yang lebih dahulu disahkan.

**Tech Stack:** Python 3.11, Flask/SQLAlchemy/Alembic, OpenCV/NumPy/cryptography yang sudah terpasang; ONNX Runtime untuk AuraFace, LiteRT untuk model periocular yang sudah ada; Express/CommonJS, Knex/MySQL, node:test yang sudah tersedia. HTTP Python memakai standard library `urllib`, tanpa dependency HTTP tambahan.

**Spec:** `../specs/2026-10-14-eoffice-camera-attendance-design.md` (commit `8de8894`). Pengguna menyetujui spesifikasi pada 14 Oktober 2026; rencana ini belum disetujui untuk dieksekusi.

## Global Constraints

- “Tahap pertama hanya masuk pagi dan pulang, tanpa sesi istirahat atau kegiatan/event.”
- “Tidak ada enrollment ulang di kios.”
- “Sinkronisasi hanya mencakup pegawai Diskominfo yang aktif dan enrollment yang diizinkan e-office.”
- “Pengiriman tertunda tetap dinilai berdasarkan waktu kejadian.”
- “Catatan sah yang lebih dahulu diterima e-office dipertahankan.”
- “Hasil pengenalan ambigu atau pemeriksaan anti-foto gagal tidak boleh menghasilkan absensi.”
- “Vektor beda model tidak pernah dibandingkan.”
- “Transfer melalui TLS.”
- “Tidak ada penghapusan otomatis kejadian pending untuk mengatasi ruang disk.”
- “Implementasi dimulai setelah rencana ditinjau dan metode eksekusinya dipilih pengguna.”

## Review Focus

1. Skeleton `as_id=2` bukan bukti sesi sudah diisi; cuti/izin/koreksi sah tidak boleh dianggap skeleton. Tambahkan uji di Task 4.
2. Jumat dapat menulis pasangan detail `[1,3]` atau `[2,4]`; penulisan pasangan tidak boleh merusak sesi pemenang atau membuat kamera mencatat istirahat. Tambahkan uji di Task 4.
3. Perubahan konfigurasi lalu kembali ke nilai semula tidak boleh membuat jadwal offline tampak tidak berubah. Tambahkan uji revisi A→B→A di Task 3.
4. Pergantian bulan/tahun saat antrean dikirim harus menulis partisi tanggal kerja lama, bukan partisi hari penerimaan. Tambahkan uji di Task 5.
5. Dua pekerja retry atau respons lama untuk pegawai sebelumnya tidak boleh menghasilkan kiriman baru atau konfirmasi salah di layar. Tambahkan uji di Task 8–9.

## Working directories, boundaries, and verification

`CAM` di dokumen ini berarti `/Users/muhammadwildaniskandar/Projects/E-office/absensi-wajah-kantor`; `API` berarti `/Users/muhammadwildaniskandar/Projects/E-office/e-office-api-main`. Jalankan perintah pada direktori yang disebutkan, bukan folder induk workspace yang bukan Git repository. Semua path pada daftar Files relatif terhadap CAM/API masing-masing.

Ini satu fitur lintas dua aplikasi, bukan dua produk independen. Urutan task mengikat kontrak server dan kios, lalu menguji keseluruhan. Jangan mengubah salinan `.worktrees/`, `e-office-play-integrity-memory`, atau repositori admin hanya karena namanya mirip. Perubahan jadwal dari admin dilacak pada database yang sama melalui revisi sumber di Task 3, tanpa menambah UI admin.

Perintah Python di bawah memakai interpreter virtualenv CAM. Perintah Node memakai runtime yang memenuhi `package.json` API. Jangan menjalankan migrasi/test integrasi pada database produksi. Saat eksekusi, ikuti AGENTS.md, gunakan CodeGraph sebelum membaca/mengubah kode terindeks, dan pertahankan file pengguna yang tidak terkait.

## File structure and ownership

| Repo / file | Responsibility |
| --- | --- |
| API `docs/kiosk/schema-audit.md`, `docs/kiosk/protocol.md` | Bukti skema, daftar seluruh penulis sesi, kontrak dan payload lintas bahasa. |
| API `migrations/20261014_kiosk_attendance.sql` | Perangkat, snapshot, revisi kebijakan, kejadian/idempotensi, lock hari pegawai; tidak menghapus data lama. |
| API `query/KioskQuery.js` | Query perangkat, enrollment/snapshot, kejadian, dan histori revisi. |
| API `api/services/kioskProtocol.js` | Validasi bentuk dan versi payload serta hash body. |
| API `api/services/kioskSchedule.js` | Resolusi tanggal kerja/sesi berbasis snapshot jadwal server. |
| API `api/services/attendanceWrite.js` | Transaksi dan perlindungan catatan sah bersama kamera/HP. |
| API `api/services/kioskAttendance.js`, `api/APIkiosk.js` | Orkestrasi penerimaan dan respons endpoint. |
| API `app.js`, `query/AbsensiQuery.js`, `api/APIabsensi.js`, `api/services/absenSkeleton.js`, `workers/absenSkeletonWorker.js` | Wiring dan pemakaian proteksi yang sama, tanpa merombak fitur lain. |
| API `scripts/kiosk-device.js` | Penerbitan/pencabutan kredensial satu perangkat oleh operator. |
| CAM `integration/protocol.py`, `integration/client.py` | Validasi snapshot/payload dan transport HTTPS dengan timeout. |
| CAM `integration/store.py`, `integration/clock.py`, `integration/sync.py` | Cache terenkripsi, antrean persisten, waktu terpercaya, sinkronisasi/retry. |
| CAM `recognition/eoffice_models.py`, `recognition/eoffice_identify.py` | Dua model/preprocessing dan penggabungan keputusan tanpa mencampur ruang vektor. |
| CAM `kiosk/worker.py`, `kiosk/cli.py`, `kiosk/state.py`, `config.py`, `.env.example` | Mode integrasi eksplisit; pakai kamera, tracking dan status yang sudah ada. |
| CAM `static/js/kiosk.js`, `templates/kiosk.html` | Pesan hasil resmi/pending dan aksesibilitas layar yang sudah ada. |
| CAM `requirements-eoffice.txt`, `docs/EOFFICE.md`, `docs/MODEL_DECISION.md` | Runtime tambahan, pemasangan, operasi, kalibrasi, pemulihan. |

File test baru disebut pada task pemiliknya. Tidak menambah framework test, dashboard, abstraksi repository generik, atau aplikasi/layanan baru.

## Contract fixed for all tasks

API baru: `GET /api/kiosk/snapshot`, `POST /api/kiosk/events`. Autentikasi `Authorization: Bearer <device-secret>`; ID/cakupan diambil dari record perangkat server, bukan field client. Body event disimpan sebagai bytes UTF-8 identik untuk setiap retry; hash SHA-256 body menjadi pembanding konflik UUID. TLS wajib, redirect transport ditolak agar kredensial tidak bocor.

```json
{
  "protocol_version": 1,
  "event_id": "85f42b66-6a77-4c56-9efc-4231ff6eab42",
  "pegawai_id": 101,
  "occurred_at": "2026-10-14T07:25:00+08:00",
  "snapshot_id": "90d6f137-d36e-47be-88c1-6c15cd9ad25e",
  "enrollment_id": 12,
  "enrollment_revision": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "verification_type": "face",
  "embedding_version": "auraface_512",
  "evidence": {"score": 0.8, "second_score": 0.2, "quality_ok": true, "live": true, "samples": 3},
  "clock_anchor_id": "90d6f137-d36e-47be-88c1-6c15cd9ad25e",
  "elapsed_ms": 1000
}
```

`periocular` hanya menerima `mobilefacenet_v1_periocular`. Tidak kirim `ad_id`, sesi pilihan client, SKPD pilihan client, template galeri, atau keputusan keterlambatan. Evidence adalah laporan perangkat terpercaya, bukan bukti bahwa komputer yang dibobol jujur.

Respons terminal HTTP 200: `{status:true, data:{event_id,outcome,reason,ad_id,work_date,session,received_at}}`. `outcome` salah satu `recorded|duplicate|outside_schedule|rejected`; `session` bernilai `check_in|check_out|null`. UUID berisi body berbeda → HTTP 409. Payload salah → 400; kredensial salah/dicabut → 401/403; kegagalan infrastruktur → 503. Penolakan HTTP 400/409 disimpan terminal lokal; 401/403 menahan antrean dan meminta operator; 429/5xx/timeouts retry.

Snapshot: `{protocol_version:1,snapshot_id,issued_at,sync_after_seconds:60,clock_anchor_id,policy_revision,models,employees}`. `models` membawa hash bobot, versi, dimensi, ambang dan margin hasil kalibrasi; jangan mengambil ambang SFace. `employees[]` berisi `pegawai_id,display_name,verification_type,enrollment_id,enrollment_revision,embedding_version,samples`. Templat berupa array vektor float; sampel AuraFace 512, periocular 192. Snapshot JSON lengkap satu respons pada tahap pertama, batas respons 16 MiB; oversize menggagalkan sinkronisasi tanpa mengganti cache. Tidak menambah paging selama galeri Diskominfo muat.

## Task 1: Lock protocol, schema evidence, and synthetic fixtures

**Files:** API create `docs/kiosk/schema-audit.md`, `docs/kiosk/protocol.md`, `tests/fixtures/kiosk/event.json`, `tests/fixtures/kiosk/snapshot.json`, `api/services/kioskProtocol.js`, `tests/kioskProtocol.test.js`; CAM create `integration/__init__.py`, `integration/protocol.py`, `tests/test_eoffice_protocol.py`, `tests/fixtures/eoffice_snapshot.json`.

**Interfaces:** JS `validateEvent(value) -> normalizedEvent` (throws on invalid), `bodyHash(Buffer) -> hex`; Python `validate_snapshot(dict) -> dict` (raises `ValueError`). Fixtures carry synthetic vectors only. No DB mutation in this task.

- [ ] Record `SHOW CREATE TABLE`/indexes/engines for `abs_pegawai_face`, `tb_pegawai`, schedule sources, and one disposable monthly attendance partition. Check duplicate parent/detail rows with GROUP BY/HAVING; report counts, never delete. Record all callers of `update_absen_detail`, `update_absen_detail_double`, absence-reset methods, parent/detail inserts and duplicate cleanup; include `absen_v2`, dashboard fallbacks, prewarm and admin/assignment paths. Read-only schema credentials must explicitly target a staging/test database.
- [ ] Write failing protocol tests; repeat for periocular dimensions, oversized arrays, invalid UUID/offset, null/missing fields, NaN/Infinity, unknown keys and unsupported models. Test code anchor:

```javascript
const test = require('node:test');
const assert = require('node:assert/strict');
const { validateEvent, bodyHash } = require('../api/services/kioskProtocol');
const event = require('./fixtures/kiosk/event.json');
test('client cannot choose the session', () => {
  assert.throws(() => validateEvent({...event, session: 'check_out'}));
});
test('retry hash is over the persisted bytes', () => {
  const bytes = Buffer.from(JSON.stringify(event));
  assert.equal(bodyHash(bytes), bodyHash(Buffer.from(bytes)));
});
```

```python
import copy
import json
from pathlib import Path
import pytest
from integration.protocol import validate_snapshot

def test_bad_dimension_never_enters_gallery():
    path = Path(__file__).parent / "fixtures/eoffice_snapshot.json"
    snapshot = json.loads(path.read_text())
    bad = copy.deepcopy(snapshot)
    bad["employees"][0]["samples"][0] = [1.0] * 128
    with pytest.raises(ValueError):
        validate_snapshot(bad)
```

- [ ] Run API `node --test tests/kioskProtocol.test.js` and CAM `python -m pytest tests/test_eoffice_protocol.py -q`; require initial failure at missing implementation, not missing runtime/dependencies. Keep synthetic snapshot fixtures identical in both repositories and assert their content matches the protocol example.
- [ ] Implement allowlisted fields/types, positive safe integer IDs, finite bounded score fields, strict RFC3339 WITA timestamps, bounded sample counts, UUID and exact version/dimension pairs. Hash code:

```javascript
const { createHash } = require('node:crypto');
function bodyHash(bytes) {
  return createHash('sha256').update(bytes).digest('hex');
}
```

- [ ] Run both tests to PASS; commit protocol/docs/fixtures only with message `test: lock kiosk integration protocol` in each affected repo. Do not install production runtime yet.

## Task 2: Add device credentials, immutable snapshots, and enrollment eligibility

**Files:** API create `migrations/20261014_kiosk_attendance.sql`, `query/KioskQuery.js`, `scripts/kiosk-device.js`, `tests/kioskSnapshot.test.js`, `tests/kioskSchema.integration.test.js`; modify `migrations/README.md`.

**Interfaces:** `KioskQuery.authenticate(secret) -> device|null`; `createSnapshot(device) -> snapshot`; `getEligibleEnrollment(trx,device,pegawaiId,enrollmentId,revision) -> row|null`; static `KioskQuery.isEligibleEnrollment(row,{active,authorizedType}) -> boolean` for the eligibility predicate. Device contains `{device_id,skpd_id,enabled}`. Never reuse `findDuplicateMatches` unmodified: it includes pending enrollment and other SKPDs.

- [ ] Add tests for active AuraFace multi-sample, active authorized periocular with null `verified_by`, pending face excluded, moved/disabled pegawai excluded, duplicate model rows rejected rather than arbitrarily chosen, revoked device denied, and enrollment replacement changing revision.

```javascript
test('pending face is never exported', async () => {
  const row = {verification_type:'face', enrollment_status:'pending', embedding_version:'auraface_512'};
  assert.equal(KioskQuery.isEligibleEnrollment(row, {active:true, authorizedType:'face'}), false);
});
test('authorized active periocular does not require a face verifier column', () => {
  const row = {verification_type:'periocular', enrollment_status:'active', verified_by:null,
    embedding_version:'mobilefacenet_v1_periocular'};
  assert.equal(KioskQuery.isEligibleEnrollment(row, {active:true, authorizedType:'periocular'}), true);
});
```

- [ ] Run `node --test tests/kioskSnapshot.test.js` and observe expected initial failure. Tests use injected Knex/crypto dependencies, following existing API test patterns.
- [ ] Create additive InnoDB tables in `simpeg_integrasi`: `abs_kiosk_devices` (UUID PK, skpd, credential_hash UNIQUE, enabled, model_policy JSON); `abs_kiosk_snapshots` (UUID PK, device FK, policy_revision, issued_at, encrypted_payload); `abs_kiosk_events` (device_id,event_id composite PK, body_hash, result_json, received_at, pegawai_id,work_date,session,ad_id); `abs_attendance_day_locks` (pegawai_id,work_date composite PK). Use `CHAR(36)` UUID, `CHAR(64)` hash, DATETIME(6) UTC storage and JSON result. Snapshot payload, including templates and policy, encrypted using existing server crypto key material; no plaintext biometric JSON at rest. Do not add/delete indexes on historical attendance partitions without audit evidence. In this protocol `clock_anchor_id` equals snapshot_id and anchor time equals issued_at; a second clock-anchor table is unnecessary.
- [ ] Implement a random 32-byte device secret; store only SHA-256 digest and compare using `timingSafeEqual` after fixed-length validation. CLI supports `create --skpd-id <id> --model-policy <json-file>` and `revoke --device-id <uuid>`; rejects missing/unverified unit IDs and prints secret once. Validate model_policy contains both model hashes, calibrated score/margin/quality/liveness settings and sample minima; no inherited SFace defaults. Staging trial policy is explicitly separate from approved production policy. Unit ID must come from staging/production configuration, never guessed from the name Diskominfo.
- [ ] Query raw authoritative employee/enrollment data without stale Redis employee cache. Decrypt using existing `embeddingCrypto`; validate finite nonzero vectors and exact chunk boundaries; derive revision from enrollment metadata plus encrypted-blob digest. Build a full snapshot inside one consistent read transaction, bind it to the device, encrypt before persistence. Never include blocked/pending/skip/biometric_key rows.
- [ ] Run the unit tests plus `node --test tests/kioskSchema.integration.test.js` with an explicitly disposable MySQL schema. The test creates fresh fixtures, applies migration twice safely, and verifies duplicate event/device/day-lock constraints. Commit scoped files: `feat: add kiosk device and enrollment snapshots`.

## Task 3: Resolve schedules from event time with auditable revisions

**Files:** API create `api/services/kioskSchedule.js`, `tests/kioskSchedule.test.js`; extend `query/KioskQuery.js`, `migrations/20261014_kiosk_attendance.sql`, `tests/kioskSchema.integration.test.js`, `docs/kiosk/schema-audit.md`; modify `query/AbsensiQuery.js` only to expose date-explicit policy reads where necessary.

**Interfaces:** `resolveCameraSession({occurredAt,policy}) -> {outcome,workDate,session,ahId,fields,reason}`; `KioskQuery.getPolicyAt(trx,snapshotId,occurredAt) -> policy|null`. `fields` contains the existing e-office attendance values including seconds-based late length and TPP, not newly invented calculations.

- [ ] Add baseline characterization tests of the existing morning/checkout rules, Friday pairs, per-day overrides, holidays/exceptions and special employee statuses. Specify only `ah_id=1` as check-in and `ah_id=2` as check-out; 3/4 are never selected as independent camera sessions. Primary-only and Friday replication still follow e-office's detail representation.

```javascript
const { resolveCameraSession } = require('../api/services/kioskSchedule');
test('Friday resolves checkout, never an independent break session', () => {
  const policy = {workDate:'2026-10-16', holiday:false, windows:[
    {session:'check_out', ahId:2, from:'2026-10-16T11:30:00+08:00', to:'2026-10-16T13:00:00+08:00', fields:{as_id:1}}
  ]};
  const out = resolveCameraSession({occurredAt:'2026-10-16T11:45:00+08:00',policy});
  assert.equal(out.session,'check_out');
  assert.equal(out.ahId,2);
});
```

- [ ] Run `node --test tests/kioskSchedule.test.js`; initial missing-module failure expected. Add boundary tests exactly at opens/closes, no schedule, two eligible overlapping windows, previous-day work date, and changed shift while offline.
- [ ] Extract the relevant decision calculation from `APIabsensi.js` into date-explicit functions in `kioskSchedule.js` and call the same calculation from mobile without changing mobile confirmation or security gates. Keep the mobile five-minute stale-session guard intact; offline events use the dedicated endpoint. Camera never bypasses a confirmation by setting `was_reported=1`. Camera excludes early checkout requiring confirmation and is_encrypt/random-time exceptions from automatic writes; authoritative special-status rows remain protected.
- [ ] Persist the resolved source policy in server snapshots. Add `abs_kiosk_policy_revision` (singleton revision) and `abs_kiosk_policy_changes` (revision, changed_at UTC). Transactional DB triggers increment revision on relevant source mutations, capturing changes from every admin/API writer. Trigger sources must include actual schedule/assignment/holiday/TPP dependencies recorded in Task 1: `abs_absen_hour`, `abs_absen_hour_day`, `abs_absen_shift`, `abs_holiday`, `abs_holiday_exept`, `abs_absen_except`, `_setting`, `tb_pegawai`, `tb_skpd`, `tb_subunit`, `tb_usulpelaksana`, and any additional source used by the extracted policy. Generate column predicates from the audited schema for assignment/status fields; unrelated login/profile changes must not invalidate all snapshots. Fail migration if required source/trigger coverage is absent; never claim historical support from hashes alone.
- [ ] To avoid a separate scheduling subsystem, use immutable snapshots plus revision validity intervals. A snapshot is usable only after issued_at and before the first committed source revision change. If capture occurred after a revision change without a corresponding server snapshot, return `historical_schedule_unavailable`, keep audit, and do not write attendance. Existing valid snapshots still support events captured before a later change. Initial deployment has no invented pre-deployment schedule history. Missing partition/policy is an explicit rejection, not fallback to today's values.
- [ ] Add a real-DB test that mutates A→B→A and verifies an old snapshot cannot authorize the middle interval; assert trigger changes roll back with the source transaction. Test simultaneous snapshot creation/source edit for a consistent revision+payload pair. Run Task 2–3 tests to PASS and commit `feat: resolve kiosk attendance against event-time schedule`.

## Task 4: Serialize legitimate session writes across HP, camera, and skeleton creation

**Files:** API create `api/services/attendanceWrite.js`, `tests/attendanceWrite.test.js`, `tests/attendanceWrite.integration.test.js`; modify `query/AbsensiQuery.js` (`update_absen_detail` around 1066, `update_absen_detail_double` around 1101 and absence reset methods), `api/APIabsensi.js` (`absen_v2` write block around 1950–1990 and dashboard skeleton fallbacks), `api/services/absenSkeleton.js`, `workers/absenSkeletonWorker.js`.

**Interfaces:** `withAttendanceDay(db,{pegawaiId,workDate},fn) -> Promise<result>` supplies one transaction to callback; `writeAttendanceSession(trx,{query,workDate,ahIds,fields,source}) -> {outcome,adIds}`. Every query in a protected operation uses `trx`, not the global connection. Existing admin correction remains explicit privileged behavior.

- [ ] Write tests where camera wins, HP wins, two simultaneous HP submissions, existing cuti/admin correction, empty skeleton, legitimate recorded midnight, partially filled Friday pair, and skeleton prewarm races with camera. A row is not empty merely because `ad_time === '00:00:00'`; compare skeleton provenance/status and absence of official actor/source/audit. Ambiguous legacy status defaults to protected, not overwritten.

```javascript
test('winner cannot be overwritten by another submission', async () => {
  const results = await Promise.all([
    withAttendanceDay(db,{pegawaiId:101,workDate:'2026-10-14'},
      trx => writeAttendanceSession(trx,{query,workDate:'2026-10-14',ahIds:[1],fields:cameraFields,source:'kamera_kantor'})),
    withAttendanceDay(db,{pegawaiId:101,workDate:'2026-10-14'},
      trx => writeAttendanceSession(trx,{query,workDate:'2026-10-14',ahIds:[1],fields:mobileFields,source:'mobile'}))
  ]);
  assert.deepEqual(results.map(x=>x.outcome).sort(), ['duplicate','recorded']);
});
```

`db/query/cameraFields/mobileFields` in this integration test are real Knex fixtures created in the test's disposable schema before each test and destroyed afterward; `cameraFields.ad_time='07:25:00'`, `mobileFields.ad_time='07:30:00'`, both `as_id=1`. No mocks substitute for contention.

- [ ] Run `node --test tests/attendanceWrite.test.js` and `node --test tests/attendanceWrite.integration.test.js`; require initial failure and refuse the integration test unless `KIOSK_TEST_DATABASE_URL` names a schema with prefix `kiosk_test_`.
- [ ] Implement transaction start, insert-ignore day-lock row, `SELECT ... FOR UPDATE`, locate/create parent/details under that lock, select affected rows for update, then conditional write. Key order is day lock → detail rows ascending ad_id. Serialize missing skeleton creation too; replace reactive duplicate deletion in the touched paths with the shared protected creator. If pre-existing duplicate official parents/details exist, refuse and report remediation; do not silently delete data.

```javascript
await trx('abs_attendance_day_locks').insert({pegawai_id:pegawaiId,work_date:workDate}).onConflict().ignore();
await trx('abs_attendance_day_locks').where({pegawai_id:pegawaiId,work_date:workDate}).forUpdate().first();
```

- [ ] Pass the same transaction through all queries and all Friday/next-session reset writes. If any target is already official, preserve it; a loser request must not run secondary resets, enqueue a new official photo, or launch attendance side effects. An authorized administrative correction is audited as correction and is not accidentally blocked as an ordinary second check-in. Keep unrelated leave/assignment workflows' permissions and semantics.
- [ ] Run baseline `npm test` plus contention tests; verify WFH, late/early logic, Friday representation, special-status and admin corrections remain intact. Commit `fix: protect attendance sessions from cross-channel overwrites` using the explicit Files list, not all working changes.

## Task 5: Expose authenticated snapshot and idempotent event endpoints

**Files:** API create `api/APIkiosk.js`, `api/services/kioskAttendance.js`, `tests/kioskAttendance.test.js`, `tests/kioskAttendance.integration.test.js`; modify `app.js`, `query/KioskQuery.js`, `docs/kiosk/protocol.md`.

**Interfaces:** `acceptKioskEvent({device,rawBody,event},deps) -> responseData`; `APIkiosk.snapshot(req,res)`, `APIkiosk.event(req,res)`; consumes Task 1 validation, Task 2 eligibility, Task 3 resolution, Task 4 transaction writer.

- [ ] Write tests for unauthenticated access, forged SKPD/device/session, snapshot belonging to another device, enrollment revised/revoked, duplicate UUID same body, changed body 409, lost response, HP winner, disconnected Redis, malformed JSON, oversized body, and event-time partition selection.

```javascript
test('same event cannot change after first processing', async () => {
  const first = await acceptKioskEvent({device,rawBody:bytes,event},deps);
  assert.equal(first.outcome,'recorded');
  const changed = {...event, occurred_at:'2026-10-14T07:26:00+08:00'};
  await assert.rejects(
    acceptKioskEvent({device,rawBody:Buffer.from(JSON.stringify(changed)),event:changed},deps),
    error => error.statusCode === 409
  );
});
```

`device/event/bytes/deps` use Task 1 JSON and a fresh test transaction fixture; add a separate real-DB test for concurrency, not only injected dependencies.

- [ ] Run `node --test tests/kioskAttendance.test.js`; expected fail before endpoint service exists.
- [ ] Mount routes with scoped JSON/raw-body capture before unrelated global parsing loses the bytes, limit event body to 16 KiB, and use existing response/error logging conventions. Validate then authenticate/authorize before decrypting snapshots or exposing employee data. Snapshot endpoint uses `Cache-Control: no-store`. Explicitly reject unsupported content types and redirects at the client.
- [ ] Within one transaction, reserve `(device_id,event_id)`, compare body hash for existing entries, recheck device/employee/enrollment, validate clock proof, resolve policy, obtain the day lock and write only an empty eligible session; persist terminal result in the event row. SQL failure rolls back both attendance and event result. Deadlock retry uses bounded attempts and the same event ID; exhaustion returns 503. Replay of an existing valid terminal event returns its persisted result; a revoked credential still cannot authenticate to retrieve it.
- [ ] Test a capture on `2026-10-31T16:05:00+08:00` received in November and a December capture received January: `setDB(...,workDate)` selects the old partition, not current month. Clock anchor validation accepts elapsed offline time, rejects negative elapsed/future time/inconsistent WITA, and never substitutes received_at. Record source/device/event in `abs_kiosk_events`; no need to add a source column to every monthly detail partition.
- [ ] Run all API unit and real-DB integration tests, then commit `feat: accept idempotent camera attendance events`.

## Task 6: Reproduce both enrollment model pipelines on the kiosks

**Files:** CAM create `recognition/eoffice_models.py`, `requirements-eoffice.txt`, `tests/test_eoffice_models.py`, `tests/test_eoffice_model_parity.py`; modify `docs/MODEL_DECISION.md`. Read API `face-service/app/recognizer.py` and Android/iOS preprocessing sources as references; do not change mobile enrollment.

**Interfaces:** `AuraFaceEmbedder(model_path).embed(frame, landmarks) -> float32[512]`; `PeriocularEmbedder(model_path).embed(frame, eye_landmarks) -> float32[192]`; `periocular_tensor(crop_bgr) -> float32[1,112,112,3]`. `model_manifest(paths) -> {version,sha256,dimension}`. Missing/mismatched assets cause startup failure.

- [ ] First create failing tests for BGR→RGB, shape, normalization, zero/non-finite outputs, model hashes, integer rounding/clamped eye crop, too-small eye distance, and output L2 norm. Use synthetic colored crops with hard-coded expected tensors and landmarks; model tests are separate from camera/transport tests.

```python
def test_periocular_preprocessing_matches_mobile_constants():
    import numpy as np
    from recognition.eoffice_models import periocular_tensor
    crop = np.zeros((112,112,3), dtype=np.uint8)
    tensor = periocular_tensor(crop)
    assert tensor.shape == (1,112,112,3)
    assert np.all(tensor == np.float32(-127.5 / 128.0))
```

- [ ] Run `python -m pytest tests/test_eoffice_models.py -q`; expected initial failure. Export `periocular_tensor` with the exact name used by the test.
- [ ] Add integration-only dependencies: `-r requirements.txt`, `onnxruntime==1.26.0` (matching existing face-service runtime), `ai-edge-litert==2.3.0`, and `scikit-image==0.25.2`. This scikit-image release lists Python 3.11 Windows wheels on [PyPI](https://pypi.org/project/scikit-image/0.25.2/). Do not replace GUI OpenCV with headless OpenCV or add full TensorFlow. Resolve/install/check the entire set on the actual Windows kiosk architecture before declaring this task passed; dependency conflict blocks the task and requires a documented compatible pin, not a silent broad upgrade.
- [ ] Use the exact AuraFace `glintr100.onnx` and the existing MobileFaceNet asset. Android and iOS assets currently share SHA-256 `be4bc7cfc53f7bc336d0f28b1ab92535f618c913a422b683210750f6b5354854`. Compare deployed weights, not just repository filenames. Copy model artifacts through operator installation into model_files; no production template or large new binary commit.

```python
from ai_edge_litert.interpreter import Interpreter
interpreter = Interpreter(model_path=str(model_path), num_threads=2)
interpreter.allocate_tensors()
```

- [ ] Port AuraFace alignment/preprocessing exactly from existing server recognizer; use LiteRT Interpreter on the original `.tflite` without conversion. Port mobile eye-region crop, round-half-up coordinate behavior and RGB normalization. Detector landmarks may differ from ML Kit: first prove tensor/model parity with fixed mobile landmarks, then separately test actual camera landmarks and cadar detection; do not treat model parity as detector parity.
- [ ] Run parity tests with real local weights and non-production consented samples; compare AuraFace outputs to server and periocular outputs to a mobile-generated golden embedding from the same normalized image/landmarks. Use cosine ≥0.999 for fixed tensor model parity and record max absolute difference. Mark tests requiring assets explicitly and do not count skipped parity tests as success. Run `python -m pip check` and unit tests; commit `feat: run e-office face and periocular models locally`.

Runtime evidence checked while planning: [Google LiteRT Python migration](https://developers.google.com/edge/litert/migration) documents the Interpreter import; [PyPI ai-edge-litert 2.3.0](https://pypi.org/project/ai-edge-litert/2.3.0/) lists a CPython 3.11 Windows x86-64 wheel. Availability of a wheel does not prove our whole dependency set or model works on the kiosk.

## Task 7: Persist encrypted gallery, event queue, and trustworthy time

**Files:** CAM create `integration/store.py`, `integration/clock.py`, `tests/test_eoffice_store.py`, `tests/test_eoffice_clock.py`; modify `config.py`, `.env.example`.

**Interfaces:** `IntegrationStore(path,key).replace_snapshot(snapshot)`, `.load_snapshot()`, `.enqueue(event_bytes)`, `.pending()`, `.finish(event_id,response)`, `.close()`; pending entries expose `event_id` and immutable `body` bytes. `TrustedClock.anchor(server_time,anchor_id,monotonic_now)`, `.event_time(monotonic_now) -> {occurred_at,clock_anchor_id,elapsed_ms}`. Store owns a standard-library SQLite DB at `instance/eoffice.sqlite3`, separate from standalone MySQL employee IDs.

- [ ] Write failure tests for process reopen after pending insert, snapshot partial/corrupt download, tampered ciphertext/wrong key, same UUID different bytes, DB full, and two senders seeing the same pending row. Test immutable stored bytes and encrypted names/templates. Final transport idempotence protects duplicate sends; single-instance kiosk lock prevents ordinary dual workers.

```python
def test_pending_bytes_survive_reopen(tmp_path):
    from integration.store import IntegrationStore
    path = tmp_path / 'eoffice.sqlite3'
    key = bytes(range(32))
    first = IntegrationStore(path,key)
    payload = b'{"event_id":"85f42b66-6a77-4c56-9efc-4231ff6eab42"}'
    first.enqueue(payload)
    first.close()
    second = IntegrationStore(path,key)
    assert second.pending()[0].body == payload
```

- [ ] Run `python -m pytest tests/test_eoffice_store.py tests/test_eoffice_clock.py -q`; require initial failure.
- [ ] Implement SQLite transactions, FULL synchronous durability, bounded busy timeout, atomic snapshot replace, encrypted payloads with AES-GCM and associated data identifying snapshot/event. Reuse key-file loading from biometric service where suitable; do not reuse employee-bound packing or import all e-office employees into standalone tables. Keep pending rows until a validated terminal response is committed. Wrong key is an operator error, never silently create an empty gallery/queue.
- [ ] Add clock anchors issued by authenticated server snapshot, persisting server ID and capture boot ID; within one boot derive elapsed time from monotonic time, compare wall-clock jumps, and stop new capture if difference exceeds 30 seconds. Server verifies capture time against anchor+elapsed with maximum rounding tolerance 2 seconds and rejects >120 seconds in the future. These are implementation guard constants, not attendance tolerances. After restart offline, keep old pending events and pause new capture until a fresh anchor; do not pretend process monotonic clocks persist across boots.
- [ ] Integration config: `ATTENDANCE_MODE=standalone|eoffice` (default standalone), `EOFFICE_BASE_URL`, `EOFFICE_DEVICE_TOKEN`, local model paths and key path. Require HTTPS, both model assets, passive-only liveness and complete server-calibrated model policy in eoffice mode. Retain existing standalone configuration defaults. Tests use temporary DB/key files only. Run tests to PASS and commit `feat: persist encrypted kiosk integration state`.

## Task 8: Synchronize snapshots and retry events outside camera processing

**Files:** CAM create `integration/client.py`, `integration/sync.py`, `tests/test_eoffice_sync.py`; extend `tests/test_eoffice_protocol.py`.

**Interfaces:** `EofficeClient(base_url,token).snapshot() -> dict`, `.send(event_bytes) -> result`; `SyncWorker(store,client,clock).run_once() -> status`. One background worker owns network I/O; frame processing owns inference. Worker uses its own SQLite connection and communicates UI results through a bounded in-process queue.

- [ ] Add fake-transport tests for timeout, lost response after commit, 429/503, 400/409 terminal, 401/403 pause, wrong event_id/invalid outcome, partial response, oversize snapshot, redirect, reordered results, and normal reconnection.

```python
def test_timeout_keeps_same_event_pending(store):
    from integration.sync import SyncWorker
    class Offline:
        def snapshot(self):
            raise TimeoutError()
        def send(self, body):
            raise TimeoutError()
    original = store.pending()[0].body
    SyncWorker(store,Offline(),None).run_once()
    assert store.pending()[0].body == original
```

The `store` test fixture creates a temporary `IntegrationStore`, inserts Task 1 event bytes and yields it; define it locally in `test_eoffice_sync.py`, not in production helpers. Use a fixed trusted test clock for snapshot-success cases.

- [ ] Run `python -m pytest tests/test_eoffice_sync.py -q`; expected initial failure.
- [ ] Implement HTTPS urllib requests with explicit 5-second timeout, certificate verification, no redirects, request body bytes from the queue, response size limits and response validation. Poll snapshot after 60 seconds and immediately on reconnect, but failed snapshot refresh must not starve pending event submission. Retry backoff 1,2,4,8,16,32,60 seconds capped at 60, reset after success. No sleep/network calls in camera `step()`.
- [ ] Persist terminal status only after matching event ID and known outcome. Use event timestamp+ID for stable oldest-first order. 401/403 pauses sends without discarding rows; retry only after credential/config repair. Surface storage, connection and auth status separately. Refresh gallery first where possible; queued events retain their original snapshot/enrollment revision.
- [ ] Test two worker attempts sending the same stored body and a late response: queue remains consistent and eventual terminal result equals server's persisted result. Run Task 7–8 tests and commit `feat: sync kiosk gallery and offline attendance queue`.

## Task 9: Connect recognition, passive gates, queue and existing kiosk display

**Files:** CAM create `recognition/eoffice_identify.py`, `tests/test_eoffice_identify.py`, `tests/test_eoffice_kiosk.py`; modify `kiosk/worker.py`, `kiosk/cli.py`, `kiosk/state.py`, `static/js/kiosk.js`, `templates/kiosk.html`; extend `tests/test_kiosk.py`, `tests/test_face_enrollment_remote.py`.

**Interfaces:** `recognize_eoffice_frame(frame,detector,models,gallery,quality,tracker,now) -> (tracks,events)` uses existing `MatchResult`, `Matcher`, `FaceTracker`; `merge_model_matches(face:MatchResult,eye:MatchResult) -> MatchResult` rejects conflicting identities. `KioskWorker.handle_event` selects standalone recorder OR integration enqueue exactly once. Integration events use authoritative pegawai_id and enrollment metadata, not local Employee primary keys.

- [ ] Write tests: stable full-face identity, stable periocular identity, close second candidate, contradictory model identities, two people crossing tracks, insufficient frames, passive spoof/uncertain rejection, no snapshot, store failure, and legacy standalone recording unchanged. Assert eoffice mode never calls `att.record` or claims/executes local face-enrollment requests.

```python
def test_model_identity_conflict_is_rejected():
    from recognition.matcher import MatchResult, MATCH, AMBIGUOUS
    from recognition.eoffice_identify import merge_model_matches
    face = MatchResult(MATCH,101,0.90,0.10)
    eye = MatchResult(MATCH,202,0.95,0.20)
    assert merge_model_matches(face,eye).status == AMBIGUOUS
```

- [ ] Run `python -m pytest tests/test_eoffice_identify.py tests/test_eoffice_kiosk.py -q`; expected initial failure.
- [ ] Reuse existing Matcher per model with snapshot-specific thresholds/margins, identity voting and per-person cooldown. Add only the dispatch needed to preserve model provenance through tracking/liveness. Full-face and eye pipelines stay separate; combined match conflicts reject. Existing face quality checks that require visible mouth/nose cannot be applied blindly to periocular; use tested eye-landmark/region quality without weakening liveness.
- [ ] Integration mode enforces passive-only gates with sufficient frames and type-specific calibrated checks. No success with liveness off, missing periocular calibration, timeout, or uncertain result. Do not declare MiniFASNet reliable on cadar by copying a threshold. Task 10 validates that gate; failed field validation blocks official activation.
- [ ] Startup selects integration dependencies without loading SFace or local enrollment gallery. Background sync starts/stops with kiosk lifecycle; release camera, join worker with bounded timeout and close DB safely. Frame processing commits queue before showing pending, then consumes asynchronous server results.
- [ ] Existing display shows first name and statuses: “Menunggu sinkronisasi”, “Berhasil masuk/pulang”, “Sudah absen”, “Di luar jadwal”, “Identitas belum dipastikan”, “Penyimpanan bermasalah”, “Hubungi operator”. Publish a unique message/event ID; late result for an older person must not replace current person's result. Add `aria-live` for status; use text plus optional local desktop sound without depending on browser autoplay permission. Keep scores, identifiers, templates and auth errors out of public_view.
- [ ] Add display tests for pending not counted as recorded, fresh recorded count, stale response suppression, status expiry and auth/storage faults. Run existing CAM regression suite `python -m pytest -q`, inspect `/kiosk` with synthetic states, and commit `feat: connect walk-through camera attendance to e-office`.

## Task 10: Prove compatibility, data consistency, and operational recovery

**Files:** CAM create `tests/test_eoffice_end_to_end.py`, `docs/EOFFICE.md`, `docs/qa/2026-10-14-eoffice-camera-acceptance.md`; modify `docs/INSTALASI.md`, `docs/OPERASIONAL.md`, `docs/KEAMANAN.md`, `docs/MODEL_DECISION.md`; API create `docs/kiosk/operations.md`; extend `tests/kioskAttendance.integration.test.js`.

**Interfaces:** Use actual Task 5 HTTP endpoints, Task 6 local models, Task 7 queue and Task 9 worker. Integration runner uses a disposable DB/API process and fake frame events for deterministic networking tests; hardware trials use the actual kiosk and existing consented enrollment, never fixture biometric data in Git.

- [ ] Add failing end-to-end tests for capture 07.25 offline → HP commit 07.30 → reconnect 07.40, camera-first/HP-second, opposite-channel checkout, response lost after commit, reboot with pending rows, enrollment revoke during outage, month rollover, and snapshot policy change. Final expected official time for the first case is 07.30 and exactly one session row.

```python
def test_offline_camera_does_not_replace_mobile(live_api, kiosk_store):
    camera = live_api.camera_event(occurred_at="2026-10-14T07:25:00+08:00")
    kiosk_store.enqueue(camera)
    live_api.mobile_submit(pegawai_id=101, time="07:30:00", session="check_in")
    response = live_api.send_camera(kiosk_store.pending()[0].body)
    assert response["outcome"] == "duplicate"
    assert live_api.official_time(101,"2026-10-14","check_in") == "07:30:00"
```

`live_api` is a test fixture in this file that starts the local disposable API, provisions one test device, creates synthetic enrollment/policy and exposes HTTP calls plus read-only fixture DB inspection. `mobile_submit` must invoke the real APIabsensi shared writer path, not write the attendance table directly. It may stub unrelated photo/integrity network providers only within test process; production security gates remain intact.

- [ ] Run `python -m pytest tests/test_eoffice_end_to_end.py -q` against the disposable service and observe each new assertion failing on the missing behavior before fixing only its owning component. Run all relevant suites after fixes; do not label environment setup failures as expected red tests.
- [ ] Write exact operator instructions: model/key installation, migration order, verified Diskominfo ID, device secret provisioning, TLS verification, time sync, virtualenv dependency check, queue status/recovery, credential revoke, missing historical policy, and backup both encrypted SQLite and local key separately. Restore test must recover pending event IDs/bytes. Rollback disables device/event endpoint and integration mode, keeps event/queue records, and resumes standalone only by explicit operator selection; never flush pending data.
- [ ] Complete field trials separately for AuraFace and periocular: 20 normal walking passes per participating employee, morning/afternoon lighting, glasses/cadar, two people passing together, and printed-photo/phone-screen attempts. Record numerator/denominator, failures, false identities, spoof accepts, and decision latency. No wrong identity/spoof acceptance is allowed in this set; all participants must demonstrate no-stop success with existing enrollment. These trial counts define the initial acceptance exercise, not a statistical guarantee.
- [ ] If periocular detection/liveness or CPU performance fails, keep official activation disabled; record measured cause and request a reviewed design adjustment if hardware/approach must change. Do not replace “walk through” with required stopping or exclude periocular as a hidden workaround.
- [ ] Verify API `npm test`, disposable MySQL contention tests, CAM `python -m pytest -q`, dependency `pip check`, real model parity, backup/restore and field reports. Acceptance report distinguishes passed/failed/not-run with evidence. Commit only scoped code/docs with `test: verify kiosk attendance consistency and recovery`. Do not deploy or enable official attendance in this planning session.

## Self-review and coverage map

| Spec section | Tasks |
| --- | --- |
| 1: scope and decisions | Global constraints, 5, 9, 10 |
| 2: model/source compatibility | 1, 2, 6, 10 |
| 3: architecture/trust boundaries | 2, 4, 5, 8 |
| 4: enrollment sync/encryption/revocation | 2, 5, 7, 8 |
| 5: walk-through recognition and passive anti-spoof | 6, 9, 10 |
| 6: event contract/results | 1, 5, 7, 8 |
| 7: schedule/history and HP contention | 3, 4, 5 |
| 8: offline queue/time/restart | 5, 7, 8, 10 |
| 9: status/operations | 8, 9, 10 |
| 10: change scope and reuse | File structure, every task Files list |
| 11: automated and physical validation | Every test cycle, 10 |
| 12: review/execution gate | Handoff below |

Planning decisions to review: history is limited to verified snapshot/revision intervals, with explicit rejection for uncovered changes; after offline restart new captures pause until time can be anchored again; existing pending events survive; runtime additions exist only in integration requirements. These preserve the spec's fail-closed behavior and avoid inventing historical data or retraining/re-enrolling employees.

## Execution handoff

Review this plan before implementation. Choose one execution method:

- **Subagent-driven:** one implementer and fresh reviewer per task, followed by branch review. Recommended because model compatibility, persistent retries and cross-channel transactions need distinct checks before later tasks depend on them.
- **Native:** implement task-by-task in this session and request a fresh branch review at the end. Fewer context handoffs, but independent review happens later.

No implementation, runtime installation, migration execution, production database access or hardware testing has occurred while writing this plan. Commit changes per repository, preserving unrelated work. Do not start execution until the user approves this written plan and chooses a method.
