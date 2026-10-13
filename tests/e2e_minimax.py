"""Explicit LIVE MiniMax browser test. Requires an already running configured server."""

from pathlib import Path

from playwright.sync_api import expect, sync_playwright


def run():
    errors = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=playwright.chromium.executable_path)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto("http://127.0.0.1:7860")
        expect(page.locator("#memory-label")).to_contain_text("对话已就绪")
        page.locator("#message-input").fill("你好，小满。请用一句话和我打个招呼。")
        page.locator("#message-input").press("Enter")
        expect(page.locator("#caption-name")).to_have_text("小满", timeout=60000)
        expect(page.locator("#caption-text")).not_to_have_text("今天过得怎么样？", timeout=60000)
        expect(page.locator("#orb-label")).to_have_text("开始对话", timeout=60000)
        reply = page.locator("#caption-text").inner_text()
        assert len(reply.strip()) > 2
        assert "<think>" not in reply
        page.get_by_role("button", name="聊天记录", exact=True).click()
        expect(page.locator(".history-message")).to_have_count(2)
        page.get_by_role("button", name="关闭侧栏").click()
        output = Path(__file__).resolve().parent.parent / "output"
        output.mkdir(exist_ok=True)
        page.screenshot(path=str(output / "minimax-chat.png"), full_page=True)
        browser.close()
    assert not errors, errors
    print("PASS: live MiniMax browser chat, streamed reply, persisted history, and no TTS dependency")
    print(f"Reply: {reply}")


if __name__ == "__main__":
    run()
