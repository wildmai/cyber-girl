import json
import hashlib
import tarfile
import os
import subprocess
import sys

import pytest

from app.deployment import network_config
from deploy.entrypoint import initialize


def test_network_defaults_remain_loopback_only(monkeypatch):
    monkeypatch.delenv("SAIBOGIRL_PUBLIC_ORIGIN", raising=False)
    monkeypatch.delenv("SAIBOGIRL_ALLOWED_HOSTS", raising=False)
    origin, hosts = network_config()
    assert origin == ""
    assert hosts == ["localhost", "127.0.0.1", "[::1]", "testserver"]


@pytest.mark.parametrize("value", ["*", "http://chat.example.com", "https://user:pass@chat.example.com",
                                  "https://chat.example.com/path", "https://chat.example.com?x=1"])
def test_public_origin_rejects_unsafe_or_incomplete_values(monkeypatch, value):
    monkeypatch.setenv("SAIBOGIRL_PUBLIC_ORIGIN", value)
    with pytest.raises(ValueError):
        network_config()


def test_public_origin_adds_only_the_named_host(monkeypatch):
    monkeypatch.setenv("SAIBOGIRL_PUBLIC_ORIGIN", "https://chat.example.com/")
    origin, hosts = network_config()
    assert origin == "https://chat.example.com"
    assert hosts[-1] == "chat.example.com"
    assert "*" not in hosts


def test_explicit_server_hosts_and_wildcard(monkeypatch):
    monkeypatch.delenv("SAIBOGIRL_PUBLIC_ORIGIN", raising=False)
    monkeypatch.setenv("SAIBOGIRL_ALLOWED_HOSTS", "150.158.124.148, chat.example.com")
    _, hosts = network_config()
    assert "150.158.124.148" in hosts and "chat.example.com" in hosts
    monkeypatch.setenv("SAIBOGIRL_ALLOWED_HOSTS", "*")
    assert network_config() == ("", ["*"])


def test_public_hosts_work_in_the_real_app_without_relaxing_origin_checks():
    code = '''
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
from app.main import app
with TestClient(app, base_url="http://150.158.124.148:7860") as client:
    assert client.get("/").status_code == 200
    assert client.get("/", headers={"host": "another.example.com"}).status_code == 200
    with client.websocket_connect("ws://150.158.124.148:7860/ws", headers={"origin": "http://150.158.124.148:7860"}) as ws:
        assert ws.receive_json()["type"] == "connected"
    try:
        with client.websocket_connect("ws://150.158.124.148:7860/ws", headers={"origin": "https://evil.example"}):
            raise AssertionError("Cross-origin connection was accepted")
    except WebSocketDisconnect:
        pass
'''
    environment = {**os.environ, "SAIBOGIRL_ALLOWED_HOSTS": "*", "SAIBOGIRL_PUBLIC_ORIGIN": "",
                   "MINIMAX_API_KEY": "", "SAIBOGIRL_AUTOLOAD_SPEECH": "0", "SAIBOGIRL_DESKTOP": "0"}
    result = subprocess.run([sys.executable, "-c", code], env=environment,
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr


def test_container_initialization_preserves_saved_config(tmp_path):
    (tmp_path / "defaults").mkdir()
    (tmp_path / "defaults/models.json").write_text('{"device":"auto"}', encoding="utf-8")
    initialize(tmp_path)
    assert json.loads((tmp_path / "config/avatar.json").read_text())["mode"] == "realtime"
    models = tmp_path / "config/models.json"
    models.write_text('{"device":"cpu","saved":true}', encoding="utf-8")
    avatar = tmp_path / "config/avatar.json"
    avatar.write_text('{"provider":"musetalk"}', encoding="utf-8")
    initialize(tmp_path)
    assert json.loads(models.read_text())["saved"]
    assert json.loads(avatar.read_text())["provider"] == "musetalk"


def test_release_excludes_private_state_and_preserves_integrity(tmp_path):
    from scripts.package_linux import DIRECTORIES, FILES, build
    for name in FILES:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"public artifact")
    for directory in DIRECTORIES:
        (tmp_path / directory).mkdir(parents=True, exist_ok=True)
    (tmp_path / "app/main.py").write_text("# app\n")
    for name in (".env", "config/persona.json", "config/avatar-portrait.png", "logs/app.log",
                 "app/.env", "deploy/.env", "models/private.bin"):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"private-canary-secret")
    (tmp_path / "config/models.json").write_text(json.dumps({"models": [
        {"id": "whisper-base", "path": "models/whisper/base.pt"},
        {"id": "local-private", "path": "C:/private/model.pt"}], "selected": {"stt": "local-private"}}))
    release = build(tmp_path, tmp_path / "release.tar.gz")
    with tarfile.open(release) as archive:
        names = archive.getnames()
        assert "xiaoman-linux/models/whisper/base.pt" in names
        assert "xiaoman-linux/.env" not in names
        manifest = json.load(archive.extractfile("xiaoman-linux/MANIFEST.json"))
        for member in archive.getmembers():
            data = archive.extractfile(member).read()
            assert b"private-canary-secret" not in data
            if member.name.endswith("MANIFEST.json"):
                continue
            relative = member.name.removeprefix("xiaoman-linux/")
            assert manifest[relative]["sha256"] == hashlib.sha256(data).hexdigest()
        config = json.load(archive.extractfile("xiaoman-linux/config/models.json"))
        assert config["device"] == "cpu"
        assert config["selected"]["stt"] == "whisper-base"
        assert len(config["models"]) == 1
