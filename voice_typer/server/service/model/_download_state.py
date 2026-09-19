"""Per-download cancel-event registry shared by the download paths."""

from __future__ import annotations

import logging
import secrets
import threading

log = logging.getLogger(__name__)


class DownloadStateMixin:
    def __init__(self) -> None:
        """PERF-10 / SVC-9: short-TTL cache (5s) for get_model_status."""
        self._download_cancel_events: dict[str, threading.Event] = {}
        self._download_cancel_lock = threading.Lock()
        self._active_download_id: str | None = None
        # FIFO of model NAMES awaiting their turn behind the active
        self._download_queue: list[str] = []
        self._model_status_cache: dict[str, object] | None = None
        self._model_status_cache_ts: float = 0.0
        self._model_status_cache_lock = threading.Lock()

    def _register_download(self, model_name: str) -> str:
        """Create a per-download cancellation Event and return its id."""
        download_id = f"{model_name}:{secrets.token_hex(8)}"
        event = threading.Event()
        with self._download_cancel_lock:
            self._download_cancel_events[download_id] = event
            self._active_download_id = download_id
        return download_id

    def _unregister_download(self, download_id: str) -> None:
        """Remove the per-download Event from the dict and clear"""
        with self._download_cancel_lock:
            self._download_cancel_events.pop(download_id, None)
            if self._active_download_id == download_id:
                self._active_download_id = None

    def _is_download_cancelled(self, download_id: str) -> bool:
        """Return True if the download identified by ``download_id``"""
        with self._download_cancel_lock:
            event = self._download_cancel_events.get(download_id)
        return event.is_set() if event is not None else False
