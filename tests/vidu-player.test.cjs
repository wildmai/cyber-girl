const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

(async () => {
  let latest;
  class FakeRTC {
    static EVENT = { REMOTE_VIDEO_AVAILABLE: 'video', KICKED_OUT: 'kick', AUTOPLAY_FAILED: 'autoplay', AUDIO_VOLUME: 'volume' };
    static TYPE = { SCENE_LIVE: 'live', ROLE_AUDIENCE: 'audience', ROLE_ANCHOR: 'anchor' };
    static create() { latest = new FakeRTC(); return latest; }
    static setLogLevel() {}
    handlers = {};
    volumes = [];
    on(name, fn) { this.handlers[name] = fn; }
    async enterRoom(options) {
      this.options = options;
      queueMicrotask(() => this.handlers.video({ userId: 'avatar', streamType: 'main' }));
    }
    async startRemoteVideo(options) { this.video = options; }
    async startLocalAudio(options) { this.published = options.option.audioTrack; }
    setRemoteAudioVolume(userId, volume) { this.volumes.push({ userId, volume }); }
    enableAudioVolumeEvaluation(interval) { this.interval = interval; }
    async exitRoom() { this.exited = true; }
    destroy() { this.destroyed = true; }
  }
  let silentContext;
  class SilentAudioContext {
    constructor() { silentContext = this; }
    source = { offset: { value: 1 }, connect() {}, start() {}, stop() { this.stopped = true; } };
    track = { stop() { this.stopped = true; } };
    createConstantSource() { return this.source; }
    createMediaStreamDestination() { return { stream: { getAudioTracks: () => [this.track] } }; }
    async resume() {}
    async close() { this.closed = true; }
  }
  const timers = new Map();
  const context = vm.createContext({ console, AudioContext: SilentAudioContext,
    setTimeout: (fn, delay) => { const id = setTimeout(fn, delay); timers.set(id, { fn, delay }); return id; },
    clearTimeout: id => { clearTimeout(id); timers.delete(id); },
  });
  const sdk = new vm.SyntheticModule(['default'], function () { this.setExport('default', FakeRTC); }, { context });
  const source = new vm.SourceTextModule(fs.readFileSync('web/vidu.js', 'utf8'), {
    context, importModuleDynamically: async () => {
      if (sdk.status === 'unlinked') await sdk.link(() => {});
      await sdk.evaluate();
      return sdk;
    },
  });
  await source.link(() => {});
  await source.evaluate();
  const classes = new Set();
  const view = { classList: { add: v => classes.add(v), remove: v => classes.delete(v) }, replaceChildren() {} };
  const ready = [];
  const speech = [];
  const progress = [];
  const endings = [];
  const completed = [];
  const player = new source.namespace.ViduPlayer(view, id => ready.push(id), assert.fail,
    (v, finished) => { speech.push(v); if (finished) endings.push(true); },
    (liveId, requestId) => completed.push({ liveId, requestId }), seconds => progress.push(seconds));
  await player.connect({ rtc: { app_id: '123', channel_id: 'room', user_id: 'viewer', token: 'ephemeral' }, live_id: '9007199254740993123' });
  assert.equal(latest.options.role, 'audience');
  assert.equal(latest.options.strRoomId, 'room');
  assert.equal(latest.options.sdkAppId, 123);
  assert.equal(latest.options.userSig, 'ephemeral');
  assert.deepEqual(ready, ['9007199254740993123']);
  assert(classes.has('playing'));
  player.setVolume(0.4);
  assert.equal(latest.volumes.at(-1).volume, 0);
  latest.handlers.volume({ result: [{ userId: 'avatar', volume: 25 }] });
  assert.equal(progress.length, 0); // Muted and pre-playback frames cannot advance captions.
  player.mute(false);
  assert.equal(latest.volumes.at(-1).volume, 40);
  latest.handlers.volume({ result: [{ userId: 'avatar', volume: 25 }] });
  assert.equal(speech.at(-1), true);
  assert.deepEqual(progress, [0.2]);
  latest.handlers.volume({ result: [{ userId: 'avatar', volume: 0 }] });
  assert.equal(progress.length, 1);
  player.mute(true);
  assert.equal(latest.volumes.at(-1).volume, 0);
  assert.equal(speech.at(-1), false);
  assert.equal(endings.length, 0); // User interruption is not natural completion.
  player.expectSpeech('reply-1');
  latest.handlers.volume({ result: [{ userId: 'avatar', volume: 0 }] });
  assert.equal(completed.length, 0); // Silence before any audio is not completion.
  latest.handlers.volume({ result: [{ userId: 'avatar', volume: 25 }] });
  const silence = [...timers].find(([, value]) => value.delay === 1800);
  assert(silence);
  clearTimeout(silence[0]);
  timers.delete(silence[0]);
  silence[1].fn();
  assert.deepEqual(completed, [{ liveId: '9007199254740993123', requestId: 'reply-1' }]);
  assert.equal(endings.length, 1);
  assert.equal(player.muted, true);
  player.expectSpeech('cancelled-reply');
  latest.handlers.volume({ result: [{ userId: 'avatar', volume: 25 }] });
  player.mute(true);
  assert.equal(completed.length, 1);
  assert(![...timers.values()].some(value => value.delay === 1800));
  await player.close();
  assert(latest.exited && latest.destroyed);
  assert(!classes.has('playing'));
  assert.equal(player.client, null);
  await player.connect({ rtc: { app_id: '123', channel_id: 'room', user_id: 'viewer', token: 'ephemeral' }, live_id: '2', mode: 'realtime' });
  assert.equal(latest.options.role, 'anchor');
  assert.equal(silentContext.source.offset.value, 0);
  assert.equal(latest.published, silentContext.track);
  await player.close();
  assert(silentContext.source.stopped && silentContext.track.stopped && silentContext.closed);
  FakeRTC.prototype.enterRoom = async function () {};
  const connecting = player.connect({ rtc: { app_id: '123', channel_id: 'room', user_id: 'viewer', token: 'ephemeral' }, live_id: '1' });
  const cancelled = assert.rejects(connecting, /数字人连接已取消/);
  await new Promise(resolve => setImmediate(resolve));
  await player.close();
  await cancelled;
  assert.equal(player.client, null);
  assert(latest.exited && latest.destroyed);
  FakeRTC.prototype.enterRoom = function () { return new Promise(() => {}); };
  const stuckJoin = player.connect({ rtc: { app_id: '123', channel_id: 'room', user_id: 'viewer', token: 'ephemeral' }, live_id: 'stuck' });
  const timedOut = assert.rejects(stuckJoin, /连接超时/);
  await new Promise(resolve => setImmediate(resolve));
  const deadline = [...timers.values()].find(timer => timer.delay === 45000);
  assert(deadline, 'Whole RTC join must have a deadline');
  deadline.fn();
  await timedOut;
  assert.equal(player.client, null);
  assert(latest.destroyed, 'A hung join must still destroy the RTC client');

  FakeRTC.prototype.exitRoom = function () { return new Promise(() => {}); };
  const boundedCleanup = player.connect({ rtc: { app_id: '123', channel_id: 'room', user_id: 'viewer', token: 'ephemeral' }, live_id: 'cleanup' });
  const cancelledCleanup = assert.rejects(boundedCleanup, /已取消/);
  await new Promise(resolve => setImmediate(resolve));
  const cleanup = player.close();
  await new Promise(resolve => setImmediate(resolve));
  const exitDeadline = [...timers.values()].find(timer => timer.delay === 1500);
  assert(exitDeadline, 'RTC exit also needs a deadline');
  exitDeadline.fn();
  await cleanup;
  await cancelledCleanup;
  assert(latest.destroyed);
  console.log('Vidu RTC player: audience subscription, readiness, volume and teardown passed');
})().catch(error => { console.error(error); process.exitCode = 1; });
