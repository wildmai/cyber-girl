"""Explicit download step. Inference never downloads weights."""

import argparse
import hashlib
import time
from pathlib import Path

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

SESSION = requests.Session()
SESSION.mount("https://", HTTPAdapter(max_retries=Retry(total=5, backoff_factor=1,
    status_forcelist=(429, 500, 502, 503, 504))))

ROOT = Path(__file__).resolve().parents[1]
WEIGHTS = [
    ("TMElyralab/MuseTalk", "2bcb936e2fddb4d86db4c62fd45b387d0c061571", "", ["musetalkV15/musetalk.json", "musetalkV15/unet.pth", "LICENSE.txt"]),
    ("stabilityai/sd-vae-ft-mse", "31f26fdeee1355a5c34592e401dd41e45d25a493", "sd-vae", ["config.json", "diffusion_pytorch_model.bin"]),
    ("openai/whisper-tiny", "169d4a4341b33bc18d8881c4b69c2e104e1cc0af", "whisper", ["config.json", "pytorch_model.bin", "preprocessor_config.json"]),
    ("ManyOtherFunctions/face-parse-bisent", "0073b233a5a3c4b1377d4dbf49245017938a72b5", "face-parse-bisent", ["79999_iter.pth", "resnet18-5c106cde.pth"]),
]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--endpoint", choices=("https://huggingface.co", "https://hf-mirror.com"),
                        default="https://huggingface.co")
    args = parser.parse_args()
    for repo, revision, directory, files in WEIGHTS:
        print(f"Preparing {repo}@{revision}", flush=True)
        for name in files:
            parent = str(Path(name).parent).replace("\\", "/")
            tree = SESSION.get(f"{args.endpoint}/api/models/{repo}/tree/{revision}/" + (parent if parent != "." else ""), timeout=30)
            tree.raise_for_status()
            spec = next(item for item in tree.json() if item["path"] == name)
            target = ROOT / "models/MuseTalk" / directory / name
            download(f"{args.endpoint}/{repo}/resolve/{revision}/{name}?download=true", target, spec)
            if name == "resnet18-5c106cde.pth":
                # This checksum-verified upstream checkpoint uses legacy TAR serialization.
                # Convert during explicit preparation so runtime uses weights_only=True.
                import torch
                state = torch.load(target, map_location="cpu", weights_only=False)
                torch.save(state, target.with_name("resnet18-safe.pth"))
    print("MuseTalk 1.5 weights prepared locally.", flush=True)


def download(url, target, spec):
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.is_file() and verify(target, spec):
        print(f"Verified {target.name}", flush=True)
        return
    part = target.with_suffix(target.suffix + ".part")
    for attempt in range(6):
        offset = part.stat().st_size if part.exists() else 0
        if offset == spec["size"]:
            break
        try:
            with SESSION.get(url, headers={"Range": f"bytes={offset}-"}, stream=True, timeout=(20, 60)) as response:
                response.raise_for_status()
                if response.status_code != 206:
                    offset = 0
                stamp = time.monotonic()
                with part.open("ab" if offset else "wb") as output:
                    for chunk in response.iter_content(1024 * 1024):
                        output.write(chunk)
                        offset += len(chunk)
                        if time.monotonic() - stamp > 10:
                            print(f"{target.name}: {offset / spec['size']:.0%}", flush=True)
                            stamp = time.monotonic()
            break
        except requests.RequestException:
            if attempt == 5:
                raise
            time.sleep(2)
    if not verify(part, spec):
        raise RuntimeError(f"Checksum mismatch: {target.name}; remove the .part file before retrying")
    part.replace(target)
    print(f"Verified {target.name}", flush=True)


def verify(path, spec):
    if path.stat().st_size != spec["size"]:
        return False
    digest = hashlib.sha256() if "lfs" in spec else hashlib.sha1()
    if "lfs" not in spec:
        digest.update(f"blob {spec['size']}\0".encode())
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest() == (spec["lfs"]["oid"] if "lfs" in spec else spec["oid"])


if __name__ == "__main__":
    main()
