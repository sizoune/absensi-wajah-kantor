const test = require('node:test');
const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const vm = require('node:vm');

const flush = () => new Promise(resolve => setImmediate(resolve));
function element(extra = {}) {
  const events = new Map();
  return Object.assign({hidden: false, disabled: false, textContent: '', dataset: {},
    addEventListener(name, handler) { events.set(name, handler); },
    fire(name) { return events.get(name)?.({preventDefault() {}}); },
  }, extra);
}

test('native preview saves the selected camera and displays checked JPEG without attendance requests', async () => {
  const elements = new Map();
  for (const id of ['setup-native-camera', 'native-camera-select', 'native-camera-check',
    'native-camera-status', 'native-camera-preview']) elements.set(id, element());
  elements.get('setup-native-camera').dataset = {csrfToken: 'csrf', checkUrl: '/setup/camera/check'};
  elements.get('native-camera-select').action = '/setup/camera/select';
  const calls = [];
  let available = true;
  vm.runInNewContext(readFileSync('static/js/setup-camera.js', 'utf8'), {
    document: {getElementById: id => elements.get(id)},
    FormData: class FormData {},
    fetch: async (url, request) => {
      calls.push({url, request});
      return {ok: available, json: async () => available ? {
        ok: true, preview: 'data:image/jpeg;base64,checked', lighting: 'Pencahayaan cukup.'
      } : {error: 'busy', message: 'Kamera sedang digunakan layanan lain.'}};
    },
  });
  await elements.get('native-camera-select').fire('submit');
  assert.match(elements.get('native-camera-status').textContent, /pilihan tersimpan/i);
  await elements.get('native-camera-check').fire('click');
  assert.deepEqual(calls.map(call => call.url), ['/setup/camera/select', '/setup/camera/check']);
  assert.ok(calls.every(call => call.request.headers['X-CSRFToken'] === 'csrf'));
  assert.equal(elements.get('native-camera-preview').src, 'data:image/jpeg;base64,checked');
  assert.equal(elements.get('native-camera-preview').hidden, false);
  assert.match(elements.get('native-camera-status').textContent, /tidak mencatat absensi/i);
  available = false;
  await elements.get('native-camera-check').fire('click');
  assert.equal(elements.get('native-camera-preview').hidden, true);
  assert.match(elements.get('native-camera-status').textContent, /sedang digunakan/i);
  assert.equal(elements.get('native-camera-check').disabled, false);
});

test('readiness refresh reconciles completed models and never presents an unanchored runtime as ready', async () => {
  const reasons = ['models', 'camera', 'clock'].map(reason => element({dataset: {readinessReason: reason}}));
  const pending = ['models', 'camera'].map(step => element({dataset: {setupStep: step}}));
  const root = element({dataset: {statusUrl: '/setup/readiness'}, querySelectorAll: () => reasons});
  const title = element(), finish = element({disabled: true}), timers = [];
  let status = {complete: false, ready: false, can_finish: true, blocked: ['camera', 'clock'], pending: ['camera']};
  vm.runInNewContext(readFileSync('static/js/setup-readiness.js', 'utf8'), {
    document: {getElementById: id => id === 'setup-readiness' ? root : id === 'readiness-title' ? title : finish,
      querySelectorAll: () => pending},
    fetch: async () => ({ok: true, json: async () => status}),
    setTimeout: callback => timers.push(callback),
  });
  await flush();
  assert.equal(title.textContent, 'Setup belum selesai');
  assert.equal(reasons[0].hidden, true);
  assert.equal(pending[0].hidden, true);
  assert.equal(finish.disabled, false);
  status = {complete: true, ready: false, can_finish: true, blocked: ['clock'], pending: []};
  timers.shift()(); await flush();
  assert.equal(title.textContent, 'Setup selesai, belum siap absensi');
  assert.equal(reasons[2].hidden, false);
  status = {...status, ready: true, blocked: []};
  timers.shift()(); await flush();
  assert.equal(title.textContent, 'Siap absensi');
  assert.ok(reasons.every(item => item.hidden));
});

test('readiness retries a failed HTTP poll and recovers on the next response', async () => {
  const timers = [], title = element();
  const root = element({dataset: {statusUrl: '/setup/readiness'}, querySelectorAll: () => []});
  let requests = 0;
  vm.runInNewContext(readFileSync('static/js/setup-readiness.js', 'utf8'), {
    document: {getElementById: id => id === 'setup-readiness' ? root : id === 'readiness-title' ? title : null,
      querySelectorAll: () => []},
    fetch: async () => ({ok: ++requests > 1,
      json: async () => ({complete: true, ready: true, blocked: [], pending: []})}),
    setTimeout: callback => timers.push(callback),
  });
  await flush();
  assert.equal(timers.length, 1);
  timers.shift()(); await flush();
  assert.equal(requests, 2);
  assert.equal(title.textContent, 'Siap absensi');
});
