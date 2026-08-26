"""PasteMixin — paste orchestrator + gates + dispatch + SendInput senders.

Split verbatim out of the pre-split ``clipboard/manager.py`` module.

Design contract: paste() ALWAYS sends a paste keystroke (Ctrl+V or
platform equivalent); terminal emulators use Shift+Insert. All
patchable symbols are looked up via the PACKAGE (``_cb.X``) at call
time so test patches on ``voice_typer.server.clipboard`` keep working.
The pending-restores registry objects are imported from
:mod:`voice_typer.server.clipboard.restore` — the SAME list/lock/int
objects re-exported by the package and manager namespaces, so mutations
through any namespace are visible everywhere.
"""

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

        Orchestrator: the original 542-LOC ``paste()``
        was split into 16 focused helpers (``_register_pending_restore``,
        ``_spawn_restore_daemon``, ``_check_pynput_available``,
        ``_check_rate_limit``, ``_check_paste_enabled``,
        ``_recheck_seq_mismatch``, ``_compute_paste_delay``,
        ``_check_target_safety``, ``_check_ime_composition``,
        ``_post_delay_recheck``, ``_capture_target_handle``,
        ``_log_rich_editor``, ``_recheck_toctou``, ``_recheck_macos_toctou``,
        ``_dispatch_keystroke``, ``_finalize_paste``). Each helper has an
        explicit error contract — ``(ok, reason)`` tuple or bool — so the
        orchestrator decides whether to short-circuit. No silent
        failures (E13): every short-circuit logs a reason.
        """
        # Schedule restore FIRST — failure to send the keystroke must not
        # prevent the paired restore (DP1/DP2: borrow always paired w/ restore).
        entry = self._register_pending_restore(snapshot, restore_delay, pasted_text)
        if entry is not None:
            self._spawn_restore_daemon(entry[1], entry[2], entry[3], entry)
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
        # Dispatch + finalize (try/except mirrors original: clipboard still has the text).
        try:
            if not self._post_delay_recheck(paste_delay):
                return False
            safe_hwnd, safe_macos_pid = self._capture_target_handle()
            process_name = self._detect_focused_process()
            is_terminal = self._is_terminal_process(process_name)
            self._log_rich_editor(process_name)
            if not self._dispatch_keystroke(is_terminal, safe_hwnd, safe_macos_pid, pasted_text):
                return False
            return self._finalize_paste(is_terminal, process_name, snapshot)
        except Exception as e:
            _cb.log.warning("[CLIPBOARD] Auto-paste failed (clipboard still has the text): %s", e)
            return False

    # ─── paste() helpers ─────────────────────────────────────────────────
    #
    # Each helper has an explicit error contract: returns a result tuple
    # ``(ok, reason)`` or a bool, OR raises an exception (only
    # ``_dispatch_keystroke`` raises — and paste()'s try/except catches
    # it). No silent failures — E13 forbids ``except: pass``; each
    # helper either succeeds, returns a (False, reason) tuple so paste()
    # can short-circuit with a logged reason, or lets the exception
    # propagate.

    def _register_pending_restore(
        self,
        snapshot: ClipboardSnapshot | None,
        restore_delay: float | None,
        pasted_text: str | None,
    ) -> tuple[Any, ClipboardSnapshot, str, float] | None:
        """Append the pending-restore entry to the atexit registry.

        Returns the 4-tuple ``(self, snapshot, expected_text, delay)`` if
        a snapshot was provided (so the caller can pass it to
        :meth:`_spawn_restore_daemon`); ``None`` if ``snapshot is None``
        (no restore to schedule).

        Honors the ``_MAX_PENDING_RESTORES`` cap: when the registry is
        full, the OLDEST entry is force-restored synchronously (under
        the lock, atomically w.r.t. other appends) BEFORE appending the
        new entry. This bounds peak RSS without leaking snapshots — the
        oldest entry is the one closest to having been restored anyway
        (its daemon thread is the one most likely already mid-sleep or
        stuck), so evicting it minimises disruption.
        """
        if snapshot is None:
            return None
        delay = restore_delay if restore_delay is not None else (self._restore_delay_ms / 1000.0)
        expected = pasted_text if pasted_text is not None else self._last_copied_text
        entry: tuple[Any, ClipboardSnapshot, str, float] = (self, snapshot, expected, delay)
        with _pending_restores_lock:
            # Hard cap on the in-flight pending-restores list. If we're
            # at the cap, force-restore the OLDEST entry's snapshot
            # synchronously (under the lock — atomic w.r.t. other
            # appends) BEFORE appending the new entry. This bounds peak
            # RSS: without the cap, a runaway condition (user-set 60 s
            # restore delay, daemon-thread leak, OpenClipboard hang)
            # pins N × ~16 MB × N formats of clipboard snapshots in
            # Python heap until atexit.
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
            _pending_restores.append(entry)
        return entry

    def _spawn_restore_daemon(
        self,
        snapshot: ClipboardSnapshot,
        expected: str,
        delay: float,
        pending_entry: Any,
    ) -> None:
        """Spawn the daemon-thread restore; rollback on spawn failure.

        Wraps ``Thread().start()`` in try/except — if start() fails
        (out of thread resources / fd exhaustion), remove the orphaned
        entry from ``_pending_restores`` so it doesn't hold the snapshot
        (potentially large image/file clipboard content) and dictated
        text for the process lifetime. Log a WARNING. Do NOT call
        ``snapshot.restore_now()`` — if thread start failed, the system
        is resource-starved and synchronous restore might also fail.
        """
        try:
            threading.Thread(
                target=self._delayed_restore,
                args=(snapshot, expected, delay, pending_entry),
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
                _pending_restores.remove(pending_entry)  # already removed by another path

    def _check_pynput_available(self) -> bool:
        """Return True if some paste mechanism is available.

        Short-circuits paste() when pynput is missing AND we're not on
        Windows (which uses ``_send_ctrl_v_win32`` via SendInput without
        pynput) AND we're not on Linux Wayland with ``wtype`` available
        (which routes through ``_linux_paste_via_wtype``).
        """
        if _cb._Controller is not None:
            return True
        if _cb.is_windows():
            return True
        if _cb.is_linux() and _cb._is_wayland_paste_session() and _cb._have_wtype():
            _cb.log.debug("[CLIPBOARD] pynput unavailable — will use wtype on Wayland")
            return True
        _cb.log.warning("[CLIPBOARD] pynput unavailable — cannot paste")
        return False

    def _check_rate_limit(self) -> tuple[bool, str | None]:
        """Return ``(False, reason)`` if paste would be rate-limited.

        Short-circuits BEFORE seq-mismatch re-copy so a rate-limited
        paste doesn't waste the re-copy work (and potentially race with
        a concurrent paste cycle).
        """
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
        """Return ``(False, reason)`` if paste is disabled by config.

        ``force=True`` bypasses the gate (used by ``repaste_last()`` —
        a manual user action that must never be coupled to the auto-paste
        (``paste_on_stop``) setting; see §2.12).
        """
        if not self.paste_enabled and not force:
            _cb.log.info("[CLIPBOARD] Paste disabled by config -- skipping keystroke")
            return (False, "[CLIPBOARD] Paste disabled by config -- skipping keystroke")
        return (True, None)

    def _recheck_seq_mismatch(self, pasted_text: str | None, pasted_seq: int | None) -> None:
        """Windows-only: re-copy the clipboard if its sequence number changed.

        PLAT-CLIPRACE: verifies the clipboard wasn't modified between
        ``copy()`` and ``paste()``. If another app (clipboard manager,
        password manager, screenshot tool) overwrote the clipboard,
        re-copy the dictated text before pasting.

        Threads the request-scoped ``pasted_text`` parameter (NOT
        ``self._last_copied_text``) so a concurrent cycle's copy()
        can't clobber the value this cycle re-copies — a concurrent
        repaste_last() can run ``copy(text_B)`` between this cycle's
        ``copy(text_A)`` and ``paste()``, overwriting
        ``self._last_copied_text`` with ``text_B``. Reading the instance
        attribute here would then re-copy ``text_B`` — wrong text pasted
        while the daemon's ``expected=text_A`` no longer matches,
        triggering an unwanted restore that clobbers ``text_B``.

        Uses :func:`_cb._copy_to_clipboard` (not ``pyperclip.copy``
        directly) so the Wayland dispatcher routes through ``wl-copy``
        on Linux Wayland sessions instead of pyperclip's xclip/xsel
        (X11-only, silently no-op under native Wayland apps).
        """
        if not _cb.is_windows():
            return
        if not hasattr(self, "_clipboard_seq"):
            return
        # CRIT-3: use the request-scoped seq when provided (set by
        # copy() for THIS request), falling back to the instance
        # attribute for repaste/callers that don't thread it. This
        # prevents a concurrent copy() in another request from
        # clobbering the seq we validate against.
        expected_seq = pasted_seq if pasted_seq is not None else self._clipboard_seq
        if not expected_seq:
            return
        current_seq = self._get_clipboard_sequence_number()
        if current_seq == expected_seq:
            return
        _cb.log.warning(
            "[CLIPBOARD] Clipboard modified between copy and paste (seq %d -> %d) — re-copying",
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
                "[CLIPBOARD] Failed to re-copy after seq mismatch: %s — paste may deliver stale content",
                exc,
            )

    def _compute_paste_delay(self) -> float:
        """Return the paste_delay (seconds) — 100ms in RDP sessions, 0 otherwise.

        PLAT-RDP: RDP clipboard sync is slower than local; the 100ms
        delay lets the clipboard settle before the keystroke send. The
        unconditional 20ms ``paste_delay`` sleep that used to apply to
        every paste on every platform was dropped — it added latency
        without benefit (the seq-mismatch re-copy path already has its
        own 20ms settle delay when it fires).
        """
        if not _cb.is_windows():
            return 0.0
        try:
            from voice_typer.server.server_platform.remote_session import is_remote_session

            if is_remote_session():
                _cb.log.info("[CLIPBOARD] RDP session detected — increasing paste delay to %dms", 100)
                return 0.10
        except Exception:
            # The lazy import + platform predicate call can raise
            # ImportError (server_platform not importable in some
            # test/headless envs) or any error from the underlying
            # Win32/POSIX session probe. Keep the broad catch so a flaky
            # probe never blocks paste, but log at DEBUG for forensic
            # value (was silent ``pass``).
            _cb.log.debug(
                "[CLIPBOARD] is_remote_session probe failed; using default paste delay",
                exc_info=True,
            )
        return 0.0

    def _check_target_safety(self) -> tuple[bool, int | None]:
        """Return ``(False, None)`` if the foreground window is unsafe for paste.

        Note: this helper returns ``(is_safe, None)`` — the hwnd is
        captured SEPARATELY by :meth:`_capture_target_handle` AFTER the
        optional paste_delay sleep, NOT here, so the TOCTOU re-check
        (:meth:`_recheck_toctou`) compares against the most-recent
        foreground window. Capturing hwnd at safety-check time would
        widen the TOCTOU window (a regression forbidden by E12 — never
        downgrade). The spec's ``(is_safe, hwnd)`` return type is
        honored as ``(is_safe, None)``; the hwnd field is populated
        later by :meth:`_capture_target_handle`.
        """
        if not self._is_safe_paste_target():
            _cb.log.info("[CLIPBOARD] Paste blocked — security-sensitive window in foreground")
            return (False, None)
        return (True, None)

    def _check_ime_composition(self) -> bool:
        """Return False (and publish a toast) if an IME composition is in progress.

        Windows-only. On Windows, if the foreground window has the IME
        open with an active composition string (e.g. the user is
        mid-typing a CJK character), sending Ctrl+V / Shift+Insert now
        would either (a) commit the incomplete composition string
        before pasting (corrupting the user's input) or (b) be silently
        dropped by the IME (the paste keystroke is consumed by the
        IME's key handler, never reaching the target app). We pick
        "skip and surface a toast" over "defer ~200 ms and re-check"
        because deferring would block the transcription thread for
        200 ms on every paste into an IME-active window (a measurable
        latency regression for CJK dictation).

        The toast is published via ``event_bus`` (the same channel used
        by ``paste_failed`` in the dictation pipeline) so the renderer
        can surface a sonner toast. The event is wrapped in try/except
        so a broken event bus never aborts the paste-skip path.
        """
        if not _cb.is_windows():
            return True
        try:
            from voice_typer.server.hotkeys.windows.ime_guard import is_ime_composing

            if not is_ime_composing():
                return True
            _cb.log.info("[CLIPBOARD] Paste deferred — IME composition in progress")
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
            # environments where ``ctypes.windll.imm32`` is not present
            # or has been patched to a MagicMock. Fail open — the safety
            # check above already validated the target.
            _cb.log.debug(
                "[CLIPBOARD] is_ime_composing() probe failed — failing open",
                exc_info=True,
            )
            return True

    def _post_delay_recheck(self, paste_delay: float) -> bool:
        """Sleep ``paste_delay`` seconds (if > 0) and re-validate target safety.

        Returns True if safe to proceed with dispatch, False if the
        foreground target became unsafe during the sleep (TOCTOU).

        CRIT-2: re-validate the target RIGHT BEFORE sending the
        keystroke. The safety check in :meth:`_check_target_safety`
        ran before the paste_delay sleep; the foreground window could
        have changed during that window (TOCTOU). If the target is now
        unsafe (e.g. focus moved to a credential prompt), abort — do
        NOT send the paste into the wrong/unsafe window.

        Only re-checks when ``paste_delay > 0`` — when ``paste_delay
        == 0`` the upstream safety check ran immediately before this
        branch, so a second UIA round-trip here would be redundant
        (no time for the foreground window to change).
        """
        if paste_delay <= 0:
            return True
        _cb.time.sleep(paste_delay)
        if not self._is_safe_paste_target():
            _cb.log.info("[CLIPBOARD] Paste blocked — foreground target became unsafe during paste delay")
            return False
        return True

    def _capture_target_handle(self) -> tuple[int, int | None]:
        """Capture the foreground window handle / macOS PID for TOCTOU re-check.

        Returns ``(safe_hwnd, safe_macos_pid)``. On Windows, captures
        the foreground HWND via ``GetForegroundWindow``. On macOS,
        captures the frontmost app PID via ``NSWorkspace``. On Linux,
        returns ``(0, None)`` — Wayland has no equivalent atomic probe
        (``wtype`` does not return the focused surface); the residual
        TOCTOU risk is documented in the Linux dispatch branch of
        :meth:`_dispatch_keystroke`.

        Captured AFTER the optional paste_delay sleep (in
        :meth:`_post_delay_recheck`) so the value is the most-recent
        foreground window at dispatch time — preserving the original
        pre-refactor TOCTOU window (E12: never downgrade).
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
        """Log a one-shot info when the paste target appears to be a rich editor.

        PLAT-CONTENT: emits a ``log.info`` when ``process_name`` matches
        the canonical rich-editor set (e.g. Word, OneNote, Outlook
        inspector). Used for diagnostics — contentEditable detection
        is not implemented, so we always paste plain text.
        """
        if process_name and process_name.lower() in _cb._RICH_EDITOR_PROCESS_NAMES:
            _cb.log.info(
                "[CLIPBOARD] Paste target appears to be a rich editor (%s) — "
                "pasting plain text (contentEditable detection not implemented)",
                process_name,
            )

    def _recheck_toctou(self, safe_hwnd: int, key_label: str) -> bool:
        """Windows-only TOCTOU re-check. Returns True if hwnd unchanged.

        Re-fetches the foreground window and compares to ``safe_hwnd``
        captured in :meth:`_capture_target_handle`. If the user
        Alt+Tabbed (or a credential prompt stole focus) between the
        safety check and the keystroke send, abort paste to avoid
        sending ``key_label`` (e.g. ``Ctrl+V`` / ``Shift+Insert``)
        into the wrong window.

        If we can't re-fetch (ctypes itself broken), fail open — the
        upstream safety check already validated the target.

        ``key_label`` is used in the abort log message to identify
        which keystroke was avoided (the original code emitted two
        verbatim variants — ``Shift+Insert`` for terminal targets,
        ``Ctrl+V`` for non-terminal — preserved here via the parameter).
        """
        if not safe_hwnd:
            return True  # fail open — no hwnd captured (non-Windows or ctypes probe failed)
        try:
            import ctypes as _ctypes_mod

            current_hwnd = _ctypes_mod.windll.user32.GetForegroundWindow()
        except Exception:
            return True  # fail open — can't re-fetch, safety check already ran
        if current_hwnd != safe_hwnd:
            _cb.log.warning(
                "[CLIPBOARD] Foreground window changed during paste "
                "(TOCTOU: hwnd %d -> %d) — aborting paste to avoid "
                "sending %s into the wrong window",
                safe_hwnd,
                current_hwnd,
                key_label,
            )
            return False
        return True

    def _recheck_macos_toctou(self, safe_macos_pid: int | None) -> bool:
        """macOS-only TOCTOU re-check. Returns True if frontmost PID unchanged.

        Re-fetches the frontmost app PID and compares to
        ``safe_macos_pid`` captured in :meth:`_capture_target_handle`.
        If the user Cmd-Tabbed (or a credential prompt stole focus)
        between the safety check and the keystroke send, abort. If the
        PID can't be re-fetched (pyobjc unavailable), fail open — the
        upstream safety check already validated the target.

        Linux Wayland has no equivalent atomic probe (``wtype`` does
        not return the focused surface); the residual TOCTOU risk on
        Linux is documented in :meth:`_dispatch_keystroke`.
        """
        if safe_macos_pid is None:
            return True  # fail open — no PID captured (non-macOS or pyobjc unavailable)
        current_pid = self._get_frontmost_pid_macos()
        if current_pid is None:
            return True  # fail open — can't re-fetch, safety check already ran
        if current_pid != safe_macos_pid:
            _cb.log.warning(
                "[CLIPBOARD] Frontmost macOS app changed during paste "
                "(TOCTOU: pid %d -> %d) — aborting paste to avoid "
                "sending Cmd+V into the wrong window (XZ-CLIP-04)",
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
        """Platform-specific keystroke dispatch (4 platform branches × terminal/non).

        Branches by ``(is_terminal, platform)``:

        * terminal + macOS        → Cmd+V (after macOS TOCTOU re-check)
        * terminal + Linux/Wayland → wtype ``Ctrl+Shift+V``
        * terminal + Windows      → Shift+Insert (after Windows TOCTOU)
          [Win32 SendInput when pynput unavailable, else pynput]
        * terminal + other        → Shift+Insert
        * non-terminal + macOS    → Cmd+V (after macOS TOCTOU re-check)
        * non-terminal + Windows  → Ctrl+V via SendInput (after Win TOCTOU)
        * non-terminal + Linux/Wayland → wtype ``Ctrl+V``
        * non-terminal + other    → Ctrl+V

        Returns True if the keystroke was delivered cleanly, False on
        TOCTOU abort OR partial-success SendInput (1..3 of 4 events).

        ``paste_succeeded`` defaults to True; only
        :meth:`_send_ctrl_v_win32` / :meth:`_send_shift_insert_win32`
        can set it to False (partial SendInput). After dispatch, if
        ``paste_succeeded`` is False, emits the partial-success warning
        and returns False so the caller surfaces the failure.
        """
        paste_succeeded = True
        use_wayland_wtype = _cb.is_linux() and _cb._is_wayland_paste_session() and _cb._have_wtype()
        # Thread ``pasted_text`` (request-scoped value parameter)
        # through the Wayland paste call sites instead of reading the
        # shared mutable ``self._last_copied_text`` instance attribute.
        # ``_linux_paste_via_wtype`` currently ignores its ``text``
        # argument, but if a future change re-enables it, the instance
        # attribute would become a leak/race vector (same rationale as
        # the seq-mismatch re-copy path in :meth:`_recheck_seq_mismatch`).
        wtype_text = pasted_text if pasted_text is not None else self._last_copied_text
        if is_terminal:
            if _cb.is_macos():
                # TOCTOU re-check on macOS. Re-fetch the frontmost app
                # PID and compare to ``safe_macos_pid`` captured above.
                # If the user Cmd-Tabbed (or a credential prompt stole
                # focus) between the safety check and the keystroke
                # send, abort. If the PID can't be re-fetched (pyobjc
                # unavailable), fail open — the safety check above
                # already validated the target.
                if not self._recheck_macos_toctou(safe_macos_pid):
                    return False
                self._safe_key_press(_cb._Key.cmd, "v")
            elif use_wayland_wtype:
                # Linux Wayland residual risk — wtype does not return
                # the focused surface, so we cannot re-verify the
                # target atomically. The safety check above ran
                # immediately before this call (paste_delay is 0 on
                # Linux), so the TOCTOU window is bounded to the
                # wtype fork+exec time (~5-10ms). A user who Alt-Tabs
                # in that window may receive the paste into the wrong
                # target; documented as residual risk.
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
                # either unmapped or interpreted as the literal ``^V``
                # control byte). When pynput (``self._keyboard``) is
                # unavailable, route through the Win32 SendInput helper
                # so terminal paste does not silently no-op on
                # headless / sandboxed hosts. When pynput IS available,
                # use the existing ``_safe_key_press`` path (which keeps
                # the existing UX and avoids a double-paste risk if
                # both paths fire).
                #
                # TOCTOU re-check mirrors the non-terminal Windows
                # branch below — re-fetch the foreground window and
                # compare to ``safe_hwnd`` captured above; abort if the
                # user Alt+Tabbed (or a credential prompt stole focus)
                # between the safety check and the keystroke.
                if not self._recheck_toctou(safe_hwnd, "Shift+Insert"):
                    return False
                if self._keyboard is None:
                    # pynput unavailable — use the Win32 SendInput
                    # helper. The bool return is honored so a
                    # partial-success SendInput (1..3 events) does not
                    # get logged as a successful paste.
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
            # compare to ``safe_hwnd`` captured above. If the user
            # Alt+Tabbed (or a credential prompt stole focus) between
            # the safety check and the SendInput call, abort paste to
            # avoid sending Ctrl+V into the wrong/unsafe window. If we
            # can't re-fetch (ctypes itself broken), fail open — the
            # safety check above already validated the target.
            if not self._recheck_toctou(safe_hwnd, "Ctrl+V"):
                return False
            # Check the return value of _send_ctrl_v_win32. A False
            # return means SendInput reported partial success (1..3 of
            # 4 events delivered) — the paste did NOT complete cleanly.
            # Previously this was silently dropped (the user saw
            # nothing happen and no warning). Now we log a warning
            # and return False so the user knows the paste failed.
            paste_succeeded = self._send_ctrl_v_win32()
        elif use_wayland_wtype:
            _cb._linux_paste_via_wtype(wtype_text)
        else:
            assert _cb._Key is not None
            self._safe_key_press(_cb._Key.ctrl, "v")

        if not paste_succeeded:
            _cb.log.warning("[CLIPBOARD] Auto-paste failed (SendInput partial success — UIPI may have blocked)")
        return paste_succeeded

    def _finalize_paste(
        self,
        is_terminal: bool,
        process_name: str | None,
        snapshot: ClipboardSnapshot | None,
    ) -> bool:
        """Update ``_last_paste_time`` and emit the audit log. Always returns True.

        Bookkeeping + audit-log concern extracted from the original
        paste()'s tail. Called ONLY after :meth:`_dispatch_keystroke`
        returns True (keystroke delivered cleanly) — on partial-success
        / TOCTOU abort / dispatch exception, paste() returns False
        before reaching here.
        """
        self._last_paste_time = _cb.time.monotonic()
        _cb.log.info(
            "[CLIPBOARD-AUDIT] Sent paste keystroke (terminal=%s, target=%s, restore_scheduled=%s)",
            is_terminal,
            process_name or "unknown",
            snapshot is not None,
        )
        return True

    def _send_ctrl_v_win32(self) -> bool:
        """Send Ctrl+V via a single atomic SendInput batch.

        On Windows, we always prefer SendInput over
        pynput.keyboard.Controller because pynput's Controller is
        blocked by UIPI when targeting elevated processes from a
        non-elevated one.  Our direct SendInput call is subject to
        the same UIPI restriction, but we log the failure explicitly
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
        # SendInput returns 0 — total failure) is bound here because
        # it needs self._safe_key_press + _Key.shift + _Key.insert
        # (instance + package state).
        return _cb._send_shift_insert_win32(fallback=lambda: self._safe_key_press(_cb._Key.shift, _cb._Key.insert))
