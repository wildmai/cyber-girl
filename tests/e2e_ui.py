"""Browser acceptance checks against the running local service; no model mocks."""

from pathlib import Path

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parent.parent


def run():
    output = ROOT / "output"
    output.mkdir(exist_ok=True)
    errors = []
    external = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=playwright.chromium.executable_path)
        page = browser.new_page(viewport={"width": 1440, "height": 900}, device_scale_factor=1)
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on("request", lambda request: external.append(request.url) if not request.url.startswith(("http://127.0.0.1:7860", "data:", "blob:")) else None)
        page.goto("http://127.0.0.1:7860")
        expect(page.locator("#memory-label")).to_contain_text("对话")
        expect(page.locator("h1")).to_have_text("小满")
        page.screenshot(path=str(output / "desktop.png"), full_page=True)
        page.get_by_role("button", name="模型与连接", exact=True).click()
        expect(page.locator("#drawer")).to_be_visible()
        expect(page.locator("#select-llm")).to_have_value("minimax-m27")
        page.locator("#select-llm").select_option("minimax-m27-fast")
        with page.expect_response("**/api/llm/connect") as switched:
            page.locator("#connect-minimax").click()
        assert switched.value.json()["selected"]["llm"] == "minimax-m27-fast"
        expect(page.locator("#toast-text")).to_contain_text("对话已就绪")
        assert page.request.get("http://127.0.0.1:7860/api/status").json()["selected"]["llm"] == "minimax-m27-fast"
        page.locator("#select-llm").select_option("minimax-m27")
        with page.expect_response("**/api/llm/connect") as switched:
            page.locator("#connect-minimax").click()
        assert switched.value.json()["selected"]["llm"] == "minimax-m27"
        expect(page.locator("#select-llm")).to_have_value("minimax-m27")
        page.locator("#select-stt").select_option("whisper-small")
        page.locator("#select-tts").select_option("qwen3-tts-17b")
        expect(page.locator("#budget-value")).to_have_text("8.6")
        expect(page.locator("#apply-models")).to_be_enabled()
        page.locator("#select-llm").select_option("minimax-m27")
        page.locator("#select-stt").select_option("whisper-base")
        page.locator("#select-tts").select_option("qwen3-tts-06b")
        page.locator("#select-stt").select_option("whisper-small")
        page.locator("#apply-models").click()
        expect(page.locator("#model-error")).to_contain_text("缺少本地模型")
        page.screenshot(path=str(output / "models.png"), full_page=True)
        page.get_by_role("button", name="关闭侧栏").click()
        page.get_by_role("button", name="偏好设置", exact=True).click()
        page.locator("#speaker").select_option("Serena")
        page.locator("#subtitles").uncheck()
        expect(page.locator(".conversation-caption")).to_be_hidden()
        page.locator("#subtitles").check()
        page.locator("#volume").fill("45")
        expect(page.locator("#volume-value")).to_have_text("45%")
        page.get_by_role("button", name="关闭侧栏").click()
        page.get_by_role("button", name="聊天记录", exact=True).click()
        expect(page.locator(".history-empty")).to_be_visible()
        page.keyboard.press("Escape")
        expect(page.locator("#drawer")).to_be_hidden()
        if not page.request.get("http://127.0.0.1:7860/api/status").json()["ready"]:
            page.locator("#voice-orb").click()
            expect(page.locator("#drawer")).to_be_visible()
            page.keyboard.press("Escape")
        if page.locator("#toast").is_visible():
            page.locator("#toast-close").click()
        page.locator("#message-input").fill("")
        page.set_viewport_size({"width": 390, "height": 844})
        page.screenshot(path=str(output / "mobile.png"), full_page=True)
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), "Horizontal overflow"
        page.get_by_role("button", name="模型与连接", exact=True).click()
        expect(page.locator("#drawer")).to_be_visible()
        page.screenshot(path=str(output / "mobile-models.png"), full_page=True)
        page.keyboard.press("Escape")
        # Reproduce a real two-line reply: flex layout must not crop its first line.
        page.locator("#caption-text").evaluate("element => { element.textContent = '你好呀，我今天挺好的，每一天都很充实。你今天过得怎么样？'; element.scrollTop = element.scrollHeight; }")
        for width, height, name in ((390, 844, "mobile-reply"), (1440, 900, "desktop-reply")):
            page.set_viewport_size({"width": width, "height": height})
            assert page.locator("#caption-text").evaluate("e => e.clientHeight >= Math.min(98, e.scrollHeight) - 1"), "Clipped subtitle"
            assert page.locator(".model-strip").bounding_box()["y"] < page.locator(".footer").bounding_box()["y"], "Reply overlaps footer"
            page.screenshot(path=str(output / f"{name}.png"), full_page=True)
        browser.close()
    assert not errors, errors
    assert not external, external
    print("PASS: desktop/mobile UI, missing-model errors, local VRAM estimates, preferences, history, keyboard, and local browser requests")
    print(f"Screenshots: {output}")


if __name__ == "__main__":
    run()
