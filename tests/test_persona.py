import pytest
from pydantic import ValidationError

from app.persona import DEFAULT_PERSONA, RESPONSE_RULES, PersonaConfig, PersonaStore


def test_default_and_persistence_across_restart(tmp_path):
    path = tmp_path / "config/persona.json"
    store = PersonaStore(path)
    assert store.status() == {"prompt": DEFAULT_PERSONA}
    assert "女友" in store.system_prompt() and "小满" in store.system_prompt()
    config = PersonaConfig(prompt="  你叫小月，性格开朗，喜欢天文学。  ")
    store.save(config)
    restarted = PersonaStore(path)
    assert restarted.status()["prompt"] == "你叫小月，性格开朗，喜欢天文学。"
    assert restarted.system_prompt().endswith(RESPONSE_RULES)
    assert not path.with_suffix(".tmp").exists()


@pytest.mark.parametrize("value", ["", " \n\t ", "字" * 6001], ids=["empty", "whitespace", "too-long"])
def test_invalid_persona_is_rejected(value):
    with pytest.raises(ValidationError):
        PersonaConfig(prompt=value)


def test_failed_write_keeps_previous_persona(tmp_path, monkeypatch):
    from pathlib import Path
    store = PersonaStore(tmp_path / "persona.json")
    store.save(PersonaConfig(prompt="原先的人设"))

    def fail(*args, **kwargs):
        raise PermissionError("read-only")

    monkeypatch.setattr(Path, "replace", fail)
    with pytest.raises(PermissionError):
        store.save(PersonaConfig(prompt="新的设定"))
    assert store.status()["prompt"] == "原先的人设"
    assert PersonaStore(store.path).status() == store.status()
