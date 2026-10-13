import json
import threading
from types import SimpleNamespace

import httpx
import pytest

from app.local_llm import LOCAL_MODEL, LlamaEngine, LocalLLM, LocalLLMError
from app.models import ModelManager
from scripts.configure_mac import configure


class FakeEngine:
    def __init__(self):
        self.lock = threading.RLock()
        self.base_url = "http://127.0.0.1:12345"
        self.low_memory = True
        self.events = []

    def start(self, cancelled):
        self.events.append("start")

    def stop(self):
        self.events.append("stop")


def test_local_sse_is_keyless_and_releases_memory_after_reply():
    engine = FakeEngine()

    def respond(request):
        assert str(request.url) == engine.base_url + "/v1/chat/completions"
        assert "authorization" not in request.headers
        payload = json.loads(request.content)
        assert payload["chat_template_kwargs"] == {"enable_thinking": False}
        assert payload["messages"][0]["content"] == "你叫小满"
        return httpx.Response(200, text='data: {"choices":[{"delta":{"reasoning_content":"secret"}}]}\n\n'
            'data: {"choices":[{"delta":{"content":"你好呀。"}}]}\n\n'
            'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n')

    model = LocalLLM(engine=engine, client=httpx.Client(transport=httpx.MockTransport(respond)))
    try:
        output = list(model.create_chat_completion(messages=[{"role": "system", "content": "你叫小满"}]))
        assert output == [{"choices": [{"delta": {"content": "你好呀。"}}]}]
        assert engine.events == ["start", "stop"]
    finally:
        model.close()


@pytest.mark.parametrize("content", ['data: {"choices":[{"delta":{"content":"半句"}}]}\n\n',
                                   'data: broken\n\n', 'data: {"error":"failed"}\n\n'])
def test_invalid_or_incomplete_response_fails_and_stops_engine(content):
    engine = FakeEngine()
    model = LocalLLM(engine=engine, client=httpx.Client(transport=httpx.MockTransport(
        lambda _: httpx.Response(200, text=content))))
    try:
        with pytest.raises(LocalLLMError):
            list(model.create_chat_completion(messages=[]))
        assert engine.events == ["start", "stop"]
    finally:
        model.close()


def test_cancelled_stream_drops_remaining_tokens_without_affecting_other_clients():
    engine = FakeEngine()
    text = 'data: {"choices":[{"delta":{"content":"一句"}}]}\n\n' * 3 + 'data: [DONE]\n\n'
    model = LocalLLM(engine=engine, client=httpx.Client(transport=httpx.MockTransport(
        lambda _: httpx.Response(200, text=text))))
    other = model.fork()
    try:
        stream = model.create_chat_completion(messages=[])
        next(stream)
        model.cancel()
        assert list(stream) == []
        assert not other.cancelled.is_set() and not other.closed
        assert engine.events == ["start", "stop"]
        model.close()
        assert not other.closed
    finally:
        other.close()


def test_owned_server_is_loopback_only_and_is_terminated(monkeypatch, tmp_path):
    import app.local_llm as module
    path = tmp_path / "model.gguf"
    path.touch()
    monkeypatch.setattr(module, "server_binary", lambda: str(tmp_path / "llama-server"))
    commands = []
    terminated = []
    process = SimpleNamespace(pid=4321, poll=lambda: None,
        terminate=lambda: terminated.append(True), wait=lambda timeout: 0)
    monkeypatch.setattr(module.subprocess, "Popen", lambda command, **kwargs: commands.append(command) or process)

    class HealthClient:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def get(self, url):
            assert url.startswith("http://127.0.0.1:") and url.endswith("/health")
            return SimpleNamespace(status_code=200)

    monkeypatch.setattr(module.httpx, "Client", lambda **kwargs: HealthClient())
    engine = LlamaEngine(path, low_memory=True)
    engine.start(threading.Event())
    command = commands[0]
    assert command[command.index("--host") + 1] == "127.0.0.1"
    assert command[command.index("-c") + 1] == "4096"
    assert "-hf" not in command and "--model-url" not in command
    engine.stop()
    assert terminated == [True] and engine.process is None


def test_mac_profile_is_local_and_preserves_user_settings(tmp_path):
    (tmp_path / "config").mkdir()
    source = {"models": [LOCAL_MODEL], "selected": {}, "device": "auto"}
    (tmp_path / "config/models.json").write_text(json.dumps(source))
    path = configure(tmp_path)
    config = json.loads(path.read_text())
    assert config["selected"]["llm"] == LOCAL_MODEL["id"]
    assert config["low_memory"] and config["device"] == "cpu"
    assert config["reserve_gib"] == 2.5
    assert json.loads(path.with_name("avatar.json").read_text())["provider"] == "musetalk"
    config["budget_gib"] = 13
    path.write_text(json.dumps(config))
    configure(tmp_path)
    assert json.loads(path.read_text())["budget_gib"] == 13


def test_local_weights_can_be_selected_without_api_key(monkeypatch, tmp_path):
    # A real manager and loader dispatch, with only the native engine mocked.
    import app.models as module
    weight = tmp_path / "local.gguf"
    weight.touch()
    spec = {**LOCAL_MODEL, "path": str(weight)}
    speech = [{"id": s, "stage": s, "path": str(weight), "vram_gib": 0.1}
              for s in ("vad", "stt", "tts")]
    path = tmp_path / "models.json"
    path.write_text(json.dumps({"models": [spec, *speech], "selected": {
        "llm": spec["id"], "vad": "vad", "stt": "stt", "tts": "tts"},
        "device": "cpu", "budget_gib": 14, "reserve_gib": 2.5, "low_memory": True}))
    monkeypatch.delenv("MINIMAX_API_KEY", raising=False)
    monkeypatch.setattr(module, "server_binary", lambda: "llama-server")
    monkeypatch.setattr(module, "LocalLLM", lambda *args, **kwargs: SimpleNamespace(close=lambda: None))
    manager = ModelManager(path)
    result = manager.switch({}, llm_only=True)
    assert result["llm_api"]["local"] and result["offline"]
    assert list(manager.loaded) == ["llm"]
    manager.avatar.config["enabled"] = True
    assert manager.estimate(manager.selected) == 9.3
    manager.close()


def test_low_memory_pipeline_releases_llm_before_speech_and_avatar():
    from tests.test_pipeline import FakeTTS, build_pipeline
    engine = FakeEngine()
    text = ('data: {"choices":[{"delta":{"content":"我在这里。今天开心吗？"}}]}\n\n'
            'data: [DONE]\n\n')
    model = LocalLLM(engine=engine, client=httpx.Client(transport=httpx.MockTransport(
        lambda _: httpx.Response(200, text=text))))

    class CheckedTTS(FakeTTS):
        def generate_custom_voice(self, **kwargs):
            assert engine.events == ["avatar-released", "start", "stop"]
            return super().generate_custom_voice(**kwargs)

    pipeline, events, done = build_pipeline({"llm": model, "tts": CheckedTTS()})
    pipeline.manager.config["low_memory"] = True
    pipeline.manager.avatar = SimpleNamespace(enabled=False, realtime=False,
        release_local=lambda: engine.events.append("avatar-released"))
    try:
        pipeline.text("你好")
        assert done.wait(5), events
        assert not any(e["type"] == "error" for e in events)
        assert len([e for e in events if e["type"] == "audio"]) == 2
    finally:
        pipeline.close()
        model.close()


def test_session_local_clients_share_engine_but_have_private_cancellation(tmp_path):
    from app.avatar import AvatarService
    from app.persona import PersonaStore
    from app.sessions import SessionModels, new_token
    model = LocalLLM(engine=FakeEngine())
    shared = SimpleNamespace(config_path=tmp_path / "models.json", selected={},
        lock=threading.RLock(), loaded={"llm": model}, persona=PersonaStore(tmp_path / "persona.json"))
    shared.avatar = AvatarService(shared, tmp_path / "avatar.json")
    one, two = SessionModels(shared, new_token()), SessionModels(shared, new_token())
    try:
        assert one.loaded["llm"].engine is two.loaded["llm"].engine is model.engine
        one.loaded["llm"].cancel()
        assert not two.loaded["llm"].cancelled.is_set()
        one.close()
        assert not two.loaded["llm"].closed
    finally:
        two.close()
        model.close()
