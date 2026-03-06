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
    if action.action == ActionType.CLICK:
        if action.x is None or action.y is None:
            raise ValueError("Click action requires x and y coordinates")

        screen_x, screen_y = _map_coordinates(
            hwnd, action.x, action.y, img_width, img_height
        )
        pyautogui.click(screen_x, screen_y)

    elif action.action == ActionType.TYPE:
        if action.text is None:
            raise ValueError("Type action requires text")

        # Click the window first to ensure it has focus
        _ensure_window_focus(hwnd)
        # Use typewrite for ASCII, write for unicode
        pyautogui.write(action.text, interval=0.02)

    elif action.action == ActionType.KEY:
        if action.key is None:
            raise ValueError("Key action requires key name")

        _ensure_window_focus(hwnd)
        # Normalize key name for pyautogui
        key = _normalize_key(action.key)
        pyautogui.hotkey(*key.split("+"))

    elif action.action == ActionType.SCROLL:
        direction = action.direction or "down"
        # Get window center for scroll position
        left, top, right, bottom = get_window_rect(hwnd)
        center_x = (left + right) // 2
        center_y = (top + bottom) // 2
        pyautogui.moveTo(center_x, center_y)

        clicks = 3 if direction == "down" else -3
        pyautogui.scroll(clicks)

    elif action.action == ActionType.WAIT:
        time.sleep(1.5)

    elif action.action == ActionType.DONE:
        pass  # No action needed

    else:
        raise ValueError(f"Unknown action type: {action.action}")


def _ensure_window_focus(hwnd: int) -> None:
    """Ensure the VM window has keyboard focus."""
    user32 = ctypes.windll.user32
    fg = user32.GetForegroundWindow()
    if fg != hwnd:
        user32.SetForegroundWindow(hwnd)
        time.sleep(0.1)


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
