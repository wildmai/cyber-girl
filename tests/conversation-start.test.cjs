const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

// Run the actual frontend module with local service, RTC and microphone substitutes.
async function setup({ fail = false, key = true, realtime = true, pending = false, suspendOnPermission = false } = {}) {
  const elements = new Map();
  const element = selector => {
    if (!elements.has(selector)) elements.set(selector, {
      value: '', open: false, dataset: {}, style: {}, handlers: {}, attributes: {},
      classList: { toggle() {}, add() {}, remove() {} },
      addEventListener(name, fn) { this.handlers[name] = fn; },
      setAttribute(name, value) { this.attributes[name] = value; },
      showModal() { this.open = true; }, close() { this.open = false; },
      focus() {}, scrollIntoView() {},
      isConnected: true, querySelectorAll() { return []; },
    });
    return elements.get(selector);
  };
  const state = { loaded: ['vad', 'stt', 'llm'], ready: true, selected: {}, models: [],
    reserve_gib: 1.5, budget_gib: 15, dependencies: {}, llm_api: { key_configured: true, base_url: '' },
    avatar: { realtime, enabled: true, provider: 'vidu', providers: [], estimated_gib: 0,
      vidu: { connected: false, key_configured: key } } };
  const sent = [];
  const frames = [];
  const captures = [];
  const mediaRequests = [];
  const fetchRequests = [];
  const calls = [];
  let websocket;
  let finishRTC;
  let cancelRTC;
  let player;
  let audio;
  const sources = [];
  const timers = new Map();
  let timerId = 0;
  let now = 0;
  const track = { stopped: false, stop() { this.stopped = true; } };
  class Socket {
    static OPEN = 1;
    readyState = 1;
    bufferedAmount = 0;
    constructor() { websocket = this; }
    send(raw) { if (typeof raw === 'string') sent.push(JSON.parse(raw)); else frames.push(raw); }
  }
  class Player {
    client = null;
    constructor(view, ready, failed, speech, completed, progress) {
      Object.assign(this, { ready, speech, completed, progress });
      player = this;
    }
    mute() {} setVolume() {}
    expectSpeech() {}
    async connect() {
      calls.push('rtc');
      if (pending) await new Promise((resolve, reject) => { finishRTC = resolve; cancelRTC = reject; });
      if (fail) throw new Error('测试：数字人音视频连接失败');
      this.client = {};
      state.avatar.vidu.connected = true;
      this.ready('test-live');
    }
    async close() { cancelRTC?.(new Error('数字人连接已取消')); this.client = null; state.avatar.vidu.connected = false; }
  }
  const audioNode = () => ({ connect() { return this; }, disconnect() {}, gain: { value: 0 }, port: {} });
  class Audio {
    constructor() { audio = this; }
    currentTime = 0;
    state = 'running'; sampleRate = 48000;
    async resume() { calls.push('resume'); this.state = 'running'; }
    audioWorklet = { async addModule() {} };
    createMediaStreamSource() { return audioNode(); }
    createGain() { return audioNode(); }
    async decodeAudioData() { return { duration: 4 }; }
    createBufferSource() {
      const node = { ...audioNode(), start() {}, stop() {} };
      sources.push(node);
      return node;
    }
  }
  const context = vm.createContext({
    console: { info() {}, warn() {}, error() {} },
    document: { querySelector: element, querySelectorAll: () => [], addEventListener() {} },
    window: { addEventListener() {} }, location: { protocol: 'http:', host: '127.0.0.1:7860' },
    localStorage: { getItem() { return null; }, setItem() {}, removeItem() {} },
    WebSocket: Socket, AudioContext: Audio, AbortSignal, AudioWorkletNode: class { constructor() { const node = audioNode(); captures.push(node); return node; } },
    navigator: { mediaDevices: { async enumerateDevices() { return [{ kind: 'audioinput', deviceId: 'usb-mic', label: 'USB microphone' }]; }, async getUserMedia(constraints) {
      calls.push('microphone');
      mediaRequests.push(constraints);
      if (suspendOnPermission) audio.state = 'suspended';
      return { getTracks: () => [track], getAudioTracks: () => [track] };
    } } },
    atob: value => Buffer.from(value, 'base64').toString('binary'),
    Date: class extends Date { static now() { return now; } },
    setTimeout: () => 1, clearTimeout() {},
    setInterval: (fn, delay) => { if (delay === 50 || delay === 1000) { timers.set(++timerId, fn); return timerId; } },
    clearInterval: id => timers.delete(id),
    fetch: async (url, options) => {
      fetchRequests.push({ url, options });
      calls.push(url);
      if (url.endsWith('/connect')) return { ok: true, json: async () => ({ live_id: 'test-live', rtc: {} }) };
      return { ok: true, json: async () => state };
    },
  });
  const source = new vm.SourceTextModule(fs.readFileSync('web/app.js', 'utf8'), { context });
  const dependency = new vm.SyntheticModule(['ViduPlayer'], function () { this.setExport('ViduPlayer', Player); }, { context });
  await source.link(async specifier => {
    if (specifier.includes('vidu.js')) return dependency;
    const captions = new vm.SourceTextModule(fs.readFileSync('web/captions.js', 'utf8'), { context });
    await captions.link(() => {});
    return captions;
  });
  await source.evaluate();
  await new Promise(setImmediate);
  websocket.onmessage({ data: JSON.stringify({ type: 'connected', generation: 0, session_token: 'private-page-session' }) });
  const submit = () => element('#composer').handlers.submit({ preventDefault() {} });
  return { element, sent, calls, state, submit, track, player,
    sources, timers, frames, captures, mediaRequests, fetchRequests, audio: () => audio,
    advanceTime: ms => { now += ms; },
    receive: event => websocket.onmessage({ data: JSON.stringify({ generation: 0, ...event }) }),
    finishRTC: () => finishRTC() };
}

(async () => {
  let app = await setup();
  app.element('#message-input').value = '你好，小满';
  await app.submit();
  assert.equal(app.element('#drawer').open, false);
  assert.equal(app.calls.filter(url => url === '/api/avatar/vidu/connect').length, 1);
  assert.equal(app.fetchRequests.find(r => r.url === '/api/avatar/vidu/connect').options.headers['X-Conversation-Token'], 'private-page-session');
  assert(app.sent.find(event => event.type === 'text' && event.text === '你好，小满'));
  assert(app.sent.findIndex(event => event.type === 'vidu_ready') < app.sent.findIndex(event => event.type === 'text'));
  assert.equal(app.element('#message-input').value, '');
  app.element('#message-input').value = '第二句';
  await app.submit();
  assert.equal(app.calls.filter(url => url === '/api/avatar/vidu/connect').length, 1);

  app = await setup();
  await app.element('#voice-orb').handlers.click();
  assert.equal(app.element('#drawer').open, false);
  assert.equal(app.element('#mic-button').attributes['aria-pressed'], 'true');
  assert(!app.calls.includes('rtc'), 'Silence must not open a paid cloud session');
  const frame = new ArrayBuffer(1024);
  app.captures[0].port.onmessage({ data: frame });
  assert.equal(app.frames.length, 1, 'Starting voice must upload captured PCM');
  app.receive({ type: 'state', state: 'hearing' });
  app.captures[0].port.onmessage({ data: frame });
  assert.equal(app.frames.length, 2, 'VAD needs trailing silence after hearing speech');
  await new Promise(setImmediate);
  assert(app.calls.indexOf('microphone') < app.calls.indexOf('rtc'));
  assert(!app.sent.some(event => event.type === 'interrupt'), 'Voice-triggered reconnect must preserve the utterance');
  app.receive({ type: 'state', state: 'transcribing' });
  app.captures[0].port.onmessage({ data: frame });
  assert.equal(app.frames.length, 2, 'Do not upload echo while preparing a response');
  app.receive({ type: 'done' });
  app.captures[0].port.onmessage({ data: frame });
  assert.equal(app.frames.length, 3, 'Resume capture for the next turn');
  await app.element('#voice-orb').handlers.click();
  assert.equal(app.track.stopped, true);
  assert.equal(app.timers.size, 0, 'Stop input monitoring when closing the microphone');

  app = await setup({ suspendOnPermission: true });
  await app.element('#voice-orb').handlers.click();
  assert.equal(app.audio().state, 'running', 'Resume capture after awaiting microphone permission');
  assert(app.calls.indexOf('resume') > app.calls.indexOf('microphone'));
  app.captures[0].onprocessorerror();
  assert.equal(app.track.stopped, true);
  assert.match(app.element('#toast-text').textContent, /采集已中断/);

  app = await setup();
  await app.element('#voice-orb').handlers.click();
  for (const fn of app.timers.values()) fn();
  assert.match(app.element('#voice-hint').textContent, /采集暂停/);
  app.advanceTime(6000);
  app.captures[0].port.onmessage({ data: new ArrayBuffer(1024) });
  for (const fn of app.timers.values()) fn();
  assert.match(app.element('#voice-hint').textContent, /未检测到麦克风声音/);
  app.captures[0].port.onmessage({ data: new Int16Array(512).fill(3000).buffer });
  for (const fn of app.timers.values()) fn();
  assert.match(app.element('#voice-hint').textContent, /麦克风输入 9%/);
  app.advanceTime(6000);
  app.captures[0].port.onmessage({ data: new ArrayBuffer(1024) });
  for (const fn of app.timers.values()) fn();
  assert.doesNotMatch(app.element('#voice-hint').textContent, /未检测到/, 'Normal pauses after speech are not microphone failures');
  await app.element('#settings-button').handlers.click();
  await new Promise(setImmediate);
  assert.match(app.element('#microphone').innerHTML, /USB microphone/);
  await app.element('#microphone').onchange({ target: { value: 'usb-mic' } });
  assert.equal(app.mediaRequests.at(-1).audio.deviceId.exact, 'usb-mic');
  app.track.onended();
  assert.equal(app.element('#mic-button').attributes['aria-pressed'], 'false');
  assert.match(app.element('#toast-text').textContent, /麦克风已断开/);

  app = await setup();
  await app.element('#voice-orb').handlers.click();
  app.receive({ type: 'state', state: 'hearing' });
  await new Promise(setImmediate);
  app.receive({ type: 'avatar_closed', message: '连接超时，数字人已自动断开。' });
  assert.equal(app.element('#mic-button').attributes['aria-pressed'], 'false');
  assert.equal(app.track.stopped, true);
  assert.match(app.element('#toast-text').textContent, /自动断开/);
  await app.element('#voice-orb').handlers.click();
  app.receive({ type: 'state', state: 'hearing' });
  await new Promise(setImmediate);
  assert.equal(app.element('#mic-button').attributes['aria-pressed'], 'true');
  assert.equal(app.calls.filter(url => url === '/api/avatar/vidu/connect').length, 2);

  app.receive({ type: 'avatar_idle', message: '10 秒没有说话，云端已暂停。' });
  assert.equal(app.element('#mic-button').attributes['aria-pressed'], 'true');
  const interrupts = app.sent.filter(event => event.type === 'interrupt').length;
  app.receive({ type: 'state', state: 'hearing' });
  app.captures.at(-1).port.onmessage({ data: new ArrayBuffer(1024) });
  await new Promise(setImmediate);
  assert.equal(app.calls.filter(url => url === '/api/avatar/vidu/connect').length, 3);
  assert.equal(app.sent.filter(event => event.type === 'interrupt').length, interrupts);

  app = await setup({ fail: true });
  app.element('#message-input').value = '失败后仍保留';
  await app.submit();
  assert.equal(app.element('#message-input').value, '失败后仍保留');
  assert(!app.sent.some(event => event.type === 'text'));
  assert.equal(app.element('#drawer').open, false);
  assert.match(app.element('#toast-text').textContent, /连接失败/);
  assert(app.calls.includes('/api/avatar/vidu/disconnect'));

  app = await setup({ fail: true });
  await app.element('#voice-orb').handlers.click();
  app.receive({ type: 'state', state: 'hearing' });
  await new Promise(setImmediate);
  assert(app.calls.includes('microphone'));
  assert(app.sent.some(event => event.type === 'avatar_connect_failed'));

  app = await setup({ key: false });
  app.element('#message-input').value = '密钥未配置';
  await app.submit();
  assert.equal(app.element('#drawer').open, true);
  assert.match(app.element('#drawer-content').innerHTML, /VIDU_API_KEY/);
  assert(!app.calls.includes('/api/avatar/vidu/connect'));

  app = await setup({ realtime: false });
  app.element('#message-input').value = '普通文字对话';
  await app.submit();
  assert(!app.calls.includes('/api/avatar/vidu/connect'));
  assert(app.sent.some(event => event.type === 'text'));

  app = await setup({ pending: true });
  app.element('#message-input').value = '只发送一次';
  const firstSend = app.submit();
  await new Promise(setImmediate);
  await app.submit();
  assert.equal(app.calls.filter(url => url === '/api/avatar/vidu/connect').length, 1);
  app.finishRTC();
  await firstSend;
  assert.equal(app.sent.filter(event => event.type === 'text').length, 1);

  app = await setup({ pending: true });
  app.element('#message-input').value = '取消后保留';
  const cancelled = app.submit();
  await new Promise(setImmediate);
  await app.element('#stop-button').handlers.click();
  await cancelled;
  assert(!app.sent.some(event => event.type === 'text'));
  assert.equal(app.element('#message-input').value, '取消后保留');
  app = await setup();
  const reply = '你好呀，今晚一起去散步好吗？';
  app.receive({ type: 'user', text: '你好' });
  app.receive({ type: 'state', state: 'thinking', speech: true });
  app.receive({ type: 'token', text: reply });
  app.receive({ type: 'assistant', text: reply });
  assert.equal(app.element('#caption-text').textContent, '你好');
  app.receive({ type: 'vidu_speaking', text: reply, request_id: 'one' });
  assert.notEqual(app.element('#orb-label').textContent, '正在回应');
  app.player.speech(true);
  app.player.progress(0.2);
  const partial = app.element('#caption-text').textContent;
  assert(reply.startsWith(partial) && partial.length > 0 && partial.length < reply.length);
  app.receive({ type: 'assistant', text: reply });
  app.receive({ type: 'done' });
  assert.equal(app.element('#caption-text').textContent, partial);
  app.player.speech(false, true);
  assert.equal(app.element('#caption-text').textContent, reply);
  app.receive({ type: 'user', text: '第二轮' });
  app.receive({ type: 'state', state: 'thinking', speech: true });
  app.receive({ type: 'vidu_speaking', text: reply, request_id: 'two' });
  app.player.progress(0.2);
  await app.element('#stop-button').handlers.click();
  const interrupted = app.element('#caption-text').textContent;
  app.player.progress(10);
  app.player.speech(false, true);
  assert.equal(app.element('#caption-text').textContent, interrupted);
  app.receive({ type: 'user', text: '仅文字' });
  app.receive({ type: 'state', state: 'thinking', speech: false });
  app.receive({ type: 'token', text: '文字无需等声音' });
  assert.equal(app.element('#caption-text').textContent, '文字无需等声音');
  app = await setup({ realtime: false });
  app.receive({ type: 'user', text: '请说两句话' });
  app.receive({ type: 'state', state: 'thinking', speech: true });
  app.receive({ type: 'token', text: '今天辛苦了。我会陪着你。' });
  app.receive({ type: 'audio', text: '今天辛苦了。', data: 'AA==' });
  app.receive({ type: 'audio', text: '我会陪着你。', data: 'AA==' });
  app.receive({ type: 'done' });
  await new Promise(setImmediate);
  assert.equal(app.element('#caption-text').textContent, '请说两句话');
  assert.equal(app.sources.length, 1); // The second chunk waits for actual playback completion.
  app.audio().currentTime = 2;
  for (const fn of app.timers.values()) fn();
  assert('今天辛苦了。'.startsWith(app.element('#caption-text').textContent));
  assert.notEqual(app.element('#caption-text').textContent, '今天辛苦了。');
  app.audio().currentTime = 4.03;
  app.sources[0].onended();
  await new Promise(setImmediate);
  assert.equal(app.sources.length, 2);
  assert.equal(app.element('#caption-text').textContent, '今天辛苦了。');
  app.audio().currentTime = 6;
  for (const fn of app.timers.values()) fn();
  assert(app.element('#caption-text').textContent.startsWith('今天辛苦了。'));
  assert.notEqual(app.element('#caption-text').textContent, '今天辛苦了。我会陪着你。');
  app.sources[1].onended();
  await new Promise(setImmediate);
  assert.equal(app.element('#caption-text').textContent, '今天辛苦了。我会陪着你。');
  assert.equal(app.timers.size, 0);
  app = await setup({ realtime: false });
  const video = app.element('#avatar-video');
  Object.assign(video, { currentTime: 0, duration: 4,
    play() { this.onplaying(); return Promise.resolve(); }, pause() {}, load() {}, removeAttribute() {} });
  app.receive({ type: 'user', text: '带口型的视频' });
  app.receive({ type: 'state', state: 'thinking', speech: true });
  app.receive({ type: 'avatar', text: '今天辛苦了。', url: '/one.mp4' });
  app.receive({ type: 'avatar', text: '我会陪着你。', url: '/two.mp4' });
  app.receive({ type: 'done' });
  await new Promise(setImmediate);
  assert.equal(video.src, '/one.mp4');
  video.currentTime = 2;
  for (const fn of app.timers.values()) fn();
  assert.notEqual(app.element('#caption-text').textContent, '今天辛苦了。我会陪着你。');
  video.currentTime = 0;
  video.onended();
  await new Promise(setImmediate);
  assert.equal(video.src, '/two.mp4');
  video.currentTime = 1;
  for (const fn of app.timers.values()) fn();
  const videoPartial = app.element('#caption-text').textContent;
  assert(videoPartial.startsWith('今天辛苦了。') && videoPartial.length < '今天辛苦了。我会陪着你。'.length);
  await app.element('#stop-button').handlers.click();
  video.currentTime = 4;
  for (const fn of app.timers.values()) fn();
  assert.equal(app.element('#caption-text').textContent, videoPartial);
  assert.equal(video.onended, null);
  assert.equal(app.timers.size, 0);
  console.log('PASS: text and voice auto-connect, reuse, failure recovery, missing key, standard mode, duplicate clicks and cancellation');
})().catch(error => { console.error(error); process.exitCode = 1; });
