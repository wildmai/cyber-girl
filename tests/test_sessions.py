from types import SimpleNamespace
import threading

from app.avatar import AvatarService
from app.persona import PersonaStore
from app.sessions import SessionModels, new_token


def test_vad_and_cancellation_are_private_but_large_weights_are_shared(tmp_path):
    class CloudClient:
        cancelled = False
        def cancel(self):
            self.cancelled = True
        def close(self):
            self.cancelled = True

    shared = SimpleNamespace(config_path=tmp_path / "models.json", selected={}, lock=threading.RLock(),
        loaded={"vad": SimpleNamespace(buffer=[]), "stt": object(), "llm": CloudClient()},
        persona=PersonaStore(tmp_path / "persona.json"))
    shared.avatar = AvatarService(shared, tmp_path / "avatar.json")
    one = SessionModels(shared, new_token())
    two = SessionModels(shared, new_token())
    try:
        one.loaded["vad"].buffer.append("first-voice")
        assert not two.loaded["vad"].buffer and not shared.loaded["vad"].buffer
        assert one.loaded["stt"] is two.loaded["stt"] is shared.loaded["stt"]
        one.loaded["llm"].cancel()
        assert not two.loaded["llm"].cancelled
        one.close()
        assert not two.loaded["llm"].cancelled
    finally:
        two.close()
