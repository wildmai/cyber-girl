import hashlib
import json
from pathlib import Path

from scripts import prepare_speech
from scripts.prepare_avatar import verify


def test_fresh_checkout_prepares_both_speech_models_with_integrity_checks(tmp_path, monkeypatch):
    data = b"downloaded-checkpoint"
    spec = {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(), "source": "https://example.com/weights"}
    (tmp_path / "config").mkdir()
    (tmp_path / "config/model-sources.json").write_text(json.dumps({
        "silero-v5": spec, "whisper-base": spec}))
    prepared = []

    def download(url, destination, expected):
        assert url == spec["source"]
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)
        assert verify(destination, expected)
        prepared.append(destination.relative_to(tmp_path).as_posix())

    monkeypatch.setattr(prepare_speech, "download", download)
    prepare_speech.prepare(tmp_path)
    assert prepared == ["models/silero-v5/silero_vad.jit", "models/whisper/base.pt"]


def test_mac_setup_prepares_all_weights_from_a_source_checkout():
    source = (Path(__file__).resolve().parents[1] / "scripts/setup-mac.command").read_text(encoding="utf-8")
    for step in ("prepare_speech", "prepare_llm", "prepare_tts", "prepare_avatar", "configure_mac"):
        assert f".venv/bin/python -m scripts.{step}" in source
    assert source.index("prepare_speech") < source.index("configure_mac")
