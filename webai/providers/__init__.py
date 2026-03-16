"""Provider registry for web AI interfaces."""
from webai.providers.base import BaseProvider

PROVIDERS: dict[str, type[BaseProvider]] = {}


def register(name: str):
    """Decorator to register a provider class."""
    def decorator(cls):
        PROVIDERS[name] = cls
        return cls
    return decorator


def get_provider(name: str) -> type[BaseProvider]:
    """Get provider class by name."""
    # Import all provider modules to trigger registration
    from webai.providers import (  # noqa: F401
        gemini, deepseek, zai, kimi, chatgpt, claude_ai, perplexity, grok, lumo, mistral,
    )
    if name not in PROVIDERS:
        available = ", ".join(sorted(PROVIDERS.keys()))
        raise ValueError(f"Unknown provider: {name!r}. Available: {available}")
    return PROVIDERS[name]


def list_providers() -> list[str]:
    """Return list of registered provider names."""
    from webai.providers import (  # noqa: F401
        gemini, deepseek, zai, kimi, chatgpt, claude_ai, perplexity, grok, lumo, mistral,
    )
    return sorted(PROVIDERS.keys())
