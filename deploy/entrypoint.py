"""Initialize a persistent config volume without overwriting user settings."""

import json
import shutil
from pathlib import Path

import uvicorn

ROOT = Path(__file__).resolve().parents[1]


def initialize(root=ROOT):
    config = root / "config"
    config.mkdir(parents=True, exist_ok=True)
    models = config / "models.json"
    if not models.exists():
        shutil.copyfile(root / "defaults/models.json", models)
    avatar = config / "avatar.json"
    if not avatar.exists():
        avatar.write_text(json.dumps({"enabled": True, "custom_portrait": False,
            "provider": "vidu", "mode": "realtime"}) + "\n", encoding="utf-8")


if __name__ == "__main__":
    initialize()
    uvicorn.run("app.main:app", host="0.0.0.0", port=7860, ws_max_size=16384,
                timeout_graceful_shutdown=10)
