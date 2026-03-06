"""vmClaw CLI entry point."""

from __future__ import annotations

import argparse
import io
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

    if config.provider == "github" and not config.github_token:
        print("Error: GitHub provider selected but no token configured.")
        print("Set GITHUB_TOKEN environment variable or add github_token to config.toml")
        return
    elif config.provider == "openai" and not config.openai_api_key:
        print("Error: No OpenAI API key configured.")
        print("Set OPENAI_API_KEY environment variable or add openai_api_key to config.toml")
        print("Tip: To use GitHub Copilot instead, set GITHUB_TOKEN and provider = \"github\"")
        return

    print("vmClaw - VM Computer Use Agent\n")
    print("Discovering VM windows...")

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
