import base64
import io
import threading
import wave
from types import SimpleNamespace

import numpy as np
import pytest

from app.models import ModelError
from app.persona import DEFAULT_PERSONA, RESPONSE_RULES
from app.pipeline import Pipeline, VisibleText, Work


def test_reasoning_tags_split_across_tokens_do_not_leak():
    parser = VisibleText()
    parts = ["<thi", "nk>secret", " private thought", "</th", "ink>", "你好", "。"]
    assert "".join(parser.feed(s) for s in parts) == "你好。"


def test_plain_reply_and_incomplete_non_tag_preserved():
    parser = VisibleText()
    assert parser.feed("hello ") + parser.feed("<3", final=True) == "hello <3"


def test_continuous_speech_keeps_cloud_session_active_after_speech_start():
    class OngoingVAD:
        triggered = True
        active_speech_samples = 512
        def __call__(self, tensor):
            return None

    touched = []
    pipeline, events, _ = build_pipeline({"vad": OngoingVAD()})
    pipeline.manager.avatar = SimpleNamespace(realtime=False,
        vidu=SimpleNamespace(touch=lambda: touched.append(True)))
    pipeline.vad_generation = pipeline.generation
    try:
        pipeline._vad(Work(pipeline.generation, bytes(1024)))
        pipeline._vad(Work(pipeline.generation, bytes(1024)))
        assert len(touched) == 2
        assert not any(e.get("state") == "hearing" for e in events)
    finally:
        pipeline.close()


class FakeLLM:
    def create_chat_completion(self, **kwargs):
        assert kwargs["stream"]
        for part in ("<think>hidden</think>", "今天", "辛苦了。", "我在这里。"):
            yield {"choices": [{"delta": {"content": part}}]}


class FakeTTS:
    def generate_custom_voice(self, **kwargs):
        assert kwargs["speaker"] == "Vivian"
        return [np.zeros(240, dtype=np.float32)], 24000


def build_pipeline(loaded):
    events = []
    done = threading.Event()
    manager = SimpleNamespace(loaded=loaded, lock=threading.RLock(), config={"device": "cpu"}, device="cpu",
                              persona=SimpleNamespace(system_prompt=lambda: DEFAULT_PERSONA + "\n\n回复要求：" + RESPONSE_RULES))

    def emit(event):
        events.append(event)
        if event["type"] in ("done", "error"):
            done.set()

    return Pipeline(manager, emit), events, done


def test_queue_cascade_streams_visible_reply_and_valid_wav():
    pipeline, events, done = build_pipeline({"llm": FakeLLM(), "tts": FakeTTS()})
    try:
        pipeline.text("今天有点累")
        assert done.wait(5), events
        assert not any(e["type"] == "error" for e in events)
        answer = next(e["text"] for e in events if e["type"] == "assistant")
        assert answer == "今天辛苦了。我在这里。"
        audio = [e for e in events if e["type"] == "audio"]
        assert len(audio) == 2
        assert [e["text"] for e in audio] == ["今天辛苦了。", "我在这里。"]
        assert next(e for e in events if e.get("state") == "thinking")["speech"] is True
        states = [e["state"] for e in events if e["type"] == "state"]
        assert "synthesizing" in states
        assert "speaking" not in states  # Only the browser knows when playback actually starts.
        with wave.open(io.BytesIO(base64.b64decode(audio[0]["data"])), "rb") as wav:
            assert wav.getframerate() == 24000
            assert wav.getnchannels() == 1
        assert list(pipeline.history)[-1]["content"] == answer
        assert events[-1]["type"] == "done"
    finally:
        pipeline.close()


def test_each_llm_turn_uses_current_persona(tmp_path):
    from app.persona import PersonaConfig, PersonaStore
    requests = []

    class CapturingLLM(FakeLLM):
        def create_chat_completion(self, **kwargs):
            requests.append(kwargs["messages"])
            yield from super().create_chat_completion(**kwargs)

    pipeline, events, done = build_pipeline({"llm": CapturingLLM()})
    store = PersonaStore(tmp_path / "persona.json")
    pipeline.manager.persona = store
    try:
        pipeline.text("介绍一下你自己")
        assert done.wait(5), events
        assert requests[0][0]["content"].startswith(DEFAULT_PERSONA)
        assert next(e for e in events if e.get("state") == "thinking")["speech"] is False
        store.save(PersonaConfig(prompt="你叫小月，喜欢天文学。"))
        pipeline.history.clear()
        done.clear()
        pipeline.text("你叫什么名字")
        assert done.wait(5), events
        assert requests[1][0] == {"role": "system", "content": store.system_prompt()}
        assert "小满" not in requests[1][0]["content"]
        assert len(requests[1]) == 2
    finally:
        pipeline.close()


def test_interrupt_discards_inflight_response():
    entered = threading.Event()
    release = threading.Event()

    class SlowLLM:
        def create_chat_completion(self, **kwargs):
            entered.set()
            release.wait(5)
            yield {"choices": [{"delta": {"content": "过期回复。"}}]}

    pipeline, events, done = build_pipeline({"llm": SlowLLM(), "tts": FakeTTS()})
    try:
        pipeline.text("第一句")
        assert entered.wait(2)
        pipeline.interrupt()
        release.set()
        pipeline.enqueue("tts", Work(pipeline.generation, None))
        assert done.wait(2)
        assert not any(e["type"] in ("audio", "token", "assistant") for e in events)
        assert not pipeline.history
    finally:
        release.set()
        pipeline.close()


def test_model_missing_and_malformed_audio_fail_explicitly():
    pipeline, events, done = build_pipeline({})
    try:
        with pytest.raises(ModelError, match="尚未连接"):
            pipeline.text("你好")
        with pytest.raises(ModelError, match="512"):
            pipeline.audio(b"bad")
    finally:
        pipeline.close()


def test_local_vad_keeps_receiving_pcm_while_cloud_avatar_is_paused(monkeypatch):
    pipeline, _, _ = build_pipeline({"vad": object(), "stt": object(), "llm": FakeLLM()})
    pipeline.manager.avatar = SimpleNamespace(realtime=True, provider="vidu",
        vidu=SimpleNamespace(status=lambda: {"connected": False}, interrupt=lambda: None))
    queued = []
    monkeypatch.setattr(pipeline, "enqueue", lambda stage, work: queued.append((stage, work)))
    try:
        raw = bytes(1024)
        pipeline.audio(raw)
        assert len(queued) == 1 and queued[0][0] == "vad"
        assert queued[0][1].value == raw
    finally:
        pipeline.close()
