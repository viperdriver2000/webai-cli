"""Google Gemini provider."""
import asyncio
import re
from webai.providers import register
from webai.providers.base import BaseProvider


@register("gemini")
class GeminiProvider(BaseProvider):
    name = "Gemini"
    url = "https://gemini.google.com"
    input_selector = "input-area-v2"
    send_button_selector = 'button[aria-label="Nachricht senden"], button[aria-label="Send message"]'
    response_selector = "message-content"
    new_chat_selector = ""  # uses URL navigation
    attach_button_selector = (
        'button[aria-label*="nhang"], button[aria-label*="ttach"], '
        'button[aria-label*="pload"], button[aria-label*="datei"]'
    )

    async def _login_flow(self):
        print("First run (Gemini): please log in with your Google account.")
        print("Do NOT close the browser. Press Enter here when done...")
        await self._page.goto("https://accounts.google.com")
        await asyncio.get_event_loop().run_in_executor(None, input)
        pages = self._context.pages
        self._page = pages[-1] if pages else await self._context.new_page()
        await self._page.goto(self.url)
        await self._page.wait_for_load_state("load")

    async def send_message(self, text: str):
        await self._page.wait_for_selector(self.input_selector, timeout=30000)
        els = await self._page.query_selector_all(self.response_selector)
        self._response_count = len(els)
        self._last_response_text = await self.get_response_text(els[-1]) if els else ""
        await self._page.locator(self.input_selector).click()
        await self._page.evaluate(
            "text => document.execCommand('insertText', false, text)", text
        )
        await asyncio.sleep(0.3)
        sent = await self._page.evaluate("""() => {
            const btn = document.querySelector('button[aria-label="Nachricht senden"]')
                     || document.querySelector('button[aria-label="Send message"]');
            if (btn && !btn.disabled) { btn.click(); return true; }
            return false;
        }""")
        if not sent:
            await self._page.keyboard.press("Enter")
        try:
            await self._page.wait_for_url("**/app/**", timeout=5000)
        except Exception:
            pass

    async def get_response_text(self, el) -> str:
        text = await self._page.evaluate("""el => {
            function toMarkdown(node) {
                if (node.nodeType === 3) return node.textContent;
                const tag = node.tagName?.toLowerCase();
                if (tag === 'pre') {
                    const code = node.querySelector('code');
                    const lang = code?.className?.match(/language-(\\w+)/)?.[1] ?? '';
                    return '\\n```' + lang + '\\n' + (code || node).innerText + '\\n```\\n';
                }
                if (tag === 'code') return '`' + node.innerText + '`';
                if (tag === 'br') return '\\n';
                if (['p','div','li','h1','h2','h3'].includes(tag))
                    return Array.from(node.childNodes).map(toMarkdown).join('') + '\\n';
                return Array.from(node.childNodes).map(toMarkdown).join('');
            }
            return Array.from(el.childNodes).map(toMarkdown).join('').trim();
        }""", el)
        text = text.replace('\r\n', '\n').replace('\r', '\n')
        return re.sub(r'\n(\w{1,30})\n{2,6}```\n', lambda m: f'\n```{m.group(1).lower()}\n', text)

    async def stream_response(self):
        async for text in self._poll_response():
            yield text

    async def new_chat(self):
        await self._page.goto(self.url)
        await self._page.wait_for_load_state("load")
        self._response_count = 0
        self._last_response_text = ""

    async def _extract_images_from_element(self, el) -> list[bytes]:
        """Gemini-specific: try model-response parent for download buttons."""
        img_urls = await self._page.evaluate("""(el) => {
            const urls = [];
            for (const img of el.querySelectorAll('img')) {
                if (img.width >= 50 && img.src && !img.src.startsWith('data:image/svg'))
                    urls.push(img.src);
            }
            return urls;
        }""", el)
        if not img_urls:
            return []
        results = []
        parent = await self._page.evaluate_handle(
            "(el) => el.closest('model-response') || el.parentElement?.parentElement || el", el,
        )
        dl_buttons = await parent.query_selector_all(
            'button[aria-label*="ownload"], button[aria-label*="erunterladen"], button[aria-label*="riginal"]'
        )
        from pathlib import Path
        for btn in dl_buttons:
            try:
                async with self._page.expect_download(timeout=30000) as dl_info:
                    await btn.click()
                download = await dl_info.value
                path = await download.path()
                if path:
                    results.append(Path(path).read_bytes())
                    await download.delete()
            except Exception:
                pass
        if not results:
            for url in img_urls:
                try:
                    resp = await self._page.request.get(url)
                    if resp.ok:
                        results.append(await resp.body())
                except Exception:
                    pass
        return results

    async def _open_model_picker(self) -> dict[str, dict]:
        await self._page.locator(
            'button[aria-label="Modusauswahl öffnen"], button[aria-label="Open model picker"]'
        ).click()
        await asyncio.sleep(0.5)
        return await self._page.evaluate("""() => {
            const result = {};
            document.querySelectorAll('button[data-test-id^="bard-mode-option-"]').forEach(btn => {
                const id = btn.getAttribute('data-test-id').replace('bard-mode-option-', '');
                const name = btn.querySelector('span.mode-title')?.innerText?.trim() ?? id;
                const desc = btn.querySelector('span.mode-desc')?.innerText?.trim() ?? '';
                result[id] = {name, desc};
            });
            return result;
        }""")

    async def get_models(self) -> dict[str, dict]:
        models = await self._open_model_picker()
        await self._page.keyboard.press("Escape")
        return models

    async def select_model(self, mode: str) -> str:
        models = await self._open_model_picker()
        key = mode.lower()
        match = (
            key if key in models else
            next((k for k in models if k.startswith(key)), None) or
            next((k for k, v in models.items() if v["name"].lower().startswith(key)), None)
        )
        if match is None:
            await self._page.keyboard.press("Escape")
            available = ", ".join(f"{k} ({v['name']})" for k, v in models.items())
            raise ValueError(f"Unknown mode: {mode!r}. Available: {available}")
        btn = self._page.locator(f'button[data-test-id="bard-mode-option-{match}"]')
        if await btn.get_attribute("disabled") is not None:
            await self._page.keyboard.press("Escape")
            raise ValueError(f"Mode {mode!r} ({models[match]['name']}) requires a paid plan.")
        await btn.click()
        return models[match]["name"]
