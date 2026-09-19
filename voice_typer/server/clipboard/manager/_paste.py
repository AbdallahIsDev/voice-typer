"""PasteMixin, paste orchestrator + gates + dispatch + SendInput senders."""

from __future__ import annotations

import contextlib
import threading
from typing import Any

from voice_typer.server import clipboard as _cb
from voice_typer.server.clipboard.restore import (
    _MAX_PENDING_RESTORES,
    _pending_restores,
    _pending_restores_lock,
)
from voice_typer.server.clipboard_snapshot import ClipboardSnapshot


class PasteMixin:
    """Paste orchestration mixin for :class:`ClipboardManager`."""

    def paste(
        self,
        snapshot: ClipboardSnapshot | None = None,
        restore_delay: float | None = None,
        pasted_text: str | None = None,
        force: bool = False,
        pasted_seq: int | None = None,
    ) -> bool:
        """Send a paste keystroke into the focused window."""
        # Register the restore entry first (so the cap-overflow
        entry = self._register_pending_restore(snapshot, restore_delay, pasted_text)
        dispatched_ok = False
        try:
            dispatched_ok = self._attempt_paste_dispatch(snapshot, pasted_text, pasted_seq, force)
            return dispatched_ok
        except Exception as e:
            _cb.log.warning("[CLIPBOARD] Auto-paste failed (clipboard still has the text): %s", e)
            return False
        finally:
            self._settle_pending_restore(entry, dispatched_ok)

    def _attempt_paste_dispatch(
        self,
        snapshot: ClipboardSnapshot | None,
        pasted_text: str | None,
        pasted_seq: int | None,
        force: bool,
    ) -> bool:
        """Run the gate chain + keystroke dispatch. Returns True iff delivered."""
        # Early-return gates (original order: pynput → rate-limit → paste_enabled).
        if not self._check_pynput_available():
            return False
        if not self._check_rate_limit()[0]:
            return False
        if not self._check_paste_enabled(force)[0]:
            return False
        # Pre-dispatch prep (Windows-only seq-mismatch + RDP delay; stuck-mods release).
        self._recheck_seq_mismatch(pasted_text, pasted_seq)
        paste_delay = self._compute_paste_delay()
        self._release_stuck_modifiers()
        # Safety + IME gates (Windows-only IME guard).
        if not self._check_target_safety()[0]:
            return False
        if not self._check_ime_composition():
            return False
        # Dispatch + finalize (clipboard still has the text on failure).
        if not self._post_delay_recheck(paste_delay):
            return False
        safe_hwnd, safe_macos_pid = self._capture_target_handle()
        process_name = self._detect_focused_process()
        is_terminal = self._is_terminal_process(process_name)
        self._log_rich_editor(process_name)
        if not self._dispatch_keystroke(is_terminal, safe_hwnd, safe_macos_pid, pasted_text):
            return False
        return self._finalize_paste(is_terminal, process_name, snapshot)

    def _settle_pending_restore(
        self,
        pending_entry: Any,
        dispatched_ok: bool,
    ) -> None:
        """Spawn the restore daemon (dispatch OK) or roll back (failure)."""
        if pending_entry is None:
            return
        if dispatched_ok:
            _cm, snapshot, expected, delay = pending_entry
            self._spawn_restore_daemon(snapshot, expected, delay, pending_entry)
            return
        with _pending_restores_lock, contextlib.suppress(ValueError):
            _pending_restores.remove(pending_entry)
        _cb.log.info("[CLIPBOARD] Paste not delivered, keeping dictated text in clipboard for manual paste")

    # Each helper has an explicit error contract: returns a result tuple

    def _register_pending_restore(
        self,
        snapshot: ClipboardSnapshot | None,
        restore_delay: float | None,
        pasted_text: str | None,
    ) -> tuple[Any, ClipboardSnapshot, str, float] | None:
        """Append the pending-restore entry to the atexit registry.

        Returns the 4-tuple ``(self, snapshot, expected_text, delay)`` if
        """
        if snapshot is None:
            return None
        delay = restore_delay if restore_delay is not None else (self._restore_delay_ms / 1000.0)
        expected = pasted_text if pasted_text is not None else self._last_copied_text
        entry: tuple[Any, ClipboardSnapshot, str, float] = (self, snapshot, expected, delay)
        with _pending_restores_lock:
            # Hard cap on the in-flight pending-restores list. If we're
            if len(_pending_restores) >= _MAX_PENDING_RESTORES:
                oldest_entry = _pending_restores.pop(0)
                _oldest_cm, oldest_snapshot, _oldest_text, _oldest_delay = oldest_entry
                try:
                    oldest_snapshot.restore()
                    _cb.log.warning(
                        "[CLIPBOARD] _pending_restores cap hit (%d), "
                        "synchronously restored oldest snapshot to prevent "
                        "unbounded growth",
                        _MAX_PENDING_RESTORES,
                    )
                except Exception:
                    _cb.log.exception(
                        "[CLIPBOARD] force-restore of oldest pending entry "
                        "failed (cap=%d), leaving clipboard state as-is",
                        _MAX_PENDING_RESTORES,
                    )
            _pending_restores.append(entry)
        return entry

    def _spawn_restore_daemon(
        self,
        snapshot: ClipboardSnapshot,
        expected: str,
        delay: float,
        pending_entry: Any,
    ) -> None:
        """Spawn the daemon-thread restore; rollback on spawn failure."""
        try:
            threading.Thread(
                target=self._delayed_restore,
                args=(snapshot, expected, delay, pending_entry),
                daemon=True,
                name="clipboard-restore",
            ).start()
        except (OSError, RuntimeError) as exc:
            _cb.log.warning(
                "[CLIPBOARD] failed to start clipboard-restore thread: %s, "
                "removing orphaned _pending_restores entry to prevent leak",
                exc,
            )
            with _pending_restores_lock, contextlib.suppress(ValueError):
                _pending_restores.remove(pending_entry)  # already removed by another path

    def _check_pynput_available(self) -> bool:
        """Return True if some paste mechanism is available."""
        if _cb._Controller is not None:
            return True
        if _cb.is_windows():
            return True
        if _cb.is_linux() and _cb._is_wayland_paste_session() and _cb._have_wtype():
            _cb.log.debug("[CLIPBOARD] pynput unavailable, will use wtype on Wayland")
            return True
        _cb.log.warning("[CLIPBOARD] pynput unavailable, cannot paste")
        return False

    def _check_rate_limit(self) -> tuple[bool, str | None]:
        """Return ``(False, reason)`` if paste would be rate-limited."""
        now = _cb.time.monotonic()
        if now - self._last_paste_time < self._PASTE_RATE_LIMIT:
            _cb.log.info(
                "[CLIPBOARD] Paste rate-limited (%.0f ms since last paste)",
                (now - self._last_paste_time) * 1000,
            )
            return (
                False,
                "[CLIPBOARD] Paste rate-limited (%.0f ms since last paste)" % ((now - self._last_paste_time) * 1000),
            )
        return (True, None)

    def _check_paste_enabled(self, force: bool) -> tuple[bool, str | None]:
        """Return ``(False, reason)`` if paste is disabled by config."""
        if not self.paste_enabled and not force:
            _cb.log.info("[CLIPBOARD] Paste disabled by config -- skipping keystroke")
            return (False, "[CLIPBOARD] Paste disabled by config -- skipping keystroke")
        return (True, None)

    def _recheck_seq_mismatch(self, pasted_text: str | None, pasted_seq: int | None) -> None:
        """Windows-only: re-copy the clipboard if its sequence number changed."""
        if not _cb.is_windows():
            return
        if not hasattr(self, "_clipboard_seq"):
            return
        # Prefer the request-scoped seq when provided (set by
        expected_seq = pasted_seq if pasted_seq is not None else self._clipboard_seq
        if not expected_seq:
            return
        current_seq = self._get_clipboard_sequence_number()
        if current_seq == expected_seq:
            return
        _cb.log.warning(
            "[CLIPBOARD] Clipboard modified between copy and paste (seq %d -> %d), re-copying",
            expected_seq,
            current_seq,
        )
        try:
            recopy_text = pasted_text if pasted_text is not None else self._last_copied_text
            if recopy_text:
                _cb._copy_to_clipboard(recopy_text)
                # Brief delay to let the clipboard settle.
                _cb.time.sleep(0.02)
                # Update seq so a subsequent mismatch check is accurate.
                self._clipboard_seq = self._get_clipboard_sequence_number()
        except Exception as exc:
            _cb.log.error(
                "[CLIPBOARD] Failed to re-copy after seq mismatch: %s, paste may deliver stale content",
                exc,
            )

    def _compute_paste_delay(self) -> float:
        """Return the paste_delay (seconds), 100ms in RDP sessions, 0 otherwise."""
        if not _cb.is_windows():
            return 0.0
        try:
            from voice_typer.server.server_platform.remote_session import is_remote_session

            if is_remote_session():
                _cb.log.info("[CLIPBOARD] RDP session detected, increasing paste delay to %dms", 100)
                return 0.10
        except Exception:
            # The lazy import + platform predicate call can raise
            _cb.log.debug(
                "[CLIPBOARD] is_remote_session probe failed; using default paste delay",
                exc_info=True,
            )
        return 0.0

    def _check_target_safety(self) -> tuple[bool, int | None]:
        """Return ``(False, None)`` if the foreground window is unsafe for paste."""
        if not self._is_safe_paste_target():
            _cb.log.info("[CLIPBOARD] Paste blocked: security-sensitive window in foreground")
            return (False, None)
        return (True, None)

    def _check_ime_composition(self) -> bool:
        """Return False (and publish a toast) if an IME composition is in progress."""
        if not _cb.is_windows():
            return True
        try:
            from voice_typer.server.hotkeys.windows.ime_guard import is_ime_composing

            if not is_ime_composing():
                return True
            _cb.log.info("[CLIPBOARD] Paste deferred. IME composition in progress")
            try:
                from voice_typer.server import event_bus

                event_bus.publish(
                    {
                        "type": "paste_deferred",
                        "data": {
                            # ``reason`` only. The renderer maps it to a
                            "reason": "ime_composition",
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
            _cb.log.debug(
                "[CLIPBOARD] is_ime_composing() probe failed, failing open",
                exc_info=True,
            )
            return True

    def _post_delay_recheck(self, paste_delay: float) -> bool:
        """Sleep ``paste_delay`` seconds (if > 0) and re-validate target safety.

        Returns True if safe to proceed with dispatch, False if the
        """
        if paste_delay <= 0:
            return True
        _cb.time.sleep(paste_delay)
        if not self._is_safe_paste_target():
            _cb.log.info("[CLIPBOARD] Paste blocked: foreground target became unsafe during paste delay")
            return False
        return True

    def _capture_target_handle(self) -> tuple[int, int | None]:
        """Capture the foreground window handle / macOS PID for TOCTOU re-check.

        Returns ``(safe_hwnd, safe_macos_pid)``. On Windows, captures
        """
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
        return (safe_hwnd, safe_macos_pid)

    def _log_rich_editor(self, process_name: str | None) -> None:
        """Log a one-shot info when the paste target appears to be a rich editor."""
        if process_name and process_name.lower() in _cb._RICH_EDITOR_PROCESS_NAMES:
            _cb.log.info(
                "[CLIPBOARD] Paste target appears to be a rich editor (%s), "
                "pasting plain text (contentEditable detection not implemented)",
                process_name,
            )

    def _recheck_toctou(self, safe_hwnd: int, key_label: str) -> bool:
        """Windows-only TOCTOU re-check. Returns True if hwnd unchanged."""
        if not safe_hwnd:
            return True  # fail open, no hwnd captured (non-Windows or ctypes probe failed)
        try:
            import ctypes as _ctypes_mod

            current_hwnd = _ctypes_mod.windll.user32.GetForegroundWindow()
        except Exception:
            return True  # fail open, can't re-fetch, safety check already ran
        if current_hwnd != safe_hwnd:
            _cb.log.warning(
                "[CLIPBOARD] Foreground window changed during paste "
                "(TOCTOU: hwnd %d -> %d), aborting paste to avoid "
                "sending %s into the wrong window",
                safe_hwnd,
                current_hwnd,
                key_label,
            )
            return False
        return True

    def _recheck_macos_toctou(self, safe_macos_pid: int | None) -> bool:
        """macOS-only TOCTOU re-check. Returns True if frontmost PID unchanged."""
        if safe_macos_pid is None:
            return True  # fail open, no PID captured (non-macOS or pyobjc unavailable)
        current_pid = self._get_frontmost_pid_macos()
        if current_pid is None:
            return True  # fail open, can't re-fetch, safety check already ran
        if current_pid != safe_macos_pid:
            _cb.log.warning(
                "[CLIPBOARD] Frontmost macOS app changed during paste "
                "(TOCTOU: pid %d -> %d), aborting paste to avoid "
                "sending Cmd+V into the wrong window",
                safe_macos_pid,
                current_pid,
            )
            return False
        return True

    def _dispatch_keystroke(
        self,
        is_terminal: bool,
        safe_hwnd: int,
        safe_macos_pid: int | None,
        pasted_text: str | None,
    ) -> bool:
        """Platform-specific keystroke dispatch (4 platform branches × terminal/non)."""
        paste_succeeded = True
        use_wayland_wtype = _cb.is_linux() and _cb._is_wayland_paste_session() and _cb._have_wtype()
        # Thread ``pasted_text`` (request-scoped value parameter)
        wtype_text = pasted_text if pasted_text is not None else self._last_copied_text
        if is_terminal:
            if _cb.is_macos():
                # TOCTOU re-check on macOS. Re-fetch the frontmost app
                if not self._recheck_macos_toctou(safe_macos_pid):
                    return False
                self._safe_key_press(_cb._Key.cmd, "v")
            elif use_wayland_wtype:
                # Linux Wayland residual risk, wtype does not return
                _cb._linux_paste_via_wtype(wtype_text, is_terminal=True)
            elif _cb.is_windows():
                # Windows terminal paste uses Shift+Insert (terminal
                if not self._recheck_toctou(safe_hwnd, "Shift+Insert"):
                    return False
                if self._keyboard is None:
                    # pynput unavailable: use the Win32 SendInput
                    paste_succeeded = self._send_shift_insert_win32()
                else:
                    assert _cb._Key is not None
                    self._safe_key_press(_cb._Key.shift, _cb._Key.insert)
            else:
                assert _cb._Key is not None
                self._safe_key_press(_cb._Key.shift, _cb._Key.insert)
        elif _cb.is_macos():
            # Same TOCTOU re-check as the terminal branch above.
            if not self._recheck_macos_toctou(safe_macos_pid):
                return False
            assert _cb._Key is not None
            self._safe_key_press(_cb._Key.cmd, "v")
        elif _cb.is_windows():
            # TOCTOU re-check. Re-fetch the foreground window and
            if not self._recheck_toctou(safe_hwnd, "Ctrl+V"):
                return False
            # Check the return value of _send_ctrl_v_win32. A False
            paste_succeeded = self._send_ctrl_v_win32()
        elif use_wayland_wtype:
            _cb._linux_paste_via_wtype(wtype_text)
        else:
            assert _cb._Key is not None
            self._safe_key_press(_cb._Key.ctrl, "v")

        if not paste_succeeded:
            _cb.log.warning("[CLIPBOARD] Auto-paste failed (SendInput partial success, UIPI may have blocked)")
        return paste_succeeded

    def _finalize_paste(
        self,
        is_terminal: bool,
        process_name: str | None,
        snapshot: ClipboardSnapshot | None,
    ) -> bool:
        """Update ``_last_paste_time`` and emit the audit log. Always returns True."""
        self._last_paste_time = _cb.time.monotonic()
        _cb.log.info(
            "[CLIPBOARD-AUDIT] Sent paste keystroke (terminal=%s, target=%s, restore_scheduled=%s)",
            is_terminal,
            process_name or "unknown",
            snapshot is not None,
        )
        return True

    def _send_ctrl_v_win32(self) -> bool:
        """Send Ctrl+V via a single atomic SendInput batch."""
        # Delegate to the package-level _send_ctrl_v_win32 helper
        return _cb._send_ctrl_v_win32(fallback=lambda: self._safe_key_press(_cb._Key.ctrl, "v"))

    def _send_shift_insert_win32(self) -> bool:
        """Send Shift+Insert via a single atomic SendInput batch."""
        # Delegate to the package-level _send_shift_insert_win32 helper
        return _cb._send_shift_insert_win32(fallback=lambda: self._safe_key_press(_cb._Key.shift, _cb._Key.insert))
