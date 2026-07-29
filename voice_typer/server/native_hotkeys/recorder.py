"""Native hotkey recorder (capture mode).

Split out from the original ``native_hotkeys.py`` god-file in Phase 4.5
(ARCH-045).

This module owns:

- :class:`NativeHotkeyRecorder` — spawns the native binary in
  "stream" mode (with a dummy hotkey spec) and collects events into a
  queue so the caller can build a hotkey spec from a single key press.
"""

import contextlib
import threading
import time
from collections.abc import Sequence

from .base import SubprocessHotkeyBackend
from .factory import create_native_backend, is_native_backend_available
from .modifiers import _key_name_to_token, _modifier_to_token

# ─── Capture mode (hotkey recorder) ────────────────────────────────────────


class NativeHotkeyRecorder:
    """Hotkey recorder that uses the native binary in "stream" mode.

    The native binary doesn't have a separate record mode — it always
    emits all key events. This class spawns it with a dummy hotkey spec
    (``<f2>`` — never matches anything the user might press for capture)
    and collects events into a queue for the caller to consume.

    Usage::

        import logging
        log = logging.getLogger(__name__)

        recorder = NativeHotkeyRecorder(timeout=10.0)
        recorder.start()
        result = recorder.wait_for_event()
        recorder.stop()
        if result:
            log.info("[HOTKEY] Captured: %s", result)
    """

    DUMMY_SPEC = "<f2>"

    def __init__(self, timeout: float = 10.0):
        self.timeout = timeout
        self._backend: SubprocessHotkeyBackend | None = None
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
                backend._error_message = line[len("ERROR:") :]
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
                    name = line[len(prefix) :]
                    self._record_event(event_type, name)
                    return
            # Fall back to original handler for unknown lines
            original_handler(line)

        # XZ-CC-11: ``# type: ignore[assignment]`` is required because
        # ``recording_handler`` is a free function (signature
        # ``(line: str) -> None``) while ``backend._handle_line`` is a
        # bound method (signature ``(self, line: str) -> None``). The
        # override is intentional: the recorder needs to intercept ALL
        # wire-protocol lines during capture mode (not just hotkey
        # matches), so it replaces the bound method with a closure that
        # captures ``backend`` (and ``self``) via closure scope instead
        # of taking them as parameters. The override is reverted when
        # ``stop()`` sets ``self._backend = None`` (the next
        # ``start()`` call creates a fresh backend with the original
        # bound method).
        backend._handle_line = recording_handler  # type: ignore[assignment]
        backend.start(lambda: None)

    def _record_event(self, event_type: str, name: str) -> None:
        with self._cond:
            self._events.append((event_type, name))
            # Signal done on the first useful event
            if not self._done:
                self._done = True
                self._cond.notify_all()

    def wait_for_event(self) -> str | None:
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
            with contextlib.suppress(Exception):
                self._backend.stop()
            self._backend = None

    def _build_spec_from_events(self, events: Sequence[tuple[str, str]]) -> str | None:
        """Build a hotkey spec from the captured events.

        Strategy: take the first non-modifier KEY_DOWN as the main key,
        and any MOD_DOWN events that preceded it (and haven't been
        released) as the modifiers.
        """
        if not events:
            return None
        held_modifiers: list[str] = []
        main_key: str | None = None
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
