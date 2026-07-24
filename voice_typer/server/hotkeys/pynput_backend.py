"""Pynput-based hotkey backend (cross-platform fallback).

Split out from the original ``hotkeys.py`` god-file in Phase 4.5
(ARCH-045).
"""

import contextlib
import time
from collections.abc import Callable

from voice_typer.server import hotkeys as _hotkeys_pkg

from .base import HotkeyBackend, log


# PLAT-030 / patch-target: tests do ``patch("voice_typer.server.hotkeys.is_macos")``
# (and is_windows / is_linux).  For the patch to take effect on calls
# made from *this* submodule, the bare ``is_macos()`` references must
# resolve to the package-level binding (which is what the patch
# replaces).  We therefore expose them as thin wrappers that delegate
# to the package's binding at call time, rather than capturing the
# function object at import time.
def is_macos() -> bool:
    return _hotkeys_pkg.is_macos()


def is_windows() -> bool:
    return _hotkeys_pkg.is_windows()


def is_linux() -> bool:
    return _hotkeys_pkg.is_linux()


class PynputHotkey(HotkeyBackend):
    """Hotkey backend using pynput.keyboard.GlobalHotKeys.

    Falls back to a regular ``Listener`` with manual key matching if
    ``GlobalHotKeys`` fails (common on some Windows / WSL setups).
    """

    def __init__(self, hotkey_str: str):
        super().__init__(hotkey_str)
        self._listener = None
        self._on_release_callback: Callable[[], None] | None = None
        # Toggle-mode flag: when True (set by HotkeyDispatcher for the main
        # dictation hotkey in toggle mode), the toggle fires on key-UP
        # (release) instead of key-down. Prevents a press-and-hold from
        # starting and then immediately stopping recording.
        self._toggle_on_keyup: bool = False

    def start(self, callback: Callable[[], None]) -> None:
        # PERF-012: On Linux/macOS, use pynput's event-driven Listener
        # instead of polling. The Listener receives key events from the
        # OS, so it uses zero CPU while idle and has zero latency.
        # On Windows, the WindowsNativeHotkey backend is preferred (uses
        # GetAsyncKeyState in a tight 1ms-polling loop).
        from pynput.keyboard import GlobalHotKeys, Key, KeyCode, Listener

        log.info("Registering hotkey via pynput: %r -> callback", self.hotkey_str)

        try:
            self._listener = GlobalHotKeys({self.hotkey_str: callback})
            self._listener.start()
            # PERF-NEW-017: was 0.5s — the listener thread reaches
            # "alive" state within a few ms. 50ms is enough on the
            # slowest machines. With 3 hotkeys (toggle, PTT, repaste)
            # this saves ~1.4s of startup time.
            time.sleep(0.05)
            alive = self._listener.is_alive()
            log.info(
                "Pynput GlobalHotKeys started (alive=%s, daemon=%s)",
                alive,
                getattr(self._listener, "daemon", "?"),
            )
            if not alive:
                log.error("GlobalHotKeys thread died immediately; falling back to manual Listener")
                self._stop_listener()
                self._start_fallback(callback, Listener, Key, KeyCode)
        except Exception:
            log.exception("[HOTKEY] GlobalHotKeys failed; trying fallback Listener")

            # PLAT-030: On macOS, pynput failure usually means the
            # Accessibility permission is missing. Show a user-friendly
            # guide so the user knows how to fix it.
            if is_macos():
                log.warning(
                    "[HOTKEY] macOS: pynput keyboard listener failed. "
                    "This usually means the Accessibility permission is not "
                    "granted to Voice Typer. To fix:\n"
                    "  1. Open System Preferences > Privacy & Security > "
                    "Accessibility\n"
                    "  2. Click the lock icon and authenticate\n"
                    "  3. Add Voice Typer (or your terminal/Python) to the "
                    "allowed apps\n"
                    "  4. Restart Voice Typer\n"
                    "Without this permission, hotkeys will not work."
                )

            try:
                self._start_fallback(callback, Listener, Key, KeyCode)
            except Exception:
                log.exception("[HOTKEY] Fallback Listener also failed")

                # PLAT-030: also warn for the fallback path on macOS
                if is_macos():
                    log.warning(
                        "[HOTKEY] macOS: Both GlobalHotKeys and Listener "
                        "failed. Please grant Accessibility permissions:\n"
                        "  System Preferences > Privacy & Security > "
                        "Accessibility > add Voice Typer"
                    )

    # --- internal helpers ---------------------------------------------------

    def _start_fallback(self, callback, listener, key, key_code) -> None:
        target = _parse_hotkey_to_pynput(self.hotkey_str, key, key_code)
        if target is None:
            raise RuntimeError(f"Cannot parse hotkey {self.hotkey_str!r} for fallback")

        # NEW-DEAD-030: for composite hotkeys (tuple), extract BOTH the
        # modifier keys and the target key.  Previously the fallback
        # listener only matched on the target key, ignoring modifiers —
        # so ``<ctrl>+<f2>`` would fire on bare ``<f2>``.  We now track
        # which modifier keys are currently held (via the pynput
        # on_press/on_release events) and require ALL of them to be held
        # before firing the callback.
        if isinstance(target, tuple):
            modifier_keys, match_key = target
        else:
            modifier_keys, match_key = (), target

        # Track currently-held modifier keys so we can check the full
        # composite state before firing.
        held_modifiers = set()
        # UX-001: track whether the matched key is currently held down
        # so we can fire the on_release callback exactly once per
        # press-release cycle (pynput fires repeated on_press events
        # while a key is held).
        held = {"value": False}

        def on_press(key):
            # Track modifier presses.
            if modifier_keys and key in modifier_keys:
                held_modifiers.add(key)
            if key == match_key:
                # NEW-DEAD-030: only fire if ALL modifiers are held.
                if modifier_keys and len(held_modifiers) < len(modifier_keys):
                    return
                if not held["value"]:
                    held["value"] = True
                    # Push-to-talk starts recording on press; toggle mode
                    # with toggle_on_keyup defers to release. Otherwise
                    # (legacy toggle, e.g. repaste) fire on press.
                    if self._on_release_callback is not None:
                        log.info(
                            "[HOTKEY FALLBACK] Matched key: %s (PTT press)",
                            key,
                        )
                        callback()
                    elif getattr(self, "_toggle_on_keyup", False):
                        # Defer to release so holding the key never
                        # starts-then-stops recording.
                        pass
                    else:
                        log.info(
                            "[HOTKEY FALLBACK] Matched key: %s (mods=%d/%d)",
                            key,
                            len(held_modifiers),
                            len(modifier_keys),
                        )
                        callback()

        def on_release(key):
            # NEW-DEAD-030: track modifier releases so the held_modifiers
            # set stays accurate.
            if modifier_keys and key in modifier_keys:
                held_modifiers.discard(key)
            # UX-001: invoke the on_release callback (used by
            # push-to-talk mode) when the matched key is released, or
            # fire the toggle on release when toggle_on_keyup is set.
            # The check ``held["value"]`` ensures we only fire on the
            # transition from held -> released, not on every spurious
            # release event pynput may emit.
            if key == match_key and held["value"]:
                held["value"] = False
                if self._on_release_callback is not None:
                    log.info("[HOTKEY FALLBACK] Key released (PTT): %s", key)
                    try:
                        self._on_release_callback()
                    except Exception:
                        log.exception("[HOTKEY FALLBACK] on_release callback raised")
                elif getattr(self, "_toggle_on_keyup", False):
                    log.info("[HOTKEY FALLBACK] Key released (toggle): %s", key)
                    try:
                        callback()
                    except Exception:
                        log.exception("[HOTKEY FALLBACK] toggle callback raised")

        self._listener = listener(on_press=on_press, on_release=on_release)
        self._listener.start()
        # PERF-NEW-017: was 0.5s — reduced to 50ms for the same reason.
        time.sleep(0.05)
        self._fallback = True
        log.info(
            "[HOTKEY] Fallback listener started, watching for %s (alive=%s)",
            match_key,
            self._listener.is_alive(),
        )

    def _stop_listener(self) -> None:
        if self._listener is not None:
            with contextlib.suppress(Exception):
                self._listener.stop()
            self._listener = None

    # --- public interface ---------------------------------------------------

    def stop(self) -> None:
        if self._listener is not None:
            log.info("[HOTKEY] Stopping pynput hotkey listener")
            self._stop_listener()

    def is_alive(self) -> bool:
        return self._listener is not None and self._listener.is_alive()

    def diagnose(self) -> str:
        if self._listener is None:
            return "PynputHotkey: no listener registered"
        alive = self._listener.is_alive()
        daemon = getattr(self._listener, "daemon", "?")
        name = getattr(self._listener, "name", "?")
        mode = "fallback" if self._fallback else "GlobalHotKeys"
        return (
            f"PynputHotkey ({mode})\n"
            f"Hotkey: {self.hotkey_str}\n"
            f"Thread name: {name}\n"
            f"Thread alive: {alive}\n"
            f"Thread daemon: {daemon}"
        )


def _parse_hotkey_to_pynput(hotkey_str, key, key_code):
    """Parse '<f2>' or '<ctrl>+1' -> pynput key/KeyCode for fallback matching.

    Handles composite hotkeys with modifiers (ctrl, alt, shift, cmd/win).
    Returns a tuple of (modifier_keys, target_key) for composite hotkeys,
    or a single key/KeyCode for simple hotkeys.

    RW-1 (Hotkey parser unification): this now delegates to the
    canonical :func:`voice_typer.server.hotkey_spec.parse_hotkey` for
    tokenisation and alias resolution. The pynput-specific concerns
    that remain in this function are:

    - Modifier ``key`` collapsing: canonical ``win`` / ``super`` /
      ``cmd`` all map to ``key.cmd`` (pynput does not distinguish —
      it has ``key.cmd`` / ``key.cmd_l`` / ``key.cmd_r`` but no
      ``key.win`` or ``key.super``).
    - ``key`` / ``KeyCode`` conversion: pynput's ``key`` enum (for
      named keys like ``f2``, ``space``, ``enter``) and
      ``key_code.from_char`` / ``from_vk`` (for letters, digits, and
      function keys not in the enum).
    """
    from voice_typer.server.hotkey_spec import parse_hotkey

    def _to_pynput_key(name: str):
        """Convert a canonical key name to a pynput key/KeyCode, or None."""
        if hasattr(key, name):
            return getattr(key, name)
        if name.startswith("f") and name[1:].isdigit():
            fnum = int(name[1:])
            if 1 <= fnum <= 24:
                return key_code.from_vk(0x6F + fnum)
        if len(name) == 1:
            return key_code.from_char(name)
        return None

    def _to_pynput_modifier(name: str):
        """Convert a canonical modifier name to a pynput key, or None.

        pynput collapses win/super/cmd → key.cmd (no key.win or
        key.super exists). alt_gr maps to key.alt_gr when available
        (platform-dependent), otherwise key.alt_r, otherwise None.
        fn maps to key.fn when available (macOS only), otherwise None.
        """
        _canonical_to_pynput = {
            "ctrl": "ctrl",
            "shift": "shift",
            "alt": "alt",
            # pynput collapses win/super → cmd (no key.win / key.super).
            "cmd": "cmd",
            "win": "cmd",
            "super": "cmd",
        }
        attr = _canonical_to_pynput.get(name)
        if attr is not None and hasattr(key, attr):
            return getattr(key, attr)
        # alt_gr / fn: try the canonical name, fall back to None.
        if name == "alt_gr":
            for fallback in ("alt_gr", "alt_r"):
                if hasattr(key, fallback):
                    return getattr(key, fallback)
        if name == "fn" and hasattr(key, "fn"):
            return key.fn
        return None

    parsed = parse_hotkey(hotkey_str)
    if parsed.is_empty:
        return None

    # Single-modifier special case (preserves the prior behaviour where
    # a 1-part spec like ``<alt>`` returns ``key.alt`` directly rather
    # than ``(modifiers, target)``). For multi-modifier specs with no
    # main key (e.g. ``<ctrl>+<shift>``), pynput cannot match without a
    # target key — return None, matching the previous behaviour.
    if not parsed.keys:
        if len(parsed.modifiers) == 1:
            mod_key = _to_pynput_modifier(parsed.modifiers[0])
            return mod_key  # may be None if pynput lacks the attribute
        return None

    target = _to_pynput_key(parsed.keys[0])
    if target is None:
        return None

    modifier_keys = []
    for mod in parsed.modifiers:
        mod_key = _to_pynput_modifier(mod)
        if mod_key is not None:
            modifier_keys.append(mod_key)

    if modifier_keys:
        return (tuple(modifier_keys), target)
    return target
