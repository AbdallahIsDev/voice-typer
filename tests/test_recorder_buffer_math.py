"""Recorder buffer math at 16kHz AND 48kHz (rate-scaled blocks)."""

from __future__ import annotations

import pytest
from voice_typer.server._audio_constants import scaled_audio_blocksize

REPO_ROOT_CONTEXT = "voice_typer.server.recording"  # noqa: N816 (readability)


class _OkStream:
    """No-op ``InputStream`` mock that records the requested samplerate."""

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
    """Patch ``sounddevice`` with a no-op ``InputStream`` + device query"""
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
    """buffer-math tests."""
    from tests.fixtures.ipc_test_helpers import make_fake_recorder

    return make_fake_recorder(
        max_recording_time_seconds=max_rec,
        pre_roll_buffer_seconds=preroll_seconds,
        recording_channels=1,
    )


class TestMainBufferSizing:
    """The main recording buffer's maxlen must scale with the device's"""

    @pytest.mark.parametrize(
        "native_rate",
        [16000, 48000],
        ids=["16kHz-native", "48kHz-native"],
    )
    def test_buffer_capacity_holds_full_max_recording_time(self, monkeypatch, native_rate):
        """At both 16kHz and 48kHz, the buffer maxlen must be large"""
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
        """Core rate-scaling invariant: with ~32 ms chunks at every"""
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
        """
        At 16kHz with the default ``max_recording_time_seconds=900``,
        This pins the 'preserves default 16kHz/512-sample behavior'
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


class TestPrerollSizing:
    """The pre-roll deque's maxlen must be sized against the device's"""

    @pytest.mark.parametrize(
        "native_rate",
        [16000, 48000],
        ids=["16kHz-native", "48kHz-native"],
    )
    def test_preroll_capacity_holds_configured_duration(self, monkeypatch, native_rate):
        """CONFIGURED 1.0s of pre-speech audio as a DURATION, pinned"""
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
            assert 0.95 <= duration_s <= 1.15, (
                f"At {native_rate}Hz with 1.0s pre-roll, deque holds "
                f"{actual_maxlen} chunks × {blocksize} samples = "
                f"{duration_s:.3f}s, must be ≈ 1.0s. A fixed-512 chunk "
                f"computation would capture {int(1.0 * native_rate / 512) * blocksize / native_rate:.2f}s "
                f"(over-capture) once the stream scales its blocks."
            )
            # The chunk count follows from the duration contract.
            assert actual_maxlen == int(1.0 * native_rate / blocksize) + 2
        finally:
            r.stop()

    def test_48khz_preroll_duration_matches_16khz(self, monkeypatch):
        """Sanity: with rate-scaled ~32 ms chunks, the pre-roll DURATION"""
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
        """When ``pre_roll_buffer_seconds=0``, the pre-roll deque maxlen"""
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
        """
        At 16kHz (matching config.sample_rate), ``start()`` must NOT
        This pins the 'preserves default 16kHz/512-sample behavior'
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
