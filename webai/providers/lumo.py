"""Lumo Chat provider."""
import asyncio
from webai.providers import register
from webai.providers.base import BaseProvider


@register("lumo")
class LumoProvider(BaseProvider):
    name = "Lumo"
    url = "https://lumo.chat/"
    input_selector = 'textarea, div[contenteditable="true"]'
    send_button_selector = 'button[type="submit"], button[aria-label*="Send"], button[aria-label*="Senden"]'
    response_selector = 'div[class*="message"][class*="assistant"], div[class*="markdown"], div[class*="response"]'
    new_chat_selector = 'a[href="/"], button[aria-label*="New"]'

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
            await self._page.evaluate(
                "text => document.execCommand('insertText', false, text)", text
            )
        await asyncio.sleep(0.3)
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
