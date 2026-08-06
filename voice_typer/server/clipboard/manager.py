"""ClipboardManager orchestrator (slim delegator after clipboard split).

Extracted from the original ``clipboard/manager.py`` monolith (1417 LOC)
into three focused modules:

* :mod:`.restore` — ``_pending_restores`` registry,
  ``_pending_restores_lock``, ``_MAX_PENDING_RESTORES``,
  ``_force_restore_pending_at_exit`` atexit handler, and the
  implementations of :meth:`ClipboardManager._delayed_restore` /
  :meth:`ClipboardManager.restore_now`.
* :mod:`.safety` — implementations of
  :meth:`ClipboardManager._is_safe_paste_target` /
  :meth:`ClipboardManager._is_terminal_process` /
  :meth:`ClipboardManager._detect_focused_process` /
  :meth:`ClipboardManager._get_frontmost_pid_macos`.
* :mod:`.manager` (this file) — slim :class:`ClipboardManager` with
  ``__init__``, ``refresh_config``, ``copy()``, ``paste()``, and thin
  delegator methods that forward to :mod:`.restore` / :mod:`.safety`.

Contains:

* :class:`ClipboardCopyError` — distinguishes "copy failed" from
  "save/restore disabled" (ADR-0010 §5.2).
* :class:`ClipboardManager` — copy/paste orchestrator with snapshot
  borrow/restore lifecycle (ADR-0010 §5.x).

Design contract: all patchable symbols (``is_windows``, ``is_macos``,
``is_linux``, ``pyperclip``, ``time``, ``log``, ``_Controller``,
``_Key``, ``_copy_to_clipboard``, ``_paste_from_clipboard``,
``_linux_paste_via_wtype``, ``_is_wayland_paste_session``,
``_have_wtype``, ``Win32Clipboard``, ``_win32_empty_clipboard``,
``_send_ctrl_v_win32``, ``_is_elevated_target``, ``_is_password_field``,
``_is_content_editable``, ``_get_uia_focused_element``) are looked up
via the PACKAGE (``_cb.X``) at call time — NOT via this module's
globals — so test patches like
``patch.object(clip_mod, "is_windows", return_value=True)`` /
``patch("voice_typer.server.clipboard._is_elevated_target", ...)``
actually take effect on the code paths in this module (and in the
extracted :mod:`.restore` / :mod:`.safety` siblings, which share the
same ``_cb`` lookup discipline).

``_pending_restores`` and ``_pending_restores_lock`` were moved to
:mod:`.restore` and are re-exported below (and by the package
``__init__``) so test code that does
``from voice_typer.server.clipboard.manager import _pending_restores``
(see ``tests/test_clipboard_restore_race.py``) and
``with clip_mod._pending_restores_lock:`` keeps working unchanged. The
re-export binds the SAME list / lock / int objects, so mutations made
through any of the three namespaces (``manager._pending_restores``,
``restore._pending_restores``, ``clip_mod._pending_restores``) are
visible through all of them.
"""

from __future__ import annotations

import contextlib
import threading
from typing import Any

from voice_typer.server import clipboard as _cb
from voice_typer.server.clipboard_snapshot import ClipboardSnapshot

# import the single canonical credential-dialog class set
# so this module and ``clipboard_target_safety.py`` can't drift.
# ``#32770`` (generic Win32 Dialog class — ) is NOT in the
# unified set; legitimate dictation into Open/Save As / Properties
# dialogs is governed by the UIA ``IsPassword`` check instead.
# import canonical default for the restore-delay literal that
# was previously duplicated as `150` in three places in this module.
# Aliased to a private name to make the provenance obvious at call
# sites without bloating the import line.
from voice_typer.server.config import DEFAULT_CLIPBOARD_RESTORE_DELAY_MS as _DEFAULT_RESTORE_DELAY_MS

# the per-submodule `log = logging.getLogger(...)` definition that
# used to live here was removed because it was unused — every log call in
# this module routes through `_cb.log` (the package-level logger imported
# above as `_cb`). Defining a separate `log` here would shadow the
# package logger and risk future contributors adding `log.info(...)`
# calls that bypass the `_cb.log` patch surface used by tests
# (`tests/test_clipboard.py` patches `voice_typer.server.clipboard.log`).
# The `import logging` was also removed (no remaining references).
# ─── clipboard split (pass a): pending-restores registry re-export ────────────
#
# The module-level ``_pending_restores`` list, ``_pending_restores_lock``,
# ``_MAX_PENDING_RESTORES`` cap, and ``_force_restore_pending_at_exit``
# atexit handler were extracted to :mod:`.restore`. They are re-exported
# here so legacy call sites and tests that import them from
# ``voice_typer.server.clipboard.manager`` (e.g.
# ``tests/test_clipboard_restore_race.py``,
# ``tests/test_clipboard_pending_restores_cap.py``) keep working
# unchanged. The re-export binds the SAME list / lock / int / function
# objects — mutations made through ``manager._pending_restores`` are
# visible through ``restore._pending_restores`` and vice versa.
from .restore import (  # noqa: E402,F401
    _MAX_PENDING_RESTORES,
    _delayed_restore_impl,
    _force_restore_pending_at_exit,
    _pending_restores,
    _pending_restores_lock,
    _restore_now_impl,
)

# ─── clipboard split (pass b): paste-target safety impls re-export ────────────
#
# The implementations of the four safety staticmethods were extracted
# to :mod:`.safety`. The staticmethods on :class:`ClipboardManager`
# below are thin delegators that forward to these module-level impl
# functions, preserving the ``patch.object(ClipboardManager,
# "_is_safe_paste_target", ...)`` patch surface used by ~12 tests.
from .safety import (  # noqa: E402,F401
    _detect_focused_process_impl,
    _get_frontmost_pid_macos_impl,
    _is_safe_paste_target_impl,
    _is_terminal_process_impl,
)


class ClipboardCopyError(RuntimeError):
    """Raised when ``ClipboardManager.copy()`` fails to write text to the
    clipboard after retries.

    ADR-0010 §5.2: distinguishes "copy failed" (caller should write to
    crash recovery) from "save/restore disabled" (caller skips the
    clipboard). The snapshot, if captured, is restored before raising
    so the clipboard is never left torn.
    """


class ClipboardManager:
    """Handles copying text to clipboard and pasting into the focused app."""

    # rate limit for paste operations. The PROBLEMS
    # invariant says "500ms"; the code previously said 1.0s. We
    # align to 500ms (0.5s) which is the documented invariant —
    # fast enough for rapid dictation but slow enough to prevent
    # accidental double-paste from a stuck hotkey.
    _PASTE_RATE_LIMIT = 0.5

    def __init__(self, paste_enabled: bool = True):
        self.paste_enabled = paste_enabled
        # Lazy-import pynput so the module can load headless. The actual
        # Controller() instantiation still requires a display (will raise
        # at instance construction time, NOT at module import time).
        _cb._ensure_pynput_imported()
        self._keyboard = _cb._Controller() if _cb._Controller is not None else None
        self._last_paste_time: float = 0.0
        # PLAT-CLIPRACE: clipboard sequence number for race detection on Windows
        self._clipboard_seq: int = 0
        # PLAT-SECURE: last copied text for seq-mismatch recovery in paste().
        # ADR-0010 §5.1: kept (not removed) because paste() still re-copies
        # on clipboard sequence number mismatch.
        self._last_copied_text: str = ""
        # ADR-0010 §5.6 / DP8: removed ``_clear_thread`` and
        # ``_saved_clipboard`` — dead code replaced by the snapshot
        # return value of copy() and the daemon-thread restore in paste().
        # PLAT-SECURE (revised): cached config flag for clipboard_save_restore.
        # Initialized to True (default) and refreshed via refresh_config()
        # when the user changes the setting. Used to gate snapshot capture
        # in copy() (DP7 — the flag is now actually consulted).
        self._clipboard_save_restore_enabled: bool = True
        # ADR-0010 §5.6 / §5.3: cached restore delay in milliseconds. Read
        # by paste() when scheduling the daemon-thread restore. Refreshed
        # at runtime via refresh_config().
        self._restore_delay_ms: int = _DEFAULT_RESTORE_DELAY_MS

    def refresh_config(self, config) -> None:
        """Refresh cached config flags from a Config object.

        ADR-0010 §5.5: called from ``service.apply_config()`` after
        ``config.save()`` (inside the config-mutation lock). Keeps the
        live ClipboardManager in sync with runtime config changes —
        including the ``paste_enabled`` ↔ ``paste_on_stop`` mirror
        (§2.12), which is otherwise stale until restart.

        Without the ``paste_on_stop`` mirror, toggling auto-paste in the
        UI leaves ``paste_enabled`` stale and auto-paste / repaste
        silently no-op until restart (§2.12).
        """
        try:
            self._clipboard_save_restore_enabled = bool(getattr(config, "clipboard_save_restore", True))
        except Exception:
            _cb.log.warning(
                "[CLIPBOARD] refresh_config: clipboard_save_restore lookup failed — using safe default",
                exc_info=True,
            )
            self._clipboard_save_restore_enabled = True  # safe default

        try:
            self._restore_delay_ms = int(getattr(config, "clipboard_restore_delay_ms", _DEFAULT_RESTORE_DELAY_MS))
        except Exception:
            _cb.log.warning(
                "[CLIPBOARD] refresh_config: clipboard_restore_delay_ms lookup failed — using default",
                exc_info=True,
            )
            self._restore_delay_ms = _DEFAULT_RESTORE_DELAY_MS

        # §2.12: mirror paste_on_stop → paste_enabled so a runtime toggle
        # of auto-paste actually takes effect. Without this, paste()'s
        # internal gate stays stale and auto-paste (and repaste) silently
        # no-op until restart.
        try:
            self.paste_enabled = bool(getattr(config, "paste_on_stop", True))
        except Exception:
            _cb.log.warning(
                "[CLIPBOARD] refresh_config: paste_on_stop lookup failed — using safe default",
                exc_info=True,
            )
            self.paste_enabled = True

        _cb.log.debug(
            "[CLIPBOARD] refresh_config: save_restore=%s, restore_delay=%dms, paste_enabled=%s",
            self._clipboard_save_restore_enabled,
            self._restore_delay_ms,
            self.paste_enabled,
        )

    @staticmethod
    def _get_clipboard_sequence_number() -> int:
        """Get the Windows clipboard sequence number.

        PLAT-CLIPRACE: used to detect if another app modified the
        clipboard between our copy and paste.
        delegates to Win32Clipboard.get_sequence_number().
        """
        return _cb.Win32Clipboard.get_sequence_number()

    @staticmethod
    def _is_safe_paste_target() -> bool:
        """Check that the foreground window is safe for pasting.

        Thin delegator (clipboard split pass b): forwards to
        :func:`.safety._is_safe_paste_target_impl`. The implementation
        (UAC / credential-prompt / elevated-target / password-field
        blocking, COM init hoisting, fail-closed vs fail-open routing)
        lives in :mod:`.safety`. Kept as a ``@staticmethod`` on the
        class so ``patch.object(ClipboardManager, "_is_safe_paste_target",
        ...)`` (used by ~12 tests) keeps working unchanged.
        """
        return _is_safe_paste_target_impl()

    @staticmethod
    def _is_terminal_process(process_name: str | None) -> bool:
        """Thin delegator (clipboard split pass b) → :func:`.safety._is_terminal_process_impl`."""
        return _is_terminal_process_impl(process_name)

    @staticmethod
    def _detect_focused_process() -> str | None:
        """Thin delegator (clipboard split pass b) → :func:`.safety._detect_focused_process_impl`."""
        return _detect_focused_process_impl()

    @staticmethod
    def _get_frontmost_pid_macos() -> int | None:
        """Thin delegator (clipboard split pass b) → :func:`.safety._get_frontmost_pid_macos_impl`."""
        return _get_frontmost_pid_macos_impl()


    def copy(self, text: str) -> ClipboardSnapshot | None:
        """Copy text to the clipboard. Returns a snapshot of the prior content.

        ADR-0010 §5.2 / DP4 / DP7.

        Returns ``None`` if:
          * ``text`` is empty (no-op).
          * ``clipboard_save_restore`` is disabled (no snapshot captured).

        Returns a :class:`ClipboardSnapshot` (which may have an empty
        ``items`` list if the clipboard was empty) when snapshot capture
        succeeds. The caller is responsible for restoring the snapshot
        after the text has been consumed — see :meth:`paste` and
        :meth:`restore_now`.

        Raises :class:`ClipboardCopyError` if the text copy/verify fails
        after retries. The snapshot, if captured, is restored before
        raising so the clipboard is never left torn. The caller should
        write the transcription to crash recovery on this exception.
        """
        if not text:
            return None

        # ① SNAPSHOT (gated by config flag — DP7). The snapshot is a
        # value returned to the caller; it is NOT stored on self. This
        # makes overlapping cycles safe (DP4).
        snapshot: ClipboardSnapshot | None = None
        if self._clipboard_save_restore_enabled:
            snapshot = ClipboardSnapshot.capture()
            # snapshot may be None if capture failed (clipboard locked
            # or empty). That's OK — we just won't restore. Log for
            # debugging.
            if snapshot is None:
                _cb.log.debug("[CLIPBOARD] Snapshot capture returned None (clipboard locked or empty)")

        try:
            # ② WIN32 EMPTY (existing ). On Windows, empty the
            # clipboard before copying to clear rich text artifacts from
            # the previous clipboard content. : uses
            # Win32Clipboard abstraction instead of direct ctypes calls.
            _cb._win32_empty_clipboard()

            # ③ COPY TEXT (existing  retry on ERROR_ACCESS_DENIED).
            # ADR-0020 §6.6: on Linux Wayland, route through _copy_to_clipboard
            # which uses `wl-copy` instead of pyperclip's xclip/xsel (which
            # are X11-only and silently no-op under native Wayland apps).
            for attempt in range(3):
                try:
                    _cb._copy_to_clipboard(text)
                    break
                except OSError as copy_err:
                    # ERROR_ACCESS_DENIED = 5 on Windows. pyperclip wraps
                    # the underlying win32 clipboard error as OSError or
                    # pywintypes.error (which is a subclass of OSError).
                    winerror = getattr(copy_err, "winerror", None)
                    if winerror == 5 and attempt < 2:
                        _cb.time.sleep(0.05 * (attempt + 1))
                        continue
                    raise copy_err

            # ④ VERIFY (existing PLAT-PASTEVR).
            for verify_attempt in range(3):
                try:
                    actual = _cb._paste_from_clipboard()
                    if actual == text:
                        break
                    _cb.log.warning(
                        "[CLIPBOARD] Clipboard verification failed (attempt %d/3) — expected %d chars, got %d.",
                        verify_attempt + 1,
                        len(text),
                        len(actual) if actual else 0,
                    )
                    _cb._copy_to_clipboard(text)
                except (ImportError, AttributeError, NotImplementedError, OSError):
                    # narrowed from bare ``except Exception: pass``.
                    # ``_paste_from_clipboard`` may raise ImportError if
                    # pyperclip is missing, AttributeError / NotImplemented
                    # if the platform backend lacks paste support, or
                    # OSError on a Win32 / wl-paste failure. All are
                    # non-fatal — verification is best-effort.
                    _cb.log.debug(
                        "[CLIPBOARD] verify attempt %d failed",
                        verify_attempt,
                        exc_info=True,
                    )
            else:
                _cb.log.error("[CLIPBOARD] Clipboard verification still failed after 3 retries")

            # ④b (Privacy): tag the clipboard with the Windows
            # monitor-exclusion format
            # (``ExcludeClipboardContentFromMonitorProcessing``) so the
            # dictated text is NOT added to the Windows clipboard
            # history (Win+V), NOT synced via Cloud Clipboard, and NOT
            # indexed by conforming third-party clipboard monitors
            # (Microsoft PowerToys' Clipboard Manager, MDM-managed
            # clipboard providers). The helper is best-effort — a
            # failure leaves the dictated text in the clipboard history
            # (the pre-fix behavior), which is the safe degraded mode.
            # The tag is set AFTER the verify loop (which may re-copy
            # and re-empty the clipboard via ``pyperclip.copy``) so the
            # tag is the LAST format set and survives the copy() return.
            if _cb.is_windows():
                with contextlib.suppress(Exception):
                    _cb._win32_exclude_clipboard_from_monitoring()

            # ⑤ STORE METADATA (existing PLAT-CLIPRACE / PLAT-SECURE).
            #
            #  (session-DE, Medium, Privacy): only cache
            # ``_last_copied_text`` when a snapshot was captured (i.e.
            # a restore IS scheduled, whose ``_delayed_restore`` finally
            # block will clear it after the restore-delay window).
            # When ``snapshot is None`` (clipboard_save_restore disabled
            # OR capture failed), no restore will be scheduled, so
            # caching the dictated text here would leak PII (which can
            # be passwords, messages, financial data — anything the
            # user dictated) into process memory for the entire process
            # lifetime. The seq-mismatch re-copy path in ``paste()``
            # threads ``pasted_text`` as a request-scoped value
            # parameter (), so it does not depend on the instance
            # attribute when ``snapshot is None``. The Wayland paste
            # call sites also thread ``pasted_text`` (). Defensive
            # clear of any stale value from a prior cycle.
            if snapshot is not None:
                self._last_copied_text = text
            else:
                self._last_copied_text = ""
            self._clipboard_seq = self._get_clipboard_sequence_number()
            _cb.log.info(
                "[CLIPBOARD-AUDIT] Copied %d chars to clipboard (seq=%d, snapshot=%s)",
                len(text),
                self._clipboard_seq,
                "captured" if snapshot is not None else "none",
            )
            return snapshot

        except Exception as e:
            _cb.log.error("[CLIPBOARD] Failed to copy to clipboard: %s", e)
            # If copy failed, restore the snapshot immediately so we don't
            # leave the clipboard in a torn state, then signal failure.
            if snapshot is not None:
                with contextlib.suppress(Exception):
                    snapshot.restore()
            raise ClipboardCopyError(str(e)) from e

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

    def paste(
        self,
        snapshot: ClipboardSnapshot | None = None,
        restore_delay: float | None = None,
        pasted_text: str | None = None,
        force: bool = False,
        pasted_seq: int | None = None,
    ) -> bool:
        """Send a paste keystroke into the focused window.

        ADR-0010 §5.3 / DP1 / DP2 / DP3 / DP4.

        If a snapshot is provided, a delayed restore is ALWAYS scheduled
        on a daemon thread (DP3) — even when the keystroke is later
        skipped (pynput missing, rate-limited, paste disabled, unsafe
        target). This guarantees the clipboard borrow is always paired
        with a restore (DP1/DP2), so the user's original clipboard is
        never orphaned.

        ``force=True`` bypasses the ``paste_enabled`` gate. Used by
        ``repaste_last()`` — a manual user action that must never be
        coupled to the auto-paste (``paste_on_stop``) setting. See §2.12.

        The snapshot and the expected pasted text are passed as value
        parameters — no instance state is read or written for the
        snapshot or the restore guard (DP4). This makes overlapping
        cycles safe: cycle B's copy() cannot corrupt cycle A's restore,
        because every cycle carries its own expected text. The
        transcription thread is never blocked by the restore (it runs on
        a daemon thread).

        Returns ``True`` if a keystroke was sent, ``False`` if paste is
        disabled, rate-limited, or blocked by the safety check.
        """
        # ── schedule restore FIRST, before any early return (DP1/DP2) ──
        # The borrow happened in copy(); failure to send the keystroke
        # must not prevent the paired restore. ``_delayed_restore``
        # re-checks the clipboard before restoring, so this is safe even
        # if the paste never lands.
        #
        # Register the pending restore in the module-level _pending_restores
        # list so the atexit handler can force-restore it if the app exits
        # before the daemon thread fires. The daemon thread removes its
        # entry on normal completion. (History: )
        _pending_entry: tuple[Any, Any, str, float] | None = None
        if snapshot is not None:
            delay = restore_delay if restore_delay is not None else (self._restore_delay_ms / 1000.0)
            expected = pasted_text if pasted_text is not None else self._last_copied_text
            _pending_entry = (self, snapshot, expected, delay)
            with _pending_restores_lock:
                # Hard cap on the in-flight pending-restores list.
                # If we're at the cap, force-restore the OLDEST entry's
                # snapshot synchronously (under the lock — atomic w.r.t.
                # other appends) BEFORE appending the new entry. This
                # bounds peak RSS: without the cap, a runaway condition
                # (user-set 60 s restore delay, daemon-thread leak,
                # OpenClipboard hang) pins N × ~16 MB × N formats of
                # clipboard snapshots in Python heap until atexit. The
                # oldest entry is the one closest to having been
                # restored anyway (its daemon thread is the one most
                # likely already mid-sleep or stuck), so evicting it
                # minimises disruption. The force-restore path is the
                # same ``snapshot.restore()`` call used by the atexit
                # handler — the snapshot is restored to the clipboard,
                # clobbering whatever the most recent paste() put there.
                # That's the correct behaviour: the cap exists to
                # prevent unbounded memory growth, and the user's
                # original clipboard content (the snapshot) is more
                # valuable than the dictated text that would have been
                # restored a few hundred ms later.
                if len(_pending_restores) >= _MAX_PENDING_RESTORES:
                    oldest_entry = _pending_restores.pop(0)
                    _oldest_cm, oldest_snapshot, _oldest_text, _oldest_delay = oldest_entry
                    try:
                        oldest_snapshot.restore()
                        _cb.log.warning(
                            "[CLIPBOARD] _pending_restores cap hit (%d) — "
                            "synchronously restored oldest snapshot to prevent "
                            "unbounded growth",
                            _MAX_PENDING_RESTORES,
                        )
                    except Exception:
                        _cb.log.exception(
                            "[CLIPBOARD] force-restore of oldest pending entry "
                            "failed (cap=%d) — leaving clipboard state as-is",
                            _MAX_PENDING_RESTORES,
                        )
                _pending_restores.append(_pending_entry)
            # wrap Thread().start() in try/except — if start() fails
            # (out of thread resources / fd exhaustion), remove the orphaned
            # entry from _pending_restores so it doesn't hold the snapshot
            # (potentially large image/file clipboard content) and dictated
            # text for the process lifetime. Log a WARNING. Do NOT call
            # snapshot.restore_now() — if thread start failed, the system is
            # resource-starved and synchronous restore might also fail.
            try:
                threading.Thread(
                    target=self._delayed_restore,
                    args=(snapshot, expected, delay, _pending_entry),
                    daemon=True,
                    name="clipboard-restore",
                ).start()
            except (OSError, RuntimeError) as exc:
                _cb.log.warning(
                    "[CLIPBOARD] failed to start clipboard-restore thread: %s — "
                    "removing orphaned _pending_restores entry to prevent leak",
                    exc,
                )
                with _pending_restores_lock, contextlib.suppress(ValueError):
                    _pending_restores.remove(_pending_entry)  # already removed by another path

        # pynput is optional. If both _Key and _Controller are
        # unavailable AND we're not on Windows (where _send_ctrl_v_win32
        # uses SendInput directly without pynput), there's no way to
        # synthesize a paste keystroke. Bail out early rather than
        # AttributeError on _Key.cmd / _Key.ctrl below.
        #
        # on Linux Wayland, we route through `wtype` (no pynput
        # needed), so don't bail out even if pynput is missing — let the
        # Wayland branch below handle it.
        if _cb._Controller is None and not _cb.is_windows():
            if _cb.is_linux() and _cb._is_wayland_paste_session() and _cb._have_wtype():
                _cb.log.debug("[CLIPBOARD] pynput unavailable — will use wtype on Wayland")
            else:
                _cb.log.warning("[CLIPBOARD] pynput unavailable — cannot paste")
                return False

        # Rate-limit check moved BEFORE seq-mismatch re-copy.
        # Previously, a rate-limited paste would still trigger a
        # re-copy of the clipboard (via pyperclip.copy) even though no
        # keystroke would be sent — wasting the re-copy work and
        # potentially racing with a concurrent paste cycle. Now we
        # short-circuit on rate-limit before doing any clipboard work.
        now = _cb.time.monotonic()
        if now - self._last_paste_time < self._PASTE_RATE_LIMIT:
            _cb.log.info(
                "[CLIPBOARD] Paste rate-limited (%.0f ms since last paste)",
                (now - self._last_paste_time) * 1000,
            )
            return False

        # §2.12: ``force`` bypasses the paste_enabled gate so repaste
        # (a manual user action) works regardless of the auto-paste
        # (paste_on_stop) setting.
        if not self.paste_enabled and not force:
            _cb.log.info("[CLIPBOARD] Paste disabled by config -- skipping keystroke")
            return False

        # PLAT-CLIPRACE (revised): verify clipboard wasn't modified between
        # copy and paste. The previous code only LOGGED a warning when the
        # clipboard sequence number changed between copy() and paste() —
        # paste proceeded anyway with potentially stale clipboard content.
        #
        # Now we actually RECOVER: if the seq changed, re-copy the
        # text before pasting. This handles the case where another app
        # (clipboard manager, password manager, screenshot tool) overwrote
        # the clipboard between our copy() and the paste keystroke.
        if _cb.is_windows() and hasattr(self, "_clipboard_seq"):
            # CRIT-3: use the request-scoped seq when provided (set by
            # copy() for THIS request), falling back to the instance
            # attribute for repaste/callers that don't thread it. This
            # prevents a concurrent copy() in another request from
            # clobbering the seq we validate against.
            expected_seq = pasted_seq if pasted_seq is not None else self._clipboard_seq
            if expected_seq:
                current_seq = self._get_clipboard_sequence_number()
                if current_seq != expected_seq:
                    _cb.log.warning(
                        "[CLIPBOARD] Clipboard modified between copy and paste (seq %d -> %d) — re-copying",
                        expected_seq,
                        current_seq,
                    )
                    # Re-copy the text we want to paste.
                    #
                    #  (session-DE, Medium, Concurrency): thread the
                    # request-scoped ``pasted_text`` parameter through
                    # this re-copy path instead of reading the shared
                    # mutable ``self._last_copied_text`` instance
                    # attribute. A concurrent cycle (e.g. repaste_last()
                    # triggered by hotkey during a dictation cycle) can
                    # run ``copy(text_B)`` between this cycle's
                    # ``copy(text_A)`` and ``paste()``, overwriting
                    # ``self._last_copied_text`` with ``text_B``. Reading
                    # the instance attribute here would then re-copy
                    # ``text_B`` — wrong text pasted while the daemon's
                    # ``expected=text_A`` no longer matches, triggering
                    # an unwanted restore that clobbers ``text_B``. The
                    # threaded parameter is the value THIS cycle copied;
                    # it is safe. We fall back to the instance attribute
                    # only if ``pasted_text`` is None (legacy callers
                    # that don't thread it).
                    #
                    #  (Medium, Wayland): use _copy_to_clipboard()
                    # instead of pyperclip.copy() so the Wayland dispatcher
                    # routes through `wl-copy` on Linux Wayland sessions.
                    # Previously, paste()'s seq-mismatch re-copy bypassed
                    # the dispatcher and called pyperclip.copy directly —
                    # which uses xclip/xsel (X11-only) and silently no-ops
                    # under native Wayland apps, leaving the re-copy stale.
                    try:
                        recopy_text = pasted_text if pasted_text is not None else self._last_copied_text
                        if recopy_text:
                            _cb._copy_to_clipboard(recopy_text)
                            # Brief delay to let the clipboard settle
                            _cb.time.sleep(0.02)
                            # Update seq so a subsequent mismatch check is accurate
                            self._clipboard_seq = self._get_clipboard_sequence_number()
                    except Exception as exc:
                        _cb.log.error(
                            "[CLIPBOARD] Failed to re-copy after seq mismatch: %s — paste may deliver stale content",
                            exc,
                        )

        # PLAT-RDP: increase paste delay in RDP sessions where clipboard
        # sync is slower.
        #
        #  (Low, Perf): drop the unconditional 20ms ``paste_delay``
        # sleep. The 20ms was applied to every paste on every platform,
        # adding latency without benefit (the seq-mismatch re-copy path
        # already has its own 20ms settle delay when it fires). The 100ms
        # RDP sleep is preserved because RDP clipboard sync genuinely
        # needs the extra delay.
        paste_delay = 0.0
        if _cb.is_windows():
            try:
                from voice_typer.server.server_platform import is_remote_session

                if is_remote_session():
                    paste_delay = 0.10
                    _cb.log.info(
                        "[CLIPBOARD] RDP session detected — increasing paste delay to %dms", int(paste_delay * 1000)
                    )
            except Exception:
                # was silent ``pass``. The protected block is a
                # lazy import + platform predicate call which can raise
                # ImportError (server_platform not importable in some
                # test/headless envs) or any error from the underlying
                # Win32/POSIX session probe. Keep the broad catch so a
                # flaky probe never blocks paste, but log at DEBUG.
                _cb.log.debug(
                    "[CLIPBOARD] is_remote_session probe failed; using default paste delay",
                    exc_info=True,
                )

        # PLAT-STUCK: release any stuck modifier keys before pasting.
        # Gated to run AFTER the rate-limit / paste_enabled / pynput-
        # availability early-return paths so a rate-limited or disabled
        # paste cycle does NOT pay the 4 pynput ``.release()`` round-trips
        # (Ctrl / Shift / Alt / Cmd). The release is idempotent and
        # harmless when no modifier is stuck, so running it on every
        # paste-eligible cycle is safe.
        self._release_stuck_modifiers()

        if not self._is_safe_paste_target():
            _cb.log.info("[CLIPBOARD] Paste blocked — security-sensitive window in foreground")
            return False

        # IME composition guard. On Windows, if the foreground window
        # has the IME open with an active composition string (e.g. the
        # user is mid-typing a CJK character), sending Ctrl+V /
        # Shift+Insert now would either (a) commit the incomplete
        # composition string before pasting (corrupting the user's
        # input) or (b) be silently dropped by the IME (the paste
        # keystroke is consumed by the IME's key handler, never
        # reaching the target app). We pick "skip and surface a toast"
        # over "defer ~200 ms and re-check" because deferring would
        # block the transcription thread for 200 ms on every paste into
        # an IME-active window (a measurable latency regression for CJK
        # dictation).
        #
        # The toast is published via ``event_bus`` (the same channel
        # used by ``paste_failed`` in the dictation pipeline) so the
        # renderer can surface a sonner toast. The event is wrapped
        # in try/except so a broken event bus never aborts the
        # paste-skip path.
        if _cb.is_windows():
            try:
                from voice_typer.server.hotkeys.windows.ime_guard import is_ime_composing

                if is_ime_composing():
                    _cb.log.info(
                        "[CLIPBOARD] Paste deferred — IME composition in progress"
                    )
                    try:
                        from voice_typer.server import event_bus

                        event_bus.publish(
                            {
                                "type": "paste_deferred",
                                "data": {
                                    "reason": "ime_composition",
                                    "message": "Paste deferred — IME composition in progress",
                                },
                            }
                        )
                    except Exception:
                        _cb.log.debug(
                            "[CLIPBOARD] could not publish paste_deferred event",
                            exc_info=True,
                        )
                    return False
            except Exception:
                # The lazy import / call can fail in headless / test
                # environments where ``ctypes.windll.imm32`` is not
                # present or has been patched to a MagicMock. Fail open
                # — the safety check above already validated the target.
                _cb.log.debug(
                    "[CLIPBOARD] is_ime_composing() probe failed — failing open",
                    exc_info=True,
                )

        try:
            if paste_delay > 0:
                _cb.time.sleep(paste_delay)

            # CRIT-2: re-validate the target RIGHT BEFORE sending the
            # keystroke. The safety check above (line ~1563) ran before the
            # paste_delay sleep; the foreground window could have changed
            # during that window (TOCTOU). If the target is now unsafe
            # (e.g. focus moved to a credential prompt), abort — do NOT
            # send the paste into the wrong/unsafe window.
            #
            # only re-check when we actually slept. When
            # ``paste_delay == 0`` the check above ran immediately before
            # this branch, so a second UIA round-trip here would be
            # redundant (no time for the foreground window to change).
            if paste_delay > 0 and not self._is_safe_paste_target():
                _cb.log.info("[CLIPBOARD] Paste blocked — foreground target became unsafe during paste delay")
                return False

            #  (session-4): capture the foreground window handle
            # immediately after the safety check so we can re-verify
            # right before sending the Win32 paste keystroke. The check
            # above (``_is_safe_paste_target``) already validated the
            # target, but a TOCTOU window remains between that check
            # and the ``_send_ctrl_v_win32`` call below: the user could
            # Alt+Tab to a credential prompt in the ~5ms between the
            # check and the SendInput call. We re-fetch
            # ``GetForegroundWindow`` here and compare to the value
            # captured right after the safety check; if they differ,
            # abort paste to avoid sending Ctrl+V into the wrong window.
            #
            # extend the TOCTOU re-check to macOS by
            # capturing the frontmost application's PID via
            # ``NSWorkspace.sharedWorkspace().frontmostApplication()
            # .processIdentifier()``. If the PID differs between the
            # safety check and the keystroke send, abort. Linux Wayland
            # has no equivalent atomic probe (wtype does not return the
            # focused surface); the residual risk is documented in the
            # Linux branch below.
            safe_hwnd: int = 0
            safe_macos_pid: int | None = None
            if _cb.is_windows():
                try:
                    import ctypes as _ctypes_mod

                    safe_hwnd = _ctypes_mod.windll.user32.GetForegroundWindow()
                except Exception:
                    safe_hwnd = 0
            elif _cb.is_macos():
                safe_macos_pid = self._get_frontmost_pid_macos()

            process_name = self._detect_focused_process()
            is_terminal = self._is_terminal_process(process_name)

            # PLAT-CONTENT: log when the paste target appears to be a rich editor
            if process_name and process_name.lower() in _cb._RICH_EDITOR_PROCESS_NAMES:
                _cb.log.info(
                    "[CLIPBOARD] Paste target appears to be a rich editor (%s) — "
                    "pasting plain text (contentEditable detection not implemented)",
                    process_name,
                )

            # on Linux Wayland, pynput silently no-ops (it's
            # X11-only). Route through `wtype` instead — uses Ctrl+V
            # (the clipboard was already populated by copy()). Falls
            # through to the pynput path on X11, when wtype isn't
            # installed, or on non-Linux platforms.
            #
            # ``_linux_paste_via_wtype`` now always uses the Ctrl+V
            # clipboard path (no more ``-d 50`` keystroke delay for
            # short text). (History: )
            use_wayland_wtype = _cb.is_linux() and _cb._is_wayland_paste_session() and _cb._have_wtype()
            paste_succeeded = True
            #  (session-DE): thread ``pasted_text`` (request-scoped
            # value parameter) through the Wayland paste call sites too,
            # instead of reading the shared mutable
            # ``self._last_copied_text`` instance attribute.
            # ``_linux_paste_via_wtype`` currently ignores its ``text``
            # argument (), but if  is ever reverted, the
            # instance attribute would become a leak/race vector (same
            # rationale as the seq-mismatch re-copy path above).
            wtype_text = pasted_text if pasted_text is not None else self._last_copied_text
            if is_terminal:
                if _cb.is_macos():
                    # TOCTOU re-check on macOS. Re-fetch the
                    # frontmost app PID and compare to ``safe_macos_pid``
                    # captured above. If the user Cmd-Tabbed (or a
                    # credential prompt stole focus) between the safety
                    # check and the keystroke send, abort. If the PID
                    # can't be re-fetched (pyobjc unavailable), fail
                    # open — the safety check above already validated
                    # the target.
                    if safe_macos_pid is not None:
                        current_pid = self._get_frontmost_pid_macos()
                        if current_pid is not None and current_pid != safe_macos_pid:
                            _cb.log.warning(
                                "[CLIPBOARD] Frontmost macOS app changed during paste "
                                "(TOCTOU: pid %d -> %d) — aborting paste to avoid "
                                "sending Cmd+V into the wrong window (XZ-CLIP-04)",
                                safe_macos_pid,
                                current_pid,
                            )
                            return False
                    self._safe_key_press(_cb._Key.cmd, "v")
                elif use_wayland_wtype:
                    # Linux Wayland residual risk — wtype
                    # does not return the focused surface, so we cannot
                    # re-verify the target atomically. The safety check
                    # above ran immediately before this call (paste_delay
                    # is 0 on Linux), so the TOCTOU window is bounded to
                    # the wtype fork+exec time (~5-10ms). A user who
                    # Alt-Tabs in that window may receive the paste into
                    # the wrong target; documented as residual risk.
                    #
                    # Pass ``is_terminal=True`` so wtype sends
                    # ``ctrl+shift+v`` for terminal targets. Terminals
                    # bind paste to Ctrl+Shift+V; a plain Ctrl+V is
                    # interpreted as the literal ``^V`` / 0x16 control
                    # byte and corrupts shell input.
                    _cb._linux_paste_via_wtype(wtype_text, is_terminal=True)
                elif _cb.is_windows():
                    # Windows terminal paste uses Shift+Insert (terminal
                    # emulators bind paste to Shift+Insert; Ctrl+V is
                    # either unmapped or interpreted as the literal
                    # ``^V`` control byte). When pynput
                    # (``self._keyboard``) is unavailable, route through
                    # the Win32 SendInput helper so terminal paste does
                    # not silently no-op on headless / sandboxed hosts.
                    # When pynput IS available, use the existing
                    # ``_safe_key_press`` path (which keeps the existing
                    # UX and avoids a double-paste risk if both paths
                    # fire).
                    #
                    # TOCTOU re-check mirrors the non-terminal Windows
                    # branch below — re-fetch the foreground window and
                    # compare to ``safe_hwnd`` captured above; abort if
                    # the user Alt+Tabbed (or a credential prompt stole
                    # focus) between the safety check and the keystroke.
                    if safe_hwnd:
                        try:
                            import ctypes as _ctypes_mod

                            current_hwnd = _ctypes_mod.windll.user32.GetForegroundWindow()
                        except Exception:
                            current_hwnd = safe_hwnd  # fail open
                        if current_hwnd != safe_hwnd:
                            _cb.log.warning(
                                "[CLIPBOARD] Foreground window changed during paste "
                                "(TOCTOU: hwnd %d -> %d) — aborting paste to avoid "
                                "sending Shift+Insert into the wrong window",
                                safe_hwnd,
                                current_hwnd,
                            )
                            return False
                    if self._keyboard is None:
                        # pynput unavailable — use the Win32 SendInput
                        # helper. The bool return is honored so a
                        # partial-success SendInput (1..3 events) does
                        # not get logged as a successful paste.
                        paste_succeeded = self._send_shift_insert_win32()
                    else:
                        self._safe_key_press(_cb._Key.shift, _cb._Key.insert)
                else:
                    self._safe_key_press(_cb._Key.shift, _cb._Key.insert)
            elif _cb.is_macos():
                # same TOCTOU re-check as the terminal branch above.
                if safe_macos_pid is not None:
                    current_pid = self._get_frontmost_pid_macos()
                    if current_pid is not None and current_pid != safe_macos_pid:
                        _cb.log.warning(
                            "[CLIPBOARD] Frontmost macOS app changed during paste "
                            "(TOCTOU: pid %d -> %d) — aborting paste to avoid "
                            "sending Cmd+V into the wrong window (XZ-CLIP-04)",
                            safe_macos_pid,
                            current_pid,
                        )
                        return False
                self._safe_key_press(_cb._Key.cmd, "v")
            elif _cb.is_windows():
                #  (session-4): TOCTOU re-check. Re-fetch the
                # foreground window and compare to ``safe_hwnd`` captured
                # above. If the user Alt+Tabbed (or a credential prompt
                # stole focus) between the safety check and the SendInput
                # call, abort paste to avoid sending Ctrl+V into the
                # wrong/unsafe window. If we can't re-fetch (ctypes
                # itself broken), fail open — the safety check above
                # already validated the target.
                if safe_hwnd:
                    try:
                        import ctypes as _ctypes_mod

                        current_hwnd = _ctypes_mod.windll.user32.GetForegroundWindow()
                    except Exception:
                        current_hwnd = safe_hwnd  # fail open
                    if current_hwnd != safe_hwnd:
                        _cb.log.warning(
                            "[CLIPBOARD] Foreground window changed during paste "
                            "(TOCTOU: hwnd %d -> %d) — aborting paste to avoid "
                            "sending Ctrl+V into the wrong window",
                            safe_hwnd,
                            current_hwnd,
                        )
                        return False
                # Check the return value of _send_ctrl_v_win32.
                # A False return means SendInput reported partial success
                # (1..3 of 4 events delivered) — the paste did NOT
                # complete cleanly. Previously this was silently dropped
                # (the user saw nothing happen and no warning). Now we
                # log a warning and return False so the user knows the
                # paste failed.
                paste_succeeded = self._send_ctrl_v_win32()
            elif use_wayland_wtype:
                _cb._linux_paste_via_wtype(wtype_text)
            else:
                self._safe_key_press(_cb._Key.ctrl, "v")

            if not paste_succeeded:
                _cb.log.warning("[CLIPBOARD] Auto-paste failed (SendInput partial success — UIPI may have blocked)")
                return False

            self._last_paste_time = _cb.time.monotonic()
            _cb.log.info(
                "[CLIPBOARD-AUDIT] Sent paste keystroke (terminal=%s, target=%s, restore_scheduled=%s)",
                is_terminal,
                process_name or "unknown",
                snapshot is not None,
            )
            return True
        except Exception as e:
            _cb.log.warning("[CLIPBOARD] Auto-paste failed (clipboard still has the text): %s", e)
            return False
    def _delayed_restore(
        self,
        snapshot: ClipboardSnapshot,
        pasted_text: str,
        delay: float,
        pending_entry: Any = None,
    ) -> None:
        """Restore a snapshot after a delay. Runs on a daemon thread.

        Thin delegator (clipboard split pass a): forwards to
        :func:`.restore._delayed_restore_impl`. The implementation
        (claim-under-lock before restore, defensive clipboard re-check,
        ``_last_copied_text`` privacy clear in ``finally``, atexit-race
        short-circuit) lives in :mod:`.restore`. Kept as an instance
        method on the class with the EXACT 4-arg + ``pending_entry=None``
        signature pinned by ``tests/test_clipboard_restore_args.py`` and
        ``tests/test_clipboard_borrow_restore.py`` (which call
        ``inspect.signature(ClipboardManager._delayed_restore)``).
        """
        _delayed_restore_impl(self, snapshot, pasted_text, delay, pending_entry)

    def restore_now(self, snapshot: ClipboardSnapshot | None) -> None:
        """Restore a snapshot immediately (no paste keystroke, no delay).

        Thin delegator (clipboard split pass a): forwards to
        :func:`.restore._restore_now_impl`. The implementation
        (snapshot.restore + ``_last_copied_text`` privacy clear in
        ``finally``) lives in :mod:`.restore`. Kept as an instance
        method on the class so the public API and the
        ``cm.restore_now(snap)`` call sites in tests keep working.
        """
        _restore_now_impl(self, snapshot)


    def _send_ctrl_v_win32(self) -> bool:
        """Send Ctrl+V via a single atomic SendInput batch.

        On Windows, we always prefer SendInput over
        pynput.keyboard.Controller because pynput's Controller is
        blocked by UIPI when targeting elevated processes from a
        non-elevated one.  Our direct SendInput call is subject to the
        same UIPI restriction, but we log the failure explicitly
        instead of silently dropping it.

        Returns ``True`` if the full Ctrl+V sequence was delivered
        (SendInput returned 4) OR the pynput fallback was invoked
        (best-effort — assumed success since pynput raises on failure).
        Returns ``False`` on partial success (SendInput returned 1..3)
        so the caller can surface a warning without risking a
        double-paste.
        """
        # Delegate to the package-level _send_ctrl_v_win32 helper
        # (defined in .windows). The pynput fallback (used when
        # SendInput returns 0 — total failure) is bound here because
        # it needs self._safe_key_press + _Key.ctrl (instance + package
        # state).
        return _cb._send_ctrl_v_win32(fallback=lambda: self._safe_key_press(_cb._Key.ctrl, "v"))

    def _send_shift_insert_win32(self) -> bool:
        """Send Shift+Insert via a single atomic SendInput batch.

        Terminal-emulator paste equivalent of :meth:`_send_ctrl_v_win32`.
        Terminal emulators (Windows Terminal, conhost, cmd.exe, pwsh.exe)
        bind paste to Shift+Insert; Ctrl+V is either unmapped (legacy
        conhost) or interpreted as the literal ``^V`` control byte.

        Returns ``True`` on full success (4 events) or when the
        pynput fallback is invoked (best-effort). Returns ``False`` on
        partial success (1..3 events) so the caller can surface a
        warning without risking a double-paste.
        """
        # Delegate to the package-level _send_shift_insert_win32 helper
        # (defined in .windows). The pynput fallback (used when
        # SendInput returns 0 — total failure) is bound here because it
        # needs self._safe_key_press + _Key.shift + _Key.insert
        # (instance + package state).
        return _cb._send_shift_insert_win32(
            fallback=lambda: self._safe_key_press(_cb._Key.shift, _cb._Key.insert)
        )

__all__ = [
    "ClipboardCopyError",
    "ClipboardManager",
    "_MAX_PENDING_RESTORES",
    "_force_restore_pending_at_exit",
    "_pending_restores",
    "_pending_restores_lock",
]
