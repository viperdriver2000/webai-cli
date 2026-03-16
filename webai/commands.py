"""Slash command parser and handlers."""
import asyncio
import subprocess
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable
from rich.console import Console
from rich.panel import Panel
from webai import context as ctx
from webai.batch import parse_prompt_file

BLOCKED_BRANCHES = {"master", "main", "qa", "devel"}
_console = Console()


@dataclass
class SessionState:
    session_context: str = ""
    pending_upload: str = ""
    model: str = ""
    edit_mode: bool = False
    cwd: Path = field(default_factory=Path.cwd)
    system_prompt: str = ""
    context_sent: bool = False
    pending_plan_reset: bool = False
    post_apply_plan_reset: bool = False
    auto_apply_patch: bool = False
    run_commands: dict = field(default_factory=dict)
    image_dir: str = "webai-images"
    provider_name: str = "gemini"


COMMANDS = {}


def command(name: str):
    def decorator(fn: Callable):
        COMMANDS[name] = fn
        return fn
    return decorator


async def handle(line: str, state: SessionState, browser) -> str | None:
    parts = line.strip().split()
    cmd = parts[0].lstrip("/")
    args = parts[1:]
    handler = COMMANDS.get(cmd)
    if handler is None:
        return f"Unknown command: /{cmd}. Type /help for a list."
    return await handler(args, state, browser)


@command("upload")
async def cmd_upload(args, state, browser) -> str:
    if not args:
        return "Usage: /upload <file|dir> [glob]"
    path, glob = args[0], args[1] if len(args) > 1 else "*"
    try:
        state.pending_upload = ctx.load_files(path, glob)
        return f"Loaded upload: {path} ({len(state.pending_upload)} chars)"
    except FileNotFoundError as e:
        return str(e)


@command("edit")
async def cmd_edit(args, state, browser) -> str:
    r = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=state.cwd, capture_output=True, text=True
    )
    branch = r.stdout.strip()
    if branch in BLOCKED_BRANCHES:
        _console.print(Panel(
            f"[bold]You are on a protected branch: [red]{branch}[/red][/bold]\n"
            "Edit mode may cause direct changes to the main codebase!",
            title="[bold red]WARNING[/bold red]",
            border_style="bold red",
        ))
        answer = await asyncio.get_event_loop().run_in_executor(
            None, lambda: input('Type "YES" to confirm: ')
        )
        if answer.strip() != "YES":
            return "Cancelled."
    state.edit_mode = True
    return "Edit mode enabled."


@command("plan")
async def cmd_plan(args, state, browser) -> str:
    if state.edit_mode:
        state.pending_plan_reset = True
    state.edit_mode = False
    return "Edit mode disabled. Context unchanged."


@command("git")
async def cmd_git(args, state, browser) -> str:
    if not args:
        return "Usage: /git <git-args>"
    r = subprocess.run(["git"] + args, cwd=state.cwd, capture_output=True, text=True)
    state.session_context = ctx.load_git_context(state.cwd)
    output = (r.stdout + r.stderr).strip()
    suffix = f"\nContext reloaded: {len(state.session_context)} chars"
    return (output + suffix) if output else suffix.strip()


@command("run")
async def cmd_run(args, state, browser) -> str:
    cmds = state.run_commands
    if not cmds:
        return "No /run commands configured. Add a [run] section to ~/.webai/config.toml."
    if not args:
        lines = [f"  {k:<20} {v}" for k, v in cmds.items()]
        return "Configured run commands:\n" + "\n".join(lines)
    key = args[0]
    if key not in cmds:
        return f"Unknown key: '{key}'. Available: {', '.join(cmds)}"
    r = subprocess.run(cmds[key], cwd=state.cwd, capture_output=True, text=True, shell=True)
    return (r.stdout + r.stderr).strip() or "(no output)"


@command("clear")
async def cmd_clear(args, state, browser) -> str:
    state.pending_upload = ""
    state.edit_mode = False
    await browser.new_chat()
    state.session_context = ctx.load_git_context(state.cwd)
    state.context_sent = False
    return "Conversation cleared. Context reloaded."


@command("history")
async def cmd_history(args, state, browser) -> str:
    if not state.session_context:
        return "No context loaded."
    lines = state.session_context.count("\n")
    return f"Session context: {len(state.session_context)} chars, {lines} lines"


@command("model")
async def cmd_model(args, state, browser) -> str:
    if not args:
        try:
            models = await browser.get_models()
            if not models:
                return f"Current model: {state.model or '(default)'}\nThis provider does not support model listing."
            lines = [f"Current: {state.model or '(default)'}", "Available:"]
            lines += [f"  {k:<24} {v['name']:<20} {v.get('desc', '')}" for k, v in models.items()]
            return "\n".join(lines)
        except Exception as e:
            return f"Current model: {state.model or '(default)'} ({e})"
    try:
        display = await browser.select_model(args[0])
        state.model = args[0]
        return f"Model set to: {display}"
    except NotImplementedError as e:
        return str(e)
    except Exception as e:
        return f"Failed to switch model: {e}"


@command("provider")
async def cmd_provider(args, state, browser) -> str:
    """Show current provider or list available ones."""
    from webai.providers import list_providers
    if not args:
        available = ", ".join(list_providers())
        return f"Current: {state.provider_name}\nAvailable: {available}\nSwitch via config.toml or --provider flag."
    return "Provider can only be changed in ~/.webai/config.toml or via --provider flag."


@command("apply")
async def cmd_apply(args, state, browser) -> str:
    auto_yes = "-y" in args or "--yes" in args
    result = await cmd_edit([], state, browser)
    if result == "Cancelled.":
        return result
    state.post_apply_plan_reset = True
    state.auto_apply_patch = auto_yes
    return "__apply__"


@command("paste")
async def cmd_paste(args, state, browser) -> str:
    return "__paste_mode__"


@command("image")
async def cmd_image(args, state, browser) -> str:
    if not args:
        return "Usage: /image <prompt text>"
    return "__image__:" + " ".join(args)


@command("ref")
async def cmd_ref(args, state, browser) -> str:
    if not args:
        return "Usage: /ref <image-path>"
    image_path = Path(" ".join(args)).expanduser()
    if not image_path.exists():
        return f"File not found: {image_path}"
    if image_path.suffix.lower() not in (".png", ".jpg", ".jpeg", ".webp", ".gif"):
        return f"Not an image file: {image_path}"
    try:
        await browser.upload_image(image_path)
        return f"Reference image uploaded: {image_path.name}\nType your message — it will be sent together with the image."
    except Exception as e:
        return f"Upload failed: {e}"


@command("gallery")
async def cmd_gallery(args, state, browser) -> str:
    img_base = Path(state.image_dir) if Path(state.image_dir).is_absolute() else state.cwd / state.image_dir
    if not img_base.exists():
        return f"No images directory found: {img_base}"
    images = sorted(
        f for f in img_base.rglob("*")
        if f.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp") and not f.name.startswith(".")
    )
    if not images:
        return "No images found."
    groups = defaultdict(list)
    for img in images:
        try:
            rel = img.relative_to(img_base)
            group = str(rel.parent) if rel.parent != Path(".") else "(root)"
        except ValueError:
            group = "(root)"
        groups[group].append(img)
    lines = [f"Images in {img_base}:", ""]
    total_size = 0
    for group, files in sorted(groups.items()):
        lines.append(f"  [bold]{group}/[/bold]" if group != "(root)" else "  [bold](root)[/bold]")
        for f in files:
            size = f.stat().st_size
            total_size += size
            size_str = f"{size / 1024:.0f}K" if size < 1024 * 1024 else f"{size / 1024 / 1024:.1f}M"
            lines.append(f"    {f.name:<40} {size_str:>8}")
    lines.append(f"\n  {len(images)} images, {total_size / 1024 / 1024:.1f}M total")
    return "\n".join(lines)


@command("save-images")
async def cmd_save_images(args, state, browser) -> str:
    return "__save_images__"


@command("batch")
async def cmd_batch(args, state, browser) -> str:
    if not args:
        return "Usage: /batch <file.md> [--dry-run] [--start-at <name>] [--resume] [--retries N] [--model <name>]"
    filepath = args[0]
    dry_run = "--dry-run" in args
    start_at = None
    if "--start-at" in args:
        idx = args.index("--start-at")
        if idx + 1 < len(args):
            start_at = args[idx + 1]
    p = Path(filepath).expanduser()
    if not p.exists():
        return f"File not found: {filepath}"
    try:
        batch = parse_prompt_file(p)
    except Exception as e:
        return f"Parse error: {e}"
    prompts = batch.prompts
    if not prompts:
        return "No prompts found in file."
    if start_at:
        found = False
        for i, pr in enumerate(prompts):
            if start_at in pr.filename:
                prompts = prompts[i:]
                found = True
                break
        if not found:
            return f"Start-at '{start_at}' not found. Available: {', '.join(p.filename for p in prompts)}"
    if dry_run:
        import json
        subdir = Path(filepath).stem
        img_base = Path(state.image_dir) if Path(state.image_dir).is_absolute() else state.cwd / state.image_dir
        progress_file = img_base / subdir / ".batch-progress.json"
        done, failed = [], []
        if progress_file.exists():
            prog = json.loads(progress_file.read_text())
            done = prog.get("done", [])
            failed = prog.get("failed", [])
        lines = [f"Intro: {len(batch.intro)} chars", f"Style prefix: {len(batch.style_prefix)} chars", f"Prompts: {len(prompts)}", ""]
        for i, pr in enumerate(prompts, 1):
            note = f" ({pr.note})" if pr.note else ""
            ref = f" [ref: {pr.ref_image}]" if pr.ref_image else ""
            status = ""
            if pr.filename in done:
                status = " [green]DONE[/green]"
            elif pr.filename in failed:
                status = " [red]FAILED[/red]"
            lines.append(f"  {i:>2}. {pr.filename}{ref}{note}{status}")
        if done:
            lines.append(f"\n  {len(done)}/{len(prompts)} completed. Use --resume to skip done.")
        return "\n".join(lines)
    return f"__batch__:{filepath}"


@command("help")
async def cmd_help(args, state, browser) -> str:
    cmds = {
        "/upload <file|dir> [glob]": "Append files to next message",
        "/edit":                     "Enable edit mode (apply diffs from responses)",
        "/plan":                     "Disable edit mode (keep context)",
        "/apply [-y]":               "EDIT -> patch -> PLAN (-y: auto-yes)",
        "/ref <image-path>":         "Upload reference image for next message",
        "/image <prompt>":           "Send prompt and save generated images",
        "/gallery":                  "List all saved images",
        "/save-images":              "Save all images from current chat history",
        "/batch <file> [opts]":      "Batch (--dry-run, --start-at, --resume, --retries, --model)",
        "/provider":                 "Show current provider and available ones",
        "/git <args>":               "Run git command + reload context",
        "/run [key]":                "Run allowed command by key (no key = list all)",
        "/clear":                    "New conversation + reload context",
        "/history":                  "Show context summary",
        "/model [name]":             "Show or set model",
        "/paste":                    "Enter multiline paste mode",
        "/help":                     "Show this help",
        "/exit":                     "Quit",
    }
    return "\n".join(f"  {k:<30} {v}" for k, v in cmds.items())


@command("exit")
async def cmd_exit(args, state, browser) -> str:
    return "__exit__"
