(function () {
  "use strict";
  var root = document.getElementById("setup-readiness");
  if (!root) return;
  async function refresh() {
    try {
      var response = await fetch(root.dataset.statusUrl, {credentials: "same-origin", cache: "no-store"});
      if (!response.ok) return;
      var status = await response.json();
      document.getElementById("readiness-title").textContent = !status.complete ? "Setup belum selesai"
        : status.ready ? "Siap absensi" : "Setup selesai, belum siap absensi";
      root.querySelectorAll("[data-readiness-reason]").forEach(function (item) {
        item.hidden = !status.blocked.includes(item.dataset.readinessReason);
      });
      document.querySelectorAll("[data-setup-step]").forEach(function (item) {
        item.hidden = !status.pending.includes(item.dataset.setupStep);
      });
      var finish = document.getElementById("setup-finish");
      if (finish) finish.disabled = !status.can_finish;
    } catch (_) { /* A failed status refresh never authorizes capture. */ }
    finally { setTimeout(refresh, 3000); }
  }
  refresh();
})();
