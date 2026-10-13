"""Owned, loopback-only llama.cpp process; no downloads or API credentials."""

import json
import logging
import os
import shutil
import socket
import subprocess
import threading
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
log = logging.getLogger(__name__)
LOCAL_MODEL = {
    "id": "qwen35-9b-local", "stage": "llm", "backend": "llama.cpp",
    "name": "Qwen3.5 9B · 本地", "detail": "Uncensored Aggressive · Q4_K_M · llama.cpp",
    "path": "models/llm/Qwen3.5-9B-Uncensored-HauhauCS-Aggressive-Q4_K_M.gguf",
    "vram_gib": 6.5,
}


def server_binary():
    override = os.environ.get("LLAMA_SERVER_PATH", "").strip()
    if override:
        return str(Path(override).expanduser().resolve()) if Path(override).expanduser().is_file() else None
    name = "llama-server.exe" if os.name == "nt" else "llama-server"
    bundled = ROOT / "runtime/llama.cpp" / name
    return str(bundled) if bundled.is_file() else shutil.which(name)


class LocalLLMError(RuntimeError):
    pass


class LlamaEngine:
    def __init__(self, path, low_memory=False):
        self.path = Path(path)
        self.binary = server_binary()
        if not self.path.is_file() or not self.binary:
            raise LocalLLMError("缺少 GGUF 权重或 llama-server，请先运行 scripts/prepare_llm.py。")
        self.low_memory = low_memory
        self.lock = threading.RLock()
        self.process = None
        self.base_url = None

    def start(self, cancelled):
        if self.process is not None and self.process.poll() is None:
            return
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        self.base_url = f"http://127.0.0.1:{port}"
        command = [self.binary, "-m", str(self.path), "--host", "127.0.0.1", "--port", str(port),
                   "-c", "4096", "-np", "1", "-b", "256", "-ub", "128", "-ngl", "auto",
                   "--jinja", "--chat-template-kwargs", '{"enable_thinking":false}']
        (ROOT / "logs").mkdir(exist_ok=True)
        try:
            with (ROOT / "logs/llama-server.log").open("ab") as output:
                self.process = subprocess.Popen(command, cwd=Path(self.binary).parent,
                    stdout=output, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            log.info("Starting local llama.cpp; pid=%s context=4096", self.process.pid)
            deadline = time.monotonic() + 180
            with httpx.Client(timeout=2, trust_env=False) as client:
                while time.monotonic() < deadline:
                    if cancelled.is_set():
                        self.stop()
                        return
                    if self.process.poll() is not None:
                        raise LocalLLMError("llama-server 启动失败，请查看 logs/llama-server.log。")
                    try:
                        if client.get(self.base_url + "/health").status_code == 200:
                            return
                    except httpx.HTTPError:
                        pass
                    cancelled.wait(0.1)
            raise LocalLLMError("本地模型加载超时，请查看 logs/llama-server.log。")
        except Exception:
            self.stop()
            raise

    def stop(self):
        if self.process is not None:
            process, self.process = self.process, None
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
            log.info("Local llama.cpp released; pid=%s", process.pid)


class LocalLLM:
    def __init__(self, path=None, low_memory=False, engine=None, client=None):
        self.engine = engine or LlamaEngine(path, low_memory)
        self.owner = engine is None
        self.client = client or httpx.Client(timeout=httpx.Timeout(180, connect=5), trust_env=False)
        self.cancelled = threading.Event()
        self.response = None
        self.closed = False

    def fork(self):
        return LocalLLM(engine=self.engine)

    def create_chat_completion(self, *, messages, max_tokens=2048, temperature=0.8, top_p=0.9, stream=True):
        if self.closed:
            raise LocalLLMError("本地模型连接已关闭，请重新连接。")
        self.cancelled.clear()
        with self.engine.lock:
            if self.cancelled.is_set():
                return
            try:
                self.engine.start(self.cancelled)
                if self.cancelled.is_set():
                    return
                payload = {"messages": messages, "stream": True, "max_tokens": min(max_tokens, 1024),
                           "temperature": temperature, "top_p": top_p,
                           "chat_template_kwargs": {"enable_thinking": False}}
                with self.client.stream("POST", self.engine.base_url + "/v1/chat/completions", json=payload) as response:
                    self.response = response
                    if response.status_code != 200:
                        raise LocalLLMError(f"本地模型请求失败（HTTP {response.status_code}），请检查上下文长度及服务日志。")
                    complete = False
                    for line in response.iter_lines():
                        if self.cancelled.is_set():
                            return
                        if not line.startswith("data:"):
                            continue
                        value = line[5:].strip()
                        if value == "[DONE]":
                            complete = True
                            break
                        if not value:
                            continue
                        data = json.loads(value)
                        if data.get("error"):
                            raise LocalLLMError("本地模型返回错误，请查看 logs/llama-server.log。")
                        choices = data.get("choices", [])
                        if not choices:
                            continue
                        choice = choices[0]
                        complete |= choice.get("finish_reason") in ("stop", "length")
                        text = choice.get("delta", {}).get("content")
                        if text:
                            yield {"choices": [{"delta": {"content": text}}]}
                    if not complete and not self.cancelled.is_set():
                        raise LocalLLMError("本地模型响应中断，请重试。")
            except (httpx.HTTPError, ValueError) as exc:
                if not self.cancelled.is_set():
                    raise LocalLLMError("本地模型连接或流式响应异常，请查看 logs/llama-server.log。") from exc
            finally:
                self.response = None
                if self.engine.low_memory:
                    self.engine.stop()

    def cancel(self):
        self.cancelled.set()
        if self.response is not None:
            self.response.close()

    def close(self):
        self.closed = True
        self.cancel()
        self.client.close()
        if self.owner:
            with self.engine.lock:
                self.engine.stop()
