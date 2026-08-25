"""Regression test for NEW-UX-006: paste-failure surfaces as a renderer toast.

Pre-fix (``voice_typer/server/dictation_pipeline.py:_copy_and_paste``):
when ``clipboard.copy()`` raised ``ClipboardCopyError``, the pipeline
only notified the user via ``tray.notify(...)`` — i.e. the OS tray
icon tooltip. The renderer had no toast indication of the failure, so
users on Wayland / locked-screen / focus-stealer scenarios got a
silent recovery-file save unless they happened to glance at the tray
icon. No data loss (crash-recovery file at
``~/.voice-typer/recovery.json``), but a real UX gap.

Post-fix: the pipeline ALSO publishes a ``paste_failed`` event on the
in-process event bus (``voice_typer.server.event_bus.publish``), with
the payload shape the renderer's toast subscription expects::

    {
        "type": "paste_failed",
        "data": {
            "message": str,             # multi-line notice (same as tray)
            "recovery_path": str | None # path to crash-recovery file, or None
        },
    }

The renderer (``App.tsx``) subscribes via ``usePythonEvent("paste_failed", ...)``
and shows a sonner warning toast with an "Copy path" action button when
``recovery_path`` is present. The existing ``tray.notify(...)`` is
PRESERVED (kept for redundancy — tray tooltip is visible when the user
is on another app; the toast is visible when the renderer has focus).

These tests verify:
  1. ``event_bus.publish`` is called with ``type="paste_failed"`` and
     the correct payload shape on the clipboard-copy failure path.
  2. The tray notification STILL fires (existing behavior preserved).
  3. ``recovery_path`` is the crash-recovery file path when crash
     recovery is enabled, and ``None`` when disabled.
  4. A broken ``event_bus.publish`` does NOT abort the pipeline — the
     tray notification + crash-recovery write still complete (defence
     in depth: a misbehaving subscriber must never break the recovery
     path).
"""

from __future__ import annotations

from unittest.mock import MagicMock

from voice_typer.server import event_bus
from voice_typer.server.clipboard import ClipboardCopyError
from voice_typer.server.dictation_pipeline import DictationPipeline

# ─── Test app stub ──────────────────────────────────────────────────────


class _TestApp:
    """Minimal app stub for ``DictationPipeline._copy_and_paste``.

    Mirrors the pattern in ``tests/app/test_notify_once_flags.py``
    but specialized for the paste-failure path: we need ``clipboard``,
    ``_crash_recovery``, ``_waveform_bubble``, ``tray``, ``_busy_event``,
    and ``_schedule_timer`` to be controllable mocks. Other attributes
    the pipeline touches (history_db, models, etc.) are NOT needed for
    ``_copy_and_paste`` — that method only touches the clipboard and
    recovery state.
    """

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
        # Default: simulate a real recovery-file Path-like object that
        # str()s to a plausible path. Individual tests override this.
        self._crash_recovery._path = "/fake/recovery/recovery.json"
        self._waveform_bubble = MagicMock()
        self._busy_event = MagicMock()
        self._busy_event.set = MagicMock()
        self._device_info = "test-device"

        # _schedule_timer accepts (delay, callback) — store them so
        # tests can assert on the scheduled teardown timer if needed.
        # We do NOT invoke the callback inline; the production code
        # schedules a 3s tray-state reset that the tests don't need
        # to observe.
        self._scheduled: list[tuple[float, object]] = []

        def _schedule_timer(delay: float, cb: object) -> None:
            self._scheduled.append((delay, cb))

        self._schedule_timer = MagicMock(side_effect=_schedule_timer)


def _new_pipeline(app: _TestApp) -> DictationPipeline:
    """Build a fresh DictationPipeline tied to ``app``.

    Mirrors the construction pattern in
    ``tests/fixtures/dictation_pipeline_helpers.py``: bypass
    ``DictationPipeline.__init__`` (which requires a full audio
    pipeline) and set only the attributes ``_copy_and_paste``
    actually reads.
    """
    pipeline = DictationPipeline.__new__(DictationPipeline)
    pipeline._app = app
    pipeline._cycle_id = "test-cycle"
    pipeline._device_info = "test-device"
    return pipeline


def _capture_publish(monkeypatch) -> list[dict]:
    """Replace ``event_bus.publish`` with a capture-list-accumulating stub.

    Returns the list that the stub appends to — tests assert on its
    contents after invoking the pipeline.
    """
    published: list[dict] = []

    def _capture(event: dict) -> bool:
        published.append(event)
        return True

    monkeypatch.setattr(event_bus, "publish", _capture)
    return published


# ─── Tests ──────────────────────────────────────────────────────────────


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
        """Payload must have ``type`` + ``data.message`` + ``data.recovery_path``.

        The renderer's ``usePythonEvent("paste_failed", ...)`` handler in
        ``App.tsx`` reads ``data.message`` (string) and
        ``data.recovery_path`` (string | null). The test pins that shape
        so a future server-side refactor can't silently break the
        renderer subscription.
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

        # data shape
        data = event["data"]
        assert isinstance(data, dict), f"data must be a dict; got {type(data)}"
        assert "message" in data, "data.message is required (renderer toast title)"
        assert isinstance(data["message"], str), (
            f"data.message must be a string (renderer toasts on it); got {type(data['message'])}"
        )
        assert "recovery_path" in data, (
            "data.recovery_path is required (renderer uses it for the 'Copy path' action button)"
        )
        # recovery_path may be a string or None — both are valid; the
        # renderer hides the action button when it's None.
        assert data["recovery_path"] is None or isinstance(data["recovery_path"], str), (
            f"data.recovery_path must be str | None; got {type(data['recovery_path'])}"
        )

    def test_message_mentions_clipboard_unavailable(self, monkeypatch):
        """The user-facing message must mention 'clipboard' so the toast
        is self-explanatory (the renderer toast title is the first line
        of this message)."""
        app = _TestApp()
        app.clipboard.copy.side_effect = ClipboardCopyError("clipboard locked")
        pipeline = _new_pipeline(app)
        published = _capture_publish(monkeypatch)

        pipeline._copy_and_paste("hello world")

        events = [e for e in published if e.get("type") == "paste_failed"]
        assert events
        msg = events[0]["data"]["message"]
        assert "clipboard" in msg.lower(), (
            f"Message should mention 'clipboard' so the toast is self-explanatory; got: {msg!r}"
        )


class TestRecoveryPathPlumbing:
    """``recovery_path`` in the payload reflects the crash-recovery state."""

    def test_recovery_path_included_when_crash_recovery_enabled(self, monkeypatch):
        app = _TestApp()
        # crash_recovery_enabled defaults to True in _TestApp
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
        # so recovery_path must be None. The renderer hides the
        # "Copy path" action button in this case (toast shows message only).
        assert events[0]["data"]["recovery_path"] is None, (
            "recovery_path must be None when crash_recovery_enabled is "
            "False — the renderer uses this to decide whether to show "
            "the 'Copy path' action button."
        )


class TestTrayNotificationStillFires:
    """NEW-UX-006 critical rule: the existing tray notification is PRESERVED.

    The event publish is ADDITIVE — both must fire so the user sees the
    failure regardless of whether the renderer or the tray is in focus.
    """

    def test_tray_notify_still_called(self, monkeypatch):
        app = _TestApp()
        app.clipboard.copy.side_effect = ClipboardCopyError("clipboard locked")
        pipeline = _new_pipeline(app)
        published = _capture_publish(monkeypatch)

        pipeline._copy_and_paste("hello world")

        assert app.tray.notify.called, (
            "tray.notify must STILL fire after the event-bus publish was "
            "added (NEW-UX-006 critical rule: do not remove the tray "
            "notification — add the renderer toast alongside for "
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
        # The tray.notify message and the event payload message must
        # match (single source of truth for the failure wording).
        tray_call_args = app.tray.notify.call_args
        tray_message = tray_call_args.args[1] if tray_call_args.args else ""
        event_message = next(e["data"]["message"] for e in published if e.get("type") == "paste_failed")
        assert tray_message == event_message, (
            "tray.notify message and paste_failed event payload message "
            "must be identical (single source of truth for the failure "
            f"wording). tray={tray_message!r}, event={event_message!r}"
        )


class TestPublishFailureDoesNotBreakPipeline:
    """Defence in depth: a broken ``event_bus.publish`` must NOT abort
    the clipboard-failure recovery path. The existing tray.notify +
    crash-recovery write + busy_event.set + scheduled tray-state reset
    must all still complete so the user is never left in a stuck
    "transcribing…" state."""

    def test_pipeline_does_not_raise_when_publish_raises(self, monkeypatch):
        app = _TestApp()
        app.clipboard.copy.side_effect = ClipboardCopyError("clipboard locked")

        def _boom(_event: dict) -> bool:
            raise RuntimeError("event bus broken")

        monkeypatch.setattr(event_bus, "publish", _boom)

        pipeline = _new_pipeline(app)
        # Must not raise — the publish is wrapped in try/except in the
        # production code so a broken event bus never aborts the
        # clipboard-failure recovery path.
        pipeline._copy_and_paste("hello world")

        # Tray notify still fired (the existing behavior is preserved
        # even when the new event publish fails).
        assert app.tray.notify.called, (
            "tray.notify must fire even if event_bus.publish raises — "
            "the publish is wrapped in try/except for defence in depth."
        )
        # busy_event.set was called (so the UI doesn't get stuck in
        # "transcribing…" state).
        assert app._busy_event.set.called, "_busy_event.set must fire even if event_bus.publish raises."

    def test_pipeline_does_not_raise_when_event_bus_module_missing(self, monkeypatch):
        """Even an ImportError on the inline ``from voice_typer.server
        import event_bus`` must not abort the pipeline."""
        app = _TestApp()
        app.clipboard.copy.side_effect = ClipboardCopyError("clipboard locked")

        # Simulate the inline import failing — replace the publish
        # callable with one that raises ImportError on call (the
        # try/except in production catches Exception, which includes
        # ImportError).
        def _import_error(_event: dict) -> bool:
            raise ImportError("event_bus module missing")

        monkeypatch.setattr(event_bus, "publish", _import_error)

        pipeline = _new_pipeline(app)
        pipeline._copy_and_paste("hello world")

        assert app.tray.notify.called


class TestPayloadMatchesRendererExpectations:
    """Pin the exact payload contract the renderer's
    ``usePythonEvent("paste_failed", ...)`` handler in ``App.tsx``
    depends on. If the server-side payload schema ever drifts, this
    test fails before the renderer can silently break."""

    def test_renderer_reads_message_and_recovery_path(self, monkeypatch):
        """The renderer's handler does::

            const payload = (data ?? {}) as {
                message?: string;
                recovery_path?: string | null;
            };
            const message = payload.message ?? <default>;
            const recoveryPath =
                typeof payload.recovery_path === "string"
                    ? payload.recovery_path : null;

        This test feeds a real pipeline invocation and verifies the
        payload matches what the renderer expects to destructure."""
        app = _TestApp()
        app.clipboard.copy.side_effect = ClipboardCopyError("clipboard locked")
        app._crash_recovery._path = "/real/path/recovery.json"
        pipeline = _new_pipeline(app)
        published = _capture_publish(monkeypatch)

        pipeline._copy_and_paste("transcribed text")

        events = [e for e in published if e.get("type") == "paste_failed"]
        assert events
        data = events[0]["data"]

        # Renderer reads data.message as a string — must be present and
        # non-empty so the toast title is not blank.
        assert isinstance(data.get("message"), str)
        assert data["message"], "message must be a non-empty string"

        # Renderer reads data.recovery_path as string | null. When it's
        # a string, the renderer shows the "Copy path" action button.
        # When null, the button is omitted. Both are valid; we just pin
        # that the field is present and well-typed.
        assert "recovery_path" in data
        assert data["recovery_path"] is None or isinstance(data["recovery_path"], str)
        # In this test scenario, crash_recovery_enabled=True and the
        # path is set, so recovery_path must be the string path.
        assert data["recovery_path"] == "/real/path/recovery.json"
