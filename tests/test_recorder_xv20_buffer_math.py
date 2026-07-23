"""XV-20 / XV-21 regression tests: recorder buffer math at 16kHz AND 48kHz.

XV-20 (CRITICAL): ``DEFAULT_MAX_BUFFER_CHUNKS = 30000`` was sized for a
stale 1024-sample/16kHz assumption (``chunk_seconds = 0.064``), but the
actual blocksize is 512 and the effective sample rate may be
44.1/48kHz (device native rate). At 48kHz the stale default only holds
``30000 × 512/48000 ≈ 5.3 min`` — a 30-min dictation silently loses the
first ~25 min via deque maxlen eviction.

XV-21 (High): the pre-roll deque was sized in ``__init__`` using
``config.sample_rate`` (16kHz) instead of the device's effective sample
rate. At 48kHz the same 1-second pre-roll needs 3× the chunk capacity,
so the deque would only capture ~0.33s of pre-roll instead of the
configured 1.0s.

Fix: compute ``chunk_seconds = blocksize / effective_sr`` in ``start()``
AFTER ``_resolve_effective_sample_rate`` returns; size the main buffer
to ``int(max_rec / chunk_seconds) + safety`` and re-size the pre-roll
deque using ``effective_sr``. The ``__init__`` placeholder sizing is
preserved as a default for the common 16kHz case (so existing 16kHz
behaviour is unchanged — see ``test_default_16khz_preserves_existing_behavior``).
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

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
    monkeypatch.setattr(
        recording_mod.sd, "query_hostapis", lambda idx=None: {"name": "MME"}
    )


def _make_recorder(max_rec: int = 1800, preroll_seconds: float = 1.0):
    """Build a ``Recorder`` with a ``MagicMock`` config suitable for
    buffer-math tests.

    ``sample_rate=16000`` is the Whisper target rate (NOT the device
    native rate — that's the whole point of XV-20). ``microphone=None``
    selects the system default device, so the candidate loop calls
    ``_resolve_effective_sample_rate(None)`` which queries
    ``sd.query_devices(kind="input")`` — the device our mock patches.
    """
    from voice_typer.server.recording import Recorder

    config = MagicMock(
        sample_rate=16000,  # Whisper target rate
        microphone=None,  # system default
        max_recording_time_seconds=max_rec,
        pre_roll_buffer_seconds=preroll_seconds,
        recording_channels=1,
    )
    return Recorder(config)


# ── XV-20: main recording buffer scales with effective_sr ────────


class TestXV20MainBufferSizing:
    """XV-20: the main recording buffer's maxlen must scale with the
    device's effective sample rate so a 30-min dictation at 48kHz
    doesn't silently lose the first ~25 min via deque eviction."""

    @pytest.mark.parametrize(
        "native_rate",
        [16000, 48000],
        ids=["16kHz-native", "48kHz-native"],
    )
    def test_buffer_capacity_holds_full_max_recording_time(self, monkeypatch, native_rate):
        """At both 16kHz and 48kHz, the buffer maxlen must be large
        enough to hold ``max_recording_time_seconds=1800`` (30 min) of
        audio at blocksize=512.

        Pre-fix (XV-20 bug): at 48kHz the buffer was sized against a
        stale 0.064s chunk-duration assumption, so 30000 default chunks
        only held ~5.3 min and a 30-min dictation lost the first ~25
        min. Post-fix: ``chunk_seconds = 512 / effective_sr`` and the
        deque is resized to ``int(1800 / chunk_seconds) + 1000``.
        """
        import voice_typer.server.recording as recording_mod

        _patch_sd(monkeypatch, recording_mod, native_rate=native_rate)
        r = _make_recorder(max_rec=1800, preroll_seconds=0.0)
        try:
            r.start()
            # The device's native rate must be the effective rate.
            assert r._effective_sr == native_rate, (
                f"expected _effective_sr={native_rate} (device native), "
                f"got {r._effective_sr}"
            )

            chunk_seconds = 512 / native_rate
            expected_min_chunks = int(1800 / chunk_seconds)  # no safety margin
            actual_maxlen = r._buffer.maxlen or 0
            assert actual_maxlen >= expected_min_chunks, (
                f"At {native_rate}Hz, buffer maxlen {actual_maxlen} must be "
                f">= {expected_min_chunks} chunks (1800s / "
                f"{chunk_seconds:.4f}s/chunk) to hold a 30-min dictation. "
                f"XV-20 regression: stale DEFAULT_MAX_BUFFER_CHUNKS=30000 "
                f"only holds {30000 * chunk_seconds:.1f}s at this rate "
                f"(would lose the first {1800 - 30000 * chunk_seconds:.1f}s)."
            )
        finally:
            r.stop()

    def test_48khz_buffer_capacity_is_3x_larger_than_16khz(self, monkeypatch):
        """Sanity: at 48kHz the buffer must hold ~3× more chunks than
        at 16kHz for the same recording duration, because
        ``chunk_seconds = 512/sr`` is 3× smaller at 48kHz.

        This is the core XV-20 invariant: the buffer capacity in
        *seconds* must be constant regardless of the device's native
        sample rate. If the 48kHz maxlen is NOT ~3× the 16kHz maxlen,
        the buffer was sized against the wrong rate.
        """
        import voice_typer.server.recording as recording_mod

        maxlens: dict[int, int] = {}
        for native_rate in (16000, 48000):
            _patch_sd(monkeypatch, recording_mod, native_rate=native_rate)
            r = _make_recorder(max_rec=1800, preroll_seconds=0.0)
            try:
                r.start()
                maxlens[native_rate] = r._buffer.maxlen or 0
            finally:
                r.stop()

        ratio = maxlens[48000] / maxlens[16000]
        # Expected ratio is exactly 3.0 (48000/16000); the +1K safety
        # margin is a constant offset that slightly dilutes the ratio
        # at small scales, but at 1800s/16kHz it's negligible.
        assert ratio >= 2.5, (
            f"48kHz buffer maxlen ({maxlens[48000]}) should be ~3× the "
            f"16kHz maxlen ({maxlens[16000]}); got ratio {ratio:.2f}. "
            f"XV-20 regression: buffer was sized against config.sample_rate "
            f"(16kHz) instead of effective_sr."
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
            assert r._buffer.maxlen == DEFAULT_MAX_BUFFER_CHUNKS, (
                f"At 16kHz/900s, buffer maxlen should stay at the default "
                f"{DEFAULT_MAX_BUFFER_CHUNKS} (no resize needed: 900s / "
                f"{512 / 16000:.4f}s = {int(900 / (512 / 16000))} chunks < "
                f"{DEFAULT_MAX_BUFFER_CHUNKS}), got {r._buffer.maxlen}. "
                f"XV-20 fix must not change the 16kHz default codepath."
            )
        finally:
            r.stop()


# ── XV-21: pre-roll buffer scales with effective_sr ──────────────


class TestXV21PrerollSizing:
    """XV-21: the pre-roll deque's maxlen must be sized against the
    device's effective sample rate, not ``config.sample_rate`` (16kHz)."""

    @pytest.mark.parametrize(
        "native_rate",
        [16000, 48000],
        ids=["16kHz-native", "48kHz-native"],
    )
    def test_preroll_capacity_holds_configured_duration(self, monkeypatch, native_rate):
        """At both 16kHz and 48kHz, the pre-roll deque maxlen must be
        large enough to hold 1 second of audio at blocksize=512.

        Pre-fix (XV-21 bug): at 48kHz the deque was sized in ``__init__``
        using ``config.sample_rate=16000``, so a 1.0s pre-roll only
        captured ~0.33s (the deque evicted 2/3 of the pre-roll).
        Post-fix: ``start()`` re-sizes the deque using ``effective_sr``.
        """
        import voice_typer.server.recording as recording_mod

        _patch_sd(monkeypatch, recording_mod, native_rate=native_rate)
        r = _make_recorder(max_rec=900, preroll_seconds=1.0)
        try:
            r.start()
            assert r._effective_sr == native_rate, (
                f"expected _effective_sr={native_rate}, got {r._effective_sr}"
            )

            expected_min_preroll_chunks = int(1.0 * native_rate / 512)
            actual_maxlen = r._preroll_buffer.maxlen or 0
            assert actual_maxlen >= expected_min_preroll_chunks, (
                f"At {native_rate}Hz with 1.0s pre-roll, deque maxlen "
                f"{actual_maxlen} must be >= {expected_min_preroll_chunks} "
                f"chunks (1.0s × {native_rate}/512). XV-21 regression: "
                f"deque was sized against config.sample_rate (16kHz) "
                f"instead of effective_sr — would only capture "
                f"{actual_maxlen * 512 / native_rate:.2f}s of pre-roll."
            )
        finally:
            r.stop()

    def test_48khz_preroll_capacity_is_3x_larger_than_16khz(self, monkeypatch):
        """Sanity: at 48kHz the pre-roll deque must hold ~3× more
        chunks than at 16kHz for the same pre-roll duration."""
        import voice_typer.server.recording as recording_mod

        maxlens: dict[int, int] = {}
        for native_rate in (16000, 48000):
            _patch_sd(monkeypatch, recording_mod, native_rate=native_rate)
            r = _make_recorder(max_rec=900, preroll_seconds=1.0)
            try:
                r.start()
                maxlens[native_rate] = r._preroll_buffer.maxlen or 0
            finally:
                r.stop()

        ratio = maxlens[48000] / maxlens[16000]
        assert ratio >= 2.5, (
            f"48kHz pre-roll maxlen ({maxlens[48000]}) should be ~3× the "
            f"16kHz maxlen ({maxlens[16000]}); got ratio {ratio:.2f}. "
            f"XV-21 regression: deque was sized against config.sample_rate "
            f"instead of effective_sr."
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
                f"pre-roll disabled but maxlen is "
                f"{r._preroll_buffer.maxlen} (expected 0)"
            )
            assert not r._preroll_active, (
                "pre-roll should be inactive when pre_roll_buffer_seconds=0"
            )
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
