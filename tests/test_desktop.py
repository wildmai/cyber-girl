import os
from types import SimpleNamespace

from app.desktop_server import refresh_user_environment, watch_parent


def test_parent_pipe_eof_requests_graceful_shutdown():
    server = SimpleNamespace(should_exit=False)
    read_fd, write_fd = os.pipe()
    os.close(write_fd)
    with os.fdopen(read_fd, "rb") as pipe:
        watch_parent(pipe, server)
    assert server.should_exit


def test_windows_registry_keys_refresh_without_logging(monkeypatch, caplog):
    import sys
    from contextlib import nullcontext
    values = {"MINIMAX_API_KEY": "new-private-key", "VIDU_API_KEY": "vidu-private-key"}

    def query(key, name):
        if name not in values:
            raise FileNotFoundError
        return values[name], 1

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setitem(sys.modules, "winreg", SimpleNamespace(HKEY_CURRENT_USER=1,
        OpenKey=lambda *args: nullcontext(object()), QueryValueEx=query))
    monkeypatch.setenv("MINIMAX_API_KEY", "old-private-key")
    monkeypatch.setenv("VIDU_API_KEY", "old-vidu-key")
    refresh_user_environment()
    assert os.environ["MINIMAX_API_KEY"] == "new-private-key"
    assert os.environ["VIDU_API_KEY"] == "vidu-private-key"
    assert "private-key" not in caplog.text
