""":mod:`voice_typer.server.recording_controller`."""

from __future__ import annotations

from unittest.mock import MagicMock


def _make_controller_with_mock_app():
    """MagicMock app so all attribute reads succeed."""
    from voice_typer.server.recording_controller import RecordingController

    app = MagicMock()
    ctrl = RecordingController(app)
    # Initialize the cycle counter / id attributes the toggle impl reads.
    app._cycle_counter = 0
    app._cycle_id = "#test"
    # ``_busy_event.is_set()`` returns True → "not busy" (the toggle
    app._busy_event.is_set.return_value = True
    app.recorder.recording = False
    app.models.active_transcriber.return_value = None
    # ``app.models._model_load_thread`` is None, the loader already
    app.models._model_load_thread = None
    return ctrl, app


# F2 re-triggers start_background_load on model-load failure ───


class TestRetriesModelLoad:
    """FR-15: pressing F2 after a model-load failure re-triggers"""

    def test_re_triggers_start_background_load_when_no_active_transcriber(self):
        """``_model_load_thread`` is None, ``_toggle_impl`` must call"""
        ctrl, app = _make_controller_with_mock_app()
        ctrl.toggle()
        app.models.start_background_load.assert_called_once()
        # ``_pending_dictation`` must be set so the loader's ``finally``
        assert app.models._pending_dictation is True

    def test_tray_shows_retrying_model_load_message(self):
        """The tray must show \"Retrying model load...\" (the FR-15"""
        ctrl, app = _make_controller_with_mock_app()
        ctrl.toggle()
        # Find the set_state call with the retry message.
        set_state_calls = app.tray.set_state.call_args_list
        messages = [call.args[1] for call in set_state_calls if len(call.args) >= 2]
        assert "Retrying model load..." in messages, (
            f"FR-15: tray should show 'Retrying model load...' on F2 retry; got messages: {messages}"
        )

    def test_does_not_show_starting_up_message_on_retry(self):
        """The misleading 'starting up' message must"""
        ctrl, app = _make_controller_with_mock_app()
        ctrl.toggle()
        set_state_calls = app.tray.set_state.call_args_list
        messages = [call.args[1] for call in set_state_calls if len(call.args) >= 2]
        assert "Starting up | please wait..." not in messages, (
            f"FR-15: 'starting up' message should not appear on the retry happy-path; got messages: {messages}"
        )

    def test_start_background_load_failure_falls_back_to_starting_up(self):
        """If ``start_background_load()`` itself raises (extremely"""
        ctrl, app = _make_controller_with_mock_app()
        app.models.start_background_load.side_effect = RuntimeError("boom")
        # Should NOT raise, the exception is caught and the tray
        ctrl.toggle()
        set_state_calls = app.tray.set_state.call_args_list
        messages = [call.args[1] for call in set_state_calls if len(call.args) >= 2]
        assert any("starting up" in str(m).lower() for m in messages), (
            f"FR-15: fallback 'starting up' message should appear when "
            f"start_background_load raises; got messages: {messages}"
        )

    def test_does_not_re_trigger_when_loader_is_alive(self):
        """progress), the existing 'queuing dictation' path runs instead"""
        ctrl, app = _make_controller_with_mock_app()
        # Simulate a live loader thread.
        live_thread = MagicMock()
        live_thread.is_alive.return_value = True
        app.models._model_load_thread = live_thread
        # Still no active transcriber (load hasn't finished yet).
        app.models.active_transcriber.return_value = None
        ctrl.toggle()
        # The queuing path runs, start_background_load is NOT called
        app.models.start_background_load.assert_not_called()
        assert app.models._pending_dictation is True

    def test_does_not_re_trigger_when_active_transcriber_exists(self):
        """When ``active_transcriber()`` returns a non-None transcriber"""
        ctrl, app = _make_controller_with_mock_app()
        # Simulate a successful load.
        app.models.active_transcriber.return_value = MagicMock(is_loaded=True)
        # ``app.recorder.recording`` is False → toggle starts a recording.
        app.recorder.recording = False
        # ``app._start_dictation`` is a MagicMock by default, toggle
        ctrl.toggle()
        # The retry path did NOT fire.
        app.models.start_background_load.assert_not_called()
        # The normal start path fired.
        app._start_dictation.assert_called_once()
