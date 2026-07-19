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

import contextlib
import json
import logging
import os
import queue
import threading
from pathlib import Path
from typing import Any

from voice_typer.server.platform_utils import is_windows

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

    def __init__(
        self,
        config_dir: Path | None = None,
        thread_registry: Any | None = None,
    ):
        if config_dir is None:
            from voice_typer.server.config import _config_dir

            config_dir = _config_dir()
        self._path = config_dir / RECOVERY_FILENAME
        self._entries: list[dict] = []
        self._lock = threading.Lock()
        # Serializes _save_sync() disk writes so that concurrent
        # callers (the background worker + any post-shutdown sync
        # fallback callers) don't trample each other on the file
        # write.  Distinct from ``_lock`` (which only guards the
        # in-memory ``_entries`` list) so reads/writes to
        # ``_entries`` aren't blocked while the disk I/O runs.
        # RELIABILITY-005 + a-review Finding A1: post-shutdown calls
        # to ``add()`` / ``mark_pasted()`` / etc. fall back to
        # synchronous saves (per the documented shutdown() contract);
        # this lock makes that fallback safe under concurrency.
        self._save_lock = threading.Lock()
        self._save_queue: queue.Queue[dict | None] = queue.Queue(maxsize=_SAVE_QUEUE_MAXSIZE)
        self._save_thread: threading.Thread | None = None
        self._stopped = False
        # THREAD-REGISTRY: optional central registry for shutdown
        # coordination. When provided, the crash-recovery-saver thread
        # is registered so ``shutdown_all()`` can join it during
        # ``VoiceTyperApp.quit()``. We register with ``stop_event=None``
        # because the existing ``shutdown()`` method (called by
        # ``_do_cleanup()``) handles the actual stop via the
        # ``_stopped`` boolean + None sentinel on the queue. The
        # registry's ``shutdown_all()`` just verifies the thread is
        # tracked; the existing per-site cleanup handles the actual
        # shutdown. When ``None`` (e.g. in unit tests), behavior is
        # unchanged.
        self._thread_registry = thread_registry
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

        a-review Finding A1: ``_save_lock`` serializes the disk write
        so that the background worker and any post-shutdown sync
        fallback callers don't race on the file.  ``_lock`` is still
        acquired only for the in-memory snapshot so reads of
        ``_entries`` aren't blocked during I/O.
        """
        with self._save_lock:
            try:
                from voice_typer.server.config import _secure_atomic_write

                self._path.parent.mkdir(parents=True, exist_ok=True)
                if not is_windows():
                    try:
                        os.chmod(self._path.parent, 0o700)
                    except OSError as e:
                        log.warning("[RECOVERY] Failed to chmod dir: %s", e)
                with self._lock:
                    snapshot = json.dumps(
                        {"entries": self._entries},
                        indent=2,
                        ensure_ascii=False,
                    )
                _secure_atomic_write(self._path, snapshot)
            except Exception as exc:
                log.error("[RECOVERY] Failed to save: %s", exc)

    def _enqueue_save(self) -> None:
        """Enqueue a save request to the background worker.

        If the queue is full (worker fell behind), drop the oldest
        pending save.  This is safe because saves are idempotent —
        only the latest snapshot matters for crash recovery.

        a-review Finding A1: after ``shutdown()`` the worker thread
        has exited, so enqueuing would silently lose the mutation.
        Fall back to a synchronous save (serialized via
        ``_save_lock``) to honor the documented shutdown() contract:
        "After shutdown, any further calls to ``add()`` /
        ``mark_pasted()`` / etc. will fall back to synchronous saves".
        """
        if self._stopped:
            # Worker has exited (or never started) — persist on the
            # caller's thread.  ``_save_sync`` takes ``_save_lock``
            # so concurrent post-shutdown callers serialize cleanly.
            self._save_sync()
            return
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
        """Start (or restart) the background save worker thread.

        THREAD-REGISTRY: when a registry was provided to ``__init__``,
        the worker thread is registered so ``shutdown_all()`` can join
        it during ``VoiceTyperApp.quit()``. We register with
        ``stop_event=None`` because the existing ``shutdown()`` method
        handles the actual stop via the ``_stopped`` boolean + None
        sentinel on the queue. The registry's ``shutdown_all()`` just
        verifies the thread is tracked; the existing per-site cleanup
        (``flush()`` + ``shutdown()``) handles the actual shutdown.
        """
        if self._save_thread is not None and self._save_thread.is_alive():
            return
        self._stopped = False
        self._save_thread = threading.Thread(
            target=self._save_loop,
            name="crash-recovery-saver",
            daemon=True,
        )
        self._save_thread.start()
        if self._thread_registry is not None:
            self._thread_registry.register(
                name="crash-recovery-saver",
                thread=self._save_thread,
                stop_event=None,
                # Short timeout: shutdown_all() can't actually stop this
                # thread (no stop_event), so we just verify it's tracked.
                # The existing ``_do_cleanup()`` path calls ``flush()`` +
                # ``shutdown()`` which gracefully drains and stops the
                # worker. A 0.5s join gives the worker a brief window to
                # exit naturally if it happens to be at a checkpoint.
                join_timeout=0.5,
            )

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
            if isinstance(item, dict) and "flush_event" in item:
                # RW-4: flush barrier sentinel.  All saves queued
                # before this sentinel have now been processed, so
                # signal the waiting flush() caller.  Do NOT treat
                # this as a save — it is a barrier, not a snapshot
                # request.  The worker continues running and remains
                # ready for more items.
                event = item.get("flush_event")
                if event is not None:
                    with contextlib.suppress(Exception):
                        event.set()
                self._save_queue.task_done()
                continue
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

        RW-4: previously this called ``Queue.join()``, which has no
        ``timeout`` parameter in the stdlib — if the worker stalled
        (disk full, NFS hang, fsync on a dying SSD, antivirus lock
        on Windows), ``flush()`` blocked forever, preventing clean
        shutdown.  Now we enqueue a sentinel carrying a
        ``threading.Event``; the worker sets the event when it
        reaches the sentinel (meaning all prior saves are done).
        We wait on the event with the timeout, so the timeout is
        actually enforced.  The worker thread is NOT killed when
        the timeout fires — it keeps running and will eventually
        process the sentinel (the event.set() becomes a no-op).
        """
        event = threading.Event()
        sentinel = {"flush_event": event}
        # Enqueue the sentinel.  If the queue is full (worker is way
        # behind), try to make room by dropping the oldest pending
        # item — matching the _enqueue_save strategy.  We call
        # task_done() on the dropped item to keep the queue's
        # unfinished_tasks counter consistent.
        try:
            self._save_queue.put_nowait(sentinel)
        except queue.Full:
            try:
                self._save_queue.get_nowait()
                self._save_queue.task_done()
            except queue.Empty:
                pass
            try:
                self._save_queue.put_nowait(sentinel)
            except queue.Full:
                log.warning("[RECOVERY] flush: save queue full; cannot enqueue sentinel")
                return False
        completed = event.wait(timeout=timeout)
        if not completed:
            log.warning(
                "[RECOVERY] flush timed out after %.2fs; pending saves may be lost",
                timeout,
            )
            return False
        return True

    def shutdown(self) -> None:
        """Signal the background save thread to stop.

        Safe to call multiple times.  After shutdown, any further
        calls to ``add()`` / ``mark_pasted()`` / etc. will fall
        back to synchronous saves (so the data is still persisted,
        just on the calling thread).

        S-6: ``_save_thread`` is now joined with a short timeout
        after the sentinel is enqueued, so the thread is properly
        tracked and doesn't leak in test start/stop cycles.

        a-review Finding A3: after joining the worker, do one final
        ``_save_sync()`` so any mutations queued or in-flight when
        shutdown was called are guaranteed persisted.  This is cheap
        insurance on top of the worker's natural drain — and it also
        covers the rare window where a concurrent ``add()`` races
        with shutdown() and enqueues after the sentinel.
        """
        self._stopped = True
        with contextlib.suppress(queue.Full):
            self._save_queue.put_nowait(None)  # sentinel
        if self._save_thread is not None and self._save_thread.is_alive():
            self._save_thread.join(timeout=1.0)
        # Final synchronous save after the worker has exited.  The
        # worker should have drained the queue, but if it timed out
        # or if a concurrent caller raced in a final mutation, this
        # guarantees the latest ``_entries`` state is on disk.
        # ``_save_lock`` makes this safe even if a post-shutdown
        # ``add()`` is in flight on another thread.
        self._save_sync()

    # ── Public API ───────────────────────────────────────────────────

    def add(self, text: str, *, pasted: bool = False) -> None:
        """Add a transcription to the recovery buffer.

        Keeps only the last MAX_RECOVERY_ENTRIES entries.

        Args:
            text: The transcribed text to store.
            pasted: Whether the text was successfully pasted.
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
        """Mark an entry as successfully pasted.

        Args:
            index: The index of the entry to mark.

        Returns:
            True if the entry was found and marked, False otherwise.
        """
        with self._lock:
            if 0 <= index < len(self._entries):
                self._entries[index]["pasted"] = True
                self._enqueue_save()
                return True
            return False

    def mark_latest_pasted(self) -> None:
        """Mark the most recent entry as pasted.

        This is called after a successful paste operation to indicate
        the transcription was delivered to the target application.
        """
        with self._lock:
            if self._entries:
                self._entries[-1]["pasted"] = True
        self._enqueue_save()

    def get_unpasted(self) -> list[dict]:
        """Return all entries that were not pasted (potential crash losses).

        Returns:
            List of entry dicts with 'text', 'timestamp', and 'pasted' keys.
        """
        with self._lock:
            return [e for e in self._entries if not e.get("pasted", False)]

    def get_all(self) -> list[dict]:
        """Return all recovery entries.

        Returns:
            List of all entry dicts (copies, safe to modify).
        """
        with self._lock:
            return list(self._entries)

    def check_on_startup(self) -> list[dict] | None:
        """Check for unpasted transcriptions from a previous session.

        Returns a list of unpasted entries if any exist, or None.
        The caller should notify the user about these entries so they
        can recover the text that was lost due to a crash or forced close.

        Returns:
            List of unpasted entry dicts, or None if no unpasted entries.
        """
        unpasted = self.get_unpasted()
        if unpasted:
            log.info("[RECOVERY] Found %d unpasted transcriptions from previous session", len(unpasted))
            return unpasted
        return None

    def clear(self) -> None:
        """Clear all recovery entries after user acknowledgment.

        Removes all stored entries and saves the empty state to disk.
        """
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

        a-review Finding A3: previously this only saved if
        ``_save_thread.is_alive() and not _save_queue.empty()`` —
        which skipped the save entirely after ``shutdown()`` killed
        the worker, dropping any post-shutdown mutations on GC.
        Now we save whenever ``_entries`` is non-empty, regardless
        of worker state.  ``_save_lock`` serializes against any
        in-flight worker save.  The whole body stays wrapped in
        try/except so interpreter shutdown never raises from GC.
        """
        try:
            # Signal the worker to stop, then do one final
            # synchronous save to capture any pending state.
            self._stopped = True
            # Save whenever there's any data to lose.  This covers
            # both "worker alive with pending queue items" (worker
            # is mid-save; _save_lock serializes) and "worker dead
            # after shutdown() with post-shutdown mutations" (the
            # Finding A3 regression).  If _entries is empty, this
            # is a no-op.
            if self._entries:
                self._save_sync()
        except Exception:
            pass  # __del__ must never raise

    def create_diagnostic_bundle(self) -> str | None:
        """PROD-010: Create a diagnostic bundle zip file.

        Collects:
          - voice-typer.log
          - config.json (redacted — API keys removed)
          - System info (platform, Python version, GPU info)
          - Model info (loaded model, device)
          - Crash recovery entries

        Returns the path to the created zip file, or None on failure.
        """
        import zipfile
        from datetime import datetime

        try:
            from voice_typer.server.config import _config_dir

            config_dir = _config_dir()
        except Exception:
            config_dir = self._path.parent

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        bundle_name = f"voice-typer-diagnostics-{timestamp}.zip"
        bundle_path = config_dir / bundle_name

        try:
            with zipfile.ZipFile(str(bundle_path), "w", zipfile.ZIP_DEFLATED) as zf:
                # 1. Log file
                log_path = config_dir / "voice-typer.log"
                if log_path.exists():
                    with contextlib.suppress(Exception):
                        zf.write(str(log_path), "voice-typer.log")

                # 2. Config (redacted)
                config_path = config_dir / "config.json"
                if config_path.exists():
                    try:
                        import json

                        # NF-R18-1: iterate over the canonical
                        # ``_SECRET_CONFIG_FIELDS`` set (defined in
                        # ``ipc_server.py``) instead of a hardcoded tuple
                        # that missed ``cloud_api_key`` and ``groq_api_key``.
                        # Pre-fix, the diagnostic bundle leaked 2 of the 5
                        # API keys to the zip file (and thus to any bug
                        # report the user attached it to). The shared
                        # frozenset is the single source of truth — any
                        # future secret field added there is automatically
                        # redacted here too.
                        from voice_typer.server.ipc_server import (
                            _SECRET_CONFIG_FIELDS,
                        )

                        raw = config_path.read_text(encoding="utf-8")
                        data = json.loads(raw)
                        # Redact sensitive keys
                        for key in _SECRET_CONFIG_FIELDS:
                            if key in data and data[key]:
                                data[key] = "[REDACTED]"
                        zf.writestr("config.json", json.dumps(data, indent=2))
                    except Exception:
                        log.debug(
                            "[CRASH-RECOVERY] failed to redact+write config.json into diagnostic bundle",
                            exc_info=True,
                        )

                # 3. System info
                import platform
                import sys

                sys_info = [
                    f"Platform: {platform.platform()}",
                    f"Python: {sys.version}",
                    f"Architecture: {platform.machine()}",
                    f"Processor: {platform.processor()}",
                ]
                # GPU info
                try:
                    import torch

                    sys_info.append(f"CUDA available: {torch.cuda.is_available()}")
                    if torch.cuda.is_available():
                        sys_info.append(f"CUDA version: {torch.version.cuda}")
                        sys_info.append(f"GPU: {torch.cuda.get_device_name(0)}")
                        _gpu_props = torch.cuda.get_device_properties(0)
                        # TASK-14: ``_CudaDeviceProperties`` is created
                        # dynamically by torch (``_dummy_type`` when CUDA
                        # is not compiled in), so its attribute surface
                        # is invisible to pyrefly.  Use ``getattr`` to
                        # read ``total_mem`` (bytes) without a static
                        # ``missing-attribute`` error.
                        _total_mem = getattr(_gpu_props, "total_mem", 0)
                        _gpu_mem = _total_mem // 1048576
                        sys_info.append(f"GPU memory: {_gpu_mem} MB")
                except ImportError:
                    sys_info.append("PyTorch not installed")
                except Exception as exc:
                    sys_info.append(f"GPU info error: {exc}")
                zf.writestr("system_info.txt", "\n".join(sys_info))

                # 4. Model info
                try:
                    from voice_typer.server.config import Config

                    cfg = Config.load()
                    model_info = [
                        f"Model: {cfg.model_size}",
                        f"Device: {cfg.device}",
                    ]
                    zf.writestr("model_info.txt", "\n".join(model_info))
                except Exception:
                    pass

                # 5. Crash recovery entries
                import json as _json

                with self._lock:
                    entries_json = _json.dumps(
                        {"entries": self._entries},
                        indent=2,
                        ensure_ascii=False,
                    )
                zf.writestr("crash_recovery.json", entries_json)

                # 6. Prewarm health check (Task 4)
                # Bundles the full prewarm status + sentinel/PID file
                # contents so support engineers can diagnose prewarm
                # issues without asking the user to run --status manually.
                try:
                    from voice_typer.server.prewarm import (
                        _pid_file_path,
                        _sentinel_path,
                        get_prewarm_status,
                    )

                    prewarm_data = get_prewarm_status()
                    # Add the raw sentinel + PID file contents + paths
                    # for full diagnostics.
                    prewarm_data["sentinel_path"] = str(_sentinel_path())
                    prewarm_data["pid_file_path"] = str(_pid_file_path())
                    # Read sentinel file raw contents (if it exists).
                    sentinel = _sentinel_path()
                    if sentinel.exists():
                        try:
                            prewarm_data["sentinel_contents"] = sentinel.read_text()
                        except OSError as e:
                            prewarm_data["sentinel_contents"] = f"<read error: {e}>"
                    else:
                        prewarm_data["sentinel_contents"] = None
                    # Read PID file raw contents (if it exists).
                    pid_file = _pid_file_path()
                    if pid_file.exists():
                        try:
                            prewarm_data["pid_file_contents"] = pid_file.read_text()
                        except OSError as e:
                            prewarm_data["pid_file_contents"] = f"<read error: {e}>"
                    else:
                        prewarm_data["pid_file_contents"] = None
                    zf.writestr(
                        "prewarm.json",
                        _json.dumps(prewarm_data, indent=2, default=str),
                    )
                except Exception as prewarm_exc:
                    # Defensive: never let a prewarm probe failure abort
                    # the entire diagnostics export. Include the error
                    # so support engineers know why prewarm data is missing.
                    zf.writestr(
                        "prewarm.json",
                        _json.dumps(
                            {"error": str(prewarm_exc)},
                            indent=2,
                            default=str,
                        ),
                    )

            log.info("[RECOVERY] Diagnostic bundle created: %s", bundle_path)
            return str(bundle_path)
        except Exception as exc:
            log.error("[RECOVERY] Failed to create diagnostic bundle: %s", exc)
            return None
