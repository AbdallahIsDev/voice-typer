"""Single-active-job manager with cancellation (ADR-0023 E14)."""

from __future__ import annotations

import threading
import uuid
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass, field
from typing import Any


@dataclass
class MediaJob:
    """One background ingest job."""

    job_id: str
    source: str
    status: str = "running"
    progress: float = 0.0
    cancel_event: threading.Event = field(default_factory=threading.Event)
    result: dict[str, Any] | None = None
    error: str | None = None


_OnEvent = Callable[[dict], None]


class MediaJobManager:
    """Owns at most one active job; cancel is cooperative via event."""

    def __init__(self, on_event: _OnEvent | None = None) -> None:
        self._lock = threading.Lock()
        self._active: MediaJob | None = None
        self._last: MediaJob | None = None
        self._on_event = on_event or (lambda _e: None)

    def start(self, source: str, runner: Callable[[MediaJob], dict[str, Any]]) -> MediaJob:
        """Start ``runner`` on a daemon thread; raise if a job is active."""
        from voice_typer.server.media_ingest.errors import JOB_BUSY

        with self._lock:
            if self._active is not None and self._active.status == "running":
                from voice_typer.server.media_ingest.errors import MediaIngestError

                raise MediaIngestError(JOB_BUSY, "a transcription job is already running")
            job = MediaJob(job_id=uuid.uuid4().hex[:12], source=source)
            self._active = job
            started = threading.Event()

        def _run() -> None:
            started.set()
            try:
                job.result = runner(job)
                job.status = "cancelled" if job.cancel_event.is_set() else "completed"
            except Exception as exc:  # noqa: BLE001, captured into job state
                job.status = "failed"
                job.error = str(exc)
                code = getattr(exc, "code", "internal_error")
                with suppress(Exception):
                    self._on_event(
                        {
                            "type": "media_transcribe_error",
                            "data": {"job_id": job.job_id, "code": str(code), "message": job.error},
                        }
                    )
            finally:
                with self._lock:
                    if self._active is job:
                        self._active = None
                    self._last = job

        thread = threading.Thread(target=_run, name="media-ingest", daemon=True)
        thread.start()
        started.wait(timeout=5)
        return job

    def cancel(self) -> bool:
        """Signal cancellation; True when a running job was found."""
        with self._lock:
            job = self._active
        if job is None or job.status != "running":
            return False
        job.cancel_event.set()
        return True

    def snapshot(self) -> dict[str, Any] | None:
        """Return the active (or last finished) job snapshot, None when idle."""
        with self._lock:
            job = self._active if self._active is not None else self._last
        if job is None:
            return None
        return {
            "job_id": job.job_id,
            "source": job.source,
            "status": job.status,
            "progress": job.progress,
            "error": job.error,
        }

    def publish(self, event: dict) -> None:
        """Forward a job event to the subscriber (event_bus wiring)."""
        self._on_event(event)


_MANAGER: MediaJobManager | None = None
_MANAGER_LOCK = threading.Lock()


def get_job_manager() -> MediaJobManager:
    """Process-wide job manager singleton."""
    global _MANAGER
    if _MANAGER is None:
        with _MANAGER_LOCK:
            if _MANAGER is None:
                _MANAGER = MediaJobManager()
    return _MANAGER
