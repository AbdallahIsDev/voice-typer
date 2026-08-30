"""Native hotkey backend — {name}."""

from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger(__name__)


from voice_typer.server.native_hotkeys.modifiers import (
    _canonical_modifier,
    _canonical_modifier_name_for_token,
)


class _MatchingMixin:
    def _on_fn_event(self, payload: str = "", *, down: bool) -> None:
        """Handle FN_DOWN / FN_UP. Used by the macOS backend only.

        ``payload`` is accepted for dispatch-table uniformity
        but ignored — ``FN_DOWN`` / ``FN_UP`` are exact-match events
        with no payload (the prefix IS the line).
        """
        del payload  # unused — kept for dispatch-table signature parity
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
                # auto-repeat filter: if the modifier is
                # already tracked as held, this MOD_DOWN is an OS
                # auto-repeat (not a fresh press). Skip the add (no-op
                # for the set) AND skip the ``_try_match`` call so a
                # modifier-only hotkey doesn't re-fire on every repeat.
                if canonical in self._held_modifiers:
                    return
                self._held_modifiers.add(canonical)
            else:
                self._held_modifiers.discard(canonical)
        # For modifier-only hotkeys (e.g. <alt> alone), the modifier
        # press itself is the trigger.
        if self._parsed and self._parsed["is_modifier_only"]:
            self._try_match(down)

    def _on_key_event(self, key_name: str, *, down: bool) -> None:
        """Handle KEY_DOWN / KEY_UP events.

        auto-repeat filter: the OS auto-repeats key-down
        events while a key is held (Windows WM_KEYDOWN repeats,
        Linux evdev emits value=2 repeats, macOS NSEvent .keyDown
        repeats). Without filtering, each repeat would re-call
        ``_try_match``, re-firing the hotkey callback on every repeat
        — for a toggle-mode hotkey that means toggling on/off every
        ~30ms while the key is held. We suppress the repeat by
        checking ``self._main_key_down`` BEFORE updating state — if
        the main key is already tracked as down, this KEY_DOWN is an
        OS auto-repeat (not a fresh press) and we return early.
        The not-down → down transition (the first KEY_DOWN after a
        KEY_UP or after init) is the only one that fires ``_try_match``.

        Known limitation: ``_main_key_down`` is a single boolean
        shared across all keys, not a per-key set. This means a
        KEY_DOWN:A followed by a KEY_DOWN:V (without KEY_UP:A) would
        suppress the V press. In practice this never happens because
        the OS only auto-repeats the most-recent key, and the wire
        protocol doesn't emit a new KEY_DOWN for a different key
        while the previous one is still held (the user must release
        first). If this assumption ever breaks, the fix is to track
        per-key down-state in a set, not a boolean.
        """
        with self._match_lock:
            if down:
                # auto-repeat filter — if the main key is
                # already tracked as down, this KEY_DOWN is an OS
                # auto-repeat. Skip the state update (no-op anyway)
                # AND skip the ``_try_match`` call so the hotkey
                # doesn't re-fire on every repeat.
                if self._main_key_down:
                    return
                self._main_key_down = True
            else:
                self._main_key_down = False
        self._try_match(down, key_name=key_name)

    def _try_match(self, down: bool, *, key_name: str | None = None) -> None:
        """Check if the current event matches any registered hotkey spec.

        The primary spec (``self._parsed``, role "dictation") is tried
        first; extra matchers (``self._extra_matchers``) are tried in
        registration order. The first matcher whose spec matches the
        current event fires its callback and short-circuits — at most
        ONE role fires per event. This prevents double-firing when two
        specs could both match (e.g. ``<ctrl>+v`` and ``<ctrl>+<shift>+v``
        would both match a Ctrl+Shift+V press if we didn't short-circuit;
        the more-specific matcher is whichever was registered first).

        Matching rules (per spec):
        - ``<fn>`` alone: matches FN_DOWN/FN_UP events
        - ``<modifier>`` alone (e.g. ``<alt>``): matches MOD_DOWN/MOD_UP of
          that modifier, with no other modifiers held
        - ``<caps_lock>`` alone: matches KEY_DOWN/KEY_UP of CapsLock
        - ``<key>`` alone (e.g. ``<f2>``): matches KEY_DOWN/KEY_UP of that key
          with no modifiers held
        - ``<modifier>+<key>`` (e.g. ``<ctrl>+<alt>+v``): matches KEY_DOWN/
          KEY_UP of the main key when ALL modifiers are currently held
        """
        # Primary matcher (role "dictation"). Uses the legacy
        # ``self._parsed`` / ``self._callback`` / etc. slots so existing
        # single-spec tests and the ``_NativeBackendAdapter`` are
        # unaffected.
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
        # independent — if the primary already fired, we skip them.
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
        deferred it to key-up via ``toggle_on_keyup``); False if the
        matcher did not match. The caller uses the return value to
        short-circuit further matchers (at most ONE role fires per
        event).

        ``matcher`` is a dict with keys: ``role``, ``parsed``,
        ``callback``, ``on_release_callback``, ``toggle_on_keyup``.
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
        # This prevents <ctrl>+v from firing when <ctrl>+<alt>+v is held
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
                # key-up so holding the key cannot start-then-stop
                # recording. Do nothing here.
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
        """Invoke the press callback (with exception shielding).

        Backwards-compat shim for the primary (dictation) role —
        delegates to :meth:`_fire_callback_for` with a primary-role
        matcher dict so the exception-shielding and log-message logic
        is shared between the primary and extra matchers.
        """
        primary = {
            "role": "dictation",
            "callback": getattr(self, "_callback", None),
        }
        self._fire_callback_for(primary)

    def _fire_callback_for(self, matcher: dict[str, Any]) -> None:
        """Invoke ``matcher["callback"]`` with exception shielding.

        Shared by the primary matcher (via :meth:`_fire_callback`) and
        the extra matchers. Logs the role so operators can attribute
        the callback invocation in the unified log.
        """
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
        """Invoke the release callback (push-to-talk mode).

        Backwards-compat shim for the primary (dictation) role —
        delegates to :meth:`_fire_on_release_for`.
        """
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
