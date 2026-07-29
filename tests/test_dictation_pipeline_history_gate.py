"""FR-28: regression tests for the ``history_enabled`` config gate in
``dictation_pipeline._store_result``.

When ``self._app.config.history_enabled`` is ``False``, the
``add_transcription`` call must be skipped entirely (incognito mode).
The clipboard paste still happens — only persistence is disabled.
``history_enabled`` defaults to ``True`` (preserving the pre-FR-28
behavior); ``getattr(..., True)`` is used so dictation still works on
an older Config instance that hasn't yet picked up the new field
(added by P4-A2).
"""

from __future__ import annotations

from unittest.mock import MagicMock


def _make_pipeline(history_enabled=True, add_returns=1):
    """Build a minimal DictationPipeline for testing ``_store_result``.

    Mirrors the pattern in ``test_dictation_pipeline_h17_and_s3_cr10_fixes.py``:
    a MagicMock-backed app with explicit ``history_enabled`` (which may
    be missing on older Config instances).
    """
    from voice_typer.server.dictation_pipeline import DictationPipeline

    pipeline = DictationPipeline.__new__(DictationPipeline)
    app = MagicMock()
    # Set history_enabled explicitly (or delete it to simulate older Config).
    if history_enabled is None:
        # Simulate older Config without the field.
        del app.config.history_enabled
    else:
        app.config.history_enabled = history_enabled
    app.config.model_size = "tiny.en"
    app.config.device = "cpu"
    app.config.crash_recovery_enabled = False
    app.history_db.add_transcription.return_value = add_returns
    app.history_db.flush = MagicMock()
    app.tray.notify = MagicMock()
    app._history_fail_notified = False
    pipeline._app = app
    pipeline._duration = 1.0
    return pipeline, app


class TestHistoryEnabledGate:
    """FR-28: ``_store_result`` gates the ``add_transcription`` call on
    ``config.history_enabled``."""

    def test_calls_add_transcription_when_enabled(self):
        pipeline, app = _make_pipeline(history_enabled=True)
        pipeline._store_result("hello world")
        app.history_db.add_transcription.assert_called_once_with(
            "hello world",
            duration=1.0,
            model="tiny.en",
            device="cpu",
        )
        app.history_db.flush.assert_called_once()

    def test_skips_add_transcription_when_disabled(self):
        pipeline, app = _make_pipeline(history_enabled=False)
        pipeline._store_result("incognito dictation")
        app.history_db.add_transcription.assert_not_called()
        app.history_db.flush.assert_not_called()
        # No tray notification (no failure to report).
        app.tray.notify.assert_not_called()
        # The notify-once flag must remain False (no failure occurred).
        assert app._history_fail_notified is False

    def test_defaults_to_enabled_when_field_missing(self):
        """When the Config instance doesn't have ``history_enabled``
        (older Config pre-P4-A2), ``getattr(..., True)`` falls back to
        ``True`` — dictation is persisted as before."""
        pipeline, app = _make_pipeline(history_enabled=None)
        pipeline._store_result("legacy config")
        app.history_db.add_transcription.assert_called_once()
        app.history_db.flush.assert_called_once()

    def test_toggling_disabled_to_enabled_re_enables_persistence(self):
        """Toggling history_enabled from False to True at runtime must
        re-enable persistence on the next dictation."""
        pipeline, app = _make_pipeline(history_enabled=False)
        pipeline._store_result("first")
        assert app.history_db.add_transcription.call_count == 0

        # User enables history at runtime.
        app.config.history_enabled = True
        pipeline._store_result("second")
        assert app.history_db.add_transcription.call_count == 1
        app.history_db.flush.assert_called_once()

    def test_toggling_enabled_to_disabled_stops_persistence(self):
        """Toggling history_enabled from True to False at runtime must
        stop persistence on the next dictation."""
        pipeline, app = _make_pipeline(history_enabled=True)
        pipeline._store_result("first")
        assert app.history_db.add_transcription.call_count == 1
        app.history_db.flush.reset_mock()

        # User disables history at runtime.
        app.config.history_enabled = False
        pipeline._store_result("second")
        assert app.history_db.add_transcription.call_count == 1  # unchanged
        app.history_db.flush.assert_not_called()


class TestHistoryEnabledGateWithCrashRecovery:
    """FR-28: disabling history does NOT disable crash recovery
    (crash_recovery_enabled is a separate flag). When both are enabled
    + history_enabled is False, crash recovery still fires."""

    def test_crash_recovery_fires_when_history_disabled(self):
        from voice_typer.server.dictation_pipeline import DictationPipeline

        pipeline = DictationPipeline.__new__(DictationPipeline)
        app = MagicMock()
        app.config.history_enabled = False
        app.config.model_size = "tiny.en"
        app.config.device = "cpu"
        app.config.crash_recovery_enabled = True
        app.history_db.add_transcription.return_value = 1
        app.history_db.flush = MagicMock()
        app.tray.notify = MagicMock()
        app._history_fail_notified = False
        app._crash_recovery_fail_notified = False
        app._crash_recovery.add = MagicMock()
        app._crash_recovery.flush = MagicMock()
        pipeline._app = app
        pipeline._duration = 1.0

        pipeline._store_result("incognito + crash recovery")

        # History DB NOT called.
        app.history_db.add_transcription.assert_not_called()
        # Crash recovery IS called.
        app._crash_recovery.add.assert_called_once_with("incognito + crash recovery", pasted=False)
        app._crash_recovery.flush.assert_called_once()
