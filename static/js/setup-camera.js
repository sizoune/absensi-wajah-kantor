(function () {
  "use strict";
  var root = document.getElementById("setup-native-camera");
  if (!root) return;
  var form = document.getElementById("native-camera-select");
  var check = document.getElementById("native-camera-check");
  var status = document.getElementById("native-camera-status");
  var image = document.getElementById("native-camera-preview");
  async function send(url, body) {
    var response = await fetch(url, {method: "POST", body: body, credentials: "same-origin", mode: "same-origin",
      cache: "no-store", headers: {"X-CSRFToken": root.dataset.csrfToken}});
    var data = await response.json();
    if (!response.ok) throw new Error(data.message || "Sesi halaman berakhir. Muat ulang halaman lalu coba lagi.");
    return data;
  }
  form.addEventListener("submit", async function (event) {
    event.preventDefault();
    image.hidden = true;
    try {
      await send(form.action, new FormData(form));
      status.textContent = "Pilihan tersimpan. Periksa kamera yang dipilih.";
    } catch (error) { status.textContent = error.message; }
  });
  check.addEventListener("click", async function () {
    check.disabled = true;
    status.textContent = "Memeriksa kamera…";
    image.hidden = true;
    try {
      var data = await send(root.dataset.checkUrl);
      image.src = data.preview;
      image.hidden = false;
      status.textContent = "Kamera mengirim gambar. Tidak mencatat absensi. " + data.lighting;
    } catch (error) { status.textContent = error.message; }
    finally { check.disabled = false; }
  });
})();
