(function () {
  "use strict";

  var root = document.getElementById("browser-camera");
  if (!root) return;
  var video = document.getElementById("browser-camera-video");
  var activate = document.getElementById("browser-camera-start");
  var deactivate = document.getElementById("browser-camera-stop");
  var status = document.getElementById("browser-camera-status");
  var device = document.getElementById("browser-camera-device");
  var recover = document.getElementById("browser-camera-admin-stop");
  var preferredDevice = "";
  try { preferredDevice = localStorage.getItem("eoffice-camera-device") || ""; } catch (_) {}
  var session = null;
  var pendingStart = null;
  var stopping = null;
  var recovering = false;

  function controls() {
    activate.disabled = !!(session || pendingStart || stopping || recovering);
    deactivate.disabled = !session;
    if (recover) recover.disabled = recovering;
  }

  function errorText(error) {
    var messages = {
      NotAllowedError: "Izin kamera ditolak. Izinkan kamera di pengaturan browser, lalu aktifkan lagi.",
      NotFoundError: "Kamera tidak ditemukan. Hubungkan kamera, lalu aktifkan lagi.",
      NotReadableError: "Kamera sedang digunakan aplikasi lain. Tutup aplikasi tersebut, lalu aktifkan lagi.",
      OverconstrainedError: "Kamera yang dipilih tidak tersedia. Pilih kamera lain lalu aktifkan lagi.",
      busy: "Kamera sedang digunakan tab atau layanan lain. Matikan kamera di sana, lalu coba lagi.",
      unsupported: "Browser ini tidak menyediakan akses kamera. Gunakan browser yang mendukung kamera di localhost.",
      unavailable: "Layanan kamera belum siap. Hubungi admin untuk memeriksa konfigurasi e-office dan model.",
      not_ready: "Perangkat belum siap absensi. Admin dapat memeriksa model, enrollment, dan kamera melalui Kesiapan perangkat.",
      csrf: "Sesi halaman kedaluwarsa. Muat ulang halaman sebelum mengaktifkan kamera.",
      origin: "Buka halaman kamera melalui alamat localhost yang sama dengan server.",
      frame_too_large: "Gambar kamera terlalu besar. Turunkan resolusi kamera, lalu aktifkan lagi.",
      invalid_frame: "Gambar kamera tidak dapat dibaca. Aktifkan kamera untuk mencoba lagi.",
      stale: "Pengiriman gambar terlalu lambat. Periksa koneksi, lalu aktifkan kamera lagi.",
      expired: "Sesi kamera berakhir. Aktifkan kamera untuk memulai lagi.",
      admin_required: "Login sebagai admin lokal aktif untuk menghentikan sesi kamera lain."
    };
    return messages[error.code || error.name] || "Koneksi ke server kamera terputus. Periksa koneksi, lalu aktifkan kamera lagi.";
  }

  async function request(current, action, extraHeaders, body, keepalive) {
    var controller = new AbortController();
    var timer = setTimeout(function () { controller.abort(); }, action === "stop" ? 2000 : 5000);
    if (action !== "stop") current.request = controller;
    try {
      var headers = Object.assign({"X-CSRFToken": root.dataset.csrfToken}, extraHeaders);
      if (current.owner) headers["X-Camera-Owner"] = current.owner;
      // POST supplies the browser's Origin; mode prevents a cross-origin transport.
      var response = await fetch(root.dataset[action + "Url"], {
        method: "POST", credentials: "same-origin", mode: "same-origin", cache: "no-store",
        headers: headers, body: body, signal: controller.signal, keepalive: !!keepalive
      });
      var data = await response.json();
      if (!response.ok || !data.ok) throw Object.assign(new Error(), {code: data.error});
      return data;
    } finally {
      clearTimeout(timer);
      if (current.request === controller) current.request = null;
    }
  }

  function release(current, keepalive) {
    if (!current.owner) return;
    var closing = request(current, "stop", null, undefined, keepalive).catch(function () {
      // A disconnected server also expires the owner after its idle timeout.
    });
    current.owner = null;
    stopping = closing;
    controls();
    closing.finally(function () {
      if (stopping === closing) stopping = null;
      controls();
    });
  }

  function stop(text, keepalive) {
    var current = session;
    if (!current) return;
    session = null;
    clearTimeout(current.timer);
    // A pending start must return its token so it can still be released.
    if (current.request && pendingStart !== current) current.request.abort();
    if (current.stream) current.stream.getTracks().forEach(function (track) { track.stop(); });
    if (current.canvas) current.canvas.width = current.canvas.height = 0;
    video.pause();
    video.srcObject = null;
    video.hidden = true;
    status.textContent = text || "Kamera dimatikan. Aktifkan kamera untuk memulai lagi.";
    release(current, keepalive);
    controls();
  }

  function encode(current) {
    return new Promise(function (resolve, reject) {
      var timer = setTimeout(function () { reject(Object.assign(new Error(), {code: "invalid_frame"})); }, 2000);
      current.canvas.toBlob(function (blob) {
        clearTimeout(timer);
        current.canvas.width = current.canvas.height = 0;
        resolve(blob);
      }, "image/jpeg", 0.75);
    });
  }

  async function frame(current) {
    if (session !== current) return;
    try {
      var challenge = await request(current, "challenge");
      if (session !== current) return;
      if (video.readyState < 2 || !video.videoWidth || !video.videoHeight) {
        throw Object.assign(new Error(), {code: "invalid_frame"});
      }
      var scale = Math.min(1, 1280 / video.videoWidth, 720 / video.videoHeight);
      current.canvas.width = Math.max(1, Math.floor(video.videoWidth * scale));
      current.canvas.height = Math.max(1, Math.floor(video.videoHeight * scale));
      current.canvas.getContext("2d").drawImage(video, 0, 0, current.canvas.width, current.canvas.height);
      var blob = await encode(current);
      if (session !== current) return;
      if (!blob || blob.type !== "image/jpeg" || !blob.size) throw Object.assign(new Error(), {code: "invalid_frame"});
      if (blob.size > 512 * 1024) throw Object.assign(new Error(), {code: "frame_too_large"});
      await request(current, "frame", {"X-Camera-Challenge": challenge.challenge, "Content-Type": "image/jpeg"}, blob);
      if (session === current) current.timer = setTimeout(function () { frame(current); }, 200);
    } catch (error) {
      if (session === current) stop(errorText(error));
    }
  }

  async function start() {
    if (session || pendingStart || stopping || recovering || document.hidden) return;
    var current = {};
    session = current;
    controls();
    status.textContent = "Menunggu izin kamera dari browser…";
    try {
      if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
        throw Object.assign(new Error(), {code: "unsupported"});
      }
      var stream = await navigator.mediaDevices.getUserMedia({audio: false, video: {
        width: {ideal: 1280}, height: {ideal: 720}, frameRate: {ideal: 5, max: 5},
        ...(device && (device.value || preferredDevice) ? {deviceId: {exact: device.value || preferredDevice}} : {})
      }});
      if (session !== current) {
        stream.getTracks().forEach(function (track) { track.stop(); });
        return;
      }
      current.stream = stream;
      listDevices();
      stream.getTracks().forEach(function (track) {
        track.addEventListener("ended", function () {
          if (session === current) stop("Kamera terputus. Hubungkan kamera, lalu aktifkan lagi.");
        }, {once: true});
      });
      video.srcObject = stream;
      video.hidden = false;
      await video.play();
      if (session !== current) return;
      pendingStart = current;
      var data;
      try {
        data = await request(current, "start");
        current.owner = data.owner;
      } finally {
        if (pendingStart === current) pendingStart = null;
        controls();
      }
      if (session !== current) {
        release(current, true);
        return;
      }
      status.textContent = data.mode === "preview"
        ? "Pratinjau kamera aktif. Tidak mencatat absensi."
        : data.mode === "camera_test" ? "Uji pengenalan kamera aktif; tidak mencatat absensi resmi. Hasil uji ditampilkan dari server."
        : "Kamera aktif. Gambar diproses; hasil absensi ditampilkan dari server.";
      current.canvas = document.createElement("canvas");
      frame(current);
    } catch (error) {
      if (error.name === "OverconstrainedError") saveDevice("");
      if (session === current) stop(errorText(error));
    }
  }

  activate.addEventListener("click", start);
  if (recover && root.dataset.adminStopUrl) recover.addEventListener("click", async function () {
    if (recovering) return;
    recovering = true;
    stop();
    controls();
    status.textContent = "Menghentikan sesi kamera lama…";
    try {
      var result = await request({}, "adminStop");
      status.textContent = result.draining
        ? "Sesi lama dihentikan. Tunggu pemrosesan selesai, lalu aktifkan kamera di sini."
        : "Sesi lama dihentikan. Aktifkan kamera untuk memakai kamera di sini.";
    } catch (error) { status.textContent = errorText(error); }
    finally { recovering = false; controls(); }
  });
  async function listDevices() {
    if (!device || !navigator.mediaDevices || !navigator.mediaDevices.enumerateDevices) return;
    try {
      var cameras = (await navigator.mediaDevices.enumerateDevices()).filter(function (item) { return item.kind === "videoinput"; });
      var selected = device.value || preferredDevice;
      device.replaceChildren(new Option("Kamera bawaan browser", ""));
      cameras.forEach(function (item, index) {
        device.add(new Option(item.label || "Kamera " + (index + 1), item.deviceId));
      });
      if (selected && !cameras.some(function (item) { return item.deviceId === selected; })) {
        if (cameras.some(function (item) { return item.deviceId; })) saveDevice("");
        else device.add(new Option("Kamera tersimpan", selected));
      }
      device.value = preferredDevice;
    } catch (_) { /* The permission prompt remains the authoritative camera check. */ }
  }
  function saveDevice(value) {
    preferredDevice = value;
    if (device) device.value = value;
    try { localStorage.setItem("eoffice-camera-device", value); } catch (_) {}
  }
  if (device) device.addEventListener("change", function () {
    saveDevice(device.value);
    stop("Pilihan kamera berubah. Aktifkan kamera untuk memeriksanya.");
  });
  deactivate.addEventListener("click", function () { stop(); });
  window.addEventListener("pagehide", function () { stop(null, true); });
  window.addEventListener("offline", function () { stop(errorText({}), true); });
  document.addEventListener("visibilitychange", function () { if (document.hidden) stop(null, true); });
  controls();
  listDevices();
})();
