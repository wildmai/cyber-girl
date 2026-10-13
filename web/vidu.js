// Microphone and camera are never published; realtime mode uplinks generated silence.
export class ViduPlayer {
  constructor(view, ready, failed, speech, completed, progress) {
    this.view = view;
    this.ready = ready;
    this.failed = failed;
    this.speech = speech;
    this.completed = completed;
    this.progress = progress;
    this.client = null;
    this.volume = 0.8;
    this.muted = true;
    this.epoch = 0;
  }

  async connect({ rtc, live_id, mode = 'standard' }) {
    await this.close();
    const epoch = this.epoch;
    let timer;
    let cancel;
    const deadline = new Promise((resolve, reject) => {
      cancel = () => reject(new Error('数字人连接已取消'));
      this.cancelConnect = cancel;
      timer = setTimeout(() => reject(new Error('数字人音视频连接超时，已取消连接，请检查网络后重试')), 45000);
    });
    try {
      await Promise.race([this.startSession({ rtc, live_id, mode }, epoch), deadline]);
    } catch (error) {
      if (epoch === this.epoch) await this.close();
      throw error;
    } finally {
      clearTimeout(timer);
      if (this.cancelConnect === cancel) this.cancelConnect = null;
    }
  }

  async startSession({ rtc, live_id, mode }, epoch) {
    this.liveId = live_id;
    const { default: TRTC } = await import('/rtc-sdk.js');
    if (epoch !== this.epoch) throw new Error('数字人连接已取消');
    TRTC.setLogLevel(5, false);
    const client = TRTC.create();
    this.client = client;
    const videoReady = new Promise((resolve, reject) => {
      client.on(TRTC.EVENT.REMOTE_VIDEO_AVAILABLE, async ({ userId, streamType }) => {
        try {
          await client.startRemoteVideo({ userId, streamType, view: this.view, option: { fillMode: 'cover' } });
          if (epoch !== this.epoch) return;
          this.view.classList.add('playing');
          this.ready(live_id);
          resolve();
        } catch { reject(new Error('数字人画面订阅失败')); }
      });
      client.on(TRTC.EVENT.KICKED_OUT, () => {
        if (epoch === this.epoch) this.failed('数字人连接已结束，请重新连接');
      });
      client.on(TRTC.EVENT.AUTOPLAY_FAILED, () => {
        if (epoch === this.epoch) this.failed('浏览器阻止了数字人声音，请点击页面后重新连接');
      });
      client.on(TRTC.EVENT.AUDIO_VOLUME, ({ result }) => {
        if (epoch !== this.epoch || this.muted) return;
        if (result.some(({ userId, volume }) => userId && volume > 1)) {
          clearTimeout(this.speechTimer);
          this.speech(true);
          this.progress?.(0.2);
          const requestId = this.playbackId;
          this.speechTimer = setTimeout(() => {
            this.speech(false, true);
            if (requestId && requestId === this.playbackId) {
              this.playbackId = null;
              this.completed?.(this.liveId, requestId);
              this.mute(true);
            }
          }, requestId ? 1800 : 600);
        }
      });
    });
    // Attach a rejection handler before enterRoom finishes to avoid an unhandled timeout.
    videoReady.catch(() => {});
    try {
      await client.enterRoom({ sdkAppId: Number(rtc.app_id), strRoomId: rtc.channel_id,
        userId: rtc.user_id, userSig: rtc.token, scene: TRTC.TYPE.SCENE_LIVE,
        role: mode === 'realtime' ? TRTC.TYPE.ROLE_ANCHOR : TRTC.TYPE.ROLE_AUDIENCE, autoReceiveAudio: true });
      if (epoch !== this.epoch) throw new Error('数字人连接已取消');
      if (mode === 'realtime') {
        // The speech provider needs a continuous input stream to survive idle periods.
        // This zero-valued track is independent of the user's microphone.
        const context = new AudioContext();
        const source = context.createConstantSource();
        source.offset.value = 0;
        const destination = context.createMediaStreamDestination();
        source.connect(destination);
        const track = destination.stream.getAudioTracks()[0];
        this.silent = { context, source, track };
        source.start();
        await context.resume();
        if (epoch !== this.epoch) throw new Error('数字人连接已取消');
        await client.startLocalAudio({ option: { audioTrack: track } });
      }
      client.setRemoteAudioVolume('*', 0);
      client.enableAudioVolumeEvaluation(200);
      await videoReady;
      console.info('[avatar] Vidu RTC stream connected');
    } catch (error) {
      if (epoch === this.epoch) {
        console.warn('[avatar] RTC connection failed', { code: error.code || 'connection-failed' });
        await this.close();
      }
      throw new Error(error.message?.includes('数字人') ? error.message : '数字人音视频连接失败，请检查网络后重试');
    }
  }

  setVolume(volume) {
    this.volume = volume;
    this.client?.setRemoteAudioVolume('*', this.muted ? 0 : Math.round(volume * 100));
  }

  expectSpeech(requestId) {
    clearTimeout(this.speechTimer);
    this.playbackId = requestId;
    this.mute(false);
  }

  mute(muted) {
    this.muted = muted;
    if (muted) { this.playbackId = null; clearTimeout(this.speechTimer); this.speech(false); }
    this.setVolume(this.volume);
  }

  async close() {
    this.epoch++;
    const epoch = this.epoch;
    this.cancelConnect?.();
    clearTimeout(this.speechTimer);
    this.speech(false);
    const client = this.client;
    const silent = this.silent;
    this.silent = null;
    this.client = null;
    this.playbackId = null;
    this.view.classList.remove('playing');
    this.muted = true;
    if (client) {
      let timer;
      try {
        client.setRemoteAudioVolume('*', 0);
        await Promise.race([client.exitRoom(), new Promise(resolve => { timer = setTimeout(resolve, 1500); })]);
      } catch { /* Already disconnected. */ }
      finally { clearTimeout(timer); client.destroy(); }
    }
    if (silent) { silent.source.stop(); silent.track.stop(); await silent.context.close(); }
    if (epoch === this.epoch) this.view.replaceChildren();
  }
}
