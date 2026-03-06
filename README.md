# vmClaw 🦀

**AI agent that sees and operates your virtual machines — free with GitHub Copilot.**

vmClaw captures your VM screen, sends it to an AI vision model, and executes the actions it decides on — clicks, typing, keyboard shortcuts, scrolling — in a continuous loop until the task is done.

<!-- TODO: Replace with actual demo GIF -->
<!-- ![vmClaw demo](docs/demo.gif) -->

## Why vmClaw?

- **Multi-model** — GPT-5.4, Claude Opus 4.6, GPT-4o, DeepSeek, Grok, and 15+ more models.
- **Local** — Runs on your Windows machine. Screenshots never leave your network (sent directly to the AI API).
- **Universal** — Supports Hyper-V, VMware, VirtualBox, and QEMU VMs.
- **Simple** — One command to start. No complex setup.

## Quick Start

```bash
# Install
pip install -e .

# Run (uses GitHub Copilot — free, authenticates via browser)
python -m vmclaw run
```

That's it. vmClaw will walk you through selecting a provider, model, and VM window interactively.

## How It Works

```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│  Capture VM  │────>│  AI Vision   │────>│  Execute     │
│  Screenshot  │     │  Model       │     │  Action      │
└─────────────┘     └─────────────┘     └─────────────┘
       ^                                        │
       └────────────────────────────────────────┘
                    repeat until done
```

1. **Capture** — Takes a screenshot of the selected VM window
2. **Think** — Sends the screenshot + task description to an AI vision model
3. **Act** — Executes the AI's decision (click, type, key press, scroll)
4. **Repeat** — Loops until the AI reports the task is done (or hits the action limit)

## Supported Models

| Provider | Models | Auth |
|----------|--------|------|
| **GitHub Copilot** (free) | Claude Opus 4.6, Claude Sonnet 4.6, GPT-5.4, GPT-5-mini, GPT-4o, GPT-4.1, o3, o4-mini, DeepSeek-R1, Grok-3, and more | `gh auth login` (browser) |
| **OpenAI** (API key) | GPT-4o, GPT-4.1, o3, o4-mini, and any OpenAI model | `OPENAI_API_KEY` env var |

## Commands

```bash
python -m vmclaw run         # Start the AI agent loop
python -m vmclaw list        # List detected VM windows
python -m vmclaw list-all    # List all windows (for debugging)
python -m vmclaw capture     # Capture a VM screenshot
```

## Requirements

- **Windows 10/11** with Python 3.10+
- A running VM (Hyper-V, VMware, VirtualBox, or QEMU)
- GitHub CLI (`gh`) for GitHub Copilot auth, or an OpenAI API key

## Configuration

vmClaw works out of the box with interactive prompts. For automation, create a `config.toml`:

```toml
[api]
provider = "github"    # or "openai"
model = "claude-opus-4.6"

[agent]
max_actions = 50       # Safety limit
action_delay = 1.0     # Seconds between actions
screenshot_width = 1024
```

## License

MIT
