const {test} = require('node:test');
const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const vm = require('node:vm');

test('persistent integration health survives person expiry and clears on lost service', async () => {
  const elements = new Map();
  const element = id => {
    if (!elements.has(id)) elements.set(id, {textContent: '', hidden: true, dataset: {}});
    return elements.get(id);
  };
  let data = {online: true, camera: 'ok', time: '08:01', date: '15 Oktober',
    message: {id: 'event', kind: 'info', text: 'Synthetic: Menunggu sinkronisasi'},
    integration: {text: 'E-office terputus · Penyimpanan siap', action: 'Periksa koneksi jaringan.'}};
  let nextPoll;
  const context = {
    document: {querySelector: () => ({dataset: {statusUrl: '/status'}}), getElementById: element},
    fetch: async () => ({json: async () => data}),
    setTimeout: callback => { nextPoll = callback; },
  };
  vm.runInNewContext(readFileSync('static/js/kiosk.js', 'utf8'), context);
  const flush = () => new Promise(resolve => setImmediate(resolve));
  await flush();
  assert.equal(element('integration-health').hidden, false);
  assert.equal(element('integration-health').textContent, 'E-office terputus · Penyimpanan siap. Periksa koneksi jaringan.');
  assert.equal(element('camera-text').textContent, 'Kamera aktif');
  data = {...data, message: null};
  nextPoll(); await flush();
  assert.equal(element('message').textContent, 'Silakan berjalan di depan kamera.');
  assert.equal(element('integration-health').hidden, false);
  data = {online: false};
  nextPoll(); await flush();
  assert.equal(element('integration-health').hidden, true);
  data = {...data, online: true, camera: 'ok', integration: {text: 'E-office terhubung', action: ''}};
  nextPoll(); await flush();
  assert.equal(element('integration-health').hidden, false);
  context.fetch = async () => { throw new Error('offline'); };
  nextPoll(); await flush();
  assert.equal(element('integration-health').hidden, true);
});
