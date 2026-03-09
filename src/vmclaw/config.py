"""Configuration loading for vmClaw."""

from __future__ import annotations

import os
from pathlib import Path

from .models import Config
from .fleet_models import FleetConfig, PeerConfig

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
        if api.get("github_token"):
            config.github_token = api["github_token"]
        if api.get("provider"):
            config.provider = api["provider"]
        if api.get("api_base_url"):
            config.api_base_url = api["api_base_url"]
        if api.get("model"):
            config.model = api["model"]
        if agent.get("max_actions"):
            config.max_actions = int(agent["max_actions"])
        if agent.get("action_delay"):
            config.action_delay = float(agent["action_delay"])
        if agent.get("screenshot_width"):
            config.screenshot_width = int(agent["screenshot_width"])
        if agent.get("memory_enabled") is not None:
            config.memory_enabled = bool(agent["memory_enabled"])
        if vm.get("window_keywords"):
            config.window_keywords = vm["window_keywords"]

        # Fleet configuration
        fleet_data = data.get("fleet", {})
        if fleet_data:
            fc = FleetConfig()
            if "enabled" in fleet_data:
                fc.enabled = bool(fleet_data["enabled"])
            if fleet_data.get("node_name"):
                fc.node_name = fleet_data["node_name"]
            if fleet_data.get("role"):
                fc.role = fleet_data["role"]
            if fleet_data.get("listen_port"):
                fc.listen_port = int(fleet_data["listen_port"])
            if fleet_data.get("auth_token"):
                fc.auth_token = fleet_data["auth_token"]

            # Parse [[fleet.peers]] array
            peers_data = fleet_data.get("peers", [])
            for peer in peers_data:
                if peer.get("name") and peer.get("url"):
                    fc.peers.append(PeerConfig(
                        name=peer["name"],
                        url=peer["url"],
                        token=peer.get("token", ""),
                    ))

            config.fleet = fc

    # Environment variables override file config
    env_key = os.environ.get("OPENAI_API_KEY")
    if env_key:
        config.openai_api_key = env_key

    env_github = os.environ.get("GITHUB_TOKEN")
    if env_github:
        config.github_token = env_github

    env_provider = os.environ.get("VMCLAW_PROVIDER")
    if env_provider:
        config.provider = env_provider

    env_model = os.environ.get("VMCLAW_MODEL")
    if env_model:
        config.model = env_model

    env_memory = os.environ.get("VMCLAW_MEMORY")
    if env_memory is not None:
        config.memory_enabled = env_memory.lower() not in ("0", "false", "no", "off")

    # Auto-detect provider if not explicitly set and only one key is available
    if config.provider == "openai" and not config.openai_api_key and config.github_token:
        config.provider = "github"

    return config


def append_peers_to_config(
    peers: list[PeerConfig],
    path: Path | None = None,
) -> Path:
    """Append new [[fleet.peers]] entries to config.toml.

    Appends raw TOML text to the end of the file. Existing content is not
    modified. TOML allows [[fleet.peers]] array-of-tables entries anywhere
    after the [fleet] section.

    Returns:
        The path that was written to.

    Raises:
        FileNotFoundError: If no config file is found.
    """
    if path is None:
        path = find_config_file()
    if path is None:
        raise FileNotFoundError("No config.toml found. Create one first.")

    lines: list[str] = []
    for peer in peers:
        lines.append("")
        lines.append("[[fleet.peers]]")
        lines.append(f'name = "{peer.name}"')
        lines.append(f'url = "{peer.url}"')
        lines.append(f'token = "{peer.token}"')

    text = "\n".join(lines) + "\n"

    with open(path, "a", encoding="utf-8") as f:
        f.write(text)

    return path
