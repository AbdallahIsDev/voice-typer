"""Offline transcription for the worker (master plan §7.4).

This module owns the ``transcribe_offline`` contract's worker half: the
slim-core sidecar forwards ``{audio_path, sample_rate, language}`` to
the worker over the worker's dedicated WS hop, and the worker runs a
real ASR inference and returns the transcript via the
``transcribe_offline_result`` push event.

It reuses the exact same backend machinery the slim-core sidecar uses
(``AsrBackendRegistry`` from ``voice_typer/server/asr_registry.py``) so
there is ONE engine-construction path for both processes — the worker
is a second consumer of the same registry, not a parallel
re-implementation (E7 / P2: no duplicated engine logic).

Engine lifecycle (§7.3 "long-lived worker"): the engine is built lazily
on the first ``transcribe_offline`` request and cached for the worker's
lifetime (a fresh request does not rebuild it). A request carrying a
``language`` that differs from the cached engine's language rebuilds the
engine with the request's language — language is fixed at engine
construction in this codebase (``TranscriptionEngine.language`` is set
in ``__init__``), so a per-request override requires reconstruction.

Concurrency: transcription is blocking C-level work (0.5-30 s per file)
and MUST NOT run on the asyncio event loop — the caller
(``_ws_server._handle_connection``) wraps :func:`transcribe_file` in
``asyncio.to_thread`` so heartbeats and the ``shutdown`` command stay
responsive mid-inference. A ``threading.Lock`` guards the lazy
construction so two racing requests cannot double-build the engine.

Errors: any failure (missing file, engine load failure, decode error)
is caught and returned as ``{"text": "", "latency_ms": <elapsed>,
"error": <message>}`` — the caller turns that into a
``transcribe_offline_result`` push so the slim-core sidecar's caller
never hangs waiting for an event that will never arrive.
"""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from voice_typer.server.asr_registry import AsrBackendRegistry

log = logging.getLogger("voice_typer.worker")

# The sample rate every ASR backend in this project expects after
# resampling (whisper-family / parakeet / qwen all consume 16 kHz).
_ASR_SAMPLE_RATE = 16_000


def _load_wav_float32(path: str | Path) -> tuple[object, int]:
    """Load a WAV file into a float32 numpy array + its native sample rate.

    Returns ``(audio_f32, sample_rate)`` where ``audio_f32`` is a 1-D
    float32 array in [-1.0, 1.0] (mono — multi-channel files are
    downmixed by averaging). Raises on any decode failure so the caller
    can produce a structured error result.
    """
    import wave

    import numpy as np

    with wave.open(str(path), "rb") as wf:
        sample_rate = wf.getframerate()
        channels = wf.getnchannels()
        frame_count = wf.getnframes()
        raw = wf.readframes(frame_count)

    if frame_count == 0:
        return (np.zeros(0, dtype=np.float32), sample_rate)

    audio = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32767.0
    if channels > 1:
        audio = audio.reshape(-1, channels).mean(axis=1)
    return (audio, sample_rate)


def _resample_to_16k(audio: object, sample_rate: int) -> object:
    """Resample ``audio`` to 16 kHz if needed.

    Delegates to the shared :func:`voice_typer.server.recording.resampling.resample_audio`
    (scipy polyphase with cached FIR taps, linear-interp fallback) so
    the worker uses the exact same resampler as the slim-core sidecar
    (E7 / P2: no duplicated resampling logic).
    """
    if sample_rate == _ASR_SAMPLE_RATE:
        return audio
    from voice_typer.server.recording.resampling import resample_audio

    return resample_audio(audio, int(sample_rate), _ASR_SAMPLE_RATE, log=log)


class WorkerTranscriber:
    """Lazily-constructed, cached ASR engine for ``transcribe_offline``.

    Mirrors ``ModelManager._ensure_engine``'s kwargs so the worker
    builds the exact same engine the slim-core sidecar would build for
    the same config. The engine is built once and cached for the
    worker's lifetime (§7.3 long-lived worker).
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._registry = None
        self._engine = None
        self._engine_language: str | None = None

    # ── engine construction ──────────────────────────────────────────

    def _build_registry(self, config: Any) -> AsrBackendRegistry:
        """Construct a fresh ``AsrBackendRegistry`` from ``config``."""
        from voice_typer.server.asr_registry import AsrBackendRegistry

        registry = AsrBackendRegistry(config)
        name = getattr(config, "asr_backend", "whisper")
        if name == "parakeet":
            registry.create(
                "parakeet",
                parakeet_kwargs=dict(
                    device=getattr(config, "device", "auto"),
                    language=getattr(config, "language", None),
                ),
            )
        elif name == "qwen":
            registry.create(
                "qwen",
                qwen_kwargs=dict(
                    model_path=getattr(config, "qwen_model_path", None),
                    device=getattr(config, "device", "auto"),
                    language=getattr(config, "language", None),
                ),
            )
        else:
            registry.create(
                "whisper",
                whisper_kwargs=dict(
                    model_size=getattr(config, "model_size", "small.en"),
                    device=getattr(config, "device", "auto"),
                    language=getattr(config, "language", None),
                    beam_size=getattr(config, "beam_size", 1),
                    best_of=getattr(config, "best_of", 1),
                    condition_on_previous_text=getattr(config, "condition_on_previous_text", False),
                ),
            )
        return registry

    def _ensure_engine(self, language: str | None) -> Any:
        """Return the cached engine, building it if absent or language changed."""
        with self._lock:
            if self._engine is not None and self._engine_language == language:
                return self._engine
            from voice_typer.server.config import Config

            config = Config.load()
            registry = self._build_registry(config)
            backend = registry.load_with_fallback()
            if backend is None:
                raise RuntimeError(
                    "ASR backend failed to load — model not downloaded or "
                    "integrity check failed (open the Models page and download it)"
                )
            self._registry = registry
            self._engine = backend
            self._engine_language = language
            log.info(
                "[WORKER] offline ASR engine ready: backend=%s language=%s",
                getattr(config, "asr_backend", "whisper"),
                language,
            )
            return backend

    # ── public API ───────────────────────────────────────────────────

    def transcribe_file(self, audio_path: str, sample_rate: int | None, language: str | None) -> dict:
        """Transcribe a WAV file; return the ``transcribe_offline_result`` payload.

        Returns ``{"text": str, "latency_ms": int, "error": str|None}``.
        Never raises — errors are captured into the payload so the
        caller can always emit a result event.
        """
        t0 = time.perf_counter()

        def _done(result: dict) -> dict:
            result["latency_ms"] = int((time.perf_counter() - t0) * 1000)
            return result

        if not audio_path:
            return _done({"text": "", "error": "missing audio_path"})
        if not Path(audio_path).is_file():
            return _done({"text": "", "error": f"audio file not found: {audio_path}"})
        try:
            audio, file_rate = _load_wav_float32(audio_path)
        except Exception as exc:  # noqa: BLE001 — structured error result
            log.warning("[WORKER] failed to decode %s: %s", audio_path, exc)
            return _done({"text": "", "error": f"failed to decode audio: {exc}"})
        audio = _resample_to_16k(audio, file_rate)
        try:
            engine = self._ensure_engine(language)
            # Run inference directly on the loaded backend (the
            # registry's busy-flag wrapper is for the slim-core
            # sidecar's concurrent dictation flow; the worker has a
            # single client and serialized requests).
            text = str(engine.transcribe_with_fallback(audio) or "").strip()
            log.info(
                "[WORKER] offline transcription complete (len=%d chars)_%s",
                len(text),
                f"{time.perf_counter() - t0:.1f}s",
            )
            return _done({"text": text, "error": None})
        except Exception as exc:  # noqa: BLE001 — structured error result
            log.exception("[WORKER] offline transcription failed: %s", exc)
            return _done({"text": "", "error": f"transcription failed: {exc}"})


# Module-level singleton so the engine survives across WS connections
# (the slim-core sidecar may briefly disconnect + reconnect per the
# respawn scheduler, §7.2 — the loaded engine must not be rebuilt).
_TRANSCRIBER: WorkerTranscriber | None = None
_TRANSCRIBER_LOCK = threading.Lock()


def get_transcriber() -> WorkerTranscriber:
    """Return the process-wide :class:`WorkerTranscriber` singleton."""
    global _TRANSCRIBER
    if _TRANSCRIBER is None:
        with _TRANSCRIBER_LOCK:
            if _TRANSCRIBER is None:
                _TRANSCRIBER = WorkerTranscriber()
    return _TRANSCRIBER


__all__ = [
    "WorkerTranscriber",
    "get_transcriber",
    "_ASR_SAMPLE_RATE",
    "_load_wav_float32",
    "_resample_to_16k",
]
