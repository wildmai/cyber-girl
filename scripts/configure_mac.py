"""Create the Mac 16GB profile without overwriting existing user settings."""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def configure(root=ROOT):
    source = json.loads((root / "config/models.json").read_text(encoding="utf-8"))
    source.update(device="cpu", low_memory=True, reserve_gib=2.5, budget_gib=14)
    source["selected"] = {"vad": "silero-v5", "stt": "whisper-base", "llm": "qwen35-9b-local", "tts": "qwen3-tts-06b"}
    path = root / "config/mac/models.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text(json.dumps(source, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    avatar = path.with_name("avatar.json")
    if not avatar.exists():
        avatar.write_text(json.dumps({"enabled": True, "custom_portrait": False,
            "provider": "musetalk", "mode": "standard"}) + "\n", encoding="utf-8")
    return path


if __name__ == "__main__":
    print(configure())
