import { ViduPlayer } from './vidu.js?v=sessions1';
import { PlaybackCaptions } from './captions.js?v=1';

const $ = (selector) => document.querySelector(selector);
const escapeHTML = (value) => String(value).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const icon = (name) => `<svg aria-hidden="true"><use href="#i-${name}"/></svg>`;
const displayMessage = (message) => String(message)
  .replace(/MINIMAX_API_KEY/gi, "对话服务密钥")
  .replace(/MINIMAX_BASE_URL/gi, "对话服务地址")
  .replace(/https:\/\/api\.minimax(?:i\.com|\.io)\/v1/gi, "官方服务地址")
  .replace(/minimax/gi, "对话服务")
  .replace(/\bVidu(?:\s+S2)?\b/gi, "实时数字人");
const modelName = (model) => model?.backend === "minimax" ? (model.model.includes("highspeed") ? "快速对话模型" : "标准对话模型") : model?.name;
let status = null;
let socket = null;
let connected = false;
let sessionToken = null;
let portraitObjectUrl;
let generation = 0;
let reconnectTimer;
let toastTimer;
let drawerView = "models";
let switching = false;
let pendingSelection = {};
let audioContext;
let micStream;
let captureNode;
let inputSource;
let micOn = false;
let micStarting = false;
let micMonitor;
let micDevices = [];
let conversationBusy = false;
let serverDone = true;
let playbackEnd = 0;
let audioDecoding = 0;
let playingSources = new Set();
let audioChain = Promise.resolve();
let playbackEpoch = 0;
let cancelMedia;
let avatarPlaying = false;
let serverState = "idle";
let currentReply = "";
let replyHasSpeech = false;
let viduConnecting = false;
let viduAttempt = 0;
let viduError = '';
let viduSpeaking = false;
let cloudPaused = false;
const isRealtime = () => Boolean(status?.avatar?.realtime);
const playbackCaptions = new PlaybackCaptions(text => caption('小满', text));
const viduPlayer = new ViduPlayer($("#vidu-view"),
  (liveId) => send({ type: "vidu_ready", live_id: liveId }),
  (message) => { toast(message, true); disconnectVidu(); },
  (speaking, finished = false) => {
    if (speaking && !viduSpeaking) console.info('[avatar] Vidu remote audio detected');
    viduSpeaking = speaking;
    if (speaking) { conversationBusy = true; setState('speaking'); }
    else {
      if (finished) playbackCaptions.endRemote();
      finishPlayback();
    }
  },
  (liveId, requestId) => send({ type: 'vidu_playback_done', live_id: liveId, request_id: requestId }),
  seconds => playbackCaptions.tick(seconds));
const preferences = { speaker: "Vivian", volume: 0.8, subtitles: true, saveHistory: true, microphone: "" };
let history = [];
try {
  Object.assign(preferences, JSON.parse(localStorage.getItem("xiaoman.preferences") || "{}"));
  history = JSON.parse(localStorage.getItem("xiaoman.history") || "[]");
  if (!Array.isArray(history)) history = [];
} catch { /* A private browser may disable localStorage. */ }

function savePreferences() {
  try { localStorage.setItem("xiaoman.preferences", JSON.stringify(preferences)); } catch { /* Optional persistence. */ }
}
function saveHistory() {
  try {
    if (preferences.saveHistory) localStorage.setItem("xiaoman.history", JSON.stringify(history.slice(-200)));
    else localStorage.removeItem("xiaoman.history");
  } catch { /* Conversation still works without disk persistence. */ }
}
function addMessage(role, text) {
  history.push({ role, text, time: new Date().toISOString() });
  history = history.slice(-200);
  saveHistory();
  if ($("#drawer").open && drawerView === "history") renderHistory();
}
function toast(message, persistent = false) {
  clearTimeout(toastTimer);
  $("#toast-text").textContent = displayMessage(message);
  $("#toast").hidden = false;
  if (!persistent) toastTimer = setTimeout(() => { $("#toast").hidden = true; }, 6500);
}
$("#toast-close").addEventListener("click", () => { $("#toast").hidden = true; });

const states = {
  idle: ["准备好时，轻触声球", "开始对话", "语音在本地处理，小满在这里回应你"],
  connecting: ["正在连接数字人，请稍候", "连接中", "连接成功后会自动继续本次对话"],
  listening: ["我在听，你慢慢说", "正在聆听", "说完后稍停片刻，我就会回应"],
  hearing: ["嗯，我在认真听", "正在聆听", "说完后稍停片刻，我就会回应"],
  transcribing: ["正在听清你的话", "听懂你", "声音正在本地转成文字"],
  thinking: ["让我想一想，怎么回应你", "想一想", "轻触右侧按钮，随时停止回复"],
  synthesizing: ["正在本地生成声音，请稍候", "生成声音", "声音生成后自动播放，字幕会随声音显示"],
  cloud_synthesizing: ["正在生成实时声音", "生成声音", "实时数字人正在云端生成声音与人物画面"],
  animating: ["正在本地生成人物口型", "生成口型", "人物视频完成后，声音与口型会同步播放"],
  speaking: ["这一刻，想说给你听", "正在回应", "轻触右侧按钮，随时停止回复"],
};
function setState(name) {
  const state = states[name] || states.idle;
  $("#state-label").textContent = state[0];
  $("#orb-label").textContent = state[1];
  $("#voice-hint").textContent = name === "idle" && isRealtime() ? "语音在本机识别，声音与人物画面由云端生成" : name === "synthesizing" && status?.device === "cpu" ? "CPU 合成较慢，首段可能需要几十秒，完成后自动播放" : state[2];
  $("#voice-orb").classList.toggle("active", ["hearing", "speaking"].includes(name));
  $("#voice-orb").classList.toggle("busy", ["connecting", "thinking", "transcribing", "synthesizing", "cloud_synthesizing", "animating"].includes(name));
  $("#state-dot").classList.toggle("active", name !== "idle");
}
function idle() { setState(micOn ? "listening" : "idle"); }
function caption(name, text) {
  $("#caption-name").textContent = name;
  $("#caption-text").textContent = text;
  $("#caption-text").scrollTop = $("#caption-text").scrollHeight;
}
function send(data) {
  if (socket?.readyState !== WebSocket.OPEN) {
    toast("本地服务连接已断开，正在重连。请确认启动窗口仍在运行。");
    return false;
  }
  socket.send(JSON.stringify(data));
  return true;
}
function apiFetch(url, options = {}) {
  const headers = { ...options.headers };
  if (sessionToken) headers['X-Conversation-Token'] = sessionToken;
  return fetch(url, { ...options, headers });
}
async function setPortrait(url) {
  const previous = portraitObjectUrl;
  if (url?.startsWith('/api/')) {
    const response = await apiFetch(url);
    if (!response.ok) throw new Error('人物照片读取失败');
    portraitObjectUrl = URL.createObjectURL(await response.blob());
    $('#portrait').src = portraitObjectUrl;
  } else { portraitObjectUrl = null; $('#portrait').src = url; }
  $('#portrait').dataset.source = url;
  if (previous) URL.revokeObjectURL(previous);
}
function connect() {
  clearTimeout(reconnectTimer);
  socket = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws`);
  socket.onmessage = ({ data }) => {
    const event = JSON.parse(data);
    if (event.type === "connected") {
      console.info("[local] Conversation connected");
      connected = true;
      sessionToken = event.session_token;
      refreshStatus(true);
      generation = event.generation;
      send({ type: "voice", speaker: preferences.speaker });
      idle();
      return;
    }
    if (event.generation !== undefined && event.generation < generation) return;
    if (event.generation !== undefined) generation = event.generation;
    switch (event.type) {
      case "interrupted":
        stopPlayback();
        conversationBusy = false;
        serverDone = true;
        currentReply = "";
        if (viduConnecting) setState('connecting');
        else idle();
        break;
      case "user":
        stopPlayback();
        serverDone = false;
        conversationBusy = true;
        currentReply = "";
        replyHasSpeech = false;
        addMessage("user", event.text);
        caption("你", event.text);
        break;
      case "state":
        if (typeof event.speech === 'boolean') replyHasSpeech = event.speech;
        console.info("[voice]", event.state);
        conversationBusy = event.state !== "hearing";
        serverDone = false;
        serverState = event.state;
        setState(playingSources.size > 0 || avatarPlaying || viduSpeaking ? "speaking" : event.state);
        if (event.state === 'hearing' && micOn && isRealtime() && !viduPlayer.client && !viduConnecting) {
          const turn = generation;
          ensureRealtime(true).then(ready => {
            if (!ready && micOn && turn === generation) send({ type: 'avatar_connect_failed' });
          });
        }
        if (event.state === "animating" && !avatarPlaying && playingSources.size === 0) {
          $("#voice-hint").textContent = `${status?.device === "cpu" ? "CPU 可能需数分钟 · " : ""}口型生成 ${event.progress}% · 完成后同步播放`;
        }
        break;
      case "token":
        currentReply += event.text;
        if (!replyHasSpeech) caption("小满", currentReply);
        break;
      case "assistant":
        addMessage("assistant", event.text);
        currentReply = event.text;
        if (!replyHasSpeech) caption("小满", event.text);
        break;
      case "audio":
        queueMedia(event, playAudio);
        break;
      case "avatar":
        queueMedia(event, playAvatar);
        break;
      case "vidu_speaking":
        playbackCaptions.enqueue(event.text, event.duration);
        if (event.request_id) viduPlayer.expectSpeech(event.request_id);
        else viduPlayer.mute(false);
        break;
      case "vidu_stopped":
        playbackCaptions.reset();
        viduPlayer.mute(true);
        break;
      case 'avatar_idle':
        viduAttempt++;
        viduConnecting = false;
        cloudPaused = true;
        stopPlayback();
        viduPlayer.close();
        conversationBusy = false;
        serverDone = true;
        idle();
        toast(event.message);
        refreshStatus(true);
        break;
      case 'avatar_closed':
        viduAttempt++;
        viduConnecting = false;
        stopMic();
        stopPlayback();
        viduPlayer.close();
        conversationBusy = false;
        serverDone = true;
        idle();
        toast(event.message, true);
        refreshStatus(true);
        break;
      case "warning":
        toast(event.message);
        break;
      case "done":
        serverDone = true;
        finishPlayback();
        break;
      case "error":
        console.warn("[local]", event.message);
        stopPlayback();
        conversationBusy = false;
        serverDone = true;
        idle();
        toast(event.message, true);
        break;
    }
  };
  socket.onclose = (event) => {
    sessionToken = null;
    viduAttempt++;
    viduConnecting = false;
    viduPlayer.close();
    connected = false;
    stopMic();
    stopPlayback();
    conversationBusy = false;
    if (event.code === 1013) {
      $("#state-label").textContent = "服务繁忙，请稍后重试";
      toast("当前同时在线人数已达上限，请稍后刷新重试。", true);
      return;
    }
    $("#state-label").textContent = "本地连接已断开，正在重连";
    reconnectTimer = setTimeout(connect, 2500);
  };
  socket.onerror = () => console.warn("Local WebSocket unavailable");
}

async function ensureAudio() {
  if (!audioContext || audioContext.state === "closed") audioContext = new AudioContext();
  if (audioContext.state === "suspended") await audioContext.resume();
}
function queueMedia(event, player) {
  const epoch = playbackEpoch;
  audioDecoding++;
  audioChain = audioChain.then(() => {
    if (epoch === playbackEpoch && event.generation === generation) return player(event, epoch);
  }).catch((error) => {
    if (epoch !== playbackEpoch) return;
    console.warn("Local media playback failed", error);
    toast("播放失败。请点击页面启用声音，或检查输出设备。");
  }).finally(() => {
    if (epoch !== playbackEpoch) return;
    audioDecoding = Math.max(0, audioDecoding - 1);
    finishPlayback();
  });
}
async function playAvatar(event, epoch) {
  if (event.generation !== generation || epoch !== playbackEpoch) return;
  let mediaUrl = event.url;
  let objectUrl;
  if (mediaUrl.startsWith('/api/')) {
    const response = await apiFetch(mediaUrl);
    if (!response.ok) throw new Error('人物视频读取失败');
    objectUrl = URL.createObjectURL(await response.blob());
    mediaUrl = objectUrl;
    if (epoch !== playbackEpoch) { URL.revokeObjectURL(objectUrl); return; }
  }
  const video = $("#avatar-video");
  video.volume = Number(preferences.volume);
  try {
    await new Promise((resolve, reject) => {
      let settled = false;
      let started = false;
      const finish = (error) => {
        if (settled) return;
        settled = true;
        playbackCaptions.endLocal(false);
        video.onplaying = video.onended = video.onerror = null;
        video.pause();
        video.classList.remove("playing");
        video.removeAttribute("src");
        video.load();
        if (objectUrl) URL.revokeObjectURL(objectUrl);
        avatarPlaying = false;
        cancelMedia = null;
        error ? reject(error) : resolve();
      };
      cancelMedia = () => finish();
      video.onplaying = () => {
        avatarPlaying = true;
        video.classList.add("playing");
        setState("speaking");
        if (!started) {
          started = true;
          playbackCaptions.play(event.text, video.duration, () => video.currentTime);
        }
        console.info("[avatar] Synchronized video/audio playback started", { seconds: video.duration });
      };
      video.onended = () => { playbackCaptions.endLocal(); finish(); };
      video.onerror = () => finish(new Error("人物视频播放失败"));
      video.src = mediaUrl;
      video.play().catch(finish);
    });
    console.info("[avatar] Playback ended or cancelled");
  } catch (error) {
    if (epoch !== playbackEpoch || event.generation !== generation) return;
    console.warn("[avatar] Falling back to audio", error);
    toast("人物视频暂时无法播放，已切换为声音。");
    await playAudio({ ...event, data: event.audio }, epoch);
  }
}
async function playAudio(event, epoch) {
  if (event.generation !== generation || epoch !== playbackEpoch) return;
  await ensureAudio();
  const raw = Uint8Array.from(atob(event.data), (c) => c.charCodeAt(0));
  const buffer = await audioContext.decodeAudioData(raw.buffer);
  if (event.generation !== generation || epoch !== playbackEpoch) return;
  const source = audioContext.createBufferSource();
  const gain = audioContext.createGain();
  gain.gain.value = Number(preferences.volume);
  source.buffer = buffer;
  source.connect(gain).connect(audioContext.destination);
  source.localGain = gain;
  playingSources.add(source);
  const start = Math.max(audioContext.currentTime + 0.03, playbackEnd);
  playbackEnd = start + buffer.duration;
  await new Promise((resolve) => {
    source.onended = () => {
      playbackCaptions.endLocal();
      playingSources.delete(source);
      source.disconnect();
      gain.disconnect();
      cancelMedia = null;
      resolve();
    };
    cancelMedia = resolve;
    source.start(start);
    playbackCaptions.play(event.text, buffer.duration, () => audioContext.currentTime - start);
    console.info("[voice] Playback started", { seconds: buffer.duration, volume: gain.gain.value, context: audioContext.state });
    setState("speaking");
  });
}
function finishPlayback() {
  if (serverDone && playingSources.size === 0 && !avatarPlaying && !viduSpeaking && audioDecoding === 0) {
    conversationBusy = false;
    idle();
  } else if (!serverDone && playingSources.size === 0 && audioDecoding === 0 && currentReply) {
    setState(serverState === "animating" ? "animating" : isRealtime() ? "cloud_synthesizing" : "synthesizing");
  }
}
function stopPlayback() {
  playbackCaptions.reset();
  viduPlayer.mute(true);
  playbackEpoch++;
  cancelMedia?.();
  cancelMedia = null;
  audioChain = Promise.resolve();
  audioDecoding = 0;
  for (const source of playingSources) {
    source.onended = null;
    try { source.stop(); source.disconnect(); source.localGain.disconnect(); } catch { /* Already ended. */ }
  }
  playingSources.clear();
  playbackEnd = 0;
}
function interrupt() {
  send({ type: "interrupt" });
  stopPlayback();
  conversationBusy = false;
  serverDone = true;
  idle();
}
async function toggleMic() {
  if (micStarting || switching) return;
  if (micOn) { stopMic(); interrupt(); return; }
  if (!connected) { toast("正在连接本地服务，请稍候。"); return; }
  if (!status?.ready) {
    openDrawer("models");
    toast(isRealtime() ? "请先加载本地语音检测与识别模型。你也可以先打字对话。" : "语音对话需要加载三个本地语音模型。你也可以先打字和小满聊天。");
    return;
  }
  let stream;
  micStarting = true;
  try {
    if (!navigator.mediaDevices?.getUserMedia) throw new Error("请使用 localhost 地址访问，以启用麦克风。");
    await ensureAudio();
    // Keep local listening independent of the paid cloud connection.
    if (!isRealtime() && !await ensureRealtime()) return;
    stream = await navigator.mediaDevices.getUserMedia({ audio: {
      channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true,
      ...(preferences.microphone ? { deviceId: { exact: preferences.microphone } } : {}),
    } });
    // Permission dialogs and RTC setup can suspend the context after the first resume.
    await ensureAudio();
    await audioContext.audioWorklet.addModule("/audio-worklet.js");
    micStream = stream;
    inputSource = audioContext.createMediaStreamSource(stream);
    captureNode = new AudioWorkletNode(audioContext, "local-pcm-capture");
    let frames = 0;
    let uploaded = 0;
    let peak = 0;
    const micStartedAt = Date.now();
    let heardSound = false;
    captureNode.onprocessorerror = () => {
      console.error('[voice] Microphone processor failed');
      stopMic();
      toast('麦克风采集已中断，请重新开启语音对话。', true);
    };
    captureNode.port.onmessage = ({ data }) => {
      frames++;
      for (const sample of new Int16Array(data)) peak = Math.max(peak, Math.abs(sample) / 32768);
      if (peak > 0.003) heardSound = true;
      // Pause capture during a response to prevent speaker echo becoming a new turn.
      if (micOn && !conversationBusy && !viduSpeaking && connected && socket.bufferedAmount < 65536) {
        socket.send(data);
        uploaded++;
      }
    };
    inputSource.connect(captureNode);
    const silent = audioContext.createGain();
    silent.gain.value = 0;
    captureNode.connect(silent).connect(audioContext.destination);
    captureNode.silentOutput = silent;
    micOn = true;
    stream.getAudioTracks?.().forEach(track => { track.onended = () => {
      if (!micOn) return;
      stopMic();
      toast('麦克风已断开，请检查输入设备后重新开启。', true);
    }; });
    micMonitor = setInterval(() => {
      if (!micOn) return;
      const paused = audioContext.state !== 'running';
      if (paused) ensureAudio().catch(error => console.warn('[voice] Audio resume failed', error.message));
      if (!conversationBusy && !viduSpeaking && !viduConnecting) {
        $('#voice-hint').textContent = paused || !frames
          ? '麦克风采集暂停，请点击页面或重新开启语音'
          : !uploaded ? '声音尚未上传，请检查网络连接'
          : !heardSound && Date.now() - micStartedAt > 5000 ? '未检测到麦克风声音，请在偏好设置中选择正确的输入设备'
          : `麦克风输入 ${Math.min(100, Math.round(peak * 100))}% · ${cloudPaused ? '云端已暂停，开口后自动连接' : '说完后稍停片刻'}`;
      }
      console.info('[voice] Capture', JSON.stringify({ frames, uploaded, peak: Number(peak.toFixed(3)), context: audioContext.state }));
      frames = uploaded = 0;
      peak = 0;
    }, 1000);
    refreshMicrophones();
    cloudPaused = isRealtime() && !viduPlayer.client;
    console.info("[voice] Microphone started", audioContext.sampleRate);
    $("#mic-button").setAttribute("aria-pressed", "true");
    $("#mic-button").setAttribute("aria-label", "关闭麦克风");
    $("#mic-button").title = "关闭麦克风";
    $("#voice-orb").setAttribute("aria-label", "关闭语音对话");
    idle();
  } catch (error) {
    stream?.getTracks().forEach((track) => track.stop());
    stopMic();
    const denied = error.name === "NotAllowedError";
    toast(denied ? "麦克风权限未开启。请在浏览器地址栏允许麦克风，然后重试。" : `无法开启麦克风：${error.message}`, true);
  } finally {
    micStarting = false;
    if (!micOn) idle();
  }
}
function stopMic() {
  micOn = false;
  clearInterval(micMonitor);
  micStream?.getTracks().forEach((track) => track.stop());
  inputSource?.disconnect();
  captureNode?.disconnect();
  captureNode?.silentOutput?.disconnect();
  if (captureNode) { captureNode.port.onmessage = null; captureNode.onprocessorerror = null; }
  micStream = captureNode = inputSource = null;
  $("#mic-button").setAttribute("aria-pressed", "false");
  $("#mic-button").setAttribute("aria-label", "开启麦克风");
  $("#mic-button").title = "开启麦克风";
  $("#voice-orb").setAttribute("aria-label", "开始语音对话");
  idle();
}
$("#mic-button").addEventListener("click", toggleMic);
$("#voice-orb").addEventListener("click", toggleMic);
$("#stop-button").addEventListener("click", () => viduConnecting ? disconnectVidu() : interrupt());
$("#composer").addEventListener("submit", async (event) => {
  event.preventDefault();
  const text = $("#message-input").value.trim();
  if (!text) { $("#message-input").focus(); return; }
  if (!status?.loaded.includes("llm")) {
    openDrawer("models");
    toast("对话模型尚未连接，请在模型设置中准备并连接所选模型。");
    return;
  }
  try { await ensureAudio(); } catch { /* Text remains available if audio is unavailable. */ }
  if (!await ensureRealtime()) return;
  if (send({ type: "text", text })) $("#message-input").value = "";
});

async function refreshStatus(quiet = false) {
  try {
    const response = await apiFetch("/api/status");
    if (!response.ok) throw new Error("状态检查失败");
    status = await response.json();
    $(".offline-badge").innerHTML = `<span></span>${isRealtime() ? "实时模式" : status.offline ? "本地模式" : "本地语音"}`;
    $(".privacy-note").innerHTML = `${icon("shield")} ${isRealtime() ? "实时模式 · 本机识别 · 云端声音与画面" : status.avatar?.enabled && status.avatar?.provider === "vidu" ? "云端数字人 · 图片与合成语音联网处理" : status.offline ? "语音与对话均在本机处理" : "音频留在本地 · 对话文本联网处理"}`;
    if (viduPlayer.client && !status.avatar?.vidu?.connected && !viduConnecting) viduPlayer.close();
    if (status.avatar) {
      const portrait = status.avatar.portrait_url;
      if ($("#portrait").dataset.source !== portrait) {
        await setPortrait(portrait);
      }
    }
    for (const button of document.querySelectorAll("[data-model-stage]")) {
      const stage = button.dataset.modelStage;
      if (stage === 'llm') button.innerHTML = `<span></span>对话模型 <small>${status.llm_api.local ? '本地' : 'API'}</small>`;
      if (stage === 'tts') {
        button.innerHTML = `<span></span>${isRealtime() ? '实时数字人 <small>语音</small>' : 'Qwen3-TTS'}`;
        if (isRealtime()) {
          button.classList.toggle('loaded', status.avatar.vidu.connected);
          button.title = `实时数字人 · 豆包语音 · ${status.avatar.vidu.connected ? '已连接' : '待连接'}`;
          continue;
        }
      }
      const loaded = status.loaded.includes(stage);
      button.classList.toggle("loaded", loaded);
      button.title = `${modelName(status.models.find((m) => m.id === status.selected[stage]))} · ${loaded ? "已加载" : "未加载"}`;
    }
    $("#memory-label").textContent = isRealtime() ? `实时模式 · ${status.avatar.vidu.connected ? '数字人已连接' : '数字人待连接'}` : status.gpu ? `显存 ${status.gpu.used_gib.toFixed(1)} / ${status.budget_gib} GiB` : status.ready ? `对话已就绪 · 本地语音 ${status.device === "cpu" ? "CPU" : "已加载"}` : status.loaded.includes("llm") ? "对话已就绪 · 本地语音待加载" : "对话待连接 · 本地语音";
    if ($("#drawer").open && drawerView === "models" && !switching) renderModels();
    return status;
  } catch (error) {
    $("#memory-label").textContent = "本地服务未连接";
    if (!quiet) toast(`无法读取本地环境：${error.message}`);
  }
}

function openDrawer(view) {
  drawerView = view;
  const titles = { models: ["模型与连接", "VOICE & CONVERSATION"], history: ["我们的对话", "LITTLE MOMENTS, KEPT HERE"], settings: ["偏好设置", "MAKE YOURSELF AT HOME"] };
  $("#drawer-title").textContent = titles[view][0];
  $("#drawer-eyebrow").textContent = titles[view][1];
  if (view === "models") { pendingSelection = { ...status?.selected }; renderModels(); }
  if (view === "history") renderHistory();
  if (view === "settings") renderSettings();
  if (!$("#drawer").open) $("#drawer").showModal();
  $("#drawer-content").scrollTop = 0;
}
$("#models-button").addEventListener("click", () => openDrawer("models"));
$("#history-button").addEventListener("click", () => openDrawer("history"));
$("#settings-button").addEventListener("click", () => openDrawer("settings"));
$("#memory-button").addEventListener("click", () => openDrawer("models"));
document.querySelectorAll("[data-model-stage]").forEach((button) => button.addEventListener("click", () => {
  openDrawer("models");
  $(`#select-${button.dataset.modelStage}`)?.focus();
}));
$("#close-drawer").addEventListener("click", () => $("#drawer").close());
$("#drawer").addEventListener("click", (event) => {
  const bounds = $("#drawer").getBoundingClientRect();
  if (event.clientX < bounds.left) $("#drawer").close();
});

const stageNames = { vad: "语音检测", stt: "语音识别", llm: "对话模型", tts: "声音合成" };
function renderModels() {
  if (!status) {
    $("#drawer-content").innerHTML = `<p class="panel-description">正在检查本地环境…</p><button id="retry-status" class="secondary-button">重新连接</button>`;
    $("#retry-status").onclick = () => refreshStatus();
    return;
  }
  pendingSelection = { ...status.selected, ...pendingSelection };
  $("#drawer-content").innerHTML = `
    <p class="panel-description">${isRealtime() ? "实时模式：本机识别语音，云端生成声音与画面。" : status.llm_api.local ? "本地对话：语音识别、对话与合成均在本机运行。" : "语音识别与合成在本机运行，连接在线对话服务。"}<br>数字人可选择本地口型或云端实时画面。</p>
    <div class="model-field"><label class="field-label" for="avatar-provider">数字人模型<small>AVATAR</small></label><select id="avatar-provider" ${switching || viduConnecting ? "disabled" : ""}>${(status.avatar?.providers || []).map((p) => `<option value="${p.id}" ${status.avatar.provider === p.id ? "selected" : ""}>${escapeHTML(p.id === 'vidu' ? '实时数字人' : p.name)}</option>`).join("")}</select></div>
    ${status.avatar?.provider === 'vidu' ? `<div class="model-field"><label class="field-label" for="avatar-mode">运行模式<small>MODE</small></label><select id="avatar-mode" ${switching || viduConnecting ? 'disabled' : ''}><option value="standard" ${status.avatar.mode !== 'realtime' ? 'selected' : ''}>本地语音模式 · Qwen3-TTS</option><option value="realtime" ${status.avatar.mode === 'realtime' ? 'selected' : ''}>实时模式 · 云端语音</option></select><p class="setting-note">切换模式只会断开自己的数字人会话；实时模式无需加载本地语音合成模型。</p></div>` : ''}
    <label class="setting-row"><span><strong>开启数字人</strong><p>${status.avatar?.provider === "vidu" ? "实时数字人 · 实时画面与声音同步" : "MuseTalk 1.5 · 按句生成本地口型视频"}</p></span><input id="avatar-enabled" class="switch" type="checkbox" ${status.avatar?.enabled ? "checked" : ""} ${switching || viduConnecting ? "disabled" : ""}></label>
    ${status.avatar?.provider === "vidu" ? `<p class="setting-note">${isRealtime() ? "人物图片与回复文字发送至实时数字人服务，由豆包语音生成声音；无需加载本地 Qwen3-TTS。" : "人物图片和 Qwen3-TTS 合成语音会发送至实时数字人服务。"}麦克风原始音频仍在本机识别。云端会话持续计费，停止回复会保留连接；10 秒无有效对话会自动断开，也可立即手动断开。</p><p class="setting-note">多人独立对话${status.capacity ? ` · 最多 ${status.capacity.max_avatars} 路数字人同时在线` : ""}。画面连接超过 45 秒会自动取消，达到并发上限时提示忙碌。</p><div class="env-row"><span>数字人密钥</span><span class="${status.avatar.vidu.key_configured ? "ok" : ""}">${status.avatar.vidu.key_configured ? "已在服务端配置" : "请设置 VIDU_API_KEY 后重启"}</span></div><button id="vidu-connect" class="secondary-button" ${switching || !status.avatar.enabled || !status.avatar.vidu.key_configured ? "disabled" : ""}>${viduConnecting ? "取消数字人连接" : status.avatar.vidu.connected ? "断开实时数字人会话" : "连接实时数字人"}</button>` : `<p class="setting-note">${status.device === "cpu" ? "当前使用 CPU，口型生成需要较长时间。" : "与语音模型共用推理锁，避免显存峰值叠加。"}仅生成口型，不生成全身动作。</p>`}
    ${status.avatar?.last_error ? `<p class="error-note">${escapeHTML(displayMessage(status.avatar.last_error))}</p>` : ""}
    ${status.avatar?.provider === 'vidu' && viduError ? `<p class="error-note">${escapeHTML(displayMessage(viduError))}</p>` : ''}
    <div class="budget-panel" id="budget-panel">
      <div class="budget-top"><span>${status.low_memory ? "模型内存峰值预估" : "本地模型显存预估"}</span><span class="budget-number"><span id="budget-value"></span> <small>/ ${status.budget_gib} GiB</small></span></div>
      <div class="budget-track"><div class="budget-fill" id="budget-fill"></div></div>
      <p class="budget-note" id="budget-note"></p>
    </div>
    ${Object.entries(stageNames).map(([stage, name]) => `
      <div class="model-field">
        <label class="field-label" for="select-${stage}">${name}<small>${stage.toUpperCase()}</small></label>
        <select id="select-${stage}" data-stage="${stage}" ${switching || (stage === 'tts' && isRealtime()) ? "disabled" : ""}>
          ${stage === 'tts' && isRealtime() ? `<option>实时数字人 · 豆包语音</option>` : status.models.filter((m) => m.stage === stage).map((m) => `<option value="${escapeHTML(m.id)}" ${pendingSelection[stage] === m.id ? "selected" : ""}>${escapeHTML(modelName(m))}${m.available ? "" : m.backend === "minimax" ? " · 未配置密钥" : " · 未安装"}</option>`).join("")}
        </select>
        <div class="model-path" id="path-${stage}"></div>
      </div>`).join("")}
    <div class="panel-actions">
      <button class="secondary-button" id="connect-minimax" ${switching ? "disabled" : ""}>${status.loaded.includes("llm") ? "应用对话模型选择" : "连接对话服务 · 开始文字对话"}</button>
      <button class="primary-button" id="apply-models" ${switching ? "disabled" : ""}>${switching ? "正在释放显存并加载…" : status.ready ? "应用并热切换" : "加载本地模型"}</button>
      <button class="secondary-button" id="rescan" ${switching ? "disabled" : ""}>${icon("refresh")}重新检测本地文件</button>
    </div>
    <div class="environment">
      <p class="section-label">运行环境</p>
      <div class="env-row"><span>外部连接</span><span>${status.llm_api.local ? (status.avatar?.provider === "vidu" && status.avatar.enabled ? "仅云端数字人" : "无需联网") : status.avatar?.provider === "vidu" && status.avatar.enabled ? "对话服务与云端数字人" : "仅官方对话服务"}</span></div>
      <div class="env-row"><span>对话服务</span><span class="${status.llm_api.key_configured ? "ok" : ""}">${status.llm_api.local ? "llama.cpp · 无需密钥" : status.llm_api.key_configured ? "已在服务端配置" : "请按 README 配置服务密钥"}</span></div>
      <div class="env-row"><span>服务位置</span><span>${status.llm_api.local ? "本机 · 127.0.0.1" : status.llm_api.base_url.includes("minimaxi.com") ? "国内站" : "国际站"}</span></div>
      <div class="env-row"><span>显卡</span><span>${status.gpu ? escapeHTML(status.gpu.name) : "未检测到 NVIDIA GPU"}</span></div>
      <div class="env-row"><span>语音推理设备</span><span>${status.device === "cpu" ? "CPU · 速度较慢" : status.device === "auto" ? "自动选择 CUDA / CPU" : escapeHTML(status.device)}</span></div>
      <div class="env-row"><span>实际显存</span><span>${status.gpu ? `${status.gpu.used_gib} / ${status.gpu.total_gib} GiB` : "暂无实测数据"}</span></div>
      ${Object.entries(status.dependencies).map(([name, present]) => `<div class="env-row"><span>${escapeHTML(displayMessage(name))}</span><span class="${present ? "ok" : ""}">${present ? "已安装" : "待安装"}</span></div>`).join("")}
      <p class="setting-note">预估含 ${status.reserve_gib} GiB 运行余量。${status.llm_api.local ? "本地对话不发送文字到外部服务；首次回复时加载模型。" : "文字消息与当前对话上下文会发送至对话服务。"}${status.low_memory ? "低内存模式按阶段释放模型，等待时间会增加；这是模型预算估算，仍需在目标 Mac 上实测。" : ""}</p>
    </div>
    <details class="environment"><summary class="section-label">添加其他本地模型</summary>
      <p class="setting-note">Silero 必须为 v5 JIT；Whisper 为 PT；TTS 为 Qwen3-TTS CustomVoice；对话模型为 GGUF。</p>
      <form id="register-model">
        <div class="model-field"><label for="new-stage" class="field-label">模型类型</label><select id="new-stage">${Object.entries(stageNames).map(([s, n]) => `<option value="${s}">${n}</option>`).join("")}</select></div>
        <div class="model-field"><label for="new-name" class="field-label">显示名称</label><input id="new-name" maxlength="60" required placeholder="例如 Whisper medium"></div>
        <div class="model-field"><label for="new-path" class="field-label">本地完整路径</label><input id="new-path" required placeholder="E:/models/medium.pt"></div>
        <div class="model-field"><label for="new-memory" class="field-label">预计显存占用 / GiB</label><input id="new-memory" required type="number" min="0.1" max="14" step="0.1" value="4"></div>
        <button class="secondary-button" type="submit">添加到模型列表</button>
      </form>
    </details>
    <div id="model-error" class="error-note" hidden></div>`;
  document.querySelectorAll("select[data-stage]").forEach((select) => select.onchange = () => {
    pendingSelection[select.dataset.stage] = select.value;
    updateBudget();
  });
  $("#apply-models").onclick = applyModels;
  $("#connect-minimax").onclick = connectMiniMax;
  $("#rescan").onclick = () => refreshStatus();
  $("#register-model").onsubmit = registerModel;
  $("#avatar-provider").onchange = () => configureAvatar($("#avatar-enabled").checked, $("#avatar-provider").value);
  if ($("#avatar-mode")) $("#avatar-mode").onchange = () => configureAvatar($("#avatar-enabled").checked, 'vidu', $("#avatar-mode").value);
  if ($("#vidu-connect")) $("#vidu-connect").onclick = () => status.avatar.vidu.connected || viduConnecting ? disconnectVidu() : connectVidu();
  $("#avatar-enabled").onchange = async (event) => {
    await configureAvatar(event.target.checked, $("#avatar-provider").value);
  };
  updateBudget();
}
async function configureAvatar(enabled, provider, mode = status.avatar.mode) {
  stopMic();
  interrupt();
  switching = true;
  renderModels();
  try {
    const response = await apiFetch('/api/avatar', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ enabled, provider, mode }) });
    const result = await response.json();
    if (!response.ok) throw new Error(result.detail || '数字人设置失败');
    await viduPlayer.close();
    status = result;
    viduError = '';
    toast(enabled ? `${isRealtime() ? '实时模式' : result.avatar.name} 已选择${provider === 'vidu' ? '，请手动连接数字人。' : '，下一句生效。'}` : '数字人已关闭。');
  } catch (error) { toast(error.message, true); }
  finally { switching = false; await refreshStatus(true); renderModels(); }
}
async function ensureRealtime(preserveInput = false) {
  if (!connected) { toast("正在连接本地服务，请稍候。"); return false; }
  if (!isRealtime() || (status.avatar.vidu.connected && viduPlayer.client)) return true;
  if (viduConnecting) { toast("正在连接数字人，请稍候。"); return false; }
  if (!status.avatar.vidu.key_configured) {
    viduError = '请在本机配置 VIDU_API_KEY 并重启服务，再使用实时模式。';
    openDrawer('models');
    return false;
  }
  console.info('[conversation] Connecting realtime avatar before starting');
  const ready = await connectVidu(preserveInput);
  if (preserveInput && conversationBusy) setState(serverState);
  else idle();
  return ready;
}
async function connectVidu(preserveInput = false) {
  if (viduConnecting || !connected) return false;
  if (!preserveInput) interrupt();
  const attempt = ++viduAttempt;
  viduError = '';
  viduConnecting = true;
  setState('connecting');
  if ($("#drawer").open && drawerView === 'models') renderModels();
  try {
    await ensureAudio();
    const response = await apiFetch('/api/avatar/vidu/connect', { method: 'POST', signal: AbortSignal.timeout(90000) });
    const result = await response.json();
    if (attempt !== viduAttempt) return false;
    if (!response.ok) throw new Error(result.detail || '数字人连接失败');
    viduPlayer.setVolume(preferences.volume);
    await viduPlayer.connect(result);
    if (attempt !== viduAttempt) return false;
    cloudPaused = false;
    toast('实时数字人已连接，可以开始对话。会话持续计费，使用后请断开。');
    return true;
  } catch (error) {
    if (attempt !== viduAttempt) return false;
    await viduPlayer.close();
    await apiFetch('/api/avatar/vidu/disconnect', { method: 'POST' }).catch(() => {});
    viduError = error.name === 'TimeoutError' ? '数字人准备超时，已取消连接，请稍后重试。' : error.message;
    toast(viduError, true);
    return false;
  } finally {
    if (attempt === viduAttempt) { viduConnecting = false; await refreshStatus(true); }
  }
}
async function disconnectVidu() {
  viduAttempt++;
  viduConnecting = false;
  interrupt();
  await viduPlayer.close();
  try {
    const response = await apiFetch('/api/avatar/vidu/disconnect', { method: 'POST' });
    if (!response.ok) throw new Error('数字人会话断开失败，请关闭页面或重启本地服务。');
  } catch (error) { toast(error.message, true); }
  await refreshStatus(true);
}
function updateBudget() {
  let estimate = status.reserve_gib;
  if (status.avatar?.enabled) estimate += status.avatar.estimated_gib;
  for (const stage of Object.keys(stageNames)) {
    if (stage === 'tts' && isRealtime()) {
      $('#path-tts').textContent = '云端语音合成 · 不加载本地 TTS · 不占本地模型显存';
      continue;
    }
    const model = status.models.find((m) => m.id === pendingSelection[stage]);
    if (!model) continue;
    estimate += model.vram_gib;
    $(`#path-${stage}`).innerHTML = model.backend === "minimax" ? `在线对话服务 · 文本通过 HTTPS 传输<br><span class="path-status ${model.available ? "available" : ""}">${model.available ? "密钥已配置" : "请按 README 配置服务密钥"}</span> · 不占本地显存` : `${escapeHTML(model.detail)}<br><span class="path-status ${model.available ? "available" : ""}">${model.available ? "已找到本地文件" : model.backend === "llama.cpp" ? "请准备 GGUF 与 llama-server" : "未找到权重"}</span> · ${escapeHTML(model.path)}`;
  }
  const llm = status.models.find((m) => m.id === pendingSelection.llm);
  const serial = status.low_memory && llm?.backend === "llama.cpp";
  if (serial && status.avatar?.enabled) estimate -= Math.min(llm.vram_gib, status.avatar.estimated_gib);
  const over = estimate > status.budget_gib;
  $("#budget-value").textContent = estimate.toFixed(1);
  $("#budget-fill").style.width = `${Math.min(100, estimate / status.budget_gib * 100)}%`;
  $("#budget-panel").classList.toggle("over-budget", over);
  $("#budget-note").textContent = over ? "超出显存预算，请选择更小的模型组合。" : serial ? "低内存模式 · 按阶段释放模型 · 内存峰值为估算，需在目标 Mac 实测" : `${status.avatar?.enabled && status.avatar.provider === "musetalk" ? "含人物口型 3.8 GiB 预估 · " : ""}含运行余量 · ${status.gpu ? "加载后将检查实际 GPU 占用" : "待 GPU 环境就绪后验证实际占用"}`;
  $("#apply-models").disabled = switching || over;
}
async function applyModels() {
  if (switching) return;
  stopMic();
  interrupt();
  switching = true;
  renderModels();
  try {
    const response = await apiFetch("/api/models/switch", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ selection: pendingSelection }) });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || "加载失败");
    status = data;
    pendingSelection = { ...status.selected };
    console.info("[models] Loaded", status.loaded, status.device);
    switching = false;
    renderModels();
    refreshStatus(true);
    toast(isRealtime() ? "本地识别模型已加载，连接实时数字人后即可使用实时语音。" : status.device === "cpu" ? "模型已在 CPU 加载，可以语音对话；合成回应需要较长时间。" : "模型已加载，现在可以开始语音对话了。");
  } catch (error) {
    switching = false;
    await refreshStatus(true);
    renderModels();
    $("#model-error").hidden = false;
    $("#model-error").textContent = displayMessage(error.message);
    $("#model-error").scrollIntoView({ block: "nearest", behavior: "smooth" });
  }
}
async function connectMiniMax() {
  interrupt();
  try {
    const response = await apiFetch("/api/llm/connect", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ model_id: pendingSelection.llm }) });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || "连接失败");
    status = data;
    pendingSelection.llm = status.selected.llm;
    renderModels();
    refreshStatus(true);
    toast("对话已就绪，可以开始文字对话。首次发送消息时会验证服务权限。");
  } catch (error) { toast(error.message, true); }
}
async function registerModel(event) {
  event.preventDefault();
  const value = { stage: $("#new-stage").value, name: $("#new-name").value, path: $("#new-path").value, vram_gib: Number($("#new-memory").value) };
  try {
    const response = await apiFetch("/api/models/register", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(value) });
    const data = await response.json();
    if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : "请检查模型类型、路径和显存占用。");
    await refreshStatus();
    toast("已添加到本地模型列表，选择后即可加载。");
  } catch (error) { toast(error.message, true); }
}
function renderHistory() {
  $("#drawer-content").innerHTML = history.length ? `
    <div class="history-top"><span>${history.length} 条对话 · 仅保存在此浏览器</span><button id="clear-history">${icon("trash")}清空对话</button></div>
    <div class="history-list">${history.map((m) => `
      <article class="history-message ${m.role === "assistant" ? "assistant" : "user"}">
        <span class="message-avatar">${m.role === "assistant" ? "满" : "你"}</span>
        <div class="message-body"><div class="message-meta"><span>${m.role === "assistant" ? "小满" : "你"}</span><time>${escapeHTML(new Date(m.time).toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" }))}</time></div><p>${escapeHTML(m.text)}</p></div>
      </article>`).join("")}</div>` : `
    <div class="history-empty">${icon("chat")}<p>故事，从一句你好开始。</p><small>聊过的点滴，会留在这里。<br>对话只保存在你的浏览器中。</small></div>`;
  if ($("#clear-history")) $("#clear-history").onclick = () => {
    history = [];
    saveHistory();
    send({ type: "reset" });
    stopPlayback();
    caption("小满", "今天过得怎么样？");
    renderHistory();
    toast("聊天记录和当前对话记忆已清空。");
  };
}
async function refreshMicrophones() {
  try {
    const devices = await navigator.mediaDevices?.enumerateDevices?.();
    if (devices) micDevices = devices.filter(device => device.kind === 'audioinput');
    const select = $('#microphone');
    if (!select) return;
    select.innerHTML = '<option value="">系统默认麦克风</option>' + micDevices.map((device, index) =>
      `<option value="${escapeHTML(device.deviceId)}">${escapeHTML(device.label || `麦克风 ${index + 1}`)}</option>`).join('');
    select.value = preferences.microphone;
  } catch (error) { console.warn('[voice] Cannot list microphones', error.message); }
}
function renderSettings() {
  $("#drawer-content").innerHTML = `
    <p class="panel-description">调成你喜欢的模样。<br>每个人的对话与人设独立，播放偏好保存在当前浏览器。</p>
    <form id="persona-form" class="setting-block">
      <label for="persona-prompt">对话人设</label>
      <textarea id="persona-prompt" class="setting-textarea" rows="10" maxlength="6000" required disabled aria-describedby="persona-note" placeholder="正在读取人设…"></textarea>
      <p id="persona-note" class="setting-note">可修改名字、身份、性格和说话风格，最多 6000 字。实时模式的表情和动作也会遵循人设。保存后重置对话上下文，聊天记录保留；数字人会在下次对话时自动重连。人设会随对话发送至对话服务，实时模式还会发送至数字人服务。</p>
      <div class="panel-actions persona-actions"><button id="save-persona" class="primary-button" type="submit" disabled>保存人设</button><button id="reset-persona" class="secondary-button" type="button" disabled>恢复默认人设</button></div>
      <p id="persona-feedback" class="setting-note" role="status" aria-live="polite">正在读取本机人设…</p>
    </form>
    <div class="setting-block"><label for="speaker">小满的声音</label><select class="setting-select" id="speaker" ${isRealtime() ? 'disabled' : ''}>
      ${isRealtime() ? '<option>实时数字人 · 豆包默认音色</option>' : [["Vivian", "Vivian · 明亮自然"], ["Serena", "Serena · 温暖柔和"], ["Ono_Anna", "Ono Anna · 轻盈细腻"], ["Sohee", "Sohee · 清澈柔和"]].map(([id, name]) => `<option value="${id}" ${preferences.speaker === id ? "selected" : ""}>${name}</option>`).join("")}</select>
      <p class="setting-note">${isRealtime() ? "实时模式由实时数字人服务生成声音，本地预设音色不生效。" : "使用 Qwen3-TTS CustomVoice 预设音色，下一句生效。"}</p></div>
    <div class="setting-block"><label for="microphone">输入麦克风</label><select class="setting-select" id="microphone"><option value="">系统默认麦克风</option></select><p class="setting-note">选择你正在使用的麦克风，避免误选虚拟声卡。开启语音后可查看输入音量；更换设备会重新开始采音。</p></div>
    <div class="setting-block"><label for="volume">播放音量</label><div class="volume-row">${icon("volume")}<input id="volume" type="range" min="0" max="100" value="${Math.round(preferences.volume * 100)}"><output id="volume-value">${Math.round(preferences.volume * 100)}%</output></div></div>
    <label class="setting-row"><span><strong>显示对话字幕</strong><p>在声球下方显示彼此说的话</p></span><input id="subtitles" class="switch" type="checkbox" ${preferences.subtitles ? "checked" : ""}></label>
    <label class="setting-row"><span><strong>保存聊天记录</strong><p>保存在当前浏览器，关闭后清除已保存记录</p></span><input id="save-history" class="switch" type="checkbox" ${preferences.saveHistory ? "checked" : ""}></label>
    <div class="setting-block"><label>陪伴人物</label><label class="upload-label" for="portrait-upload">选择本地图片<input id="portrait-upload" type="file" accept="image/png,image/jpeg,image/webp"></label><button id="restore-portrait" class="secondary-button">恢复默认背景</button><p class="setting-note">${status?.avatar?.provider === "vidu" ? "图片先保存到本机，连接实时数字人时会发送至云端；更换图片后需重新连接数字人。" : "图片仅保存到本机服务，供 MuseTalk 生成口型。"}请选择清晰正脸，并将人物放在画面右侧。</p></div>
    <div class="environment"><p class="section-label">关于这份陪伴</p><p class="panel-description">${isRealtime() ? "实时模式在本机识别语音；人物图片与回复文字发送至实时数字人服务，由云端生成声音和画面。" : status?.llm_api?.local ? "语音识别、对话和合成均在本机运行。MuseTalk 在本机生成口型；选择云端数字人时，人物图片与回复会发送至数字人服务。" : "语音识别和合成在本机运行，对话文本与当前上下文发送至在线对话服务。MuseTalk 在本机生成口型；使用实时数字人时，人物图片、合成语音与回复文本发送至云端。"}浏览器通过 RTC 接收数字人音视频，不向 RTC 发布麦克风或摄像头。</p><p class="setting-note">Silero VAD v5 → Whisper → 对话模型 → ${isRealtime() ? "实时数字人 · 云端声音与画面" : `Qwen3-TTS → ${status?.avatar?.provider === "vidu" ? "实时数字人" : "MuseTalk 1.5"}`}<br>服务密钥仅由服务端读取，浏览器仅接收临时 RTC 入会凭证。<br>基于 Hugging Face speech-to-speech 的四阶段管线。</p></div>`;
  refreshMicrophones();
  $('#microphone').onchange = async event => {
    preferences.microphone = event.target.value;
    savePreferences();
    if (micOn) { stopMic(); interrupt(); await toggleMic(); }
  };
  const personaForm = $("#persona-form");
  const personaPrompt = $("#persona-prompt");
  const personaFeedback = $("#persona-feedback");
  const personaControls = [...personaForm.querySelectorAll("textarea, button")];
  async function requestPersona(method = "GET") {
    personaControls.forEach((control) => control.disabled = true);
    personaFeedback.className = "setting-note";
    personaFeedback.textContent = method === "GET" ? "正在读取本机人设…" : "正在保存人设…";
    try {
      const response = await apiFetch("/api/persona", { method, ...(method === "PUT" ? { headers: { "Content-Type": "application/json" }, body: JSON.stringify({ prompt: personaPrompt.value.trim() }) } : {}) });
      const result = await response.json();
      if (!response.ok) throw new Error(typeof result.detail === "string" ? result.detail : "人设不能为空，且不能超过 6000 字。");
      if (!personaForm.isConnected) return;
      personaPrompt.value = result.prompt;
      personaPrompt.placeholder = "写下你希望她拥有的身份、性格和说话风格";
      personaFeedback.textContent = method === "GET" ? "已读取本机人设，可直接编辑。" : "人设已保存，下一轮对话生效。";
      if (method !== "GET") {
        stopMic(); stopPlayback();
        if (isRealtime()) await disconnectVidu();
        console.info("[persona] Saved; conversation context reset");
      }
    } catch (error) {
      if (!personaForm.isConnected) return;
      personaFeedback.className = "error-note";
      personaFeedback.textContent = displayMessage(error.message);
      console.error("[persona] Request failed", error.message);
    } finally {
      if (personaForm.isConnected) personaControls.forEach((control) => control.disabled = false);
    }
  }
  personaForm.onsubmit = (event) => {
    event.preventDefault();
    if (!personaPrompt.value.trim()) { personaFeedback.textContent = "请先填写人设，不能只输入空格。"; return; }
    requestPersona("PUT");
  };
  $("#reset-persona").onclick = () => requestPersona("DELETE");
  requestPersona();
  $("#speaker").onchange = (event) => {
    preferences.speaker = event.target.value;
    savePreferences();
    send({ type: "voice", speaker: preferences.speaker });
    toast("音色已更新，下一句会使用新声音。");
  };
  $("#volume").oninput = (event) => {
    preferences.volume = Number(event.target.value) / 100;
    $("#volume-value").textContent = `${event.target.value}%`;
    for (const source of playingSources) source.localGain.gain.value = preferences.volume;
    $("#avatar-video").volume = preferences.volume;
    viduPlayer.setVolume(preferences.volume);
    savePreferences();
  };
  $("#subtitles").onchange = (event) => {
    preferences.subtitles = event.target.checked;
    document.querySelector(".conversation-caption").style.visibility = preferences.subtitles ? "visible" : "hidden";
    savePreferences();
  };
  $("#save-history").onchange = (event) => {
    preferences.saveHistory = event.target.checked;
    savePreferences();
    saveHistory();
  };
  $("#portrait-upload").onchange = async (event) => {
    const file = event.target.files[0];
    if (!file) return;
    if (!/^image\/(png|jpeg|webp)$/.test(file.type) || file.size > 15 * 1024 * 1024) { toast("请选择 15 MB 以内的 PNG、JPG 或 WebP 图片。"); return; }
    interrupt();
    try {
      await viduPlayer.close();
      const response = await apiFetch("/api/avatar/portrait", { method: "PUT", body: file });
      const result = await response.json();
      if (!response.ok) throw new Error(result.detail || "图片保存失败");
      await setPortrait(result.portrait_url);
      toast(status?.avatar?.provider === "vidu" ? "人物照片已保存，请重新连接实时数字人。" : "人物照片已保存到本机，下一句会使用新照片生成口型。");
    } catch (error) { toast(error.message, true); }
  };
  $("#restore-portrait").onclick = async () => {
    interrupt();
    try {
      await viduPlayer.close();
      const response = await apiFetch("/api/avatar/portrait", { method: "DELETE" });
      if (!response.ok) throw new Error("恢复默认人物失败");
      $("#portrait").src = "/assets/xiaoman.png";
      $("#portrait").dataset.source = "/assets/xiaoman.png";
      toast("已恢复小满的默认背景。");
    } catch (error) { toast(error.message, true); }
  };
}

document.querySelector(".conversation-caption").style.visibility = preferences.subtitles ? "visible" : "hidden";
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && !$("#drawer").open && conversationBusy) interrupt();
});
window.addEventListener("pagehide", () => { stopMic(); stopPlayback(); viduPlayer.close(); socket?.close(); });
connect();
refreshStatus(true);
setInterval(() => { if (!$("#drawer").open) refreshStatus(true); }, 15000);
