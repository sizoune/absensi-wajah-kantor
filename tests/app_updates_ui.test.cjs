const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

async function main() {
  const elements = new Map();
  for (const id of ['app-updates', 'update-message', 'update-check', 'update-install',
    'update-progress', 'update-version', 'update-notes', 'installed-version']) {
    elements.set(id, {dataset: {statusUrl: '/setup/updates/status'}, textContent: '', disabled: false});
  }
  const forms = [{action: '/setup/updates/install', addEventListener(event, listener) { this.submit = listener; }}];
  const timers = [];
  let response = {ok: true, redirected: false, status: 200, json: async () => ({
    version: '1.1.0', stage: 'available', message: 'Ready', busy: false,
    notes: '', size: 100, received: 0, offered_version: '1.2.0', recovery_required: false})};
  vm.runInNewContext(fs.readFileSync('static/js/app_updates.js', 'utf8'), {
    document: {getElementById: id => elements.get(id), querySelectorAll: () => forms},
    fetch: async () => response,
    FormData: class {}, setTimeout: callback => timers.push(callback),
  });
  await new Promise(setImmediate);
  assert.equal(elements.get('installed-version').textContent, '1.1.0');
  assert.equal(elements.get('update-install').disabled, false);
  response = {ok: true, redirected: true, status: 200};
  await forms[0].submit({preventDefault() {}});
  assert.equal(elements.get('update-install').disabled, true);
  assert.match(elements.get('update-message').textContent, /Muat ulang/);
  response = {ok: true, redirected: false, status: 200, json: async () => ({
    version: '1.1.0', stage: 'available', message: 'Ready', busy: false,
    recovery_required: false, notes: ''})};
  await timers[0]();
  assert.equal(elements.get('update-install').disabled, true);
  assert.match(elements.get('update-message').textContent, /Muat ulang/);
}
main().catch(error => { console.error(error); process.exitCode = 1; });
