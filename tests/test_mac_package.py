import hashlib
import json
import tarfile

from app.local_llm import LOCAL_MODEL
from scripts.package_mac import DIRECTORIES, FILES, build


def test_mac_release_has_verified_sources_and_excludes_private_state(tmp_path):
    for name in FILES:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"public")
    for directory in DIRECTORIES:
        (tmp_path / directory).mkdir(parents=True, exist_ok=True)
    (tmp_path / "app/main.py").write_text("# app\n")
    (tmp_path / "config/models.json").write_text(json.dumps({
        "models": [LOCAL_MODEL, {"id": "local-private", "path": "/private/path"}],
        "selected": {"llm": "minimax-m27"}}))
    for name in (".env", "config/mac/persona.json", "config/profiles/persona.json",
                 "models/llm/private.gguf", "runtime/llama.cpp/secret", "app/.env", "logs/app.log"):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("PRIVATE")
    destination = build(tmp_path)
    with tarfile.open(destination) as archive:
        names = archive.getnames()
        manifest = json.load(archive.extractfile("xiaoman-mac/MANIFEST.json"))
        for name, spec in manifest.items():
            data = archive.extractfile("xiaoman-mac/" + name).read()
            assert hashlib.sha256(data).hexdigest() == spec["sha256"]
            assert b"PRIVATE" not in data
        assert not any(name.endswith(".gguf") or "profiles" in name or "runtime/" in name for name in names)
        config = json.load(archive.extractfile("xiaoman-mac/config/models.json"))
        assert config["selected"]["llm"] == "qwen35-9b-local"
        assert config["low_memory"] and config["device"] == "cpu"
        assert not any(m["id"].startswith("local-") for m in config["models"])
        assert archive.getmember("xiaoman-mac/start-mac.command").mode == 0o755
        assert archive.getmember("xiaoman-mac/scripts/setup-mac.command").mode == 0o755
