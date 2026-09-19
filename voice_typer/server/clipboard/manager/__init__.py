"""ClipboardManager orchestrator (composition root after manager split)."""

from __future__ import annotations

import threading  # noqa: F401  # re-exported: tests patch Thread via ``mgr_mod.threading`` (stdlib module, shared)
from typing import Any

from voice_typer.server import clipboard as _cb

# Pending-restores registry re-export
from voice_typer.server.clipboard.restore import (  # noqa: E402,F401
    _MAX_PENDING_RESTORES,
    _delayed_restore_impl,
    _force_restore_pending_at_exit,
    _pending_restores,
    _pending_restores_lock,
    _restore_now_impl,
)

# Paste-target safety re-exports
from voice_typer.server.clipboard.safety import (  # noqa: E402,F401
    _detect_focused_process_impl,
    _get_frontmost_pid_macos_impl,
    _is_safe_paste_target_impl,
    _is_terminal_process_impl,
)
from voice_typer.server.clipboard_snapshot import ClipboardSnapshot

# import the single canonical credential-dialog class set
from voice_typer.server.config import DEFAULT_CLIPBOARD_RESTORE_DELAY_MS as _DEFAULT_RESTORE_DELAY_MS

from ._copy import CopyMixin  # noqa: E402,F401
from ._errors import ClipboardCopyError  # noqa: E402,F401
from ._keyboard import KeyboardMixin  # noqa: E402,F401
from ._paste import PasteMixin  # noqa: E402,F401


class ClipboardManager(KeyboardMixin, PasteMixin, CopyMixin):
    """Handles copying text to clipboard and pasting into the focused app."""

    # rate limit for paste operations. The PROBLEMS
    _PASTE_RATE_LIMIT = 0.5

    def __init__(self, paste_enabled: bool = True):
        self.paste_enabled = paste_enabled
        # Lazy-import pynput so the module can load headless. The actual
        _cb._ensure_pynput_imported()
        self._keyboard = _cb._Controller() if _cb._Controller is not None else None
        self._last_paste_time: float = 0.0
        # PLAT-CLIPRACE: clipboard sequence number for race detection on Windows
        self._clipboard_seq: int = 0
        # PLAT-SECURE: last copied text for seq-mismatch recovery in paste().
        self._last_copied_text: str = ""
        # ADR-0010 §5.6 / DP8: removed ``_clear_thread`` and
        self._clipboard_save_restore_enabled: bool = True
        # ADR-0010 §5.6 / §5.3: cached restore delay in milliseconds. Read
        self._restore_delay_ms: int = _DEFAULT_RESTORE_DELAY_MS

    def refresh_config(self, config) -> None:
        """Refresh cached config flags from a Config object."""
        try:
            self._clipboard_save_restore_enabled = bool(getattr(config, "clipboard_save_restore", True))
        except Exception:
            _cb.log.warning(
                "[CLIPBOARD] refresh_config: clipboard_save_restore lookup failed, using safe default",
                exc_info=True,
            )
            self._clipboard_save_restore_enabled = True  # safe default

        try:
            self._restore_delay_ms = int(getattr(config, "clipboard_restore_delay_ms", _DEFAULT_RESTORE_DELAY_MS))
        except Exception:
            _cb.log.warning(
                "[CLIPBOARD] refresh_config: clipboard_restore_delay_ms lookup failed, using default",
                exc_info=True,
            )
            self._restore_delay_ms = _DEFAULT_RESTORE_DELAY_MS

        # §2.12: mirror paste_on_stop → paste_enabled so a runtime toggle
        try:
            self.paste_enabled = bool(getattr(config, "paste_on_stop", True))
        except Exception:
            _cb.log.warning(
                "[CLIPBOARD] refresh_config: paste_on_stop lookup failed, using safe default",
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
        """Get the Windows clipboard sequence number."""
        return _cb.Win32Clipboard.get_sequence_number()

    @staticmethod
    def _is_safe_paste_target() -> bool:
        """Check that the foreground window is safe for pasting."""
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
        """Restore a snapshot immediately (no paste keystroke, no delay)."""
        _restore_now_impl(self, snapshot)


__all__ = [
    "ClipboardCopyError",
    "ClipboardManager",
    "_MAX_PENDING_RESTORES",
    "_force_restore_pending_at_exit",
    "_pending_restores",
    "_pending_restores_lock",
]
