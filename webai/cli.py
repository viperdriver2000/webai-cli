"""Main CLI loop using prompt_toolkit."""
import asyncio
import subprocess
from prompt_toolkit import PromptSession
from prompt_toolkit.history import FileHistory
from prompt_toolkit.completion import WordCompleter
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.formatted_text import HTML
from rich.console import Console
from rich.live import Live
from rich.markdown import Markdown
from pathlib import Path

from webai import config as cfg
from webai.providers import get_provider
from webai.commands import SessionState, handle, BLOCKED_BRANCHES
from webai.context import load_git_context
from webai.patch import extract_diffs, normalize_diff, apply_diff

HISTORY_FILE = Path.home() / ".webai" / "history"
SLASH_COMMANDS = [
    "/upload", "/ref", "/edit", "/plan", "/apply", "/image", "/batch",
    "/gallery", "/save-images", "/provider", "/git", "/run", "/clear",
    "/history", "/model", "/paste", "/help", "/exit",
]

EDIT_INSTRUCTION = (
    "IMPORTANT: You are in edit mode. You MUST respond with unified diffs ONLY. "
    "Wrap each diff in a fenced code block: ```diff ... ```. "
    "No explanations, no prose — only fenced unified diffs.\n"
    "Always use a/ and b/ prefixes, even for new or deleted files:\n"
    "```diff\n--- a/path/to/file\n+++ b/path/to/file\n@@ -L,N +L,N @@\n"
    " context\n-removed\n+added\n```\n"
    "For new files use @@ -0,0 +1,N @@ and only + lines.\n"
    "For deleted files use @@ -1,N +0,0 @@ and only - lines.\n"
)

PLAN_INSTRUCTION = (
    "IMPORTANT: Edit mode is now disabled. "
    "Stop responding with unified diffs. "
    "Return to normal responses: explanations, analysis, discussion. "
    "Do NOT output any diffs or patches."
)

PRIME_INSTRUCTION = "Study this project context carefully. When done, reply with just 'OK'."

console = Console()


def _image_dir(state) -> Path:
    p = Path(state.image_dir).expanduser()
    return p if p.is_absolute() else state.cwd / p


async def _extract_and_save_images(browser, output_dir: Path, filename_prefix: str = "") -> list[Path]:
    from time import strftime
    await asyncio.sleep(2)
    images = await browser.extract_images()
    if not images:
        return []
    output_dir.mkdir(parents=True, exist_ok=True)
    saved = []
    ts = strftime("%Y%m%d-%H%M%S")
    for i, img_data in enumerate(images, 1):
        suffix = ".png" if img_data[:4] == b'\x89PNG' else ".jpg"
        if filename_prefix:
            base = Path(filename_prefix).stem
            name = f"{base}{suffix}" if len(images) == 1 else f"{base}-{i}{suffix}"
        else:
            name = f"{ts}-{i}{suffix}"
        fname = output_dir / name
        fname.write_bytes(img_data)
        console.print(f"[bold cyan]Image saved:[/bold cyan] {fname}")
        saved.append(fname)
    return saved


async def _send_and_get_images(browser, message: str, output_dir: Path, filename: str = "") -> list[Path]:
    try:
        await browser.send_message(message)
    except Exception:
        console.print("[bold red]Browser closed.[/bold red]")
        return []
    response_text = ""
    with Live(console=console, refresh_per_second=8, transient=True) as live:
        async for text in browser.stream_response():
            response_text = text
            live.update(Markdown(text))
    if response_text:
        console.print(Markdown(response_text))
    return await _extract_and_save_images(browser, output_dir, filename)


def _build_session() -> PromptSession:
    HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    completer = WordCompleter(SLASH_COMMANDS, sentence=True)
    kb = KeyBindings()

    @kb.add("escape", "enter")
    def _newline(event):
        event.current_buffer.insert_text("\n")

    return PromptSession(
        history=FileHistory(str(HISTORY_FILE)),
        completer=completer,
        key_bindings=kb,
        multiline=False,
    )


async def _paste_mode() -> str:
    console.print("[dim]Paste mode: enter text, finish with Ctrl+D[/dim]")
    lines = []
    try:
        while True:
            line = await asyncio.get_event_loop().run_in_executor(None, input)
            lines.append(line)
    except EOFError:
        pass
    return "\n".join(lines)


async def _save_all_images(browser, state):
    from time import strftime
    console.print("[dim]Scanning chat for images...[/dim]")
    all_images = await browser.extract_all_images()
    if not all_images:
        console.print("No images found in chat history.")
        return
    img_dir = _image_dir(state)
    img_dir.mkdir(parents=True, exist_ok=True)
    ts = strftime("%Y%m%d-%H%M%S")
    total = sum(len(imgs) for _, imgs in all_images)
    for resp_idx, images in all_images:
        for i, img_data in enumerate(images, 1):
            suffix = ".png" if img_data[:4] == b'\x89PNG' else ".jpg"
            fname = img_dir / f"{ts}-r{resp_idx}-{i}{suffix}"
            fname.write_bytes(img_data)
            console.print(f"[bold cyan]Image saved:[/bold cyan] {fname}")
    console.print(f"\n[bold green]{total} images saved from {len(all_images)} responses.[/bold green]")


def _load_batch_progress(progress_file: Path) -> dict:
    import json
    if progress_file.exists():
        return json.loads(progress_file.read_text())
    return {"done": [], "failed": []}


def _save_batch_progress(progress_file: Path, progress: dict):
    import json
    progress_file.parent.mkdir(parents=True, exist_ok=True)
    progress_file.write_text(json.dumps(progress, indent=2))


async def _run_batch(browser, state, filepath: str, raw_input: str):
    from webai.batch import parse_prompt_file
    p = Path(filepath).expanduser()
    batch = parse_prompt_file(p)
    prompts = batch.prompts

    parts = raw_input.strip().split()
    start_at = None
    resume = "--resume" in parts
    max_retries = 1
    batch_model = None
    if "--start-at" in parts:
        idx = parts.index("--start-at")
        if idx + 1 < len(parts):
            start_at = parts[idx + 1]
    if "--retries" in parts:
        idx = parts.index("--retries")
        if idx + 1 < len(parts):
            max_retries = int(parts[idx + 1])
    if "--model" in parts:
        idx = parts.index("--model")
        if idx + 1 < len(parts):
            batch_model = parts[idx + 1]

    subdir = Path(filepath).stem
    img_dir = _image_dir(state) / subdir
    progress_file = img_dir / ".batch-progress.json"
    progress = _load_batch_progress(progress_file)

    if resume and progress["done"]:
        done_set = set(progress["done"])
        skipped = [pr for pr in prompts if pr.filename in done_set]
        prompts = [pr for pr in prompts if pr.filename not in done_set]
        if skipped:
            console.print(f"[dim]Resuming: skipping {len(skipped)} already completed prompts[/dim]")
        if progress["failed"]:
            console.print(f"[dim]Will retry {len(progress['failed'])} previously failed prompts[/dim]")

    if start_at:
        for i, pr in enumerate(prompts):
            if start_at in pr.filename:
                prompts = prompts[i:]
                break

    total = len(prompts)
    failed = []

    old_model = None
    if batch_model:
        try:
            display = await browser.select_model(batch_model)
            old_model = state.model
            console.print(f"[dim]Batch model: {display}[/dim]")
        except Exception as e:
            console.print(f"[yellow]Warning: Could not set model {batch_model!r}: {e}[/yellow]")

    console.print(f"\n[bold]Batch: {total} prompts ({state.provider_name})[/bold]")
    console.print(f"[bold]Intro: {len(batch.intro)} chars (included in each prompt)[/bold]")
    console.print(f"[bold]Output: {img_dir}[/bold]")
    console.print(f"[bold]Retries: {max_retries}[/bold]\n")

    prompt_dir = p.parent

    cancelled = False
    for n, pr in enumerate(prompts, 1):
        console.print(f"\n[bold yellow]━━━ [{n}/{total}] {pr.filename} ━━━[/bold yellow]")
        if pr.note:
            console.print(f"[dim]Note: {pr.note}[/dim]")

        if batch.intro:
            full_prompt = (
                f"{batch.intro}\n\n"
                f"Now generate this image based on the characters and style above.\n"
                f"CRITICAL: The image must contain absolutely NO text, NO labels, NO names, "
                f"NO captions, NO speech bubbles, and NO written words of any kind.\n\n"
                f"{pr.prompt}"
            )
        else:
            full_prompt = pr.prompt

        saved = []
        try:
            for attempt in range(1, max_retries + 1):
                await browser.new_chat()
                await asyncio.sleep(1)

                if attempt > 1:
                    console.print(f"[dim]Retry {attempt}/{max_retries}...[/dim]")

                if pr.ref_image:
                    ref_path = Path(pr.ref_image).expanduser()
                    if not ref_path.is_absolute():
                        ref_path = prompt_dir / ref_path
                    if ref_path.exists():
                        console.print(f"[dim]Uploading ref: {ref_path.name}[/dim]")
                        try:
                            await browser.upload_image(ref_path)
                        except Exception as e:
                            console.print(f"[yellow]Ref upload failed: {e}[/yellow]")
                    else:
                        console.print(f"[yellow]Ref not found: {ref_path}[/yellow]")

                saved = await _send_and_get_images(browser, full_prompt, img_dir, pr.filename)
                if saved:
                    break
        except KeyboardInterrupt:
            console.print(f"\n[bold yellow]Batch paused at [{n}/{total}] {pr.filename}[/bold yellow]")
            _save_batch_progress(progress_file, progress)
            console.print(f"[dim]Progress saved. Use --resume to continue.[/dim]")
            cancelled = True
            break

        if saved:
            progress["done"].append(pr.filename)
            if pr.filename in progress["failed"]:
                progress["failed"].remove(pr.filename)
        else:
            console.print(f"[bold red]No images extracted for {pr.filename}[/bold red]")
            failed.append(pr.filename)
            if pr.filename not in progress["failed"]:
                progress["failed"].append(pr.filename)

        _save_batch_progress(progress_file, progress)

    if old_model:
        try:
            await browser.select_model(old_model)
        except Exception:
            pass

    if not cancelled:
        console.print(f"\n[bold green]━━━ Batch complete: {total - len(failed)}/{total} successful ━━━[/bold green]")
        if failed:
            console.print(f"[bold red]Failed: {', '.join(failed)}[/bold red]")
            console.print(f"[dim]Run with --resume to retry failed prompts[/dim]")


async def _query_provider(provider_name: str, prompt: str, conf) -> tuple[str, str]:
    """Query a single provider and return (name, response_text)."""
    ProviderClass = get_provider(provider_name)
    profile_dir = Path(conf.profile_dir).expanduser() / provider_name
    browser = ProviderClass(profile_dir, conf.headless)
    try:
        await browser.start()
        await browser.send_message(prompt)
        response_text = ""
        async for text in browser.stream_response():
            response_text = text
        return (browser.name, response_text)
    except Exception as e:
        return (browser.name, f"[Error: {e}]")
    finally:
        await browser.stop()


async def run_oneshot(provider_names: list[str], prompt: str, raw: bool = False):
    """Send a single prompt to one or more providers, print responses, then exit."""
    conf = cfg.load()
    if not provider_names:
        provider_names = [conf.provider]

    multi = len(provider_names) > 1

    if multi:
        # Run all providers concurrently
        console.print(f"[dim]Sending to {len(provider_names)} providers...[/dim]")
        tasks = [_query_provider(name, prompt, conf) for name in provider_names]
        results = await asyncio.gather(*tasks)
        for name, response_text in results:
            if raw:
                print(f"=== {name} ===")
                print(response_text)
                print()
            else:
                console.print(f"\n[bold cyan]━━━ {name} ━━━[/bold cyan]")
                if response_text:
                    console.print(Markdown(response_text))
                else:
                    console.print("[dim]No response[/dim]")
    else:
        # Single provider — stream with live rendering
        name = provider_names[0]
        ProviderClass = get_provider(name)
        profile_dir = Path(conf.profile_dir).expanduser() / name
        browser = ProviderClass(profile_dir, conf.headless)
        try:
            await browser.start()
        except Exception as e:
            console.print(f"[bold red]Error:[/bold red] Failed to start browser: {e}")
            return
        try:
            await browser.send_message(prompt)
            response_text = ""
            if raw:
                async for text in browser.stream_response():
                    response_text = text
                if response_text:
                    print(response_text)
            else:
                with Live(console=console, refresh_per_second=8, transient=True) as live:
                    async for text in browser.stream_response():
                        response_text = text
                        live.update(Markdown(text))
                if response_text:
                    console.print(Markdown(response_text))
        except Exception as e:
            console.print(f"[bold red]Error:[/bold red] {e}")
        finally:
            await browser.stop()


async def run(provider_override: str | None = None):
    conf = cfg.load()
    provider_name = provider_override or conf.provider
    cwd = Path.cwd()

    if not (cwd / ".git").exists():
        console.print(f"[bold red]Error:[/bold red] Not a git repository: {cwd}")
        return
    r = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=cwd, capture_output=True, text=True
    )
    if r.returncode != 0:
        console.print("[bold red]Error:[/bold red] Empty git repository — please make an initial commit first.")
        return
    branch = r.stdout.strip()
    console.print(f"[dim]Branch: {branch} — loading context...[/dim]")
    context = load_git_context(cwd)
    file_count = context.count("=== ")
    console.print(f"[dim]{len(context)} chars from {file_count} files[/dim]")

    state = SessionState(
        model=conf.model, session_context=context, cwd=cwd,
        system_prompt=conf.system_prompt, run_commands=conf.run_commands,
        image_dir=conf.image_dir, provider_name=provider_name,
    )

    # Create provider instance
    ProviderClass = get_provider(provider_name)
    profile_dir = Path(conf.profile_dir).expanduser() / provider_name
    browser = ProviderClass(profile_dir, conf.headless)
    session = _build_session()

    console.print(f"[bold green]webai-cli[/bold green] ({browser.name}) - type [bold]/help[/bold] for commands")
    console.print("Starting browser...")
    try:
        await browser.start()
    except Exception as e:
        console.print(f"[bold red]Error:[/bold red] Failed to start browser: {e}")
        return
    if conf.model:
        try:
            await browser.select_model(conf.model)
        except (NotImplementedError, Exception) as e:
            console.print(f"[yellow]Warning: {e}[/yellow]")
    console.print("Ready.\n")

    try:
        while True:
            try:
                rel = state.cwd.relative_to(Path.home())
                mode = " <ansired>EDIT</ansired>" if state.edit_mode else " <ansigreen>PLAN</ansigreen>"
                prov = f" <ansicyan>{browser.name}</ansicyan>"
                prompt = HTML(f"<ansigreen>{rel}{prov}{mode} ></ansigreen> ")
                user_input = await session.prompt_async(prompt)
            except (EOFError, KeyboardInterrupt):
                break

            user_input = user_input.strip()
            if not user_input:
                continue

            if user_input.startswith("/"):
                result = await handle(user_input, state, browser)
                if result == "__exit__":
                    break
                elif result == "__paste_mode__":
                    user_input = await _paste_mode()
                    if not user_input.strip():
                        continue
                elif result == "__apply__":
                    user_input = "write me a patch!"
                elif result and result.startswith("__image__:"):
                    img_prompt = result[len("__image__:"):]
                    await _send_and_get_images(browser, img_prompt, _image_dir(state))
                    continue
                elif result and result.startswith("__batch__:"):
                    filepath = result[len("__batch__:"):]
                    await _run_batch(browser, state, filepath, user_input)
                    continue
                elif result == "__save_images__":
                    await _save_all_images(browser, state)
                    continue
                else:
                    if result:
                        console.print(result)
                    continue

            # Context priming
            if state.session_context and not state.context_sent:
                prime_parts = []
                if state.system_prompt:
                    prime_parts.append(state.system_prompt)
                prime_parts.append(state.session_context)
                prime_parts.append(PRIME_INSTRUCTION)
                console.print("[dim]Sending context...[/dim]")
                try:
                    await browser.send_message("\n\n".join(prime_parts))
                    async for _ in browser.stream_response():
                        pass
                    state.context_sent = True
                except Exception:
                    console.print("[bold red]Browser closed.[/bold red]")
                    break

            # Assemble message
            parts = []
            if state.session_context and not state.context_sent:
                parts.append(state.session_context)
            if state.edit_mode:
                parts.append(EDIT_INSTRUCTION)
            if state.pending_plan_reset:
                parts.append(PLAN_INSTRUCTION)
                state.pending_plan_reset = False
            if state.pending_upload:
                parts.append(state.pending_upload)
                state.pending_upload = ""
            parts.append(user_input)
            message = "\n\n".join(parts)

            try:
                await browser.send_message(message)
                state.context_sent = True
            except Exception:
                console.print("[bold red]Browser closed.[/bold red]")
                break
            response_text = ""
            with Live(console=console, refresh_per_second=8, transient=True) as live:
                async for text in browser.stream_response():
                    response_text = text
                    live.update(Markdown(text))
            if response_text:
                console.print(Markdown(response_text))

            await _extract_and_save_images(browser, _image_dir(state))

            if state.edit_mode and response_text:
                diffs = [normalize_diff(d, state.cwd) for d in extract_diffs(response_text)]
                total = len(diffs)
                any_skipped = False
                for n, diff in enumerate(diffs, 1):
                    fname = next((l[6:] for l in diff.splitlines() if l.startswith("+++ b/")), "?")
                    console.print(f"\n[bold yellow]Patch {n}/{total}:[/bold yellow] {fname}")
                    console.print(diff, markup=False)
                    if state.auto_apply_patch:
                        console.print("[dim]Auto-applying patch...[/dim]")
                        answer = "y"
                    else:
                        answer = await asyncio.get_event_loop().run_in_executor(
                            None, lambda: input("Apply this patch? [y/N] ")
                        )
                    if answer.strip().lower() == "y":
                        ok, out = apply_diff(diff, state.cwd)
                        if ok:
                            console.print(f"[green]Applied.[/green] {out}")
                            state.session_context = load_git_context(state.cwd)
                            state.context_sent = False
                        else:
                            console.print(f"[red]Failed:[/red] {out}")
                    else:
                        any_skipped = True
                if any_skipped:
                    await browser.new_chat()
                    state.session_context = load_git_context(state.cwd)
                    state.context_sent = False
                    console.print("[dim]Patches skipped — cleared chat, context reloaded.[/dim]")

            if state.post_apply_plan_reset:
                state.edit_mode = False
                state.pending_plan_reset = True
                state.post_apply_plan_reset = False
                state.auto_apply_patch = False
                console.print("[dim]Returned to PLAN mode.[/dim]")

    finally:
        await browser.stop()


def main():
    import argparse
    from webai.providers import list_providers
    available = list_providers()
    p = argparse.ArgumentParser(prog="webai")
    p.add_argument("--provider", "-p", type=str, help="AI provider(s) — comma-separated for multi (e.g. chatgpt,claude,deepseek)")
    p.add_argument("--all", action="store_true", help="Send prompt to ALL providers (use with --prompt)")
    p.add_argument("--prompt", type=str, help="Send a single prompt and exit (one-shot mode)")
    p.add_argument("--raw", action="store_true", help="Output raw text instead of rendered markdown (for piping)")
    p.add_argument("--bot", action="store_true", help="Telegram bot mode")
    args = p.parse_args()

    # Parse provider list
    if args.all:
        providers = available
    elif args.provider:
        providers = [name.strip() for name in args.provider.split(",")]
        for name in providers:
            if name not in available:
                p.error(f"Unknown provider: {name!r}. Available: {', '.join(available)}")
    else:
        providers = []

    if args.bot:
        from webai.bot import run_bot
        asyncio.run(run_bot())
    elif args.prompt:
        asyncio.run(run_oneshot(providers, args.prompt, raw=args.raw))
    else:
        provider = providers[0] if providers else None
        asyncio.run(run(provider_override=provider))


if __name__ == "__main__":
    main()
