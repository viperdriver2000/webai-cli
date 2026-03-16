"""Anthropic Claude provider."""
import asyncio
from webai.providers import register
from webai.providers.base import BaseProvider


@register("claude")
class ClaudeProvider(BaseProvider):
    name = "Claude"
    url = "https://claude.ai/new"
    input_selector = 'div[contenteditable="true"].ProseMirror, div[contenteditable="true"]'
    send_button_selector = 'button[aria-label="Send Message"], button[aria-label="Nachricht senden"]'
    response_selector = 'div[data-is-streaming], div.font-claude-message div.grid-cols-1 div.break-words'
    new_chat_selector = 'a[href="/new"]'
    attach_button_selector = 'button[aria-label*="Attach"], button[aria-label*="Anhang"]'

    async def send_message(self, text: str):
        await self._page.wait_for_selector(self.input_selector, timeout=30000)
        els = await self._page.query_selector_all(self.response_selector)
        self._response_count = len(els)
        self._last_response_text = await self.get_response_text(els[-1]) if els else ""
        # Claude uses ProseMirror contenteditable
        input_el = await self._page.query_selector(self.input_selector)
        await input_el.click()
        await self._page.evaluate(
            "text => document.execCommand('insertText', false, text)", text
        )
        await asyncio.sleep(0.3)
        sent = await self._page.evaluate("""() => {
            const btn = document.querySelector('button[aria-label="Send Message"]')
                     || document.querySelector('button[aria-label="Nachricht senden"]');
            if (btn && !btn.disabled) { btn.click(); return true; }
            return false;
        }""")
        if not sent:
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

    async def new_chat(self):
        await self._page.goto(self.url)
        await self._page.wait_for_load_state("load")
        self._response_count = 0
        self._last_response_text = ""
