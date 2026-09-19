"""Pipeline shared helpers."""

from __future__ import annotations

import contextlib
import logging
import threading
import time
import typing
from typing import Any

# vocabulary-automation analyzer degrade-gracefully defaults
_EMPTY_SEGMENTS: tuple = ()
_NO_TRANSCRIPT_CONFIDENCE: float = 0.0

log = logging.getLogger(__name__)


# distinct error for the "active ASR backend was never loaded"
class BackendNotLoadedError(RuntimeError):
    """Raised when the active ASR backend is not loaded at transcribe time."""

    def __init__(self, message: str = "", *, engine_name: str | None = None) -> None:
        super().__init__(message)
        self.engine_name = engine_name


# raw exception messages from ctranslate2 / torch / faster-whisper
def _friendly_transcription_error(exc: BaseException) -> str:
    """Return a user-friendly message describing a transcription failure."""
    # BackendNotLoadedError has a distinct, friendly message —
    if isinstance(exc, BackendNotLoadedError):
        return (
            "The speech model was not loaded when dictation finished. "
            "Wait for it to finish loading, or open Settings to verify "
            "the model is available."
        )
    msg = str(exc).lower()
    name = type(exc).__name__
    # Walk the __cause__ chain, cloud_engines.py wraps errors with
    names = {name}
    c = exc.__cause__
    while c is not None:
        names.add(type(c).__name__)
        c = c.__cause__
    # GPU / CUDA errors
    if "out of memory" in msg or "cuda" in msg and "memory" in msg:
        return "The GPU ran out of memory while transcribing. Try a smaller model."
    if "cuda" in msg or "cudnn" in msg or "cublas" in msg:
        return "A GPU/CUDA error occurred. The app will fall back to CPU on the next attempt."
    if "device" in msg and ("not available" in msg or "not found" in msg):
        return "The selected audio or compute device is unavailable."
    # Model file errors
    if "model" in msg and ("download" in msg or "load" in msg or "file" in msg):
        return "The speech model could not be loaded. Check your internet connection and try again."
    # Audio errors
    if "audio" in msg and ("empty" in msg or "no speech" in msg):
        return "No speech was detected in the recording."
    if names & {"ConnectionError", "TimeoutError", "URLError"}:
        return "A network error occurred while contacting the transcription service."
    # Permission errors
    if "PermissionError" in names:
        return "A file permission error occurred. Check that the app can write to its data directory."
    return f"Transcription failed ({name}). See the log file for technical details."


def _lookup_local_whisper(app: Any) -> Any:
    """Look up the local Whisper engine from the app's model registry.

    Returns ``None`` when the app has no ``models`` attribute, the models
    """
    try:
        models = getattr(app, "models", None)
    except Exception:  # pragma: no cover, defensive
        return None
    if models is None:
        return None
    registry = getattr(models, "registry", None)
    if registry is None:
        return None
    try:
        return registry.get("whisper")
    except Exception:  # pragma: no cover, defensive
        log.debug("[PIPELINE] _lookup_local_whisper: registry.get raised", exc_info=True)
        return None


@contextlib.contextmanager
def _timed_stage(timings: dict[str, float], name: str) -> typing.Iterator[None]:
    """Context manager that records a single stage's wall-clock duration."""
    t0 = time.perf_counter()
    try:
        yield
    finally:
        timings[name] = (time.perf_counter() - t0) * 1000


class _AbortWatcher:
    """Lightweight daemon thread that bridges the recording controller's"""

    _POLL_INTERVAL_SECONDS: float = 0.1

    def __init__(self, app: Any, cycle_id: str, engine: Any) -> None:
        self._app = app
        self._cycle_id = cycle_id
        self._engine = engine
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._abort_signalled: bool = False

    def start(self) -> None:
        self._thread = threading.Thread(
            target=self._run,
            name="DictationAbortWatcher",
            daemon=True,
        )
        self._thread.start()

    def _run(self) -> None:
        while not self._stop_event.wait(self._POLL_INTERVAL_SECONDS):
            try:
                recording = getattr(self._app, "recording", None)
                if recording is None:
                    continue
                cancelled_set = getattr(recording, "_cancelled_cycle_ids", None)
                cancelled_lock = getattr(recording, "_cancelled_cycle_ids_lock", None)
                if cancelled_set is None or cancelled_lock is None:
                    continue
                with cancelled_lock:
                    is_cancelled = self._cycle_id in cancelled_set
                if is_cancelled:
                    log.info(
                        "[PIPELINE] abort watcher detected cancel for cycle %s, signalling engine.request_abort()",
                        self._cycle_id,
                    )
                    try:
                        self._engine.request_abort()
                    except Exception:
                        log.debug(
                            "[PIPELINE] engine.request_abort() raised (non-fatal)",
                            exc_info=True,
                        )
                    self._abort_signalled = True
                    return
            except Exception:
                log.debug(
                    "[PIPELINE] abort watcher poll failed (non-fatal)",
                    exc_info=True,
                )

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=1.0)
