"""ClipboardManager orchestrator + atexit registry (PVT-23 split).

Extracted from the original ``clipboard.py`` monolith. Contains:

* :class:`ClipboardCopyError` — distinguishes "copy failed" from
  "save/restore disabled" (ADR-0010 §5.2).
* :class:`ClipboardManager` — copy/paste orchestrator with snapshot
  borrow/restore lifecycle (ADR-0010 §5.x).
* :data:`_pending_restores` / :data:`_pending_restores_lock` — module-
  level registry of pending delayed-restores (CLIP-8).
* :func:`_force_restore_pending_at_exit` — atexit handler that
  force-restores any pending snapshots if the app exits during the
  restore-delay window.

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
actually take effect on the code paths in this module.

``_pending_restores`` and ``_pending_restores_lock`` live HERE (in
``manager.py``'s module namespace) and are re-exported by the package
so test code that does ``with clip_mod._pending_restores_lock:`` works.
"""

from __future__ import annotations

import atexit
import contextlib
import logging
import threading
from typing import Any

from voice_typer.server import clipboard as _cb
from voice_typer.server.clipboard_snapshot import ClipboardSnapshot

log = logging.getLogger("voice_typer.server.clipboard")


# ─── CLIP-8: module-level registry of pending delayed-restores ────────
#
# When paste() schedules a daemon-thread restore, it also appends an
# entry to this list. The daemon thread removes its entry on normal
# completion. If the app exits while a delayed restore is still
# pending (e.g. user quits during the 150ms restore-delay window),
# the atexit handler walks this list and force-restores each snapshot
# synchronously — preventing the user's original clipboard content
# from being lost forever.
#
# Each entry is a tuple of (ClipboardManager, ClipboardSnapshot,
# pasted_text, delay). The lock guards the list because the daemon
# thread and atexit handler may run concurrently.
_pending_restores: list[tuple[Any, Any, str, float]] = []
_pending_restores_lock = threading.Lock()


def _force_restore_pending_at_exit() -> None:
    """CLIP-8: atexit handler — force-restore any pending snapshots.

    Walks the module-level ``_pending_restores`` list and synchronously
    restores each snapshot. This prevents data loss when the app exits
    while a delayed restore is still pending (e.g. user quits during
    the 150ms restore delay window).

    The handler is best-effort: per-snapshot failures are logged but
    do not abort the loop (we restore as many as we can).
    """
    with _pending_restores_lock:
        items = list(_pending_restores)
        _pending_restores.clear()
    for _cm, snapshot, pasted_text, delay in items:
        try:
            # Try to read the clipboard to decide whether to restore.
            # If we can't read it, restore anyway (data-loss prevention
            # beats false-positive restore — see CLIP-9).
            try:
                current = _cb._paste_from_clipboard()
            except Exception:
                current = None
            if current is None or current == pasted_text:
                snapshot.restore()
                _cb.log.info(
                    "[CLIPBOARD-AUDIT] Atexit: restored snapshot (delay was %.3fs)",
                    delay,
                )
            else:
                _cb.log.debug(
                    "[CLIPBOARD-AUDIT] Atexit: skip restore (clipboard changed, current=%d chars, expected=%d chars)",
                    len(current) if current else 0,
                    len(pasted_text),
                )
        except Exception:
            _cb.log.exception("[CLIPBOARD] Atexit restore failed")


# Register the atexit handler once at module import. Idempotent guard
# prevents double-registration if the module is re-imported.
_ATEXIT_REGISTERED = False
if not _ATEXIT_REGISTERED:
    try:
        atexit.register(_force_restore_pending_at_exit)
        _ATEXIT_REGISTERED = True
    except Exception:  # pragma: no cover — atexit.register only fails if interpreter is shutting down
        pass


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

    # NEW-CQ-025: rate limit for paste operations. The PROBLEMS
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
        self._restore_delay_ms: int = 150

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
            self._clipboard_save_restore_enabled = True  # safe default

        try:
            self._restore_delay_ms = int(getattr(config, "clipboard_restore_delay_ms", 150))
        except Exception:
            self._restore_delay_ms = 150

        # §2.12: mirror paste_on_stop → paste_enabled so a runtime toggle
        # of auto-paste actually takes effect. Without this, paste()'s
        # internal gate stays stale and auto-paste (and repaste) silently
        # no-op until restart.
        try:
            self.paste_enabled = bool(getattr(config, "paste_on_stop", True))
        except Exception:
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
        PLAT-027: delegates to Win32Clipboard.get_sequence_number().
        """
        return _cb.Win32Clipboard.get_sequence_number()

    @staticmethod
    def _is_safe_paste_target() -> bool:
        """Check that the foreground window is safe for pasting.

        Blocks paste into UAC dialogs, credential prompts, and
        Winlogon windows to prevent credential theft.

        CLIP-3 (Medium, Security): the outer ``except Exception`` now
        distinguishes "safety-check infrastructure is broken" from
        "target is safe." Previously any exception (including from
        ``_is_password_field`` or ``_is_elevated_target``) returned
        ``True`` (fail-open) — meaning a broken UIA install would
        silently disable credential-prompt blocking. Now:

          * Exceptions from ``_is_password_field`` or
            ``_is_elevated_target`` → log WARNING, return ``False``
            (fail-closed). We'd rather skip paste than risk pasting
            into a credential prompt with broken detection.
          * Exceptions from ``_is_content_editable`` → log DEBUG,
            continue (fail-open). contentEditable is informational
            only — not a security gate.
          * Truly outer exceptions (e.g. ``ctypes`` itself broken) →
            still fail-open. This indicates a broken Python install,
            not a security infra issue.

        CLIP-4 (Perf): the focused UIA element is fetched ONCE at the
        top via :func:`_get_uia_focused_element` and passed to both
        :func:`_is_password_field` and :func:`_is_content_editable`.
        Previously each helper fetched the focused element separately
        (2x ``GetFocusedElement`` RPCs per paste).

        CLIP-5 (Perf): ``CoInitialize`` / ``CoUninitialize`` are
        hoisted here to wrap both ``_is_password_field`` and
        ``_is_content_editable`` calls. Previously each helper did
        its own init/teardown (2x ``CoInitialize`` + 2x
        ``CoUninitialize`` per paste).

        CLIP-12 (Perf): ``GetForegroundWindow`` is fetched ONCE at
        the top and passed to ``_is_elevated_target`` and
        ``_is_password_field`` (for the cred-dialog fallback).
        Previously each helper fetched it separately.
        """
        if not _cb.is_windows():
            # G4-H-05 (session-4): dispatch to platform-native password-field
            # detection. Previously returned ``True`` unconditionally
            # on non-Windows, which allowed dictated text to be pasted
            # into password fields, SSH passphrase prompts, credit-card
            # forms, etc.
            #
            #   * macOS: Accessibility API via pyobjc.
            #   * Linux: AT-SPI2 via pyatspi.
            #
            # If the platform library is unavailable, the helper logs a
            # WARNING (once) and returns False (no password field
            # detected), preserving the legacy fail-open behavior of
            # allowing paste. See the helper docstrings for residual
            # risk notes (SIGKILL, broken AX/AT-SPI2 infrastructure).
            try:
                if _cb.is_macos():
                    if _cb._is_password_field_macos():
                        _cb.log.info("[CLIPBOARD] Paste blocked — macOS password field is focused (G4-H-05)")
                        return False
                elif _cb.is_linux():  # noqa: SIM102
                    if _cb._is_password_field_linux():
                        _cb.log.info("[CLIPBOARD] Paste blocked — Linux password field is focused (G4-H-05)")
                        return False
            except Exception:
                # Outer fail-open: if the dispatch itself raises,
                # log and allow paste. This is the legacy non-Windows
                # behavior, and we'd rather allow paste than block all
                # dictation because of a bug in the platform helper.
                _cb.log.warning(
                    "[CLIPBOARD] non-Windows password-field check raised — failing open (G4-H-05)",
                    exc_info=True,
                )
            return True
        try:
            import ctypes

            user32 = ctypes.windll.user32
            # CLIP-12: fetch hwnd ONCE and pass to all helpers.
            hwnd = user32.GetForegroundWindow()
            if not hwnd:
                return True

            # Check window class name for security-sensitive windows
            buf = ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(hwnd, buf, 256)
            class_name = buf.value

            # Block UAC/consent dialogs and credential prompts
            blocked_classes = {"#32770", "Credential Dialog Xaml Host", "CredDialog"}
            if class_name in blocked_classes:
                _cb.log.warning("[CLIPBOARD] Blocked paste into security-sensitive window (class=%s)", class_name)
                return False

            # PLAT-013 (revised): Block paste if the target is elevated
            # and we are not. The previous code called _is_elevated_target()
            # but discarded the return value — only the side-effect (a log
            # line inside the function) was observed, and the paste
            # proceeded anyway. This was unsafe: if the target is elevated
            # (e.g. an Administrator cmd window), our SendInput will be
            # silently blocked by UIPI, but we'd still try to paste —
            # potentially into the wrong window if the user switched focus
            # at the last moment.
            #
            # Now we actually USE the return value: if the target is
            # elevated and we are not, return False to abort the paste.
            # The user will see no paste and can investigate (the function
            # logs a warning explaining why).
            #
            # CLIP-3: fail-closed on exception — if the elevation check
            # itself raises, we block paste rather than risk UIPI failure.
            try:
                if _cb._is_elevated_target(hwnd):
                    _cb.log.warning(
                        "[CLIPBOARD] Target window is elevated but we are not — blocking paste to avoid UIPI failure"
                    )
                    return False
            except Exception:
                _cb.log.warning(
                    "[CLIPBOARD] _is_elevated_target check raised — blocking paste (fail-closed, CLIP-3)",
                    exc_info=True,
                )
                return False

            # CLIP-4/5: fetch the focused UIA element ONCE and hoist
            # CoInitialize/CoUninitialize to wrap both password-field
            # and content-editable checks. Falls back to the
            # credential-dialog heuristic when comtypes is unavailable.
            focused = None
            com_initialized = False
            try:
                import comtypes

                comtypes.CoInitialize()
                com_initialized = True
                focused = _cb._get_uia_focused_element()
            except ImportError:
                # comtypes unavailable — _is_password_field will fall
                # through to its ImportError branch and use the
                # window-class heuristic. Pass focused=None so the
                # helper knows to fetch it itself (which will also
                # fail safely).
                pass
            except Exception:
                # COM init failed — log and proceed. _is_password_field
                # will retry CoInitialize (idempotent on same thread).
                _cb.log.debug("[CLIPBOARD] CoInitialize failed in _is_safe_paste_target", exc_info=True)

            try:
                # PLAT-014: check if the focused element is a password field.
                # CLIP-3: fail-closed on exception — if password-field
                # detection itself raises, block paste rather than risk
                # pasting into a credential prompt.
                try:
                    if _cb._is_password_field(focused, hwnd):
                        return False
                except Exception:
                    _cb.log.warning(
                        "[CLIPBOARD] _is_password_field check raised — blocking paste (fail-closed, CLIP-3)",
                        exc_info=True,
                    )
                    return False

                # PLAT-CONTENT: check if the focused element is a
                # contentEditable element (rich editor like Word, Gmail
                # compose, etc.). We don't block paste — just log it so
                # the user knows the paste target supports rich text and
                # our plain-text paste may lose formatting.
                #
                # CLIP-3: keep fail-OPEN here — contentEditable is
                # informational, not a security gate.
                try:
                    if _cb._is_content_editable(focused):
                        _cb.log.info(
                            "[CLIPBOARD] Paste target is a contentEditable element — "
                            "pasting plain text (rich text formatting may be lost)"
                        )
                except Exception:
                    _cb.log.debug("[CLIPBOARD] contentEditable check failed", exc_info=True)
            finally:
                if com_initialized:
                    with contextlib.suppress(Exception):
                        import comtypes as _ct  # local re-import; safe inside finally

                        _ct.CoUninitialize()

            return True
        except Exception:
            # CLIP-3: outer exception — fail-open ONLY when truly
            # broken infra (e.g. ctypes itself unavailable). This is
            # rare and indicates a broken Python install rather than
            # a security infra issue. Security-check exceptions are
            # caught earlier (per-helper) and fail-closed.
            _cb.log.warning("[CLIPBOARD] _is_safe_paste_target outer exception — failing open", exc_info=True)
            return True  # Fail open — don't block paste on outer infra error

    @staticmethod
    def _is_terminal_process(process_name: str | None) -> bool:
        if not process_name:
            return False
        return process_name.lower().strip() in _cb._TERMINAL_PROCESS_NAMES

    @staticmethod
    def _detect_focused_process() -> str | None:
        """Detect the focused process name (Windows only)."""
        if not _cb.is_windows():
            return None
        try:
            import ctypes
            from ctypes import wintypes

            user32 = ctypes.windll.user32
            kernel32 = ctypes.windll.kernel32

            hwnd = user32.GetForegroundWindow()
            if not hwnd:
                return None

            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if not pid.value:
                return None

            process_query_limited_information = 0x1000
            h_process = kernel32.OpenProcess(process_query_limited_information, False, pid.value)
            if not h_process:
                return None

            try:
                size = wintypes.DWORD(512)
                buf = ctypes.create_unicode_buffer(512)
                if kernel32.QueryFullProcessImageNameW(h_process, 0, buf, ctypes.byref(size)):
                    return buf.value.rsplit("\\", 1)[-1].lower()
            finally:
                kernel32.CloseHandle(h_process)
        except (OSError, AttributeError):
            # EC-15: narrowed from bare ``except Exception: pass``. The
            # protected block is a Win32 ctypes call sequence
            # (OpenProcess / QueryFullProcessImageNameW / CloseHandle)
            # which raises ``OSError`` on Win32 failures and
            # ``AttributeError`` if a ctypes function pointer is missing.
            _cb.log.debug(
                "[CLIPBOARD] _detect_focused_process Win32 query failed",
                exc_info=True,
            )
        return None

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
            # ② WIN32 EMPTY (existing PLAT-006). On Windows, empty the
            # clipboard before copying to clear rich text artifacts from
            # the previous clipboard content. PLAT-027: uses
            # Win32Clipboard abstraction instead of direct ctypes calls.
            _cb._win32_empty_clipboard()

            # ③ COPY TEXT (existing PLAT-007 retry on ERROR_ACCESS_DENIED).
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
                except Exception:
                    pass  # pyperclip.paste() may not be supported on all platforms
            else:
                _cb.log.error("[CLIPBOARD] Clipboard verification still failed after 3 retries")

            # ⑤ STORE METADATA (existing PLAT-CLIPRACE / PLAT-SECURE).
            #
            # DE-59 (session-DE, Medium, Privacy): only cache
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
            # parameter (DE-60), so it does not depend on the instance
            # attribute when ``snapshot is None``. The Wayland paste
            # call sites also thread ``pasted_text`` (DE-60). Defensive
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
        during _send_keystroke_sequence), Ctrl/Shift/Alt/Cmd may be
        left in a pressed state. Releasing them before the next paste
        prevents stuck-modifier behavior.
        """
        # TASK-10: pynput is optional — _Key / _Controller stay None in
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
            # EC-15: was silent ``pass``. The protected block is a pynput
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
        # TASK-10: pynput may be unavailable (headless / sandboxed).
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
        # CLIP-8: register the pending restore in the module-level
        # _pending_restores list so the atexit handler can force-restore
        # it if the app exits before the daemon thread fires. The daemon
        # thread removes its entry on normal completion.
        _pending_entry: tuple[Any, Any, str, float] | None = None
        if snapshot is not None:
            delay = restore_delay if restore_delay is not None else (self._restore_delay_ms / 1000.0)
            expected = pasted_text if pasted_text is not None else self._last_copied_text
            _pending_entry = (self, snapshot, expected, delay)
            with _pending_restores_lock:
                _pending_restores.append(_pending_entry)
            # ER-72: wrap Thread().start() in try/except — if start() fails
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
                log.warning(
                    "[CLIPBOARD] failed to start clipboard-restore thread: %s — "
                    "removing orphaned _pending_restores entry to prevent leak",
                    exc,
                )
                with _pending_restores_lock:
                    try:
                        _pending_restores.remove(_pending_entry)
                    except ValueError:
                        pass  # already removed by another path

        # PLAT-STUCK: release any stuck modifier keys before pasting
        self._release_stuck_modifiers()

        # TASK-10: pynput is optional. If both _Key and _Controller are
        # unavailable AND we're not on Windows (where _send_ctrl_v_win32
        # uses SendInput directly without pynput), there's no way to
        # synthesize a paste keystroke. Bail out early rather than
        # AttributeError on _Key.cmd / _Key.ctrl below.
        #
        # XPLAT-15: on Linux Wayland, we route through `wtype` (no pynput
        # needed), so don't bail out even if pynput is missing — let the
        # Wayland branch below handle it.
        if _cb._Controller is None and not _cb.is_windows():
            if _cb.is_linux() and _cb._is_wayland_paste_session() and _cb._have_wtype():
                _cb.log.debug("[CLIPBOARD] pynput unavailable — will use wtype on Wayland")
            else:
                _cb.log.warning("[CLIPBOARD] pynput unavailable — cannot paste")
                return False

        # CLIP-13: rate-limit check moved BEFORE seq-mismatch re-copy.
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
                    # DE-60 (session-DE, Medium, Concurrency): thread the
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
                    # CLIP-7 (Medium, Wayland): use _copy_to_clipboard()
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
        # CLIP-11 (Low, Perf): drop the unconditional 20ms ``paste_delay``
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
                # EC-15: was silent ``pass``. The protected block is a
                # lazy import + platform predicate call which can raise
                # ImportError (server_platform not importable in some
                # test/headless envs) or any error from the underlying
                # Win32/POSIX session probe. Keep the broad catch so a
                # flaky probe never blocks paste, but log at DEBUG.
                _cb.log.debug(
                    "[CLIPBOARD] is_remote_session probe failed; using default paste delay",
                    exc_info=True,
                )

        if not self._is_safe_paste_target():
            _cb.log.info("[CLIPBOARD] Paste blocked — security-sensitive window in foreground")
            return False

        try:
            if paste_delay > 0:
                _cb.time.sleep(paste_delay)

            # CRIT-2: re-validate the target RIGHT BEFORE sending the
            # keystroke. The safety check above (line ~1563) ran before the
            # paste_delay sleep; the foreground window could have changed
            # during that window (TOCTOU). If the target is now unsafe
            # (e.g. focus moved to a credential prompt), abort — do NOT
            # send the paste into the wrong/unsafe window.
            if not self._is_safe_paste_target():
                _cb.log.info("[CLIPBOARD] Paste blocked — foreground target became unsafe during paste delay")
                return False

            # G4-M-25 (session-4): capture the foreground window handle
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
            # On non-Windows this is a no-op (no hwnd to compare).
            safe_hwnd: int = 0
            if _cb.is_windows():
                try:
                    import ctypes as _ctypes_mod

                    safe_hwnd = _ctypes_mod.windll.user32.GetForegroundWindow()
                except Exception:
                    safe_hwnd = 0

            process_name = self._detect_focused_process()
            is_terminal = self._is_terminal_process(process_name)

            # PLAT-CONTENT: log when the paste target appears to be a rich editor
            if process_name and process_name.lower() in _cb._RICH_EDITOR_PROCESS_NAMES:
                _cb.log.info(
                    "[CLIPBOARD] Paste target appears to be a rich editor (%s) — "
                    "pasting plain text (contentEditable detection not implemented)",
                    process_name,
                )

            # XPLAT-15: on Linux Wayland, pynput silently no-ops (it's
            # X11-only). Route through `wtype` instead — uses Ctrl+V
            # (the clipboard was already populated by copy()). Falls
            # through to the pynput path on X11, when wtype isn't
            # installed, or on non-Linux platforms.
            #
            # CLIP-10: ``_linux_paste_via_wtype`` now always uses the
            # Ctrl+V clipboard path (no more ``-d 50`` keystroke delay
            # for short text).
            use_wayland_wtype = _cb.is_linux() and _cb._is_wayland_paste_session() and _cb._have_wtype()
            paste_succeeded = True
            # DE-60 (session-DE): thread ``pasted_text`` (request-scoped
            # value parameter) through the Wayland paste call sites too,
            # instead of reading the shared mutable
            # ``self._last_copied_text`` instance attribute.
            # ``_linux_paste_via_wtype`` currently ignores its ``text``
            # argument (CLIP-10), but if CLIP-10 is ever reverted, the
            # instance attribute would become a leak/race vector (same
            # rationale as the seq-mismatch re-copy path above).
            wtype_text = pasted_text if pasted_text is not None else self._last_copied_text
            if is_terminal:
                if _cb.is_macos():
                    self._safe_key_press(_cb._Key.cmd, "v")
                elif use_wayland_wtype:
                    _cb._linux_paste_via_wtype(wtype_text)
                else:
                    self._safe_key_press(_cb._Key.shift, _cb._Key.insert)
            elif _cb.is_macos():
                self._safe_key_press(_cb._Key.cmd, "v")
            elif _cb.is_windows():
                # G4-M-25 (session-4): TOCTOU re-check. Re-fetch the
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
                            "sending Ctrl+V into the wrong window (G4-M-25)",
                            safe_hwnd,
                            current_hwnd,
                        )
                        return False
                # CLIP-14: check the return value of _send_ctrl_v_win32.
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
                _cb.log.warning(
                    "[CLIPBOARD] Auto-paste failed (SendInput partial success — UIPI may have blocked) (CLIP-14)"
                )
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

        ADR-0010 §5.3 / DP3.

        Defensive check: if the clipboard no longer contains
        ``pasted_text`` (user copied something else, or target app
        rewrote it), skip restore to avoid clobbering the new content.

        CR-3 fix: the ``pending_entry`` parameter is the tuple that
        ``paste()`` appended to ``_pending_restores`` before spawning
        this daemon thread. The entry is removed from the list (under
        the lock) BEFORE ``snapshot.restore()`` runs so the list does
        not grow unboundedly across many paste invocations (memory
        leak) and so the atexit handler does not double-restore an
        already-restored snapshot. The default ``None`` preserves
        backward compatibility with legacy 3-arg direct calls (e.g.
        existing tests at
        ``tests/test_clipboard_borrow_restore.py:301/318``).

        DE-63 fix (session-DE, Medium, Concurrency): the pre-fix code
        removed the entry in the ``finally`` block AFTER
        ``snapshot.restore()`` completed. There was a window between
        the daemon's ``snapshot.restore()`` call and the ``finally``
        block's removal during which the entry was still in the list.
        If ``_force_restore_pending_at_exit()`` (fired by atexit OR the
        SIGTERM/SIGHUP handler in ``__init__.py``) fired in that
        window, atexit would acquire the lock, copy the list (still
        containing the daemon's entry), clear the list, then iterate
        and call ``snapshot.restore()`` on the SAME snapshot the
        daemon was concurrently restoring. Two threads inside
        ``snapshot.restore()`` is unsafe on every platform (Win32
        OpenClipboard fails on the second thread, X11 races on
        selection ownership, macOS NSPasteboard is non-main-thread UB).

        Fix: claim the ``pending_entry`` under the lock BEFORE calling
        ``snapshot.restore()``. If the entry was already claimed by
        atexit (ValueError on remove), short-circuit — atexit will
        restore synchronously.
        """
        try:
            _cb.time.sleep(delay)

            # DE-63: claim the pending_entry under the lock BEFORE
            # calling snapshot.restore(). If atexit has already taken
            # the entry (cleared the list), the remove() raises
            # ValueError and we short-circuit — atexit will restore
            # synchronously. This prevents the concurrent-restore race.
            if pending_entry is not None:
                try:
                    with _pending_restores_lock:
                        try:
                            _pending_restores.remove(pending_entry)
                        except ValueError:
                            # Entry was already claimed by atexit (or
                            # another path) — atexit will restore
                            # synchronously. Bail out to avoid a
                            # concurrent snapshot.restore() call.
                            _cb.log.debug(
                                "[CLIPBOARD-AUDIT] Pending entry already claimed "
                                "by atexit — skipping daemon restore (DE-63)"
                            )
                            return  # CR-84 / DE-63: short-circuit
                except Exception:  # pragma: no cover — catastrophic lock failure
                    _cb.log.exception("[CLIPBOARD] Failed to claim pending restore entry — proceeding with restore")
                    # Continue with restore anyway (best-effort). The
                    # atexit race window is now narrowed to the
                    # catastrophic-lock-failure case.

            try:
                # ADR-0020 §6.6: on Linux Wayland, _paste_from_clipboard
                # uses `wl-paste` so the defensive check actually reads
                # the Wayland clipboard (pyperclip.paste() would no-op).
                current = _cb._paste_from_clipboard()
            except Exception:
                current = None
            if current == pasted_text:
                snapshot.restore()
                _cb.log.info(
                    "[CLIPBOARD-AUDIT] Restored snapshot after %.3fs delay",
                    delay,
                )
            else:
                _cb.log.debug(
                    "[CLIPBOARD-AUDIT] Restore skipped — clipboard changed (current=%d chars, expected=%d chars)",
                    len(current) if current else 0,
                    len(pasted_text),
                )
        except Exception:
            _cb.log.exception("[CLIPBOARD] Delayed restore failed")
        finally:
            # G4-M-24 (session-4): clear the cached last-copied text
            # after the restore completes (or skips, or raises). The
            # pasted text is no longer needed — the clipboard has been
            # restored to the user's original content (or the user
            # replaced it). Keeping ``_last_copied_text`` around for
            # the lifetime of the process was a minor privacy leak
            # (dictated text retained in process memory after the paste
            # completed) and could cause a future paste()'s
            # seq-mismatch re-copy path to re-copy stale content.
            # Clearing here bounds the retention to the paste →
            # restore-delay window (default 150 ms) instead of the
            # process lifetime.
            #
            # Best-effort: never raise from the finally block.
            #
            # DE-63: the ``pending_entry`` removal has moved to BEFORE
            # ``snapshot.restore()`` (above). The ``finally`` block
            # now only clears ``_last_copied_text``.
            try:
                self._last_copied_text = ""
            except Exception:  # pragma: no cover — attribute access broken
                _cb.log.debug("[CLIPBOARD] Failed to clear _last_copied_text", exc_info=True)

    def restore_now(self, snapshot: ClipboardSnapshot | None) -> None:
        """Restore a snapshot immediately (no paste keystroke, no delay).

        ADR-0010 §5.4 / DP2.

        Used when copy() borrowed the clipboard but no paste follows
        (``paste_on_stop = False``). This is the critical fix for the
        data-loss bug where ``paste_on_stop=False`` permanently
        destroyed the user's clipboard.
        """
        if snapshot is None:
            return
        try:
            snapshot.restore()
            _cb.log.info("[CLIPBOARD-AUDIT] Restored snapshot immediately (no paste)")
        except Exception:
            _cb.log.exception("[CLIPBOARD] Immediate restore failed")
        finally:
            # DE-59 (session-DE, Medium, Privacy): clear the cached
            # last-copied text. ``restore_now()`` is the path used when
            # ``paste_on_stop=False`` (no paste keystroke follows), so
            # no ``_delayed_restore`` daemon thread will run to clear
            # the cached text in its ``finally`` block. Without this
            # clear, the dictated PII would remain in process memory
            # for the process lifetime.
            #
            # Best-effort: never raise from the finally block.
            try:
                self._last_copied_text = ""
            except Exception:  # pragma: no cover — attribute access broken
                _cb.log.debug(
                    "[CLIPBOARD] Failed to clear _last_copied_text in restore_now",
                    exc_info=True,
                )

    def _send_keystroke_sequence(self, modifier, char) -> None:
        # PLAT-STUCK: ensure modifier is always released even on exception.
        # Uses a robust try/finally pattern that presses modifier + char,
        # releases in reverse order, and guarantees ALL keys are released
        # in the finally block even if an intermediate release raises.
        # TASK-10: guard None keyboard — pynput is optional.
        if self._keyboard is None:
            _cb.log.debug("[CLIPBOARD] _send_keystroke_sequence skipped — no keyboard controller")
            return
        try:
            self._keyboard.press(modifier)
            self._keyboard.press(char)
            self._keyboard.release(char)
            self._keyboard.release(modifier)
        finally:
            # Double-release: guarantee modifier is freed even if the
            # normal release path above was skipped by an exception.
            for key in (modifier, char):
                with contextlib.suppress(Exception):
                    self._keyboard.release(key)

    def _send_ctrl_v_win32(self) -> bool:
        """Send Ctrl+V via a single atomic SendInput batch.

        PLAT-001: On Windows, we always prefer SendInput over
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


__all__ = [
    "ClipboardCopyError",
    "ClipboardManager",
    "_force_restore_pending_at_exit",
    "_pending_restores",
    "_pending_restores_lock",
]
