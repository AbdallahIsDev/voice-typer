"""Audio-quality chunk delegation-loss warning gate — unit tests.

The audio pipeline delivers quality chunks at ~94 Hz. When the
``audio_quality`` controller is unavailable (lazy-init failed), the
delegation path in ``VoiceTyperApp._on_audio_quality_chunk`` must warn
exactly ONCE per delegate-loss episode instead of once per chunk
(~94 WARNING lines per second of recording). The latch resets on the
first successful delegation so a fresh loss episode warns again.
"""

from __future__ import annotations

import logging
import time
from unittest.mock import MagicMock

import pytest
from voice_typer.server.app import (
    _LAZY_FAILED,
    RETRY_TTL_SECONDS,
    VoiceTyperApp,
)


def _make_app() -> VoiceTyperApp:
    """Build a ``VoiceTyperApp`` via ``__new__`` with only the lazy-init
    backing fields the ``audio_quality`` property reads."""
    app = VoiceTyperApp.__new__(VoiceTyperApp)
    app._audio_quality_backing = None
    app._audio_quality_failed_at = None
    return app


def _make_delegateless_app() -> VoiceTyperApp:
    """Build an app whose ``audio_quality`` property returns None for the
    whole test (fresh lazy-init failure inside the retry TTL — the
    property returns None without re-attempting construction)."""
    app = _make_app()
    app._audio_quality_backing = _LAZY_FAILED
    app._audio_quality_failed_at = time.monotonic()
    assert RETRY_TTL_SECONDS > 0
    return app


class TestAudioQualityChunkWarningGate:
    def test_many_delegateless_chunks_warn_exactly_once(self, caplog):
        app = _make_delegateless_app()
        with caplog.at_level(logging.DEBUG, logger="voice_typer.server.app"):
            for _ in range(1000):
                assert app._on_audio_quality_chunk(0.5, 0.9) is None

        warnings = [r for r in caplog.records if r.levelno == logging.WARNING and "audio_quality" in r.getMessage()]
        assert len(warnings) == 1, (
            "delegate-less chunks arrive at ~94 Hz; the warning must fire "
            "exactly once per delegate-loss episode, not once per chunk"
        )
        # Subsequent chunks stay audible at debug level (or silent), but
        # never as warnings.
        debugs = [r for r in caplog.records if r.levelno == logging.DEBUG]
        assert len(debugs) >= 1

    def test_warning_refires_after_delegate_recovery(self, caplog):
        app = _make_delegateless_app()

        # Episode 1: one warning across many chunks.
        with caplog.at_level(logging.DEBUG, logger="voice_typer.server.app"):
            for _ in range(50):
                app._on_audio_quality_chunk(0.1, 0.2)
        assert len([r for r in caplog.records if r.levelno == logging.WARNING]) == 1

        # Delegate recovers: successful delegation resets the latch.
        delegate = MagicMock()
        app.audio_quality = delegate
        with caplog.at_level(logging.DEBUG, logger="voice_typer.server.app"):
            app._on_audio_quality_chunk(0.3, 0.4)
        delegate._on_audio_quality_chunk.assert_called_once_with(0.3, 0.4)
        assert len([r for r in caplog.records if r.levelno == logging.WARNING]) == 1

        # Episode 2: delegate lost again → a FRESH warning fires.
        app.audio_quality = None
        app._audio_quality_backing = _LAZY_FAILED
        app._audio_quality_failed_at = time.monotonic()
        with caplog.at_level(logging.DEBUG, logger="voice_typer.server.app"):
            for _ in range(50):
                app._on_audio_quality_chunk(0.5, 0.6)
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warnings) == 2, "a new delegate-loss episode must warn again exactly once"

    def test_successful_delegation_passes_through(self):
        app = _make_app()
        delegate = MagicMock()
        app.audio_quality = delegate

        result = app._on_audio_quality_chunk(1.5, 2.5)

        assert result is delegate._on_audio_quality_chunk.return_value
        delegate._on_audio_quality_chunk.assert_called_once_with(1.5, 2.5)


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__])
