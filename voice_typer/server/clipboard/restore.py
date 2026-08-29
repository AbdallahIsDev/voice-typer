"""Clipboard snapshot restore registry + delayed/immediate restore impls.

Extracted from the original ``clipboard/manager.py`` monolith (pass a). Contains:

* :data:`_pending_restores` / :data:`_pending_restores_lock` — module-
  level registry of pending delayed-restores.
* :data:`_MAX_PENDING_RESTORES` — hard cap on the in-flight list.
* :func:`_force_restore_pending_at_exit` — atexit handler that
  force-restores any pending snapshots if the app exits during the
  restore-delay window.
* :func:`_delayed_restore_impl` — daemon-thread implementation of
  :meth:`ClipboardManager._delayed_restore`.
* :func:`_restore_now_impl` — implementation of
  :meth:`ClipboardManager.restore_now`.

Design contract (preserved verbatim from the pre-split ``manager.py``):

* ``_pending_restores`` and ``_pending_restores_lock`` live HERE (in
  ``restore.py``'s module namespace) and are re-exported by
  ``manager.py`` (so ``from voice_typer.server.clipboard.manager import
  _pending_restores`` keeps working) AND by the package ``__init__``
  (so ``clip_mod._pending_restores`` keeps working). Because the
  re-exports bind the SAME list / lock / int objects, mutations made
  through any of the three namespaces are visible through all of them.
* All patchable symbols (``_cb.time``, ``_cb.log``,
  ``_cb._paste_from_clipboard``) are looked up via the PACKAGE
  (``_cb.X``) at call time — NOT via this module's globals — so test
  patches like ``patch("voice_typer.server.clipboard.time", ...)``
  actually take effect on the code paths in this module.
* :meth:`ClipboardManager._delayed_restore` /
  :meth:`ClipboardManager.restore_now` remain as thin delegator
  methods on the class (preserving the public API and the
  ``inspect.signature(ClipboardManager._delayed_restore)`` contract
  pinned by ``tests/test_clipboard_restore_args.py`` and
  ``tests/test_clipboard_borrow_restore.py``).
"""

from __future__ import annotations

import atexit
import contextlib
import threading
from typing import Any

from voice_typer.server import clipboard as _cb

# ─── module-level registry of pending delayed-restores ────────────────
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
#
# Hard cap on the in-flight pending-restores list. Under normal
# use (``_restore_delay_ms=150``), entries live ~150 ms so the
# steady-state size is bounded by paste rate × 0.15 s — typically 1-2
# entries. BUT: (a) ``clipboard_restore_delay_ms`` is user-configurable
# with no upper bound — a user setting it to 5000 ms creates a 5 s
# window per entry; (b) if the daemon thread fails to start, a hang in
# ``_delayed_restore`` leaves the entry forever; (c) each entry holds a
# ``ClipboardSnapshot`` whose ``items`` list can be 16 MB × N formats,
# so the old cap of 64 allowed ~1 GB of pinned snapshots in the worst
# case. 8 bounds the worst case at ~128 MB — still far above the 1-2
# entries normal use ever holds (the force-restore-on-overflow path
# fires only in the runaway conditions above: a leaked daemon thread or
# a user-set multi-second restore delay), but small enough that a
# runaway cannot pin gigabytes of clipboard snapshots.
_pending_restores: list[tuple[Any, Any, str, float]] = []
_pending_restores_lock = threading.Lock()
_MAX_PENDING_RESTORES = 8


def _force_restore_pending_at_exit() -> None:
    """atexit handler — force-restore any pending snapshots.

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
            # beats false-positive restore — see ).
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
    except RuntimeError:  # pragma: no cover — atexit.register only fails if interpreter is shutting down
        # ``RuntimeError`` is raised when atexit hooks fire during
        # interpreter shutdown. Previously a broad
        # ``except Exception: pass``.
        pass


def _delayed_restore_impl(
    manager: Any,
    snapshot: Any,
    pasted_text: str,
    delay: float,
    pending_entry: Any = None,
) -> None:
    """Restore a snapshot after a delay. Runs on a daemon thread.

    ADR-0010 §5.3 / DP3.

    Defensive check: if the clipboard no longer contains
    ``pasted_text`` (user copied something else, or target app
    rewrote it), skip restore to avoid clobbering the new content.

     fix: the ``pending_entry`` parameter is the tuple that
    ``paste()`` appended to ``_pending_restores`` before spawning
    this daemon thread. The entry is removed from the list (under
    the lock) BEFORE ``snapshot.restore()`` runs so the list does
    not grow unboundedly across many paste invocations (memory
    leak) and so the atexit handler does not double-restore an
    already-restored snapshot. The default ``None`` preserves
    backward compatibility with legacy 3-arg direct calls (e.g.
    existing tests at
    ``tests/test_clipboard_borrow_restore.py:301/318``).

     fix (session-DE, Medium, Concurrency): the pre-fix code
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
    atexit (ValueError on remove), short-circuit — atexit will restore
    synchronously.

    This is the implementation backing
    :meth:`ClipboardManager._delayed_restore`; the method on the
    class is a thin delegator so the public API and the
    ``inspect.signature`` contract are preserved.
    """
    try:
        _cb.time.sleep(delay)

        # claim the pending_entry under the lock BEFORE
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
                            "[CLIPBOARD-AUDIT] Pending entry already claimed by atexit — skipping daemon restore"
                        )
                        return  #  short-circuit
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
        #  (session-4): clear the cached last-copied text
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
        # the ``pending_entry`` removal has moved to BEFORE
        # ``snapshot.restore()`` (above). The ``finally`` block
        # now only clears ``_last_copied_text``.
        #
        # defensively re-attempt the ``pending_entry`` removal
        # here (under the lock, suppressing ValueError if atexit or
        # the pre-restore path already claimed it). This closes two
        # leak windows left by the  move: (1) ``sleep(delay)``
        # raises before the pre-restore removal runs; (2) the
        # catastrophic-lock-failure broad ``except`` at the
        # pre-restore path leaves the entry in the list while
        # proceeding with restore. Without this defensive remove,
        # each such orphan pins the ClipboardSnapshot (up to 16MB x
        # N formats) for the process lifetime.
        try:
            manager._last_copied_text = ""
        except Exception:  # pragma: no cover — attribute access broken
            _cb.log.debug("[CLIPBOARD] Failed to clear _last_copied_text", exc_info=True)
        if pending_entry is not None:
            with _pending_restores_lock, contextlib.suppress(ValueError):
                _pending_restores.remove(pending_entry)


def _restore_now_impl(manager: Any, snapshot: Any) -> None:
    """Restore a snapshot immediately (no paste keystroke, no delay).

    ADR-0010 §5.4 / DP2.

    Used when copy() borrowed the clipboard but no paste follows
    (``paste_on_stop = False``). This is the critical fix for the
    data-loss bug where ``paste_on_stop=False`` permanently
    destroyed the user's clipboard.

    This is the implementation backing
    :meth:`ClipboardManager.restore_now`; the method on the class is a
    thin delegator so the public API is preserved.
    """
    if snapshot is None:
        return
    try:
        snapshot.restore()
        _cb.log.info("[CLIPBOARD-AUDIT] Restored snapshot immediately (no paste)")
    except Exception:
        _cb.log.exception("[CLIPBOARD] Immediate restore failed")
    finally:
        #  (session-DE, Medium, Privacy): clear the cached
        # last-copied text. ``restore_now()`` is the path used when
        # ``paste_on_stop=False`` (no paste keystroke follows), so
        # no ``_delayed_restore`` daemon thread will run to clear
        # the cached text in its ``finally`` block. Without this
        # clear, the dictated PII would remain in process memory
        # for the process lifetime.
        #
        # Best-effort: never raise from the finally block.
        try:
            manager._last_copied_text = ""
        except Exception:  # pragma: no cover — attribute access broken
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
