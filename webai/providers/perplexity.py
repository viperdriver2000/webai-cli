"""Perplexity AI provider."""
import asyncio
from webai.providers import register
from webai.providers.base import BaseProvider


@register("perplexity")
class PerplexityProvider(BaseProvider):
    name = "Perplexity"
    url = "https://www.perplexity.ai/"
    input_selector = 'div#ask-input[contenteditable="true"]'
    send_button_selector = ''  # Enter key works
    response_selector = 'div.scrollable-container'
    new_chat_selector = 'a[href="/"]'
    attach_button_selector = ''

    async def send_message(self, text: str):
        await self._page.wait_for_selector(self.input_selector, timeout=30000)
        els = await self._page.query_selector_all(self.response_selector)
        self._response_count = len(els)
        self._last_response_text = await self.get_response_text(els[-1]) if els else ""
        # Perplexity uses Lexical editor — execCommand doesn't work.
        # Use clipboard paste instead.
        input_el = await self._page.query_selector(self.input_selector)
        await input_el.click()
        await self._page.evaluate("""(text) => {
            const dt = new DataTransfer();
            dt.setData('text/plain', text);
            const el = document.querySelector('#ask-input');
            el.dispatchEvent(new ClipboardEvent('paste', {
                clipboardData: dt, bubbles: true, cancelable: true
            }));
        }""", text)
        await asyncio.sleep(0.5)
        # Verify text appeared, fallback to keyboard typing
        content = await self._page.evaluate(
            "() => document.querySelector('#ask-input')?.innerText?.trim() || ''"
        )
        if not content:
            # Fallback: type character by character (slow but works)
            await input_el.click()
            await self._page.keyboard.type(text, delay=20)
            await asyncio.sleep(0.3)
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
