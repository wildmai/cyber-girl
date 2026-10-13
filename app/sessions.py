"""Per-browser settings and per-connection inference state; large weights stay shared."""

import copy
import secrets
import shutil
import threading

from app.avatar import AvatarService
from app.minimax import MiniMaxLLM
from app.local_llm import LocalLLM
from app.models import ModelError, ModelManager
from app.persona import PersonaConfig, PersonaStore


_profile_lock = threading.RLock()


class SessionModels:
    shared_models = True

    def __init__(self, shared, profile):
        self.shared = shared
        self.selected = dict(shared.selected)
        self._private = {}
        self._sources = {}
        self.vad_lock = threading.RLock()
        self.llm_lock = threading.RLock()
        with _profile_lock:
            profile_dir = shared.config_path.parent / "profiles" / profile
            profile_dir.mkdir(parents=True, exist_ok=True)
            self.persona = PersonaStore(profile_dir / "persona.json")
            if not self.persona.path.exists():
                self.persona.save(PersonaConfig(**shared.persona.status()))
            self.avatar = AvatarService(self, profile_dir / "avatar.json")
            if not self.avatar.config_path.exists():
                self.avatar.config = dict(shared.avatar.config)
                if self.avatar.config["custom_portrait"]:
                    shutil.copyfile(shared.avatar.portrait, profile_dir / "avatar-portrait.png")
                self.avatar.save()
            self.avatar.media_dir = shared.avatar.media_dir / profile
        self.refresh_models()

    def __getattr__(self, name):
        return getattr(self.shared, name)

    @property
    def loaded(self):
        return {**self.shared.loaded, **self._private}

    def refresh_models(self):
        with self.lock, self.vad_lock:
            for stage in ("vad", "llm"):
                source = self.shared.loaded.get(stage)
                if self._sources.get(stage) is source:
                    continue
                previous = self._private.pop(stage, None)
                if stage == "llm" and previous is not None and hasattr(previous, "close"):
                    previous.close()
                self._sources[stage] = source
                if source is not None:
                    self._private[stage] = (source.fork() if isinstance(source, LocalLLM) else
                        MiniMaxLLM(source.model) if isinstance(source, MiniMaxLLM) else copy.deepcopy(source))

    @property
    def required_stages(self):
        return ("vad", "stt", "llm") if self.avatar.realtime else ("vad", "stt", "llm", "tts")

    def estimate(self, selection, include_tts=None):
        return ModelManager.estimate(self, selection, include_tts)

    def status(self):
        return ModelManager.status(self)

    def switch(self, changes, llm_only=False):
        self.shared.switch(changes, llm_only, required_stages=self.required_stages)
        self.selected = dict(self.shared.selected)
        self.refresh_models()
        return self.status()

    def connect_llm(self, model_id):
        spec = self.catalog.get(model_id)
        if not spec or spec["stage"] != "llm":
            raise ModelError("无效的对话模型。")
        if spec.get("backend") == "llama.cpp" or isinstance(self.shared.loaded.get("llm"), LocalLLM):
            return self.switch({"llm": model_id}, llm_only=True)
        model = MiniMaxLLM(spec["model"])
        with self.llm_lock:
            previous = self._private.get("llm")
            if previous is not None and hasattr(previous, "close"):
                previous.close()
            self._private["llm"] = model
            self._sources["llm"] = self.shared.loaded.get("llm")
            self.selected["llm"] = model_id
        return self.status()

    def close(self):
        self.avatar.unload()
        model = self._private.get("llm")
        if model is not None and hasattr(model, "close"):
            model.close()
        self._private.clear()


def new_token():
    return secrets.token_urlsafe(32)
