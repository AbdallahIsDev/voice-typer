"""Native hotkey backend, {name}."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from typing import Any

from voice_typer.server.native_hotkeys.modifiers import (
    _canonical_modifier,
    _canonical_modifier_name_for_token,
)

log = logging.getLogger(__name__)


class _MatchingMixin:
    # Members provided by the composed ``SubprocessHotkeyBackend``
    platform_name: str
    _match_lock: threading.Lock
    _held_modifiers: set[str]
    _fn_down: bool
    _main_key_down: bool
    _parsed: dict[str, Any] | None
    _extra_matchers: list[dict[str, Any]]
    _on_release_callback: Callable[[], None] | None

    def _on_fn_event(self, payload: str = "", *, down: bool) -> None:
        """Handle FN_DOWN / FN_UP. Used by the macOS backend only."""
        with self._match_lock:
            self._fn_down = down
        self._try_match(down)

    def _on_modifier_event(self, mod_name: str, *, down: bool) -> None:
        """Handle MOD_DOWN / MOD_UP events."""
        canonical = _canonical_modifier(mod_name)
        if canonical is None:
            return
        with self._match_lock:
            if down:
                # auto-repeat filter: if the modifier is
                if canonical in self._held_modifiers:
                    return
                self._held_modifiers.add(canonical)
            else:
                self._held_modifiers.discard(canonical)
        # For modifier-only hotkeys (e.g. <alt> alone), the modifier
        if self._parsed and self._parsed["is_modifier_only"]:
            self._try_match(down)

    def _on_key_event(self, key_name: str, *, down: bool) -> None:
        """Handle KEY_DOWN / KEY_UP events."""
        with self._match_lock:
            if down:
                # auto-repeat filter, if the main key is
                if self._main_key_down:
                    return
                self._main_key_down = True
            else:
                self._main_key_down = False
        self._try_match(down, key_name=key_name)

    def _try_match(self, down: bool, *, key_name: str | None = None) -> None:
        """Check if the current event matches any registered hotkey spec."""
        # Primary matcher (role "dictation"). Uses the legacy
        primary = {
            "role": "dictation",
            "parsed": self._parsed,
            "callback": getattr(self, "_callback", None),
            "on_release_callback": self._on_release_callback,
            "toggle_on_keyup": getattr(self, "_toggle_on_keyup", False),
        }
        if self._try_match_one(primary, down, key_name=key_name):
            return
        # Extra matchers (roles "esc", "repaste", etc.). Each is
        for matcher in self._extra_matchers:
            if self._try_match_one(matcher, down, key_name=key_name):
                return

    def _try_match_one(
        self,
        matcher: dict[str, Any],
        down: bool,
        *,
        key_name: str | None = None,
    ) -> bool:
        """Try a single matcher against the current event.

        Returns True if the matcher matched (and fired its callback or
        """
        parsed = matcher["parsed"]
        if parsed is None:
            return False

        # FN-only hotkey
        if parsed["is_fn_only"]:
            if down:
                self._fire_callback_for(matcher)
            else:
                self._fire_on_release_for(matcher)
            return True

        # Modifier-only hotkey (e.g. <alt>, or <ctrl>+<alt>)
        if parsed["is_modifier_only"]:
            required = parsed["modifiers"]
            if "fn" in required:
                # Already handled by FN_DOWN/FN_UP above
                return False
            # Convert spec tokens to canonical modifier names
            required_canonical = set()
            for token in required:
                c = _canonical_modifier_name_for_token(token)
                if c is not None:
                    required_canonical.add(c)
            if not required_canonical:
                return False
            with self._match_lock:
                held = set(self._held_modifiers)
            # The hotkey is "these exact modifiers and no others"
            if held != required_canonical:
                return False
            if down:
                self._fire_callback_for(matcher)
            else:
                self._fire_on_release_for(matcher)
            return True

        # Regular hotkey (single key or combo)
        main_key = parsed["main_key"]
        if key_name != main_key:
            return False

        required_mods = parsed["modifiers"]
        with self._match_lock:
            held_mods = set(self._held_modifiers)
            # For FN-containing combos, add 'fn' to held_mods if FN is down
            if self._fn_down:
                held_mods.add("fn")

        # All required modifiers must be held
        if not required_mods.issubset(held_mods):
            return False

        # No extra modifiers should be held (unless they're required)
        extra = held_mods - required_mods
        if extra:
            return False

        toggle_on_keyup = bool(matcher.get("toggle_on_keyup", False))
        on_release = matcher.get("on_release_callback")
        if down:
            if on_release is not None:
                # Push-to-talk: start recording on press.
                self._fire_callback_for(matcher)
            elif toggle_on_keyup:
                # Toggle mode with toggle_on_keyup: defer the toggle to
                pass
            else:
                # Legacy toggle (e.g. ESC, repaste): fire on press.
                self._fire_callback_for(matcher)
        else:
            if on_release is not None:
                # Push-to-talk: stop recording on release.
                self._fire_on_release_for(matcher)
            elif toggle_on_keyup:
                # Toggle mode: fire the toggle exactly once on key-up.
                self._fire_callback_for(matcher)
            # else: legacy toggle-on-keydown -> nothing to do on key-up.
        return True

    def _fire_callback(self) -> None:
        """Invoke the press callback (with exception shielding)."""
        primary = {
            "role": "dictation",
            "callback": getattr(self, "_callback", None),
        }
        self._fire_callback_for(primary)

    def _fire_callback_for(self, matcher: dict[str, Any]) -> None:
        """Invoke ``matcher["callback"]`` with exception shielding."""
        cb = matcher.get("callback")
        if cb is None:
            return
        role = matcher.get("role", "dictation")
        try:
            cb()
        except Exception:
            log.exception(
                "[NATIVE-HOTKEY] Press callback raised in %s backend (role=%s)",
                self.platform_name,
                role,
            )

    def _fire_on_release(self) -> None:
        """Invoke the release callback (push-to-talk mode)."""
        primary = {
            "role": "dictation",
            "on_release_callback": self._on_release_callback,
        }
        self._fire_on_release_for(primary)

    def _fire_on_release_for(self, matcher: dict[str, Any]) -> None:
        """Invoke ``matcher["on_release_callback"]`` with exception
        shielding. Shared by the primary and extra matchers."""
        cb = matcher.get("on_release_callback")
        if cb is None:
            return
        role = matcher.get("role", "dictation")
        try:
            cb()
        except Exception:
            log.exception(
                "[NATIVE-HOTKEY] Release callback raised in %s backend (role=%s)",
                self.platform_name,
                role,
            )
