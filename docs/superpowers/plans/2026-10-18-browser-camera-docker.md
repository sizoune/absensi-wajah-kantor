# Browser Camera Docker Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run docker compose up and activate Mac built-in camera from the kiosk browser.
**Architecture:** Single Flask/Waitress container serves UI and receives bounded authenticated same-origin frames; an owned runtime feeds the existing e-office worker when explicitly configured. Default is clearly labelled preview-only trial with no attendance writes. External e-office HTTPS API remains configured separately.
**Tech Stack:** Python 3.11, Flask, OpenCV, existing vanilla JavaScript, Docker Compose.
**Spec:** docs/superpowers/specs/2026-10-18-browser-camera-docker-design.md

## Global Constraints
- No attendance writes in default preview trial; reuse e-office validation, models, passive liveness, clock and durable queue in configured integration.
- Loopback publish only, no raw frame persistence, single owner, CSRF and origin checks, bounded frame/session lifetime and single-use freshness challenge.
- Preserve native camera behavior by default outside Compose. No new runtime dependency unless existing libraries cannot do the work.
- Explicitly document remaining native/hardware/model acceptance and browser transport timing limitations.

## Review Focus
- Permission denied or stopped tracks release UI/runtime ownership and permit retry.
- Multiple tabs cannot mix identity votes or own one runtime simultaneously.
- Slow/stale/replayed frames cannot cause delayed attendance or unbounded buffers.
- Missing models/keys/token starts preview successfully, integration fails closed.
- Image build excludes enrollment, secrets, caches, datasets and instance queues.

### Task 1: Browser transport and runtime
**Files:** routes/kiosk.py, kiosk/browser.py (new), config.py, tests/test_browser_camera.py (new), worker integration only if necessary.
**Interfaces:** API under /kiosk/camera: POST start, POST challenge, POST frame (JPEG), POST stop. Use session cookie+CSRF; start returns owner token, challenge returns one-use nonce and POST frame supplies nonce and owner headers. Expose template boolean browser_camera_enabled. Public /kiosk/status stays compatible. Default CAMERA_SOURCE=native; browser source explicitly enabled. Browser preview mode ATTENDANCE_MODE=standalone never invokes standalone attendance pipeline. Runtime may choose focused threads/queue to ensure worker/SQLite ownership and bounded shutdown.
- [ ] Write tests and observe RED: disabled mode, CSRF/cross-origin, owner contention, JPEG limits/malformed/stale/replay, disconnect expiry, no attendance preview, e-office acquisition timestamp and worker reuse.
- [ ] Implement bounded runtime/session transport with shared worker validation; isolate thread-owned stores, no browser date authority.
- [ ] Run tests plus existing kiosk/integration tests; commit and report exact commands/results.

### Task 2: Browser controls
**Files:** templates/kiosk.html, static/js/browser-camera.js (new), static/css/kiosk.css, tests/browser-camera.test.js (new), related template tests.
**Interfaces:** Consume Task 1 endpoints and owner/challenge contracts; page provides CSRF token. Controls rendered only when browser source enabled. Keep existing status poll independent.
- [ ] Write Node tests using platform test runner for start/stop, denied permission, one-inflight and resource cleanup; observe RED.
- [ ] Add accessible activate/stop/preview and safe Indonesian trial/error text. Capture JPEG <=1280x720, one inflight; get fresh challenge BEFORE grabbing image and do not retry old images. Stop on hidden/disconnected stream/failure. Never show attendance success from transport acknowledgement.
- [ ] Run Node and template tests; commit and report.

### Task 3: Compose packaging and operational smoke
**Files:** Dockerfile, compose.yaml, .dockerignore, docker/entrypoint.sh, docker/compose.env.example, docs/DOCKER.md, README.md, tests/test_docker_packaging.py.
**Interfaces:** Build existing pinned Python requirements incl integration; no hardware mapping. Default preview uses SQLite local app metadata, persistent instance volume and generated-once SECRET_KEY (never regenerate on restart); no real data/auto enrollment. Explicit optional env config enables eoffice with read-only externally supplied model/key mounts. Env example no real secret. Start web through Waitress one process; runtime single-owner; proper SIGTERM cleanup. Host port localhost:5000, optional host port override. Keep API external and document exact one-time setup versus up.
- [ ] Write packaging checks/self-check and observe RED.
- [ ] Implement smallest Compose, image, startup and docs. Validate dependencies on actual build architecture; report unavailable image dependencies honestly, do not silently loosen pinned native requirements.
- [ ] Run docker compose config, actual image build, up, HTTP kiosk/health and frame smoke with synthetic JPEG in preview, restart persistence, down without deleting volumes; verify no secrets in image context. Run targeted regressions, commit and report.
