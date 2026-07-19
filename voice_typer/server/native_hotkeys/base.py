"""Base class for native subprocess hotkey backends.

Split out from the original ``native_hotkeys.py`` god-file in Phase 4.5
(ARCH-045).

This module owns:

- :data:`MAX_RESTART_ATTEMPTS`, :data:`RESTART_DELAY_BASE_SECONDS`,
  :data:`READY_TIMEOUT_SECONDS` — restart/backoff constants.
- :class:`SubprocessHotkeyBackend` — ABC that handles subprocess
  plumbing, parsing, matching, restart, and shutdown for all three
  platform backends (macOS / Windows / Linux).

Patch-path compatibility: tests do
``monkeypatch.setattr(native_hotkeys, "is_macos", lambda: True)``
(and is_windows / is_linux).  For the patch to take effect on calls
made from *this* submodule, the bare ``is_macos()`` references must
resolve to the package-level binding (which is what the patch
replaces).  We therefore expose them as thin wrappers that delegate
to the package's binding at call time, rather than capturing the
function object at import time.
"""

import signal
import subprocess
import threading
from abc import ABC, abstractmethod
from collections.abc import Callable
from pathlib import Path

from voice_typer.server import native_hotkeys as _native_hotkeys_pkg

from .binary_path import get_native_binary_path
from .modifiers import _canonical_modifier, _canonical_modifier_name_for_token
from .spec_parser import log, parse_hotkey_spec

# See factory.py / mac_backend.py / etc. for the rationale.
is_windows = lambda: _native_hotkeys_pkg.is_windows()
is_macos = lambda: _native_hotkeys_pkg.is_macos()
is_linux = lambda: _native_hotkeys_pkg.is_linux()

# ─── Constants ─────────────────────────────────────────────────────────────

MAX_RESTART_ATTEMPTS = 5
RESTART_DELAY_BASE_SECONDS = 1.0  # 1, 2, 4, 8, 16s backoff
READY_TIMEOUT_SECONDS = 5.0


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
        self._on_release_callback: Callable[[], None] | None = None
        self._process: subprocess.Popen | None = None
        self._reader_thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._ready_event = threading.Event()
        self._failed = False
        self._error_message: str | None = None
        self._binary_path: Path | None = get_native_binary_path()
        # Hotkey state tracking for matching
        self._held_modifiers: set[str] = set()
        self._fn_down: bool = False
        self._main_key_down: bool = False
        self._match_lock = threading.Lock()
        # Toggle-mode flag: when True (set by HotkeyDispatcher for the main
        # dictation hotkey in toggle mode), the toggle fires on key-UP
        # (release) instead of key-down. Prevents a press-and-hold from
        # starting and then immediately stopping recording.
        self._toggle_on_keyup: bool = False
        # GAP-2/GAP-4: optional callbacks invoked from _handle_line and
        # _reader_loop. Set by _NativeBackendAdapter (in hotkeys.py) so
        # the adapter can (a) show a permission notification on ERROR
        # and (b) swap to a legacy backend when the native binary dies
        # permanently. Both default to None (no-op) so the callbacks are
        # opt-in and don't affect tests that don't care about them.
        self._on_error_callback: Callable[[str], None] | None = None
        self._on_permanent_failure_callback: Callable[[], None] | None = None

    # ── HotkeyBackend interface (compatible with hotkeys.HotkeyBackend) ──

    def set_on_release(self, callback: Callable[[], None] | None) -> None:
        """Set the callback for key release (push-to-talk mode)."""
        self._on_release_callback = callback

    def set_toggle_on_keyup(self, value: bool) -> None:
        """In toggle mode, fire the toggle on key-up (release) instead of
        key-down. Set True by HotkeyDispatcher for the main dictation
        hotkey so a press-and-hold cannot start-then-stop recording.
        """
        self._toggle_on_keyup = value

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
            self.platform_name,
            self._binary_path,
            self.hotkey_str,
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
    def _validate_platform(self) -> str | None:
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
                    self._error_message = f"{self.platform_name} binary crashed {attempts} times; giving up"
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
                    self.platform_name,
                    attempts,
                    MAX_RESTART_ATTEMPTS,
                    delay,
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
                    self.platform_name,
                    line,
                )

    def _handle_line(self, line: str) -> None:
        """Parse one wire-protocol line and dispatch to the hotkey matcher."""
        if line == "READY":
            self._ready_event.set()
            log.info("[NATIVE-HOTKEY] %s binary is READY", self.platform_name)
            return

        if line.startswith("ERROR:"):
            self._failed = True
            self._error_message = line[len("ERROR:") :]
            log.error(
                "[NATIVE-HOTKEY] %s binary reported ERROR: %s",
                self.platform_name,
                self._error_message,
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
            mod_name = line[len("MOD_DOWN:") :]
            self._on_modifier_event(mod_name, down=True)
            return
        if line.startswith("MOD_UP:"):
            mod_name = line[len("MOD_UP:") :]
            self._on_modifier_event(mod_name, down=False)
            return

        if line.startswith("KEY_DOWN:"):
            key_name = line[len("KEY_DOWN:") :]
            self._on_key_event(key_name, down=True)
            return
        if line.startswith("KEY_UP:"):
            key_name = line[len("KEY_UP:") :]
            self._on_key_event(key_name, down=False)
            return

        log.debug("[NATIVE-HOTKEY] Unrecognized line from %s: %r", self.platform_name, line)

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

    def _try_match(self, down: bool, *, key_name: str | None = None) -> None:
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

        # Modifier-only hotkey (e.g. <alt>, or <ctrl>+<alt>)
        if parsed["is_modifier_only"]:
            required = parsed["modifiers"]
            if "fn" in required:
                # Already handled by FN_DOWN/FN_UP above
                return
            # Convert spec tokens to canonical modifier names
            required_canonical = set()
            for token in required:
                c = _canonical_modifier_name_for_token(token)
                if c is not None:
                    required_canonical.add(c)
            if not required_canonical:
                return
            with self._match_lock:
                held = set(self._held_modifiers)
            # The hotkey is "these exact modifiers and no others"
            if held != required_canonical:
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
            if self._on_release_callback is not None:
                # Push-to-talk: start recording on press.
                self._fire_callback()
            elif getattr(self, "_toggle_on_keyup", False):
                # Toggle mode with toggle_on_keyup: defer the toggle to
                # key-up so holding the key cannot start-then-stop
                # recording. Do nothing here.
                pass
            else:
                # Legacy toggle (e.g. ESC, repaste): fire on press.
                self._fire_callback()
        else:
            if self._on_release_callback is not None:
                # Push-to-talk: stop recording on release.
                self._fire_on_release()
            elif getattr(self, "_toggle_on_keyup", False):
                # Toggle mode: fire the toggle exactly once on key-up.
                # Holding the key (no key-up) never toggles, so a
                # press-and-hold cannot start-then-stop recording.
                self._fire_callback()
            # else: legacy toggle-on-keydown -> nothing to do on key-up.

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
