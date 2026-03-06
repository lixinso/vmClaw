"""vmClaw CLI entry point."""

from __future__ import annotations

import argparse
import io
import json
import os
import shutil
import subprocess
import sys
import urllib.request
import urllib.error
from pathlib import Path


def _fix_stdout_encoding() -> None:
    """Ensure stdout can handle Unicode on Windows."""
    if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
        sys.stdout = io.TextIOWrapper(
            sys.stdout.buffer, encoding="utf-8", errors="replace"
        )

from .config import load_config
from .capture import capture_and_resize, save_screenshot
from .discovery import find_vm_windows, select_vm_window
from .orchestrator import run_task


# Available providers and their common models
PROVIDERS = {
    "openai": {
        "name": "OpenAI",
        "key_env": "OPENAI_API_KEY",
        "models": [
            "gpt-4o",
            "gpt-4o-mini",
            "gpt-4.1",
            "gpt-4.1-mini",
            "gpt-4.1-nano",
            "o4-mini",
            "o3",
        ],
    },
    "github": {
        "name": "GitHub Models (Copilot)",
        "key_env": "GITHUB_TOKEN",
        "models": [
            "openai/gpt-4o",
            "openai/gpt-4o-mini",
            "openai/gpt-4.1",
            "openai/gpt-4.1-mini",
            "openai/gpt-4.1-nano",
            "openai/o4-mini",
            "openai/o3",
            "meta-llama/llama-4-scout-17b-16e-instruct",
            "deepseek/deepseek-r1",
            "mistral-ai/mistral-large-2411",
            "xai/grok-3",
        ],
    },
}


def _find_gh_cli() -> str | None:
    """Find the GitHub CLI executable."""
    # Try PATH first
    gh_path = shutil.which("gh")
    if gh_path:
        return gh_path

    # Check common Windows install locations
    if sys.platform == "win32":
        candidates = [
            Path(os.environ.get("ProgramFiles", "C:\\Program Files")) / "GitHub CLI" / "gh.exe",
            Path(os.environ.get("ProgramFiles(x86)", "C:\\Program Files (x86)")) / "GitHub CLI" / "gh.exe",
            Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "GitHub CLI" / "gh.exe",
        ]
        for candidate in candidates:
            if candidate.is_file():
                return str(candidate)

    return None


def _gh_get_existing_token() -> str | None:
    """Check if gh CLI is already authenticated and return the token if so."""
    gh_path = _find_gh_cli()
    if not gh_path:
        return None

    try:
        result = subprocess.run(
            [gh_path, "auth", "token"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except Exception:
        pass

    return None


def _gh_auth_login() -> str | None:
    """Authenticate with GitHub via gh CLI web login. Returns token or None."""
    gh_path = _find_gh_cli()
    if not gh_path:
        print("\n  GitHub CLI (gh) is not installed.")
        print("  Install it from: https://cli.github.com/")
        return None

    print("\n  Opening browser for GitHub authentication...")
    print("  Please complete the login in your browser.\n")

    try:
        result = subprocess.run(
            [gh_path, "auth", "login", "--web"],
            timeout=120,
        )
        if result.returncode != 0:
            print("  GitHub login failed.")
            return None

        # Get the token after successful login
        result = subprocess.run(
            [gh_path, "auth", "token"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0 and result.stdout.strip():
            token = result.stdout.strip()
            print("  GitHub authentication successful.")
            return token
        else:
            print("  Could not retrieve token after login.")
            return None
    except subprocess.TimeoutExpired:
        print("  Login timed out.")
        return None
    except Exception as e:
        print(f"  Login error: {e}")
        return None


def _prompt_github_auth(config):
    """Prompt user to authenticate with GitHub when no token is found.

    First checks if gh CLI is already authenticated. If so, uses that token
    without prompting. Otherwise, offers browser login or manual token entry.

    Returns updated config with token set, or None if auth fails.
    """
    # Check for existing gh CLI session first
    existing_token = _gh_get_existing_token()
    if existing_token:
        print("\nFound existing GitHub CLI session.")
        config.github_token = existing_token
        return config

    print("\nNo GitHub token found. How would you like to authenticate?\n")
    print("  [1] Login with browser (requires GitHub CLI)")
    print("  [2] Enter token manually (PAT with models:read scope)")
    print("  [3] Cancel")

    print()
    choice = input("Choice [1-3]: ").strip()

    if choice == "1":
        token = _gh_auth_login()
        if token:
            config.github_token = token
            return config
        return None

    elif choice == "2":
        print("\n  Create a token at: https://github.com/settings/tokens")
        print("  Required scope: models:read\n")
        token = input("  Enter GitHub token: ").strip()
        if token:
            config.github_token = token
            return config
        print("  No token entered.")
        return None

    else:
        return None


def _prompt_openai_auth(config):
    """Prompt user to enter OpenAI API key when none is found.

    Returns updated config with key set, or None if cancelled.
    """
    print("\nNo OpenAI API key found.\n")
    print("  Get a key at: https://platform.openai.com/api-keys\n")
    key = input("  Enter OpenAI API key (or press Enter to cancel): ").strip()
    if key:
        config.openai_api_key = key
        return config
    return None


def _select_provider(config):
    """Prompt user to select an AI provider. Returns updated config."""
    providers = list(PROVIDERS.keys())

    # Determine which providers have keys configured
    available = {}
    for pid in providers:
        if pid == "openai" and config.openai_api_key:
            available[pid] = True
        elif pid == "github" and config.github_token:
            available[pid] = True
        else:
            available[pid] = False

    print("Select AI provider:\n")
    for i, pid in enumerate(providers, 1):
        info = PROVIDERS[pid]
        status = " (key configured)" if available[pid] else " (no key found)"
        default = " [default]" if pid == config.provider else ""
        print(f"  [{i}] {info['name']}{status}{default}")

    print()
    choice = input(f"Provider [1-{len(providers)}] (Enter for default): ").strip()

    if choice == "":
        selected = config.provider
    else:
        try:
            idx = int(choice) - 1
            if 0 <= idx < len(providers):
                selected = providers[idx]
            else:
                print(f"Invalid selection. Using default: {config.provider}")
                selected = config.provider
        except ValueError:
            print(f"Invalid selection. Using default: {config.provider}")
            selected = config.provider

    config.provider = selected

    # If no key found, prompt for authentication
    if selected == "github" and not config.github_token:
        config = _prompt_github_auth(config)
        if config is None:
            return None
    elif selected == "openai" and not config.openai_api_key:
        config = _prompt_openai_auth(config)
        if config is None:
            return None

    print(f"\nProvider: {PROVIDERS[config.provider]['name']}")
    return config


def _fetch_github_models(token: str) -> list[str]:
    """Fetch available vision-capable models from GitHub.

    Tries the GitHub Models inference endpoint first (authoritative for what
    models actually work there), then falls back to the Copilot catalog API
    filtered to models with a publisher prefix (e.g. ``openai/gpt-4o``).

    Returns a list of model IDs, or an empty list on failure.
    """
    # Endpoints to try, in priority order
    endpoints = [
        "https://models.github.ai/inference/models",
        "https://api.githubcopilot.com/models",
    ]

    for url in endpoints:
        req = urllib.request.Request(url, headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
        })
        try:
            resp = urllib.request.urlopen(req, timeout=10)
            data = json.loads(resp.read())
        except Exception:
            continue

        models = []
        seen = set()
        for m in data.get("data", []):
            mid = m.get("id", "")
            if not mid or mid in seen:
                continue
            seen.add(mid)

            # Only include chat models with vision support
            caps = m.get("capabilities", {})
            limits = caps.get("limits", {})
            has_vision = limits.get("vision") is not None
            if not has_vision:
                continue

            # When using the Copilot catalog, only keep models with a
            # publisher prefix (e.g. "openai/gpt-4o") since those are the
            # format that works with models.github.ai/inference.  Models
            # without a prefix (e.g. "gpt-5.4") exist only in the Copilot
            # ecosystem and return 404 on the inference endpoint.
            if "copilot" in url and "/" not in mid:
                continue

            models.append(mid)

        if models:
            return sorted(models)

    return []


def _select_model(config):
    """Prompt user to select an LLM model. Returns updated config."""
    info = PROVIDERS.get(config.provider, PROVIDERS["openai"])
    provider_name = info["name"]

    # Try fetching models dynamically for GitHub provider
    models = []
    if config.provider == "github" and config.github_token:
        print(f"\nFetching available models from GitHub...")
        models = _fetch_github_models(config.github_token)
        if models:
            print(f"Found {len(models)} vision-capable model(s).")

    # Fall back to static list
    if not models:
        models = info["models"]

    print(f"\nSelect model for {provider_name}:\n")
    for i, model in enumerate(models, 1):
        default = " [default]" if model == config.model else ""
        print(f"  [{i}] {model}{default}")

    print(f"\n  Or type a custom model name.")
    print()
    choice = input(f"Model [1-{len(models)}] (Enter for default '{config.model}'): ").strip()

    if choice == "":
        selected = config.model
    else:
        try:
            idx = int(choice) - 1
            if 0 <= idx < len(models):
                selected = models[idx]
            else:
                # Treat as custom model name
                selected = choice
        except ValueError:
            # Treat as custom model name
            selected = choice

    config.model = selected
    print(f"Model: {config.model}")
    return config


def cmd_list(args: argparse.Namespace) -> None:
    """List detected VM windows."""
    config = load_config()
    windows = find_vm_windows(config.window_keywords)

    if not windows:
        print("No VM windows detected with default keywords.")
        print("Try running with --all to see all windows.")
        return

    print(f"Found {len(windows)} VM window(s):\n")
    for i, win in enumerate(windows, 1):
        print(f"  [{i}] {win.title}  (hwnd={win.hwnd}, pid={win.pid})")


def cmd_list_all(args: argparse.Namespace) -> None:
    """List all visible windows."""
    from .discovery import find_all_windows

    windows = find_all_windows()
    print(f"Found {len(windows)} visible window(s):\n")
    for i, win in enumerate(windows, 1):
        print(f"  [{i}] {win.title}  (hwnd={win.hwnd}, pid={win.pid})")


def cmd_capture(args: argparse.Namespace) -> None:
    """Capture a screenshot of a selected VM window."""
    config = load_config()

    print("vmClaw - VM Computer Use Agent\n")
    print("Discovering VM windows...")

    vm = select_vm_window(config.window_keywords)
    if vm is None:
        print("No window selected.")
        return

    print(f"\nCapturing: {vm.title}")
    img = capture_and_resize(vm.hwnd, target_width=config.screenshot_width)

    if img is None:
        print("Failed to capture screenshot.")
        return

    output = Path(args.output) if args.output else Path(f"screenshot.png")
    save_screenshot(img, output)
    print(f"Screenshot saved: {output} ({img.size[0]}x{img.size[1]})")


def cmd_run(args: argparse.Namespace) -> None:
    """Run the agent loop on a selected VM window."""
    config = load_config()

    print("vmClaw - VM Computer Use Agent\n")

    # Interactive provider and model selection
    config = _select_provider(config)
    if config is None:
        return

    config = _select_model(config)
    if config is None:
        return

    # Discover and select VM window
    print("\nDiscovering VM windows...")

    vm = select_vm_window(config.window_keywords)
    if vm is None:
        print("No window selected.")
        return

    print(f"\nSelected: {vm.title}")

    # Single-task mode if --task is provided
    if args.task:
        try:
            run_task(vm, args.task, config)
        except KeyboardInterrupt:
            print("\n\nTask interrupted by user.")
        return

    # Interactive task loop
    while True:
        print()
        task = input("Enter task (or 'quit'): ").strip()
        if task.lower() in ("quit", "q", "exit"):
            break
        if not task:
            continue

        try:
            run_task(vm, task, config)
        except KeyboardInterrupt:
            print("\n\nTask interrupted by user.")
        except Exception as e:
            print(f"\nError: {e}")

    print("Goodbye.")


def main() -> None:
    _fix_stdout_encoding()

    parser = argparse.ArgumentParser(
        prog="vmclaw",
        description="vmClaw - AI-powered virtual machine screen control agent",
    )
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # list
    sub_list = subparsers.add_parser("list", help="List detected VM windows")
    sub_list.set_defaults(func=cmd_list)

    # list-all
    sub_list_all = subparsers.add_parser("list-all", help="List all visible windows")
    sub_list_all.set_defaults(func=cmd_list_all)

    # capture
    sub_capture = subparsers.add_parser("capture", help="Capture a VM window screenshot")
    sub_capture.add_argument("-o", "--output", help="Output file path (default: screenshot.png)")
    sub_capture.set_defaults(func=cmd_capture)

    # run
    sub_run = subparsers.add_parser("run", help="Run the agent loop on a VM")
    sub_run.add_argument("-t", "--task", help="Task to execute (skips interactive prompt)")
    sub_run.set_defaults(func=cmd_run)

    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        return

    args.func(args)


if __name__ == "__main__":
    main()
