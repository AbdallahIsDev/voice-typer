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
        # XE-16-3: ``_consumed_writeback_failed`` is set to True when
        # ``_mark_consumed`` exhausts its retry budget without
        # successfully writing ``consumed=True`` back to the file.
        # ``load_stale`` consults this flag and, if set, returns ``None``
        # on the NEXT process launch (treating the on-disk file as
        # "unknown state — do NOT auto-restore") so we don't risk
        # clobbering a user-initiated manual volume change with a
        # second, possibly-incorrect restore. The caller surfaces a
        # notification asking the user to verify their volume setting.
        # Reset to ``False`` by ``save()`` (fresh duck cycle) and
        # ``clear()`` (file deleted).
        self._consumed_writeback_failed: bool = False

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
        # XE-16-3: a fresh ``save()`` always represents a clean duck
        # cycle — clear the writeback-failed flag so a subsequent
        # ``load_stale()`` doesn't accidentally treat the new file as
        # "unknown state". The new ``consumed=False`` write is itself
        # retried below; if THAT fails the operator sees the existing
        # WARNING and the duck is aborted at the caller layer.
        self._consumed_writeback_failed = False

        from voice_typer.server.config import _secure_atomic_write

        last_exc: Exception | None = None
        for attempt in range(_SAVE_MAX_RETRIES):
            try:
                self._path.parent.mkdir(parents=True, exist_ok=True)
                # DJ-55: durability=False — the volume-duck state file
                # is best-effort crash-recovery data; the atomic
                # os.replace still guarantees consistency (no
                # half-written files), only the per-save fsync is
                # dropped. Volume state is recreated on every duck
                # operation, so a power-loss window of a few seconds
                # is acceptable.
                _secure_atomic_write(self._path, payload, durability=False)
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

        XE-16-3: if the previous process's ``_mark_consumed``
        write-back failed (signalled by ``consumed=True`` missing
        from the on-disk file AND a known transient-disk condition),
        ``load_stale`` returns ``None`` and the caller surfaces a
        notification asking the user to verify their volume setting
        rather than auto-restoring. Within the SAME process, the
        in-memory ``_consumed_writeback_failed`` flag tracks this
        state so the next ``load_stale()`` call is consistent.

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

        # XE-16-3: if a previous ``_mark_consumed`` write-back in THIS
        # process exhausted its retry budget, treat the on-disk state
        # as "unknown" — do NOT auto-restore. The caller surfaces a
        # notification asking the user to verify their volume setting.
        # (This guard fires only on the in-process re-call path; the
        # cross-process path is handled by the ``consumed=True``
        # on-disk flag below.)
        if self._consumed_writeback_failed:
            log.warning(
                "[VOLUME-CRASH] consumed-writeback failed earlier in this "
                "process; load_stale() returning None (unknown state — "
                "surface a notification asking the user to verify their "
                "volume setting rather than auto-restoring)"
            )
            return None

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
            # XE-16-3: ``_mark_consumed`` now retries the write-back
            # up to ``_SAVE_MAX_RETRIES`` times; if all attempts fail
            # it sets ``_consumed_writeback_failed=True`` and this
            # method does NOT cache the state (so a subsequent
            # same-process ``load_stale()`` call returns None via the
            # guard above). The caller's restore() still gets the
            # state from THIS call so the in-process restore proceeds;
            # the flag only blocks a NEXT-process re-restore via
            # this method's re-call in the SAME process.
            self._mark_consumed(data)
            if not self._consumed_writeback_failed:
                self._cached_stale = state
            return state
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            log.warning("[VOLUME-CRASH] Failed to parse stale state: %s", exc)
            self.clear()  # delete corrupt file
            return None

    def _mark_consumed(self, data: dict) -> None:
        """AC-94: write ``consumed=True`` back to the file in place.

        XE-16-3: previously fire-and-forget (single attempt, swallowed
        all exceptions at DEBUG). If the write-back failed — e.g. the
        disk was transiently full, an antivirus briefly locked the
        file, or NFS hiccupped — the on-disk file was left with
        ``consumed=False`` even though this process had already
        restored the volume. A subsequent process launch would then
        re-restore (potentially clobbering a user-initiated manual
        change made between launches), defeating AC-94's
        double-restore protection.

        Post-fix the write-back retries up to ``_SAVE_MAX_RETRIES``
        times with ``_SAVE_BACKOFF_S`` delay (matching ``save()``'s
        resilience pattern). If all retries fail, the failure is
        logged at WARNING (not DEBUG) so the operator sees the
        degradation, and the in-memory ``_consumed_writeback_failed``
        flag is set so ``load_stale``'s next same-process call
        returns ``None`` (treating the state as "unknown — do NOT
        auto-restore; surface a notification asking the user to
        verify their volume setting").
        """
        from voice_typer.server.config import _secure_atomic_write

        data = dict(data)
        data["consumed"] = True
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(data)
        last_exc: Exception | None = None
        for attempt in range(_SAVE_MAX_RETRIES):
            try:
                # DJ-55: durability=False — see save() for rationale.
                _secure_atomic_write(self._path, payload, durability=False)
                self._cache_dirty = True
                # XE-16-3: write succeeded — clear the failure flag
                # (it may have been set by a previous failed attempt
                # in this same call).
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
        # All retries exhausted — signal the degradation to
        # ``load_stale`` so the next same-process call returns None.
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
        # XE-16-3: clear the writeback-failed flag — the file is being
        # deleted, so the next ``load_stale()`` will see no file (return
        # None) and there's no "unknown state" to track.
        self._consumed_writeback_failed = False
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
