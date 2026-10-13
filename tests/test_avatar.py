import io
import json
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from app.avatar import AvatarService
from tests.test_pipeline import FakeLLM, FakeTTS, build_pipeline


def service(tmp_path, estimate=5.6):
    manager = SimpleNamespace(lock=threading.RLock(), selected={}, config={"budget_gib": 15},
                              estimate=lambda selection: estimate)
    avatar = AvatarService(manager, tmp_path / "avatar.json")
    avatar.media_dir = tmp_path / "media"
    avatar.model_dir = tmp_path / "models"
    return avatar


def test_enable_requires_weights_and_preserves_setting_on_failure(tmp_path):
    avatar = service(tmp_path)
    with pytest.raises(ValueError, match="准备完成"):
        avatar.set_enabled(True)
    assert not avatar.enabled
    assert avatar.runtime is None


def test_avatar_is_included_in_budget_and_disable_releases_model(tmp_path, monkeypatch):
    avatar = service(tmp_path, estimate=12)
    monkeypatch.setattr(avatar, "status", lambda: {"available": True, "providers": [{"available": True}]})
    with pytest.raises(ValueError, match="显存预算"):
        avatar.set_enabled(True)
    assert not avatar.enabled
    avatar.manager.estimate = lambda selection: 5.6
    avatar.set_enabled(True)
    assert avatar.enabled
    avatar.runtime = object()
    avatar.set_enabled(False)
    assert avatar.runtime is None
    assert not json.loads(avatar.config_path.read_text())["enabled"]


def test_portrait_is_validated_and_saved_only_to_local_config_directory(tmp_path):
    avatar = service(tmp_path)
    with pytest.raises(ValueError, match="有效"):
        avatar.set_portrait(b"not an image")
    data = io.BytesIO()
    Image.new("RGB", (120, 160)).save(data, format="PNG")
    result = avatar.set_portrait(data.getvalue())
    assert result["portrait_url"] == "/api/avatar/portrait"
    assert avatar.portrait.parent == tmp_path
    assert Image.open(avatar.portrait).size == (120, 160)
    avatar.set_portrait()
    assert avatar.portrait.name == "xiaoman.png"


def test_clip_access_rejects_traversal_and_retention_is_bounded(tmp_path):
    avatar = service(tmp_path)
    avatar.media_dir.mkdir()
    for index in range(27):
        (avatar.media_dir / f"{index:032x}.mp4").write_bytes(b"video")
    avatar.cleanup()
    clips = list(avatar.media_dir.glob("*.mp4"))
    assert len(clips) == 24
    assert avatar.clip_path(clips[0].name) == clips[0]
    assert avatar.clip_path("../avatar.json") is None
    assert avatar.clip_path("0" * 32 + ".wav") is None
    assert avatar.clip_path("f" * 32 + ".mp4") is None


def test_avatar_events_replace_audio_and_include_fallback_waveform():
    pipeline, events, done = build_pipeline({"llm": FakeLLM(), "tts": FakeTTS()})

    def render(wav, valid, progress):
        assert wav.startswith(b"RIFF") and valid()
        progress(50)
        return "/api/avatar/clips/" + "a" * 32 + ".mp4"

    pipeline.manager.avatar = SimpleNamespace(enabled=True, render=render)
    try:
        pipeline.text("你好")
        assert done.wait(5)
        videos = [event for event in events if event["type"] == "avatar"]
        assert len(videos) == 2
        assert all(event["audio"] for event in videos)
        assert [event["text"] for event in videos] == ["今天辛苦了。", "我在这里。"]
        assert not any(event["type"] == "audio" for event in events)
        assert any(event.get("state") == "animating" and event["progress"] == 50 for event in events)
    finally:
        pipeline.close()


def test_avatar_failure_keeps_voice_reply_available():
    pipeline, events, done = build_pipeline({"llm": FakeLLM(), "tts": FakeTTS()})

    def fail(*args):
        raise RuntimeError("face detection failed")

    pipeline.manager.avatar = SimpleNamespace(enabled=True, render=fail)
    try:
        pipeline.text("你好")
        assert done.wait(5)
        assert len([event for event in events if event["type"] == "audio"]) == 2
        assert any(event["type"] == "warning" for event in events)
        assert not any(event["type"] == "error" for event in events)
    finally:
        pipeline.close()


def test_interrupt_discards_video_generated_for_old_reply():
    pipeline, events, done = build_pipeline({"llm": FakeLLM(), "tts": FakeTTS()})
    entered = threading.Event()
    release = threading.Event()

    def render(wav, valid, progress):
        entered.set()
        release.wait(5)
        assert not valid()
        return "/api/avatar/clips/" + "a" * 32 + ".mp4"

    pipeline.manager.avatar = SimpleNamespace(enabled=True, render=render)
    try:
        pipeline.text("你好")
        assert entered.wait(3)
        pipeline.interrupt()
        release.set()
    finally:
        release.set()
        pipeline.close()
    assert not any(event["type"] in ("avatar", "audio") for event in events)


def test_cancelled_render_removes_all_partial_media(tmp_path, monkeypatch):
    class Cancelled(RuntimeError):
        pass

    monkeypatch.setitem(sys.modules, "app.avatar_runtime", SimpleNamespace(Cancelled=Cancelled, MuseTalkRuntime=object))
    avatar = service(tmp_path)
    avatar.config["enabled"] = True
    monkeypatch.setattr(avatar, "status", lambda: {"available": True})

    class CancelRuntime:
        def render(self, photo, audio, output, ffmpeg, valid, progress):
            Path(output).write_bytes(b"partial")
            Path(output).with_suffix(".silent.mp4").write_bytes(b"partial")
            raise Cancelled()

    avatar.runtime = CancelRuntime()
    assert avatar.render(b"wav", lambda: True, lambda percent: None) is None
    assert list(avatar.media_dir.iterdir()) == []
