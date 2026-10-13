"""Real local VAD + Whisper smoke check against a supplied WAV (never downloads)."""

import argparse
import json
import math
from pathlib import Path

from app.offline import enable_offline

enable_offline()

import numpy as np  # noqa: E402
import soundfile as sf  # noqa: E402
import torch  # noqa: E402
from scipy.signal import resample_poly  # noqa: E402

from app.models import ModelManager  # noqa: E402


def run():
    parser = argparse.ArgumentParser()
    parser.add_argument("--audio", type=Path, required=True)
    parser.add_argument("--device", default="cpu")
    args = parser.parse_args()
    torch.set_num_threads(4)
    manager = ModelManager()
    manager.config["device"] = args.device
    audio, sample_rate = sf.read(args.audio, dtype="float32", always_2d=True)
    audio = audio.mean(axis=1)
    divisor = math.gcd(sample_rate, 16000)
    audio = resample_poly(audio, 16000 // divisor, sample_rate // divisor).astype(np.float32)
    audio = np.pad(audio, (0, 16000))
    vad = manager._load(manager.catalog[manager.selected["vad"]])
    utterances = []
    for offset in range(0, len(audio) - 511, 512):
        utterance = vad(torch.from_numpy(audio[offset:offset + 512]).to(args.device))
        if utterance:
            utterances.append(torch.cat(utterance).cpu().numpy())
    if not utterances:
        raise RuntimeError("Silero did not detect speech in the supplied WAV")
    model = manager._load(manager.catalog[manager.selected["stt"]])
    texts = [model.transcribe(part, language="zh", fp16=args.device.startswith("cuda"))["text"].strip() for part in utterances]
    if not any(texts):
        raise RuntimeError("Whisper returned no transcript")
    print(json.dumps({"device": args.device, "utterances": len(utterances), "transcripts": texts}, ensure_ascii=False))


if __name__ == "__main__":
    run()
