"""Explicit one-time weight preparation; never imported by the application."""

import os
from pathlib import Path

os.environ["HF_HUB_OFFLINE"] = "0"
os.environ["HF_HUB_DISABLE_XET"] = "1"

from huggingface_hub import snapshot_download  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
REPO = "Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice"
REVISION = "85e237c12c027371202489a0ec509ded67b5e4b5"


if __name__ == "__main__":
    result = snapshot_download(REPO, revision=REVISION, token=False, max_workers=3,
        local_dir=ROOT / "models/Qwen3-TTS-12Hz-0.6B-CustomVoice",
        ignore_patterns=[".gitattributes"])
    print(f"Prepared local TTS: {result}")
