"""Explicit live smoke request using a synthetic greeting, never user chat history."""

import json
from urllib.parse import urlparse

from app.minimax import MiniMaxLLM, api_base_url
from app.offline import enable_offline


def run():
    enable_offline(urlparse(api_base_url()).hostname)
    model = MiniMaxLLM("MiniMax-M2.7")
    try:
        reply = "".join(
            chunk["choices"][0]["delta"]["content"] for chunk in model.create_chat_completion(
                messages=[{"role": "user", "content": "这是接口连通性测试。请仅回复：你好，我是小满。"}],
                max_tokens=512,
            )
        )
        if not reply.strip():
            raise RuntimeError("MiniMax returned no visible text")
        print(json.dumps({"provider": "MiniMax", "endpoint": api_base_url(), "reply": reply}, ensure_ascii=False))
    finally:
        model.close()


if __name__ == "__main__":
    run()
