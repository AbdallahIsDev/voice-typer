"""Tests for the merged Silero VAD preload log lines.

``vad.preload()`` emits ONE INFO line covering both the model load and
the warm-up (they are one event):

- success → ``[VAD] Silero VAD model loaded from local ONNX, preloaded
  + warmed <duration>``
- warm-up failure → ``[VAD] Silero VAD model loaded from local ONNX,
  not warmed``
- repeat call → no second INFO (guarded by ``_preload_warmed_logged``).

The old behavior logged "loaded from local ONNX" AND "preloaded +
warmed" as two separate INFO lines for the same startup event.
"""

from __future__ import annotations

import logging

import pytest
from voice_typer.server import vad


@pytest.fixture(autouse=True)
def _reset_one_shot_guard(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(vad, "_preload_warmed_logged", False)


@pytest.fixture()
def _fake_model(monkeypatch: pytest.MonkeyPatch):
    session = object()
    monkeypatch.setattr(vad, "_load_model", lambda: (session, ("i", "s", None, "o", "so")))
    monkeypatch.setattr(vad, "_run_one_inference", lambda chunk, sr: None)
    monkeypatch.setattr(vad, "reset_states", lambda: None)
    return session


def _info_lines(caplog) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.levelno == logging.INFO and r.name == vad.log.name]


def test_preload_success_logs_single_merged_line(caplog, _fake_model):
    with caplog.at_level(logging.DEBUG, logger=vad.log.name):
        assert vad.preload() is True
    lines = [ln for ln in _info_lines(caplog) if "VAD" in ln]
    assert len(lines) == 1
    assert "loaded from local ONNX, preloaded + warmed" in lines[0]
    # C-LOG-2: duration suffix at line end.
    assert lines[0].rstrip().endswith("s")


def test_warmup_failure_logs_not_warmed(caplog, _fake_model, monkeypatch):
    def _boom(chunk, sr):
        raise RuntimeError("ort exploded")

    monkeypatch.setattr(vad, "_run_one_inference", _boom)
    with caplog.at_level(logging.DEBUG, logger=vad.log.name):
        assert vad.preload() is False
    lines = [ln for ln in _info_lines(caplog) if "VAD" in ln]
    assert len(lines) == 1
    assert "not warmed" in lines[0]


def test_repeat_call_does_not_duplicate_info(caplog, _fake_model):
    with caplog.at_level(logging.DEBUG, logger=vad.log.name):
        assert vad.preload() is True
        assert vad.preload() is True
    lines = [ln for ln in _info_lines(caplog) if "VAD" in ln and "preloaded" in ln]
    assert len(lines) == 1


def test_lazy_load_path_no_longer_logs_info(caplog, _fake_model):
    """``_load_model()`` demoted to DEBUG — the merged preload line is
    the single INFO marker, so a plain load must not emit one."""
    with caplog.at_level(logging.DEBUG, logger=vad.log.name):
        session, _names = vad._load_model()
    assert session is not None
    info = [r for r in caplog.records if r.levelno == logging.INFO]
    assert info == []
