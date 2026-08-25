"""KeyboardMixin — keyboard-controller lifecycle helpers.

Split verbatim out of the pre-split ``clipboard/manager.py`` module.
Holds the stuck-modifier release and the guaranteed modifier-release
key press used by every paste dispatch path.
"""

from __future__ import annotations

import contextlib

from voice_typer.server import clipboard as _cb


class KeyboardMixin:
    """Keyboard-controller lifecycle mixin for :class:`ClipboardManager`."""

    def _release_stuck_modifiers(self) -> None:
        """Release any stuck modifier keys before paste.

        PLAT-STUCK: if a previous paste was interrupted (e.g. exception
        during _safe_key_press), Ctrl/Shift/Alt/Cmd may be
        left in a pressed state. Releasing them before the next paste
        prevents stuck-modifier behavior.
        """
        # pynput is optional — _Key / _Controller stay None in
        # headless environments, and self._keyboard is None whenever
        # _Controller is unavailable. Guard both before touching the
        # keyboard controller.
        if _cb._Key is None or self._keyboard is None:
            return
        try:
            for key in (_cb._Key.ctrl, _cb._Key.shift, _cb._Key.alt, _cb._Key.cmd):
                with contextlib.suppress(Exception):
                    self._keyboard.release(key)
        except Exception:
            # was silent ``pass``. The protected block is a pynput
            # keyboard.release loop; pynput can raise a variety of
            # exceptions (OSError, RuntimeError, AttributeError on a
            # half-initialised controller) so we keep the broad catch
            # but log at DEBUG for forensic value.
            _cb.log.debug(
                "[CLIPBOARD] _release_stuck_modifiers pynput loop failed",
                exc_info=True,
            )

    def _safe_key_press(self, modifier, char) -> None:
        """PLAT-STUCK: Press modifier + char with guaranteed modifier release.

        Wraps the modifier press/release in try/finally to ensure the
        modifier key is always released even if the character press or
        release raises an exception.
        """
        # pynput may be unavailable (headless / sandboxed).
        # Without this guard, .press() / .release() would raise
        # AttributeError on the None controller, defeating the
        # try/finally cleanup below.
        if self._keyboard is None:
            _cb.log.debug("[CLIPBOARD] _safe_key_press skipped — no keyboard controller")
            return
        try:
            self._keyboard.press(modifier)
            self._keyboard.press(char)
            self._keyboard.release(char)
        finally:
            self._keyboard.release(modifier)
