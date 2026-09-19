"""``load_with_fallback`` / whisper-fallback load timeout tests."""

from __future__ import annotations

import logging
import time
from unittest.mock import MagicMock

from voice_typer.server.asr.registry import MODEL_LOAD_TIMEOUT_SECONDS
from voice_typer.server.asr_registry import AsrBackendRegistry


class _Config:
    """Minimal config stub for the registry tests."""

    def __init__(self, asr_backend: str = "parakeet") -> None:
        self.asr_backend = asr_backend
        self.model_size = asr_backend
        self.device = "cpu"
        self.language = "en"
        self.beam_size = 1
        self.best_of = 1
        self.condition_on_previous_text = False


def _make_registry_with_primary_and_whisper(
    *,
    primary_name: str = "parakeet",
) -> tuple[AsrBackendRegistry, MagicMock, MagicMock]:
    """Registry with a slow-loading primary backend and a fast whisper"""
    registry = AsrBackendRegistry(_Config(primary_name))
    primary = MagicMock()
    primary.is_loaded = False
    whisper = MagicMock()
    whisper.is_loaded = False
    registry.register(primary_name, primary)
    registry.register("whisper", whisper)
    return registry, primary, whisper


class TestLoadWithTimeout:
    """``load_with_fallback`` must bound the primary load with a hard"""

    def test_constant_is_sane(self):
        """The ceiling must exceed the documented 5-50s load window."""
        assert MODEL_LOAD_TIMEOUT_SECONDS > 50, (
            "MODEL_LOAD_TIMEOUT_SECONDS must exceed the documented 5-50s "
            "load window so a legitimate slow load is not cut short."
        )

    def test_slow_primary_load_falls_to_whisper_and_logs_warning(self, monkeypatch, caplog):
        """A primary load that never completes returns the TIMEOUT path:"""
        registry, primary, whisper = _make_registry_with_primary_and_whisper()
        # Load hangs forever; use a tiny timeout so the test is fast.
        monkeypatch.setattr("voice_typer.server.asr.registry.MODEL_LOAD_TIMEOUT_SECONDS", 0.05)
        primary.load.side_effect = lambda **kw: time.sleep(60)

        with caplog.at_level(logging.WARNING):
            result = registry.load_with_fallback(progress_callback=lambda msg: None)

        assert result is whisper, "TIMEOUT must fall through to the whisper fallback, not return None and not raise."
        whisper.load.assert_called_once()
        # Best-effort unload of the abandoned primary.
        primary.unload.assert_called_once()
        assert registry.failure_count("parakeet") == 0, (
            "A timeout must NOT trip the circuit breaker, it is a "
            "transient stall, not a permanent failure (a retry may "
            "succeed)."
        )
        assert any("timed out" in r.getMessage() and r.levelno == logging.WARNING for r in caplog.records), (
            "A WARNING naming the timeout must be logged so the stall is observable in the log."
        )

    def test_fast_primary_load_returns_normally(self, monkeypatch):
        """A fast primary load returns the primary backend directly —"""
        registry, primary, whisper = _make_registry_with_primary_and_whisper()
        monkeypatch.setattr("voice_typer.server.asr.registry.MODEL_LOAD_TIMEOUT_SECONDS", 60)
        primary.load.return_value = None

        result = registry.load_with_fallback(progress_callback=lambda msg: None)

        assert result is primary, "A fast load must return the primary backend."
        whisper.load.assert_not_called()
        primary.unload.assert_not_called()
        assert registry.failure_count("parakeet") == 0

    def test_primary_load_raises_trips_breaker_and_falls_to_whisper(self, monkeypatch):
        """A genuine (non-timeout) primary load exception must STILL trip"""
        registry, primary, whisper = _make_registry_with_primary_and_whisper()
        monkeypatch.setattr("voice_typer.server.asr.registry.MODEL_LOAD_TIMEOUT_SECONDS", 60)
        primary.load.side_effect = RuntimeError("CUDA OOM")

        result = registry.load_with_fallback(progress_callback=lambda msg: None)

        assert result is whisper
        assert registry.failure_count("parakeet") == 1, (
            "A real load exception must still be recorded as a failure (only a TIMEOUT is exempt)."
        )
        primary.unload.assert_called_once()


class TestWhisperFallbackTimeout:
    """The whisper fallback load must have the same timeout behaviour."""

    def test_slow_whisper_fallback_load_returns_none_with_warning(self, monkeypatch, caplog):
        """
        When the primary fails genuinely and the whisper fallback load
        WARNING naming the timeout, it must not hang.
        """
        registry, primary, whisper = _make_registry_with_primary_and_whisper()
        monkeypatch.setattr("voice_typer.server.asr.registry.MODEL_LOAD_TIMEOUT_SECONDS", 0.05)
        primary.load.side_effect = RuntimeError("CUDA OOM")
        whisper.load.side_effect = lambda **kw: time.sleep(60)

        with caplog.at_level(logging.WARNING):
            result = registry.load_with_fallback(progress_callback=lambda msg: None)

        assert result is None, (
            "A timed-out whisper fallback must return None (no backend loaded) rather than hang or raise."
        )
        whisper.unload.assert_called_once()
        assert any(
            "whisper fallback load timed out" in r.getMessage() and r.levelno == logging.WARNING for r in caplog.records
        ), "A WARNING naming the whisper fallback timeout must be logged."

    def test_fast_whisper_fallback_load_returns_whisper(self, monkeypatch):
        """A fast whisper fallback load returns whisper, no behavior"""
        registry, primary, whisper = _make_registry_with_primary_and_whisper()
        monkeypatch.setattr("voice_typer.server.asr.registry.MODEL_LOAD_TIMEOUT_SECONDS", 60)
        primary.load.side_effect = RuntimeError("CUDA OOM")
        whisper.load.return_value = None

        result = registry.load_with_fallback(progress_callback=lambda msg: None)

        assert result is whisper
        whisper.unload.assert_not_called()
