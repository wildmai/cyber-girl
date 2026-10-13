"""Desktop-owned loopback server; parent pipe EOF requests graceful shutdown."""

import os
import socket
import sys
import threading
import time

import uvicorn


def refresh_user_environment():
    if sys.platform != "win32":
        return
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
            for name in ("MINIMAX_API_KEY", "MINIMAX_BASE_URL", "VIDU_API_KEY"):
                try:
                    value, _ = winreg.QueryValueEx(key, name)
                    if isinstance(value, str) and value.strip():
                        os.environ[name] = value.strip()
                except FileNotFoundError:
                    pass
    except FileNotFoundError:
        pass


def watch_parent(pipe, server):
    if sys.platform == "win32":
        # Blocking CRT stdin reads can deadlock DLL imports (NumPy / OpenBLAS).
        # Probe the pipe directly without holding a CRT stream lock.
        import ctypes
        import msvcrt
        from ctypes import wintypes
        peek = ctypes.WinDLL("kernel32", use_last_error=True).PeekNamedPipe
        peek.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD,
                         ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
        peek.restype = wintypes.BOOL
        handle = msvcrt.get_osfhandle(pipe.fileno())
        while peek(handle, None, 0, None, None, None):
            time.sleep(0.5)
    else:
        pipe.read(1)
    server.should_exit = True


def main():
    refresh_user_environment()
    os.environ["SAIBOGIRL_DESKTOP"] = "1"
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        # A stable origin preserves browser chat history and preferences across launches.
        listener.bind(("127.0.0.1", 7861))
        port = listener.getsockname()[1]
        server = uvicorn.Server(uvicorn.Config("app.main:app", host="127.0.0.1", port=port,
            ws_max_size=16384, timeout_graceful_shutdown=5))
        threading.Thread(target=watch_parent, args=(sys.stdin.buffer, server), daemon=True).start()
        print(f"XIAOMAN_PORT={port}", flush=True)
        server.run(sockets=[listener])


if __name__ == "__main__":
    main()
