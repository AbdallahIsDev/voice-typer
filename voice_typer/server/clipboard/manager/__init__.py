"""ClipboardManager orchestrator (composition root after manager split).

Extracted from the original ``clipboard/manager.py`` monolith (1417 LOC)
into three focused modules:

* :mod:`..restore` — ``_pending_restores`` registry,
  ``_pending_restores_lock``, ``_MAX_PENDING_RESTORES``,
  ``_force_restore_pending_at_exit`` atexit handler, and the
  implementations of :meth:`ClipboardManager._delayed_restore` /
  :meth:`ClipboardManager.restore_now`.
* :mod:`..safety` — implementations of
  :meth:`ClipboardManager._is_safe_paste_target` /
  :meth:`ClipboardManager._is_terminal_process` /
  :meth:`ClipboardManager._detect_focused_process` /
  :meth:`ClipboardManager._get_frontmost_pid_macos`.
* this package (``clipboard/manager/``) — slim
  :class:`ClipboardManager` composed from concern mixins:
  :mod:`._copy` (:class:`CopyMixin` — snapshot capture + copy +
  verify), :mod:`._paste` (:class:`PasteMixin` — paste orchestrator,
  gates, dispatch, SendInput senders), :mod:`._keyboard`
  (:class:`KeyboardMixin` — stuck-modifier release + safe key press),
  plus ``__init__`` / ``refresh_config`` and the thin ``*_impl``
  delegators defined HERE so tests that patch
  ``voice_typer.server.clipboard.manager.<impl>`` keep intercepting
  them.

Contains:

* :class:`ClipboardCopyError` — distinguishes "copy failed" from
  "save/restore disabled" (ADR-0010 §5.2). Defined in :mod:`._errors`
  and re-exported here.
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
extracted :mod:`..restore` / :mod:`..safety` siblings, which share the
same ``_cb`` lookup discipline).

``_pending_restores`` and ``_pending_restores_lock`` live in
:mod:`..restore` and are re-exported below (and by the package
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

import threading  # noqa: F401  # re-exported: tests patch Thread via ``mgr_mod.threading`` (stdlib module, shared)
from typing import Any

from voice_typer.server import clipboard as _cb

# ─── clipboard split (pass a): pending-restores registry re-export ────────────
#
# The module-level ``_pending_restores`` list, ``_pending_restores_lock``,
# ``_MAX_PENDING_RESTORES`` cap, and ``_force_restore_pending_at_exit``
# atexit handler were extracted to :mod:`..restore`. They are re-exported
# here so legacy call sites and tests that import them from
# ``voice_typer.server.clipboard.manager`` (e.g.
# ``tests/test_clipboard_restore_race.py``,
# ``tests/test_clipboard_pending_restores_cap.py``) keep working
# unchanged. The re-export binds the SAME list / lock / int / function
# objects — mutations made through ``manager._pending_restores`` are
# visible through ``restore._pending_restores`` and vice versa.
from voice_typer.server.clipboard.restore import (  # noqa: E402,F401
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
# to :mod:`..safety`. The staticmethods on :class:`ClipboardManager`
# below are thin delegators that forward to these module-level impl
# functions, preserving the ``patch.object(ClipboardManager,
# "_is_safe_paste_target", ...)`` patch surface used by ~12 tests.
from voice_typer.server.clipboard.safety import (  # noqa: E402,F401
    _detect_focused_process_impl,
    _get_frontmost_pid_macos_impl,
    _is_safe_paste_target_impl,
    _is_terminal_process_impl,
)
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

from ._copy import CopyMixin  # noqa: E402,F401
from ._errors import ClipboardCopyError  # noqa: E402,F401
from ._keyboard import KeyboardMixin  # noqa: E402,F401
from ._paste import PasteMixin  # noqa: E402,F401


class ClipboardManager(KeyboardMixin, PasteMixin, CopyMixin):
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
        lives in :mod:`..safety`. Kept as a ``@staticmethod`` on the
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
        short-circuit) lives in :mod:`..restore`. Kept as an instance
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
        ``finally``) lives in :mod:`..restore`. Kept as an instance
        method on the class so the public API and the
        ``cm.restore_now(snap)`` call sites in tests keep working.
        """
        _restore_now_impl(self, snapshot)


__all__ = [
    "ClipboardCopyError",
    "ClipboardManager",
    "_MAX_PENDING_RESTORES",
    "_force_restore_pending_at_exit",
    "_pending_restores",
    "_pending_restores_lock",
]
