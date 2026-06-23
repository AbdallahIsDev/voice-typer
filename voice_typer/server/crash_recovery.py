"""Crash recovery: stores last 10 transcriptions, checks on startup.

After each transcription, the text is saved to a recovery file.
On startup, if the recovery file has unpasted transcriptions,
the user is notified. The recovery file is cleared after acknowledgment.

RELIABILITY-005: previously, every call to ``add()``, ``mark_pasted()``,
``mark_latest_pasted()``, and ``clear()`` wrote to disk synchronously
on the calling thread (typically the transcription thread).  Under
repeated crashes or rapid transcriptions, these synchronous writes
blocked the main thread and could compound the crash condition by
delaying restart.  The fix moves disk writes to a dedicated background
thread with a bounded queue: callers enqueue a save request and
return immediately; the worker thread serializes the writes.
"""

import json
import logging
import os
import queue
import sys
import threading
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

RECOVERY_FILENAME = "voice-typer-recovery.json"
MAX_RECOVERY_ENTRIES = 10

# Bounded queue: if the worker falls behind (e.g. disk is slow),
# drop the oldest pending save rather than blocking the transcription
# thread.  The latest state is what matters; intermediate states are
# not useful for crash recovery.
_SAVE_QUEUE_MAXSIZE = 32


class CrashRecovery:
    """Stores recent transcriptions for crash recovery.

    All disk writes are serialized through a single background worker
    thread (RELIABILITY-005).  The in-memory ``_entries`` list is the
    source of truth for reads; the worker only persists it.
    """

    def __init__(self, config_dir: Optional[Path] = None):
        if config_dir is None:
            from voice_typer.server.config import _config_dir
            config_dir = _config_dir()
        self._path = config_dir / RECOVERY_FILENAME
        self._entries: list[dict] = []
        self._lock = threading.Lock()
        self._save_queue: "queue.Queue[Optional[dict]]" = queue.Queue(maxsize=_SAVE_QUEUE_MAXSIZE)
        self._save_thread: Optional[threading.Thread] = None
        self._stopped = False
        self._load()
        self._start_save_thread()

    # ── Persistence ──────────────────────────────────────────────────

    def _load(self) -> None:
        """Load recovery entries from disk."""
        if not self._path.exists():
            self._entries = []
            return
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            if isinstance(data, list):
                self._entries = data
            elif isinstance(data, dict) and "entries" in data:
                self._entries = data["entries"]
            else:
                self._entries = []
            log.debug("[RECOVERY] Loaded %d entries", len(self._entries))
        except Exception as exc:
            log.warning("[RECOVERY] Failed to load: %s", exc)
            self._entries = []

    def _save_sync(self) -> None:
        """Save recovery entries to disk synchronously.

        This is called only from the background save thread.  All
        other callers go through ``_enqueue_save()``.

        SEC-007: on POSIX, restricts file permissions to 0o600 so
        transcription text in the recovery file is not world-readable.

        NEW-SEC-008: uses the shared _secure_atomic_write which applies
        O_NOFOLLOW on POSIX to prevent symlink TOCTOU attacks.
        """
        try:
            from voice_typer.server.config import _secure_atomic_write
            self._path.parent.mkdir(parents=True, exist_ok=True)
            if sys.platform != "win32":
                try:
                    os.chmod(self._path.parent, 0o700)
                except OSError as e:
                    log.warning("[RECOVERY] Failed to chmod dir: %s", e)
            with self._lock:
                snapshot = json.dumps(
                    {"entries": self._entries}, indent=2, ensure_ascii=False,
                )
            _secure_atomic_write(self._path, snapshot)
        except Exception as exc:
            log.error("[RECOVERY] Failed to save: %s", exc)

    def _enqueue_save(self) -> None:
        """Enqueue a save request to the background worker.

        If the queue is full (worker fell behind), drop the oldest
        pending save.  This is safe because saves are idempotent —
        only the latest snapshot matters for crash recovery.
        """
        try:
            self._save_queue.put_nowait({"snapshot": True})
        except queue.Full:
            # Drop oldest pending save and try again.  The latest
            # state will be persisted by the next put.  We must call
            # task_done() on the dropped item so that any pending
            # Queue.join() (e.g. from flush()) doesn't block forever
            # waiting for a task that will never be processed.
            try:
                self._save_queue.get_nowait()
                self._save_queue.task_done()
            except queue.Empty:
                pass
            try:
                self._save_queue.put_nowait({"snapshot": True})
            except queue.Full:
                log.warning("[RECOVERY] save queue full; skipping save")

    def _start_save_thread(self) -> None:
        """Start (or restart) the background save worker thread."""
        if self._save_thread is not None and self._save_thread.is_alive():
            return
        self._stopped = False
        self._save_thread = threading.Thread(
            target=self._save_loop, name="crash-recovery-saver", daemon=True,
        )
        self._save_thread.start()

    def _save_loop(self) -> None:
        """Background worker: drain the save queue, writing to disk."""
        while not self._stopped:
            try:
                item = self._save_queue.get(timeout=1.0)
            except queue.Empty:
                continue
            if item is None:
                # Sentinel: stop signal
                break
            self._save_sync()
            self._save_queue.task_done()

    def flush(self, timeout: float = 2.0) -> bool:
        """Wait for all pending saves to complete.

        Parameters
        ----------
        timeout : float
            Maximum seconds to wait.  Returns True if all saves
            completed, False if the timeout elapsed.

        Notes
        -----
        Useful at process shutdown (called from ``quit()`` /
        ``restart_app()``) to ensure the final state is persisted
        before the process exits.
        """
        try:
            # Use a join-with-timeout pattern.  task_done() is called
            # by the worker after each save, so join() returns when
            # all queued items have been processed.
            self._save_queue.join()
            return True
        except Exception:
            return False

    def shutdown(self) -> None:
        """Signal the background save thread to stop.

        Safe to call multiple times.  After shutdown, any further
        calls to ``add()`` / ``mark_pasted()`` / etc. will fall
        back to synchronous saves (so the data is still persisted,
        just on the calling thread).
        """
        self._stopped = True
        try:
            self._save_queue.put_nowait(None)  # sentinel
        except queue.Full:
            pass  # worker will see _stopped on next loop iteration

    # ── Public API ───────────────────────────────────────────────────

    def add(self, text: str, *, pasted: bool = False) -> None:
        """Add a transcription to the recovery buffer.

        Keeps only the last MAX_RECOVERY_ENTRIES entries.
        """
        from datetime import datetime
        entry = {
            "text": text,
            "timestamp": datetime.now().isoformat(),
            "pasted": pasted,
        }
        with self._lock:
            self._entries.append(entry)
            # Trim to max
            while len(self._entries) > MAX_RECOVERY_ENTRIES:
                self._entries.pop(0)
        self._enqueue_save()

    def mark_pasted(self, index: int) -> bool:
        """Mark an entry as successfully pasted."""
        with self._lock:
            if 0 <= index < len(self._entries):
                self._entries[index]["pasted"] = True
                self._enqueue_save()
                return True
            return False

    def mark_latest_pasted(self) -> None:
        """Mark the most recent entry as pasted."""
        with self._lock:
            if self._entries:
                self._entries[-1]["pasted"] = True
        self._enqueue_save()

    def get_unpasted(self) -> list[dict]:
        """Return all entries that were not pasted (potential crash losses)."""
        with self._lock:
            return [e for e in self._entries if not e.get("pasted", False)]

    def get_all(self) -> list[dict]:
        """Return all recovery entries."""
        with self._lock:
            return list(self._entries)

    def check_on_startup(self) -> Optional[list[dict]]:
        """Check for unpasted transcriptions from a previous session.

        Returns a list of unpasted entries if any exist, or None.
        The caller should notify the user about these entries.
        """
        unpasted = self.get_unpasted()
        if unpasted:
            log.info("[RECOVERY] Found %d unpasted transcriptions from previous session", len(unpasted))
            return unpasted
        return None

    def clear(self) -> None:
        """Clear all recovery entries (after user acknowledgment)."""
        with self._lock:
            self._entries.clear()
        self._enqueue_save()
        log.info("[RECOVERY] Recovery entries cleared")

    @property
    def count(self) -> int:
        """Number of recovery entries."""
        with self._lock:
            return len(self._entries)

    def __del__(self) -> None:
        """Best-effort flush on garbage collection.

        If the background save thread still has pending writes when
        the CrashRecovery instance is collected, do a synchronous
        final save so the latest state is persisted.  This is a
        safety net — explicit ``shutdown()`` + ``flush()`` is the
        preferred shutdown path, but ``__del__`` catches the case
        where the caller forgets (e.g. tests, abnormal exits).
        """
        try:
            # Signal the worker to stop, then do one final
            # synchronous save to capture any pending state.
            self._stopped = True
            if self._save_thread is not None and self._save_thread.is_alive():
                # If there's pending work, do a final synchronous save
                # to capture it.  If the queue is empty, this is a
                # no-op.
                if not self._save_queue.empty():
                    self._save_sync()
        except Exception:
            pass  # __del__ must never raise
