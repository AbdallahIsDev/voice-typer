"""Per-download cancel-event registry shared by the download paths."""

from __future__ import annotations

import logging
import secrets
import threading

log = logging.getLogger(__name__)


class DownloadStateMixin:
    def __init__(self) -> None:
        """initialize ModelMixin's own state.

        Previously the six ModelMixin-specific fields
        (``_download_cancel_events``, ``_download_cancel_lock``,
        ``_active_download_id``, ``_model_status_cache``,
        ``_model_status_cache_ts``, ``_model_status_cache_lock``)
        were initialised inline in ``VoiceTyperService.__init__``
        even though they are used ONLY by ModelMixin. This is the
        same fat-base-class smell called out in  — only
        ``MicrophoneTestMixin`` got its own ``__init__`` extraction,
        so the  fix was applied inconsistently.

        mirror the MicrophoneTestMixin pattern —
        each mixin owns the initialisation of its own state. The
        mixin ``__init__`` is called explicitly from
        ``VoiceTyperService.__init__`` (rather than via cooperative
        ``super().__init__()`` chaining) because ``ServiceMixinBase``
        doesn't define an ``__init__`` that accepts the ``app``
        argument. Functionally equivalent: the state ends up on the
        same instance via the same MRO.

         SERVICE-1: per-download cancellation events guarded by
        a lock, so concurrent ``download_model`` IPC calls (via the
        ThreadPoolExecutor) don't overwrite each other's event. The
        previous single-instance attribute meant the second call's
        ``self._download_cancel_event = threading.Event()`` clobbered
        the first call's reference; the first call's polling loop then
        polled the wrong event, and when the second call finished and
        set the attribute to ``None`` the first call's
        ``.is_set()`` raised AttributeError.

        ``_active_download_id`` is initialised to ``None`` so
        :meth:`ModelMixin.cancel_model_download` can safely read it
        before any download has been registered. Previously this was
        left unset, causing an ``AttributeError`` on the first
        ``cancel_model_download`` call (covered by
        ``tests/test_history_and_models.py::TestCancelModelDownloadMechanism``).

        PERF-10 / SVC-9: short-TTL cache (5s) for get_model_status.
        """
        self._download_cancel_events: dict[str, threading.Event] = {}
        self._download_cancel_lock = threading.Lock()
        self._active_download_id: str | None = None
        self._model_status_cache: dict[str, object] | None = None
        self._model_status_cache_ts: float = 0.0
        self._model_status_cache_lock = threading.Lock()

    # ── Download cancellation helpers ( / SERVICE-1) ──────────

    def _register_download(self, model_name: str) -> str:
        """Create a per-download cancellation Event and return its id.

        Generates a unique ``download_id`` so two concurrent
        ``download_model`` calls don't share state. Stores the Event in
        ``self._download_cancel_events`` under the lock and marks it as
        the active download. ``download_model`` must call
        :meth:`_unregister_download` (in a ``finally`` or at each
        return point) to avoid leaking entries in the dict.
        """
        download_id = f"{model_name}:{secrets.token_hex(8)}"
        event = threading.Event()
        with self._download_cancel_lock:
            self._download_cancel_events[download_id] = event
            self._active_download_id = download_id
        return download_id

    def _unregister_download(self, download_id: str) -> None:
        """Remove the per-download Event from the dict and clear
        ``_active_download_id`` if it still points at us.

        Safe to call from any ``download_model`` exit path (success,
        failure, cancellation). The lookup is under the lock so a
        concurrent ``cancel_model_download`` doesn't see a half-removed
        entry.
        """
        with self._download_cancel_lock:
            self._download_cancel_events.pop(download_id, None)
            if self._active_download_id == download_id:
                self._active_download_id = None

    def _is_download_cancelled(self, download_id: str) -> bool:
        """Return True if the download identified by ``download_id``
        has been cancelled.

         SERVICE-1: looks up the Event in the per-download dict
        (under the lock) so a concurrent ``download_model`` call's
        cancel signal doesn't bleed into this download. Returns False
        if the entry is missing (already cleaned up, or never
        registered) — the None-guard prevents the AttributeError that
        the previous single-attribute design raised when a sibling
        download set the attribute to ``None``.
        """
        with self._download_cancel_lock:
            event = self._download_cancel_events.get(download_id)
        return event.is_set() if event is not None else False

    # ── Volume / Model status () ────────────────────────
