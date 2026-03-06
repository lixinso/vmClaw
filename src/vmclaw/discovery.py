"""VM window discovery - enumerate and select VM windows on the host."""

from __future__ import annotations

import ctypes
import ctypes.wintypes
from typing import Callable

from .models import VMWindow

# Win32 constants
GWL_STYLE = -16
WS_VISIBLE = 0x10000000

# Default VM window title keywords
DEFAULT_KEYWORDS = [
    "vmconnect", "vmware", "virtualbox", "qemu", "hyper-v",
    "virtual machine connection",
]


def _get_window_thread_process_id(hwnd: int) -> int:
    """Get the process ID for a window handle."""
    pid = ctypes.wintypes.DWORD()
    ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value


def enum_windows() -> list[tuple[int, str]]:
    """Enumerate all visible top-level windows. Returns list of (hwnd, title)."""
    results: list[tuple[int, str]] = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def callback(hwnd: int, _lparam: int) -> bool:
        # Skip invisible windows
        style = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_STYLE)
        if not (style & WS_VISIBLE):
            return True

        # Get window title
        length = ctypes.windll.user32.GetWindowTextLengthW(hwnd)
        if length == 0:
            return True

        buf = ctypes.create_unicode_buffer(length + 1)
        ctypes.windll.user32.GetWindowTextW(hwnd, buf, length + 1)
        title = buf.value

        if title:
            results.append((hwnd, title))
        return True

    ctypes.windll.user32.EnumWindows(callback, 0)
    return results


def find_vm_windows(keywords: list[str] | None = None) -> list[VMWindow]:
    """Find windows that match VM-related keywords.

    Args:
        keywords: List of substrings to match against window titles.
                  Defaults to common VM application names.

    Returns:
        List of VMWindow objects for matching windows.
    """
    if keywords is None:
        keywords = DEFAULT_KEYWORDS

    keywords_lower = [k.lower() for k in keywords]
    all_windows = enum_windows()
    vm_windows = []

    for hwnd, title in all_windows:
        title_lower = title.lower()
        if any(k in title_lower for k in keywords_lower):
            pid = _get_window_thread_process_id(hwnd)
            vm_windows.append(VMWindow(hwnd=hwnd, title=title, pid=pid))

    return vm_windows


def find_all_windows() -> list[VMWindow]:
    """Return all visible windows (for manual selection when no VMs are auto-detected)."""
    all_windows = enum_windows()
    results = []
    for hwnd, title in all_windows:
        pid = _get_window_thread_process_id(hwnd)
        results.append(VMWindow(hwnd=hwnd, title=title, pid=pid))
    return results


def select_vm_window(
    keywords: list[str] | None = None,
    prompt_fn: Callable[[str], str] | None = None,
) -> VMWindow | None:
    """Interactive: find VM windows and let the user pick one.

    Args:
        keywords: Keywords to filter VM windows.
        prompt_fn: Function to get user input. Defaults to built-in input().

    Returns:
        Selected VMWindow or None if user cancels.
    """
    if prompt_fn is None:
        prompt_fn = input

    vm_windows = find_vm_windows(keywords)

    if not vm_windows:
        print("No VM windows detected. Showing all windows...")
        vm_windows = find_all_windows()

    if not vm_windows:
        print("No visible windows found.")
        return None

    print(f"\nFound {len(vm_windows)} window(s):\n")
    for i, win in enumerate(vm_windows, 1):
        print(f"  [{i}] {win.title}")

    print()
    choice = prompt_fn(f"Select window [1-{len(vm_windows)}] (or 'q' to quit): ")

    if choice.lower() in ("q", "quit", ""):
        return None

    try:
        idx = int(choice) - 1
        if 0 <= idx < len(vm_windows):
            return vm_windows[idx]
    except ValueError:
        pass

    print(f"Invalid selection: {choice}")
    return None
