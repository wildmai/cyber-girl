"""Vidu S2 sessions with local PCM or cloud text-to-speech."""

import base64
import io
import json
import logging
import os
import re
import threading
import time
import uuid
import wave

import httpx
import numpy as np
from websockets.sync.client import connect

from app.persona import DEFAULT_PERSONA

API_HOST = "api.vidu.cn"
log = logging.getLogger(__name__)


class ViduError(RuntimeError):
    def __init__(self, message, audio_sent=False):
        super().__init__(message)
        self.audio_sent = audio_sent


def pcm24(wav):
    with wave.open(io.BytesIO(wav), "rb") as source:
        if source.getnchannels() != 1 or source.getsampwidth() != 2:
            raise ViduError("Vidu 需要单声道 PCM16 音频。")
        rate = source.getframerate()
        samples = np.frombuffer(source.readframes(source.getnframes()), dtype="<i2")
    if rate != 24000 and samples.size:
        count = round(samples.size * 24000 / rate)
        samples = np.interp(np.arange(count) * rate / 24000, np.arange(samples.size), samples).astype("<i2")
    return samples.tobytes()


class ViduSession:
    def __init__(self):
        self.ws = None
        self.live_id = None
        self.seq = 0
        self.lock = threading.RLock()
        self.connect_lock = threading.Lock()
        self.cancel = threading.Event()
        self.initialized = threading.Event()
        self.client_ready = threading.Event()
        self.error = None
        self.connecting = False
        self.mode = "standard"
        self.playback_id = None
        self.playback_complete = threading.Event()
        self.playback_error = None
        self.opened_at = 0
        self.last_activity = 0
        self.on_expire = None
        self.is_busy = lambda: False
        self.expired_idle = False

    def touch(self):
        self.last_activity = time.monotonic()
        if self.ws is None and not self.connecting:
            self.error = None

    def wait_ready(self, valid):
        deadline = time.monotonic() + 90
        while valid():
            if self.status()["connected"]:
                return True
            if self.error and not self.connecting:
                raise ViduError(self.error)
            if time.monotonic() >= deadline:
                raise ViduError("数字人重连超时，请检查网络后重试。")
            self.client_ready.wait(0.1)
        return False

    def expire_if_needed(self, ws, now=None):
        if self.ws is not ws:
            return False
        now = time.monotonic() if now is None else now
        reason = None
        self.expired_idle = False
        if not self.client_ready.is_set() and now - self.opened_at >= 45:
            reason = "数字人画面连接超时，已自动挂断，避免继续计费。请检查网络后重试。"
        elif self.client_ready.is_set() and not self.playback_id and not self.is_busy() and now - self.last_activity >= 10:
            self.expired_idle = True
            reason = "10 秒没有有效对话，云端数字人已暂停。麦克风继续本地监听，开口后自动重连。"
        if reason:
            self.close()
            self.error = reason
            log.info("Vidu session expired: %s", reason)
            if self.on_expire:
                self.on_expire(reason)
            return True
        return False

    def status(self):
        return {"key_configured": bool(os.environ.get("VIDU_API_KEY", "").strip()),
                "connected": self.ws is not None and self.client_ready.is_set(),
                "connecting": self.connecting, "last_error": self.error}

    def signal(self, kind, payload=None):
        with self.lock:
            if self.ws is None:
                raise ViduError("Vidu 会话未连接，请在模型设置中连接数字人。")
            self.seq += 1
            self.ws.send(json.dumps({"type": kind, "live_id": self.live_id,
                                     "seq_id": self.seq, "payload": payload or {}}))

    def open(self, portrait, mode="standard", persona=DEFAULT_PERSONA):
        key = os.environ.get("VIDU_API_KEY", "").strip()
        if not key:
            raise ViduError("请在本机设置 VIDU_API_KEY 后重启服务。")
        if not self.connect_lock.acquire(blocking=False):
            raise ViduError("Vidu 正在连接，请稍候。")
        try:
            self.close()
            self.cancel.clear()
            self.initialized.clear()
            self.error = None
            self.expired_idle = False
            self.connecting = True
            self.mode = mode
            image = "data:image/png;base64," + base64.b64encode(portrait.read_bytes()).decode("ascii")
            if mode == "realtime":
                endpoint = "/live/s_avatar/realtime"
                body = {"model": "vidu-s2", "call_mode": "video", "rtc": {"provider": "trtc"},
                        "avatar": {"image_uri": image, "persona": persona,
                                   "greeting_instruction": "保持安静，等待用户输入。"},
                        "idle_motion": {"source": "custom", "recommend": "random",
                                        "motions": [{"name": "自然等待", "content": "平静专注地看向镜头，自然眨眼和呼吸，保持放松，不做夸张表情或手势。"}],
                                        "scheduling": {"motion_interval": 20, "rest_after_motion": 5}},
                        "voice_model": {"provider": "doubao"}, "vad": {"idle_timeout_ms": 0}}
            else:
                endpoint = "/live/s_avatar/component"
                body = {"model": "vidu-s2", "image_uri": image, "managed_rtc": {"provider": "trtc"}}
            with httpx.Client(timeout=30, trust_env=False, follow_redirects=False) as client:
                response = client.post(f"https://{API_HOST}{endpoint}",
                    headers={"Authorization": f"Token {key}"}, json=body)
            if response.status_code != 200:
                raise ViduError(f"Vidu 创建失败（HTTP {response.status_code}），请检查密钥、S2 权限及账户余额。")
            data = response.json()
            if mode == "realtime" and data["live"].get("voice_model", {}).get("provider") != "doubao":
                raise ViduError("Vidu 未启用豆包语音，请检查实时模式权限。")
            live_id = str(data["live"]["id"])
            if not re.fullmatch(r"[0-9]+", live_id):
                raise ViduError("Vidu 返回了无效的会话编号。")
            self.live_id = live_id
            rtc = data["rtc" if mode == "realtime" else "managed_rtc"]
            if rtc.get("provider") != "trtc" or not all(rtc.get(k) for k in ("app_id", "channel_id", "user_id", "token")):
                raise ViduError("Vidu 未返回完整的托管 RTC 入会信息。")
            stream_path = (f"/live/ws/live/connect?live_id={live_id}" if mode == "realtime"
                           else f"/live/v1/external-lives/{live_id}/stream")
            ws = connect(f"wss://{API_HOST}{stream_path}",
                         additional_headers={"Authorization": f"Token {key}"},
                         open_timeout=15, close_timeout=2, ping_interval=5,
                         max_size=16 * 1024 * 1024, proxy=None)
            with self.lock:
                self.ws, self.live_id, self.seq = ws, live_id, 0
                self.opened_at = self.last_activity = time.monotonic()
            if self.cancel.is_set():
                raise ViduError("Vidu 连接已取消。")
            threading.Thread(target=self._receive, args=(ws,), name="vidu-control", daemon=True).start()
            self.signal(1, {"conn_init": {"version": 1}})
            deadline = time.monotonic() + 45
            while not self.initialized.wait(0.1):
                if self.cancel.is_set() or time.monotonic() >= deadline:
                    raise ViduError("Vidu 初始化已取消或超时，请重新连接。")
            if self.error:
                raise ViduError(self.error)
            log.info("Vidu S2 %s control session initialized", mode)
            # Only short-lived RTC credentials reach the browser; API key stays here.
            return {k: str(rtc[k]) for k in ("provider", "app_id", "channel_id", "user_id", "token")}
        except Exception as exc:
            self.close()
            self.error = str(exc) if isinstance(exc, ViduError) else "Vidu 连接失败，请检查网络及服务配置。"
            raise ViduError(self.error) from None
        finally:
            self.connecting = False
            self.connect_lock.release()

    def _receive(self, ws):
        retry_at = None
        try:
            while self.ws is ws and not self.cancel.is_set():
                if self.expire_if_needed(ws):
                    return
                try:
                    raw = ws.recv(timeout=0.2)
                except TimeoutError:
                    if retry_at is not None and time.monotonic() >= retry_at:
                        self.signal(1, {"conn_init": {"version": 1}})
                        retry_at = None
                    continue
                message = json.loads(raw)
                if message.get("type") == 2:
                    ack = message.get("payload", {}).get("conn_init_ack", {})
                    if ack.get("success"):
                        self.initialized.set()
                    elif ack.get("error_code") == "NOT_READY":
                        retry_at = time.monotonic() + 1
                    else:
                        raise ViduError("Vidu 渲染初始化失败，请检查 S2 权限后重新连接。")
                elif message.get("type") == 6:
                    reason = message.get("payload", {}).get("hangup", {}).get("hangup_reason")
                    known = {"user_end", "timeout", "audit_violation", "credit_insufficient", "sip_closed",
                             "provider_closed", "sip_reconnect_timeout", "client_reconnect_timeout",
                             "prepared_sip_disconnected", "owner_taken_over", "owner_lease_lost",
                             "ai_output_closed", "external"}
                    log.warning("Vidu force hangup: %s", reason if reason in known else "unspecified")
                    raise ViduError("Vidu 会话已由服务端结束，请重新连接。")
                elif message.get("type") == 103:
                    self.playback_error = "Vidu 实时语音或动作请求被拒绝，请检查豆包语音与 S2 动作权限或重新连接。"
                    self.playback_complete.set()
        except Exception as exc:
            if self.ws is ws:
                self.error = str(exc) if isinstance(exc, ViduError) else "Vidu 控制连接已断开，请重新连接数字人。"
                self.initialized.set()
                self.client_ready.clear()
                self.playback_complete.set()
                with self.lock:
                    self.ws = None
                ws.close()
                log.warning("Vidu control session disconnected: %s, close_code=%s",
                            type(exc).__name__, getattr(getattr(exc, "rcvd", None), "code", None))
                if self.on_expire:
                    self.on_expire(self.error)

    def ready(self, live_id):
        if self.ws is not None and str(live_id) == self.live_id and self.initialized.is_set() and not self.error:
            self.client_ready.set()
            self.touch()

    def playback_done(self, live_id, request_id):
        with self.lock:
            if self.live_id == str(live_id) and self.playback_id == request_id and request_id:
                self.playback_complete.set()

    def speak(self, text, valid, speaking, action=""):
        self.touch()
        if not valid():
            return
        if self.mode != "realtime" or not self.status()["connected"]:
            raise ViduError("实时模式需要先连接 Vidu S2 数字人。")
        request_id = uuid.uuid4().hex
        with self.lock:
            self.playback_id = request_id
            self.playback_error = None
            self.playback_complete.clear()
        started = time.monotonic()
        try:
            with self.lock:
                if not valid() or self.cancel.is_set():
                    return
                speaking(request_id)
                if action:
                    self.signal(101, {"text_action": {"msg_id": request_id, "content": action}})
                self.signal(100, {"text_tts": {"msg_id": request_id, "content": text}})
            log.info("Vidu realtime speech/action submitted; chars=%d action_chars=%d", len(text), len(action))
            while not self.playback_complete.wait(0.1):
                if not valid() or self.cancel.is_set():
                    return
                if time.monotonic() - started > 180:
                    self.interrupt()
                    raise ViduError("实时语音播放超时，请重新连接数字人后重试。")
            if not valid() or self.cancel.is_set():
                return
            if self.playback_error or self.error:
                raise ViduError(self.playback_error or self.error)
            log.info("Vidu realtime playback complete in %.2fs", time.monotonic() - started)
        except ViduError:
            raise
        except Exception:
            self.close()
            raise ViduError("实时语音发送失败，请重新连接数字人。") from None
        finally:
            with self.lock:
                if self.playback_id == request_id:
                    self.playback_id = None
                    self.touch()

    def stream(self, wav, text, valid, speaking):
        self.touch()
        if not valid():
            return
        if not self.status()["connected"]:
            raise ViduError("Vidu 数字人尚未连接，请在模型设置中连接。")
        pcm = pcm24(wav)
        sent = False
        try:
            self.signal(10, {"text_msg": {"msg_id": uuid.uuid4().hex, "content": text,
                                          "timestamp": int(time.time() * 1000)}})
            speaking()
            started = time.monotonic()
            for offset in range(0, len(pcm), 4800):
                while time.monotonic() < started + offset / 48000:
                    if not valid() or self.cancel.wait(0.01):
                        return
                if not valid():
                    return
                with self.lock:
                    if self.ws is None or not self.client_ready.is_set():
                        raise ViduError("Vidu 音视频连接已断开。")
                    self.ws.send(pcm[offset:offset + 4800])
                sent = True
            while time.monotonic() < started + len(pcm) / 48000:
                if not valid() or self.cancel.wait(0.01):
                    return
            log.info("Vidu PCM uplink complete: %.2fs", len(pcm) / 48000)
            self.touch()
        except Exception:
            self.error = "Vidu 音频发送失败，请重新连接数字人。"
            self.close()
            raise ViduError(self.error, audio_sent=sent) from None

    def interrupt(self):
        self.playback_id = None
        self.playback_complete.set()
        if self.ws is not None and self.initialized.is_set():
            try:
                self.signal(7)
            except Exception:
                self.close()

    def close(self):
        self.cancel.set()
        self.playback_id = None
        self.playback_complete.set()
        self.client_ready.clear()
        with self.lock:
            ws, self.ws = self.ws, None
            live_id = self.live_id
            self.live_id = None
        if ws is None and live_id:
            # A successful create followed by a failed WS handshake still needs hangup.
            path = (f"/live/ws/live/connect?live_id={live_id}" if self.mode == "realtime"
                    else f"/live/v1/external-lives/{live_id}/stream")
            try:
                ws = connect(f"wss://{API_HOST}{path}", additional_headers={
                    "Authorization": "Token " + os.environ.get("VIDU_API_KEY", "").strip()},
                    open_timeout=5, close_timeout=2, proxy=None)
                ws.send(json.dumps({"type": 1, "live_id": live_id, "seq_id": 0,
                                    "payload": {"conn_init": {"version": 1}}}))
            except Exception:
                log.warning("Vidu cleanup could not reconnect; live_id=%s", live_id)
        if ws is not None:
            try:
                self.seq += 1
                ws.send(json.dumps({"type": 5, "live_id": live_id, "seq_id": self.seq,
                    "payload": {"hangup": {"hangup_reason": "client_hangup"}}}))
            except Exception:
                log.warning("Vidu hangup signal failed; closing control socket")
            ws.close()
            log.info("Vidu S2 session closed")
