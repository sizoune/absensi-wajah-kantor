const test = require('node:test');
const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const vm = require('node:vm');

test('model preparation shows progress and keeps a failed model available for retry', async () => {
  const rows = ['yunet', 'mobilefacenet_v1_periocular'].map(key => ({dataset: {modelKey: key},
    state: {textContent: ''}, querySelector() {return this.state;}}));
  const summary = {}, progress = {}, error = {}, compatibility = {}, button = {disabled: false}, timers = new Map();
  let submit, finishFirst;
  const form = {action: '/setup/models', addEventListener(name, callback) {submit = callback;},
    querySelector() {return button;}};
  const root = {dataset: {statusUrl: '/setup/models'}, querySelector() {return form;},
    querySelectorAll() {return rows;}};
  const elements = {'setup-models': root, 'setup-models-summary': summary,
    'setup-models-progress': progress, 'setup-models-error': error, 'setup-models-compatibility': compatibility};
  const status = (completed, states) => ({completed, total: 2, compatibility_reason: 'Menunggu enrollment',
    models: states.map((state, index) => ({key: rows[index].dataset.modelKey, state,
      size: 100, downloaded_bytes: state === 'downloading' ? 50 : 100,
      reason: state === 'failed' ? 'Sumber belum tersedia.' : '',
      recovery: state === 'failed' ? 'Hubungi admin server.' : ''}))});
  let current = status(0, ['downloading', 'missing']);
  const requests = [];
  async function fetch(url, options) {
    assert.equal(options.credentials, 'same-origin');
    assert.equal(options.headers.Accept, 'application/json');
    if (options.method !== 'POST') return {ok: true, json: async () => current};
    requests.push(options.body);
    if (options.body.get('model') === 'yunet') {
      await new Promise(resolve => {finishFirst = resolve;});
      current = status(1, ['verified', 'missing']);
      return {ok: true, status: 200, json: async () => current};
    }
    current = status(1, ['verified', 'failed']);
    return {ok: false, status: 503, json: async () => current};
  }
  vm.runInNewContext(readFileSync('static/js/setup-models.js', 'utf8'), {
    document: {getElementById: id => elements[id]}, fetch,
    FormData: class extends Map {constructor() {super([['csrf_token', 'csrf']]);}},
    setInterval(callback) {timers.set(1, callback); return 1;},
    clearInterval(id) {timers.delete(id);},
  });
  const pending = submit({preventDefault() {}});
  assert.equal(button.disabled, true);
  await timers.get(1)();
  assert.match(rows[0].state.textContent, /50%/);
  finishFirst();
  await pending;
  assert.equal(button.disabled, false);
  assert.equal(timers.size, 0);
  assert.equal(progress.value, 1);
  assert.match(summary.textContent, /1 dari 2/);
  assert.equal(compatibility.textContent, 'Menunggu enrollment');
  assert.match(rows[0].state.textContent, /Terverifikasi/);
  assert.match(rows[1].state.textContent, /Hubungi admin server/);
  assert.deepEqual(requests.map(body => body.get('model')), ['yunet', 'mobilefacenet_v1_periocular']);
  assert.ok(requests.every(body => body.get('csrf_token') === 'csrf'));
});
