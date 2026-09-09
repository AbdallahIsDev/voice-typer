"""Rate-change buffer re-sizing on the hot-swap restart path.

The disconnect-restart path (:meth:`DisconnectHandler.restart_stream`)
updates ``recorder._effective_sr`` to the new device's native rate but
previously never re-ran the dynamic buffer sizing — so after a
mid-session device switch (e.g. a Bluetooth headset at 16 kHz ↔ a
built-in mic at 48 kHz) the ring buffer / pre-roll deque / main-buffer
caps kept the OLD rate's sizing for the rest of the session.

These tests pin the fixed contract:

1. A restart that lands on a DIFFERENT native rate invokes the real
   resize entry point (``SessionState.resize_buffers_for_sample_rate``)
   with the new rate and the session's cached max-recording time.
2. The pre-roll deque is actually re-sized to the new rate's scaled
   blocksize math (observable with an 8 kHz → 48 kHz switch, where the
   512-sample floor makes the chunk counts differ).
3. A restart on the SAME rate (the common BT-flap case) does NOT
   re-invoke the resize (idempotent — no redundant deque churn / log
   noise on every flap recovery).

The test scaffolding mirrors ``tests/test_hot_swap_secure_clear.py``
(canonical ``make_fake_recorder`` factory + the restart-path stubs).
"""

from __future__ import annotations

import collections
from unittest.mock import MagicMock

from voice_typer.server._audio_constants import scaled_audio_blocksize
from voice_typer.server.recording.disconnect_handler import DisconnectHandler
from voice_typer.server.recording.recorder import Recorder

# ─── Helpers (mirror tests/test_hot_swap_secure_clear.py) ──────────────


def _make_recorder() -> Recorder:
    """Build a real ``Recorder`` with a MagicMock config (no audio device)."""
    from tests.fixtures.ipc_test_helpers import make_fake_recorder

    return make_fake_recorder()


def _setup_recorder_for_restart(monkeypatch, r: Recorder, candidate_sr: int) -> None:
    """Stub external dependencies so ``restart_stream`` runs without a
    real audio device or a started stream (mirrors the
    ``test_hot_swap_secure_clear`` helper; the candidate sample rate is
    parameterized for the rate-switch scenarios)."""
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
    """Replace the recorder's resize entry point with a call-recording spy.

    The production restart path resolves the method through
    ``recorder._session_state.resize_buffers_for_sample_rate`` at call
    time, so patching the instance attribute is the correct injection
    point (no package-level indirection — C-ARCH-2).
    """
    spy = MagicMock(wraps=r._session_state.resize_buffers_for_sample_rate)
    monkeypatch.setattr(r._session_state, "resize_buffers_for_sample_rate", spy)
    return spy


# ─── Rate-change resize on restart_stream ──────────────────────────────


class TestRestartStreamRateChangeResize:
    """A mid-session rate switch must re-run the dynamic buffer sizing."""

    def test_rate_change_invokes_real_resize(self, monkeypatch):
        """16 kHz → 48 kHz restart: the real ``resize_buffers_for_sample_rate``
        must fire with the new rate + the cached max-recording time."""
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
            "when the restart lands on a different native rate — otherwise the ring "
            "buffer / pre-roll / main-buffer caps keep the old rate's sizing for the "
            "rest of the session"
        )
        args, _ = spy.call_args
        # Positional contract: (recorder, effective_sr, max_rec).
        assert args[0] is r
        assert args[1] == 48000, "resize must be invoked with the NEW effective rate"
        assert args[2] == 900, "resize must be invoked with the cached max-recording time"

    def test_rate_change_resizes_preroll_for_new_rate(self, monkeypatch):
        """8 kHz → 48 kHz restart: the pre-roll deque is re-sized to the NEW
        rate's scaled-blocksize chunk math.

        8 kHz blocks are 512 samples (the floor) → int(1.0 * 8000 / 512) + 2
        = 17 chunks; 48 kHz blocks are 1536 samples → int(1.0 * 48000 / 1536)
        + 2 = 33 chunks. The rate-scaled blocksize makes most rate pairs
        chunk-count-invariant (~31.25 chunks/s), so the 512-floor 8 kHz case
        is the observable end of the contract.
        """
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
        """A restart on the SAME native rate (the common BT-flap recovery)
        must NOT re-invoke the resize — the buffers are already correctly
        sized and a redundant resize would churn the deques + log noise on
        every flap cycle."""
        r = _make_recorder()
        _setup_recorder_for_restart(monkeypatch, r, candidate_sr=48000)
        r._effective_sr = 48000
        r._cached_max_recording_time = 900

        spy = _spy_resize(r, monkeypatch)
        DisconnectHandler(r).restart_stream(_captured_generation=0)

        assert not spy.called, (
            "restart_stream must skip the resize when the effective rate is "
            "unchanged (idempotent restart — same-rate BT flaps are the "
            "common case)"
        )

    def test_rate_change_resize_tolerates_missing_cached_max_rec(self, monkeypatch):
        """A recorder whose start()-cached ``_cached_max_recording_time`` is
        absent or non-numeric falls back to max_rec=0 (the resize's
        ``max_rec > 0`` guard then skips only the main-buffer portion —
        the ring / pre-roll sizing still runs)."""
        r = _make_recorder()
        _setup_recorder_for_restart(monkeypatch, r, candidate_sr=48000)
        r._effective_sr = 16000
        # Not yet cached (restart before any start() — defensive path).
        monkeypatch.delattr(r, "_cached_max_recording_time", raising=False)

        spy = _spy_resize(r, monkeypatch)
        # Must not raise (AttributeError on the missing cached scalar).
        DisconnectHandler(r).restart_stream(_captured_generation=0)

        assert spy.called
        args, _ = spy.call_args
        assert args[2] == 0
