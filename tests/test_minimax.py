import json

import httpx
import pytest

from app.minimax import MiniMaxError, MiniMaxLLM, api_base_url


def make_model(monkeypatch, handler):
    monkeypatch.setenv("MINIMAX_API_KEY", "unit-test-secret")
    monkeypatch.setenv("MINIMAX_BASE_URL", "https://api.minimaxi.com/v1")
    return MiniMaxLLM("MiniMax-M2.7", client=httpx.Client(transport=httpx.MockTransport(handler)))


def test_streaming_request_uses_official_api_and_discards_reasoning(monkeypatch):
    def handler(request):
        assert str(request.url) == "https://api.minimaxi.com/v1/chat/completions"
        assert request.headers["authorization"] == "Bearer unit-test-secret"
        data = json.loads(request.content)
        assert data["model"] == "MiniMax-M2.7"
        assert data["stream"] is True
        assert data["reasoning_split"] is True
        assert data["max_completion_tokens"] == 2048
        chunks = [
            {"choices": [{"delta": {"reasoning_content": "private thought"}}]},
            {"choices": [{"delta": {"content": "你好。"}}]},
            {"choices": [{"delta": {}, "finish_reason": "stop"}]},
        ]
        content = "\n".join(f"data: {json.dumps(c)}\n" for c in chunks) + "data: [DONE]\n\n"
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, text=content)

    model = make_model(monkeypatch, handler)
    try:
        chunks = list(model.create_chat_completion(messages=[{"role": "user", "content": "你好"}]))
        assert chunks == [{"choices": [{"delta": {"content": "你好。"}}]}]
    finally:
        model.close()


@pytest.mark.parametrize("code, message", [(401, "API key"), (403, "权限"), (429, "额度")])
def test_http_errors_are_actionable_and_never_echo_credentials(monkeypatch, code, message):
    model = make_model(monkeypatch, lambda request: httpx.Response(code, text="unit-test-secret"))
    try:
        with pytest.raises(MiniMaxError, match=message) as error:
            list(model.create_chat_completion(messages=[]))
        assert "unit-test-secret" not in str(error.value)
    finally:
        model.close()


def test_error_in_http_200_body_is_detected(monkeypatch):
    model = make_model(monkeypatch, lambda request: httpx.Response(200, json={"base_resp": {"status_code": 1008}}))
    try:
        with pytest.raises(MiniMaxError, match="1008"):
            list(model.create_chat_completion(messages=[]))
    finally:
        model.close()


def test_truncated_stream_is_not_treated_as_success(monkeypatch):
    model = make_model(monkeypatch, lambda request: httpx.Response(200, headers={"content-type": "text/event-stream"},
        text='data: {"choices":[{"delta":{"content":"partial"}}]}\n\n'))
    try:
        with pytest.raises(MiniMaxError, match="中途断开"):
            list(model.create_chat_completion(messages=[]))
    finally:
        model.close()


def test_missing_key_is_explicit(monkeypatch):
    monkeypatch.delenv("MINIMAX_API_KEY", raising=False)
    with pytest.raises(MiniMaxError, match="MINIMAX_API_KEY"):
        MiniMaxLLM("MiniMax-M2.7")


@pytest.mark.parametrize("url", ["http://api.minimaxi.com/v1", "https://evil.example/v1", "https://api.minimaxi.com:444/v1"])
def test_api_key_cannot_be_sent_to_custom_hosts(monkeypatch, url):
    monkeypatch.setenv("MINIMAX_BASE_URL", url)
    with pytest.raises(MiniMaxError, match="MINIMAX_BASE_URL"):
        api_base_url()
