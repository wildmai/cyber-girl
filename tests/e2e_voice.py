"""LIVE full voice test using a real WAV as Chromium's microphone device."""

import argparse
import json
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parent.parent


def run():
    parser = argparse.ArgumentParser()
    parser.add_argument("--audio", type=Path, required=True)
    args = parser.parse_args()
    errors, events = [], []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            executable_path=playwright.chromium.executable_path,
            args=["--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream",
                  f"--use-file-for-fake-audio-capture={args.audio.resolve()}%noloop"],
        )
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.on("pageerror", lambda error: errors.append(str(error)))

        def received(frame):
            event = json.loads(frame)
            if event["type"] == "audio":
                events.append({"type": "audio", "bytes_base64": len(event["data"])})
            else:
                events.append(event)
            if event["type"] in ("state", "user", "assistant", "error", "done", "audio"):
                print(event["type"] + (": " + event.get("state", "") if event["type"] == "state" else ""), flush=True)

        page.on("websocket", lambda socket: socket.on("framereceived", received))
        page.goto("http://127.0.0.1:7860")
        expect(page.locator("#memory-label")).to_contain_text("对话")
        page.get_by_role("button", name="模型与连接", exact=True).click()
        page.locator("#apply-models").click()
        expect(page.locator("#apply-models")).to_have_text("应用并热切换", timeout=180000)
        page.get_by_role("button", name="关闭侧栏").click()
        page.locator("#voice-orb").click()
        expect(page.locator("#mic-button")).to_have_attribute("aria-pressed", "true")
        expect(page.locator("#caption-name")).to_have_text("小满", timeout=90000)
        expect(page.locator("#caption-text")).not_to_have_text("今天过得怎么样？", timeout=90000)
        expect(page.locator("#orb-label")).to_have_text("正在聆听", timeout=300000)
        assert any(e["type"] == "user" for e in events), events
        assert any(e["type"] == "assistant" for e in events), events
        assert any(e["type"] == "audio" and e["bytes_base64"] > 1000 for e in events), events
        assert any(e["type"] == "done" for e in events), events
        assert not [e for e in events if e["type"] == "error"], events
        page.get_by_role("button", name="关闭麦克风", exact=True).click()
        page.screenshot(path=str(ROOT / "output/voice-chat.png"), full_page=True)
        page.get_by_role("button", name="聊天记录", exact=True).click()
        expect(page.locator(".history-message")).to_have_count(2)
        browser.close()
    assert not errors, errors
    print("PASS: Chromium microphone WAV -> AudioWorklet -> Silero -> Whisper -> MiniMax -> Qwen3-TTS -> browser playback")
    print(json.dumps([e for e in events if e["type"] in ("user", "assistant", "audio", "done")], ensure_ascii=False))


if __name__ == "__main__":
    run()
