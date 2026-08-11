"""Transcription result normalization for ``TranscriptionEngine``.

Extracted from ``voice_typer/server/transcription.py`` so the engine
module stays focused on the load/transcribe pipeline. The functions
here are module-level so they can be unit-tested in isolation.

* :func:`transcribe_unlocked` — the body of
  :meth:`TranscriptionEngine._transcribe_unlocked`. Drives the
  faster-whisper ``model.transcribe(...)`` call, iterates the segment
  generator (with abort-token check between iterations), collects
  per-segment logprobs / no_speech_probs, applies the
  low-audio-hallucination rejection gate, and returns the joined
  text. PII-safety: per-segment DEBUG logs are gated by
  ``config.log_transcriptions`` AND wrapped in ``redact_pii`` when
  emitted.
* :func:`transcribe_words_unlocked` — the body of
  :meth:`TranscriptionEngine._transcribe_words_unlocked`. Streaming
  word-timestamp variant of the above (used by the streaming
  pipeline). Returns a list of :class:`streaming.WordTiming` objects.
* :func:`format_optional_mean` — formats a list of floats as a
  2-decimal mean, or ``"n/a"`` if empty. Used by the VAD-result log
  line in :func:`transcribe_unlocked`.

The engine methods (``_transcribe_unlocked``, ``_transcribe_words_unlocked``,
``_format_optional_mean``) are now one-line delegators to these
functions — no behavior change vs the pre-refactor inline bodies.
"""

from __future__ import annotations

import logging
from typing import Any

from voice_typer.server._audio_constants import WHISPER_SAMPLE_RATE as _WHISPER_SAMPLE_RATE
from voice_typer.server._lazy_import import lazy_module
from voice_typer.server.hallucination import (
    log_hallucination_rejection,
    should_reject_low_audio_hallucination,
)

np = lazy_module("numpy")

# Use the ``transcription`` logger name so log records emitted from this
# extracted module are captured by tests that filter by
# ``logger="voice_typer.server.transcription"`` (the historical logger
# name when this code lived inline in ``transcription.py``).
log = logging.getLogger("voice_typer.server.transcription")


def format_optional_mean(values: list[float]) -> str:
    """Format a list of floats as a 2-decimal mean, or 'n/a' if empty.

    Small helper kept as-is because it has two call sites
    (the avg_logprob + no_speech_prob log fields in
    :func:`transcribe_unlocked`) and inlining would duplicate the
    empty-list check.
    """
    if not values:
        return "n/a"
    return f"{sum(values) / len(values):.2f}"


def reject_low_audio_hallucination(
    engine: Any,
    *,
    result: str,
    rms: float,
    peak: float,
    silence_pct: float,
    duration: float,
    first_segment_start: float | None,
    last_segment_end: float | None,
) -> bool:
    """Thin delegator to :func:`hallucination.should_reject_low_audio_hallucination`.

    Kept as a wrapper so :meth:`TranscriptionEngine._should_reject_low_audio_hallucination`
    can delegate without the engine module re-importing ``hallucination``
    (the import is done here, at module-load time, so the engine module
    stays lean).
    """
    return should_reject_low_audio_hallucination(
        result,
        rms,
        peak=peak,
        silence_pct=silence_pct,
        duration=duration,
        first_segment_start=first_segment_start,
        last_segment_end=last_segment_end,
    )


def transcribe_unlocked(
    engine: Any,
    audio,
    audio_stats: tuple[float, float, float] | None = None,
) -> str:
    """Body of :meth:`TranscriptionEngine._transcribe_unlocked`.

    Drives the faster-whisper ``model.transcribe(...)`` call, iterates
    the segment generator (with abort-token check between iterations),
    collects per-segment logprobs / no_speech_probs, applies the
    low-audio-hallucination rejection gate, and returns the joined
    text.

    Reads per-cycle state from the engine argument (``engine._model``,
    ``engine.beam_size``, ``engine.language``, ``engine._abort_event``,
    ``engine.config``).
    """
    if engine._model is None:
        raise RuntimeError("Model not loaded. Call load() first.")

    if len(audio) == 0:
        return ""

    # Log audio statistics for diagnostics
    duration = len(audio) / _WHISPER_SAMPLE_RATE
    # reuse pre-computed stats when provided (avoids
    # 1-3 ms + 3× 1.9 MB transient memory per dictation).
    if audio_stats is not None:
        rms, peak, silence_pct = audio_stats
    else:
        rms = float(np.sqrt(np.mean(np.square(audio), dtype=np.float64)))
        peak = float(np.max(np.abs(audio)))
        silence_pct = float(np.sum(np.abs(audio) < 0.001) / audio.size * 100)
    log.info(
        "[TRANSCRIBE] Input audio: samples=%d, duration=%.1fs, RMS=%.6f, peak=%.6f, silence_pct=%.1f%%",
        len(audio),
        duration,
        rms,
        peak,
        silence_pct,
    )
    if rms < 0.001:
        log.warning(
            "[TRANSCRIBE] Near-silence input (RMS=%.6f). Speech detection is unlikely.",
            rms,
        )

    segments, info = engine._model.transcribe(
        audio,
        beam_size=engine.beam_size,
        best_of=engine.best_of,
        temperature=0.0,
        vad_filter=True,
        vad_parameters=dict(
            min_silence_duration_ms=500,
            speech_pad_ms=200,
        ),
        language=engine.language,
        condition_on_previous_text=engine.condition_on_previous_text,
        without_timestamps=True,
    )

    # Collect segments and log VAD info
    text_parts = []
    segment_count = 0
    first_segment_start = None
    last_segment_end = None
    avg_logprobs = []
    no_speech_probs = []
    # hoist the per-segment ``log_transcriptions`` flag and
    # ``redact_pii`` import OUT of the segment loop. Pre-fix, the
    # ``getattr(engine.config, 'log_transcriptions', False)`` ran once
    # per segment and the ``from voice_typer.server.security import
    # redact_pii`` ran an ``importlib`` cache lookup per segment
    # (whenever the flag was True). For a 100+ segment long-form
    # dictation with ``log_transcriptions=True``, the redundant
    # attribute access + import lookups added ~1ms of pure overhead
    # before any actual regex work. Hoisting computes the flag once
    # and reuses the imported function for every segment.
    _log_transcriptions_flag = engine.config is not None and getattr(engine.config, "log_transcriptions", False)
    _redact_pii = None
    if _log_transcriptions_flag:
        try:
            from voice_typer.server.security import redact_pii as _redact_pii
        except Exception:
            _redact_pii = None
    for seg in segments:
        # Check the abort token BETWEEN segment iterations. The
        # ``segments`` generator yields one segment at a time, with
        # each ``next()`` call driving a ctranslate2 decoding step
        # (typically 0.5-3s per segment). Checking here lets the
        # ESC / watchdog cancel path break out of the loop within
        # one segment of being signalled — bounded latency instead
        # of waiting for the full audio to decode. ``request_abort()``
        # also best-effort calls ``ctranslate2.Translator.interrupt()``
        # so the CURRENT segment's C-level call returns promptly.
        if engine._abort_event.is_set():
            log.info(
                "[TRANSCRIBE] Abort requested — stopping segment loop early (completed %d segments, %d text parts)",
                segment_count,
                len(text_parts),
            )
            break
        segment_count += 1
        start = seg.start or 0.0
        end = seg.end or start
        if first_segment_start is None:
            first_segment_start = start
        last_segment_end = end
        avg_logprob = getattr(seg, "avg_logprob", None)
        no_speech_prob = getattr(seg, "no_speech_prob", None)
        if isinstance(avg_logprob, int | float):
            avg_logprobs.append(float(avg_logprob))
        if isinstance(no_speech_prob, int | float):
            no_speech_probs.append(float(no_speech_prob))
        if seg.text.strip():
            text_parts.append(seg.text.strip())
            # gate the per-segment DEBUG log by
            # ``log_transcriptions`` and apply ``redact_pii`` when
            # enabled. Pre-fix, raw segment text was logged whenever
            # DEBUG logging was active — leaking any PII the user
            # dictated even though the operator had not opted into
            # transcription logging.
            #
            # When ``log_transcriptions`` is False (the default), we
            # emit NO segment DEBUG log at all — not even a char-count
            # summary. The XZ-PRIV-04 regression tests pin this
            # contract: any "[TRANSCRIBE] Segment" DEBUG record while
            # the flag is off is treated as a PII leak (the very
            # presence of a segment-timing log can confirm a segment
            # was decoded at a given timestamp, which is metadata the
            # user did not opt into). Operators who need segment-level
            # diagnostics flip ``log_transcriptions=True`` (which then
            # routes the text through ``redact_pii``).
            _seg_text = seg.text.strip()
            if _log_transcriptions_flag and _redact_pii is not None:
                try:
                    _safe_seg_text = _redact_pii(_seg_text)
                except Exception:
                    # fall back to a redacted marker only — do NOT
                    # log the raw text even truncated (HU-13, AP-11
                    # twin). The opt-in ``log_transcriptions`` flag is a
                    # privacy backstop the user explicitly enabled, and
                    # a ``redact_pii`` failure (import failure / regex
                    # bug) means PII cannot be guaranteed masked.
                    # Truncating to 80 chars does NOT redact — an
                    # 80-char window can still contain an email address,
                    # phone number, or SSN fragment. Emit a marker +
                    # the segment boundaries and skip the DEBUG log.
                    log.warning(
                        "[TRANSCRIBE] Segment: [%.1fs - %.1fs] "
                        "<redaction-engine-failed — segment text NOT "
                        "logged to preserve PII guarantee; enable "
                        "voice_typer.server.security.redact_pii and "
                        "retry>",
                        start,
                        end,
                    )
                    _safe_seg_text = None  # skip the log.debug below
                if _safe_seg_text is not None:
                    log.debug(
                        "[TRANSCRIBE] Segment: [%.1fs - %.1fs] %s",
                        start,
                        end,
                        _safe_seg_text,
                    )

    log.info(
        "[TRANSCRIBE] VAD result: language=%s (prob=%.2f), "
        "segments=%d, text_segments=%d, avg_logprob=%s, no_speech_prob=%s",
        info.language,
        info.language_probability,
        segment_count,
        len(text_parts),
        format_optional_mean(avg_logprobs),
        format_optional_mean(no_speech_probs),
    )

    result = " ".join(text_parts).strip()
    if reject_low_audio_hallucination(
        engine,
        result=result,
        rms=rms,
        peak=peak,
        silence_pct=silence_pct,
        duration=duration,
        first_segment_start=first_segment_start,
        last_segment_end=last_segment_end,
    ):
        # Use the PII-safe logging helper instead of raw text
        log_transcriptions = engine.config is not None and getattr(engine.config, "log_transcriptions", False)
        log_hallucination_rejection(
            "[TRANSCRIBE]",
            result,
            reason="low-audio hallucination",
            log_transcriptions=log_transcriptions,
        )
        log.info(
            "[TRANSCRIBE] Hallucination stats: duration=%.1fs, RMS=%.6f, peak=%.6f, silence=%.1f%%",
            duration,
            rms,
            peak,
            silence_pct,
        )
        return ""
    if result:
        log.info("[TRANSCRIBE] Result: %d chars", len(result))
    else:
        log.info(
            "[TRANSCRIBE] No speech detected (RMS=%.6f, silence=%.1f%%)",
            rms,
            silence_pct,
        )
    return result


def transcribe_words_unlocked(
    engine: Any,
    audio,
    offset_seconds: float,
):
    """Body of :meth:`TranscriptionEngine._transcribe_words_unlocked`.

    Streaming word-timestamp variant of :func:`transcribe_unlocked`.
    Returns a list of :class:`streaming.WordTiming` objects.
    """
    if engine._model is None:
        raise RuntimeError("Model not loaded. Call load() first.")

    if len(audio) == 0:
        return []

    from voice_typer.server.streaming import WordTiming

    segments, _info = engine._model.transcribe(
        audio,
        beam_size=engine.beam_size,
        best_of=engine.best_of,
        temperature=0.0,
        vad_filter=True,
        vad_parameters=dict(
            min_silence_duration_ms=500,
            speech_pad_ms=200,
        ),
        language=engine.language,
        condition_on_previous_text=engine.condition_on_previous_text,
        word_timestamps=True,
        without_timestamps=False,
    )

    words = []
    segment_count = 0
    for seg in segments:
        # Check the abort token BETWEEN segment iterations,
        # mirroring the batch path (``transcribe_unlocked`` above).
        # ``segments`` is a generator that yields one segment at a
        # time, with each ``next()`` call driving a ctranslate2
        # decoding step. Without this check, an ESC / watchdog
        # cancel during streaming word-timestamp transcription would
        # only take effect after the full audio finished decoding —
        # unbounded latency instead of within-one-segment latency.
        if engine._abort_event.is_set():
            log.info(
                "[TRANSCRIBE] Abort requested — stopping streaming "
                "words segment loop early (completed %d segments, "
                "%d words)",
                segment_count,
                len(words),
            )
            break
        segment_count += 1
        for word in getattr(seg, "words", None) or []:
            text = (word.word or "").strip()
            if not text:
                continue
            start = (word.start or 0.0) + offset_seconds
            end = (word.end or word.start or 0.0) + offset_seconds
            words.append(
                WordTiming(
                    word=text,
                    start_seconds=start,
                    end_seconds=end,
                )
            )
    return words
