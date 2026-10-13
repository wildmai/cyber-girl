import io
import json
import queue
import threading
import time
import wave
from types import SimpleNamespace

import httpx
import numpy as np
import pytest

from app import vidu
from app.performance import PERFORMANCE_RULES
from app.vidu import ViduError, ViduSession, pcm24
from tests.test_avatar import service
from tests.test_pipeline import FakeLLM, FakeTTS, build_pipeline


def wav(rate=24000, count=240):
    output = io.BytesIO()
    with wave.open(output, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(rate)
        audio.writeframes(np.zeros(count, dtype="<i2").tobytes())
    return output.getvalue()


class FakeWS:
    def __init__(self, not_ready=False):
        self.messages = queue.Queue()
        self.sent = []
        self.closed = False
        self.not_ready = not_ready

    def send(self, value):
        self.sent.append((time.monotonic(), value))
        if isinstance(value, str) and json.loads(value)["type"] == 1:
            self.messages.put(json.dumps({"type": 2, "payload": {"conn_init_ack": {
                "success": not self.not_ready, "error_code": "NOT_READY" if self.not_ready else ""}}}))
            self.not_ready = False

    def recv(self, timeout):
        if self.closed:
            raise RuntimeError("closed")
        try:
            return self.messages.get(timeout=timeout)
        except queue.Empty:
            raise TimeoutError from None

    def close(self):
        self.closed = True


def mock_remote(monkeypatch, tmp_path, status=200, not_ready=False):
    monkeypatch.setenv("VIDU_API_KEY", "test-secret-never-visible")
    requests = []
    live_id = "9007199254740993123"

    def handle(request):
        requests.append(request)
        return httpx.Response(status, json={"live": {"id": live_id, "voice_model": {"provider": "doubao"}},
            "rtc": {"provider": "trtc", "app_id": "1234", "channel_id": "room-123",
                    "user_id": "viewer", "token": "short-lived-rtc-token"},
            "managed_rtc": {"provider": "trtc", "app_id": "1234", "channel_id": "room-123",
                            "user_id": "viewer", "token": "short-lived-rtc-token"}, "client_secret": "not-forwarded"})

    original = httpx.Client
    monkeypatch.setattr(vidu.httpx, "Client", lambda **kw: original(transport=httpx.MockTransport(handle), **kw))
    ws = FakeWS(not_ready)
    ws_requests = []

    def connect(url, **kw):
        ws_requests.append((url, kw))
        return ws

    monkeypatch.setattr(vidu, "connect", connect)
    portrait = tmp_path / "portrait.png"
    portrait.write_bytes(b"local-portrait")
    return portrait, requests, ws, ws_requests


def test_requires_server_key_before_network(monkeypatch, tmp_path):
    monkeypatch.delenv("VIDU_API_KEY", raising=False)
    with pytest.raises(ViduError, match="VIDU_API_KEY"):
        ViduSession().open(tmp_path / "missing.png")


def test_unjoined_session_expires_and_sends_hangup(monkeypatch, tmp_path):
    portrait, _, ws, _ = mock_remote(monkeypatch, tmp_path)
    session = ViduSession()
    notices = []
    session.on_expire = notices.append
    session.open(portrait, mode="realtime")
    assert not session.expire_if_needed(ws, session.opened_at + 44)
    assert session.expire_if_needed(ws, session.opened_at + 46)
    assert ws.closed and session.ws is None
    assert len(notices) == 1 and "连接超时" in notices[0]
    assert any(json.loads(raw).get("type") == 5 for _, raw in ws.sent if isinstance(raw, str))
    assert not session.expire_if_needed(ws, session.opened_at + 100)


def test_silent_connection_expires_but_current_reply_does_not(monkeypatch, tmp_path):
    portrait, _, ws, _ = mock_remote(monkeypatch, tmp_path)
    session = ViduSession()
    session.open(portrait, mode="realtime")
    session.ready(session.live_id)
    try:
        assert not session.expire_if_needed(ws, session.last_activity + 9.9)
        session.playback_id = "reply-in-progress"
        assert not session.expire_if_needed(ws, session.last_activity + 100)
        session.playback_id = None
        session.is_busy = lambda: True
        assert not session.expire_if_needed(ws, session.last_activity + 100), "Queued CPU recognition is an active turn"
        session.is_busy = lambda: False
        assert session.expire_if_needed(ws, session.last_activity + 10.1)
        assert ws.closed and "没有有效对话" in session.error
        assert session.expired_idle
    finally:
        session.close()


def test_reconnect_wait_preserves_the_current_turn(monkeypatch, tmp_path):
    portrait, _, _, _ = mock_remote(monkeypatch, tmp_path)
    session = ViduSession()
    session.error = "previous idle expiration"
    session.touch()
    assert session.error is None
    result = []
    worker = threading.Thread(target=lambda: result.append(session.wait_ready(lambda: True)))
    worker.start()
    try:
        session.open(portrait, mode="realtime")
        session.ready(session.live_id)
        worker.join(2)
        assert result == [True]
        assert session.wait_ready(lambda: False) is False
    finally:
        session.close()


def test_creation_followed_by_handshake_failure_still_hangs_up(monkeypatch, tmp_path):
    portrait, _, ws, _ = mock_remote(monkeypatch, tmp_path)
    attempts = []
    def flaky_connect(*args, **kwargs):
        attempts.append(True)
        if len(attempts) == 1:
            raise TimeoutError("simulated handshake failure")
        return ws
    monkeypatch.setattr(vidu, "connect", flaky_connect)
    session = ViduSession()
    with pytest.raises(ViduError, match="连接失败"):
        session.open(portrait, mode="realtime")
    assert len(attempts) == 2
    assert ws.closed
    assert any(json.loads(raw).get("type") == 5 for _, raw in ws.sent)


def test_component_init_uses_s2_and_server_auth_then_client_ack(monkeypatch, tmp_path):
    portrait, requests, ws, ws_requests = mock_remote(monkeypatch, tmp_path)
    session = ViduSession()
    try:
        rtc = session.open(portrait)
        payload = json.loads(requests[0].content)
        assert payload == {"model": "vidu-s2", "image_uri": "data:image/png;base64,bG9jYWwtcG9ydHJhaXQ=",
                           "managed_rtc": {"provider": "trtc"}}
        assert requests[0].url == "https://api.vidu.cn/live/s_avatar/component"
        assert requests[0].headers["Authorization"] == "Token test-secret-never-visible"
        assert "9007199254740993123" in ws_requests[0][0]
        assert "secret" not in ws_requests[0][0]
        assert ws_requests[0][1]["additional_headers"]["Authorization"].startswith("Token ")
        assert json.loads(ws.sent[0][1])["type"] == 1
        assert "test-secret" not in json.dumps(rtc) + json.dumps(session.status())
        assert "client_secret" not in rtc
        assert not session.status()["connected"]
        session.ready("wrong-id")
        assert not session.status()["connected"]
        session.ready(session.live_id)
        assert session.status()["connected"]
    finally:
        session.close()
    assert ws.closed


def test_not_ready_retries_init_on_same_connection(monkeypatch, tmp_path):
    portrait, _, ws, calls = mock_remote(monkeypatch, tmp_path, not_ready=True)
    session = ViduSession()
    try:
        session.open(portrait)
        assert len(calls) == 1
        assert [json.loads(raw)["type"] for _, raw in ws.sent] == [1, 1]
    finally:
        session.close()


def test_realtime_uses_doubao_and_waits_for_matching_playback_ack(monkeypatch, tmp_path):
    portrait, requests, ws, calls = mock_remote(monkeypatch, tmp_path)
    session = ViduSession()
    try:
        session.open(portrait, mode="realtime", persona="你叫小月，是一位沉稳的天文学家。")
        payload = json.loads(requests[0].content)
        assert requests[0].url.path == "/live/s_avatar/realtime"
        assert payload["voice_model"] == {"provider": "doubao"}
        assert payload["rtc"] == {"provider": "trtc"}
        assert payload["avatar"]["image_uri"].startswith("data:image/png;base64,")
        assert payload["avatar"]["persona"] == "你叫小月，是一位沉稳的天文学家。"
        assert payload["idle_motion"]["source"] == "custom"
        assert "/live/ws/live/connect?live_id=" in calls[0][0]
        session.ready(session.live_id)
        started = threading.Event()
        notices = []

        def speaking(msg_id):
            notices.append(msg_id)
            started.set()

        worker = threading.Thread(target=session.speak, args=("今天辛苦了。", lambda: True, speaking),
                                  kwargs={"action": "收起笑容，目光关切，轻轻点头一次，然后自然放松。"})
        worker.start()
        assert started.wait(2)
        session.playback_done("wrong-live", notices[0])
        session.playback_done(session.live_id, "stale-reply")
        worker.join(0.05)
        assert worker.is_alive()
        session.playback_done(session.live_id, notices[0])
        worker.join(2)
        assert not worker.is_alive()
        signals = [json.loads(raw) for _, raw in ws.sent if isinstance(raw, str)]
        speech = next(s for s in signals if s["type"] == 100)
        assert speech["payload"]["text_tts"] == {"msg_id": notices[0], "content": "今天辛苦了。"}
        action = next(s for s in signals if s["type"] == 101)
        assert action["payload"]["text_action"] == {"msg_id": notices[0],
            "content": "收起笑容，目光关切，轻轻点头一次，然后自然放松。"}
        assert action["seq_id"] < speech["seq_id"]
        assert not any(isinstance(raw, bytes) for _, raw in ws.sent)
    finally:
        session.close()


def test_realtime_interrupt_releases_pending_speech(monkeypatch, tmp_path):
    portrait, _, _, _ = mock_remote(monkeypatch, tmp_path)
    session = ViduSession()
    session.open(portrait, mode="realtime")
    session.ready(session.live_id)
    started = threading.Event()
    worker = threading.Thread(target=session.speak, args=("旧回复", lambda: True, lambda _: started.set()))
    try:
        worker.start()
        assert started.wait(2)
        session.interrupt()
        worker.join(2)
        assert not worker.is_alive()
    finally:
        session.close()


def test_realtime_server_error_releases_wait_without_exposing_payload(monkeypatch, tmp_path):
    portrait, _, ws, _ = mock_remote(monkeypatch, tmp_path)
    session = ViduSession()
    session.open(portrait, mode="realtime")
    session.ready(session.live_id)
    started = threading.Event()
    errors = []

    def run():
        try:
            session.speak("你好", lambda: True, lambda _: started.set())
        except ViduError as exc:
            errors.append(str(exc))

    worker = threading.Thread(target=run)
    try:
        worker.start()
        assert started.wait(2)
        ws.messages.put(json.dumps({"type": 103, "payload": {"error": "private-provider-details"}}))
        worker.join(2)
        assert not worker.is_alive()
        assert errors and "豆包" in errors[0]
        assert "private-provider" not in errors[0]
    finally:
        session.close()


def test_realtime_pipeline_sends_one_reply_without_local_tts():
    requests = []

    class PerformingLLM:
        def create_chat_completion(self, **kwargs):
            requests.append(kwargs["messages"])
            raw = json.dumps({"speech": "今天辛苦了。我在这里。", "action": "目光柔和，轻轻点头后自然放松。"}, ensure_ascii=False)
            for part in ("<think>hidden</think>", raw[:30], raw[30:]):
                yield {"choices": [{"delta": {"content": part}}]}

    pipeline, events, done = build_pipeline({"llm": PerformingLLM()})
    spoken = []
    pipeline.manager.avatar = SimpleNamespace(enabled=True, provider="vidu", realtime=True,
        vidu=SimpleNamespace(status=lambda: {"connected": True}, interrupt=lambda: None,
                             speak=lambda text, valid, speaking, action: (spoken.append((text, action)), speaking("reply-1"))))
    try:
        pipeline.history.extend([{"role": "user", "content": "今天面试没通过"},
                                 {"role": "assistant", "content": "听起来你很失落。"}])
        pipeline.manager.persona.system_prompt = lambda: "你是沉稳寡言的小月。"
        pipeline.text("我真是太开心了，才怪。")
        assert done.wait(5)
        assert spoken == [("今天辛苦了。我在这里。", "目光柔和，轻轻点头后自然放松。")]
        assert requests[0][0]["content"].startswith("你是沉稳寡言的小月。")
        assert PERFORMANCE_RULES in requests[0][0]["content"]
        assert requests[0][1]["content"] == "今天面试没通过"
        assert json.loads(requests[0][2]["content"]) == {"speech": "听起来你很失落。", "action": ""}
        assert requests[0][-1]["content"] == "我真是太开心了，才怪。"
        assert len(requests) == 1  # No extra planning round trip.
        assert "".join(e["text"] for e in events if e["type"] == "token") == spoken[0][0]
        assert list(pipeline.history)[-1]["content"] == spoken[0][0]
        assert list(pipeline.history)[-1]["action"] == spoken[0][1]
        assert any(e["type"] == "vidu_speaking" and e["request_id"] == "reply-1" for e in events)
        assert next(e for e in events if e["type"] == "vidu_speaking")["text"] == spoken[0][0]
        assert not any(e["type"] in ("audio", "avatar", "error") for e in events)
        done.clear()
        pipeline.text("你能陪我聊聊吗")
        assert done.wait(5)
        previous = json.loads(requests[1][-2]["content"])
        assert previous == {"speech": spoken[0][0], "action": spoken[0][1]}
        assert all(set(m) == {"role", "content"} for m in requests[1])

        # Switching back to ordinary speech must not inherit structured output.
        pipeline.manager.avatar.realtime = False
        pipeline.manager.loaded["llm"] = FakeLLM()
        done.clear()
        pipeline.text("继续聊")
        assert done.wait(5)
        assert [e for e in events if e["type"] == "assistant"][-1]["text"] == "今天辛苦了。我在这里。"
    finally:
        pipeline.close()


@pytest.mark.parametrize("raw", ['{"speech":"你好","action":""}', '{"speech":"你好"}',
                                 '```json\n{"speech":"你好","action":"微笑"}\n```',
                                 '{"speech":"你好","action":"微笑","extra":"private"}'])
def test_invalid_performance_never_reaches_speech_or_captions(raw):
    class InvalidLLM:
        def create_chat_completion(self, **kwargs):
            yield {"choices": [{"delta": {"content": raw}}]}

    pipeline, events, done = build_pipeline({"llm": InvalidLLM()})
    pipeline.manager.avatar = SimpleNamespace(realtime=True, provider="vidu",
        vidu=SimpleNamespace(status=lambda: {"connected": True}, interrupt=lambda: None,
                             speak=lambda *args, **kw: pytest.fail("invalid speech")))
    try:
        pipeline.text("你好")
        assert done.wait(5)
        assert any(e["type"] == "error" and "规划格式" in e["message"] for e in events)
        assert not any(e["type"] in ("token", "assistant", "audio", "vidu_speaking") for e in events)
        assert not pipeline.history
    finally:
        pipeline.close()


def test_stale_realtime_plan_sends_neither_action_nor_speech():
    session = ViduSession()
    ws = FakeWS()
    session.ws, session.live_id, session.mode = ws, "123", "realtime"
    session.client_ready.set()
    try:
        session.speak("旧台词", lambda: False, lambda _: pytest.fail("stale playback"), action="旧动作")
        assert ws.sent == []
    finally:
        session.close()


def test_remote_failure_never_exposes_response_or_credentials(monkeypatch, tmp_path):
    portrait, _, _, _ = mock_remote(monkeypatch, tmp_path, status=401)
    session = ViduSession()
    with pytest.raises(ViduError, match="HTTP 401") as error:
        session.open(portrait)
    assert "secret" not in str(error.value) + json.dumps(session.status())
    assert not session.connecting and session.ws is None


def test_pcm_uplink_is_resampled_paced_and_stoppable():
    assert len(pcm24(wav(16000, 1600))) == 4800
    session = ViduSession()
    ws = FakeWS()
    session.ws = ws
    session.live_id = "123"
    session.initialized.set()
    session.client_ready.set()
    speaking = []
    try:
        session.stream(wav(count=6000), "你好", lambda: True, lambda: speaking.append(True))
        audio = [(stamp, raw) for stamp, raw in ws.sent if isinstance(raw, bytes)]
        assert [len(raw) for _, raw in audio] == [4800, 4800, 2400]
        assert audio[1][0] - audio[0][0] >= 0.08
        assert json.loads(ws.sent[0][1])["payload"]["text_msg"]["content"] == "你好"
        assert speaking == [True]
        session.interrupt()
        assert json.loads(ws.sent[-1][1])["type"] == 7
        count = len(ws.sent)
        session.stream(wav(), "旧回复", lambda: False, lambda: pytest.fail("stale playback"))
        assert len(ws.sent) == count
    finally:
        session.close()


def test_disconnect_during_initialization_cancels_pending_open(monkeypatch, tmp_path):
    portrait, _, ws, _ = mock_remote(monkeypatch, tmp_path)
    ws.send = lambda value: ws.sent.append((time.monotonic(), value))
    session = ViduSession()
    errors = []

    def run():
        try:
            session.open(portrait)
        except ViduError as exc:
            errors.append(str(exc))

    thread = threading.Thread(target=run)
    thread.start()
    deadline = time.monotonic() + 2
    while session.ws is None and time.monotonic() < deadline:
        time.sleep(0.01)
    session.close()
    thread.join(2)
    assert not thread.is_alive() and errors
    assert ws.closed and session.ws is None


def test_cloud_switch_releases_local_runtime_and_removes_gpu_estimate(tmp_path):
    avatar = service(tmp_path)
    avatar.runtime = object()
    avatar.set_enabled(True, "vidu")
    assert avatar.provider == "vidu" and avatar.estimated_gib == 0
    assert avatar.runtime is None
    assert avatar.status()["name"] == "Vidu S2"
    assert not avatar.status()["vidu"]["connected"]
    assert "VIDU_API_KEY" not in avatar.config_path.read_text()


@pytest.mark.parametrize("partial", [False, True])
def test_vidu_failure_falls_back_only_before_any_remote_audio(partial):
    pipeline, events, done = build_pipeline({"llm": FakeLLM(), "tts": FakeTTS()})

    def fail(*args):
        raise ViduError("测试断连", audio_sent=partial)

    pipeline.manager.avatar = SimpleNamespace(enabled=True, provider="vidu",
        vidu=SimpleNamespace(stream=fail, interrupt=lambda: None))
    try:
        pipeline.text("你好")
        assert done.wait(5)
        audio = [e for e in events if e["type"] == "audio"]
        assert len(audio) == (0 if partial else 2)
        assert any(e["type"] == "vidu_stopped" for e in events)
        assert not any(e["type"] == "avatar" for e in events)
    finally:
        pipeline.close()


def test_component_captions_include_each_spoken_chunk_and_audio_duration():
    pipeline, events, done = build_pipeline({"llm": FakeLLM(), "tts": FakeTTS()})

    def stream(wav, text, valid, speaking):
        assert valid()
        speaking()

    pipeline.manager.avatar = SimpleNamespace(enabled=True, provider="vidu",
        vidu=SimpleNamespace(stream=stream, interrupt=lambda: None))
    try:
        pipeline.text("你好")
        assert done.wait(5)
        chunks = [e for e in events if e["type"] == "vidu_speaking"]
        assert [e["text"] for e in chunks] == ["今天辛苦了。", "我在这里。"]
        assert all(e["duration"] == 0.01 for e in chunks)
    finally:
        pipeline.close()
