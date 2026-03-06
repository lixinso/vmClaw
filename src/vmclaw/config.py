"""Configuration loading for vmClaw."""

from __future__ import annotations

import os
from pathlib import Path

from .models import Config

try:
    import tomllib
except ImportError:
    import tomli as tomllib  # type: ignore[no-redef]


def find_config_file() -> Path | None:
    """Search for config.toml in current dir, then user home."""
    candidates = [
        Path.cwd() / "config.toml",
        Path.home() / ".vmclaw" / "config.toml",
    ]
    for path in candidates:
        if path.is_file():
            return path
    return None


def load_config(path: Path | None = None) -> Config:
    """Load config from TOML file and environment variables.

    Environment variables take precedence over file values.
    """
    config = Config()

    if path is None:
        path = find_config_file()

    if path is not None and path.is_file():
        with open(path, "rb") as f:
            data = tomllib.load(f)

        api = data.get("api", {})
        agent = data.get("agent", {})
        vm = data.get("vm", {})

        if api.get("openai_api_key"):
            config.openai_api_key = api["openai_api_key"]
        if api.get("model"):
            config.model = api["model"]
        if agent.get("max_actions"):
            config.max_actions = int(agent["max_actions"])
        if agent.get("action_delay"):
            config.action_delay = float(agent["action_delay"])
        if agent.get("screenshot_width"):
            config.screenshot_width = int(agent["screenshot_width"])
        if vm.get("window_keywords"):
            config.window_keywords = vm["window_keywords"]

    # Environment variables override file config
    env_key = os.environ.get("OPENAI_API_KEY")
    if env_key:
        config.openai_api_key = env_key

    env_model = os.environ.get("VMCLAW_MODEL")
    if env_model:
        config.model = env_model

    return config
