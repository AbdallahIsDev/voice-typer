"""VAD preload is skipped when enhancements are off."""

from __future__ import annotations

import logging
from types import SimpleNamespace


def _off_config():
    return SimpleNamespace(
        noise_filter_highpass=False,
        noise_filter_gate=False,
        noise_filter_eq=False,
        noise_filter_compressor=False,
        noise_filter_limiter=False,
        noise_filter_notch=False,
        noise_suppression_method="none",
    )


def _on_config():
    cfg = _off_config()
    cfg.noise_filter_highpass = True
    return cfg


def _make_app(config):
    return SimpleNamespace(
        config=config,
        _thread_registry=None,
        _shutting_down=False,
    )


def test_phase1_skips_preload_when_vad_disabled(monkeypatch, caplog):
    from voice_typer.server import vad as _vad
    from voice_typer.server.startup_sequence import StartupSequence as _Seq

    calls = []
    monkeypatch.setattr(_vad, "preload", lambda: calls.append(1) or True)

    seq = _Seq.__new__(_Seq)
    seq._app = _make_app(_off_config())
    seq._t0 = 0.0
    import time

    monkeypatch.setattr(time, "perf_counter", lambda: 0.0)

    with caplog.at_level(logging.DEBUG, logger="voice_typer.server.startup_sequence"):
        result = seq._phase_1_init_and_vad_preload()
    assert result.success is True
    assert calls == []


def test_phase1_preloads_when_vad_enabled(monkeypatch, caplog):
    from voice_typer.server import vad as _vad
    from voice_typer.server.startup_sequence import StartupSequence as _Seq

    calls = []
    monkeypatch.setattr(_vad, "preload", lambda: calls.append(1) or True)

    seq = _Seq.__new__(_Seq)
    seq._app = _make_app(_on_config())
    seq._t0 = 0.0

    with caplog.at_level(logging.DEBUG, logger="voice_typer.server.startup_sequence"):
        result = seq._phase_1_init_and_vad_preload()
    assert result.success is True
    # The worker runs on a thread; join briefly by polling.
    import time as _time

    deadline = _time.monotonic() + 2.0
    while not calls and _time.monotonic() < deadline:
        _time.sleep(0.01)
    assert calls != []
