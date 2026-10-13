"""Prepare the pinned VAD and STT files omitted from Git checkouts."""

import json
from pathlib import Path

from scripts.prepare_avatar import download

ROOT = Path(__file__).resolve().parents[1]


def prepare(root=ROOT):
    sources = json.loads((root / "config/model-sources.json").read_text(encoding="utf-8"))
    files = {"silero-v5": "models/silero-v5/silero_vad.jit", "whisper-base": "models/whisper/base.pt"}
    for model_id, relative in files.items():
        spec = sources[model_id]
        download(spec["source"], root / relative, {"size": spec["bytes"], "lfs": {"oid": spec["sha256"]}})
    print("Verified local Silero VAD v5 and Whisper base.", flush=True)


if __name__ == "__main__":
    prepare()
