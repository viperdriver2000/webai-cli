"""Config file handling for webai-cli."""
import tomllib
from pathlib import Path
from dataclasses import dataclass, field

CONFIG_DIR = Path.home() / ".webai"
CONFIG_FILE = CONFIG_DIR / "config.toml"
PROFILE_DIR = Path.home() / ".webai" / "profiles"

DEFAULTS = {
    "provider": "gemini",
    "profile_dir": str(PROFILE_DIR),
    "headless": False,
    "model": "",
    "image_dir": "webai-images",
    "system_prompt": "",
    "telegram_token": "",
    "telegram_chat_id": "",
}


@dataclass
class Config:
    provider: str = "gemini"
    profile_dir: str = str(PROFILE_DIR)
    headless: bool = True
    model: str = ""
    image_dir: str = "webai-images"
    system_prompt: str = ""
    telegram_token: str = ""
    telegram_chat_id: str = ""
    run_commands: dict = field(default_factory=dict)

    @property
    def profile_path(self) -> Path:
        """Profile directory per provider."""
        return Path(self.profile_dir).expanduser() / self.provider

    @property
    def image_path(self) -> Path:
        p = Path(self.image_dir).expanduser()
        return p if p.is_absolute() else Path.cwd() / p


def load() -> Config:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    if not CONFIG_FILE.exists():
        _write_defaults()
    with open(CONFIG_FILE, "rb") as f:
        data = tomllib.load(f)
    cfg = Config(**{k: data.get(k, v) for k, v in DEFAULTS.items()})
    cfg.run_commands = data.get("run", {})
    return cfg


def _write_defaults():
    from webai.providers import list_providers
    providers = ", ".join(list_providers())
    lines = [
        f'# Available providers: {providers}',
        f'provider = "{DEFAULTS["provider"]}"',
        f'profile_dir = "{DEFAULTS["profile_dir"]}"',
        f'headless = {str(DEFAULTS["headless"]).lower()}',
        f'# model = "gemini-2.0-flash"',
        f'image_dir = "{DEFAULTS["image_dir"]}"',
        f'# system_prompt = "..."',
        f'# telegram_token = "1234:AAxx..."',
        f'# telegram_chat_id = "155463840"',
        f'',
        f'# [run]',
        f'# test = "pytest"',
        f'# lint = "ruff check ."',
    ]
    CONFIG_FILE.write_text("\n".join(lines) + "\n")
