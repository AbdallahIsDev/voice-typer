"""Shared helpers for the dictation pipeline package.

These helpers were originally defined inline at the top of
``dictation_pipeline.py`` (the 2077-LOC monolith). The split keeps them
in a single sibling module so every step mixin and the orchestrator
can import them without circular dependencies.

Contents:
  * ``_EMPTY_SEGMENTS`` / ``_NO_TRANSCRIPT_CONFIDENCE`` — module-level
    sentinels consumed by the vocabulary-automation analyzer step
    (see ``enhancement_steps._analyze_vocabulary``).
  * ``BackendNotLoadedError`` — distinct error raised by
    ``transcribe_step._transcribe`` when the active ASR backend is
    not loaded at transcribe time. Subclass of ``RuntimeError`` so
    existing ``except RuntimeError`` clauses still catch it.
  * ``_friendly_transcription_error`` — maps raw ctranslate2 / torch /
    cloud-HTTP exceptions to user-facing messages so raw exception
    text (which can leak file paths, CUDA versions, and API keys) is
    never shown in tray notifications.
  * ``_lookup_local_whisper`` — looks up the local Whisper engine
    from the app's model registry so it can be passed as the
    ``local_engine`` fallback for a CloudEngine.
  * ``_timed_stage`` — context manager that records a single stage's
    wall-clock duration (in milliseconds) into a timings dict.
  * ``_AbortWatcher`` — daemon thread that bridges the recording
    controller's cancel set to the active ASR engine's abort API so
    inference actually stops (instead of running to completion and
    the late result being dropped by the paste guard).

These symbols are re-exported from ``dictation_pipeline/__init__.py``
so existing callers (tests, app) that import them from
``voice_typer.server.dictation_pipeline`` continue to work — see the
``__all__`` list in that module.
"""

from __future__ import annotations

import contextlib
import logging
import threading
import time
import typing
from typing import Any

# vocabulary-automation analyzer degrade-gracefully defaults
# used when the transcription engine did not produce per-segment or
# per-word confidence data (e.g. faster-whisper's avg_logprob
# surface). These are module-level sentinels (NOT instance
# attributes) — the previous ``getattr(self, "_segments", None) or []``
# + ``getattr(self, "_confidence", 0.9)`` accidentally fabricated
# a confident empty segment list, which made the analyzer treat
# every word as high-confidence. Now the analyzer sees honest
# empty data and degrades gracefully.
_EMPTY_SEGMENTS: list = []
_NO_TRANSCRIPT_CONFIDENCE: float = 0.0

log = logging.getLogger(__name__)


# distinct error for the "active ASR backend was never loaded"
# failure mode. Pre-fix, ``_transcribe`` always returned the empty
# string when ``transcribe_with_fallback`` produced no text — the
# downstream ``EmptyCheckStage`` then ran ``_handle_empty_transcription``
# which shows the ambiguous "No speech detected" toast regardless of
# whether the user was silent or the model was unloaded. Raising this
# sentinel from ``_transcribe`` (only when ``active.is_loaded`` is
# False AND the engine returned empty) bypasses ``EmptyCheckStage``
# entirely (the exception propagates out of ``TranscribeStage`` and is
# caught by ``run()``'s generic ``except Exception`` block) so the user
# sees a friendly "model not loaded" message instead of the misleading
# "no speech" toast. Subclass of ``RuntimeError`` so existing
# ``except RuntimeError`` / ``except Exception`` clauses still catch it.
class BackendNotLoadedError(RuntimeError):
    """Raised when the active ASR backend is not loaded at transcribe time.

    an unloaded backend (``is_loaded is False``) can return ``""``
    from ``transcribe_with_fallback`` without raising — making the empty-
    transcription path indistinguishable from genuine silence. This
    sentinel is raised by ``DictationPipeline._transcribe`` ONLY when
    ``active.is_loaded`` was False BEFORE the transcribe call AND the
    engine returned empty output, so the run() generic ``except
    Exception`` block surfaces a friendly "model not loaded" message
    instead of falling through to ``_handle_empty_transcription``.

    The ``engine_name`` kwarg captures the backend type for telemetry /
    IPC ``isinstance`` narrowing — mirrors the pattern used by
    ``ConsentRequiredError`` in ``asr_errors.py``.
    """

    def __init__(self, message: str = "", *, engine_name: str | None = None) -> None:
        super().__init__(message)
        self.engine_name = engine_name


# raw exception messages from ctranslate2 / torch / faster-whisper
# often leak file paths, CUDA versions, and internal stack details into
# user-facing tray notifications. Map known exception classes to friendly
# messages; fall back to a generic message for unknown errors.
def _friendly_transcription_error(exc: BaseException) -> str:
    """Return a user-friendly message describing a transcription failure."""
    # BackendNotLoadedError has a distinct, friendly message —
    # do NOT fall through to the generic "model could not be loaded"
    # branch (which is about download/load-time failures, not an
    # unloaded backend at transcribe time). The user needs a different
    # recovery hint: "wait for the model to finish loading" vs.
    # "check your internet connection".
    if isinstance(exc, BackendNotLoadedError):
        return (
            "The speech model was not loaded when dictation finished. "
            "Wait for it to finish loading, or open Settings to verify "
            "the model is available."
        )
    msg = str(exc).lower()
    name = type(exc).__name__
    # Walk the __cause__ chain — cloud_engines.py wraps errors with
    # raise RuntimeError(...) from exc, hiding the original type.
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
    object exposes no ``registry``, or the registry has no ``whisper``
    backend registered (cold start, or the user explicitly unloaded whisper).

    Used by :meth:`DictationPipeline._transcribe` to wire the local
    Whisper engine as the fallback for a CloudEngine, so that
    ``CloudEngine.transcribe_with_fallback`` actually has a local engine
    to fall back to when the cloud provider is unreachable.
    """
    try:
        models = getattr(app, "models", None)
    except Exception:  # pragma: no cover — defensive
        return None
    if models is None:
        return None
    registry = getattr(models, "registry", None)
    if registry is None:
        return None
    try:
        return registry.get("whisper")
    except Exception:  # pragma: no cover — defensive
        log.debug("[PIPELINE] _lookup_local_whisper: registry.get raised", exc_info=True)
        return None


@contextlib.contextmanager
def _timed_stage(timings: dict[str, float], name: str) -> typing.Iterator[None]:
    """Context manager that records a single stage's wall-clock duration.

    replaces the 10 inline ``_stage_t0 = time.perf_counter()``
    ``_<name>_ms = (time.perf_counter() - _stage_t0) * 1000`` blocks in
    ``DictationPipeline.run`` with a single DRY primitive. Adding an
    11th stage no longer requires hand-copying the 3-line pattern AND
    hand-adding a variable to the consolidated ``[PIPE-PERF]`` log
    format string — just wrap the stage call in
    ``with _timed_stage(_timings, "<name>")`` and the dict entry
    appears automatically.

    Notes:
      * The duration is recorded in *milliseconds* (matching the
        previous inline pattern) so the consolidated log format
        strings are unchanged.
      * On exception, the duration up to the raise is still recorded
        (the ``finally`` runs before the exception propagates) so a
        ``[PIPE-PERF]`` line emitted from the ``except`` block has a
        best-effort timing for the stage that failed.
      * The dict is mutated in-place; callers pass a single dict
        instance and read ``timings["<name>"]`` after the ``with``
        block exits.
    """
    t0 = time.perf_counter()
    try:
        yield
    finally:
        timings[name] = (time.perf_counter() - t0) * 1000


class _AbortWatcher:
    """Lightweight daemon thread that bridges the recording controller's
    cancel set to the active ASR engine's abort API.

    The recording controller's cancel path (ESC hotkey, watchdog
    force-recover) adds the current ``cycle_id`` to
    ``recording._cancelled_cycle_ids`` under
    ``_cancelled_cycle_ids_lock``. Pre-fix, that was the END of the
    abort story — the transcription thread kept running ctranslate2 /
    transformers / cloud-HTTP inference to completion (potentially
    10-30s for Whisper, 30s+ for cloud), then the late result was
    dropped by the pipeline's ``CancellationGuard`` before paste.

    This watcher polls ``_cancelled_cycle_ids`` every 100ms while
    inference is running; when the cycle appears in the set, it calls
    ``engine.request_abort()`` which sets the engine's ``_abort_event``
    so:

      * **Whisper** (``transcription.py``) — the segment loop breaks
        early on the next iteration; ``ctranslate2.Translator.interrupt()``
        is also best-effort called to unblock the current C-level call.
      * **Parakeet** (``parakeet_engine.py``) — the
        ``_AbortStoppingCriteria`` returns True on the next generated
        token, so ``model.generate()`` returns early. Long-audio chunk
        loops also break after the current chunk.
      * **Cloud** (``cloud_engines.py``) — the retry loop checks the
        event at the top of each iteration and bails out instead of
        issuing another 10s HTTP call.

    Polling is used (rather than a callback / condition variable)
    because the cancel path lives in ``recording_controller.py`` (a
    module this pipeline does not own) and the cancelled-set is the
    existing coordination point. 100ms granularity is a deliberate
    trade-off: short enough that the user perceives near-instant
    compute release, long enough that the polling overhead (one lock
    acquire + set lookup, ~1us) is negligible vs. the inference cost
    per iteration (~0.5-3s for Whisper, ~1-2s for Parakeet, ~1-2s for
    cloud). The watcher is a daemon thread so it never blocks process
    exit.

    Lifetime: started in ``DictationPipeline._transcribe`` before the
    transcribe call, stopped (via ``stop()``) in a ``finally`` block
    after the call returns or raises. The stop method sets the
    watcher's own stop event and joins with a 1s timeout — if the
    watcher is mid-poll it exits within 100ms; the 1s ceiling is
    defense-in-depth.
    """

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
                        "[PIPELINE] abort watcher detected cancel for cycle %s — signalling engine.request_abort()",
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
