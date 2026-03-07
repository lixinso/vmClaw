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


WM_MOUSEMOVE = 0x0200
WM_LBUTTONDOWN = 0x0201
WM_LBUTTONUP = 0x0202
WM_MOUSEWHEEL = 0x020A
WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
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


def _ih_lparam(ih_hwnd: int, screen_x: int, screen_y: int) -> int:
    """Compute client-relative lparam for IHWindowClass PostMessage calls."""
    rect = ctypes.wintypes.RECT()
    ctypes.windll.user32.GetWindowRect(ih_hwnd, ctypes.byref(rect))
    client_x = screen_x - rect.left
    client_y = screen_y - rect.top
    return (client_y << 16) | (client_x & 0xFFFF)


def _post_click(ih_hwnd: int, screen_x: int, screen_y: int) -> None:
    """Send a mouse-move + click to IHWindowClass via PostMessage.

    Moves the VM guest cursor via WM_MOUSEMOVE, then clicks via
    WM_LBUTTONDOWN/UP.  The host cursor is never touched.
    """
    lparam = _ih_lparam(ih_hwnd, screen_x, screen_y)
    user32 = ctypes.windll.user32

    # Move VM guest cursor to target (host cursor unaffected)
    user32.PostMessageW(ih_hwnd, WM_MOUSEMOVE, 0, lparam)
    time.sleep(0.01)

    ok1 = user32.PostMessageW(ih_hwnd, WM_LBUTTONDOWN, MK_LBUTTON, lparam)
    time.sleep(0.05)
    ok2 = user32.PostMessageW(ih_hwnd, WM_LBUTTONUP, 0, lparam)
    if not ok1 or not ok2:
        print(
            "  [warn] PostMessage to IHWindowClass failed — vmclaw likely needs "
            "Administrator privileges. Run as admin to fix VM mouse input."
        )


def _post_scroll(ih_hwnd: int, screen_x: int, screen_y: int, clicks: int) -> None:
    """Send a scroll event to IHWindowClass via PostMessage WM_MOUSEWHEEL."""
    lparam = _ih_lparam(ih_hwnd, screen_x, screen_y)
    # wParam high word = wheel delta (120 per click), low word = key state
    delta = clicks * 120
    wparam = (delta & 0xFFFF) << 16
    ctypes.windll.user32.PostMessageW(ih_hwnd, WM_MOUSEWHEEL, wparam, lparam)


# ---- Keyboard via PostMessage ----

# Extended virtual keys that need bit 24 set in lparam
_EXTENDED_VKS = {
    0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x27, 0x28,  # pgup..right
    0x2D, 0x2E,  # insert, delete
    0x5B, 0x5C,  # lwin, rwin
}

# Map friendly key names to virtual-key codes
_VK_NAME_MAP = {
    "enter": 0x0D, "return": 0x0D,
    "esc": 0x1B, "escape": 0x1B,
    "backspace": 0x08, "back": 0x08,
    "delete": 0x2E, "del": 0x2E,
    "tab": 0x09,
    "space": 0x20,
    "up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27,
    "home": 0x24, "end": 0x23,
    "pageup": 0x21, "pagedown": 0x22,
    "insert": 0x2D,
    "f1": 0x70, "f2": 0x71, "f3": 0x72, "f4": 0x73,
    "f5": 0x74, "f6": 0x75, "f7": 0x76, "f8": 0x77,
    "f9": 0x78, "f10": 0x79, "f11": 0x7A, "f12": 0x7B,
    "ctrl": 0x11, "control": 0x11,
    "alt": 0x12, "menu": 0x12,
    "shift": 0x10,
    "win": 0x5B, "super": 0x5B, "lwin": 0x5B, "windows": 0x5B, "command": 0x5B,
    "capslock": 0x14,
    "numlock": 0x90,
    "printscreen": 0x2C,
}


def _vk_from_name(name: str) -> int:
    """Resolve a key name to a virtual-key code."""
    low = name.lower().strip()
    if low in _VK_NAME_MAP:
        return _VK_NAME_MAP[low]
    # Single character -> use VkKeyScanW
    if len(low) == 1:
        result = ctypes.windll.user32.VkKeyScanW(ord(low))
        if result != -1:
            return result & 0xFF
    raise ValueError(f"Unknown key name: {name!r}")


def _post_key_event(ih_hwnd: int, vk: int, is_up: bool = False) -> None:
    """Send a single WM_KEYDOWN or WM_KEYUP to IHWindowClass."""
    user32 = ctypes.windll.user32
    scan = user32.MapVirtualKeyW(vk, 0)  # MAPVK_VK_TO_VSC
    lparam = 1  # repeat count
    lparam |= (scan & 0xFF) << 16
    if vk in _EXTENDED_VKS:
        lparam |= 1 << 24
    if is_up:
        lparam |= (1 << 30) | (1 << 31)
    msg = WM_KEYUP if is_up else WM_KEYDOWN
    user32.PostMessageW(ih_hwnd, msg, vk, lparam)


def _post_key(ih_hwnd: int, key_spec: str) -> None:
    """Send a hotkey combination to IHWindowClass via PostMessage.

    Accepts key specs like ``"enter"``, ``"ctrl+a"``, ``"alt+f4"``, ``"Super"``.
    """
    parts = [p.strip() for p in key_spec.split("+")]
    vks = [_vk_from_name(p) for p in parts]

    # Press all keys down in order
    for vk in vks:
        _post_key_event(ih_hwnd, vk)
        time.sleep(0.01)

    # Release in reverse order
    for vk in reversed(vks):
        _post_key_event(ih_hwnd, vk, is_up=True)
        time.sleep(0.01)


def _post_type(ih_hwnd: int, text: str) -> None:
    """Type a string into IHWindowClass via PostMessage WM_KEYDOWN/WM_KEYUP.

    For each character, determines the required virtual key and modifier
    state using VkKeyScanW, sends modifier down, key down/up, modifier up.
    """
    user32 = ctypes.windll.user32
    for char in text:
        result = user32.VkKeyScanW(ord(char))
        if result == -1 or result == 0xFFFF:
            continue  # Character unavailable on current keyboard layout
        vk = result & 0xFF
        shift_state = (result >> 8) & 0xFF

        # Press modifiers
        if shift_state & 1:
            _post_key_event(ih_hwnd, 0x10)  # VK_SHIFT
        if shift_state & 2:
            _post_key_event(ih_hwnd, 0x11)  # VK_CONTROL
        if shift_state & 4:
            _post_key_event(ih_hwnd, 0x12)  # VK_MENU (Alt)

        # Key down + up
        _post_key_event(ih_hwnd, vk)
        time.sleep(0.01)
        _post_key_event(ih_hwnd, vk, is_up=True)

        # Release modifiers (reverse order)
        if shift_state & 4:
            _post_key_event(ih_hwnd, 0x12, is_up=True)
        if shift_state & 2:
            _post_key_event(ih_hwnd, 0x11, is_up=True)
        if shift_state & 1:
            _post_key_event(ih_hwnd, 0x10, is_up=True)

        time.sleep(0.02)


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

    # Clamp to valid image bounds (AI may return coords slightly outside)
    img_x = max(0, min(img_x, img_width - 1))
    img_y = max(0, min(img_y, img_height - 1))

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

    When an IHWindowClass child window is found (Hyper-V vmconnect), all
    input is sent via PostMessage so the host cursor and focus are never
    disturbed.  For other windows, falls back to SendInput / pyautogui.

    Args:
        hwnd: Window handle of the target VM.
        action: The action to execute.
        img_width: Width of the screenshot image (for coordinate mapping).
        img_height: Height of the screenshot image (for coordinate mapping).
    """
    ih_hwnd = _find_input_capture_window(hwnd)

    # vmconnect's IHWindowClass only processes PostMessage input when the
    # window is in the foreground.  Bring it to front (steals focus, but the
    # host cursor stays put because we never call SendInput for moves).
    if action.action in (
        ActionType.CLICK, ActionType.TYPE, ActionType.KEY, ActionType.SCROLL,
    ):
        user32 = ctypes.windll.user32
        if user32.GetForegroundWindow() != hwnd:
            user32.SetForegroundWindow(hwnd)
            time.sleep(0.15)
        if not ih_hwnd:
            _ensure_window_focus(hwnd)

    if action.action == ActionType.CLICK:
        if action.x is None or action.y is None:
            raise ValueError("Click action requires x and y coordinates")

        screen_x, screen_y = _map_coordinates(
            hwnd, action.x, action.y, img_width, img_height
        )
        if ih_hwnd:
            _post_click(ih_hwnd, screen_x, screen_y)
        else:
            _send_click(screen_x, screen_y)

    elif action.action == ActionType.TYPE:
        if action.text is None:
            raise ValueError("Type action requires text")

        if ih_hwnd:
            _post_type(ih_hwnd, action.text)
        else:
            pyautogui.write(action.text, interval=0.02)

    elif action.action == ActionType.KEY:
        if action.key is None:
            raise ValueError("Key action requires key name")

        if ih_hwnd:
            _post_key(ih_hwnd, action.key)
        else:
            key = _normalize_key(action.key)
            pyautogui.hotkey(*key.split("+"))

    elif action.action == ActionType.SCROLL:
        direction = action.direction or "down"
        left, top, right, bottom = get_window_rect(hwnd)
        center_x = (left + right) // 2
        center_y = (top + bottom) // 2
        clicks = 3 if direction == "down" else -3

        if ih_hwnd:
            _post_scroll(ih_hwnd, center_x, center_y, clicks)
        else:
            _send_move(center_x, center_y)
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
