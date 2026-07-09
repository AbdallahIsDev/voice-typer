"""Native subprocess hotkey backends.

Provides out-of-process hotkey detection via small native binaries that
emit line-delimited events on stdout. This module implements the
"Freestyle architecture" for Voice Typer:

- macOS: ``macos-key-listener`` (Swift) — supports the FN key via
  ``NSEvent.modifierFlags.contains(.function)`` plus a ``CGEvent.tap``
  for key-up reliability and hotkey suppression.
- Windows: ``windows-key-listener.exe`` (C) — uses ``WH_KEYBOARD_LL``
  (event-driven) instead of ``GetAsyncKeyState`` polling. Lower CPU,
  sub-millisecond latency, supports key suppression and modifier-only
  detection.
- Linux: ``linux-key-listener`` (C) — uses ``/dev/input/event*``
  (evdev). Works on both X11 and Wayland (unlike pynput which is X11
  only). Read-only — no key suppression on Linux.

Wire protocol (line-delimited stdout, same for all three binaries):

    READY                  # emitted once after init succeeds
    FN_DOWN                # macOS only — Fn/Globe pressed (edge-detected)
    FN_UP                  # macOS only — Fn/Globe released (edge-detected)
    KEY_DOWN:<Name>        # non-modifier key pressed
    KEY_UP:<Name>          # non-modifier key released
    MOD_DOWN:<Name>        # modifier pressed (Ctrl, Shift, Alt, Cmd/Win/Super)
    MOD_UP:<Name>          # modifier released
    ERROR:<message>        # fatal error, binary will exit(1)

Key names are normalized across platforms:
- Letters: A..Z
- Digits: 0..9
- Function keys: F1..F24
- Special: Space, Enter, Tab, Esc, Backspace, Insert, Delete, Home,
  End, PageUp, PageDown, CapsLock, NumLock, ScrollLock, PrintScreen, Pause
- Arrows: Up, Down, Left, Right
- Numpad: Num0..Num9, NumDecimal, NumAdd, NumSubtract, NumMultiply,
  NumDivide, NumEnter
- Media: MediaPlay, MediaStop, MediaNext, MediaPrev
- Modifiers: Ctrl, Shift, Alt, Cmd (macOS), Win (Windows), Super (Linux)

All three backends share ``SubprocessHotkeyBackend`` which handles:

- Binary discovery (dev mode + PyInstaller bundle)
- subprocess.Popen with the hotkey spec as argv[1]
- Reader thread parsing stdout lines
- Hotkey matching against the parsed spec
- Restart-on-crash with exponential backoff (max 5 attempts)
- Clean shutdown via SIGTERM (POSIX) / terminate() (Windows)
"""

from __future__ import annotations

import logging
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Callable, Optional, Sequence

from voice_typer.server.platform_utils import is_linux, is_macos, is_windows

log = logging.getLogger("voice_typer.server.native_hotkeys")

# ─── Constants ─────────────────────────────────────────────────────────────

MAX_RESTART_ATTEMPTS = 5
RESTART_DELAY_BASE_SECONDS = 1.0  # 1, 2, 4, 8, 16s backoff
READY_TIMEOUT_SECONDS = 5.0

# Binary names per platform
_BINARY_NAMES = {
    "darwin": "macos-key-listener",
    "win32": "windows-key-listener.exe",
    "linux": "linux-key-listener",
}


# ─── Hotkey spec parsing ───────────────────────────────────────────────────


def parse_hotkey_spec(spec: str) -> Optional[dict]:
    """Parse a pynput-style hotkey spec into a structured form.

    Returns a dict with keys:
        - ``modifiers``: set of modifier names (lowercased: ctrl, shift, alt, cmd, fn)
        - ``main_key``: the non-modifier key name, or None if modifier-only
        - ``is_fn_only``: True if the hotkey is exactly ``<fn>``
        - ``is_modifier_only``: True if the hotkey is a single modifier
          (e.g. ``<alt>``, ``<caps_lock>``)
        - ``is_caps_lock``: True if main_key is "CapsLock"

    Returns None if the spec is empty or unparseable.
    """
    if not spec:
        return None

    # Strip < > and split on +
    cleaned = spec.strip().strip("<>")
    parts = [p.strip().strip("<>").strip() for p in cleaned.split("+")]
    parts = [p for p in parts if p]
    if not parts:
        return None

    modifier_aliases = {
        "ctrl": "ctrl", "control": "ctrl",
        "shift": "shift",
        "alt": "alt", "alt_l": "alt", "alt_r": "alt",
        "altgr": "altgr", "right_alt": "altgr", "ralt": "altgr",
        "cmd": "cmd", "cmd_l": "cmd", "cmd_r": "cmd",
        "win": "cmd", "win_l": "cmd", "win_r": "cmd",
        "super": "cmd", "super_l": "cmd", "super_r": "cmd",
        "fn": "fn", "globe": "fn",
    }

    modifiers: set[str] = set()
    main_key: Optional[str] = None

    for part in parts:
        lower = part.lower()
        if lower in modifier_aliases:
            modifiers.add(modifier_aliases[lower])
        else:
            # Non-modifier token — only one allowed
            if main_key is not None:
                log.warning(
                    "Hotkey spec %r has multiple non-modifier keys; using first",
                    spec,
                )
                continue
            main_key = _normalize_key_name(part)

    if not modifiers and main_key is None:
        return None

    is_fn_only = modifiers == {"fn"} and main_key is None
    is_modifier_only = (not main_key) and bool(modifiers)
    is_caps_lock = main_key == "CapsLock"

    return {
        "modifiers": modifiers,
        "main_key": main_key,
        "is_fn_only": is_fn_only,
        "is_modifier_only": is_modifier_only,
        "is_caps_lock": is_caps_lock,
    }


def _normalize_key_name(token: str) -> str:
    """Normalize a non-modifier key token to the wire-protocol name."""
    t = token.lower().strip()
    # Function keys
    if t.startswith("f") and t[1:].isdigit():
        n = int(t[1:])
        if 1 <= n <= 24:
            return f"F{n}"
    # Special keys
    special_map = {
        "space": "Space",
        "enter": "Enter", "return": "Enter",
        "tab": "Tab",
        "esc": "Esc", "escape": "Esc",
        "backspace": "Backspace",
        "insert": "Insert",
        "delete": "Delete", "del": "Delete",
        "home": "Home",
        "end": "End",
        "page_up": "PageUp", "pageup": "PageUp",
        "page_down": "PageDown", "pagedown": "PageDown",
        "caps_lock": "CapsLock", "capslock": "CapsLock",
        "num_lock": "NumLock", "numlock": "NumLock",
        "scroll_lock": "ScrollLock", "scrolllock": "ScrollLock",
        "print_screen": "PrintScreen", "printscreen": "PrintScreen",
        "pause": "Pause",
        "up": "Up", "down": "Down", "left": "Left", "right": "Right",
        "media_play_pause": "MediaPlay", "media_play": "MediaPlay",
        "media_stop": "MediaStop",
        "media_next": "MediaNext", "media_prev": "MediaPrev",
        "media_previous": "MediaPrev",
    }
    if t in special_map:
        return special_map[t]
    # Single letter
    if len(t) == 1 and t.isalpha():
        return t.upper()
    # Single digit
    if len(t) == 1 and t.isdigit():
        return t
    # Numpad keys
    numpad_map = {
        "num_0": "Num0", "num_1": "Num1", "num_2": "Num2", "num_3": "Num3",
        "num_4": "Num4", "num_5": "Num5", "num_6": "Num6", "num_7": "Num7",
        "num_8": "Num8", "num_9": "Num9",
        "numpad_0": "Num0", "numpad_1": "Num1", "numpad_2": "Num2",
        "numpad_3": "Num3", "numpad_4": "Num4", "numpad_5": "Num5",
        "numpad_6": "Num6", "numpad_7": "Num7", "numpad_8": "Num8",
        "numpad_9": "Num9",
        "num_decimal": "NumDecimal", "num_add": "NumAdd",
        "num_subtract": "NumSubtract", "num_multiply": "NumMultiply",
        "num_divide": "NumDivide", "num_enter": "NumEnter",
    }
    if t in numpad_map:
        return numpad_map[t]
    # Unknown — return as-is (will likely never match)
    return token


# ─── Binary discovery ──────────────────────────────────────────────────────


def get_native_binary_path() -> Optional[Path]:
    """Find the native key-listener binary for the current platform.

    Search order:
    1. ``VOICE_TYPER_NATIVE_BINARY`` env var (explicit override)
    2. ``voice_typer/server/native/<binary-name>`` (dev mode — source tree)
    3. ``voice_typer/server/native/<binary-name>.exe`` (Windows dev mode)
    4. Next to the Python executable (PyInstaller onedir mode)
    5. Inside ``_MEIPASS`` (PyInstaller onefile mode)

    Returns ``None`` if no binary is found.
    """
    binary_name = _BINARY_NAMES.get(sys.platform)
    if binary_name is None:
        return None

    # 1. Explicit override
    env_path = os.environ.get("VOICE_TYPER_NATIVE_BINARY")
    if env_path:
        p = Path(env_path)
        if p.is_file():
            return p

    # 2/3. Dev mode — alongside this module's source
    module_dir = Path(__file__).resolve().parent / "native"
    candidates = [
        module_dir / binary_name,
        # Some platforms may have a .exe suffix even in dev (cross-compile)
        module_dir / f"{binary_name}.exe",
    ]
    for c in candidates:
        if c.is_file():
            return c

    # 4. PyInstaller onedir: binary sits next to python executable
    exe_dir = Path(sys.executable).resolve().parent
    onedir_candidate = exe_dir / binary_name
    if onedir_candidate.is_file():
        return onedir_candidate

    # 5. PyInstaller onefile: binary extracted to _MEIPASS
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        meipass_candidate = Path(meipass) / "voice_typer" / "server" / "native" / binary_name
        if meipass_candidate.is_file():
            return meipass_candidate

    return None


# ─── Base class ────────────────────────────────────────────────────────────


class SubprocessHotkeyBackend(ABC):
    """Base class for out-of-process native hotkey backends.

    Subclasses just provide:
    - ``platform_name`` (used in log messages)
    - ``supports_fn`` (whether the FN key is observable on this platform)

    All the subprocess plumbing, parsing, matching, restart, and shutdown
    logic lives here.
    """

    platform_name: str = "subprocess"
    supports_fn: bool = False

    def __init__(self, hotkey_str: str):
        self.hotkey_str = hotkey_str
        self._parsed = parse_hotkey_spec(hotkey_str)
        self._on_release_callback: Optional[Callable[[], None]] = None
        self._process: Optional[subprocess.Popen] = None
        self._reader_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._ready_event = threading.Event()
        self._failed = False
        self._error_message: Optional[str] = None
        self._binary_path: Optional[Path] = get_native_binary_path()
        # Hotkey state tracking for matching
        self._held_modifiers: set[str] = set()
        self._fn_down: bool = False
        self._main_key_down: bool = False
        self._match_lock = threading.Lock()
        # GAP-2/GAP-4: optional callbacks invoked from _handle_line and
        # _reader_loop. Set by _NativeBackendAdapter (in hotkeys.py) so
        # the adapter can (a) show a permission notification on ERROR
        # and (b) swap to a legacy backend when the native binary dies
        # permanently. Both default to None (no-op) so the callbacks are
        # opt-in and don't affect tests that don't care about them.
        self._on_error_callback: Optional[Callable[[str], None]] = None
        self._on_permanent_failure_callback: Optional[Callable[[], None]] = None

    # ── HotkeyBackend interface (compatible with hotkeys.HotkeyBackend) ──

    def set_on_release(self, callback: Optional[Callable[[], None]]) -> None:
        """Set the callback for key release (push-to-talk mode)."""
        self._on_release_callback = callback

    def start(self, callback: Callable[[], None]) -> None:
        """Spawn the native binary and start parsing its stdout."""
        if self._parsed is None:
            raise ValueError(f"Cannot parse hotkey spec: {self.hotkey_str!r}")

        # Validate platform-specific constraints
        validation_error = self._validate_platform()
        if validation_error:
            raise ValueError(validation_error)

        if self._binary_path is None:
            raise FileNotFoundError(
                f"Native {self.platform_name} key-listener binary not found. "
                f"Set VOICE_TYPER_NATIVE_BINARY or rebuild the project."
            )

        log.info(
            "[NATIVE-HOTKEY] Starting %s backend (binary=%s, hotkey=%r)",
            self.platform_name, self._binary_path, self.hotkey_str,
        )

        self._callback = callback
        self._stop_event.clear()
        self._ready_event.clear()
        self._failed = False

        # Spawn the binary
        self._spawn_process()

        # Wait for READY (or ERROR/early exit)
        if not self._ready_event.wait(timeout=READY_TIMEOUT_SECONDS):
            self._failed = True
            self._error_message = f"Timed out waiting for READY from {self.platform_name} binary"
            log.error("[NATIVE-HOTKEY] %s", self._error_message)
            self.stop()
            raise RuntimeError(self._error_message)

        if self._failed:
            msg = self._error_message or f"{self.platform_name} binary failed to start"
            raise RuntimeError(msg)

    def stop(self) -> None:
        """Stop the binary cleanly."""
        if self._stop_event.is_set():
            return
        log.info("[NATIVE-HOTKEY] Stopping %s backend", self.platform_name)
        self._stop_event.set()

        if self._process is not None:
            try:
                if self._process.poll() is None:  # still running
                    # Try graceful shutdown first
                    try:
                        if is_windows():
                            self._process.terminate()
                        else:
                            self._process.send_signal(signal.SIGTERM)
                    except (OSError, ProcessLookupError):
                        pass
                    try:
                        self._process.wait(timeout=2.0)
                    except subprocess.TimeoutExpired:
                        # Force kill
                        try:
                            self._process.kill()
                            self._process.wait(timeout=1.0)
                        except (subprocess.TimeoutExpired, OSError):
                            pass
            finally:
                self._process = None

        if self._reader_thread is not None and self._reader_thread.is_alive():
            self._reader_thread.join(timeout=1.0)
            self._reader_thread = None

    def is_alive(self) -> bool:
        """Return True if the binary is running and READY was received."""
        return (
            self._process is not None
            and self._process.poll() is None
            and self._ready_event.is_set()
            and not self._stop_event.is_set()
        )

    def diagnose(self) -> str:
        """Return a human-readable diagnostic string."""
        binary = str(self._binary_path) if self._binary_path else "<not found>"
        alive = self._process is not None and self._process.poll() is None
        ready = self._ready_event.is_set()
        failed = self._failed
        return (
            f"{type(self).__name__} ({self.platform_name})\n"
            f"Hotkey: {self.hotkey_str}\n"
            f"Binary: {binary}\n"
            f"Process alive: {alive}\n"
            f"Ready: {ready}\n"
            f"Failed: {failed}\n"
            f"Error: {self._error_message or 'none'}"
        )

    # ── Platform-specific hooks ──────────────────────────────────────────

    @abstractmethod
    def _validate_platform(self) -> Optional[str]:
        """Return an error message if the hotkey is invalid for this platform,
        or None if valid. Subclasses must implement."""
        ...

    # ── Process management ───────────────────────────────────────────────

    def _spawn_process(self) -> None:
        """Spawn the native binary with the hotkey spec as argv[1]."""
        cmd = [str(self._binary_path), self.hotkey_str]
        try:
            self._process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                # No console window on Windows
                creationflags=(
                    subprocess.CREATE_NO_WINDOW if is_windows() and hasattr(subprocess, "CREATE_NO_WINDOW") else 0
                ),
                # Reset signal handlers in child so SIGTERM works cleanly
                start_new_session=is_macos() or is_linux(),
            )
        except OSError as exc:
            self._failed = True
            self._error_message = f"Failed to spawn {self.platform_name} binary: {exc}"
            raise RuntimeError(self._error_message) from exc

        # Start the reader thread
        self._reader_thread = threading.Thread(
            target=self._reader_loop,
            name=f"{self.platform_name}-hotkey-reader",
            daemon=True,
        )
        self._reader_thread.start()

    def _reader_loop(self) -> None:
        """Read lines from the binary's stdout and dispatch."""
        attempts = 0
        while not self._stop_event.is_set():
            if self._process is None or self._process.poll() is not None:
                # Process exited — decide whether to restart
                if self._stop_event.is_set():
                    return
                attempts += 1
                if attempts > MAX_RESTART_ATTEMPTS:
                    self._failed = True
                    self._error_message = (
                        f"{self.platform_name} binary crashed {attempts} times; giving up"
                    )
                    log.error("[NATIVE-HOTKEY] %s", self._error_message)
                    self._ready_event.set()  # unblock start() wait
                    # GAP-4: notify the adapter so it can swap to a
                    # legacy backend. The callback is invoked on the
                    # reader thread; adapters must be thread-safe.
                    if self._on_permanent_failure_callback is not None:
                        try:
                            self._on_permanent_failure_callback()
                        except Exception:
                            log.exception(
                                "[NATIVE-HOTKEY] _on_permanent_failure_callback raised in %s backend",
                                self.platform_name,
                            )
                    return
                delay = RESTART_DELAY_BASE_SECONDS * (2 ** (attempts - 1))
                log.warning(
                    "[NATIVE-HOTKEY] %s binary exited (attempt %d/%d); restarting in %.1fs",
                    self.platform_name, attempts, MAX_RESTART_ATTEMPTS, delay,
                )
                # Don't sleep with the GIL — use Event.wait for early cancel
                if self._stop_event.wait(timeout=delay):
                    return
                try:
                    self._spawn_process()
                except RuntimeError as exc:
                    self._failed = True
                    self._error_message = str(exc)
                    self._ready_event.set()
                    # Also notify the adapter on spawn failure (binary
                    # disappeared mid-restart, etc.)
                    if self._on_permanent_failure_callback is not None:
                        try:
                            self._on_permanent_failure_callback()
                        except Exception:
                            log.exception(
                                "[NATIVE-HOTKEY] _on_permanent_failure_callback raised in %s backend",
                                self.platform_name,
                            )
                    return
                continue

            assert self._process is not None
            assert self._process.stdout is not None
            try:
                line_bytes = self._process.stdout.readline()
            except Exception:
                line_bytes = b""
            if not line_bytes:
                # EOF — process likely exited
                continue

            line = line_bytes.decode("utf-8", errors="replace").rstrip("\r\n")
            if not line:
                continue
            try:
                self._handle_line(line)
            except Exception:
                log.exception(
                    "[NATIVE-HOTKEY] Error handling line from %s binary: %r",
                    self.platform_name, line,
                )

    def _handle_line(self, line: str) -> None:
        """Parse one wire-protocol line and dispatch to the hotkey matcher."""
        if line == "READY":
            self._ready_event.set()
            log.info("[NATIVE-HOTKEY] %s binary is READY", self.platform_name)
            return

        if line.startswith("ERROR:"):
            self._failed = True
            self._error_message = line[len("ERROR:"):]
            log.error(
                "[NATIVE-HOTKEY] %s binary reported ERROR: %s",
                self.platform_name, self._error_message,
            )
            self._ready_event.set()  # unblock start() wait
            # GAP-2: notify the adapter so it can classify the error and
            # potentially show a permission prompt. The callback is
            # invoked on the reader thread; adapters must be thread-safe.
            if self._on_error_callback is not None:
                try:
                    self._on_error_callback(self._error_message)
                except Exception:
                    log.exception(
                        "[NATIVE-HOTKEY] _on_error_callback raised in %s backend",
                        self.platform_name,
                    )
            return

        if line == "FN_DOWN":
            self._on_fn_event(down=True)
            return
        if line == "FN_UP":
            self._on_fn_event(down=False)
            return

        if line.startswith("MOD_DOWN:"):
            mod_name = line[len("MOD_DOWN:"):]
            self._on_modifier_event(mod_name, down=True)
            return
        if line.startswith("MOD_UP:"):
            mod_name = line[len("MOD_UP:"):]
            self._on_modifier_event(mod_name, down=False)
            return

        if line.startswith("KEY_DOWN:"):
            key_name = line[len("KEY_DOWN:"):]
            self._on_key_event(key_name, down=True)
            return
        if line.startswith("KEY_UP:"):
            key_name = line[len("KEY_UP:"):]
            self._on_key_event(key_name, down=False)
            return

        log.debug("[NATIVE-HOTKEY] Unrecognized line from %s: %r",
                  self.platform_name, line)

    # ── Hotkey matching ─────────────────────────────────────────────────

    def _on_fn_event(self, *, down: bool) -> None:
        """Handle FN_DOWN / FN_UP. Used by the macOS backend only."""
        with self._match_lock:
            self._fn_down = down
        self._try_match(down)

    def _on_modifier_event(self, mod_name: str, *, down: bool) -> None:
        """Handle MOD_DOWN / MOD_UP events.

        ``mod_name`` is one of: Ctrl, Shift, Alt, Cmd (macOS), Win
        (Windows), Super (Linux). We normalize all of these to lowercase
        'ctrl', 'shift', 'alt', 'cmd'.
        """
        canonical = _canonical_modifier(mod_name)
        if canonical is None:
            return
        with self._match_lock:
            if down:
                self._held_modifiers.add(canonical)
            else:
                self._held_modifiers.discard(canonical)
        # For modifier-only hotkeys (e.g. <alt> alone), the modifier
        # press itself is the trigger.
        if self._parsed and self._parsed["is_modifier_only"]:
            self._try_match(down)

    def _on_key_event(self, key_name: str, *, down: bool) -> None:
        """Handle KEY_DOWN / KEY_UP events."""
        with self._match_lock:
            if down:
                self._main_key_down = True
            else:
                self._main_key_down = False
        self._try_match(down, key_name=key_name)

    def _try_match(self, down: bool, *, key_name: Optional[str] = None) -> None:
        """Check if the current event matches the registered hotkey.

        Matching rules:
        - ``<fn>`` alone: matches FN_DOWN/FN_UP events
        - ``<modifier>`` alone (e.g. ``<alt>``): matches MOD_DOWN/MOD_UP of
          that modifier, with no other modifiers held
        - ``<caps_lock>`` alone: matches KEY_DOWN/KEY_UP of CapsLock
        - ``<key>`` alone (e.g. ``<f2>``): matches KEY_DOWN/KEY_UP of that key
          with no modifiers held
        - ``<modifier>+<key>`` (e.g. ``<ctrl>+<alt>+v``): matches KEY_DOWN/
          KEY_UP of the main key when ALL modifiers are currently held
        """
        if self._parsed is None:
            return
        parsed = self._parsed

        # FN-only hotkey
        if parsed["is_fn_only"]:
            if down:
                self._fire_callback()
            else:
                self._fire_on_release()
            return

        # Modifier-only hotkey (e.g. <alt>)
        if parsed["is_modifier_only"]:
            mod = next(iter(parsed["modifiers"]))  # the only modifier
            if mod == "fn":
                # Already handled by FN_DOWN/FN_UP above
                return
            canonical = _canonical_modifier_name_for_token(mod)
            if canonical is None:
                return
            with self._match_lock:
                held = set(self._held_modifiers)
                fn_down = self._fn_down
            # The hotkey is "this modifier and no others"
            if held != {canonical}:
                return
            if down:
                self._fire_callback()
            else:
                self._fire_on_release()
            return

        # Regular hotkey (single key or combo)
        main_key = parsed["main_key"]
        if key_name != main_key:
            return

        required_mods = parsed["modifiers"]
        with self._match_lock:
            held_mods = set(self._held_modifiers)
            # For FN-containing combos, add 'fn' to held_mods if FN is down
            if self._fn_down:
                held_mods.add("fn")

        # All required modifiers must be held
        if not required_mods.issubset(held_mods):
            return

        # No extra modifiers should be held (unless they're required)
        # This prevents <ctrl>+v from firing when <ctrl>+<alt>+v is held
        extra = held_mods - required_mods
        if extra:
            return

        if down:
            self._fire_callback()
        else:
            self._fire_on_release()

    def _fire_callback(self) -> None:
        """Invoke the press callback (with exception shielding)."""
        cb = getattr(self, "_callback", None)
        if cb is None:
            return
        try:
            cb()
        except Exception:
            log.exception(
                "[NATIVE-HOTKEY] Press callback raised in %s backend",
                self.platform_name,
            )

    def _fire_on_release(self) -> None:
        """Invoke the release callback (push-to-talk mode)."""
        if self._on_release_callback is None:
            return
        try:
            self._on_release_callback()
        except Exception:
            log.exception(
                "[NATIVE-HOTKEY] Release callback raised in %s backend",
                self.platform_name,
            )


# ─── Modifier name canonicalization ───────────────────────────────────────


_MOD_CANONICAL_MAP = {
    # NSEvent / macOS modifiers
    "Ctrl": "ctrl",
    "Shift": "shift",
    "Alt": "alt",
    "Cmd": "cmd",
    # Windows modifiers
    "Win": "cmd",
    # Linux modifiers
    "Super": "cmd",
}


def _canonical_modifier(wire_name: str) -> Optional[str]:
    """Convert a wire-protocol modifier name (e.g. 'Win', 'Super', 'Cmd')
    to a canonical lowercase form ('ctrl', 'shift', 'alt', 'cmd').

    Returns None if the name is not a recognized modifier.
    """
    return _MOD_CANONICAL_MAP.get(wire_name)


def _canonical_modifier_name_for_token(token: str) -> Optional[str]:
    """Convert a hotkey-spec modifier token (e.g. 'ctrl', 'alt', 'cmd',
    'win', 'super') to canonical form."""
    aliases = {
        "ctrl": "ctrl",
        "shift": "shift",
        "alt": "alt",
        "altgr": "alt",  # treat AltGr as Alt for matching purposes
        "cmd": "cmd",
        "win": "cmd",
        "super": "cmd",
    }
    return aliases.get(token)


# ─── Concrete platform backends ───────────────────────────────────────────


class MacNativeHotkey(SubprocessHotkeyBackend):
    """macOS native hotkey backend.

    Spawns ``macos-key-listener`` (Swift). Supports the FN key via
    ``NSEvent.modifierFlags.contains(.function)``. Requires macOS
    Accessibility permission.
    """

    platform_name = "macOS"
    supports_fn = True

    def _validate_platform(self) -> Optional[str]:
        if not is_macos():
            return f"MacNativeHotkey requires macOS (current: {sys.platform})"
        if self._parsed and "fn" in self._parsed["modifiers"]:
            # FN is supported on macOS
            pass
        return None


class WindowsHookHotkey(SubprocessHotkeyBackend):
    """Windows native hotkey backend.

    Spawns ``windows-key-listener.exe`` (C) which uses
    ``WH_KEYBOARD_LL`` (event-driven) instead of ``GetAsyncKeyState``
    polling. Lower CPU, supports key suppression and modifier-only
    detection.
    """

    platform_name = "Windows"
    supports_fn = False  # FN is firmware-only on Windows

    def _validate_platform(self) -> Optional[str]:
        if not is_windows():
            return f"WindowsHookHotkey requires Windows (current: {sys.platform})"
        if self._parsed and "fn" in self._parsed["modifiers"]:
            return (
                "FN key is not supported on Windows — it is firmware-only "
                "and never reaches the OS. Use Caps Lock, Alt, or a function "
                "key instead."
            )
        return None


class LinuxEvdevHotkey(SubprocessHotkeyBackend):
    """Linux native hotkey backend.

    Spawns ``linux-key-listener`` (C) which reads from
    ``/dev/input/event*`` (evdev). Works on both X11 and Wayland
    (unlike pynput which is X11-only). Requires the user to be in the
    ``input`` group.
    """

    platform_name = "Linux"
    supports_fn = False  # FN is firmware-only on most Linux laptops

    def _validate_platform(self) -> Optional[str]:
        if not is_linux():
            return f"LinuxEvdevHotkey requires Linux (current: {sys.platform})"
        if self._parsed and "fn" in self._parsed["modifiers"]:
            return (
                "FN key is not supported on Linux — it is firmware-only on "
                "most laptops. Use Caps Lock, Alt, or a function key instead."
            )
        return None


# ─── Factory ───────────────────────────────────────────────────────────────


def create_native_backend(hotkey_str: str) -> Optional[SubprocessHotkeyBackend]:
    """Create a native subprocess backend for the current platform.

    Returns ``None`` if no native binary is available (caller should fall
    back to a legacy backend).
    """
    binary = get_native_binary_path()
    if binary is None:
        return None

    if is_macos():
        return MacNativeHotkey(hotkey_str)
    if is_windows():
        return WindowsHookHotkey(hotkey_str)
    if is_linux():
        return LinuxEvdevHotkey(hotkey_str)
    return None


def is_native_backend_available() -> bool:
    """Return True if a native binary is available for the current platform."""
    return get_native_binary_path() is not None


# ─── Capture mode (hotkey recorder) ────────────────────────────────────────


class NativeHotkeyRecorder:
    """Hotkey recorder that uses the native binary in "stream" mode.

    The native binary doesn't have a separate record mode — it always
    emits all key events. This class spawns it with a dummy hotkey spec
    (``<f2>`` — never matches anything the user might press for capture)
    and collects events into a queue for the caller to consume.

    Usage::

        recorder = NativeHotkeyRecorder(timeout=10.0)
        recorder.start()
        result = recorder.wait_for_event()
        recorder.stop()
        if result:
            print(f"Captured: {result}")
    """

    DUMMY_SPEC = "<f2>"

    def __init__(self, timeout: float = 10.0):
        self.timeout = timeout
        self._backend: Optional[SubprocessHotkeyBackend] = None
        self._events: list[tuple[str, str]] = []  # (event_type, name)
        self._lock = threading.Lock()
        self._cond = threading.Condition(self._lock)
        self._done = False

    def start(self) -> None:
        """Spawn the native binary in stream mode."""
        if not is_native_backend_available():
            raise RuntimeError("No native binary available for recording")
        backend = create_native_backend(self.DUMMY_SPEC)
        if backend is None:
            raise RuntimeError("Failed to create native backend for recording")
        self._backend = backend
        # TASK-14: capture ``backend`` in a local so the
        # ``recording_handler`` closure below does not need to re-read
        # ``self._backend`` (which is ``Optional`` and could be reset to
        # ``None`` by ``stop()`` on another thread).  ``backend`` is
        # narrowed to non-None by the guard above, so attribute accesses
        # on it type-check cleanly.
        # Override the line handler so events go to our queue instead of
        # the hotkey matcher.
        original_handler = backend._handle_line

        def recording_handler(line: str) -> None:
            if line == "READY":
                backend._ready_event.set()
                return
            if line.startswith("ERROR:"):
                backend._failed = True
                backend._error_message = line[len("ERROR:"):]
                backend._ready_event.set()
                with self._cond:
                    self._done = True
                    self._cond.notify_all()
                return
            if line == "FN_DOWN":
                self._record_event("MOD_DOWN", "Fn")
                return
            if line == "FN_UP":
                self._record_event("MOD_UP", "Fn")
                return
            for prefix in ("KEY_DOWN:", "KEY_UP:", "MOD_DOWN:", "MOD_UP:"):
                if line.startswith(prefix):
                    event_type = prefix.rstrip(":")
                    name = line[len(prefix):]
                    self._record_event(event_type, name)
                    return
            # Fall back to original handler for unknown lines
            original_handler(line)

        backend._handle_line = recording_handler  # type: ignore[assignment]
        backend.start(lambda: None)

    def _record_event(self, event_type: str, name: str) -> None:
        with self._cond:
            self._events.append((event_type, name))
            # Signal done on the first useful event
            if not self._done:
                self._done = True
                self._cond.notify_all()

    def wait_for_event(self) -> Optional[str]:
        """Block until an event is captured or timeout elapses.

        Returns a hotkey-spec string like ``<caps_lock>`` or
        ``<ctrl>+<alt>+v``, or None on timeout.
        """
        deadline = time.monotonic() + self.timeout
        with self._cond:
            while not self._done:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                self._cond.wait(timeout=remaining)
            if not self._events:
                return None
        return self._build_spec_from_events(self._events)

    def stop(self) -> None:
        """Stop the recording binary."""
        if self._backend is not None:
            try:
                self._backend.stop()
            except Exception:
                pass
            self._backend = None

    def _build_spec_from_events(self, events: Sequence[tuple[str, str]]) -> Optional[str]:
        """Build a hotkey spec from the captured events.

        Strategy: take the first non-modifier KEY_DOWN as the main key,
        and any MOD_DOWN events that preceded it (and haven't been
        released) as the modifiers.
        """
        if not events:
            return None
        held_modifiers: list[str] = []
        main_key: Optional[str] = None
        for event_type, name in events:
            if event_type == "MOD_DOWN":
                if name not in held_modifiers:
                    held_modifiers.append(name)
            elif event_type == "MOD_UP":
                if name in held_modifiers:
                    held_modifiers.remove(name)
            elif event_type == "KEY_DOWN":
                main_key = name
                break
            elif event_type == "KEY_UP":
                # Single-key release without a preceding KEY_DOWN —
                # could be a modifier-only capture (e.g. user pressed
                # and released Alt). Build a modifier-only spec.
                pass

        # If we have only modifiers (no main key), build a modifier-only spec
        if main_key is None and held_modifiers:
            # Use the first held modifier as the hotkey
            mod = held_modifiers[0]
            return f"<{_modifier_to_token(mod)}>"

        if main_key is None:
            return None

        # Convert wire names back to spec tokens
        mod_tokens = [_modifier_to_token(m) for m in held_modifiers]
        key_token = _key_name_to_token(main_key)
        if key_token is None:
            return None
        parts = mod_tokens + [key_token]
        return "+".join(f"<{p}>" for p in parts)


def _modifier_to_token(wire_name: str) -> str:
    """Convert a wire-protocol modifier name to a spec token."""
    mapping = {
        "Ctrl": "ctrl",
        "Shift": "shift",
        "Alt": "alt",
        "Cmd": "cmd",
        "Win": "win",
        "Super": "super",
        "Fn": "fn",
    }
    return mapping.get(wire_name, wire_name.lower())


def _key_name_to_token(name: str) -> Optional[str]:
    """Convert a wire-protocol key name back to a spec token."""
    # Reverse of _normalize_key_name
    if not name:
        return None
    # Function keys
    if name.startswith("F") and name[1:].isdigit():
        return name.lower()
    # Single letter / digit
    if len(name) == 1:
        return name.lower()
    # Special keys (reverse map)
    reverse = {
        "Space": "space", "Enter": "enter", "Tab": "tab", "Esc": "esc",
        "Backspace": "backspace", "Insert": "insert", "Delete": "delete",
        "Home": "home", "End": "end",
        "PageUp": "page_up", "PageDown": "page_down",
        "CapsLock": "caps_lock", "NumLock": "num_lock",
        "ScrollLock": "scroll_lock", "PrintScreen": "print_screen",
        "Pause": "pause",
        "Up": "up", "Down": "down", "Left": "left", "Right": "right",
        "MediaPlay": "media_play_pause", "MediaStop": "media_stop",
        "MediaNext": "media_next", "MediaPrev": "media_prev",
        "Num0": "num_0", "Num1": "num_1", "Num2": "num_2", "Num3": "num_3",
        "Num4": "num_4", "Num5": "num_5", "Num6": "num_6", "Num7": "num_7",
        "Num8": "num_8", "Num9": "num_9",
        "NumDecimal": "num_decimal", "NumAdd": "num_add",
        "NumSubtract": "num_subtract", "NumMultiply": "num_multiply",
        "NumDivide": "num_divide", "NumEnter": "num_enter",
    }
    return reverse.get(name)
