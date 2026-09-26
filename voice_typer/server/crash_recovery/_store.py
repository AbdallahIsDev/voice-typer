"""Crash-recovery store read/write."""

import collections
import contextlib
import logging
import queue
import threading
from pathlib import Path
from typing import Any

from voice_typer.server.crash_recovery._io import (
    _LEGACY_RECOVERY_FILENAME,
    _SAVE_QUEUE_MAXSIZE,
    MAX_RECOVERY_ENTRIES,
    RECOVERY_FILENAME,
    _RecoveryIO,
)
from voice_typer.server.crash_recovery._worker import _LIVE_INSTANCES, _SaveWorker

# Logger name pinned to the package name (C-LOG-1). See _worker.py.
log = logging.getLogger("voice_typer.server.crash_recovery")


class CrashRecovery(_SaveWorker, _RecoveryIO):
    """Stores recent transcriptions for crash recovery."""

    def __init__(
        self,
        config_dir: Path | None = None,
        thread_registry: Any | None = None,
    ):
        if config_dir is None:
            from voice_typer.server.config import _config_dir

            config_dir = _config_dir()
        self._path = config_dir / RECOVERY_FILENAME
        # One-time migration of the legacy prefixed name
        _legacy = config_dir / _LEGACY_RECOVERY_FILENAME
        if _legacy.exists() and not self._path.exists():
            try:
                _legacy.rename(self._path)
                log.debug(
                    "[RECOVERY] migrated legacy %s -> %s",
                    _LEGACY_RECOVERY_FILENAME,
                    RECOVERY_FILENAME,
                )
            except OSError as exc:
                log.debug("[RECOVERY] legacy file migration failed: %s", exc)
        # pop(0)`` trim in ``add()`` is now a defensive no-op (kept for
        self._entries: collections.deque = collections.deque(maxlen=MAX_RECOVERY_ENTRIES)
        self._lock = threading.Lock()
        # Serializes _save_sync() disk writes so that concurrent
        self._save_lock = threading.Lock()
        self._save_queue: queue.Queue[dict | None] = queue.Queue(maxsize=_SAVE_QUEUE_MAXSIZE)
        self._save_thread: threading.Thread | None = None
        self._stopped = False
        # ``_dir_ensured`` guards the per-save ``os.chmod``
        self._dir_ensured = False
        # ``_final_save_done`` deduplicates the final
        self._final_save_done = False
        # THREAD-REGISTRY: optional central registry for shutdown
        self._thread_registry = thread_registry
        # ``_load()`` is deferred out of ``__init__`` (which runs
        self._loaded = False
        self._start_save_thread()
        # Atexit flushing is now handled by the SINGLE module-level
        _LIVE_INSTANCES.add(self)

    # ── Persistence (``_load`` / ``_quarantine_corrupt`` / ``_save_sync``)

    def add(
        self,
        text: str,
        *,
        pasted: bool = False,
        cycle_id: str | None = None,
    ) -> None:
        """Add a transcription to the recovery buffer."""
        from datetime import datetime

        entry: dict = {
            "text": text,
            "timestamp": datetime.now().isoformat(),
            "pasted": pasted,
        }
        if cycle_id is not None:
            entry["cycle_id"] = cycle_id
        with self._lock:
            self._entries.append(entry)
            # Trim to max
            while len(self._entries) > MAX_RECOVERY_ENTRIES:
                self._entries.popleft()
        self._enqueue_save()

    def mark_pasted(self, index: int) -> bool:
        """Mark an entry as successfully pasted."""
        found = False
        with self._lock:
            if 0 <= index < len(self._entries):
                self._entries[index]["pasted"] = True
                found = True
        if found:
            self._enqueue_save()
        return found

    def mark_latest_pasted(self) -> None:
        """Mark the most recent entry as pasted."""
        with self._lock:
            if self._entries:
                self._entries[-1]["pasted"] = True
        self._enqueue_save()

    def get_unpasted(self) -> list[dict]:
        """Return all entries that were not pasted (potential crash losses).

        Returns:
        """
        self._load()
        with self._lock:
            return [e for e in self._entries if not e.get("pasted", False)]

    def get_all(self) -> list[dict]:
        """Return all recovery entries.

        Returns:
        """
        self._load()
        with self._lock:
            return list(self._entries)

    def check_on_startup(self) -> list[dict] | None:
        """Check for unpasted transcriptions from a previous session.

        Returns a list of unpasted entries if any exist, or None.
        The caller should notify the user about these entries so they
        can recover the text that was lost due to a crash or forced close.

        Returns:
            List of unpasted entry dicts, or None if no unpasted entries.

        This method (called from ``StartupSequence.run`` on the
        startup daemon thread) is the PRIMARY load site for recovery
        entries. ``_load()`` was moved here from ``__init__`` so the
        synchronous disk read happens on the background thread rather
        than blocking the main thread during ``LausuApp.__init__``.
        The ``_loaded`` guard in ``_load()`` makes this a no-op if the
        entries were already loaded (e.g. by an earlier read-accessor
        lazy-load call).
        """
        # Load recovery entries from disk. Deferred from
        self._load()
        # Detect interrupted dictations: if the .dictation-in-flight sentinel
        self._detect_and_notify_lost_dictation()
        unpasted = self.get_unpasted()
        if unpasted:
            log.info("[RECOVERY] Found %d unpasted transcriptions from previous session", len(unpasted))
            return unpasted
        return None

    def _detect_and_notify_lost_dictation(self) -> None:
        """Detect if a dictation was in-flight when the previous process crashed."""
        with contextlib.suppress(Exception):
            from voice_typer.server import event_bus
            from voice_typer.server._paths import config_dir as _config_dir

            _sentinel = _config_dir() / ".dictation-in-flight"
            if _sentinel.exists():
                # Read the cycle_id BEFORE deleting the sentinel so we
                cycle_id = ""
                with contextlib.suppress(Exception):
                    # Read through ``_secure_read_text`` (POSIX
                    from voice_typer.server.config import _secure_read_text

                    cycle_id = _secure_read_text(_sentinel).strip()
                # Delete FIRST so a publish failure can't cause a re-fire loop.
                _sentinel.unlink(missing_ok=True)
                # Look up matching unpasted entries. ``_lock`` guards the
                with self._lock:
                    recoverable = any(
                        bool(cycle_id) and e.get("cycle_id") == cycle_id and not e.get("pasted", False)
                        for e in self._entries
                    )
                recovery_type = "text_only" if recoverable else "none"
                log.warning(
                    "[RECOVERY] Detected interrupted dictation from previous session "
                    "(cycle_id=%r), emitting dictation_lost event "
                    "(recoverable=%s, recovery_type=%s)",
                    cycle_id,
                    recoverable,
                    recovery_type,
                )
                event_bus.publish(
                    {
                        "type": "dictation_lost",
                        "data": {
                            "message": (
                                "A dictation was interrupted by a crash. "
                                "Partial text may be recoverable if the crash "
                                "was soft; no audio is recoverable."
                            ),
                            "recoverable": recoverable,
                            "recovery_type": recovery_type,
                            "cycle_id": cycle_id,
                        },
                    }
                )

    def clear(self) -> None:
        """Clear all recovery entries after user acknowledgment.

        Removes all stored entries and saves the empty state to disk.
        """
        with self._lock:
            self._entries.clear()
            # Mark as loaded so a subsequent lazy ``_load()`` (from
            self._loaded = True
        self._enqueue_save()
        log.info("[RECOVERY] Recovery entries cleared")

    @property
    def count(self) -> int:
        """Number of recovery entries."""
        self._load()
        with self._lock:
            return len(self._entries)

    def __del__(self) -> None:
        """Best-effort GC-time flush, delegates to ``_cleanup_*`` helpers."""
        try:
            self._cleanup_signal_stop()
            self._cleanup_flush_pending()
        except BaseException:
            # Defense-in-depth: each helper owns its own try/except,
            pass

    def _cleanup_signal_stop(self) -> None:
        """``__del__`` helper: signal the background worker to stop."""
        try:  # noqa: SIM105, explicit try/except preserves the
            #       "never raise from GC" pattern's visual parity with
            self._stopped = True
        except BaseException:
            # ``__del__``-path helpers must NEVER raise, see
            pass

    def _cleanup_flush_pending(self) -> None:
        """``__del__`` helper: synchronously save any pending state."""
        try:
            # acquire ``_lock`` for the empty-check so a
            with self._lock:
                has_entries = bool(self._entries)
            # Save whenever there's any data to lose.  This covers
            if has_entries:
                self._save_sync(durability=True)
        except BaseException:
            # ``__del__`` must NEVER raise: including for
            pass

    def entries_metadata_snapshot(self) -> list[dict]:
        """Return a metadata-only snapshot of the recovery entries."""
        self._load()
        with self._lock:
            return [
                {
                    "timestamp": e.get("timestamp"),
                    "pasted": e.get("pasted", False),
                    "text_length": len(e.get("text", "")),
                }
                for e in self._entries
            ]
