import json
import os
import threading
from urllib.parse import urlparse

import httpx


class MiniMaxError(RuntimeError):
    pass


def api_base_url():
    value = os.environ.get("MINIMAX_BASE_URL", "https://api.minimaxi.com/v1").rstrip("/")
    parsed = urlparse(value)
    if (parsed.scheme != "https" or parsed.hostname not in ("api.minimaxi.com", "api.minimax.io")
            or parsed.path != "/v1" or parsed.username or parsed.password or parsed.query or parsed.fragment
            or parsed.port not in (None, 443)):
        raise MiniMaxError("MINIMAX_BASE_URL 必须为 https://api.minimaxi.com/v1 或 https://api.minimax.io/v1")
    return value


class MiniMaxLLM:
    """Official HTTPS API, streaming visible content; key stays on the server."""

    def __init__(self, model, client=None):
        key = os.environ.get("MINIMAX_API_KEY", "").strip()
        if not key:
            raise MiniMaxError("请在本机设置 MINIMAX_API_KEY 并重启服务，密钥无需输入网页。")
        self.model = model
        self.base_url = api_base_url()
        self.client = client or httpx.Client(
            timeout=httpx.Timeout(60, connect=10), trust_env=False, follow_redirects=False,
        )
        self.client.headers.update({"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
        self._response = None
        self._lock = threading.Lock()
        self._closed = False

    @staticmethod
    def _error(response):
        reasons = {401: "API key 无效或服务区域不匹配", 403: "API key 没有此模型权限", 429: "请求频率或账户额度受限"}
        return MiniMaxError(f"MiniMax 请求失败（HTTP {response.status_code}）：{reasons.get(response.status_code, '请检查账户额度、模型权限或稍后重试')}")

    def create_chat_completion(self, *, messages, max_tokens=2048, temperature=0.8, top_p=0.9, stream=True):
        if self._closed:
            raise MiniMaxError("MiniMax 连接已关闭，请重新连接模型。")
        payload = {"model": self.model, "messages": messages, "stream": True, "reasoning_split": True,
                   "max_completion_tokens": max_tokens, "temperature": temperature, "top_p": top_p}
        try:
            with self.client.stream("POST", f"{self.base_url}/chat/completions", json=payload) as response:
                if response.status_code != 200:
                    raise self._error(response)
                if "text/event-stream" not in response.headers.get("content-type", ""):
                    response.read()
                    try:
                        data = response.json()
                        code = data.get("base_resp", {}).get("status_code", "unknown")
                    except ValueError:
                        code = "invalid-response"
                    raise MiniMaxError(f"MiniMax 未返回流式响应（错误码 {code}），请检查账户额度与模型权限。")
                with self._lock:
                    self._response = response
                complete = False
                for line in response.iter_lines():
                    if not line.startswith("data:"):
                        continue
                    value = line[5:].strip()
                    if not value:
                        continue
                    if value == "[DONE]":
                        complete = True
                        break
                    try:
                        data = json.loads(value)
                    except ValueError as exc:
                        raise MiniMaxError("MiniMax 流式响应格式异常，请重试。") from exc
                    if data.get("base_resp", {}).get("status_code", 0) != 0 or data.get("error"):
                        code = data.get("base_resp", {}).get("status_code", "api-error")
                        raise MiniMaxError(f"MiniMax 返回错误（{code}），请检查账户额度与模型权限。")
                    choices = data.get("choices", [])
                    if not choices:
                        continue
                    choice = choices[0]
                    if choice.get("finish_reason") in ("stop", "length"):
                        complete = True
                    # Never forward or store reasoning_content.
                    text = choice.get("delta", {}).get("content")
                    if text:
                        yield {"choices": [{"delta": {"content": text}}]}
                if not complete:
                    raise MiniMaxError("MiniMax 响应中途断开，请重试。")
        except httpx.TimeoutException as exc:
            raise MiniMaxError("MiniMax 响应超时，请检查网络后重试。") from exc
        except httpx.HTTPError as exc:
            raise MiniMaxError("无法连接 MiniMax，请检查网络和服务区域设置。") from exc
        finally:
            with self._lock:
                self._response = None

    def cancel(self):
        with self._lock:
            response = self._response
        if response is not None:
            response.close()

    def close(self):
        self._closed = True
        self.cancel()
        self.client.close()
