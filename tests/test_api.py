import socket

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture
def client(monkeypatch, tmp_path):
    from app import main
    from app.avatar import AvatarService
    from app.persona import PersonaStore
    monkeypatch.delenv("MINIMAX_API_KEY", raising=False)
    monkeypatch.setattr(main.manager, "config_path", tmp_path / "models.json")
    monkeypatch.setattr(main.manager, "avatar", AvatarService(main.manager, tmp_path / "avatar.json"))
    monkeypatch.setattr(main.manager, "persona", PersonaStore(tmp_path / "persona.json"))
    with TestClient(app) as connection:
        yield connection


def test_interface_is_served_with_local_resource_policy(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "赛博女友" in response.text
    assert "connect-src 'self'" in response.headers["content-security-policy"]
    assert "https://*.cloud-rtc.com" in response.headers["content-security-policy"]
    assert "wss://*.rtc-web.com" in response.headers["content-security-policy"]
    script = client.get("/app.js")
    assert script.status_code == 200
    assert "javascript" in script.headers["content-type"]
    assert client.get("/assets/xiaoman.png").status_code == 200


def test_persona_save_validation_and_restore(client):
    from app import main
    from app.persona import DEFAULT_PERSONA, PersonaStore
    response = client.get("/api/persona")
    assert response.json() == {"prompt": DEFAULT_PERSONA}
    assert response.headers["cache-control"] == "no-store"
    for prompt in ("", "  \n ", "字" * 6001):
        assert client.put("/api/persona", json={"prompt": prompt}).status_code == 422
    assert client.put("/api/persona", json={"prompt": "你好", "extra": True}).status_code == 422
    assert client.put("/api/persona", json={"prompt": "改写"}, headers={"origin": "https://evil.example"}).status_code == 403
    assert client.delete("/api/persona", headers={"origin": "https://evil.example"}).status_code == 403
    assert client.put("/api/persona", json={"prompt": "  你叫小月，喜欢星星。  "}).json() == {"prompt": "你叫小月，喜欢星星。"}
    assert PersonaStore(main.manager.persona.path).status()["prompt"] == "你叫小月，喜欢星星。"
    assert client.delete("/api/persona").json() == {"prompt": DEFAULT_PERSONA}
    assert PersonaStore(main.manager.persona.path).status()["prompt"] == DEFAULT_PERSONA


def test_persona_save_interrupts_reply_and_clears_context(client):
    from app import main
    with client.websocket_connect("/ws", headers={"origin": "http://testserver"}) as ws:
        token = ws.receive_json()["session_token"]
        client.headers["x-conversation-token"] = token
        pipeline = main.sessions[token]
        pipeline.history.append({"role": "user", "content": "旧人设的对话"})
        assert client.put("/api/persona", json={"prompt": "新的设定"}).status_code == 200
        assert ws.receive_json()["type"] == "interrupted"
        assert not pipeline.history
    assert not any(thread.is_alive() for thread in pipeline.threads.threads)


def test_persona_save_error_preserves_context(client, monkeypatch):
    from app import main

    def fail(config):
        raise PermissionError("read-only")

    with client.websocket_connect("/ws", headers={"origin": "http://testserver"}) as ws:
        token = ws.receive_json()["session_token"]
        client.headers["x-conversation-token"] = token
        pipeline = main.sessions[token]
        monkeypatch.setattr(pipeline.manager.persona, "save", fail)
        pipeline.history.append({"role": "user", "content": "应保留的对话"})
        assert client.put("/api/persona", json={"prompt": "新的设定"}).status_code == 503
        assert len(pipeline.history) == 1


def test_status_never_reports_missing_models_as_ready(client):
    status = client.get("/api/status").json()
    assert status["offline"] is False
    assert status["speech_local"] is True
    assert status["budget_gib"] == 15
    assert status["ready"] == (len(status["loaded"]) == 4)
    assert len(status["models"]) >= 7


def test_invalid_switch_and_cross_origin_write_are_rejected(client):
    response = client.post("/api/models/switch", json={"selection": {"llm": "unknown"}})
    assert response.status_code == 409
    assert "无效" in response.json()["detail"]
    response = client.post("/api/models/switch", json={"selection": {}}, headers={"origin": "https://evil.example"})
    assert response.status_code == 403


def test_websocket_missing_models_returns_actionable_error(client):
    with client.websocket_connect("/ws", headers={"origin": "http://testserver"}) as ws:
        assert ws.receive_json()["type"] == "connected"
        ws.send_json({"type": "text", "text": "你好"})
        error = ws.receive_json()
        assert error["type"] == "error"
        assert "尚未连接" in error["message"]
        ws.send_json({"type": "interrupt"})
        assert ws.receive_json()["type"] == "interrupted"


def test_websocket_rejects_remote_origin(client):
    from starlette.websockets import WebSocketDisconnect
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws", headers={"origin": "https://evil.example"}):
            pass


def test_https_proxy_origin_allows_conversation_and_rejects_other_sites(client, monkeypatch):
    from app import main
    from starlette.websockets import WebSocketDisconnect
    monkeypatch.setattr(main, "PUBLIC_ORIGIN", "https://testserver")
    assert client.put("/api/persona", json={"prompt": "你好"},
                      headers={"origin": "https://testserver"}).status_code == 409
    assert client.put("/api/persona", json={"prompt": "你好"},
                      headers={"origin": "https://evil.example"}).status_code == 403
    with client.websocket_connect("/ws", headers={"origin": "https://testserver"}) as ws:
        event = ws.receive_json()
        assert event["type"] == "connected"
        assert client.put("/api/persona", json={"prompt": "你好"}, headers={
            "origin": "https://testserver", "x-conversation-token": event["session_token"]}).status_code == 200
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws", headers={"origin": "http://testserver"}):
            pass


def test_offline_guard_rejects_external_connections_and_dns():
    with socket.socket() as sock:
        with pytest.raises(OSError, match="离线模式"):
            sock.connect(("8.8.8.8", 443))
    with pytest.raises(OSError, match="离线模式"):
        socket.getaddrinfo("huggingface.co", 443)


def test_avatar_clip_only_exposes_uuid_video_and_supports_range(client, tmp_path, monkeypatch):
    from app import main
    from app.avatar import AvatarService
    avatar = AvatarService(main.manager, tmp_path / "avatar.json")
    avatar.media_dir = tmp_path
    monkeypatch.setattr(main.manager, "avatar", avatar)
    name = "a" * 32 + ".mp4"
    (tmp_path / name).write_bytes(b"local-video")
    response = client.get("/api/avatar/clips/" + name, headers={"range": "bytes=0-4"})
    assert response.status_code == 206
    assert response.content == b"local"
    assert response.headers["content-type"] == "video/mp4"
    assert response.headers["cache-control"] == "private, no-store"
    assert client.get("/api/avatar/clips/avatar.json").status_code == 404
    assert client.get("/api/avatar/clips/" + "b" * 32 + ".mp4").status_code == 404


def test_portrait_upload_validation_and_same_origin(client, tmp_path, monkeypatch):
    import io
    from PIL import Image
    from app import main
    from app.avatar import AvatarService
    avatar = AvatarService(main.manager, tmp_path / "avatar.json")
    monkeypatch.setattr(main.manager, "avatar", avatar)
    assert client.put("/api/avatar/portrait", content=b"bad-image").status_code == 422
    data = io.BytesIO()
    Image.new("RGB", (60, 80)).save(data, format="PNG")
    assert client.put("/api/avatar/portrait", content=data.getvalue(),
                      headers={"origin": "https://evil.example"}).status_code == 403
    assert client.put("/api/avatar/portrait", content=data.getvalue()).status_code == 200
    assert client.get("/api/avatar/portrait").content.startswith(b"\x89PNG")
    assert client.delete("/api/avatar/portrait").status_code == 200
    assert not avatar.config["custom_portrait"]


def test_vidu_selection_and_connect_requirements_do_not_leak_key(client, tmp_path, monkeypatch):
    from app import main
    from app.avatar import AvatarService
    avatar = AvatarService(main.manager, tmp_path / "avatar.json")
    monkeypatch.setattr(main.manager, "avatar", avatar)
    monkeypatch.delenv("VIDU_API_KEY", raising=False)
    response = client.post("/api/avatar", json={"enabled": True, "provider": "vidu"})
    assert response.status_code == 200
    assert response.json()["avatar"]["provider"] == "vidu"
    assert response.json()["estimated_gib"] == 5.6
    assert client.post("/api/avatar", json={"enabled": True, "provider": "unknown"}).status_code == 422
    assert client.post("/api/avatar/vidu/connect", headers={"origin": "https://evil.example"}).status_code == 403
    assert client.post("/api/avatar/vidu/connect").status_code == 409
    with client.websocket_connect("/ws", headers={"origin": "http://testserver"}) as ws:
        client.headers["x-conversation-token"] = ws.receive_json()["session_token"]
        response = client.post("/api/avatar/vidu/connect")
        assert response.status_code == 409 and "VIDU_API_KEY" in response.json()["detail"]
        monkeypatch.setenv("VIDU_API_KEY", "never-expose-this-secret")
        assert "never-expose" not in client.get("/api/status").text
        assert client.get("/api/status").json()["avatar"]["vidu"]["key_configured"]
    assert client.post("/api/avatar/vidu/disconnect").status_code == 409


def test_realtime_mode_persists_and_rejects_invalid_modes(client, tmp_path, monkeypatch):
    from app import main
    from app.avatar import AvatarService
    avatar = AvatarService(main.manager, tmp_path / "avatar.json")
    monkeypatch.setattr(main.manager, "avatar", avatar)
    response = client.post("/api/avatar", json={"enabled": True, "provider": "vidu", "mode": "realtime"})
    assert response.status_code == 200
    assert response.json()["avatar"]["realtime"]
    assert response.json()["estimated_gib"] == 2.4
    assert not response.json()["speech_local"]
    assert AvatarService(main.manager, avatar.config_path).realtime
    assert client.post("/api/avatar", json={"enabled": True, "mode": "invalid"}).status_code == 422


def test_realtime_connection_uses_current_persona_and_save_closes_old_session(client, monkeypatch):
    from app import main
    main.manager.avatar.config.update(enabled=True, provider="vidu", mode="realtime")
    calls = []
    closed = []

    def open_session(portrait, mode, persona):
        calls.append((mode, persona))
        avatar.vidu.live_id, avatar.vidu.mode = "123", mode
        return {"provider": "trtc"}

    with client.websocket_connect("/ws", headers={"origin": "http://testserver"}) as ws:
        token = ws.receive_json()["session_token"]
        client.headers["x-conversation-token"] = token
        avatar = main.sessions[token].manager.avatar
        monkeypatch.setattr(avatar.vidu, "open", open_session)
        monkeypatch.setattr(avatar.vidu, "close", lambda: closed.append(True))
        assert client.put("/api/persona", json={"prompt": "你叫小月，性格沉稳。"}).status_code == 200
        assert closed == [True]
        assert client.post("/api/avatar/vidu/connect").status_code == 200
        assert calls == [("realtime", "你叫小月，性格沉稳。")]
        assert client.put("/api/persona", json={"prompt": "你叫小满，温柔幽默。"}).status_code == 200
        assert len(closed) == 2


def test_two_users_have_separate_persona_history_and_cancellation(client):
    from app import main
    from app.sessions import new_token
    def user_headers():
        return {"origin": "http://testserver", "cookie": "xiaoman_profile=" + new_token()}

    with client.websocket_connect("/ws", headers=user_headers()) as first:
        one = first.receive_json()["session_token"]
        with client.websocket_connect("/ws", headers=user_headers()) as second:
            two = second.receive_json()["session_token"]
            assert one != two and len(main.sessions) == 2
            p1, p2 = main.sessions[one], main.sessions[two]
            assert p1.manager.avatar.vidu is not p2.manager.avatar.vidu
            assert p1.manager.shared is p2.manager.shared
            p1.history.append({"role": "user", "content": "first-private"})
            p2.history.append({"role": "user", "content": "second-private"})
            assert client.put("/api/persona", json={"prompt": "forged"}).status_code == 409
            assert client.put("/api/persona", json={"prompt": "小月，只属于第一人"},
                              headers={"x-conversation-token": one}).status_code == 200
            assert first.receive_json()["type"] == "interrupted"
            assert not p1.history and p2.history[0]["content"] == "second-private"
            assert p2.generation == 0
            assert client.get("/api/persona", headers={"x-conversation-token": one}).json()["prompt"] == "小月，只属于第一人"
            assert client.get("/api/persona", headers={"x-conversation-token": two}).json()["prompt"] != "小月，只属于第一人"
            assert client.post("/api/models/switch", json={"selection": {}},
                               headers={"x-conversation-token": one}).status_code == 409
        assert one in main.sessions and two not in main.sessions
        assert not p1.stop_event.is_set()
        first.send_json({"type": "interrupt"})
        assert first.receive_json()["type"] == "interrupted"
    assert not main.sessions
    assert client.get("/api/status", headers={"x-conversation-token": one}).status_code == 409


def test_avatar_capacity_rejects_before_creating_paid_session_and_releases_slot(client, monkeypatch):
    from app import main
    from app.sessions import new_token
    from contextlib import ExitStack
    main.manager.avatar.config.update(enabled=True, provider="vidu", mode="realtime")
    monkeypatch.setattr(main, "MAX_AVATARS", 2)
    opened = []

    class ControlSocket:
        closed = False
        def send(self, raw):
            pass
        def close(self):
            self.closed = True

    with ExitStack() as stack:
        users = []
        for index in range(3):
            ws = stack.enter_context(client.websocket_connect("/ws", headers={
                "origin": "http://testserver", "cookie": "xiaoman_profile=" + new_token()}))
            token = ws.receive_json()["session_token"]
            session = main.sessions[token].manager.avatar.vidu
            def open_session(portrait, mode, persona, target=session, number=index):
                target.ws, target.live_id, target.mode = ControlSocket(), str(number), mode
                opened.append(number)
                return {"provider": "trtc"}
            monkeypatch.setattr(session, "open", open_session)
            users.append((token, session))
        for token, _ in users[:2]:
            assert client.post("/api/avatar/vidu/connect", headers={"x-conversation-token": token}).status_code == 200
        token, session = users[2]
        assert client.post("/api/avatar/vidu/connect", headers={"x-conversation-token": token}).status_code == 429
        assert opened == [0, 1] and session.ws is None
        assert client.post("/api/avatar/vidu/connect", headers={"x-conversation-token": users[0][0]}).status_code == 409
        assert opened == [0, 1], "Duplicate clicks must not create another paid session"
        assert client.post("/api/avatar/vidu/disconnect", headers={"x-conversation-token": users[0][0]}).status_code == 200
        assert users[1][1].ws is not None
        assert client.post("/api/avatar/vidu/connect", headers={"x-conversation-token": token}).status_code == 200
        assert opened == [0, 1, 2]
