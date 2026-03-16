"""Kimi (Moonshot AI) Chat provider."""
import asyncio
from webai.providers import register
from webai.providers.base import BaseProvider


@register("kimi")
class KimiProvider(BaseProvider):
    name = "Kimi"
    url = "https://www.kimi.com/"
    input_selector = "div[contenteditable='true'][class*='editor'], textarea[class*='input']"
    send_button_selector = 'button[class*="send"], button[aria-label*="Send"]'
    response_selector = "div[class*='message-content'][class*='assistant'], div[class*='markdown-body']"
    new_chat_selector = 'div[class*="new-chat"], button[class*="new"]'
    attach_button_selector = 'button[class*="upload"], button[aria-label*="Upload"]'

    async def send_message(self, text: str):
        await self._page.wait_for_selector(self.input_selector, timeout=30000)
        els = await self._page.query_selector_all(self.response_selector)
        self._response_count = len(els)
        self._last_response_text = await self.get_response_text(els[-1]) if els else ""
        input_el = await self._page.query_selector(self.input_selector)
        await input_el.click()
        tag = await self._page.evaluate("el => el.tagName.toLowerCase()", input_el)
        if tag == "textarea":
            await input_el.fill(text)
        else:
            # contenteditable div — use execCommand
            await self._page.evaluate(
                "text => document.execCommand('insertText', false, text)", text
            )
        await asyncio.sleep(0.3)
        # Try send button
        if self.send_button_selector:
            sent = await self._page.evaluate(f"""() => {{
                const btn = document.querySelector('{self.send_button_selector}');
                if (btn && !btn.disabled) {{ btn.click(); return true; }}
                return false;
            }}""")
            if not sent:
                await self._page.keyboard.press("Enter")
        else:
            await self._page.keyboard.press("Enter")

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
                if (tag === 'code' && node.parentElement?.tagName?.toLowerCase() !== 'pre')
                    return '`' + node.innerText + '`';
                if (tag === 'br') return '\\n';
                if (['p','div','li','h1','h2','h3','h4'].includes(tag))
                    return Array.from(node.childNodes).map(toMarkdown).join('') + '\\n';
                return Array.from(node.childNodes).map(toMarkdown).join('');
            }
            return Array.from(el.childNodes).map(toMarkdown).join('').trim();
        }""", el)
        return text.replace('\r\n', '\n').replace('\r', '\n')

    async def stream_response(self):
        async for text in self._poll_response():
            yield text
