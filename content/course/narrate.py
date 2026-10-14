"""Generate cached chapter narration with the official Doubao HTTP API.

Voice IDs are supplied at runtime; neither credentials nor private voice bindings
are written into the source project. Failed/uncertain requests are not retried.
"""

import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import uuid
import wave

import httpx
import numpy as np

HERE = Path(__file__).resolve().parent
OUT = HERE.parents[1] / "output/course/narration"
URL = "https://openspeech.bytedance.com/api/v3/tts/unidirectional"


def api_key():
    value = os.getenv("DOUBAO_TTS_API_KEY")
    if not value and os.name == "nt":
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
                value = winreg.QueryValueEx(key, "DOUBAO_TTS_API_KEY")[0]
        except FileNotFoundError:
            pass
    if not value:
        raise RuntimeError("Set DOUBAO_TTS_API_KEY in your local environment.")
    return value.strip()


def synthesize(client, chapter, speaker, resource):
    params = {"text": chapter["text"], "speaker": speaker,
              "model": "seed-tts-2.0-standard",
              "audio_params": {"format": "pcm", "sample_rate": 48000,
                               "speech_rate": 0, "enable_subtitle": True}}
    identity = {"resource": resource, "req_params": params}
    digest = hashlib.sha256(json.dumps(identity, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    directory = OUT / chapter["id"]
    directory.mkdir(parents=True, exist_ok=True)
    audio = directory / (digest[:16] + ".wav")
    metadata = audio.with_suffix(".json")
    if metadata.exists() and audio.exists():
        record = json.loads(metadata.read_text(encoding="utf-8"))
        if record.get("complete") and record.get("request_sha256") == digest and record.get("audio_sha256") == hashlib.sha256(audio.read_bytes()).hexdigest():
            print(f"{chapter['id']}: cached {record['duration_s']:.2f}s", flush=True)
            return record
    attempt = directory / (digest[:16] + ".attempt.json")
    if attempt.exists():
        raise RuntimeError(f"{chapter['id']}: prior request is incomplete/uncertain; inspect its attempt record before retrying.")
    request_id = str(uuid.uuid4())
    record = {"chapter": chapter["id"], "request_id": request_id,
              "request_sha256": digest, **identity, "complete": False}
    with attempt.open("x", encoding="utf-8") as marker:
        json.dump(record, marker, ensure_ascii=False, indent=2)
    headers = {"X-Api-Key": api_key(), "X-Api-Resource-Id": resource,
               "X-Api-Request-Id": request_id, "X-Control-Require-Usage-Tokens-Return": "*"}
    pcm, sentences, usage, complete = bytearray(), [], None, False
    try:
        with client.stream("POST", URL, headers=headers,
                           json={"user": {"uid": "cyber-girl-course"}, "req_params": params}) as response:
            if response.status_code != 200:
                raise RuntimeError(f"Doubao HTTP {response.status_code}; request {request_id}")
            for line in response.iter_lines():
                line = line.strip()
                if not line or line.startswith(("event:", ":")):
                    continue
                if line.startswith("data:"):
                    line = line[5:].strip()
                if line == "[DONE]":
                    continue
                event = json.loads(line)
                code = event.get("code")
                if code not in (0, 20000000):
                    raise RuntimeError(f"Doubao code {code}: {str(event.get('message', ''))[:180]}; request {request_id}")
                if event.get("data"):
                    pcm.extend(base64.b64decode(event["data"], validate=True))
                if event.get("sentence"):
                    sentences.append(event["sentence"])
                if code == 20000000:
                    complete, usage = True, event.get("usage")
        if not complete or len(pcm) < 4800 or len(pcm) % 2:
            raise RuntimeError("Incomplete end marker or invalid PCM output; no automatic retry.")
        samples = np.frombuffer(pcm, dtype="<i2").astype(np.float64) / 32768
        rms = float(np.sqrt(np.mean(samples * samples)))
        if rms < 0.001:
            raise RuntimeError("Output is almost silent; inspect before generating more chapters.")
        with wave.open(str(audio), "wb") as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(48000)
            output.writeframes(pcm)
        record.update(complete=True, duration_s=len(samples) / 48000,
                      sample_rate=48000, channels=1, rms_db=20 * float(np.log10(rms)),
                      usage=usage, sentences=sentences, audio=str(audio.resolve()),
                      audio_sha256=hashlib.sha256(audio.read_bytes()).hexdigest())
        metadata.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        attempt.unlink()
        print(f"{chapter['id']}: generated {record['duration_s']:.2f}s, {len(sentences)} subtitle events, usage={usage}", flush=True)
        return record
    except Exception as exc:
        record["failure"] = type(exc).__name__
        record["result_uncertain"] = True
        attempt.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        raise


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--speaker", required=True)
    parser.add_argument("--resource", default="seed-icl-2.0", choices=("seed-icl-2.0",))
    parser.add_argument("--film", choices=("promo", "tutorial", "both"), default="both")
    parser.add_argument("--chapter")
    args = parser.parse_args()
    script = json.loads((HERE / "script.json").read_text(encoding="utf-8"))
    films = ("promo", "tutorial") if args.film == "both" else (args.film,)
    with httpx.Client(timeout=httpx.Timeout(180, connect=20), follow_redirects=False) as client:
        for film in films:
            records = []
            for chapter in script[film]["chapters"]:
                if not args.chapter or chapter["id"] == args.chapter:
                    records.append(synthesize(client, chapter, args.speaker, args.resource))
            if not records:
                raise ValueError("Unknown chapter ID")
            (OUT / (film + ("-sample" if args.chapter else "") + ".json")).write_text(
                json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
