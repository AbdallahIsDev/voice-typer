"""Crash recovery for volume ducking.

If the application crashes while system volume is ducked (reduced to
e.g. 25%), the system would be left at that reduced level indefinitely.
This module persists the pre-duck volume state to a small JSON file on
``duck()`` and deletes it on ``restore()``.  On the next application
launch, :meth:`VolumeDucker.initialize` checks for a stale file and
restores the saved volume before any new ducking occurs.

The file lives in the voice-typer config directory (``~/.voice-typer/``)
and contains only the volume level and mute flag — no sensitive data.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path

from voice_typer.server.volume_backend_base import VolumeState

log = logging.getLogger(__name__)

_DEFAULT_FILENAME = "duck_crash_recovery.json"

# XZ-R17-05: retry configuration for ``save()``. Previously ``save()``
# was fire-and-forget — a single transient disk failure (NFS hang, disk
# full, brief permissions glitch) meant the crash-recovery file was
# NEVER written, and a subsequent app crash left the user's speakers
# stuck at the ducked level (e.g. 25%) on next launch. We now retry the
# atomic write up to ``_SAVE_MAX_RETRIES`` times with a fixed
# ``_SAVE_BACKOFF_S`` delay between attempts. Returning ``bool`` lets
# callers (``VolumeDucker.duck``) abort the duck if persistence fails
# (out of scope for this file — that change lives in ``volume_ducker``).
_SAVE_MAX_RETRIES = 3
_SAVE_BACKOFF_S = 0.1


class DuckCrashRecovery:
    """Persists ducked volume state for crash recovery.

    The file is written atomically (temp file + rename) so that a crash
    mid-write cannot corrupt it.  On POSIX, permissions are tightened
    to 0o600 to prevent other users from reading or tampering with the
    file (though it contains no secrets, defense in depth is cheap).

    AC-94 state machine
    -------------------
    The persisted JSON carries a ``"consumed": bool`` flag that
    disambiguates the two previously-overloaded meanings of "file
    present":

    * ``consumed=False`` (or absent for back-compat with files written
      by previous versions): the duck is active and the volume has not
      been restored yet. ``load_stale()`` returns the state AND writes
      ``consumed=True`` back to the file. A subsequent process launch
      that sees ``consumed=True`` returns ``None`` (the prior launch
      already restored the volume; restoring again would clobber a
      user-initiated manual change in the interim).

    * ``consumed=True``: the volume was already restored (or the
      previous restore attempt crashed mid-restore, in which case the
      user's volume is in an unknown state and auto-restoring on top
      of it would be wrong). ``load_stale()`` returns ``None`` and
      leaves the file in place (the next ``clear()`` will delete it).

    Within a single Python process, ``load_stale()`` is idempotent:
    the first call caches the state in ``self._cached_stale`` and
    subsequent calls return the cached value without re-reading the
    file. This preserves the existing test contract
    (``tests/test_volume_ducker.py::test_duck_persists_state_for_crash_recovery``
    calls ``load_stale()`` twice and expects both calls to return the
    saved state — the file is not cleared by ``load_stale()``, only by
    ``clear()``). The cache is invalidated by ``clear()`` and by
    ``save()`` (which writes a fresh ``consumed=False`` state).
    """

    def __init__(self, config_dir: Path | None = None) -> None:
        if config_dir is None:
            # RW-7: route through _paths.config_dir() so the default
            # respects the platform-aware _config_dir() logic (Windows
            # %APPDATA%, macOS ~/Library/Application Support, Linux
            # $XDG_DATA_HOME, the VOICE_TYPER_CONFIG_DIR override, and
            # the legacy ~/.voice-typer migration check) instead of the
            # previous hardcoded Path.home() / ".voice-typer".
            from voice_typer.server import _paths

            config_dir = _paths.config_dir()
        self._path = config_dir / _DEFAULT_FILENAME
        # AC-94: in-memory cache of the most-recently-loaded stale
        # state. See the class docstring for the rationale.
        self._cached_stale: VolumeState | None = None
        self._cache_dirty: bool = False

    @property
    def path(self) -> Path:
        return self._path

    def save(self, state: VolumeState) -> bool:
        """Persist the pre-duck volume state.

        Called by ``VolumeDucker.duck()`` after the volume has been
        successfully reduced.  If writing fails, a warning is logged but
        no exception is raised — crash recovery is best-effort.

        XZ-R17-05: previously fire-and-forget (single attempt, swallowed
        all exceptions). Now retries up to ``_SAVE_MAX_RETRIES`` times
        with ``_SAVE_BACKOFF_S`` backoff so transient disk failures
        (NFS hang, disk full, brief permissions glitch) don't silently
        drop the crash-recovery file — a missing file on next launch
        means the user's speakers stay stuck at the ducked level.

        SEC-003: Uses _secure_atomic_write to ensure 0o600 permissions
        on POSIX and O_NOFOLLOW symlink protection.

        AC-94: writes ``consumed=False`` so the next ``load_stale()``
        (in this or a future process) treats the state as
        "ducked, not yet restored".

        Returns
        -------
        bool
            ``True`` if the file was written successfully within the
            retry budget. ``False`` if all retries failed — callers
            (``VolumeDucker.duck``) may use this to abort the duck and
            restore the volume immediately, preventing the
            "speakers stuck at 25%" failure mode.
        """
        data = {
            "linear": state.linear,
            "muted": state.muted,
            # AC-94: default to ``False`` so a fresh save always
            # represents "ducked, not yet restored". Old files written
            # by previous versions lack this key and are treated as
            # ``False`` on load (see ``load_stale``).
            "consumed": False,
        }
        payload = json.dumps(data)
        # AC-94: invalidate the in-memory cache — the caller is
        # persisting a NEW state, so any previously-cached stale state
        # is now stale (in the "stale cache" sense, not the
        # "stale crash-recovery file" sense).
        self._cached_stale = None
        self._cache_dirty = False

        from voice_typer.server.config import _secure_atomic_write

        last_exc: Exception | None = None
        for attempt in range(_SAVE_MAX_RETRIES):
            try:
                self._path.parent.mkdir(parents=True, exist_ok=True)
                _secure_atomic_write(self._path, payload)
                return True
            except Exception as exc:
                last_exc = exc
                # XZ-R17-05: log the per-attempt failure at debug level
                # so a noisy disk doesn't spam the warning log; the
                # final-attempt warning below is the operator-visible
                # signal.
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
        and has not been consumed, or ``None`` if no file is present,
        the file has been consumed (AC-94), or it cannot be parsed.

        Does **not** delete the file — the caller is responsible for
        calling :meth:`clear` after successfully restoring. The
        ``consumed`` flag (AC-94) is the soft-clear mechanism: this
        method writes ``consumed=True`` back to the file on first
        successful load, so a subsequent process launch that finds the
        file with ``consumed=True`` returns ``None`` (avoiding a
        double-restore that would clobber a user's manual volume
        change made between launches).

        Within a single Python process, this method is idempotent:
        the first successful call caches the state, and subsequent
        calls return the cached value without re-reading the file
        (preserves the existing test contract where ``load_stale()``
        is called twice in succession and both calls return the
        saved state). The cache is invalidated by ``save()`` and
        ``clear()``.

        SEC-002: Uses _secure_read_text to prevent symlink-TOCTOU attacks.
        """
        # AC-94: in-memory cache hit — return the cached state without
        # re-reading the file. This preserves the existing test
        # contract where two successive ``load_stale()`` calls return
        # the same state (the file's ``consumed`` flag is now True
        # after the first call's write-back, but the cache holds the
        # original state).
        if self._cached_stale is not None:
            return self._cached_stale

        if not self._path.exists():
            return None
        try:
            from voice_typer.server.config import _secure_read_text

            raw = _secure_read_text(self._path, encoding="utf-8")
            data = json.loads(raw)
            # AC-94: ``consumed`` defaults to ``False`` for back-compat
            # with files written by previous versions that lack the
            # key. A ``True`` value means a prior launch already
            # restored the volume — auto-restoring again would clobber
            # a user-initiated manual change in the interim, so we
            # return ``None`` and let the caller skip the restore.
            if bool(data.get("consumed", False)):
                # Leave the file in place; ``clear()`` will delete it
                # on the next successful duck→restore cycle.
                return None
            state = VolumeState(
                linear=float(data["linear"]),
                muted=bool(data["muted"]),
            )
            # AC-94: write ``consumed=True`` back to the file so a
            # subsequent process launch (after a crash between this
            # load and the eventual ``clear()``) sees the consumed
            # flag and skips the (potentially destructive) restore.
            # Best-effort: if the write-back fails, the cached state
            # is still returned so this process can restore the
            # volume — the next launch will simply retry the load
            # (and re-attempt the consumed-writeback).
            self._mark_consumed(data)
            self._cached_stale = state
            return state
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            log.warning("[VOLUME-CRASH] Failed to parse stale state: %s", exc)
            self.clear()  # delete corrupt file
            return None

    def _mark_consumed(self, data: dict) -> None:
        """AC-94: write ``consumed=True`` back to the file in place.

        Best-effort: failures are logged at debug level and swallowed
        so the caller's ``load_stale()`` return value is not affected.
        The in-memory cache (set by ``load_stale`` before calling this
        helper) holds the state regardless of whether the write-back
        succeeds.
        """
        try:
            from voice_typer.server.config import _secure_atomic_write

            data = dict(data)
            data["consumed"] = True
            self._path.parent.mkdir(parents=True, exist_ok=True)
            _secure_atomic_write(self._path, json.dumps(data))
            self._cache_dirty = True
        except Exception as exc:
            log.debug(
                "[VOLUME-CRASH] Failed to mark stale state as consumed: %s",
                exc,
            )

    def clear(self) -> None:
        """Delete the duck state file.

        Called by ``VolumeDucker.restore()`` after volume has been
        successfully restored, and by :meth:`load_stale` if the file is
        corrupt. Also invalidates the in-memory cache (AC-94) so a
        subsequent ``load_stale()`` after ``clear()`` correctly returns
        ``None`` (rather than the cached pre-clear state).
        """
        # AC-94: invalidate the in-memory cache.
        self._cached_stale = None
        self._cache_dirty = False
        try:
            if self._path.exists():
                self._path.unlink()
        except OSError as exc:
            log.debug("[VOLUME-CRASH] Could not delete stale file: %s", exc)

    # ── AC-94: context-manager support ──────────────────────────────
    #
    # Provides ``with DuckCrashRecovery(...) as cr:`` semantics so the
    # save→restore lifecycle can be expressed as a single block. The
    # ``__exit__`` method calls ``clear()`` (no exception) or leaves
    # the file in place (exception path — the file persists so the
    # next launch can restore). This is the recommended pattern for
    # short-lived duck sessions in tests and one-shot scripts; the
    # production ``VolumeDucker`` continues to call ``save()`` /
    # ``clear()`` explicitly because its lifecycle spans multiple
    # methods (``duck`` → ... → ``restore``).

    def __enter__(self) -> DuckCrashRecovery:
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        # Only clear on a clean exit. If the ``with`` block raised,
        # leave the file in place so a subsequent process launch can
        # restore the volume (mirrors the crash-recovery contract).
        if exc_type is None:
            self.clear()
        # Returning ``None`` (falsy) so the exception (if any) is
        # propagated — we don't swallow it.
        return None
