"""Tests for the WhisperModel thread budget (``cpu_threads``).

Pins two contracts:

1. :func:`voice_typer.server.transcription_device.whisper_cpu_threads`
   derives the CTranslate2 intra-op thread budget from the machine's
   cores, capped at ``_WHISPER_CPU_THREADS_CAP`` (psutil physical-core
   count first, affinity / logical fallbacks second, floor of 1).
2. ``TranscriptionEngine`` passes that budget (plus the explicit
   ``num_workers=1`` single-decoder contract) to every
   ``WhisperModel(...)`` construction — CTranslate2 silently defaults
   to 4 intra-op threads when the option is omitted, which under-uses
   multi-core machines on the CPU path.

All external dependencies (faster_whisper, psutil) are mocked — no
real model, no real core-count probing.
"""

from __future__ import annotations

import sys
import threading
from unittest.mock import MagicMock

import pytest

# ── whisper_cpu_threads unit tests ──────────────────────────────────────


class TestWhisperCpuThreads:
    def test_capped_at_ceiling(self, monkeypatch):
        """A 64-core machine must not hand every core to the decoder."""
        import voice_typer.server.transcription_device as td

        psutil_mock = MagicMock()
        psutil_mock.cpu_count.return_value = 64
        monkeypatch.setitem(sys.modules, "psutil", psutil_mock)

        assert td.whisper_cpu_threads() == td._WHISPER_CPU_THREADS_CAP == 8
        psutil_mock.cpu_count.assert_called_once_with(logical=False)

    def test_physical_cores_under_cap_used_directly(self, monkeypatch):
        import voice_typer.server.transcription_device as td

        psutil_mock = MagicMock()
        psutil_mock.cpu_count.return_value = 4
        monkeypatch.setitem(sys.modules, "psutil", psutil_mock)

        assert td.whisper_cpu_threads() == 4

    def test_falls_back_to_sched_getaffinity_when_physical_unknown(self, monkeypatch):
        """psutil reporting None → affinity-aware logical count."""
        import voice_typer.server.transcription_device as td

        psutil_mock = MagicMock()
        psutil_mock.cpu_count.return_value = None
        monkeypatch.setitem(sys.modules, "psutil", psutil_mock)
        monkeypatch.setattr(td.os, "sched_getaffinity", lambda pid: {0, 1, 2, 3, 4, 5}, raising=False)

        assert td.whisper_cpu_threads() == 6

    def test_falls_back_to_cpu_count_without_sched_getaffinity(self, monkeypatch):
        """Windows/macOS have no ``os.sched_getaffinity`` → ``os.cpu_count``."""
        import voice_typer.server.transcription_device as td

        psutil_mock = MagicMock()
        psutil_mock.cpu_count.return_value = None
        monkeypatch.setitem(sys.modules, "psutil", psutil_mock)

        def _no_affinity(pid):
            raise AttributeError("no sched_getaffinity on this platform")

        monkeypatch.setattr(td.os, "sched_getaffinity", _no_affinity, raising=False)
        monkeypatch.setattr(td.os, "cpu_count", lambda: 3)

        assert td.whisper_cpu_threads() == 3

    def test_falls_back_when_psutil_missing(self, monkeypatch):
        """psutil import failure (sys.modules entry None → ImportError)."""
        import voice_typer.server.transcription_device as td

        monkeypatch.setitem(sys.modules, "psutil", None)
        monkeypatch.setattr(td.os, "sched_getaffinity", lambda pid: {0, 1}, raising=False)

        assert td.whisper_cpu_threads() == 2

    def test_floor_of_one(self, monkeypatch):
        """Degenerate probing (everything None / empty affinity) → 1."""
        import voice_typer.server.transcription_device as td

        psutil_mock = MagicMock()
        psutil_mock.cpu_count.return_value = None
        monkeypatch.setitem(sys.modules, "psutil", psutil_mock)
        monkeypatch.setattr(td.os, "sched_getaffinity", lambda pid: set(), raising=False)

        assert td.whisper_cpu_threads() == 1


# ── WhisperModel constructor kwargs pin ─────────────────────────────────


def _make_engine_for_load():
    """Bare TranscriptionEngine with the state ``_load_transcriber_impl`` needs."""
    from voice_typer.server.transcription import TranscriptionEngine

    engine = TranscriptionEngine.__new__(TranscriptionEngine)
    engine._model = None
    engine._device = "cpu"
    engine._compute_type = "int8"
    engine._loaded_model_size = None
    engine._configured_model_size = "tiny"
    engine.model_size = "tiny"
    engine._lock = threading.RLock()
    engine._whisper_size_cached = MagicMock(return_value=True)
    engine._probe_cuda_runtime = MagicMock()
    engine._warm_up_model = MagicMock()
    return engine


class TestWhisperModelConstructorKwargs:
    @pytest.fixture(autouse=True)
    def _mock_faster_whisper(self, monkeypatch):
        self.fw_module = MagicMock(name="faster_whisper")
        monkeypatch.setitem(sys.modules, "faster_whisper", self.fw_module)
        monkeypatch.setattr(
            "voice_typer.server.transcription._configure_nvidia_dll_paths",
            lambda: None,
        )

    def test_constructor_receives_cpu_threads_and_single_worker(self, monkeypatch):
        """The ctor kwargs pin: budget from the helper + num_workers=1.

        ``num_workers`` is faster-whisper's knob for CTranslate2's
        ``inter_threads`` — pinned to 1 so the inter-op contract is
        explicit. ``cpu_threads`` is the intra-op budget CTranslate2
        would otherwise default to 4.
        """
        import voice_typer.server.transcription as transcription

        monkeypatch.setattr(transcription, "_whisper_cpu_threads_impl", lambda: 6)

        engine = _make_engine_for_load()
        engine._load_transcriber_impl([("cpu", "int8", "tiny")], acquire_lock=False)

        self.fw_module.WhisperModel.assert_called_once_with(
            "tiny",
            device="cpu",
            compute_type="int8",
            cpu_threads=6,
            num_workers=1,
        )
        assert engine._model is self.fw_module.WhisperModel.return_value

    def test_budget_applies_to_cpu_fallback_entries_too(self, monkeypatch):
        """Every chain entry (incl. GPU→CPU fallback) gets the budget.

        The loop stops at the first successful construction, so the
        first entry is made to fail to exercise the CPU fallback.
        """
        import voice_typer.server.transcription as transcription

        monkeypatch.setattr(transcription, "_whisper_cpu_threads_impl", lambda: 8)
        self.fw_module.WhisperModel.side_effect = [
            RuntimeError("simulated GPU load failure"),
            MagicMock(name="cpu_model"),
        ]

        engine = _make_engine_for_load()
        engine._load_transcriber_impl(
            [("cuda", "float16", "tiny"), ("cpu", "int8", "tiny")],
            acquire_lock=False,
        )

        assert self.fw_module.WhisperModel.call_count == 2
        for call in self.fw_module.WhisperModel.call_args_list:
            assert call.kwargs["cpu_threads"] == 8
            assert call.kwargs["num_workers"] == 1


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
