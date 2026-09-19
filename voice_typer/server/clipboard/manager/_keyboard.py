"""KeyboardMixin, keyboard-controller lifecycle helpers."""

from __future__ import annotations

import contextlib

from voice_typer.server import clipboard as _cb


class KeyboardMixin:
    """Keyboard-controller lifecycle mixin for :class:`ClipboardManager`."""

    def _release_stuck_modifiers(self) -> None:
        """Release any stuck modifier keys before paste."""
        # pynput is optional, _Key / _Controller stay None in
        if _cb._Key is None or self._keyboard is None:
            return
        try:
            for key in (_cb._Key.ctrl, _cb._Key.shift, _cb._Key.alt, _cb._Key.cmd):
                with contextlib.suppress(Exception):
                    self._keyboard.release(key)
        except Exception:
            # was silent ``pass``. The protected block is a pynput
            _cb.log.debug(
                "[CLIPBOARD] _release_stuck_modifiers pynput loop failed",
                exc_info=True,
            )

    def _safe_key_press(self, modifier, char) -> None:
        """PLAT-STUCK: Press modifier + char with guaranteed modifier release."""
        # pynput may be unavailable (headless / sandboxed).
        if self._keyboard is None:
            _cb.log.debug("[CLIPBOARD] _safe_key_press skipped, no keyboard controller")
            return
        try:
            self._keyboard.press(modifier)
            self._keyboard.press(char)
            self._keyboard.release(char)
        finally:
            self._keyboard.release(modifier)
