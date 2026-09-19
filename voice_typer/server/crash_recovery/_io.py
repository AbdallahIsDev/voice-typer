"""Crash-recovery file IO."""

import collections
import json
import logging
import os
import time
from datetime import datetime

# ``_facade`` is a bound reference to the partially-initialized package
from voice_typer.server import crash_recovery as _facade

# Shared monotonic counter for the quarantine filename's sub-second
from voice_typer.server.security.file_io import _QUARANTINE_SUFFIX_SEQ

# Canonical constants. Defined HERE (the persistence-concern module) and
RECOVERY_FILENAME = "recovery.json"
_LEGACY_RECOVERY_FILENAME = "voice-typer-recovery.json"
MAX_RECOVERY_ENTRIES = 10

# Persistence role: ``recovery.json`` is an ACTIVE crash-recovery store

# Bounded queue: if the worker falls behind (e.g. disk is slow),
_SAVE_QUEUE_MAXSIZE = 32

# Logger name pinned to the package name (C-LOG-1). See _worker.py.
log = logging.getLogger("voice_typer.server.crash_recovery")


class _RecoveryIO:
    """Mixin: disk load/quarantine/save for :class:`CrashRecovery`."""

    def _load(self) -> None:
        """during ``VoiceTyperApp.__init__``. In production,"""
        # Fast-path guard (no lock). If already loaded, return
        if self._loaded:
            return
        with self._lock:
            # Re-check under the lock so only one thread does the
            if self._loaded:
                return
            # If ``_entries`` already has data (from ``add()``
            if len(self._entries) > 0:
                self._loaded = True
                return
            if not self._path.exists():
                self._entries = collections.deque(maxlen=MAX_RECOVERY_ENTRIES)
                self._loaded = True
                return
            try:
                from voice_typer.server.config import _secure_read_text

                raw = _secure_read_text(self._path)
                data = json.loads(raw)
                if isinstance(data, list):
                    self._entries = collections.deque(data, maxlen=MAX_RECOVERY_ENTRIES)
                elif isinstance(data, dict) and "entries" in data:
                    self._entries = collections.deque(data["entries"], maxlen=MAX_RECOVERY_ENTRIES)
                else:
                    # Shape is wrong but JSON parsed, treat as
                    self._quarantine_corrupt()
                    self._entries = collections.deque(maxlen=MAX_RECOVERY_ENTRIES)
                self._loaded = True
                log.debug("[RECOVERY] Loaded %d entries", len(self._entries))
            except Exception as exc:
                log.warning("[RECOVERY] Failed to load: %s", exc)
                # Quarantine the corrupt file so the next save creates a
                self._quarantine_corrupt()
                self._entries = collections.deque(maxlen=MAX_RECOVERY_ENTRIES)
                # Mark as loaded even on failure so a transient disk
                self._loaded = True

    def _apply_owner_only_acl(self, path) -> None:
        """Best-effort Windows owner-only ACL on a recovery file."""
        if not _facade.is_windows():
            return
        try:
            from voice_typer.server.config import _enforce_windows_owner_only_acl

            _enforce_windows_owner_only_acl(path)
        except Exception as exc:
            log.debug("[RECOVERY] ACL enforcement skipped for %s: %s", path, exc)

    def _quarantine_corrupt(self) -> None:
        """Move the corrupt recovery file to ``<path>.corrupt.<ts>-<pid>-<ns>``."""
        try:
            if not self._path.exists():
                return
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            pid = os.getpid()
            # Sub-second nanoseconds mixed with the shared monotonic
            ns = (time.time_ns() % 1_000_000 + next(_QUARANTINE_SUFFIX_SEQ)) % 1_000_000
            corrupt_path = self._path.with_name(f"{self._path.name}.corrupt.{ts}-{pid}-{ns}")
            os.replace(str(self._path), str(corrupt_path))
            # Windows: the quarantine destination is a fresh path; on
            self._apply_owner_only_acl(corrupt_path)
            log.warning(
                "[RECOVERY] Quarantined corrupt recovery file: %s -> %s",
                self._path.name,
                corrupt_path.name,
            )
        except Exception as exc:
            log.debug("[RECOVERY] Failed to quarantine corrupt file: %s", exc)

    def _save_sync(self, *, durability: bool = False, set_final_save_done: bool = False) -> None:
        """Save recovery entries to disk synchronously."""
        try:
            with self._save_lock:
                # short-circuit if the atexit handler already
                if self._final_save_done:
                    return
                try:
                    # skip the mkdir on subsequent saves. The flag
                    if not self._dir_ensured:
                        self._path.parent.mkdir(parents=True, exist_ok=True)
                        # skip the chmod on subsequent saves.
                        if not _facade.is_windows():
                            try:
                                os.chmod(self._path.parent, 0o700)
                            except OSError as e:
                                log.warning("[RECOVERY] Failed to chmod dir: %s", e)
                                # Don't set _dir_ensured, retry mkdir+chmod
                            else:
                                self._dir_ensured = True
                        else:
                            # Windows: no chmod, but mkdir succeeded, set
                            self._dir_ensured = True
                    with self._lock:
                        # Convert deque to list for JSON serialization
                        snapshot = json.dumps(
                            {"entries": list(self._entries)},
                            indent=2,
                            ensure_ascii=False,
                        )
                    # durability=False (default) for the per-dictation
                    _facade._secure_atomic_write(self._path, snapshot, durability=durability)
                    # Windows: recovery.json holds transcription text
                    self._apply_owner_only_acl(self._path)
                    # When called from the atexit path
                    if set_final_save_done:
                        self._final_save_done = True
                except Exception:
                    log.exception("[RECOVERY] Failed to save")
        except Exception:
            # lock-acquisition failure (or any other exception
            log.exception(
                "[RECOVERY] _save_sync top-level failure (lock acquisition or "
                "unexpected error); the recovery file may not be persisted"
            )
