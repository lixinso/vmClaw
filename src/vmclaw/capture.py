"""Screen capture - capture screenshots of VM windows."""

from __future__ import annotations

import ctypes
import ctypes.wintypes
import time
from io import BytesIO
from pathlib import Path

from PIL import Image


# Win32 constants
SRCCOPY = 0x00CC0020
DIB_RGB_COLORS = 0
SW_RESTORE = 9
SW_SHOW = 5


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", ctypes.wintypes.DWORD),
        ("biWidth", ctypes.wintypes.LONG),
        ("biHeight", ctypes.wintypes.LONG),
        ("biPlanes", ctypes.wintypes.WORD),
        ("biBitCount", ctypes.wintypes.WORD),
        ("biCompression", ctypes.wintypes.DWORD),
        ("biSizeImage", ctypes.wintypes.DWORD),
        ("biXPelsPerMeter", ctypes.wintypes.LONG),
        ("biYPelsPerMeter", ctypes.wintypes.LONG),
        ("biClrUsed", ctypes.wintypes.DWORD),
        ("biClrImportant", ctypes.wintypes.DWORD),
    ]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [
        ("bmiHeader", BITMAPINFOHEADER),
        ("bmiColors", ctypes.wintypes.DWORD * 3),
    ]


def get_window_rect(hwnd: int) -> tuple[int, int, int, int]:
    """Get window rectangle (left, top, right, bottom)."""
    rect = ctypes.wintypes.RECT()
    ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rect))
    return rect.left, rect.top, rect.right, rect.bottom


def get_client_rect(hwnd: int) -> tuple[int, int, int, int]:
    """Get client area rectangle (0, 0, width, height)."""
    rect = ctypes.wintypes.RECT()
    ctypes.windll.user32.GetClientRect(hwnd, ctypes.byref(rect))
    return rect.left, rect.top, rect.right, rect.bottom


def is_minimized(hwnd: int) -> bool:
    """Check if a window is minimized."""
    return bool(ctypes.windll.user32.IsIconic(hwnd))


def is_admin() -> bool:
    """Check if the current process has administrator privileges."""
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def restore_window(hwnd: int) -> bool:
    """Restore a minimized window and bring it to the foreground.

    Uses a workaround for Windows' foreground lock: briefly attaches to the
    foreground window's thread to gain permission to set foreground.

    Returns:
        True if the window was successfully restored, False otherwise.
    """
    user32 = ctypes.windll.user32

    # Get foreground window's thread to attach to
    fg_hwnd = user32.GetForegroundWindow()
    fg_thread = user32.GetWindowThreadProcessId(fg_hwnd, None)
    cur_thread = ctypes.windll.kernel32.GetCurrentThreadId()

    # Attach to foreground thread to bypass foreground lock
    if fg_thread != cur_thread:
        user32.AttachThreadInput(cur_thread, fg_thread, True)

    try:
        if is_minimized(hwnd):
            user32.ShowWindow(hwnd, SW_RESTORE)
            time.sleep(0.3)

        user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)
    finally:
        if fg_thread != cur_thread:
            user32.AttachThreadInput(cur_thread, fg_thread, False)

    time.sleep(0.5)

    # Check if restore actually worked
    if is_minimized(hwnd):
        return False
    return True


def capture_window(hwnd: int) -> Image.Image | None:
    """Capture a window's content using PrintWindow (works even if occluded).

    Args:
        hwnd: Window handle to capture.

    Returns:
        PIL Image of the window content, or None on failure.
    """
    # Get window dimensions
    left, top, right, bottom = get_window_rect(hwnd)
    width = right - left
    height = bottom - top

    if width <= 0 or height <= 0:
        return None

    # Get device contexts
    hwnd_dc = ctypes.windll.user32.GetWindowDC(hwnd)
    if not hwnd_dc:
        return None

    mem_dc = ctypes.windll.gdi32.CreateCompatibleDC(hwnd_dc)
    if not mem_dc:
        ctypes.windll.user32.ReleaseDC(hwnd, hwnd_dc)
        return None

    bitmap = ctypes.windll.gdi32.CreateCompatibleBitmap(hwnd_dc, width, height)
    if not bitmap:
        ctypes.windll.gdi32.DeleteDC(mem_dc)
        ctypes.windll.user32.ReleaseDC(hwnd, hwnd_dc)
        return None

    try:
        ctypes.windll.gdi32.SelectObject(mem_dc, bitmap)

        # PrintWindow with PW_RENDERFULLCONTENT (flag 2) for better capture
        PW_RENDERFULLCONTENT = 2
        result = ctypes.windll.user32.PrintWindow(hwnd, mem_dc, PW_RENDERFULLCONTENT)

        if not result:
            # Fallback to BitBlt
            ctypes.windll.gdi32.BitBlt(
                mem_dc, 0, 0, width, height,
                hwnd_dc, 0, 0, SRCCOPY,
            )

        # Read bitmap data
        bmi = BITMAPINFO()
        bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bmi.bmiHeader.biWidth = width
        bmi.bmiHeader.biHeight = -height  # Top-down
        bmi.bmiHeader.biPlanes = 1
        bmi.bmiHeader.biBitCount = 32
        bmi.bmiHeader.biCompression = 0  # BI_RGB

        buf_size = width * height * 4
        buf = ctypes.create_string_buffer(buf_size)

        ctypes.windll.gdi32.GetDIBits(
            mem_dc, bitmap, 0, height,
            buf, ctypes.byref(bmi), DIB_RGB_COLORS,
        )

        # Convert BGRA to RGB
        img = Image.frombytes("RGBA", (width, height), buf, "raw", "BGRA")
        return img.convert("RGB")

    finally:
        ctypes.windll.gdi32.DeleteObject(bitmap)
        ctypes.windll.gdi32.DeleteDC(mem_dc)
        ctypes.windll.user32.ReleaseDC(hwnd, hwnd_dc)


def capture_window_region(hwnd: int) -> Image.Image | None:
    """Capture a window by its screen region using mss (requires window in foreground).

    Fallback method if PrintWindow doesn't work well for a given VM.
    """
    try:
        import mss
    except ImportError:
        return None

    left, top, right, bottom = get_window_rect(hwnd)
    width = right - left
    height = bottom - top

    if width <= 0 or height <= 0:
        return None

    monitor = {"left": left, "top": top, "width": width, "height": height}

    with mss.mss() as sct:
        screenshot = sct.grab(monitor)
        img = Image.frombytes("RGB", screenshot.size, screenshot.bgra, "raw", "BGRX")
        return img


def capture_and_resize(
    hwnd: int, target_width: int = 1024
) -> Image.Image | None:
    """Capture a window and resize to target width (maintaining aspect ratio).

    Automatically restores minimized windows before capturing.

    Args:
        hwnd: Window handle to capture.
        target_width: Target image width. Height scales proportionally.

    Returns:
        Resized PIL Image, or None on failure.
    """
    # Restore if minimized
    was_minimized = is_minimized(hwnd)
    if was_minimized:
        restored = restore_window(hwnd)
        if not restored:
            if not is_admin():
                print(
                    "WARNING: Cannot restore minimized VM window. "
                    "Run vmClaw as Administrator to control elevated VM windows."
                )
            return None
        time.sleep(0.5)  # Extra wait for VM content to render

    # Use PrintWindow first — it works even when the window is behind other
    # windows, so vmclaw doesn't need to steal foreground focus.  Fall back
    # to mss region capture if PrintWindow fails.
    img = capture_window(hwnd)
    if img is None:
        img = capture_window_region(hwnd)
    if img is None:
        return None

    # Resize maintaining aspect ratio
    orig_width, orig_height = img.size
    if orig_width <= 0:
        return None

    scale = target_width / orig_width
    target_height = int(orig_height * scale)
    img = img.resize((target_width, target_height), Image.LANCZOS)

    return img


def image_to_bytes(img: Image.Image, fmt: str = "PNG") -> bytes:
    """Convert a PIL Image to bytes."""
    buf = BytesIO()
    img.save(buf, format=fmt)
    return buf.getvalue()


def save_screenshot(img: Image.Image, path: str | Path) -> Path:
    """Save a screenshot to disk."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path)
    return path
