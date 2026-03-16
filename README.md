# webai-cli

CLI tool for interacting with AI chat web interfaces via Playwright browser automation. Chat with multiple AI providers from your terminal — no API keys needed, just your browser session.

## Supported Providers

| Provider | Name | URL |
|----------|------|-----|
| Gemini | `gemini` | gemini.google.com |
| ChatGPT | `chatgpt` | chatgpt.com |
| Claude | `claude` | claude.ai |
| DeepSeek | `deepseek` | chat.deepseek.com |
| Perplexity | `perplexity` | perplexity.ai |
| Grok | `grok` | grok.com |
| Mistral | `mistral` | chat.mistral.ai |
| Kimi | `kimi` | kimi.com |
| Z.AI | `zai` | chat.z.ai |
| Lumo | `lumo` | lumo.chat |

## Installation

```bash
# Clone
git clone https://github.com/viperdriver2000/webai-cli.git
cd webai-cli

# Create venv and install
python -m venv .venv
source .venv/bin/activate
pip install -e .

# Install browser
playwright install chromium
```

Optional Telegram bot support:
```bash
pip install -e ".[bot]"
```

## Usage

```bash
# Start with default provider (Gemini)
webai

# Start with a specific provider
webai -p chatgpt
webai --provider deepseek
```

On first run for a provider, a browser window opens for manual login. After that, the session is saved and reused.

## Slash Commands

| Command | Description |
|---------|-------------|
| `/provider` | Show current provider and list available ones |
| `/model` | List or switch models (if supported by provider) |
| `/upload <path>` | Upload an image file |
| `/ref <file/dir>` | Send file or directory contents as context |
| `/edit` | Enable edit mode (responses as unified diffs) |
| `/plan` | Disable edit mode, return to normal responses |
| `/apply` | Apply last diff to files |
| `/image` | Extract and save images from last response |
| `/batch <file>` | Run prompts from a file in sequence |
| `/gallery` | Extract images from all responses in conversation |
| `/save-images` | Save all images to output directory |
| `/git` | Show git context (branch, status, recent commits) |
| `/run <name>` | Run a configured command (see config) |
| `/paste` | Read from clipboard |
| `/history` | Show conversation history |
| `/clear` | Start a new conversation |
| `/help` | Show help |
| `/exit` | Quit |

## Configuration

Config file: `~/.webai/config.toml`

```toml
# Available providers: chatgpt, claude, deepseek, gemini, grok, kimi, lumo, mistral, perplexity, zai
provider = "gemini"
headless = false
image_dir = "webai-images"
# model = "gemini-2.0-flash"
# system_prompt = "..."

# Custom run commands
# [run]
# test = "pytest"
# lint = "ruff check ."
```

Browser profiles are stored per provider in `~/.webai/profiles/<provider>/`.

## How It Works

webai-cli uses Playwright to automate Chromium and interact with AI chat web interfaces. Each provider defines CSS selectors for the input field, send button, and response elements. Messages are typed into the input, responses are polled and streamed back to the terminal with rich markdown rendering.

Since it uses real browser sessions, you get the same experience as using the web interface — including access to free tiers, no API costs, and all model features.

## Requirements

- Python >= 3.11
- Chromium (installed via Playwright)

## License

MIT
