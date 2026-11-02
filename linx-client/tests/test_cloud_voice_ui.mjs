/* 验证手机语音点击、权限失败及断线后的收音状态；不调用真实麦克风或小车。 */
import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import { readFileSync } from 'node:fs';

const html = readFileSync(new URL('../linx_client/web/cloud-demo.html', import.meta.url), 'utf8');
const script = html.match(/<script>([\s\S]*?)<\/script>/)[1];

function setup({ getUserMedia } = {}) {
  class Element {
    constructor() { this.listeners = {}; this.dataset = {}; this.textContent = ''; this.value = ''; this.attrs = {}; }
    addEventListener(name, fn) { (this.listeners[name] ??= []).push(fn); }
    setAttribute(name, value) { this.attrs[name] = value; }
    async emit(name, event = {}) {
      if (name === 'click' && this.disabled) return;
      for (const fn of this.listeners[name] ?? []) await fn({ preventDefault() {}, ...event });
    }
  }
  const elements = Object.fromEntries([...html.matchAll(/id="([^"]+)"/g)].map(m => [`#${m[1]}`, new Element()]));
  const pad = ['forward', 'backward', 'left', 'right'].map(direction => {
    const element = new Element(); element.dataset.direction = direction; return element;
  });
  const barrels = ['up', 'down'].map(direction => {
    const element = elements[`#barrel-${direction}`]; element.dataset.barrel = direction; return element;
  });
  const document = Object.assign(new Element(), {
    hidden: false, querySelector: key => elements[key], querySelectorAll: key =>
      key === '[data-barrel]' ? barrels : key === '.pad button' ? [...pad, elements['#stop']] : pad,
  });
  const intervals = new Map();
  const timeouts = new Map();
  let time = 1000, nextTimer = 0, permissionRequests = 0;
  const track = Object.assign(new Element(), { stopped: 0, stop() { this.stopped++; } });
  const stream = { getTracks: () => [track], getAudioTracks: () => [track] };
  const contexts = [], worklets = [], sockets = [];
  const connectable = () => ({ connect(node) { return node; } });
  class AudioContext {
    constructor() {
      this.currentTime = 1; this.sampleRate = 48000; this.state = 'suspended';
      this.audioWorklet = { addModule: async () => {} }; contexts.push(this);
    }
    async resume() { this.state = 'running'; }
    async close() { this.state = 'closed'; }
    createMediaStreamSource() { return connectable(); }
    createBiquadFilter() { return { ...connectable(), frequency: {} }; }
    createGain() { return { ...connectable(), gain: {} }; }
  }
  class Worklet {
    constructor() { this.port = { sent: [], postMessage(value) { this.sent.push(value); } }; worklets.push(this); }
    connect(node) { return node; }
  }
  class Socket {
    static OPEN = 1; static CONNECTING = 0;
    constructor() { this.readyState = 1; this.bufferedAmount = 0; this.sent = []; sockets.push(this); }
    send(data) { this.sent.push(typeof data === 'string' ? JSON.parse(data) : data); }
  }
  const window = Object.assign(new Element(), { isSecureContext: true, AudioContext });
  vm.runInNewContext(script, {
    document, window, navigator: { mediaDevices: { getUserMedia: async () => {
      permissionRequests++; return getUserMedia ? getUserMedia(stream) : stream;
    } } }, AudioWorkletNode: Worklet, WebSocket: Socket,
    location: { protocol: 'https:', host: 'car.example' }, Date: { now: () => time },
    setInterval(fn) { const id = ++nextTimer; intervals.set(id, fn); return id; },
    clearInterval(id) { intervals.delete(id); },
    setTimeout(fn) { const id = ++nextTimer; timeouts.set(id, fn); return id; },
    clearTimeout(id) { timeouts.delete(id); },
    ArrayBuffer, DataView, console,
  });
  const packet = value => sockets[0].onmessage({ data: JSON.stringify(value) });
  return {
    elements, contexts, track, worklets, sockets, document, window,
    get permissionRequests() { return permissionRequests; },
    async connect() {
      elements['#token'].value = '仅用于测试的密码';
      await elements['#login'].emit('submit'); sockets[0].onopen();
      packet({ type: 'ready', mode: '真实小车', car_online: true });
      packet({ type: 'audio_format', sample_rate: 16000 });
    },
    click: () => elements['#talk'].emit('click'), packet,
    frame() { const data = new ArrayBuffer(640); new DataView(data).setInt16(0, 8000, true); worklets.at(-1).port.onmessage({ data }); },
    tick(seconds) { time += seconds * 1000; for (const fn of [...intervals.values()]) fn(); },
    timeout() { for (const fn of [...timeouts.values()]) fn(); },
    disconnect() { sockets[0].readyState = 3; sockets[0].onclose({ reason: '测试断线' }); },
  };
}

test('点击开始与结束有明确反馈，音量和计时来自音频，不发动车指令', async () => {
  const h = setup(); await h.connect();
  assert.equal(h.elements['#login'].hidden, true);
  assert.equal(h.elements['#talk'].disabled, false);
  await h.click();
  assert.equal(h.permissionRequests, 1);
  assert.equal(h.elements['#talk'].textContent, '结束说话');
  assert.equal(h.elements['#talk'].attrs['aria-pressed'], 'true');
  assert.deepEqual(h.sockets[0].sent.at(-1), { type: 'mic', on: true });
  h.frame(); h.tick(1);
  assert.ok(h.elements['#mic-level'].value > 0);
  assert.equal(h.elements['#record-time'].textContent, '00:01');
  await h.click();
  assert.deepEqual(h.sockets[0].sent.at(-1), { type: 'mic', on: false });
  assert.equal(h.elements['#talk'].attrs['aria-pressed'], 'false');
  assert.equal(h.elements['#mic-level'].value, 0);
  assert.ok(!h.sockets[0].sent.some(item => item.type === 'drive'));
});

test('麦克风权限拒绝后说明原因且可再次点击重试', async () => {
  let deny = true;
  const h = setup({ getUserMedia: stream => {
    if (deny) throw Object.assign(new Error(), { name: 'NotAllowedError' });
    return stream;
  } });
  await h.connect(); await h.click();
  assert.match(h.elements['#voice-status'].textContent, /权限被拒绝/);
  assert.equal(h.contexts[0].state, 'closed');
  assert.equal(h.elements['#talk'].disabled, false);
  deny = false; await h.click();
  assert.equal(h.elements['#talk'].attrs['aria-pressed'], 'true');
});

test('等待授权时断线，迟到的麦克风不会继续录音且释放资源', async () => {
  let allow;
  const h = setup({ getUserMedia: stream => new Promise(resolve => { allow = () => resolve(stream); }) });
  await h.connect();
  const opening = h.click(); await Promise.resolve(); await Promise.resolve();
  assert.equal(h.elements['#talk'].disabled, true);
  await h.click(); assert.equal(h.permissionRequests, 1);
  h.disconnect(); allow(); await opening;
  assert.equal(h.elements['#login'].hidden, false);
  assert.equal(h.track.stopped, 1);
  assert.equal(h.elements['#talk'].disabled, true);
  assert.ok(!h.sockets[0].sent.some(item => item.type === 'mic' && item.on));
});

test('切到后台停止录音并释放麦克风，回来不会自行继续收音', async () => {
  const h = setup(); await h.connect(); await h.click();
  h.document.hidden = true; await h.document.emit('visibilitychange');
  assert.equal(h.elements['#talk'].attrs['aria-pressed'], 'false');
  assert.equal(h.track.stopped, 1);
  assert.equal(h.worklets[0].port.sent.at(-1).enabled, false);
  h.document.hidden = false; await h.document.emit('visibilitychange');
  assert.equal(h.elements['#talk'].attrs['aria-pressed'], 'false');
});

test('权限请求无响应会超时，之后允许的旧请求也不会偷偷收音', async () => {
  let allow;
  const h = setup({ getUserMedia: stream => new Promise(resolve => { allow = () => resolve(stream); }) });
  await h.connect();
  const opening = h.click(); await Promise.resolve(); await Promise.resolve();
  h.timeout(); await opening;
  assert.match(h.elements['#voice-status'].textContent, /启动超时/);
  assert.equal(h.elements['#talk'].disabled, false);
  allow(); await new Promise(resolve => setImmediate(resolve));
  assert.equal(h.track.stopped, 1);
  assert.ok(!h.sockets[0].sent.some(item => item.type === 'mic' && item.on));
});

test('麦克风没有送来音频时报告失败，避免显示虚假的录音状态', async () => {
  const h = setup(); await h.connect(); await h.click(); h.tick(2);
  assert.match(h.elements['#voice-status'].textContent, /未收到麦克风音频/);
  assert.equal(h.elements['#talk'].attrs['aria-pressed'], 'false');
  assert.equal(h.elements['#talk'].disabled, false);
  assert.equal(h.track.stopped, 1);
});

test('30秒自动结束录音，网络拥堵也会停止收音', async () => {
  const h = setup(); await h.connect(); await h.click(); h.frame(); h.tick(30);
  assert.equal(h.elements['#talk'].attrs['aria-pressed'], 'false');
  assert.match(h.elements['#voice-status'].textContent, /30 秒/);
  await h.click(); h.sockets[0].bufferedAmount = 65536; h.frame();
  assert.equal(h.elements['#talk'].attrs['aria-pressed'], 'false');
  assert.match(h.elements['#voice-status'].textContent, /网络拥堵/);
});

test('灵矽开始回复时结束收音，回复期间不再开启麦克风', async () => {
  const h = setup(); await h.connect(); await h.click();
  h.packet({ type: 'linx_event', event: { type: 'speaking', on: true } });
  assert.equal(h.elements['#talk'].attrs['aria-pressed'], 'false');
  assert.equal(h.elements['#talk'].disabled, true);
  h.packet({ type: 'linx_event', event: { type: 'speaking', on: false } });
  assert.equal(h.elements['#talk'].disabled, false);
});

test('炮台单次微调与单发按钮携带租约；锁定、冷却、离线会禁用发射', async () => {
  const h = setup(); await h.connect();
  h.packet({type: 'lease', id: '本次测试租约'});
  await h.elements['#barrel-up'].emit('click');
  assert.deepEqual(h.sockets[0].sent.at(-1), {type: 'barrel', direction: 'up', lease: '本次测试租约'});
  assert.equal(h.elements['#fire'].disabled, true);
  h.packet({type: 'fire_state', available: true, armed: false, reason: '发射已锁定'});
  await h.elements['#fire-arm'].emit('click');
  assert.deepEqual(h.sockets[0].sent.at(-1), {type: 'fire_arm', on: true, lease: '本次测试租约'});
  h.packet({type: 'fire_state', available: true, armed: true, reason: '已启用'});
  await h.elements['#fire'].emit('click'); await h.elements['#fire'].emit('click');
  assert.equal(h.sockets[0].sent.filter(x => x.type === 'fire').length, 1);
  assert.equal(h.elements['#fire'].disabled, true);
  h.packet({type: 'action_result', action: 'fire', message: '已安排单发', cooldown_ms: 2000});
  h.tick(2); h.timeout();
  assert.equal(h.elements['#fire'].disabled, false);
  await h.elements['#stop'].emit('click');
  assert.equal(h.elements['#fire'].disabled, true);
  h.disconnect();
  assert.equal(h.elements['#barrel-up'].disabled, true);
  assert.equal(h.elements['#fire-arm'].disabled, true);
});
