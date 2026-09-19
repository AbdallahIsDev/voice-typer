"""Clipboard snapshot restore registry + delayed/immediate restore impls."""

from __future__ import annotations

import atexit
import contextlib
import threading
from typing import Any

from voice_typer.server import clipboard as _cb

# When paste() schedules a daemon-thread restore, it also appends an
_pending_restores: list[tuple[Any, Any, str, float]] = []
_pending_restores_lock = threading.Lock()
_MAX_PENDING_RESTORES = 8


def _force_restore_pending_at_exit() -> None:
    """atexit handler, force-restore any pending snapshots."""
    with _pending_restores_lock:
        items = list(_pending_restores)
        _pending_restores.clear()
    for _cm, snapshot, pasted_text, delay in items:
        try:
            # Try to read the clipboard to decide whether to restore.
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
_ATEXIT_REGISTERED = False
if not _ATEXIT_REGISTERED:
    try:
        atexit.register(_force_restore_pending_at_exit)
        _ATEXIT_REGISTERED = True
    except RuntimeError:  # pragma: no cover, atexit.register only fails if interpreter is shutting down
        # ``RuntimeError`` is raised when atexit hooks fire during
        pass


def _delayed_restore_impl(
    manager: Any,
    snapshot: Any,
    pasted_text: str,
    delay: float,
    pending_entry: Any = None,
) -> None:
    """Restore a snapshot after a delay. Runs on a daemon thread."""
    try:
        _cb.time.sleep(delay)

        # claim the pending_entry under the lock BEFORE
        if pending_entry is not None:
            try:
                with _pending_restores_lock:
                    try:
                        _pending_restores.remove(pending_entry)
                    except ValueError:
                        # Entry was already claimed by atexit (or
                        _cb.log.debug(
                            "[CLIPBOARD-AUDIT] Pending entry already claimed by atexit, skipping daemon restore"
                        )
                        return  #  short-circuit
            except Exception:  # pragma: no cover, catastrophic lock failure
                _cb.log.exception("[CLIPBOARD] Failed to claim pending restore entry, proceeding with restore")
                # Continue with restore anyway (best-effort). The

        try:
            # uses `wl-paste` so the defensive check actually reads
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
                "[CLIPBOARD-AUDIT] Restore skipped, clipboard changed (current=%d chars, expected=%d chars)",
                len(current) if current else 0,
                len(pasted_text),
            )
    except Exception:
        _cb.log.exception("[CLIPBOARD] Delayed restore failed")
    finally:
        # proceeding with restore. Without this defensive remove,
        try:
            manager._last_copied_text = ""
        except Exception:  # pragma: no cover, attribute access broken
            _cb.log.debug("[CLIPBOARD] Failed to clear _last_copied_text", exc_info=True)
        if pending_entry is not None:
            with _pending_restores_lock, contextlib.suppress(ValueError):
                _pending_restores.remove(pending_entry)


def _restore_now_impl(manager: Any, snapshot: Any) -> None:
    """Restore a snapshot immediately (no paste keystroke, no delay)."""
    if snapshot is None:
        return
    try:
        snapshot.restore()
        _cb.log.info("[CLIPBOARD-AUDIT] Restored snapshot immediately (no paste)")
    except Exception:
        _cb.log.exception("[CLIPBOARD] Immediate restore failed")
    finally:
        # Clear the cached
        try:
            manager._last_copied_text = ""
        except Exception:  # pragma: no cover, attribute access broken
            _cb.log.debug(
                "[CLIPBOARD] Failed to clear _last_copied_text in restore_now",
                exc_info=True,
            )


__all__ = [
    "_ATEXIT_REGISTERED",
    "_MAX_PENDING_RESTORES",
    "_delayed_restore_impl",
    "_force_restore_pending_at_exit",
    "_pending_restores",
    "_pending_restores_lock",
    "_restore_now_impl",
]
