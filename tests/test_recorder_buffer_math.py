"""Recorder buffer math at 16kHz AND 48kHz (rate-scaled blocks).

Historical baseline (the original fix): the main buffer was sized
against a stale 1024-sample/16kHz assumption (``chunk_seconds =
0.064``) while the actual blocksize was 512 and the effective sample
rate could be 44.1/48kHz — a 30-min dictation silently lost its first
~25 min via deque maxlen eviction, and the pre-roll deque sized from
``config.sample_rate`` (16kHz) only captured ~0.33s of a 1.0s pre-roll
at 48 kHz.

Current contract (rate-scaled blocks): the stream delivers ~32 ms
chunks at EVERY native rate (``scaled_audio_blocksize`` — 512 @ 16 kHz,
1536 @ 48 kHz, 1411 @ 44.1 kHz), so the buffer math is DURATION-based:
``chunk_seconds = scaled_audio_blocksize(sr) / sr`` and every deque
maxlen is computed from that. Pinning DURATION (not chunk count) is the
regression gate: a fixed-512 chunk computation silently over-captures
~3× the configured pre-roll at 48 kHz (~6× at 96 kHz) once the stream
scales its blocks.
"""

from __future__ import annotations

import pytest
from voice_typer.server._audio_constants import scaled_audio_blocksize

REPO_ROOT_CONTEXT = "voice_typer.server.recording"  # noqa: N816 (readability)


# ── Test helpers ───────────────────────────────────────────────────


class _OkStream:
    """No-op ``InputStream`` mock that records the requested samplerate.

    Mirrors the ``_OkStream`` in ``tests/test_audio_callback.py``
    but stores ``samplerate`` so the Bluetooth-HFP detection branch in
    ``start()`` (which reads ``stream.samplerate``) doesn't blow up.
    """

    def __init__(self, *args, **kwargs):
        self.samplerate = kwargs.get("samplerate", 16000)
        self._blocksize = kwargs.get("blocksize", 512)

    def start(self):
        pass

    def stop(self):
        pass

    def close(self):
        pass


def _patch_sd(monkeypatch, recording_mod, native_rate: int) -> None:
    """Patch ``sounddevice`` with a no-op ``InputStream`` + device query
    that reports the given native sample rate.

    Matches the two PortAudio call shapes used by ``start()``:
    ``sd.query_devices()`` (enumerate) and ``sd.query_devices(device)``
    / ``sd.query_devices(kind="input")`` (single device).
    """
    monkeypatch.setattr(recording_mod.sd, "InputStream", _OkStream)

    def _query_devices(*args, **kwargs):
        device_dict = {
            "max_input_channels": 1,
            "default_samplerate": native_rate,
            "hostapi": 0,
            "index": 0,
            "name": f"Mock Input {native_rate}Hz",
        }
        # No-args call → enumerate (returns iterable of devices).
        if not args and not kwargs:
            return [device_dict]
        return device_dict

    monkeypatch.setattr(recording_mod.sd, "query_devices", _query_devices)
    monkeypatch.setattr(recording_mod.sd, "query_hostapis", lambda idx=None: {"name": "MME"})


def _make_recorder(max_rec: int = 1800, preroll_seconds: float = 1.0):
    """Build a ``Recorder`` with a ``MagicMock`` config suitable for
    buffer-math tests.

    ``sample_rate=16000`` is the Whisper target rate (NOT the device
    native rate — that's the whole point of XV-20). ``microphone=None``
    selects the system default device, so the candidate loop calls
    ``_resolve_effective_sample_rate(None)`` which queries
    ``sd.query_devices(kind="input")`` — the device our mock patches.
    Construction is delegated to the shared canonical factory (XS-42
    helper dedup) with the buffer-math-relevant fields overridden.
    """
    from tests.fixtures.ipc_test_helpers import make_fake_recorder

    return make_fake_recorder(
        max_recording_time_seconds=max_rec,
        pre_roll_buffer_seconds=preroll_seconds,
        recording_channels=1,
    )


# main recording buffer scales with effective_sr ────────


class TestMainBufferSizing:
    """The main recording buffer's maxlen must scale with the device's
    effective sample rate and the rate-scaled blocksize so a 30-min
    dictation at 48kHz doesn't silently lose the first ~25 min via
    deque eviction."""

    @pytest.mark.parametrize(
        "native_rate",
        [16000, 48000],
        ids=["16kHz-native", "48kHz-native"],
    )
    def test_buffer_capacity_holds_full_max_recording_time(self, monkeypatch, native_rate):
        """At both 16kHz and 48kHz, the buffer maxlen must be large
        enough to hold ``max_recording_time_seconds=1800`` (30 min) of
        audio.

        Historical bug: the buffer was sized against a stale 0.064s
        chunk-duration assumption, so 30000 default chunks only held
        ~5.3 min at 48kHz and a 30-min dictation lost the first ~25
        min. Contract: ``chunk_seconds = scaled_audio_blocksize(sr) /
        sr`` (~32 ms at every native rate) and the deque is resized to
        ``int(1800 / chunk_seconds) + 1000``.
        """
        import voice_typer.server.recording as recording_mod

        _patch_sd(monkeypatch, recording_mod, native_rate=native_rate)
        r = _make_recorder(max_rec=1800, preroll_seconds=0.0)
        try:
            r.start()
            # The device's native rate must be the effective rate.
            assert r._effective_sr == native_rate, (
                f"expected _effective_sr={native_rate} (device native), got {r._effective_sr}"
            )

            blocksize = scaled_audio_blocksize(native_rate)
            chunk_seconds = blocksize / native_rate
            expected_min_chunks = int(1800 / chunk_seconds)  # no safety margin
            actual_maxlen = r._audio_pipeline._buffer.maxlen or 0
            assert actual_maxlen >= expected_min_chunks, (
                f"At {native_rate}Hz, buffer maxlen {actual_maxlen} must be "
                f">= {expected_min_chunks} chunks (1800s / "
                f"{chunk_seconds:.4f}s/chunk at blocksize={blocksize}) to hold a "
                f"30-min dictation."
            )
        finally:
            r.stop()

    def test_buffer_chunk_count_is_rate_invariant_with_scaled_blocks(self, monkeypatch):
        """Core rate-scaling invariant: with ~32 ms chunks at every
        native rate, the buffer's CHUNK count for a fixed duration is
        rate-invariant — 48 kHz and 16 kHz devices get the same number
        of (3×-larger) chunks.

        Pre-scaling regression: a fixed-512 chunk computation sized the
        48 kHz buffer for 3× the chunks the stream actually delivers
        (each scaled chunk holds 3× the samples).
        """
        import voice_typer.server.recording as recording_mod

        maxlens: dict[int, int] = {}
        for native_rate in (16000, 48000):
            _patch_sd(monkeypatch, recording_mod, native_rate=native_rate)
            r = _make_recorder(max_rec=1800, preroll_seconds=0.0)
            try:
                r.start()
                maxlens[native_rate] = r._audio_pipeline._buffer.maxlen or 0
            finally:
                r.stop()

        assert maxlens[48000] == maxlens[16000], (
            f"with rate-scaled ~32 ms chunks, the chunk count for a fixed "
            f"duration must be rate-invariant: 48kHz maxlen {maxlens[48000]} "
            f"vs 16kHz maxlen {maxlens[16000]}."
        )
        # Duration contract at both rates.
        for native_rate, maxlen in maxlens.items():
            chunk_seconds = scaled_audio_blocksize(native_rate) / native_rate
            assert maxlen * chunk_seconds >= 1800, (
                f"buffer must hold 1800s at {native_rate}Hz: "
                f"{maxlen} chunks × {chunk_seconds:.4f}s = {maxlen * chunk_seconds:.0f}s"
            )

    def test_default_16khz_preserves_existing_behavior(self, monkeypatch):
        """At 16kHz with the default ``max_recording_time_seconds=900``,
        the buffer must NOT be resized — ``DEFAULT_MAX_BUFFER_CHUNKS=30000``
        is sufficient (900s / (512/16000) = 28125 chunks < 30000).

        This pins the 'preserves default 16kHz/512-sample behavior'
        requirement from the XV-20 fix brief: the existing 16kHz
        codepath (no resampling) is unchanged.
        """
        import voice_typer.server.recording as recording_mod
        from voice_typer.server.recording.recorder import DEFAULT_MAX_BUFFER_CHUNKS

        _patch_sd(monkeypatch, recording_mod, native_rate=16000)
        r = _make_recorder(max_rec=900, preroll_seconds=0.0)
        try:
            r.start()
            assert r._audio_pipeline._buffer.maxlen == DEFAULT_MAX_BUFFER_CHUNKS, (
                f"At 16kHz/900s, buffer maxlen should stay at the default "
                f"{DEFAULT_MAX_BUFFER_CHUNKS} (no resize needed: 900s / "
                f"{512 / 16000:.4f}s = {int(900 / (512 / 16000))} chunks < "
                f"{DEFAULT_MAX_BUFFER_CHUNKS}), got {r._audio_pipeline._buffer.maxlen}. "
                f"XV-20 fix must not change the 16kHz default codepath."
            )
        finally:
            r.stop()


# pre-roll buffer scales with effective_sr ──────────────


class TestPrerollSizing:
    """The pre-roll deque's maxlen must be sized against the device's
    effective sample rate AND the rate-scaled blocksize (duration
    contract), not a fixed-512 chunk count."""

    @pytest.mark.parametrize(
        "native_rate",
        [16000, 48000],
        ids=["16kHz-native", "48kHz-native"],
    )
    def test_preroll_capacity_holds_configured_duration(self, monkeypatch, native_rate):
        """At both 16kHz and 48kHz, the pre-roll deque must hold the
        CONFIGURED 1.0s of pre-speech audio as a DURATION — pinned
        via ``maxlen × scaled_blocksize / rate``, not via a chunk
        count.

        Historical bug: the deque was sized in ``__init__`` from
        ``config.sample_rate=16000``, so a 1.0s pre-roll only captured
        ~0.33s at 48kHz. Rate-scaling regression this test pins: with
        the stream delivering scaled ~32 ms chunks, a fixed-512 chunk
        computation (``1.0 × rate / 512`` chunks) makes the deque hold
        ~3.04s at 48kHz — ~3× over-capture.
        """
        import voice_typer.server.recording as recording_mod

        _patch_sd(monkeypatch, recording_mod, native_rate=native_rate)
        r = _make_recorder(max_rec=900, preroll_seconds=1.0)
        try:
            r.start()
            assert r._effective_sr == native_rate, f"expected _effective_sr={native_rate}, got {r._effective_sr}"

            blocksize = scaled_audio_blocksize(native_rate)
            actual_maxlen = r._preroll_buffer.maxlen or 0
            duration_s = actual_maxlen * blocksize / native_rate
            # DURATION contract: ≈ the configured 1.0s (+ the 2-chunk
            # sizing slack) at EVERY native rate.
            assert 0.95 <= duration_s <= 1.15, (
                f"At {native_rate}Hz with 1.0s pre-roll, deque holds "
                f"{actual_maxlen} chunks × {blocksize} samples = "
                f"{duration_s:.3f}s — must be ≈ 1.0s. A fixed-512 chunk "
                f"computation would capture {int(1.0 * native_rate / 512) * blocksize / native_rate:.2f}s "
                f"(over-capture) once the stream scales its blocks."
            )
            # The chunk count follows from the duration contract.
            assert actual_maxlen == int(1.0 * native_rate / blocksize) + 2
        finally:
            r.stop()

    def test_48khz_preroll_duration_matches_16khz(self, monkeypatch):
        """Sanity: with rate-scaled ~32 ms chunks, the pre-roll DURATION
        must be the same at 48kHz and 16kHz (and the chunk counts
        equal) for the same configured pre-roll seconds.
        """
        import voice_typer.server.recording as recording_mod

        durations: dict[int, float] = {}
        maxlens: dict[int, int] = {}
        for native_rate in (16000, 48000):
            _patch_sd(monkeypatch, recording_mod, native_rate=native_rate)
            r = _make_recorder(max_rec=900, preroll_seconds=1.0)
            try:
                r.start()
                maxlens[native_rate] = r._preroll_buffer.maxlen or 0
                durations[native_rate] = maxlens[native_rate] * scaled_audio_blocksize(native_rate) / native_rate
            finally:
                r.stop()

        assert maxlens[48000] == maxlens[16000], (
            f"rate-scaled ~32 ms chunks make the pre-roll chunk count "
            f"rate-invariant: 48kHz {maxlens[48000]} vs 16kHz {maxlens[16000]}."
        )
        assert abs(durations[48000] - durations[16000]) <= 0.05, (
            f"pre-roll DURATION must match across rates: "
            f"48kHz {durations[48000]:.3f}s vs 16kHz {durations[16000]:.3f}s."
        )
        assert 0.95 <= durations[48000] <= 1.15, (
            f"48kHz pre-roll must hold ≈ the configured 1.0s, got {durations[48000]:.3f}s."
        )

    def test_preroll_disabled_has_zero_maxlen(self, monkeypatch):
        """When ``pre_roll_buffer_seconds=0``, the pre-roll deque maxlen
        must be 0 (disabled) — preserves existing behavior."""
        import voice_typer.server.recording as recording_mod

        _patch_sd(monkeypatch, recording_mod, native_rate=48000)
        r = _make_recorder(max_rec=900, preroll_seconds=0.0)
        try:
            r.start()
            assert r._preroll_buffer.maxlen == 0, (
                f"pre-roll disabled but maxlen is {r._preroll_buffer.maxlen} (expected 0)"
            )
            assert not r._preroll_active, "pre-roll should be inactive when pre_roll_buffer_seconds=0"
        finally:
            r.stop()

    def test_16khz_preroll_maxlen_unchanged_by_start(self, monkeypatch):
        """At 16kHz (matching config.sample_rate), ``start()`` must NOT
        resize the pre-roll deque — the ``__init__`` placeholder sizing
        is already correct for the 16kHz case.

        This pins the 'preserves default 16kHz/512-sample behavior'
        requirement from the XV-21 fix brief.
        """
        import voice_typer.server.recording as recording_mod

        _patch_sd(monkeypatch, recording_mod, native_rate=16000)
        r = _make_recorder(max_rec=900, preroll_seconds=1.0)
        init_maxlen = r._preroll_buffer.maxlen
        try:
            r.start()
            assert r._preroll_buffer.maxlen == init_maxlen, (
                f"At 16kHz, start() should NOT resize the pre-roll deque "
                f"(init maxlen {init_maxlen} is already correct), but got "
                f"{r._preroll_buffer.maxlen}. XV-21 fix must not change "
                f"the 16kHz default codepath."
            )
        finally:
            r.stop()
