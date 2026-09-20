"""Volume-duck crash recovery store."""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path

from voice_typer.server.volume_backend_base import VolumeState

log = logging.getLogger(__name__)

_DEFAULT_FILENAME = "duck_crash_recovery.json"
# Separate sentinel file written by ``load_stale`` BEFORE the
_RESTORING_SENTINEL_FILENAME = "duck_crash_recovery.restoring"

# retry configuration for ``save()``. Previously ``save()``
_SAVE_MAX_RETRIES = 3
_SAVE_BACKOFF_S = 0.1


class DuckCrashRecovery:
    """Persists ducked volume state for crash recovery."""

    def __init__(self, config_dir: Path | None = None) -> None:
        if config_dir is None:
            # route through _paths.config_dir() so the default
            from voice_typer.server import _paths

            config_dir = _paths.config_dir()
        self._path = config_dir / _DEFAULT_FILENAME
        # Separate sentinel file written by ``load_stale`` before
        self._restoring_sentinel_path = config_dir / _RESTORING_SENTINEL_FILENAME
        # in-memory cache of the most-recently-loaded stale
        self._cached_stale: VolumeState | None = None
        # ``_consumed_writeback_failed`` is set to True when
        self._consumed_writeback_failed: bool = False

    @property
    def path(self) -> Path:
        return self._path

    def _write_restoring_sentinel(self) -> None:
        """Best-effort write of the restoring sentinel file."""
        try:
            self._restoring_sentinel_path.parent.mkdir(parents=True, exist_ok=True)
            from voice_typer.server.config import _secure_atomic_write

            _secure_atomic_write(self._restoring_sentinel_path, "restoring", durability=False)
        except OSError as exc:
            log.debug("[VOLUME-CRASH] Could not write restoring sentinel: %s", exc)

    def _delete_restoring_sentinel(self) -> None:
        """Best-effort delete of the restoring sentinel file."""
        try:
            if self._restoring_sentinel_path.exists():
                self._restoring_sentinel_path.unlink()
        except OSError as exc:
            log.debug("[VOLUME-CRASH] Could not delete restoring sentinel: %s", exc)

    def save(self, state: VolumeState) -> bool:
        """Persist the pre-duck volume state.

        Returns
        """
        data = {
            "linear": state.linear,
            "muted": state.muted,
            # default to ``False`` so a fresh save always
            "consumed": False,
        }
        payload = json.dumps(data)
        # invalidate the in-memory cache, the caller is
        self._cached_stale = None
        # a fresh ``save()`` always represents a clean duck
        self._consumed_writeback_failed = False
        # A fresh ``save()`` starts a new duck cycle, any
        self._delete_restoring_sentinel()

        from voice_typer.server.config import _secure_atomic_write

        last_exc: Exception | None = None
        for attempt in range(_SAVE_MAX_RETRIES):
            try:
                self._path.parent.mkdir(parents=True, exist_ok=True)
                # durability=False, the volume-duck state file
                _secure_atomic_write(self._path, payload, durability=False)
                return True
            except Exception as exc:
                last_exc = exc
                # log the per-attempt failure at debug level
                log.debug(
                    "[VOLUME-CRASH] save() attempt %d/%d failed: %s",
                    attempt + 1,
                    _SAVE_MAX_RETRIES,
                    exc,
                )
                if attempt < _SAVE_MAX_RETRIES - 1:
                    time.sleep(_SAVE_BACKOFF_S)
        log.warning(
            "[VOLUME-CRASH] Failed to persist duck state after %d attempts: %s",
            _SAVE_MAX_RETRIES,
            last_exc,
        )
        return False

    def load_stale(self) -> VolumeState | None:
        """Check for a stale duck state file from a crashed session.

        Returns the saved :class:`VolumeState` if a stale file exists
        """
        # in-memory cache hit. Return the cached state without
        if self._cached_stale is not None:
            return self._cached_stale

        # if a previous ``_mark_consumed`` write-back in THIS
        if self._consumed_writeback_failed:
            log.warning(
                "[VOLUME-CRASH] consumed-writeback failed earlier in this "
                "process; load_stale() returning None (unknown state, "
                "surface a notification asking the user to verify their "
                "volume setting rather than auto-restoring)"
            )
            return None

        # Orphaned sentinel with no main file, stale leftover from a
        if not self._path.exists():
            if self._restoring_sentinel_path.exists():
                self._delete_restoring_sentinel()
            return None
        try:
            from voice_typer.server.config import _secure_read_text

            raw = _secure_read_text(self._path, encoding="utf-8")
            data = json.loads(raw)
            # ``consumed`` defaults to ``False`` for back-compat
            if bool(data.get("consumed", False)):
                # Case 2/4: the restore already succeeded. A leftover
                if self._restoring_sentinel_path.exists():
                    self._delete_restoring_sentinel()
                return None
            state = VolumeState(
                linear=float(data["linear"]),
                muted=bool(data["muted"]),
            )
            # Case 1/3: write the restoring sentinel BEFORE returning
            self._write_restoring_sentinel()
            self._cached_stale = state
            return state
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            log.warning("[VOLUME-CRASH] Failed to parse stale state: %s", exc)
            try:
                if self._path.exists():
                    self._path.unlink()
            except OSError:
                log.debug("[VOLUME-CRASH] Could not delete corrupt state file", exc_info=True)
            self._delete_restoring_sentinel()
            return None

    def _mark_consumed(self, data: dict) -> None:
        """write ``consumed=True`` back to the file in place."""
        from voice_typer.server.config import _secure_atomic_write

        data = dict(data)
        data["consumed"] = True
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(data)
        last_exc: Exception | None = None
        for attempt in range(_SAVE_MAX_RETRIES):
            try:
                # durability=False: see save() for rationale.
                _secure_atomic_write(self._path, payload, durability=False)
                # write succeeded, clear the failure flag
                self._consumed_writeback_failed = False
                return
            except Exception as exc:
                last_exc = exc
                log.debug(
                    "[VOLUME-CRASH] _mark_consumed attempt %d/%d failed: %s",
                    attempt + 1,
                    _SAVE_MAX_RETRIES,
                    exc,
                )
                if attempt < _SAVE_MAX_RETRIES - 1:
                    time.sleep(_SAVE_BACKOFF_S)
        # All retries exhausted, signal the degradation to
        self._consumed_writeback_failed = True
        log.warning(
            "[VOLUME-CRASH] Failed to mark stale state as consumed after "
            "%d attempts (load_stale will return None on subsequent calls "
            "in this process; surface a notification asking the user to "
            "verify their volume setting): %s",
            _SAVE_MAX_RETRIES,
            last_exc,
        )

    def clear(self) -> None:
        """Delete the duck state file."""
        # invalidate the in-memory cache.
        self._cached_stale = None
        # clear the writeback-failed flag, the file is being
        self._consumed_writeback_failed = False
        # Step 1: flip consumed=True on the main file (if it parses) so
        if self._path.exists():
            try:
                from voice_typer.server.config import _secure_read_text

                raw = _secure_read_text(self._path, encoding="utf-8")
                self._mark_consumed(json.loads(raw))
            except (OSError, json.JSONDecodeError, ValueError, KeyError, TypeError):
                # Corrupt/unreadable main file, skip the flip; the
                pass
        # Step 2: delete the restoring sentinel.
        self._delete_restoring_sentinel()
        # Step 3: delete the main file.
        try:
            if self._path.exists():
                self._path.unlink()
        except OSError as exc:
            log.debug("[VOLUME-CRASH] Could not delete stale file: %s", exc)

    # Provides ``with DuckCrashRecovery(...) as cr:`` semantics so the

    def __enter__(self) -> DuckCrashRecovery:
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        # Only clear on a clean exit. If the ``with`` block raised,
        if exc_type is None:
            self.clear()
        # Returning ``None`` (falsy) so the exception (if any) is
        return None
