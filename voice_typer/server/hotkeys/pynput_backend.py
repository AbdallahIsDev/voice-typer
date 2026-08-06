"""Pynput-based hotkey backend (cross-platform fallback).

Split out from the original ``hotkeys.py`` god-file in Phase 4.5.
"""

import contextlib
import threading
import time
from collections.abc import Callable

from voice_typer.server import hotkeys as _hotkeys_pkg
from voice_typer.server.branding import APP_NAME

from .base import HotkeyBackend, log


#  patch-target: tests do ``patch("voice_typer.server.hotkeys.is_macos")``
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

    (liveness watchdog): pynput's listener thread can die
    silently — on Linux/X11 it can be killed by an X server restart,
    on macOS the Accessibility permission can be revoked at runtime,
    and on Windows the message pump can hang. The user sees "hotkey
    stopped working" with no error message. A dedicated watchdog
    thread polls ``self._listener.is_alive()`` every 30s; on death it
    attempts to restart the listener using the same
    GlobalHotKeys → fallback Listener chain. After 5 consecutive
    failures it surfaces a tray notification (via the optional
    ``_tray`` attribute, set by ``HotkeyDispatcher``) telling the user
    to restart Voice Typer.
    """

    # watchdog poll interval. 30s is short enough that a dead
    # listener is caught within a user-noticeable window, but long
    # enough that the watchdog itself adds negligible CPU (one
    # ``is_alive()`` check per 30s = ~0.0001% CPU).
    _WATCHDOG_POLL_INTERVAL_SECONDS: float = 30.0

    # max consecutive restart failures before surfacing a tray
    # notification. 5 attempts × 30s = ~2.5min of retrying before the
    # user is told to restart — long enough to ride out transient X
    # server restarts / window manager reloads, short enough that the
    # user isn't left hanging if pynput is genuinely broken.
    _WATCHDOG_MAX_FAILURES: int = 5

    def __init__(self, hotkey_str: str):
        super().__init__(hotkey_str)
        self._listener = None
        # initialize the fallback flag here so that ``diagnose()``
        # and other accessors don't raise ``AttributeError`` if a caller
        # inspects the backend before ``_start_fallback()`` has run (which
        # is the normal path when ``GlobalHotKeys`` succeeds). The value
        # is flipped to True inside ``_start_fallback()`` exactly as
        # before, so the runtime semantics are unchanged.
        self._fallback: bool = False
        # the redundant redeclarations of
        # ``self._on_release_callback`` and ``self._toggle_on_keyup``
        # that used to live here have been deleted —
        # ``super().__init__(hotkey_str)`` already initializes both
        # (see ``HotkeyBackend.__init__`` in base.py). The previous
        # redeclarations were no-ops that could mask a future base-class
        # refactor.
        # the callback passed to ``start()`` is stored so the
        # watchdog can restart the listener with the SAME callback
        # (preserving the dictation/ESC/repaste wiring). Without this,
        # a restart would create a new listener with no callback →
        # the hotkey fires but nothing happens.
        self._user_callback: Callable[[], None] | None = None
        # the watchdog thread. Daemon so it doesn't block
        # process exit. Created in ``start()``, joined in ``stop()``.
        self._watchdog_thread: threading.Thread | None = None
        # stop event for the watchdog. Set in ``stop()`` so the
        # watchdog exits its poll loop promptly.
        self._watchdog_stop_event = threading.Event()
        # consecutive restart-failure counter. Reset to 0 on a
        # successful restart. When it reaches ``_WATCHDOG_MAX_FAILURES``,
        # the watchdog surfaces a tray notification and stops retrying
        # (the user must restart Voice Typer manually).
        self._watchdog_failure_count: int = 0

    def start(self, callback: Callable[[], None]) -> None:
        # store the callback so the watchdog can restart the
        # listener with the same callback. Set BEFORE ``_start_listener``
        # so a fast watchdog tick (unlikely but possible) sees it.
        self._user_callback = callback
        self._watchdog_stop_event.clear()
        self._watchdog_failure_count = 0
        self._start_listener(callback)
        # arm the watchdog AFTER the initial start so we don't
        # double-start the listener (the watchdog's first poll is 30s
        # out, by which point the initial start has either succeeded
        # or the listener is already dead and the watchdog will catch
        # it on the first poll).
        self._start_watchdog()

    def _start_listener(self, callback: Callable[[], None]) -> bool:
        """Start (or restart) the pynput listener. Returns True on success.

        Shared by :meth:`start` (initial) and the watchdog (restart).
        On success, returns True and ``self._listener`` is set to the
        new listener. On failure, returns False and ``self._listener``
        is None (the caller — usually the watchdog — handles the
        failure count).

        The GlobalHotKeys → fallback Listener chain is identical to
        the original :meth:`start` body; this refactor just extracts
        it so the watchdog can re-invoke it.
        """
        # On Linux/macOS, use pynput's event-driven Listener
        # instead of polling. The Listener receives key events from the
        # OS, so it uses zero CPU while idle and has zero latency.
        # On Windows, the WindowsNativeHotkey backend is preferred (uses
        # GetAsyncKeyState in a tight 1ms-polling loop).
        from pynput.keyboard import GlobalHotKeys, Key, KeyCode, Listener

        log.info("Registering hotkey via pynput: %r -> callback", self.hotkey_str)

        try:
            self._listener = GlobalHotKeys({self.hotkey_str: callback})
            self._listener.start()
            # was 0.5s — the listener thread reaches
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
            return True
        except Exception:
            log.exception("[HOTKEY] GlobalHotKeys failed; trying fallback Listener")

            # On macOS, pynput failure usually means the
            # Accessibility permission is missing. Show a user-friendly
            # guide so the user knows how to fix it.
            if is_macos():
                log.warning(
                    f"[HOTKEY] macOS: pynput keyboard listener failed. "
                    f"This usually means the Accessibility permission is not "
                    f"granted to {APP_NAME}. To fix:\n"
                    f"  1. Open System Preferences > Privacy & Security > "
                    f"Accessibility\n"
                    f"  2. Click the lock icon and authenticate\n"
                    f"  3. Add {APP_NAME} (or your terminal/Python) to the "
                    f"allowed apps\n"
                    f"  4. Restart {APP_NAME}\n"
                    f"Without this permission, hotkeys will not work."
                )

            try:
                self._start_fallback(callback, Listener, Key, KeyCode)
                return True
            except Exception:
                log.exception("[HOTKEY] Fallback Listener also failed")

                # also warn for the fallback path on macOS
                if is_macos():
                    log.warning(
                        f"[HOTKEY] macOS: Both GlobalHotKeys and Listener "
                        f"failed. Please grant Accessibility permissions:\n"
                        f"  System Preferences > Privacy & Security > "
                        f"Accessibility > add {APP_NAME}"
                    )
                return False

    # --- liveness watchdog -----------------------------------------

    def _start_watchdog(self) -> None:
        """Start the watchdog thread (if not already running)."""
        if self._watchdog_thread is not None and self._watchdog_thread.is_alive():
            return
        self._watchdog_thread = threading.Thread(
            target=self._watchdog_loop,
            daemon=True,
            name="PynputWatchdog",
        )
        self._watchdog_thread.start()

    def _watchdog_loop(self) -> None:
        """Poll ``self._listener.is_alive()`` every 30s; restart on death.

        After ``_WATCHDOG_MAX_FAILURES`` consecutive failures, surface
        a tray notification (via ``self._tray``, set by
        ``HotkeyDispatcher``) and stop retrying — the user must restart
        Voice Typer manually.
        """
        while not self._watchdog_stop_event.is_set():
            # Sleep in small increments so stop() can interrupt promptly.
            # Cap each increment at 0.5s so a long poll interval (30s)
            # doesn't make stop() wait the full 30s, but ALSO respect
            # short intervals (tests use 0.02s) so the poll fires on
            # schedule.
            deadline = time.monotonic() + self._WATCHDOG_POLL_INTERVAL_SECONDS
            while time.monotonic() < deadline:
                remaining = max(0.0, deadline - time.monotonic())
                wait_for = min(0.5, remaining)
                if wait_for <= 0:
                    break
                if self._watchdog_stop_event.wait(timeout=wait_for):
                    return
            # Poll the listener. ``self._listener`` may be None if
            # the initial start failed AND the fallback failed — in
            # that case the watchdog attempts a restart on each tick.
            listener = self._listener
            alive = listener is not None and bool(_safe_is_alive(listener))
            if alive:
                # Listener is healthy; reset the failure counter.
                if self._watchdog_failure_count > 0:
                    log.info(
                        "[HOTKEY] pynput listener recovered after %d failed restart(s)",
                        self._watchdog_failure_count,
                    )
                self._watchdog_failure_count = 0
                continue
            # Listener is dead (or None). Attempt restart.
            callback = self._user_callback
            if callback is None:
                # No callback yet — start() hasn't been called or
                # completed. Skip this tick; the next one will retry.
                continue
            log.warning(
                "[HOTKEY] pynput listener died (alive=%s) — attempting restart (attempt %d/%d)",
                alive,
                self._watchdog_failure_count + 1,
                self._WATCHDOG_MAX_FAILURES,
            )
            # Stop the (possibly half-dead) listener before restarting
            # so we don't leak threads.
            self._stop_listener()
            ok = self._start_listener(callback)
            if ok and _safe_is_alive(self._listener):
                self._watchdog_failure_count = 0
                log.info("[HOTKEY] pynput listener restarted successfully")
            else:
                self._watchdog_failure_count += 1
                log.warning(
                    "[HOTKEY] pynput listener restart failed (%d/%d)",
                    self._watchdog_failure_count,
                    self._WATCHDOG_MAX_FAILURES,
                )
                if self._watchdog_failure_count >= self._WATCHDOG_MAX_FAILURES:
                    self._surface_watchdog_failure_notification()
                    # Stop retrying — the user must restart manually.
                    # The watchdog thread exits; ``is_alive()`` will
                    # return False until the user restarts Voice Typer.
                    return

    def _surface_watchdog_failure_notification(self) -> None:
        """surface a tray notification after 5 consecutive restart
        failures. Uses ``self._tray`` (set by ``HotkeyDispatcher``) if
        available; otherwise logs at ERROR (the log file is the
        fallback surface)."""
        message = f"Hotkey listener died and could not be restarted — please restart {APP_NAME}."
        tray = getattr(self, "_tray", None)
        if tray is not None:
            with contextlib.suppress(Exception):
                # ``notify_safety`` bypasses the user's notification
                # toggle (this is a safety-critical message — the
                # hotkey is dead and the user needs to know).
                notify_safety = getattr(tray, "notify_safety", None)
                if callable(notify_safety):
                    notify_safety(APP_NAME, message)
                else:
                    # Fall back to ``notify`` if ``notify_safety``
                    # isn't available (older tray implementations).
                    notify = getattr(tray, "notify", None)
                    if callable(notify):
                        notify(APP_NAME, message)
        log.error("[HOTKEY] %s", message)

    # --- internal helpers ---------------------------------------------------

    def _start_fallback(self, callback, listener, key, key_code) -> None:
        target = _parse_hotkey_to_pynput(self.hotkey_str, key, key_code)
        if target is None:
            raise RuntimeError(f"Cannot parse hotkey {self.hotkey_str!r} for fallback")

        # for composite hotkeys (tuple), extract BOTH the
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
        # track whether the matched key is currently held down
        # so we can fire the on_release callback exactly once per
        # press-release cycle (pynput fires repeated on_press events
        # while a key is held).
        held = {"value": False}

        def on_press(key):
            # Track modifier presses.
            if modifier_keys and key in modifier_keys:
                held_modifiers.add(key)
            if key == match_key:
                # only fire if ALL modifiers are held.
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
            # track modifier releases so the held_modifiers
            # set stays accurate.
            if modifier_keys and key in modifier_keys:
                held_modifiers.discard(key)
            # invoke the on_release callback (used by
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
        # PERF- was 0.5s — reduced to 50ms for the same reason.
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
        # signal the watchdog to exit BEFORE stopping the
        # listener so the watchdog doesn't see the listener die and
        # attempt a restart during shutdown.
        self._watchdog_stop_event.set()
        if self._listener is not None:
            log.info("[HOTKEY] Stopping pynput hotkey listener")
            self._stop_listener()
        # Join the watchdog thread so stop() returns cleanly. The
        # watchdog checks ``_watchdog_stop_event`` every 0.5s, so a
        # 2s join is ample.
        if self._watchdog_thread is not None:
            with contextlib.suppress(Exception):
                self._watchdog_thread.join(timeout=2.0)
            self._watchdog_thread = None

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

     (Hotkey parser unification): this now delegates to the
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


def _safe_is_alive(listener) -> bool:
    """call ``listener.is_alive()`` defensively.

    pynput's ``is_alive()`` can raise (e.g. if the listener's internal
    thread object was collected). The watchdog must not crash on a
    polling failure — treat any exception as "not alive" so the
    restart path engages.
    """
    try:
        return bool(listener.is_alive())
    except Exception:
        log.debug("[HOTKEY] listener.is_alive() raised — treating as dead", exc_info=True)
        return False
