"""Start the local app and open its UI after it becomes healthy."""

import signal
import subprocess
import sys
import time
import webbrowser

import httpx


def main():
    url = "http://127.0.0.1:7860"
    with httpx.Client(timeout=2, trust_env=False) as client:
        try:
            client.get(url + "/api/status").raise_for_status()
        except httpx.HTTPError:
            pass
        else:
            webbrowser.open(url)
            print("7860 端口已有服务，已打开页面。")
            return
        process = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1",
                                    "--port", "7860", "--ws-max-size", "16384"])
        try:
            deadline = time.monotonic() + 180
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise RuntimeError("服务启动失败，请查看日志。")
                try:
                    client.get(url + "/api/status").raise_for_status()
                    webbrowser.open(url)
                    print("小满已启动。关闭此终端或按 Ctrl+C 停止。")
                    break
                except httpx.HTTPError:
                    time.sleep(0.5)
            else:
                raise RuntimeError("服务启动超时，请查看 logs/app.log。")
            process.wait()
        except KeyboardInterrupt:
            pass
        finally:
            if process.poll() is None:
                process.send_signal(signal.SIGINT)
                try:
                    process.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()


if __name__ == "__main__":
    main()
