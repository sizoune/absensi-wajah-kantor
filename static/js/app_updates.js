(() => {
  const panel = document.getElementById('app-updates');
  const message = document.getElementById('update-message');
  const check = document.getElementById('update-check');
  const install = document.getElementById('update-install');
  const progress = document.getElementById('update-progress');
  let reloadRequired = false;
  function requireReload() {
    reloadRequired = true;
    message.textContent = 'Sesi atau token admin berakhir. Muat ulang halaman dan masuk kembali.';
    check.disabled = install.disabled = true;
  }
  function display(status) {
    if (reloadRequired) return;
    document.getElementById('installed-version').textContent = status.version;
    message.textContent = status.message;
    document.getElementById('update-version').textContent = status.offered_version ? `Versi tersedia: ${status.offered_version}` : '';
    document.getElementById('update-notes').textContent = status.notes;
    check.disabled = status.busy || status.recovery_required;
    install.disabled = status.busy || status.recovery_required || status.stage !== 'available';
    progress.hidden = status.stage !== 'downloading';
    progress.max = status.size || 1;
    progress.value = status.received;
  }
  async function poll() {
    if (reloadRequired) return;
    try {
      const response = await fetch(panel.dataset.statusUrl, {cache: 'no-store'});
      if (response.redirected || response.status === 401 || response.status === 403) {
        requireReload();
        return;
      }
      if (!response.ok) throw new Error('status');
      display(await response.json());
    } catch {
      message.textContent = 'Menunggu aplikasi kembali tersedia. Halaman akan mencoba lagi.';
      check.disabled = install.disabled = true;
    }
    setTimeout(poll, 2000);
  }
  document.querySelectorAll('.update-form').forEach(form => form.addEventListener('submit', async event => {
    event.preventDefault();
    check.disabled = install.disabled = true;
    try {
      const response = await fetch(form.action, {method: 'POST', body: new FormData(form)});
      if (response.redirected || response.status === 401 || response.status === 403) {
        requireReload();
        return;
      }
      if (!response.ok && response.status !== 409) throw new Error('request');
      const status = await response.json();
      if (response.status === 409) message.textContent = status.message;
      else display(status);
    } catch {
      message.textContent = 'Permintaan belum berhasil. Periksa koneksi dan sesi admin.';
    }
  }));
  poll();
})();
