"""Run genuine offline lip-sync on a short local WAV, without calling the LLM."""

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.offline import enable_offline  # noqa: E402

enable_offline()

from app.models import ModelManager  # noqa: E402


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("audio", type=Path)
    args = parser.parse_args()
    import torch
    torch.set_num_threads(4)
    manager = ModelManager()
    started = time.monotonic()
    try:
        manager.avatar.set_enabled(True)
        url = manager.avatar.render(args.audio.read_bytes(), lambda: True,
                                   lambda value: print(f"Lip-sync {value}%", flush=True))
        print(f"Completed in {time.monotonic() - started:.1f}s: {url}", flush=True)
    finally:
        manager.close()


if __name__ == "__main__":
    main()
