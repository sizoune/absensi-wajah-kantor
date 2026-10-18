(function () {
  "use strict";
  var root = document.getElementById("setup-models");
  if (!root) return;
  var form = root.querySelector("form");
  var button = form.querySelector("button");
  var rows = Array.from(root.querySelectorAll("[data-model-key]"));
  var summary = document.getElementById("setup-models-summary");
  var progress = document.getElementById("setup-models-progress");
  var error = document.getElementById("setup-models-error");
  var compatibility = document.getElementById("setup-models-compatibility");

  function render(status) {
    summary.textContent = status.completed + " dari " + status.total + " model terverifikasi.";
    progress.value = status.completed;
    compatibility.textContent = status.compatibility_reason;
    status.models.forEach(function (model) {
      var row = rows.find(function (item) { return item.dataset.modelKey === model.key; });
      if (!row) return;
      var text = model.state === "verified" ? "Terverifikasi" : model.reason + " " + model.recovery;
      if (model.state === "downloading") {
        text = "Mengunduh " + Math.floor(model.downloaded_bytes / model.size * 100) + "%. Model belum digunakan.";
      }
      row.querySelector("[data-model-state]").textContent = text;
    });
  }

  async function refresh() {
    var response = await fetch(root.dataset.statusUrl, {credentials: "same-origin", cache: "no-store",
      headers: {Accept: "application/json"}});
    if (!response.ok) throw new Error("status_unavailable");
    render(await response.json());
  }

  form.addEventListener("submit", async function (event) {
    event.preventDefault();
    if (button.disabled) return;
    button.disabled = true;
    error.textContent = "";
    var timer = setInterval(function () { return refresh().catch(function () {}); }, 1000);
    try {
      for (var row of rows) {
        var body = new FormData(form);
        body.set("model", row.dataset.modelKey);
        var response = await fetch(form.action, {method: "POST", body: body, credentials: "same-origin",
          headers: {Accept: "application/json"}});
        var status = await response.json();
        if (response.status === 409) {
          error.textContent = "Unduhan model sedang berjalan. Tunggu hasilnya, lalu coba lagi.";
          break;
        }
        if (!status.models) throw new Error("download_unavailable");
        render(status);
      }
    } catch (_) {
      error.textContent = "Koneksi atau sesi halaman terputus. Muat ulang halaman, lalu coba lagi. Model valid tetap tersimpan.";
    } finally {
      clearInterval(timer);
      try { await refresh(); } catch (_) {}
      button.disabled = false;
    }
  });
})();
