"""Diagnostic script to debug window handle discovery.

Run while Voice Typer is running to dump window info.
"""
import ctypes
import time
from ctypes import wintypes


def dump_windows(title_filter="Voice"):
    """Find all visible windows whose title contains *title_filter*."""
    user32 = ctypes.windll.user32
    results = []

    def _enum_cb(hwnd, _lparam):
        if user32.IsWindowVisible(hwnd):
            buf = ctypes.create_unicode_buffer(512)
            length = user32.GetWindowTextW(hwnd, buf, 512)
            title = buf.value if length > 0 else ""
            if title_filter.lower() in title.lower():
                pid = wintypes.DWORD()
                user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                # Get class name
                cls_buf = ctypes.create_unicode_buffer(256)
                user32.GetClassNameW(hwnd, cls_buf, 256)
                results.append((hwnd, title, pid.value, cls_buf.value))
        return True

    WNDENUMPROC = ctypes.CFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.EnumWindows(WNDENUMPROC(_enum_cb), 0)

    print(f"\nWindows containing '{title_filter}' in title:")
    print(f"{'HWND':<20} {'PID':<8} {'Class':<30} Title")
    print("-" * 80)
    for hwnd, title, pid, cls in results:
        print(f"{hwnd:<20} {pid:<8} {cls:<30} {title}")
    return results


def try_find_window():
    """Try FindWindowW with various approaches."""
    user32 = ctypes.windll.user32

    # Approach 1: FindWindowW with full title
    hwnd = user32.FindWindowW(None, "Voice Typer")
    print(f"\nFindWindowW('Voice Typer'): {hwnd}")

    # Approach 2: FindWindowW with class
    hwnd2 = user32.FindWindowW("FLET_WINDOW", None)
    print(f"FindWindowW('FLET_WINDOW', None): {hwnd2}")

    # Approach 3: FindWindowW with partial class
    hwnd3 = user32.FindWindowW("FLET", None)  
    print(f"FindWindowW('FLET', None): {hwnd3}")

    # Approach 4: GetForegroundWindow
    fg = user32.GetForegroundWindow()
    buf = ctypes.create_unicode_buffer(512)
    user32.GetWindowTextW(fg, buf, 512)
    print(f"GetForegroundWindow: {fg} title='{buf.value}'")


if __name__ == "__main__":
    print("=== Window Diagnostic Tool ===")
    print("Run this while Voice Typer is open.\n")
    
    for i in range(10):
        print(f"\n--- Attempt {i+1} ---")
        try_find_window()
        dump_windows("Voice")
        dump_windows("flet")
        dump_windows("flutter")
        time.sleep(1)