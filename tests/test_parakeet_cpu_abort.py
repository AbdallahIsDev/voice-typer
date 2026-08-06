"""Abort-latency regression tests for ``ParakeetEngine._transcribe_impl``.

Pre-fix (OI-14): the CPU-fallback chunk loop in
``_transcribe_impl`` (parakeet_engine.py:1250-1270) had no
``_abort_event.is_set()`` check between chunks. The dictation
pipeline's cancel path (ESC / watchdog) sets ``_abort_event`` to stop
``model.generate()``'s token generation via
``_AbortStoppingCriteria``, but on the CPU-fallback path that only
stops the CURRENT chunk's generation — the loop proceeded to decode
the NEXT chunk, so ESC during a multi-chunk CPU decode waited for the
rest of the audio to finish instead of stopping promptly.

This is bounded-latency regression: a 2-minute audio split into 5
chunks on CPU (30-60s per chunk) meant ESC could take 2-5 minutes to
take effect, instead of the documented "stop after the current chunk".

The fix adds the same abort gate at the top of the chunk loop that the
GPU batched path (``_transcribe_chunks_batched``) already has.

 refactor: ``_transcribe_impl`` now delegates the chunked path
to ``_transcribe_chunks_batched`` (which already had the abort gate
+ OOM fallback). The OI-14 contract is therefore preserved — the
gate now lives in ``_transcribe_chunks_batched`` rather than inline in
``_transcribe_impl``, but the user-visible behaviour (ESC stops the
chunk loop with bounded latency) is unchanged. These tests now patch
``_transcribe_segment`` (which the batched path calls when
``_INFERENCE_BATCH_SIZE <= 1``) and force ``_INFERENCE_BATCH_SIZE = 1``
so the sequential branch is exercised (the same branch the pre-
``_transcribe_impl`` used). The batched branch's abort gate is the
same code, just inside a ``while`` loop instead of a ``for`` loop.

These tests pin the contract:

1. When ``_abort_event`` is set BEFORE the loop starts, NO chunks are
   decoded (immediate abort).
2. When ``_abort_event`` is set MID-LOOP (after chunk 2 of 3), chunk 3
   is NOT decoded (bounded-latency abort).
3. The abort log message is emitted (observability).
4. On the happy path (no abort), all chunks are decoded and merged
   (the fix doesn't break normal transcription).
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import numpy as np
import pytest


@pytest.fixture(autouse=True)
def _reset_parakeet_engine_class_state():
    """Reset ``ParakeetEngine`` class-level state between tests.

    Mirrors the fixture in ``tests/test_parakeet_engine.py`` — without
    this, a prior test's mocked torch/transformers leak into the next.
    """
    from voice_typer.server.parakeet_engine import ParakeetEngine

    saved = (
        ParakeetEngine._imports_loaded,
        ParakeetEngine._torch,
        ParakeetEngine._AutoModelForTDT,
        ParakeetEngine._AutoProcessor,
        ParakeetEngine._hf_home_set,
    )
    ParakeetEngine._imports_loaded = False
    ParakeetEngine._torch = None
    ParakeetEngine._AutoModelForTDT = None
    ParakeetEngine._AutoProcessor = None
    ParakeetEngine._hf_home_set = False
    yield
    (
        ParakeetEngine._imports_loaded,
        ParakeetEngine._torch,
        ParakeetEngine._AutoModelForTDT,
        ParakeetEngine._AutoProcessor,
        ParakeetEngine._hf_home_set,
    ) = saved


def _make_engine():
    """Build a ParakeetEngine WITHOUT touching torch / transformers.

    ``__init__`` calls ``_ensure_hf_env()`` which swallows all errors,
    so this is safe without mocks.
    """
    from voice_typer.server.parakeet_engine import ParakeetEngine

    return ParakeetEngine(device="cpu", language="en")


def _make_long_audio(seconds: float = 60.0) -> np.ndarray:
    """Build audio longer than ``_CHUNK_SECONDS`` (25s) so
    ``_transcribe_impl`` enters the chunked path."""
    # ParakeetEngine uses WHISPER_SAMPLE_RATE (16000).
    return np.ones(int(16000 * seconds), dtype=np.float32)


# ── OI-14: CPU-fallback chunk loop must check _abort_event ────────────


class TestParakeetCpuFallbackAbortGate:
    """The CPU-fallback chunk loop must check ``_abort_event`` at the
    top of each iteration so ESC stops remaining chunks.

    ``_transcribe_impl`` now delegates to
    ``_transcribe_chunks_batched``, which owns the abort gate. These
    tests force ``_INFERENCE_BATCH_SIZE = 1`` so the sequential branch
    is exercised (matching the pre- path) — the batched branch's
    abort gate is the same code, just inside a ``while`` loop.
    """

    def test_abort_set_before_loop_skips_all_chunks(self):
        """When ``_abort_event`` is set BEFORE the loop starts, NO chunks
        are decoded. Pre-fix, the loop decoded every chunk regardless."""
        engine = _make_engine()
        engine._INFERENCE_BATCH_SIZE = 1  # exercise the sequential branch
        engine._model = MagicMock()
        engine._processor = MagicMock()
        engine._abort_event.set()

        with patch.object(engine, "_transcribe_segment") as mock_segment:
            result = engine._transcribe_impl(_make_long_audio())

        assert mock_segment.call_count == 0, (
            "OI-14: when _abort_event is set before the loop starts, "
            "_transcribe_impl must NOT decode any chunks. Pre-fix, the "
            "loop had no abort gate and decoded every chunk."
        )
        assert result == "", (
            f"OI-14: aborted _transcribe_impl must return empty string (no chunks decoded). Got: {result!r}"
        )

    def test_abort_set_mid_loop_stops_remaining_chunks(self):
        """When ``_abort_event`` is set MID-LOOP (after chunk 2 of 3),
        chunk 3 must NOT be decoded. Pre-fix, the loop decoded all
        remaining chunks before returning."""
        engine = _make_engine()
        engine._INFERENCE_BATCH_SIZE = 1  # exercise the sequential branch
        engine._model = MagicMock()
        engine._processor = MagicMock()

        # 60s audio at 25s chunk / 3s overlap → step 22s → 3 chunks.
        # chunk 0: [0:25s], chunk 1: [22s:47s], chunk 2: [44s:60s].
        call_log: list[int] = []

        def _spy_segment(audio, *args, **kwargs):
            call_log.append(len(call_log))
            # After decoding chunk 2 (the 2nd call), simulate the
            # dictation pipeline's ESC setting the abort event. The
            # NEXT loop iteration must see it set and break.
            if len(call_log) == 2:
                engine._abort_event.set()
            return f"chunk{len(call_log)}"

        with patch.object(engine, "_transcribe_segment", side_effect=_spy_segment):
            engine._transcribe_impl(_make_long_audio())

        assert len(call_log) == 2, (
            "OI-14: when _abort_event is set after chunk 2, chunk 3 must "
            f"NOT be decoded. Pre-fix, all 3 chunks were decoded. Calls: {call_log}"
        )

    def test_abort_emits_log_message(self):
        """The abort path must emit an info log so the operator can see
        why transcription stopped early (observability — without this,
        ESC-during-CPU-fallback looks like a silent truncation)."""
        engine = _make_engine()
        engine._INFERENCE_BATCH_SIZE = 1  # exercise the sequential branch
        engine._model = MagicMock()
        engine._processor = MagicMock()
        engine._abort_event.set()

        with (
            patch.object(engine, "_transcribe_segment"),
            patch("voice_typer.server.parakeet_engine.log") as mock_log,
        ):
            engine._transcribe_impl(_make_long_audio())

        # The info-level abort message must be present.
        info_calls = [c for c in mock_log.info.call_args_list if "Abort requested" in str(c)]
        assert info_calls, (
            "OI-14: _transcribe_impl must log an info message containing "
            "'Abort requested' when the abort gate fires, so operators "
            "can distinguish an ESC abort from a silent truncation bug."
        )

    def test_no_abort_decodes_all_chunks(self):
        """Happy path: when ``_abort_event`` is NOT set, all chunks must
        be decoded and merged. This guards against the fix accidentally
        breaking normal (non-aborted) CPU transcription."""
        engine = _make_engine()
        engine._INFERENCE_BATCH_SIZE = 1  # exercise the sequential branch
        engine._model = MagicMock()
        engine._processor = MagicMock()
        # Abort NOT set — normal transcription.

        call_count = {"n": 0}

        def _spy_segment(audio, *args, **kwargs):
            call_count["n"] += 1
            return f"chunk{call_count['n']}"

        with patch.object(engine, "_transcribe_segment", side_effect=_spy_segment):
            result = engine._transcribe_impl(_make_long_audio())

        # 60s audio → 3 chunks (see test_abort_set_mid_loop_stops_remaining_chunks).
        assert call_count["n"] == 3, (
            "OI-14: on the happy path (no abort), _transcribe_impl must "
            "decode ALL chunks. The abort gate must not break normal "
            f"transcription. Calls: {call_count['n']}"
        )
        # All 3 chunk texts must be present in the merged result.
        assert "chunk1" in result
        assert "chunk2" in result
        assert "chunk3" in result

    def test_abort_check_is_at_loop_top_not_bottom(self):
        """The abort check must be at the TOP of the loop (before
        ``_transcribe_segment``), not at the bottom. A
        bottom-of-loop check would decode one extra chunk after ESC
        before breaking — defeating the bounded-latency contract."""
        engine = _make_engine()
        engine._INFERENCE_BATCH_SIZE = 1  # exercise the sequential branch
        engine._model = MagicMock()
        engine._processor = MagicMock()

        # Set abort BEFORE the loop. If the check is at the top, zero
        # calls. If at the bottom, one call (chunk 1 decoded, then the
        # bottom check fires and breaks — but chunk 1 was still decoded,
        # which is what we're trying to prevent).
        engine._abort_event.set()

        with patch.object(engine, "_transcribe_segment") as mock_segment:
            engine._transcribe_impl(_make_long_audio())

        assert mock_segment.call_count == 0, (
            "OI-14: the abort check must be at the TOP of the chunk loop "
            "(before _transcribe_segment), not the bottom. A "
            "bottom check would decode one extra chunk after ESC — "
            "defeating the bounded-latency contract."
        )

    def test_abort_gate_present_in_source(self):
        """Source-level guard: ``_transcribe_chunks_batched`` (which
        ``_transcribe_impl`` delegates to) must contain the abort gate
        (``_abort_event.is_set()`` + ``break``). This catches a future
        refactor that accidentally removes the gate.

        the gate moved from ``_transcribe_impl`` to
        ``_transcribe_chunks_batched`` (the delegation target). The
        source guard now inspects ``_transcribe_chunks_batched``.
        """
        import inspect

        from voice_typer.server.parakeet_engine import ParakeetEngine

        src = inspect.getsource(ParakeetEngine._transcribe_chunks_batched)
        assert "_abort_event.is_set()" in src, (
            "OI-14: _transcribe_chunks_batched must check _abort_event.is_set() "
            "in the chunk loop. The gate appears to have been removed."
        )
        assert "break" in src, (
            "OI-14: _transcribe_chunks_batched must `break` out of the chunk loop "
            "when _abort_event is set. The break appears to have been "
            "removed."
        )

    def test_transcribe_impl_delegates_to_batched_path(self):
        """``_transcribe_impl`` (the CPU-fallback path) must
        delegate the chunked loop to ``_transcribe_chunks_batched``
        rather than running a sequential inline loop. The batched path
        respects ``_INFERENCE_BATCH_SIZE`` and has the OOM-fallback —
        bypassing it (the pre- bug) meant a CUDA OOM during CPU
        fallback crashed the whole transcription instead of degrading
        to per-chunk sequential, and the CPU path couldn't benefit
        from batching on machines where it's safe."""
        import inspect

        from voice_typer.server.parakeet_engine import ParakeetEngine

        src = inspect.getsource(ParakeetEngine._transcribe_impl)
        assert "_transcribe_chunks_batched" in src, (
            "_transcribe_impl must delegate to "
            "_transcribe_chunks_batched for the chunked path. The "
            "inline sequential loop appears to have been restored."
        )
        assert "_merge_chunks" in src, (
            "_transcribe_impl must call _merge_chunks on the "
            "batched results."
        )

    def test_batched_path_has_oom_fallback(self):
        """``_transcribe_chunks_batched`` must fall back to
        per-chunk sequential inference on a CUDA OOM. Pre-, the
        CPU-fallback path (which used ``_transcribe_segment_unlocked``
        in an inline loop) had no OOM-fallback — a CUDA OOM during CPU
        fallback crashed the whole transcription."""
        import inspect

        from voice_typer.server.parakeet_engine import ParakeetEngine

        src = inspect.getsource(ParakeetEngine._transcribe_chunks_batched)
        assert "out of memory" in src, (
            "_transcribe_chunks_batched must check for 'out of "
            "memory' in the exception string and fall back to "
            "sequential per-chunk inference on OOM."
        )
