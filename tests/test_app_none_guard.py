"""None-guard regression tests for lazy-init delegate methods."""

import logging
from unittest.mock import MagicMock

import pytest


@pytest.fixture
def app(tmp_config_dir, monkeypatch):
    """Create a LausuApp with mocked dependencies for None-guard tests."""
    monkeypatch.setattr("voice_typer.server.server_platform.autostart.is_autostart_enabled", lambda: False)
    monkeypatch.setattr("voice_typer.server.server_platform.autostart.enable_autostart", lambda: True)
    monkeypatch.setattr("voice_typer.server.server_platform.autostart.disable_autostart", lambda: True)
    monkeypatch.setattr("voice_typer.server.server_platform.microphone_list.list_microphones", lambda: [])

    from voice_typer.server.app import LausuApp

    instance = LausuApp()
    instance.config.esc_cancel_enabled = False
    instance.config.voice_biometric_consent = True
    return instance


def _force_undo_lazy_init_failure(monkeypatch):
    """Monkeypatch ``UndoRepasteController.__init__`` to raise."""

    def _boom(self, *args, **kwargs):
        raise RuntimeError("simulated UndoRepasteController lazy-init failure")

    monkeypatch.setattr("voice_typer.server.app_undo.UndoRepasteController.__init__", _boom)


def _force_audio_quality_lazy_init_failure(monkeypatch):
    """Monkeypatch ``AudioQualityController.__init__`` to raise."""

    def _boom(self, *args, **kwargs):
        raise RuntimeError("simulated AudioQualityController lazy-init failure")

    monkeypatch.setattr(
        "voice_typer.server.audio_quality_controller.AudioQualityController.__init__",
        _boom,
    )


class TestUndoNoneGuard:
    """``app.undo_last()`` / ``app.repaste_last()`` must not crash when"""

    def test_undo_last_returns_none_when_lazy_init_fails(self, app, monkeypatch, caplog):
        """When ``UndoRepasteController(self)`` raises, ``app.undo_last()``"""
        _force_undo_lazy_init_failure(monkeypatch)

        assert app.undo is None

        with caplog.at_level(logging.WARNING, logger="voice_typer.server.app"):
            result = app.undo_last()

        assert result is None
        assert any("undo controller unavailable" in rec.getMessage() for rec in caplog.records), (
            "undo_last() must log a warning when the controller is unavailable"
        )

    def test_repaste_last_returns_none_when_lazy_init_fails(self, app, monkeypatch, caplog):
        """When ``UndoRepasteController(self)`` raises,"""
        _force_undo_lazy_init_failure(monkeypatch)
        assert app.undo is None

        with caplog.at_level(logging.WARNING, logger="voice_typer.server.app"):
            result = app.repaste_last()

        assert result is None
        assert any("undo controller unavailable" in rec.getMessage() for rec in caplog.records), (
            "repaste_last() must log a warning when the controller is unavailable"
        )

    def test_undo_last_does_not_raise_attribute_error(self, app, monkeypatch):
        """``AttributeError: 'NoneType' object has no attribute 'undo_last'``"""
        _force_undo_lazy_init_failure(monkeypatch)

        try:
            app.undo_last()
        except AttributeError as exc:
            pytest.fail(f"undo_last() must not raise AttributeError when the controller is unavailable; got: {exc!r}")
        except Exception as exc:  # pragma: no cover - defensive
            # AttributeError is the documented regression and MUST NOT
            pytest.fail(
                f"undo_last() raised an unexpected exception type when "
                f"the controller was unavailable; expected None return, "
                f"got {type(exc).__name__}: {exc!r}"
            )


class TestAudioQualityNoneGuard:
    """
    ``app._on_audio_quality_chunk()`` / ``_rebuild_audio_processor()`` /
    ``_finalize_audio_quality_report()`` must not crash when
    """

    def test_on_audio_quality_chunk_returns_none_when_lazy_init_fails(self, app, monkeypatch, caplog):
        """When ``AudioQualityController(self)`` raises,"""
        _force_audio_quality_lazy_init_failure(monkeypatch)
        assert app.audio_quality is None

        with caplog.at_level(logging.WARNING, logger="voice_typer.server.app"):
            result = app._on_audio_quality_chunk(0.01, 0.05)

        assert result is None
        assert any("audio_quality controller unavailable" in rec.getMessage() for rec in caplog.records), (
            "_on_audio_quality_chunk() must log a warning when the controller is unavailable"
        )

    def test_rebuild_audio_processor_returns_none_when_lazy_init_fails(self, app, monkeypatch, caplog):
        """When ``AudioQualityController(self)`` raises,"""
        _force_audio_quality_lazy_init_failure(monkeypatch)
        assert app.audio_quality is None

        with caplog.at_level(logging.WARNING, logger="voice_typer.server.app"):
            result = app._rebuild_audio_processor(force_sr=16000)

        assert result is None
        assert any("audio_quality controller unavailable" in rec.getMessage() for rec in caplog.records)

    def test_finalize_audio_quality_report_returns_none_when_lazy_init_fails(self, app, monkeypatch, caplog):
        """When ``AudioQualityController(self)`` raises,"""
        _force_audio_quality_lazy_init_failure(monkeypatch)
        assert app.audio_quality is None

        # The ``audio`` arg is annotated ``Any`` precisely so this test
        fake_audio = MagicMock(name="audio_array")

        with caplog.at_level(logging.WARNING, logger="voice_typer.server.app"):
            result = app._finalize_audio_quality_report(fake_audio)

        assert result is None
        assert any("audio_quality controller unavailable" in rec.getMessage() for rec in caplog.records)

    def test_on_audio_quality_chunk_does_not_raise_attribute_error(self, app, monkeypatch):
        """raised ``AttributeError: 'NoneType' object has no attribute"""
        _force_audio_quality_lazy_init_failure(monkeypatch)

        try:
            app._on_audio_quality_chunk(0.01, 0.05)
        except AttributeError as exc:
            pytest.fail(
                f"_on_audio_quality_chunk() must not raise AttributeError "
                f"when the controller is unavailable; got: {exc!r}"
            )
        except Exception as exc:  # pragma: no cover - defensive
            pytest.fail(
                f"_on_audio_quality_chunk() raised an unexpected exception "
                f"type when the controller was unavailable; expected None "
                f"return, got {type(exc).__name__}: {exc!r}"
            )


class TestHappyPathForwarding:
    """Sanity check: when the lazy property returns a real controller,"""

    def test_undo_last_forwards_to_controller(self, app):
        """``app.undo_last()`` must call ``self.undo.undo_last()`` when"""
        fake_undo = MagicMock(name="UndoRepasteController")
        app.undo = fake_undo

        app.undo_last()

        fake_undo.undo_last.assert_called_once_with()

    def test_repaste_last_forwards_to_controller(self, app):
        """``app.repaste_last()`` must call ``self.undo.repaste_last()``"""
        fake_undo = MagicMock(name="UndoRepasteController")
        app.undo = fake_undo

        app.repaste_last()

        fake_undo.repaste_last.assert_called_once_with()

    def test_on_audio_quality_chunk_forwards_to_controller(self, app):
        """``app._on_audio_quality_chunk(rms, peak)`` must call"""
        fake_aq = MagicMock(name="AudioQualityController")
        app.audio_quality = fake_aq

        app._on_audio_quality_chunk(0.02, 0.08)

        fake_aq._on_audio_quality_chunk.assert_called_once_with(0.02, 0.08)
