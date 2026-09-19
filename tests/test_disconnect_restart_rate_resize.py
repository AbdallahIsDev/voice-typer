"""Rate-change buffer re-sizing on the hot-swap restart path."""

from __future__ import annotations

import collections
from unittest.mock import MagicMock

from voice_typer.server._audio_constants import scaled_audio_blocksize
from voice_typer.server.recording.disconnect_handler import DisconnectHandler
from voice_typer.server.recording.recorder import Recorder


def _make_recorder() -> Recorder:
    """Build a real ``Recorder`` with a MagicMock config (no audio device)."""
    from tests.fixtures.ipc_test_helpers import make_fake_recorder

    return make_fake_recorder()


def _setup_recorder_for_restart(monkeypatch, r: Recorder, candidate_sr: int) -> None:
    """``test_hot_swap_secure_clear`` helper; the candidate sample rate is"""
    monkeypatch.setattr(r, "_current_callback", lambda *a, **k: None, raising=False)
    monkeypatch.setattr(r._devices, "_resolve_device", lambda: None)
    monkeypatch.setattr(r._devices, "_resolve_effective_sample_rate", lambda _d: (candidate_sr, None))

    import voice_typer.server.recording.disconnect_handler as dh_mod

    monkeypatch.setattr(dh_mod, "refresh_vad_caches", lambda rec: None)
    monkeypatch.setattr(
        dh_mod.sd,
        "query_devices",
        lambda *a, **k: {"max_input_channels": 1, "name": "fake"},
    )
    fake_stream = MagicMock()
    monkeypatch.setattr(dh_mod.sd, "InputStream", lambda **kw: fake_stream)


def _spy_resize(r: Recorder, monkeypatch) -> MagicMock:
    """Replace the recorder's resize entry point with a call-recording spy."""
    spy = MagicMock(wraps=r._session_state.resize_buffers_for_sample_rate)
    monkeypatch.setattr(r._session_state, "resize_buffers_for_sample_rate", spy)
    return spy


class TestRestartStreamRateChangeResize:
    """A mid-session rate switch must re-run the dynamic buffer sizing."""

    def test_rate_change_invokes_real_resize(self, monkeypatch):
        """16 kHz → 48 kHz restart: the real ``resize_buffers_for_sample_rate``"""
        r = _make_recorder()
        _setup_recorder_for_restart(monkeypatch, r, candidate_sr=48000)
        # Simulate a session that started at 16 kHz (the pre-switch rate).
        r._effective_sr = 16000
        # The start()-cached scalar ``cache_session_config`` sets.
        r._cached_max_recording_time = 900

        spy = _spy_resize(r, monkeypatch)
        DisconnectHandler(r).restart_stream(_captured_generation=0)

        assert spy.called, (
            "restart_stream must re-run SessionState.resize_buffers_for_sample_rate "
            "when the restart lands on a different native rate, otherwise the ring "
            "buffer / pre-roll / main-buffer caps keep the old rate's sizing for the "
            "rest of the session"
        )
        args, _ = spy.call_args
        # Positional contract: (recorder, effective_sr, max_rec).
        assert args[0] is r
        assert args[1] == 48000, "resize must be invoked with the NEW effective rate"
        assert args[2] == 900, "resize must be invoked with the cached max-recording time"

    def test_rate_change_resizes_preroll_for_new_rate(self, monkeypatch):
        """8 kHz → 48 kHz restart: the pre-roll deque is re-sized to the NEW"""
        r = _make_recorder()
        _setup_recorder_for_restart(monkeypatch, r, candidate_sr=48000)
        # Session started on an 8 kHz BT-HFP device with a 1.0 s pre-roll.
        r._effective_sr = 8000
        r._preroll_seconds = 1.0
        r._preroll_active = True
        r._preroll_buffer = collections.deque(maxlen=int(1.0 * 8000 / scaled_audio_blocksize(8000)) + 2)
        assert r._preroll_buffer.maxlen == 17

        DisconnectHandler(r).restart_stream(_captured_generation=0)

        expected = int(1.0 * 48000 / scaled_audio_blocksize(48000)) + 2
        assert expected == 33
        assert r._preroll_buffer.maxlen == expected, (
            "the pre-roll deque must be re-sized for the restart's effective "
            "rate, not stay at the old rate's chunk count"
        )

    def test_same_rate_restart_skips_resize(self, monkeypatch):
        """A restart on the SAME native rate (the common BT-flap recovery)"""
        r = _make_recorder()
        _setup_recorder_for_restart(monkeypatch, r, candidate_sr=48000)
        r._effective_sr = 48000
        r._cached_max_recording_time = 900

        spy = _spy_resize(r, monkeypatch)
        DisconnectHandler(r).restart_stream(_captured_generation=0)

        assert not spy.called, (
            "restart_stream must skip the resize when the effective rate is "
            "unchanged (idempotent restart, same-rate BT flaps are the "
            "common case)"
        )

    def test_rate_change_resize_tolerates_missing_cached_max_rec(self, monkeypatch):
        """A recorder whose start()-cached ``_cached_max_recording_time`` is"""
        r = _make_recorder()
        _setup_recorder_for_restart(monkeypatch, r, candidate_sr=48000)
        r._effective_sr = 16000
        # Not yet cached (restart before any start(), defensive path).
        monkeypatch.delattr(r, "_cached_max_recording_time", raising=False)

        spy = _spy_resize(r, monkeypatch)
        # Must not raise (AttributeError on the missing cached scalar).
        DisconnectHandler(r).restart_stream(_captured_generation=0)

        assert spy.called
        args, _ = spy.call_args
        assert args[2] == 0
