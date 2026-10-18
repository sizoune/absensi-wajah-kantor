# Production Camera Test Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Recognize real active Diskominfo enrollments from Mac camera and store out-of-hours test records on production separately from official recap.
**Architecture:** Minimal test-only API patch based on actual production4f4c891 in a separate API worktree, plus explicit test purpose/path/store/UI in existing CAM worktree. Existing official integration branch is not deployed. Dedicated expiring device and three test tables isolate production experiment.
**Tech Stack:** Existing Express/Knex/MySQL/embeddingCrypto, Flask/OpenCV/AuraFace/LiteRT/passive/runtime, Compose.
**Spec:** docs/superpowers/specs/2026-10-18-production-camera-test-design.md

## Global Constraints
- User chose separate test records. Never write official recap/provenance/day locks or install old official kiosk migrations/triggers; no enrollment modifications.
- Test-only purpose, endpoints, token and queue; strict isolation rejects mixed official/test traffic/results. No fallback or model/liveness/clock relaxation.
- Real eligible gallery restricted to verified current Diskominfo79, current active statuses/approval/model pair, exclude blocked; no plaintext biometrics/token in logs/reports.
- Production patchbase4f4c891 preserves current API and remote dirty Compose; all code review/test before deploy. Model weights fixed expected hashes, secrets outside repository.

## Review Focus
- Replayed UUID with altered purpose/body cannot mutate/reclassify records or double-write.
- Revoked/expired device and changed enrollment cannot accept queued old evidence.
- Mode switch cannot send old test queue to official endpoint or render official success.
- Production hotfix must not carry unaccepted official attendance writer/trigger changes.
- Production gallery and local model startup failure must leave official attendance unaffected and fail closed.

### Task 1: Isolated production test API
**Files (new API worktree /Users/muhammadwildaniskandar/Projects/E-office/.worktrees/eoffice-camera-test/api):** api/APIkioskTest.js, api/services/kioskTest*.js, query/KioskTestQuery.js, focused shared biometric/protocol helper(s) if needed, migrations/20261018_kiosk_test.sql, scripts/kiosk-test-device.js, tests/kioskTest*.test.js and disposableSQLfixture, app.js one registration, docs/kiosk-test.md. Own no other official attendance source.
**Interfaces:** snapshot uses current strict gallery schema plus purpose camera_test; POST event current kiosk protocol plus purpose camera_test; success envelope data has exactly event_id,outcome(test_recorded|rejected),reason,test_id,official_attendance:false,purpose:camera_test,received_at (WITA). Localclient will consume these exactfields; test_id positiveinteger whenrecorded, null rejected. AuthTLS64hexBearer, server24hex? token exactly64hex random32bytes. Storedsnapshot purpose strict; immutable raw hash/body replay response. HTTP400/409 compatible localterminal envelope.
- [ ] Write failing Node + disposableMySQL regressions for auth/expiry/unit/activegallery/periocular/snapshotmembership, model/evidence/time/revocation, outofhours+exactretry+conflict/rollback, no official writes. Observe RED.
- [ ] Implement minimal test tables and authenticated query/router with pure house validators. Strictpurpose snapshot/event, event canonical source immutable, no schedule calls. Add create/revoke CLI writing secret only into requested0600 destination file (notstdout), verifiedunit and boundedexpiry, immutable modelpolicy JSON.
- [ ] Run targeted and full Node suite, test actualSQLfixture; review diffs againstprod base; commit/report.

### Task 2: Camera test protocol, queue and UI
**Files CAM:** config.py, integration/client.py/protocol.py/store.py/sync.py, kiosk/cli.py/worker.py/browser.py/state.py, templates/kiosk.html/static/js/browser-camera.js, docker/start.py and explicit test overlay/wrapper/envsample/docs, focused Python/JS tests. Use existing eoffice mode plus EOFFICE_TEST_MODE bool, never change official defaults.
**Interfaces:** Task1 exactsnapshot/event purpose and testterminalfields. Test-only paths /api/kiosk-test/snapshot and /api/kiosk-test/events, separatequeue eoffice-test.sqlite3 and unambiguous UI test label/results. No client chosen session/time; actual recognition required before enqueue.
- [ ] RED tests for official/test strictcrossrejection, separatequeue/storeterminal validation, realworker synthetic recognition→testevent→testresultUI, noofficialsuccess, existingoffline/clock/freshness andpreview/native unchanged.
- [ ] Implement mode without duplicating recognition pipeline. Purpose-bound validation and persistence, clear test labels, serverterminalonly result. Test overlay/wrapper keeps production config deliberate and clearsambientEOFFICEvars.
- [ ] Run Python/JS regressions and actualDockerbuild/HTTPsyntheticclienttestfixture; commit/report. No productioncalls byimplementer.

### Task 3: Deploy reviewed isolated API and connect local test
**Files:** operator rollout/evidence in ignored planworkspace; final operator doc checks only. Controller executes existingauthorized productionoperations, no implementers touchserver.
- [ ] Verify final scoped patch only; production4f4c891 appsourcecurrent; capture rollbackimage/ref and preserveddirtyCompose. Validate productionDDL+unit+models/policy readonly; testmigration ondisposable beforeproduction.
- [ ] Apply only testtablemigration and expiringdevice credential; transfer reviewed branch/provenance toserver and build/recreate onlyAPI app with preservedCompose/env; health401unauth/400invalidchecks, independentofficialAPIhealth before/after. Never fake real employee success.
- [ ] Prepare localexternalassets/key0600 and ignoredtestenv without logging secrets, build/run explicit testCompose, verify gallerycount and modelhashes, queuepurpose/connectionhealth; leavecamera user-activated. Document actual connection/blockers/rollback/revoke; officialrecap unchanged.
