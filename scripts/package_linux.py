"""Build an allowlisted Linux release, never copying runtime secrets or state."""

import hashlib
import io
import json
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIRECTORIES = ("app", "web", "vendor/s2s", "vendor/musetalk_core")
FILES = ("Dockerfile", "compose.yaml", ".dockerignore", ".env.example", "LINUX-DEPLOY.md",
         "requirements.txt", "config/models.json", "config/model-sources.json",
         "models/silero-v5/silero_vad.jit", "models/whisper/base.pt",
         "deploy/start.sh", "deploy/entrypoint.py", "deploy/requirements-cpu.txt", "deploy/nginx.conf.example")


def build(root=ROOT, destination=None):
    destination = Path(destination or root / "output/releases/xiaoman-linux.tar.gz")
    destination.parent.mkdir(parents=True, exist_ok=True)
    paths = [root / name for name in FILES]
    for directory in DIRECTORIES:
        paths.extend(p for p in (root / directory).rglob("*")
                     if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc"
                     and not any(part.startswith(".") for part in p.relative_to(root).parts)
                     and (directory != "app" or p.suffix == ".py"))
    manifest = {}
    with tarfile.open(destination, "w:gz") as archive:
        for path in sorted(paths):
            name = path.relative_to(root).as_posix()
            data = path.read_bytes()
            if name == "config/models.json":
                config = json.loads(data)
                # Registered absolute paths belong to the source computer.
                config["models"] = [m for m in config["models"]
                                    if not m["id"].startswith("local-")]
                config["selected"] = {"vad": "silero-v5", "stt": "whisper-base",
                                      "llm": "minimax-m27", "tts": "qwen3-tts-06b"}
                config["device"] = "cpu"
                data = (json.dumps(config, ensure_ascii=False, indent=2) + "\n").encode()
            info = tarfile.TarInfo("xiaoman-linux/" + name)
            info.size, info.mode = len(data), 0o755 if name.endswith(".sh") else 0o644
            archive.addfile(info, io.BytesIO(data))
            manifest[name] = {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
        data = (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode()
        info = tarfile.TarInfo("xiaoman-linux/MANIFEST.json")
        info.size, info.mode = len(data), 0o644
        archive.addfile(info, io.BytesIO(data))
    with destination.open("rb") as release:
        digest = hashlib.file_digest(release, "sha256").hexdigest()
    destination.with_suffix(destination.suffix + ".sha256").write_text(
        f"{digest}  {destination.name}\n", encoding="ascii")
    return destination


if __name__ == "__main__":
    print(build())
