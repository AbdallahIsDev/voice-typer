"""Regression test for NEW-UX-006: paste-failure surfaces as a renderer toast."""

from __future__ import annotations

from unittest.mock import MagicMock

from voice_typer.server import event_bus
from voice_typer.server.clipboard import ClipboardCopyError
from voice_typer.server.dictation_pipeline import DictationPipeline


class _TestApp:
    """Minimal app stub for ``DictationPipeline._copy_and_paste``."""

    def __init__(self) -> None:
        self.tray = MagicMock()
        self.tray.notify = MagicMock()
        self.tray.set_state = MagicMock()
        self.config = MagicMock()
        self.config.crash_recovery_enabled = True
        self.config.bubble_behavior = "transient"
        self.config.paste_on_stop = True
        self.config.clipboard_save_restore = True

        self.clipboard = MagicMock()
        self._crash_recovery = MagicMock()
        self._crash_recovery._path = "/fake/recovery/recovery.json"
        self._waveform_bubble = MagicMock()
        self._busyness = MagicMock()
        self._device_info = "test-device"

        # We do NOT invoke the callback inline; the production code
        self._scheduled: list[tuple[float, object]] = []

        def _schedule_timer(delay: float, cb: object) -> None:
            self._scheduled.append((delay, cb))

        self._schedule_timer = MagicMock(side_effect=_schedule_timer)


def _new_pipeline(app: _TestApp) -> DictationPipeline:
    """Build a fresh DictationPipeline tied to ``app``."""
    pipeline = DictationPipeline.__new__(DictationPipeline)
    pipeline._app = app
    pipeline._cycle_id = "test-cycle"
    pipeline._device_info = "test-device"
    return pipeline


def _capture_publish(monkeypatch) -> list[dict]:
    """Replace ``event_bus.publish`` with a capture-list-accumulating stub."""
    published: list[dict] = []

    def _capture(event: dict) -> bool:
        published.append(event)
        return True

    monkeypatch.setattr(event_bus, "publish", _capture)
    return published


class TestPasteFailurePublishesEvent:
    """NEW-UX-006: the paste-failure path publishes a ``paste_failed`` event."""

    def test_publish_called_with_paste_failed_event(self, monkeypatch):
        app = _TestApp()
        app.clipboard.copy.side_effect = ClipboardCopyError("clipboard locked")
        pipeline = _new_pipeline(app)
        published = _capture_publish(monkeypatch)

        pipeline._copy_and_paste("hello world")

        paste_failed_events = [e for e in published if e.get("type") == "paste_failed"]
        assert len(paste_failed_events) == 1, (
            f"Expected exactly one paste_failed event; got {len(paste_failed_events)}. "
            f"All published events: {published}"
        )

    def test_payload_shape_matches_renderer_subscription(self, monkeypatch):
        """
        Payload must have ``type`` + ``data.message`` + ``data.recovery_path``.
        ``data.recovery_path`` (string | null). The test pins that shape
        """
        app = _TestApp()
        app.clipboard.copy.side_effect = ClipboardCopyError("clipboard locked")
        pipeline = _new_pipeline(app)
        published = _capture_publish(monkeypatch)

        pipeline._copy_and_paste("hello world")

        events = [e for e in published if e.get("type") == "paste_failed"]
        assert events, "paste_failed event must be published"
        event = events[0]

        # Top-level shape
        assert set(event.keys()) >= {"type", "data"}, f"Event must have 'type' and 'data' keys; got {set(event.keys())}"
        assert event["type"] == "paste_failed"

        data = event["data"]
        assert isinstance(data, dict), f"data must be a dict; got {type(data)}"
        assert "recovery_path" in data, (
            "data.recovery_path is required (renderer uses it for the 'Copy path' action button)"
        )
        assert data["recovery_path"] is None or isinstance(data["recovery_path"], str), (
            f"data.recovery_path must be str | None; got {type(data['recovery_path'])}"
        )

    def test_message_omitted_so_renderer_localized_fallback_fires(self, monkeypatch):
        """renderer's ``usePasteFailedToast`` uses ``payload.message ??``"""
        app = _TestApp()
        app.clipboard.copy.side_effect = ClipboardCopyError("clipboard locked")
        pipeline = _new_pipeline(app)
        published = _capture_publish(monkeypatch)

        pipeline._copy_and_paste("hello world")

        events = [e for e in published if e.get("type") == "paste_failed"]
        assert events
        data = events[0]["data"]
        assert data.get("message") in (None, ""), (
            f"paste_failed.message must be omitted so the renderer's "
            f"localized home.pasteFailedMessage fallback fires; got: {data.get('message')!r}"
        )


class TestRecoveryPathPlumbing:
    """``recovery_path`` in the payload reflects the crash-recovery state."""

    def test_recovery_path_included_when_crash_recovery_enabled(self, monkeypatch):
        app = _TestApp()
        app.clipboard.copy.side_effect = ClipboardCopyError("clipboard locked")
        app._crash_recovery._path = "/fake/path/recovery.json"
        pipeline = _new_pipeline(app)
        published = _capture_publish(monkeypatch)

        pipeline._copy_and_paste("hello world")

        events = [e for e in published if e.get("type") == "paste_failed"]
        assert events
        assert events[0]["data"]["recovery_path"] == ("/fake/path/recovery.json"), (
            "recovery_path should be the crash-recovery file path when crash recovery is enabled."
        )

    def test_recovery_path_none_when_crash_recovery_disabled(self, monkeypatch):
        app = _TestApp()
        app.config.crash_recovery_enabled = False
        app.clipboard.copy.side_effect = ClipboardCopyError("clipboard locked")
        pipeline = _new_pipeline(app)
        published = _capture_publish(monkeypatch)

        pipeline._copy_and_paste("hello world")

        events = [e for e in published if e.get("type") == "paste_failed"]
        assert events
        # When crash_recovery is disabled, no recovery file is written,
        assert events[0]["data"]["recovery_path"] is None, (
            "recovery_path must be None when crash_recovery_enabled is "
            "False, the renderer uses this to decide whether to show "
            "the 'Copy path' action button."
        )


class TestTrayNotificationStillFires:
    """NEW-UX-006 critical rule: the existing tray notification is PRESERVED."""

    def test_tray_notify_still_called(self, monkeypatch):
        app = _TestApp()
        app.clipboard.copy.side_effect = ClipboardCopyError("clipboard locked")
        pipeline = _new_pipeline(app)
        published = _capture_publish(monkeypatch)

        pipeline._copy_and_paste("hello world")

        assert app.tray.notify.called, (
            "tray.notify must STILL fire after the event-bus publish was "
            "added (NEW-UX-006 critical rule: do not remove the tray "
            "notification, add the renderer toast alongside for "
            "redundancy)."
        )
        # And the event was published too (sanity).
        assert any(e.get("type") == "paste_failed" for e in published)

    def test_tray_notify_and_event_publish_both_fire_in_same_call(self, monkeypatch):
        app = _TestApp()
        app.clipboard.copy.side_effect = ClipboardCopyError("clipboard locked")
        pipeline = _new_pipeline(app)
        published = _capture_publish(monkeypatch)

        pipeline._copy_and_paste("hello world")

        # Both must happen in the same _copy_and_paste invocation.
        assert app.tray.notify.called
        assert any(e.get("type") == "paste_failed" for e in published)
        tray_call_args = app.tray.notify.call_args
        tray_message = tray_call_args.args[1] if tray_call_args.args else ""
        event_data = next(e["data"] for e in published if e.get("type") == "paste_failed")
        assert "clipboard" in tray_message.lower(), f"tray.notify body must mention clipboard; got: {tray_message!r}"
        assert event_data.get("message") in (None, ""), (
            "paste_failed.message must be omitted (renderer owns the "
            f"localized toast text); got: {event_data.get('message')!r}"
        )


class TestPublishFailureDoesNotBreakPipeline:
    """Defence in depth: a broken ``event_bus.publish`` must NOT abort"""

    def test_pipeline_does_not_raise_when_publish_raises(self, monkeypatch):
        app = _TestApp()
        app.clipboard.copy.side_effect = ClipboardCopyError("clipboard locked")

        def _boom(_event: dict) -> bool:
            raise RuntimeError("event bus broken")

        monkeypatch.setattr(event_bus, "publish", _boom)

        pipeline = _new_pipeline(app)
        pipeline._copy_and_paste("hello world")

        # Tray notify still fired (the existing behavior is preserved
        assert app.tray.notify.called, (
            "tray.notify must fire even if event_bus.publish raises, "
            "the publish is wrapped in try/except for defence in depth."
        )
        # "transcribing…" state).
        assert app._busyness.set_idle.called, "_busyness.set_idle must fire even if event_bus.publish raises."

    def test_pipeline_does_not_raise_when_event_bus_module_missing(self, monkeypatch):
        """Even an ImportError on the inline ``from voice_typer.server"""
        app = _TestApp()
        app.clipboard.copy.side_effect = ClipboardCopyError("clipboard locked")

        # Simulate the inline import failing, replace the publish
        def _import_error(_event: dict) -> bool:
            raise ImportError("event_bus module missing")

        monkeypatch.setattr(event_bus, "publish", _import_error)

        pipeline = _new_pipeline(app)
        pipeline._copy_and_paste("hello world")

        assert app.tray.notify.called


class TestPayloadMatchesRendererExpectations:
    """Pin the exact payload contract the renderer's"""

    def test_renderer_reads_message_and_recovery_path(self, monkeypatch):
        """The renderer's handler does::"""
        app = _TestApp()
        app.clipboard.copy.side_effect = ClipboardCopyError("clipboard locked")
        app._crash_recovery._path = "/real/path/recovery.json"
        pipeline = _new_pipeline(app)
        published = _capture_publish(monkeypatch)

        pipeline._copy_and_paste("transcribed text")

        events = [e for e in published if e.get("type") == "paste_failed"]
        assert events
        data = events[0]["data"]

        assert data.get("message") in (None, "")

        # When null, the button is omitted. Both are valid; we just pin
        assert "recovery_path" in data
        assert data["recovery_path"] is None or isinstance(data["recovery_path"], str)
        assert data["recovery_path"] == "/real/path/recovery.json"
