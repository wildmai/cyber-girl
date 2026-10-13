"""Real local Qwen3-TTS smoke synthesis. No API or downloads."""

import argparse
import json
from pathlib import Path
from time import perf_counter

from app.offline import enable_offline

enable_offline()

import soundfile as sf  # noqa: E402

from app.models import ModelManager, ROOT  # noqa: E402


def run():
    parser = argparse.ArgumentParser()
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--text", default="你好，我是小满。")
    parser.add_argument("--output", type=Path, default=ROOT / "output/tts-check.wav")
    args = parser.parse_args()
    manager = ModelManager()
    manager.config["device"] = args.device
    start = perf_counter()
    print("Loading local Qwen3-TTS...", flush=True)
    model = manager._load(manager.catalog[manager.selected["tts"]])
    print(f"Loaded in {perf_counter() - start:.1f}s. Synthesizing...", flush=True)
    wavs, sample_rate = model.generate_custom_voice(text=args.text, language="Chinese", speaker="Vivian", max_new_tokens=256)
    args.output.parent.mkdir(exist_ok=True, parents=True)
    sf.write(args.output, wavs[0], sample_rate)
    print(json.dumps({"device": args.device, "sample_rate": sample_rate, "seconds": len(wavs[0])/sample_rate,
                      "elapsed": round(perf_counter() - start, 2), "output": str(args.output)}, ensure_ascii=False))


if __name__ == "__main__":
    run()
