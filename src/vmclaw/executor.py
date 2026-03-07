"""Action executor - translate AI actions into mouse/keyboard input on VM windows."""

from __future__ import annotations

import ctypes
import ctypes.wintypes
import time

import pyautogui

from .capture import get_window_rect
from .models import Action, ActionType

# Disable pyautogui's built-in pause and failsafe for controlled execution
pyautogui.PAUSE = 0.05
pyautogui.FAILSAFE = True  # Move mouse to corner to abort

# --- Low-level SendInput mouse helper ---
# pyautogui uses SetCursorPos to move the cursor which is invisible to RDP/VM
# Connection windows (Hyper-V vmconnect, VMware, etc.) that use Raw Input.
# We send MOUSEEVENTF_MOVE|ABSOLUTE via SendInput so the VM sees real movement.

INPUT_MOUSE = 0
MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_ABSOLUTE = 0x8000


class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", ctypes.c_long),
        ("dy", ctypes.c_long),
        ("mouseData", ctypes.c_ulong),
        ("dwFlags", ctypes.c_ulong),
        ("time", ctypes.c_ulong),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]


class _INPUT_UNION(ctypes.Union):
    _fields_ = [("mi", _MOUSEINPUT)]


class _INPUT(ctypes.Structure):
    _fields_ = [
        ("type", ctypes.c_ulong),
        ("union", _INPUT_UNION),
    ]


def _to_absolute(x: int, y: int) -> tuple[int, int]:
    """Convert screen pixel coordinates to SendInput absolute coordinates (0-65535)."""
    sm = ctypes.windll.user32.GetSystemMetrics
    screen_w = sm(0)
    screen_h = sm(1)
    abs_x = int(x * 65536 / screen_w)
    abs_y = int(y * 65536 / screen_h)
    return abs_x, abs_y


def _send_click(x: int, y: int) -> None:
    """Click at absolute screen coordinates using SendInput.

    Uses MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE so that RDP / VM Connection
    windows see real hardware-level mouse movement and click events.
    """
    abs_x, abs_y = _to_absolute(x, y)

    # Move to position
    move = _INPUT()
    move.type = INPUT_MOUSE
    move.union.mi.dx = abs_x
    move.union.mi.dy = abs_y
    move.union.mi.dwFlags = MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE

    inputs = (_INPUT * 1)(move)
    ctypes.windll.user32.SendInput(1, ctypes.byref(inputs), ctypes.sizeof(_INPUT))
    time.sleep(0.05)

    # Mouse down
    down = _INPUT()
    down.type = INPUT_MOUSE
    down.union.mi.dx = abs_x
    down.union.mi.dy = abs_y
    down.union.mi.dwFlags = MOUSEEVENTF_LEFTDOWN | MOUSEEVENTF_ABSOLUTE

    inputs = (_INPUT * 1)(down)
    ctypes.windll.user32.SendInput(1, ctypes.byref(inputs), ctypes.sizeof(_INPUT))
    time.sleep(0.05)

    # Mouse up
    up = _INPUT()
    up.type = INPUT_MOUSE
    up.union.mi.dx = abs_x
    up.union.mi.dy = abs_y
    up.union.mi.dwFlags = MOUSEEVENTF_LEFTUP | MOUSEEVENTF_ABSOLUTE

    inputs = (_INPUT * 1)(up)
    ctypes.windll.user32.SendInput(1, ctypes.byref(inputs), ctypes.sizeof(_INPUT))


def _send_move(x: int, y: int) -> None:
    """Move cursor to absolute screen coordinates using SendInput."""
    abs_x, abs_y = _to_absolute(x, y)

    move = _INPUT()
    move.type = INPUT_MOUSE
    move.union.mi.dx = abs_x
    move.union.mi.dy = abs_y
    move.union.mi.dwFlags = MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE

    inputs = (_INPUT * 1)(move)
    ctypes.windll.user32.SendInput(1, ctypes.byref(inputs), ctypes.sizeof(_INPUT))


WM_LBUTTONDOWN = 0x0201
WM_LBUTTONUP = 0x0202
MK_LBUTTON = 0x0001


def _find_input_capture_window(hwnd: int) -> int | None:
    """Find the IHWindowClass child window inside a VM Connection window.

    Hyper-V vmconnect (and similar RDP-based VM viewers) use a child window
    with class ``IHWindowClass`` (titled "Input Capture Window") to intercept
    all mouse and keyboard input and forward it to the VM guest.  Sending
    WM_LBUTTONDOWN/UP directly to this window bypasses the Raw-Input
    injection-flag check that causes synthetic SendInput mouse events to
    be silently dropped.
    """
    found: list[int] = []

    def _cb(child_hwnd: int, _: int) -> bool:
        cls = ctypes.create_unicode_buffer(64)
        ctypes.windll.user32.GetClassNameW(child_hwnd, cls, 64)
        if cls.value == "IHWindowClass":
            found.append(child_hwnd)
            return False  # Stop enumeration
        return True

    WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM)
    ctypes.windll.user32.EnumChildWindows(hwnd, WNDENUMPROC(_cb), 0)
    return found[0] if found else None


def _post_click(ih_hwnd: int, screen_x: int, screen_y: int) -> None:
    """Send a click to the IHWindowClass window via PostMessage.

    PostMessage with WM_LBUTTONDOWN bypasses the Raw-Input
    LLMHF_INJECTED flag check, so vmconnect forwards the click to the VM.
    Coordinates are client-relative to the IHWindowClass window.
    """
    # IHWindowClass lives at screen origin (0,0) so client == screen coords
    rect = ctypes.wintypes.RECT()
    ctypes.windll.user32.GetWindowRect(ih_hwnd, ctypes.byref(rect))
    client_x = screen_x - rect.left
    client_y = screen_y - rect.top
    lparam = (client_y << 16) | (client_x & 0xFFFF)

    user32 = ctypes.windll.user32
    ok1 = user32.PostMessageW(ih_hwnd, WM_LBUTTONDOWN, MK_LBUTTON, lparam)
    time.sleep(0.05)
    ok2 = user32.PostMessageW(ih_hwnd, WM_LBUTTONUP, 0, lparam)
    if not ok1 or not ok2:
        print(
            "  [warn] PostMessage to IHWindowClass failed — vmclaw likely needs "
            "Administrator privileges. Run as admin to fix VM mouse input."
        )


def _map_coordinates(
    hwnd: int,
    img_x: int,
    img_y: int,
    img_width: int,
    img_height: int,
) -> tuple[int, int]:
    """Map image coordinates to screen coordinates relative to the VM window.

    Args:
        hwnd: Window handle.
        img_x: X coordinate in the screenshot image.
        img_y: Y coordinate in the screenshot image.
        img_width: Width of the screenshot image.
        img_height: Height of the screenshot image.

    Returns:
        Tuple of (screen_x, screen_y) in absolute screen coordinates.
    """
    left, top, right, bottom = get_window_rect(hwnd)
    win_width = right - left
    win_height = bottom - top

    # Scale from image coordinates to window coordinates
    screen_x = left + int((img_x / img_width) * win_width)
    screen_y = top + int((img_y / img_height) * win_height)

    return screen_x, screen_y


def execute_action(
    hwnd: int,
    action: Action,
    img_width: int = 1024,
    img_height: int = 768,
) -> None:
    """Execute an action on the VM window.

    Args:
        hwnd: Window handle of the target VM.
        action: The action to execute.
        img_width: Width of the screenshot image (for coordinate mapping).
        img_height: Height of the screenshot image (for coordinate mapping).
    """
    # Ensure the VM window is focused before any interactive action so that
    # the first click/key isn't swallowed by Windows to activate the window.
    if action.action in (
        ActionType.CLICK, ActionType.TYPE, ActionType.KEY, ActionType.SCROLL,
    ):
        _ensure_window_focus(hwnd)

    if action.action == ActionType.CLICK:
        if action.x is None or action.y is None:
            raise ValueError("Click action requires x and y coordinates")

        screen_x, screen_y = _map_coordinates(
            hwnd, action.x, action.y, img_width, img_height
        )
        ih_hwnd = _find_input_capture_window(hwnd)
        if ih_hwnd:
            _send_move(screen_x, screen_y)
            time.sleep(0.05)
            _post_click(ih_hwnd, screen_x, screen_y)
        else:
            _send_click(screen_x, screen_y)

    elif action.action == ActionType.TYPE:
        if action.text is None:
            raise ValueError("Type action requires text")

        # Use typewrite for ASCII, write for unicode
        pyautogui.write(action.text, interval=0.02)

    elif action.action == ActionType.KEY:
        if action.key is None:
            raise ValueError("Key action requires key name")

        # Normalize key name for pyautogui
        key = _normalize_key(action.key)
        pyautogui.hotkey(*key.split("+"))

    elif action.action == ActionType.SCROLL:
        direction = action.direction or "down"
        # Get window center for scroll position
        left, top, right, bottom = get_window_rect(hwnd)
        center_x = (left + right) // 2
        center_y = (top + bottom) // 2
        _send_move(center_x, center_y)

        clicks = 3 if direction == "down" else -3
        pyautogui.scroll(clicks)

    elif action.action == ActionType.WAIT:
        time.sleep(1.5)

    elif action.action == ActionType.DONE:
        pass  # No action needed

    else:
        raise ValueError(f"Unknown action type: {action.action}")


def _ensure_window_focus(hwnd: int) -> None:
    """Ensure the VM window has focus and mouse capture.

    VM Connection windows (Hyper-V vmconnect, VMware, etc.) need an actual
    click inside the client area to start forwarding mouse/keyboard events
    to the guest.  A plain ``SetForegroundWindow`` is not enough.

    When the window is not already in the foreground we:
    1. Call ``SetForegroundWindow`` to tell Windows we want the window.
    2. Click the center of the window to establish VM mouse capture.
    3. Wait for the activation to settle before the real action fires.
    """
    user32 = ctypes.windll.user32
    fg = user32.GetForegroundWindow()
    if fg != hwnd:
        user32.SetForegroundWindow(hwnd)
        time.sleep(0.15)

        # Click center of the window to establish VM mouse capture
        left, top, right, bottom = get_window_rect(hwnd)
        center_x = (left + right) // 2
        center_y = (top + bottom) // 2
        ih_hwnd = _find_input_capture_window(hwnd)
        if ih_hwnd:
            _send_move(center_x, center_y)
            time.sleep(0.05)
            _post_click(ih_hwnd, center_x, center_y)
        else:
            _send_click(center_x, center_y)
        time.sleep(0.3)


def _normalize_key(key: str) -> str:
    """Normalize key names to pyautogui format.

    Handles common variations like 'Return' -> 'enter', 'Escape' -> 'esc'.
    """
    key_map = {
        "return": "enter",
        "escape": "esc",
        "backspace": "backspace",
        "delete": "delete",
        "space": "space",
        "tab": "tab",
        "up": "up",
        "down": "down",
        "left": "left",
        "right": "right",
        "home": "home",
        "end": "end",
        "pageup": "pageup",
        "pagedown": "pagedown",
        "f1": "f1", "f2": "f2", "f3": "f3", "f4": "f4",
        "f5": "f5", "f6": "f6", "f7": "f7", "f8": "f8",
        "f9": "f9", "f10": "f10", "f11": "f11", "f12": "f12",
    }

    # Handle compound keys like ctrl+a, alt+F4
    parts = key.lower().split("+")
    normalized = []
    for part in parts:
        part = part.strip()
        normalized.append(key_map.get(part, part))

    return "+".join(normalized)
