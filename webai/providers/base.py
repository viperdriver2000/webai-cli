"""Abstract base class for web AI providers."""
import asyncio
import shutil
import tempfile
from abc import ABC, abstractmethod
from pathlib import Path
from playwright.async_api import async_playwright, BrowserContext, Page


class BaseProvider(ABC):
    """Base class for all web AI chat providers.

    Subclasses must implement the abstract methods and set class-level
    configuration for selectors and URLs.
    """

    # --- Override in subclass ---
    name: str = ""
    url: str = ""
    # CSS selectors
    input_selector: str = ""          # chat input area
    send_button_selector: str = ""    # send button (optional, fallback to Enter)
    response_selector: str = ""       # response content elements
    new_chat_selector: str = ""       # new chat button (optional)
    file_input_selector: str = 'input[type="file"]'
    attach_button_selector: str = ""  # button to reveal file input

    def __init__(self, profile_dir: Path, headless: bool = True):
        self._base_dir = profile_dir
        self.headless = headless
        self._session_dir: Path | None = None
        self._playwright = None
        self._context: BrowserContext | None = None
        self._page: Page | None = None
        self._response_count = 0
        self._last_response_text = ""

    @property
    def page(self) -> Page:
        if self._page is None:
            raise RuntimeError("Browser not started")
        return self._page

    # --- Browser lifecycle ---

    async def start(self):
        """Start browser, open login flow if no profile exists."""
        first_run = not self._base_dir.exists()
        if first_run:
            self._session_dir = self._base_dir
            self._session_dir.mkdir(parents=True, exist_ok=True)
        else:
            self._session_dir = Path(tempfile.mkdtemp(
                prefix="webai-", dir=self._base_dir.parent
            ))
            shutil.copytree(self._base_dir, self._session_dir, dirs_exist_ok=True)
        (self._session_dir / "SingletonLock").unlink(missing_ok=True)

        self._playwright = await async_playwright().start()
        headless = self.headless and not first_run
        executable = self._find_chromium()
        self._context = await self._playwright.chromium.launch_persistent_context(
            str(self._session_dir),
            headless=headless,
            executable_path=executable,
            accept_downloads=True,
            args=["--disable-blink-features=AutomationControlled"],
        )
        self._page = self._context.pages[0] if self._context.pages else await self._context.new_page()

        if first_run:
            await self._login_flow()
        else:
            await self._page.goto(self.url)
            await self._page.wait_for_load_state("load")

    async def stop(self):
        """Close browser and save profile back."""
        if self._context:
            await self._context.close()
        if self._playwright:
            await self._playwright.stop()
        if self._session_dir and self._session_dir != self._base_dir:
            shutil.copytree(self._session_dir, self._base_dir, dirs_exist_ok=True)
            shutil.rmtree(self._session_dir, ignore_errors=True)

    async def _login_flow(self):
        """Open browser for manual login. Override for custom login flows."""
        print(f"First run ({self.name}): please log in in the browser window.")
        print("Do NOT close the browser. Press Enter here when done...")
        await self._page.goto(self.url)
        await asyncio.get_event_loop().run_in_executor(None, input)
        pages = self._context.pages
        self._page = pages[-1] if pages else await self._context.new_page()
        await self._page.goto(self.url)
        await self._page.wait_for_load_state("load")

    @staticmethod
    def _find_chromium() -> str | None:
        """Return system Chromium path if available, else None (Playwright bundled)."""
        system = Path("/snap/bin/chromium")
        if system.exists():
            return str(system)
        return shutil.which("chromium") or shutil.which("chromium-browser") or shutil.which("google-chrome")

    # --- Abstract methods (provider-specific) ---

    @abstractmethod
    async def send_message(self, text: str):
        """Type and submit a message."""
        ...

    @abstractmethod
    async def get_response_text(self, el) -> str:
        """Extract text from a response element, preserving markdown."""
        ...

    @abstractmethod
    async def stream_response(self):
        """Async generator yielding growing response text until stable."""
        ...

    # --- Common methods with sensible defaults ---

    async def new_chat(self):
        """Start a new conversation."""
        if self.new_chat_selector:
            try:
                btn = await self._page.query_selector(self.new_chat_selector)
                if btn:
                    await btn.click()
                    await asyncio.sleep(1)
                    self._response_count = 0
                    self._last_response_text = ""
                    return
            except Exception:
                pass
        # Fallback: navigate to base URL
        await self._page.goto(self.url)
        await self._page.wait_for_load_state("load")
        self._response_count = 0
        self._last_response_text = ""

    async def _count_images(self, el) -> int:
        """Count non-trivial images in a response element."""
        return await self._page.evaluate("""(el) => {
            let count = 0;
            for (const img of el.querySelectorAll('img')) {
                if (img.width >= 50 && !img.src.startsWith('data:image/svg')) count++;
            }
            return count;
        }""", el)

    async def _extract_images_from_element(self, el) -> list[bytes]:
        """Extract images from a single response element."""
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
        # Try download buttons
        results = []
        parent = await self._page.evaluate_handle(
            "(el) => el.parentElement?.parentElement || el", el,
        )
        dl_buttons = await parent.query_selector_all(
            'button[aria-label*="ownload"], button[aria-label*="erunterladen"], button[aria-label*="riginal"]'
        )
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
        # Fallback: fetch preview images
        if not results:
            for url in img_urls:
                try:
                    resp = await self._page.request.get(url)
                    if resp.ok:
                        results.append(await resp.body())
                except Exception:
                    pass
        return results

    async def extract_images(self) -> list[bytes]:
        """Extract images from the last response."""
        els = await self._page.query_selector_all(self.response_selector)
        if not els:
            return []
        return await self._extract_images_from_element(els[-1])

    async def extract_all_images(self) -> list[tuple[int, list[bytes]]]:
        """Extract images from ALL responses."""
        els = await self._page.query_selector_all(self.response_selector)
        results = []
        for i, el in enumerate(els):
            count = await self._count_images(el)
            if count > 0:
                images = await self._extract_images_from_element(el)
                if images:
                    results.append((i, images))
        return results

    async def upload_image(self, image_path: Path):
        """Upload an image file."""
        await self._page.wait_for_selector(self.input_selector, timeout=30000)
        file_input = await self._page.query_selector(self.file_input_selector)
        if not file_input and self.attach_button_selector:
            attach_btn = await self._page.query_selector(self.attach_button_selector)
            if attach_btn:
                await attach_btn.click()
                await asyncio.sleep(0.5)
            file_input = await self._page.query_selector(self.file_input_selector)
        if not file_input:
            raise RuntimeError("Could not find file input element")
        await file_input.set_input_files(str(image_path))
        await asyncio.sleep(1)

    async def get_models(self) -> dict[str, dict]:
        """Return available models. Override for providers with model selection."""
        return {}

    async def select_model(self, name: str) -> str:
        """Select a model. Override for providers with model selection."""
        raise NotImplementedError(f"{self.name} does not support model selection via UI")

    # --- Helper for common send pattern ---

    async def _type_and_send(self, text: str):
        """Common pattern: click input, insert text, click send or press Enter."""
        await self._page.wait_for_selector(self.input_selector, timeout=30000)
        await self._page.locator(self.input_selector).click()
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

    async def _poll_response(self):
        """Common pattern: wait for response, yield text until stable."""
        # Wait for new response element or text change
        for _ in range(120):
            await asyncio.sleep(0.5)
            els = await self._page.query_selector_all(self.response_selector)
            if len(els) > self._response_count:
                break
            if els:
                last_text = await self.get_response_text(els[-1])
                if last_text != self._last_response_text:
                    break
                img_count = await self._count_images(els[-1])
                if img_count > 0:
                    break
        else:
            return
        # Poll until stable
        prev, stable = "", 0
        prev_imgs = 0
        while stable < 3:
            await asyncio.sleep(0.5)
            els = await self._page.query_selector_all(self.response_selector)
            current = await self.get_response_text(els[-1]) if els else ""
            cur_imgs = await self._count_images(els[-1]) if els else 0
            if (current and current != prev) or cur_imgs != prev_imgs:
                if current:
                    yield current
                stable = 0
            elif current or cur_imgs > 0:
                stable += 1
            prev = current
            prev_imgs = cur_imgs
        self._response_count = len(els)
