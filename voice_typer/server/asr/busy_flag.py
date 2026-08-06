"""Per-backend "busy" flag for the ASR backend registry.

Extracted from the former monolithic ``asr_registry.py``. Owns
the per-backend busy-set state used by the dictation watchdog to reject
new dictation requests when the active backend is stuck inside a
C-level ctranslate2 / torch inference call (which can hold the GPU +
GIL for 5–30 min).

The flag is keyed by backend NAME (not the backend object) so a backend
that was unregistered + re-registered under the same name (e.g. via
``change_model``) doesn't carry over a stale busy state.

The ``BusyFlag`` is constructed with the shared ``threading.RLock`` so
the registry's atomicity guarantees are preserved: a ``set_busy`` on
the transcribe thread and a concurrent ``is_busy`` read on the IPC
thread both acquire the same lock, so the read/write pair is atomic.
"""

from __future__ import annotations

import contextlib
import logging
import threading
from collections.abc import Callable

log = logging.getLogger(__name__)


class BusyFlag:
    """Per-backend busy flag, keyed by backend name.

    Set when a backend enters ``transcribe_with_fallback`` (via the
    registry's wrapper or the ``busy_context`` context manager), cleared
    on exit (including the exception path). Used by
    :meth:`ModelManager.ensure_active_engine_loaded` to reject new
    dictation requests when the active backend is stuck in a C-level
    ctranslate2 call.

    ``active_name_provider`` is a zero-argument callable returning the
    currently-active backend name (typically ``lambda: registry.active_name``).
    It is invoked lazily so the flag always reflects the latest
    ``config.asr_backend`` value even if the config is mutated after
    construction.
    """

    def __init__(
        self,
        lock: threading.RLock,
        active_name_provider: Callable[[], str],
    ) -> None:
        self._lock = lock
        self._active_name_provider = active_name_provider
        # The set is keyed by backend NAME (not the backend object) so
        # a backend that was unregistered + re-registered under the
        # same name (e.g. via ``change_model``) doesn't carry over a
        # stale busy state.
        self._busy_backends: set[str] = set()

    def is_busy(self, name: str | None = None) -> bool:
        """Return True if the named backend (or the active backend when
        ``name`` is None) is currently inside ``transcribe_with_fallback``.

        Thread-safety: the transcribe thread sets/clears the flag via
        :meth:`busy_context` or :meth:`transcribe_with_fallback`, and
        the IPC thread reads it via this method. Both paths acquire
        ``self._lock`` so the read/write pair is atomic. A ``False``
        return value is a snapshot — the backend may become busy
        immediately after the call returns — but callers (e.g.
        :meth:`ModelManager.ensure_active_engine_loaded`) use it as a
        defence-in-depth rejection gate, not as a strict mutual-exclusion
        primitive.

        Args:
            name: backend name to query. If None, queries the active
                backend (``self._active_name_provider()``). Returns
                False if the name is unknown or no active backend is
                configured.
        """
        target = name if name is not None else self._active_name_provider()
        if not target:
            return False
        with self._lock:
            return target in self._busy_backends

    def set_busy(self, name: str | None = None) -> None:
        """Mark the named backend (or the active backend when ``name``
        is None) as busy.

        Callers SHOULD prefer :meth:`busy_context` (or the registry's
        ``transcribe_with_fallback`` wrapper) so the flag is cleared
        automatically on exit — including the exception path. Manual
        ``set_busy`` / :meth:`clear_busy` pairs are error-prone (a
        missed ``clear_busy`` leaves the backend permanently busy,
        blocking all subsequent dictations).

        Thread-safety: see :meth:`is_busy`.
        """
        target = name if name is not None else self._active_name_provider()
        if not target:
            return
        with self._lock:
            self._busy_backends.add(target)

    def clear_busy(self, name: str | None = None) -> None:
        """Mark the named backend (or the active backend when ``name``
        is None) as not busy.

        Idempotent — calling on a backend that wasn't busy is a no-op.
        Safe to call from a finally block / context-manager exit even if
        ``set_busy`` was never called (e.g. the ``busy_context``'s
        ``__exit__`` always calls this).

        Thread-safety: see :meth:`is_busy`.
        """
        target = name if name is not None else self._active_name_provider()
        if not target:
            return
        with self._lock:
            self._busy_backends.discard(target)

    @contextlib.contextmanager
    def busy_context(self, name: str | None = None):
        """Context manager that sets the busy flag on enter and clears
        it on exit (including the exception path).

        Yields the resolved backend name so callers can pass it to
        subsequent registry methods without re-resolving.

        Usage::

            with busy_flag.busy_context("parakeet") as backend_name:
                backend = registry.get(backend_name)
                text = backend.transcribe_with_fallback(audio, ...)

        Or, equivalently and preferred, use the registry's
        ``transcribe_with_fallback`` which wraps the call automatically.

        Thread-safety: ``set_busy`` + ``clear_busy`` are both
        ``self._lock``-guarded, so the context manager is safe to
        enter/exit from any thread. The flag is keyed by backend NAME
        so a backend that was unregistered mid-transcription (e.g. by a
        concurrent ``change_model``) is still correctly marked not-busy
        on exit — the name doesn't disappear from ``_busy_backends``
        just because the backend object was replaced.
        """
        target = name if name is not None else self._active_name_provider()
        if not target:
            # Nothing to mark busy — yield the empty name and return.
            yield target
            return
        self.set_busy(target)
        try:
            yield target
        finally:
            self.clear_busy(target)

    def force_clear_busy(self, name: str | None = None) -> None:
        """Alias for :meth:`clear_busy` exposed under a more
        discoverable name for the watchdog's force-recover path.

        The watchdog (:meth:`RecordingController._force_recover_from_stuck_transcription`)
        calls :meth:`ModelManager.force_unload_active` after the 2nd
        force-recovery to tear down the stuck model's GPU resources.
        ``force_unload_active`` calls this method to clear the busy flag
        so the next dictation isn't rejected by
        :meth:`ModelManager.ensure_active_engine_loaded`'s busy-check.

        Kept as a separate public method (rather than just calling
        ``clear_busy`` directly) so the watchdog call site is
        self-documenting: ``busy_flag.force_clear_busy(name)`` reads as
        "force-clear the busy flag because the watchdog decided the
        backend is unrecoverable", whereas ``clear_busy`` could be
        misread as a routine cleanup.
        """
        self.clear_busy(name)

    def is_target_busy(self, target: str) -> bool:
        """Direct check used by :meth:`AsrBackendRegistry.unload` to
        guard against tearing down a backend mid-transcription.

        Unlike :meth:`is_busy`, this takes an already-resolved target
        name (the caller has computed it as ``name or active_name``)
        and skips the None-defaults-to-active resolution. Returns False
        for an empty target.
        """
        if not target:
            return False
        with self._lock:
            return target in self._busy_backends
