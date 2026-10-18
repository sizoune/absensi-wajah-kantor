const {test} = require('node:test');
const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const vm = require('node:vm');

const flush = () => new Promise(resolve => setImmediate(resolve));
function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return {promise, resolve, reject};
}
function target(extra = {}) {
  const listeners = new Map();
  return Object.assign({
    addEventListener(name, callback) { listeners.set(name, callback); },
    fire(name) { return listeners.get(name)?.(); },
  }, extra);
}
function setup(options = {}) {
  const calls = [], order = [], timers = new Map();
  let nextTimer = 0, permissionCalls = 0;
  const track = target({stop() { this.stopped = true; }});
  const stream = target({getTracks: () => [track]});
  const elements = new Map();
  const element = id => {
    if (!elements.has(id)) elements.set(id, target({hidden: false, disabled: false, textContent: '', dataset: {}}));
    return elements.get(id);
  };
  const root = element('browser-camera');
  root.dataset = {csrfToken: 'csrf', startUrl: '/start', stopUrl: '/stop', challengeUrl: '/challenge', frameUrl: '/frame'};
  const video = element('browser-camera-video');
  Object.assign(video, {videoWidth: 1920, videoHeight: 1080, readyState: 4,
    play: async () => {}, pause() { this.paused = true; }});
  const canvases = [];
  const document = target({hidden: false, getElementById: element, createElement(name) {
    assert.equal(name, 'canvas');
    const canvas = {width: 0, height: 0, getContext: () => ({drawImage(...args) {
      order.push('draw'); canvas.draw = args;
    }}), toBlob(callback, type, quality) {
      canvas.encoding = {type, quality};
      order.push('encode');
      if (options.encode) options.encode(callback, canvas);
      else callback(new Blob(['jpeg'], {type: 'image/jpeg'}));
    }};
    canvases.push(canvas);
    return canvas;
  }});
  const window = target();
  async function fetch(url, request) {
    calls.push({url, request}); order.push(url);
    if (options.fetch) {
      const custom = await options.fetch(url, request);
      if (custom) return custom;
    }
    return response(url === '/start' ? {owner: 'owner', mode: options.mode || 'preview'}
      : url === '/challenge' ? {challenge: 'nonce'} : {});
  }
  const context = {
    document, window, fetch, Blob, AbortController,
    navigator: {mediaDevices: options.unsupported ? undefined : {getUserMedia(constraints) {
      permissionCalls++; order.push('permission');
      assert.equal(constraints.audio, false);
      assert.equal(constraints.video.frameRate.max, 5);
      return options.permission ? options.permission(constraints) : Promise.resolve(stream);
    }}},
    setTimeout(callback, delay) { const id = ++nextTimer; timers.set(id, {callback, delay}); return id; },
    clearTimeout(id) { timers.delete(id); },
  };
  vm.runInNewContext(readFileSync('static/js/browser-camera.js', 'utf8'), context);
  return {calls, order, track, stream, video, document, window, canvases, timers, element,
    permissionCalls: () => permissionCalls,
    start: () => element('browser-camera-start').fire('click'),
    stop: () => element('browser-camera-stop').fire('click'),
    status: () => element('browser-camera-status').textContent,
    async tick() {
      const item = [...timers].find(([, timer]) => timer.delay === 200);
      assert.ok(item, 'next capture waits at least 200 ms');
      timers.delete(item[0]); item[1].callback(); await flush();
    },
  };
}
function response(data = {}, status = 200) {
  return {ok: status < 400, status, json: async () => ({ok: status < 400, ...data})};
}

test('requests camera only after activation and posts bounded JPEG after a fresh challenge', async () => {
  const ui = setup();
  assert.equal(ui.permissionCalls(), 0);
  assert.equal(ui.calls.length, 0);
  await ui.start(); await flush();
  assert.equal(ui.permissionCalls(), 1);
  assert.deepEqual(ui.order, ['permission', '/start', '/challenge', 'draw', 'encode', '/frame']);
  const frame = ui.calls.find(call => call.url === '/frame');
  assert.equal(frame.request.body.type, 'image/jpeg');
  assert.equal(frame.request.headers['X-Camera-Owner'], 'owner');
  assert.equal(frame.request.headers['X-Camera-Challenge'], 'nonce');
  assert.equal(frame.request.headers['Content-Type'], 'image/jpeg');
  assert.equal(frame.request.credentials, 'same-origin');
  assert.equal(frame.request.mode, 'same-origin');
  assert.equal(frame.request.headers['X-CSRFToken'], 'csrf');
  assert.deepEqual(Object.keys(frame.request.headers).sort(), ['Content-Type', 'X-CSRFToken', 'X-Camera-Challenge', 'X-Camera-Owner']);
  assert.ok(frame.request.signal instanceof AbortSignal);
  assert.deepEqual(ui.canvases[0].draw.slice(1), [0, 0, 1280, 720]);
  assert.equal(ui.canvases[0].width, 0, 'discard pixels after encoding');
  assert.equal(ui.video.srcObject, ui.stream);
  assert.match(ui.status(), /tidak mencatat absensi/i);
  assert.equal(ui.element('browser-camera-start').disabled, true);
  ui.stop(); await flush();
  assert.equal(ui.track.stopped, true);
  assert.equal(ui.video.srcObject, null);
  assert.equal(ui.calls.filter(call => call.url === '/stop').length, 1);
  assert.equal(ui.element('browser-camera-start').disabled, false);
  assert.equal(ui.element('browser-camera-stop').disabled, true);
});

for (const [name, text] of [['NotAllowedError', /izin kamera ditolak/i], ['NotFoundError', /kamera tidak ditemukan/i],
  ['NotReadableError', /sedang digunakan/i]]) {
  test(name + ' permits retry without retaining camera ownership', async () => {
    const ui = setup({permission: async () => { throw Object.assign(new Error(), {name}); }});
    await ui.start(); await flush();
    assert.match(ui.status(), text);
    assert.equal(ui.calls.length, 0);
    assert.equal(ui.element('browser-camera-start').disabled, false);
  });
}

test('missing browser API explains the requirement without prompting', async () => {
  const ui = setup({unsupported: true});
  await ui.start(); await flush();
  assert.match(ui.status(), /browser.*localhost|localhost.*browser/i);
  assert.equal(ui.calls.length, 0);
  assert.equal(ui.element('browser-camera-start').disabled, false);
});

test('a busy server releases local tracks and allows another activation', async () => {
  const ui = setup({fetch: async url => url === '/start' ? response({error: 'busy'}, 409) : undefined});
  await ui.start(); await flush();
  assert.match(ui.status(), /sedang digunakan/i);
  assert.equal(ui.track.stopped, true);
  assert.equal(ui.element('browser-camera-start').disabled, false);
});

test('network failure stops capture, sends owner release and never retries old image', async () => {
  const ui = setup({fetch: async url => { if (url === '/frame') throw new TypeError('offline'); }});
  await ui.start(); await flush();
  assert.equal(ui.track.stopped, true);
  assert.match(ui.status(), /koneksi.*server/i);
  assert.equal(ui.calls.filter(call => call.url === '/frame').length, 1);
  assert.equal(ui.calls.filter(call => call.url === '/stop').length, 1);
  assert.equal(ui.timers.size, 0);
  assert.equal(ui.element('browser-camera-start').disabled, false);
});

test('stop while permission is pending discards the late stream and permits retry', async () => {
  const pending = deferred();
  const ui = setup({permission: () => pending.promise});
  const starting = ui.start();
  ui.stop();
  assert.equal(ui.element('browser-camera-start').disabled, false);
  pending.resolve(ui.stream);
  await starting; await flush();
  assert.equal(ui.track.stopped, true);
  assert.equal(ui.video.srcObject, null);
  assert.equal(ui.calls.length, 0);
});

test('stop during start response releases a late owner without capturing', async () => {
  const pending = deferred();
  const ui = setup({fetch: url => url === '/start' ? pending.promise : undefined});
  const starting = ui.start(); await flush();
  ui.stop();
  assert.equal(ui.track.stopped, true);
  assert.equal(ui.element('browser-camera-start').disabled, true);
  pending.resolve(response({owner: 'late-owner', mode: 'preview'}));
  await starting; await flush();
  assert.deepEqual(ui.calls.map(call => call.url), ['/start', '/stop']);
  assert.equal(ui.calls[1].request.headers['X-Camera-Owner'], 'late-owner');
  assert.equal(ui.element('browser-camera-start').disabled, false);
});

test('a cancelled permission response cannot replace a newer active stream', async () => {
  const pending = deferred();
  let attempts = 0;
  const ui = setup({permission: () => ++attempts === 1 ? pending.promise : Promise.resolve(ui.stream)});
  const earlier = ui.start();
  ui.stop();
  await ui.start(); await flush();
  const oldTrack = {stop() { this.stopped = true; }};
  pending.resolve({getTracks: () => [oldTrack]});
  await earlier; await flush();
  assert.equal(oldTrack.stopped, true);
  assert.equal(ui.video.srcObject, ui.stream);
  assert.equal(ui.track.stopped, undefined);
  assert.equal(ui.calls.filter(call => call.url === '/start').length, 1);
  ui.stop(); await flush();
});

test('stopping a pending challenge aborts it without drawing or uploading', async () => {
  const ui = setup({fetch: (url, request) => url === '/challenge' ? new Promise((resolve, reject) => {
    request.signal.addEventListener('abort', () => reject(Object.assign(new Error(), {name: 'AbortError'})));
  }) : undefined});
  await ui.start(); await flush();
  ui.stop(); await flush();
  assert.equal(ui.calls.find(call => call.url === '/challenge').request.signal.aborted, true);
  assert.equal(ui.order.includes('draw'), false);
  assert.equal(ui.calls.filter(call => call.url === '/stop').length, 1);
  assert.equal(ui.timers.size, 0);
});

test('one request pipeline waits for processing before taking another fresh image', async () => {
  const pending = deferred();
  let frames = 0;
  const ui = setup({fetch: url => url === '/frame' && ++frames === 1 ? pending.promise : undefined});
  await ui.start(); await flush();
  assert.equal(ui.calls.filter(call => call.url === '/challenge').length, 1);
  assert.equal([...ui.timers.values()].some(timer => timer.delay === 200), false);
  pending.resolve(response()); await flush();
  await ui.tick();
  assert.equal(ui.calls.filter(call => call.url === '/challenge').length, 2);
  assert.equal(ui.calls.filter(call => call.url === '/frame').length, 2);
  assert.deepEqual(ui.order.slice(-4), ['/challenge', 'draw', 'encode', '/frame']);
  ui.stop(); await flush();
});

test('a late JPEG encoder callback after stop never uploads a frame', async () => {
  let encode;
  const ui = setup({encode: callback => { encode = callback; }});
  await ui.start(); await flush();
  ui.stop();
  encode(new Blob(['late'], {type: 'image/jpeg'})); await flush();
  assert.equal(ui.calls.some(call => call.url === '/frame'), false);
  assert.equal(ui.canvases[0].width, 0);
});

for (const event of ['pagehide', 'offline', 'visibilitychange', 'ended']) {
  test(event + ' stops tracks and releases the owner', async () => {
    const ui = setup(); await ui.start(); await flush();
    if (event === 'visibilitychange') { ui.document.hidden = true; ui.document.fire(event); }
    else if (event === 'ended') ui.track.fire(event);
    else ui.window.fire(event);
    await flush();
    assert.equal(ui.track.stopped, true);
    assert.equal(ui.video.srcObject, null);
    assert.equal(ui.calls.filter(call => call.url === '/stop').length, 1);
    if (event === 'pagehide' || event === 'visibilitychange') assert.equal(ui.calls.at(-1).request.keepalive, true);
    assert.equal(ui.timers.size, 0);
  });
}

test('a fetch timeout aborts processing and releases the stream', async () => {
  const ui = setup({fetch: (url, request) => url === '/frame' ? new Promise((resolve, reject) => {
    request.signal.addEventListener('abort', () => reject(Object.assign(new Error('timeout'), {name: 'AbortError'})));
  }) : undefined});
  await ui.start(); await flush();
  const [id, timer] = [...ui.timers].find(([, item]) => item.delay === 5000);
  ui.timers.delete(id); timer.callback(); await flush();
  assert.equal(ui.track.stopped, true);
  assert.equal(ui.calls.find(call => call.url === '/frame').request.signal.aborted, true);
  assert.equal(ui.calls.filter(call => call.url === '/stop').length, 1);
  assert.equal(ui.element('browser-camera-start').disabled, false);
});

test('a pending start response has a bounded timeout and frees local tracks', async () => {
  const ui = setup({fetch: (url, request) => url === '/start' ? new Promise((resolve, reject) => {
    request.signal.addEventListener('abort', () => reject(Object.assign(new Error(), {name: 'AbortError'})));
  }) : undefined});
  const starting = ui.start(); await flush();
  ui.stop();
  const [id, timer] = [...ui.timers].find(([, item]) => item.delay === 5000);
  ui.timers.delete(id); timer.callback();
  await starting; await flush();
  assert.equal(ui.track.stopped, true);
  assert.equal(ui.element('browser-camera-start').disabled, false);
  assert.equal(ui.calls.some(call => call.url === '/frame'), false);
});

test('stale server response ends this capture session instead of retrying', async () => {
  const ui = setup({fetch: url => url === '/frame' ? response({error: 'stale'}, 410) : undefined});
  await ui.start(); await flush();
  assert.equal(ui.track.stopped, true);
  assert.match(ui.status(), /terlalu lambat/i);
  assert.equal(ui.calls.filter(call => call.url === '/frame').length, 1);
  assert.equal(ui.timers.size, 0);
});

test('portrait camera dimensions stay within both frame limits', async () => {
  const ui = setup();
  ui.video.videoWidth = 1080;
  ui.video.videoHeight = 1440;
  await ui.start(); await flush();
  assert.deepEqual(ui.canvases[0].draw.slice(1), [0, 0, 540, 720]);
  ui.stop(); await flush();
});

test('JPEG larger than 512 KiB stops before transport instead of retaining or retrying', async () => {
  const ui = setup({encode: callback => callback(new Blob([new Uint8Array(512 * 1024 + 1)], {type: 'image/jpeg'}))});
  await ui.start(); await flush();
  assert.match(ui.status(), /gambar.*besar/i);
  assert.equal(ui.track.stopped, true);
  assert.equal(ui.calls.some(call => call.url === '/frame'), false);
});

test('frame acknowledgement is processing only and never an attendance result', async () => {
  const ui = setup({mode: 'eoffice'});
  ui.element('message').textContent = 'Server result';
  await ui.start(); await flush();
  assert.match(ui.status(), /diproses/i);
  assert.doesNotMatch(ui.status(), /berhasil|tercatat|selamat/i);
  assert.equal(ui.element('message').textContent, 'Server result');
  ui.stop(); await flush();
});

test('test mode transport acknowledgement never displays a saved or official result', async () => {
  const ui = setup({mode: 'camera_test'});
  ui.element('message').textContent = 'Menunggu sinkronisasi';
  await ui.start(); await flush();
  assert.match(ui.status(), /uji pengenalan/i);
  assert.match(ui.status(), /tidak mencatat absensi resmi/i);
  assert.doesNotMatch(ui.status(), /tersimpan|berhasil|tercatat/i);
  assert.equal(ui.element('message').textContent, 'Menunggu sinkronisasi');
  ui.stop(); await flush();
});
