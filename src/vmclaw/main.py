"""vmClaw CLI entry point."""

from __future__ import annotations

import argparse
import io
import os
import shutil
import subprocess
import sys
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
    "github": {
        "name": "GitHub Models (Copilot)",
        "key_env": "GITHUB_TOKEN",
        "models": [
            "claude-opus-4.6",
            "claude-sonnet-4.6",
            "gpt-5.4",
            "openai/gpt-5-mini",
            "openai/gpt-4o",
            "openai/gpt-4o-mini",
            "openai/gpt-4.1",
            "openai/gpt-4.1-mini",
            "openai/gpt-4.1-nano",
            "openai/o4-mini",
            "openai/o3",
            "deepseek/deepseek-r1",
            "deepseek/DeepSeek-V3-0324",
            "xai/grok-3",
            "xai/grok-3-mini",
            "cohere/cohere-command-a",
            "mistral-ai/mistral-small-2503",
            "microsoft/Phi-4-multimodal-instruct",
            "microsoft/phi-4",
        ],
    },
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


def _select_model(config):
    """Prompt user to select an LLM model. Returns updated config."""
    info = PROVIDERS.get(config.provider, PROVIDERS["openai"])
    provider_name = info["name"]

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


def _is_admin() -> bool:
    """Return True if the current process has administrator privileges."""
    import ctypes
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def _restart_as_admin() -> None:
    """Re-launch the current process with administrator privileges via UAC."""
    import ctypes
    # sys.argv[0] is the __main__.py path; pass it + remaining args to python.exe
    params = subprocess.list2cmdline(sys.argv)
    ctypes.windll.shell32.ShellExecuteW(None, "runas", sys.executable, params, None, 1)
    sys.exit(0)


def cmd_run(args: argparse.Namespace) -> None:
    """Run the agent loop on a selected VM window."""
    import ctypes

    # Hyper-V vmconnect (and similar VM viewers) run elevated. UIPI blocks mouse/
    # keyboard injection from a normal process into an elevated window. We must
    # also run elevated for PostMessage / SendInput to reach the VM guest.
    if not _is_admin():
        print(
            "vmClaw requires Administrator privileges to inject mouse and keyboard\n"
            "input into Hyper-V VM Connection windows (UIPI restriction).\n"
        )
        ans = input("Restart as Administrator now? [Y/n]: ").strip().lower()
        if ans in ("", "y", "yes"):
            _restart_as_admin()
        print("Continuing without admin — clicks may not reach the VM.\n")

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

    # Initialize memory
    memory = None
    if config.memory_enabled:
        try:
            from .memory import MemoryStore

            memory = MemoryStore()
            memory.open(config)
            print("Memory: enabled")
        except Exception as e:
            print(f"Memory: disabled ({e})")
            memory = None

    # Single-task mode if --task is provided
    if args.task:
        try:
            run_task(vm, args.task, config, memory=memory)
        except KeyboardInterrupt:
            print("\n\nTask interrupted by user.")
        finally:
            if memory:
                memory.close()
        return

    # Interactive task loop
    try:
        while True:
            print()
            task = input("Enter task (or 'quit'): ").strip()
            if task.lower() in ("quit", "q", "exit"):
                break
            if not task:
                continue

            try:
                run_task(vm, task, config, memory=memory)
            except KeyboardInterrupt:
                print("\n\nTask interrupted by user.")
            except Exception as e:
                print(f"\nError: {e}")
    finally:
        if memory:
            memory.close()

    print("Goodbye.")


def cmd_gui(args: argparse.Namespace) -> None:
    """Launch the tkinter GUI."""
    from .gui import launch_gui

    launch_gui()


def cmd_serve(args: argparse.Namespace) -> None:
    """Start the fleet agent server."""
    from .server import start_server

    config = load_config()

    # CLI overrides for fleet settings
    if args.port:
        config.fleet.listen_port = args.port
    if args.name:
        config.fleet.node_name = args.name
    if args.token:
        config.fleet.auth_token = args.token

    # Ensure fleet is marked enabled when serving
    config.fleet.enabled = True

    # Auto-detect provider token if needed
    if config.provider == "github" and not config.github_token:
        token = _gh_get_existing_token()
        if token:
            config.github_token = token

    start_server(config, host=args.host, port=args.port)


def cmd_fleet_list(args: argparse.Namespace) -> None:
    """List all fleet nodes and their VMs."""
    from .fleet import FleetClient

    config = load_config()

    if not config.fleet.peers:
        print("No fleet peers configured.")
        print("Add [[fleet.peers]] entries to your config.toml.")
        return

    client = FleetClient(config.fleet)
    fleet_map = client.discover_all()

    print(f"vmClaw Fleet — {len(config.fleet.peers)} peer(s) configured\n")

    # Show local node first
    if config.fleet.node_name:
        local_vms = find_vm_windows(config.window_keywords)
        print(f"  [{config.fleet.node_name}] (local — {config.fleet.role})")
        for vm in local_vms:
            print(f"    ├ VM: {vm.title}")
        if not local_vms:
            print(f"    └ (no VMs detected)")
        print()

    # Show remote peers
    for name, data in fleet_map.items():
        if data["reachable"]:
            info = data["info"]
            vms = data["vms"]
            print(f"  [{name}] ({info.role}) — v{info.version}, {info.vm_count} VM(s)")
            for vm in vms:
                print(f"    ├ VM: {vm['title']}")
            # Show transitive peers
            for tp in data.get("transitive_peers", []):
                tp_name = tp.get("node_name", "?")
                tp_vms = tp.get("vms", [])
                print(f"    └ [{tp_name}] (via {name})")
                for tv in tp_vms:
                    title = tv if isinstance(tv, str) else tv.get("title", "?")
                    print(f"        ├ VM: {title}")
        else:
            print(f"  [{name}] OFFLINE — {data['peer'].url}")
        print()


def cmd_fleet_run(args: argparse.Namespace) -> None:
    """Send a task to a fleet node."""
    import time

    from .fleet import FleetClient
    from .fleet_models import TaskRequest

    config = load_config()

    if not config.fleet.peers:
        print("No fleet peers configured.")
        return

    client = FleetClient(config.fleet)

    task_req = TaskRequest(
        vm_title=args.vm,
        task=args.task,
        max_actions=args.max_actions,
        action_delay=args.delay,
    )

    if args.all:
        # Send to all peers
        print(f"Sending task to all {len(config.fleet.peers)} peer(s)...\n")
        for peer in config.fleet.peers:
            result = client.submit_task(peer, task_req)
            if result and "error" not in result:
                print(f"  [{peer.name}] Task submitted: {result.get('task_id', '?')}")
            else:
                err = result.get("error", "unknown") if result else "unreachable"
                print(f"  [{peer.name}] Failed: {err}")
        return

    # Send to specific node
    peer = client.find_peer_for_node(args.node)
    if peer is None:
        print(f"Peer not found: {args.node}")
        print(f"Available peers: {', '.join(p.name for p in config.fleet.peers)}")
        return

    print(f"Sending task to [{args.node}]: {args.task}")
    print(f"  VM: {args.vm} | Max actions: {args.max_actions}\n")

    result = client.submit_task(peer, task_req)
    if result and "error" not in result:
        task_id = result.get("task_id", "?")
        print(f"Task submitted: {task_id}")
        print(f"Status: {result.get('status', '?')}")

        # Poll for completion if --follow
        if args.follow:
            print("\nFollowing task progress (Ctrl+C to detach)...\n")
            try:
                while True:
                    time.sleep(2)
                    status = client.get_task_status(peer, task_id)
                    if status is None:
                        print("  Lost connection to node.")
                        break
                    print(f"  [{status.status}] actions={status.actions_taken}", end="")
                    if status.outcome:
                        print(f" outcome={status.outcome}", end="")
                    print()
                    if status.status in ("done", "error", "stopped", "max_actions"):
                        break
            except KeyboardInterrupt:
                print("\n\nDetached from task. It continues running on the remote node.")
                print(f"Check status: vmclaw fleet status --node {args.node} --task-id {task_id}")
    else:
        err = result.get("error", "unknown") if result else "unreachable"
        print(f"Failed: {err}")


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

    # gui
    sub_gui = subparsers.add_parser("gui", help="Launch the graphical interface")
    sub_gui.set_defaults(func=cmd_gui)

    # serve
    sub_serve = subparsers.add_parser("serve", help="Start the fleet agent server")
    sub_serve.add_argument("--host", default="0.0.0.0", help="Bind address (default: 0.0.0.0)")
    sub_serve.add_argument("--port", type=int, default=None, help="Listen port (default: 8077)")
    sub_serve.add_argument("--name", help="Node name (overrides config)")
    sub_serve.add_argument("--token", help="Auth token (overrides config)")
    sub_serve.set_defaults(func=cmd_serve)

    # fleet (parent parser for fleet subcommands)
    sub_fleet = subparsers.add_parser("fleet", help="Fleet management commands")
    fleet_sub = sub_fleet.add_subparsers(dest="fleet_command", help="Fleet subcommands")

    # fleet list
    sub_fleet_list = fleet_sub.add_parser("list", help="List all fleet nodes and VMs")
    sub_fleet_list.set_defaults(func=cmd_fleet_list)

    # fleet run
    sub_fleet_run = fleet_sub.add_parser("run", help="Send a task to a fleet node")
    sub_fleet_run.add_argument("--node", help="Target node name")
    sub_fleet_run.add_argument("--vm", required=True, help="VM title on the target node")
    sub_fleet_run.add_argument("--task", required=True, help="Task to execute")
    sub_fleet_run.add_argument("--max-actions", type=int, default=50, help="Max actions (default: 50)")
    sub_fleet_run.add_argument("--delay", type=float, default=1.0, help="Action delay in seconds (default: 1.0)")
    sub_fleet_run.add_argument("--all", action="store_true", help="Send task to all peers")
    sub_fleet_run.add_argument("-f", "--follow", action="store_true", help="Follow task progress")
    sub_fleet_run.set_defaults(func=cmd_fleet_run)

    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        return

    if args.command == "fleet" and not getattr(args, "fleet_command", None):
        sub_fleet.print_help()
        return

    args.func(args)


if __name__ == "__main__":
    main()
