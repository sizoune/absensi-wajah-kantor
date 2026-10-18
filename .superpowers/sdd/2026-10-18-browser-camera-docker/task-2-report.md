# Task 2 implementation report

Implemented browser-only camera controls in the existing kiosk template. `CAMERA_SOURCE=browser` renders accessible Aktifkan kamera/Matikan kamera buttons, an initially hidden muted inline local video, CSRF token and same-origin endpoint URLs. Native pages retain the original status-only markup and script. Preview carries the persistent label “Uji pratinjau kamera — tidak mencatat absensi” and hides the attendance-result panel, including before activation; e-office retains the existing result panel with a processing label. The existing kiosk.js status poll is unchanged and independent of capture.

The new script requests video only from the activation click, with audio disabled. A fresh server challenge completes before every canvas draw. Frames preserve aspect ratio within 1280×720, encode as JPEG at quality 0.75, reject bodies larger than 512 KiB, and upload raw bytes without identity/date metadata. There is one sequential challenge/encode/upload pipeline, followed by a 200 ms delay, so capture never exceeds 5 fps. Canvas pixels are cleared after encoding and on stop; no browser storage, raw files, frame retry or backlog is used. Frame acknowledgement changes no attendance UI.

Stop, pagehide, hidden visibility, offline, ended tracks, permission errors and transport/encode failures release tracks, clear the preview and attempt owner release. Camera fetches have 5-second AbortController timeouts, stop has 2 seconds, and encoding has a 2-second deadline. A late getUserMedia result is stopped without affecting a newer session. A pending start response stays bounded and is allowed to return its owner token so a cancelled start can release that owner; activation stays disabled through the pending start/release transition. A lost start response cannot reveal its owner to the browser; the server's existing idle expiry remains the fallback. Permission denial, missing device, device/server busy, unavailable browser API, stale session and network errors have Indonesian retry instructions.

## Verification

Commands ran from `/Users/muhammadwildaniskandar/Projects/E-office/.worktrees/eoffice-camera/camera`.

- RED: `node --test tests/browser-camera.test.js && .venv/bin/python3.11 -m pytest tests/test_browser_camera_template.py -q` — Node **18 failed** as expected with missing static/js/browser-camera.js; the second command did not run because Node failed.
- RED: `.venv/bin/python3.11 -m pytest tests/test_browser_camera_template.py -q` — **2 failed, 1 passed in 0.19 seconds**, missing browser controls/processing label; native baseline passed.
- Initial GREEN: `node --test tests/browser-camera.test.js tests/kiosk_ui.test.cjs` — **19 passed**, including independent existing status-poll test.
- GREEN: `.venv/bin/python3.11 -m pytest tests/test_browser_camera_template.py tests/test_browser_camera.py tests/test_kiosk.py -q` — **55 passed in 4.35 seconds**.
- Final GREEN after extra cancellation/timeout/freshness/dimension checks: `node --test tests/browser-camera.test.js tests/kiosk_ui.test.cjs` — **24 passed**, zero skipped/cancelled. This covers denied permission, missing/busy device, busy server, network failure, missing API, stop during permission/start/challenge/encoding, late permission resolution versus a new active stream, one pipeline/backpressure and challenge-before-draw ordering, fetch timeout, stale response, portrait bounds, oversized JPEG, owner/track cleanup on lifecycle events and no attendance inference from transport.
- `git diff --check` — passed.

Changed only templates/kiosk.html, static/css/kiosk.css, new static/js/browser-camera.js, new tests/browser-camera.test.js, new tests/test_browser_camera_template.py and this report. No runtime dependency, backend endpoint/config, native camera code, thresholds, model or queue/key handling changed.

Not checked here: actual Mac camera permission/device behavior, actual browser JPEG codec output, Docker transport, proprietary models or physical/spoof acceptance; Task 3 owns container/browser smoke. Full Python suite was not repeated because focused template/transport/native checks cover this frontend change. Browser timestamps remain an estimate under the existing server challenge policy, and disconnected/unacknowledged owners may require the existing 15-second idle expiry.
