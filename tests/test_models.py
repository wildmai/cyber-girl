import json

import pytest

from app.models import ModelError, ModelManager


class FakeModel:
    def __init__(self, model_id, events):
        self.id = model_id
        self.events = events

    def close(self):
        self.events.append(("close", self.id))


@pytest.fixture
def manager(tmp_path):
    models = []
    for stage in ("vad", "stt", "llm", "tts"):
        for index in (1, 2):
            path = tmp_path / f"{stage}{index}"
            if stage == "tts":
                path.mkdir()
                (path / "config.json").write_text("{}")
                (path / "model.safetensors").touch()
            else:
                path.touch()
            models.append({"id": f"{stage}{index}", "stage": stage, "name": stage,
                           "path": str(path), "vram_gib": 1, "detail": "test"})
    config = {"budget_gib": 15, "reserve_gib": 1.5, "device": "cpu", "context_length": 4096,
              "selected": {s: f"{s}1" for s in ("vad", "stt", "llm", "tts")}, "models": models}
    config_path = tmp_path / "models.json"
    config_path.write_text(json.dumps(config))
    events = []

    def loader(spec):
        events.append(("load", spec["id"]))
        return FakeModel(spec["id"], events)

    result = ModelManager(config_path, loader=loader)
    result.events = events
    result._check_memory = lambda: None
    return result


def test_hot_switch_releases_old_before_loading_new(manager):
    manager.switch({})
    manager.events.clear()
    manager.switch({"llm": "llm2"})
    assert manager.events == [("close", "llm1"), ("load", "llm2")]
    assert manager.selected["llm"] == "llm2"
    assert json.loads(manager.config_path.read_text())["selected"]["llm"] == "llm2"


def test_realtime_mode_releases_tts_and_loads_only_local_recognition(manager):
    manager.switch({})
    manager.avatar.set_enabled(True, "vidu", "realtime")
    assert "tts" not in manager.loaded
    assert manager.avatar.realtime
    manager.events.clear()
    manager.catalog["tts1"]["path"] = "missing-tts-weights"
    manager.switch({})
    assert manager.status()["ready"]
    assert manager.estimate(manager.selected) == 4.5
    assert not manager.events
    manager.avatar.set_enabled(True, "vidu", "standard")
    assert not manager.status()["ready"]
    assert manager.estimate(manager.selected) == 5.5


def test_over_budget_never_touches_loaded_models(manager):
    manager.switch({})
    manager.events.clear()
    manager.catalog["tts2"]["vram_gib"] = 20
    with pytest.raises(ModelError, match="超过"):
        manager.switch({"tts": "tts2"})
    assert manager.events == []
    assert manager.selected["tts"] == "tts1"
    assert len(manager.loaded) == 4


def test_missing_weights_does_not_unload_current_models(manager):
    manager.switch({})
    manager.events.clear()
    manager.catalog["stt2"]["path"] = "does-not-exist.pt"
    with pytest.raises(ModelError, match="缺少"):
        manager.switch({"stt": "stt2"})
    assert manager.events == []


def test_load_failure_restores_old_model(manager):
    manager.switch({})
    original_loader = manager.loader

    def broken_loader(spec):
        if spec["id"] == "llm2":
            raise RuntimeError("provider unavailable")
        return original_loader(spec)

    manager.loader = broken_loader
    with pytest.raises(ModelError, match="provider unavailable"):
        manager.switch({"llm": "llm2"})
    assert manager.selected["llm"] == "llm1"
    assert manager.loaded["llm"].id == "llm1"
    assert manager.switching is False


def test_first_load_failure_does_not_load_models_that_were_not_previously_resident(manager):
    attempts = []

    def loader(spec):
        attempts.append(spec["id"])
        raise RuntimeError("CUDA unavailable")

    manager.loader = loader
    with pytest.raises(ModelError, match="CUDA unavailable"):
        manager.switch({})
    assert attempts == ["vad1"]
    assert not manager.loaded


def test_network_share_registration_is_rejected_before_path_access(manager):
    with pytest.raises(ModelError, match="网络共享"):
        manager.register({"stage": "stt", "name": "Network", "path": "\\\\server\\models\\model.pt", "vram_gib": 2})


def test_wrong_stage_and_unknown_models_rejected(manager):
    with pytest.raises(ModelError, match="无效"):
        manager.switch({"llm": "tts2"})
    with pytest.raises(ModelError, match="无效"):
        manager.switch({"llm": "remote-model"})


def test_custom_model_registration_is_persisted(manager, tmp_path):
    path = tmp_path / "custom.pt"
    path.touch()
    model = manager.register({"stage": "stt", "name": "Local Whisper", "path": str(path), "vram_gib": 3.5})
    assert model["id"] in manager.catalog
    restored = ModelManager(manager.config_path)
    assert restored.catalog[model["id"]]["path"] == str(path.resolve())


def test_remote_registration_is_rejected(manager):
    with pytest.raises(ModelError, match="本地绝对路径"):
        manager.register({"stage": "stt", "name": "Remote", "path": "https://example.com/model.pt", "vram_gib": 2})


def test_api_model_uses_zero_local_vram(manager):
    manager.catalog["llm2"]["vram_gib"] = 0
    assert manager.estimate({**manager.selected, "llm": "llm2"}) == 4.5


def test_api_can_switch_without_local_speech_weights(manager):
    manager.catalog["tts1"]["path"] = "missing-tts"
    manager.switch({"llm": "llm2"}, llm_only=True)
    assert manager.selected["llm"] == "llm2"
    assert list(manager.loaded) == ["llm"]
