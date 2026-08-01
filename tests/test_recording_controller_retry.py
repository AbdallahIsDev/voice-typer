"""FR-15 — regression tests for the F2 retry-model-load path in
:mod:`voice_typer.server.recording_controller`.

Pre-fix symptom: after the background model load FAILED, pressing F2
showed "starting up -- please wait..." in the tray and did nothing
(no ``start_background_load()`` re-trigger). The user was forced to
restart the app to recover the ASR engine, even though the
model_manager's tray message instructed "press F2 to retry".

Post-fix: ``_toggle_impl`` detects the "no active transcriber AND no
live loader" state and re-triggers ``start_background_load()``, sets
``_pending_dictation=True`` so the loader's ``finally`` block
auto-starts the dictation on success, and shows "Retrying model
load..." in the tray.

These tests run on any platform — the production code is
platform-agnostic (no ctypes / no PortAudio).
"""

from __future__ import annotations

from unittest.mock import MagicMock


def _make_controller_with_mock_app():
    """Build a RecordingController via the real ``__init__`` with a
    MagicMock app so all attribute reads succeed.

    Returns ``(ctrl, app)`` so the test can assert on the app's mock
    calls.
    """
    from voice_typer.server.recording_controller import RecordingController

    app = MagicMock()
    ctrl = RecordingController(app)
    # Initialize the cycle counter / id attributes the toggle impl reads.
    app._cycle_counter = 0
    app._cycle_id = "#test"
    # ``_busy_event.is_set()`` returns True → "not busy" (the toggle
    # proceeds). MagicMock auto-returns a MagicMock which is truthy —
    # but we want an explicit bool so the ``if not app._busy_event.is_set()``
    # branch is deterministic.
    app._busy_event.is_set.return_value = True
    # ``app.recorder.recording`` defaults to False (no recording in
    # progress) so the toggle proceeds past the recording check.
    app.recorder.recording = False
    # ``app.models.active_transcriber()`` returns None — this is the
    # precondition for the  path (model load failed, no active
    # engine).
    app.models.active_transcriber.return_value = None
    # ``app.models._model_load_thread`` is None — the loader already
    # exited (its ``finally`` block nulled it).
    app.models._model_load_thread = None
    return ctrl, app


# F2 re-triggers start_background_load on model-load failure ───


class TestRetriesModelLoad:
    """FR-15: pressing F2 after a model-load failure re-triggers
    ``start_background_load()`` instead of showing a misleading
    "starting up" message and doing nothing."""

    def test_re_triggers_start_background_load_when_no_active_transcriber(self):
        """When ``active_transcriber()`` returns None AND
        ``_model_load_thread`` is None, ``_toggle_impl`` must call
        ``app.models.start_background_load()`` to re-trigger the load."""
        ctrl, app = _make_controller_with_mock_app()
        ctrl.toggle()
        app.models.start_background_load.assert_called_once()
        # ``_pending_dictation`` must be set so the loader's ``finally``
        # block auto-starts the dictation if the retry succeeds.
        assert app.models._pending_dictation is True

    def test_tray_shows_retrying_model_load_message(self):
        """The tray must show "Retrying model load..." (the FR-15
        spec message) instead of the misleading "starting up" message."""
        ctrl, app = _make_controller_with_mock_app()
        ctrl.toggle()
        # Find the set_state call with the retry message.
        set_state_calls = app.tray.set_state.call_args_list
        messages = [call.args[1] for call in set_state_calls if len(call.args) >= 2]
        assert "Retrying model load..." in messages, (
            f"FR-15: tray should show 'Retrying model load...' on F2 retry; got messages: {messages}"
        )

    def test_does_not_show_starting_up_message_on_retry(self):
        """The misleading 'starting up -- please wait...' message must
        NOT be shown on the retry path (it implied passive waiting)."""
        ctrl, app = _make_controller_with_mock_app()
        ctrl.toggle()
        set_state_calls = app.tray.set_state.call_args_list
        messages = [call.args[1] for call in set_state_calls if len(call.args) >= 2]
        # The retry path may fall back to "starting up" if
        # start_background_load itself raises — but in the happy path
        # (MagicMock app), it should NOT appear.
        assert "Starting up -- please wait..." not in messages, (
            f"FR-15: 'starting up' message should not appear on the retry happy-path; got messages: {messages}"
        )

    def test_start_background_load_failure_falls_back_to_starting_up(self):
        """If ``start_background_load()`` itself raises (extremely
        unlikely — it only constructs a Thread), the tray should fall
        back to the 'starting up' message so the user still sees a
        loading indicator."""
        ctrl, app = _make_controller_with_mock_app()
        app.models.start_background_load.side_effect = RuntimeError("boom")
        # Should NOT raise — the exception is caught and the tray
        # falls back to "starting up".
        ctrl.toggle()
        set_state_calls = app.tray.set_state.call_args_list
        messages = [call.args[1] for call in set_state_calls if len(call.args) >= 2]
        # The fallback "starting up" message should appear because the
        # re-trigger failed.
        assert any("starting up" in str(m).lower() for m in messages), (
            f"FR-15: fallback 'starting up' message should appear when "
            f"start_background_load raises; got messages: {messages}"
        )

    def test_does_not_re_trigger_when_loader_is_alive(self):
        """When ``_model_load_thread`` is alive (load still in
        progress), the existing 'queuing dictation' path runs instead
        of the FR-15 retry path. ``start_background_load`` must NOT be
        called (the loader is already running)."""
        ctrl, app = _make_controller_with_mock_app()
        # Simulate a live loader thread.
        live_thread = MagicMock()
        live_thread.is_alive.return_value = True
        app.models._model_load_thread = live_thread
        # Still no active transcriber (load hasn't finished yet).
        app.models.active_transcriber.return_value = None
        ctrl.toggle()
        # The queuing path runs — start_background_load is NOT called
        # because the loader is already running.
        app.models.start_background_load.assert_not_called()
        # ``_pending_dictation`` IS set (the queuing path sets it so
        # the in-progress load's finally block auto-starts the
        # dictation on success).
        assert app.models._pending_dictation is True

    def test_does_not_re_trigger_when_active_transcriber_exists(self):
        """When ``active_transcriber()`` returns a non-None transcriber
        (load succeeded), the normal start/stop path runs. The FR-15
        retry path must NOT fire."""
        ctrl, app = _make_controller_with_mock_app()
        # Simulate a successful load.
        app.models.active_transcriber.return_value = MagicMock(is_loaded=True)
        # ``app.recorder.recording`` is False → toggle starts a recording.
        app.recorder.recording = False
        # ``app._start_dictation`` is a MagicMock by default — toggle
        # will call it.
        ctrl.toggle()
        # The retry path did NOT fire.
        app.models.start_background_load.assert_not_called()
        # The normal start path fired.
        app._start_dictation.assert_called_once()
