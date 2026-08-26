"""Tests for the GPU→CPU fallback user notification (Whisper engine).

The Whisper-family engine's ``with_gpu_fallback`` tears down the GPU
model and reloads on CPU synchronously (5-50s freeze). These tests pin:

1. ``with_gpu_fallback`` publishes the ``gpu_cpu_fallback`` event
   BEFORE ``engine._reload_under_lock()`` starts, with the same payload
   shape the parakeet engine uses for ``parakeet_cpu_fallback``
   (``{"type": ..., "data": {"device": "cpu", "reason": str(err)[:200]}}``).
2. A failing event publication never breaks the fallback (best-effort).
3. ``tray_notifications.on_gpu_cpu_fallback`` flips
   ``tray._cpu_fallback_active``, shows the toast, and re-applies tray
   state; malformed payloads are ignored.
4. The tray subscribes/unsubscribes the handler alongside the parakeet
   one, and the real event bus delivery reaches it.
"""

from __future__ import annotations

import inspect
from unittest.mock import MagicMock

import pytest

# ─── shared doubles ──────────────────────────────────────────────────────


class _RecordingEngine:
    """Minimal engine double exposing exactly what ``with_gpu_fallback``
    touches. Records every hook invocation so tests can assert order."""

    def __init__(self, calls: list[str] | None = None) -> None:
        self.calls = calls if calls is not None else []
        self._device = "cuda"
        self._model = object()
        self._compute_type = "float16"
        self._pending_gc_collect = False

    def _is_gpu_runtime_error(self, exc: Exception) -> bool:
        self.calls.append("classify")
        return "cuda" in str(exc).lower()

    def _apply_auto_beam_size(self) -> None:
        self.calls.append("beam")

    def _reload_under_lock(self) -> None:
        self.calls.append("reload")


def _make_inner(engine: _RecordingEngine):
    """Return an ``inner`` transcribe callable that fails once with a
    CUDA-classified error, then succeeds."""
    state = {"attempts": 0}

    def inner(audio, *args, **kwargs):
        engine.calls.append("inner")
        state["attempts"] += 1
        if state["attempts"] == 1:
            raise RuntimeError("CUDA error: cublas status initialization failed")
        return "transcribed"

    return inner


@pytest.fixture()
def recorded_publish(monkeypatch):
    """Replace ``event_bus.publish`` with a recording fake.

    The production site imports the module lazily inside the function
    and reads ``publish`` off it at call time, so patching the module
    attribute intercepts the real lookup path.
    """
    from voice_typer.server import event_bus

    published: list[dict] = []
    monkeypatch.setattr(event_bus, "publish", published.append)
    return published


# ─── 1. publication happens before the reload ────────────────────────────


class TestFallbackPublishesBeforeReload:
    def test_event_published_before_reload_with_parakeet_payload_shape(self, monkeypatch):
        from voice_typer.server import event_bus
        from voice_typer.server.transcription_fallback import with_gpu_fallback

        order: list[str] = []
        published: list[dict] = []

        def recording_publish(event):
            order.append("publish")
            published.append(event)

        monkeypatch.setattr(event_bus, "publish", recording_publish)

        engine = _RecordingEngine(calls=order)
        result = with_gpu_fallback(engine, _make_inner(engine), b"audio-bytes")

        assert result == "transcribed"
        # Publish must land AFTER classification and BEFORE the model
        # teardown/reload — the whole point is telling the user why the
        # next seconds freeze.
        assert order == ["inner", "classify", "publish", "beam", "reload", "inner"], f"unexpected call order: {order}"
        assert len(published) == 1
        event = published[0]
        assert event["type"] == "gpu_cpu_fallback"
        assert event["data"]["device"] == "cpu"
        reason = event["data"]["reason"]
        assert "cublas" in reason.lower()
        assert len(reason) <= 200

    def test_reason_truncated_to_200_chars(self, recorded_publish):
        from voice_typer.server.transcription_fallback import with_gpu_fallback

        long_message = "CUDA error: " + "x" * 500

        class _AlwaysGpuEngine(_RecordingEngine):
            def _is_gpu_runtime_error(self, exc: Exception) -> bool:
                return True

        def always_failing(audio, *args, **kwargs):
            raise RuntimeError(long_message)

        # Both attempts fail — we only care about the published payload.
        engine = _AlwaysGpuEngine()
        with pytest.raises(RuntimeError):
            with_gpu_fallback(engine, always_failing, b"audio")

        assert len(recorded_publish) == 1
        assert recorded_publish[0]["data"]["reason"] == long_message[:200]

    def test_non_gpu_error_never_publishes(self, recorded_publish):
        from voice_typer.server.transcription_fallback import with_gpu_fallback

        engine = _RecordingEngine()

        def inner(audio, *args, **kwargs):
            raise ValueError("plain non-gpu failure")

        with pytest.raises(ValueError):
            with_gpu_fallback(engine, inner, b"audio")

        assert recorded_publish == []
        assert "reload" not in engine.calls


# ─── 2. publish failure must not break the fallback ──────────────────────


class TestPublishFailureSuppressed:
    def test_raising_publish_does_not_break_fallback(self, monkeypatch):
        from voice_typer.server import event_bus
        from voice_typer.server.transcription_fallback import with_gpu_fallback

        def exploding_publish(event):
            raise RuntimeError("event bus down")

        monkeypatch.setattr(event_bus, "publish", exploding_publish)

        engine = _RecordingEngine()
        result = with_gpu_fallback(engine, _make_inner(engine), b"audio-bytes")
        assert result == "transcribed"
        assert "reload" in engine.calls


# ─── 3. the tray-side handler ────────────────────────────────────────────


class TestOnGpuCpuFallbackHandler:
    def test_sets_flag_shows_toast_and_republishes_state(self, monkeypatch):
        from voice_typer.server import tray_notifications as tn

        tray = MagicMock()
        tray._state = "IDLE"
        tray._message = ""
        tray._cpu_fallback_active = False
        shown: list[tuple[str, str]] = []
        monkeypatch.setattr(tn, "notify", lambda t, title, message: shown.append((title, message)))

        tn.on_gpu_cpu_fallback(
            tray,
            {"type": "gpu_cpu_fallback", "data": {"device": "cpu", "reason": "cublas"}},
        )

        assert tray._cpu_fallback_active is True
        assert len(shown) == 1
        title, message = shown[0]
        assert title  # app name branding constant, non-empty
        assert "CPU" in message
        assert "GPU" in message
        tray._apply_state.assert_called_once()
        tray._publish_tray_state.assert_called_once()

    def test_ignores_non_dict_event(self, monkeypatch):
        from voice_typer.server import tray_notifications as tn

        tray = MagicMock()
        tray._cpu_fallback_active = False
        shown: list = []
        monkeypatch.setattr(tn, "notify", lambda *a, **k: shown.append(1))

        tn.on_gpu_cpu_fallback(tray, "not a dict")  # type: ignore[arg-type]

        assert tray._cpu_fallback_active is False
        assert shown == []
        tray._apply_state.assert_not_called()

    def test_ignores_wrong_event_type(self, monkeypatch):
        from voice_typer.server import tray_notifications as tn

        tray = MagicMock()
        tray._cpu_fallback_active = False
        shown: list = []
        monkeypatch.setattr(tn, "notify", lambda *a, **k: shown.append(1))

        tn.on_gpu_cpu_fallback(tray, {"type": "parakeet_cpu_fallback", "data": {}})

        assert tray._cpu_fallback_active is False
        assert shown == []
        tray._apply_state.assert_not_called()

    def test_state_publish_failure_does_not_mask_the_rest(self, monkeypatch):
        from voice_typer.server import tray_notifications as tn

        tray = MagicMock()
        tray._state = "IDLE"
        tray._message = ""
        tray._cpu_fallback_active = False
        tray._publish_tray_state.side_effect = RuntimeError("publish boom")
        shown: list = []
        monkeypatch.setattr(tn, "notify", lambda *a, **k: shown.append(1))

        # Must not raise.
        tn.on_gpu_cpu_fallback(
            tray,
            {"type": "gpu_cpu_fallback", "data": {"device": "cpu", "reason": "x"}},
        )

        assert tray._cpu_fallback_active is True
        assert len(shown) == 1
        tray._apply_state.assert_called_once()


# ─── 4. wiring: delegate + subscribe/unsubscribe + real bus delivery ─────


class TestSubscriptionWiring:
    def test_tray_delegate_routes_to_handler(self, monkeypatch):
        from voice_typer.server import tray_notifications as tn
        from voice_typer.server.tray import TrayIcon

        routed: list[tuple[object, dict]] = []
        monkeypatch.setattr(tn, "on_gpu_cpu_fallback", lambda tray_, event: routed.append((tray_, event)))

        tray = MagicMock()
        event = {"type": "gpu_cpu_fallback", "data": {}}
        TrayIcon._on_gpu_cpu_fallback(tray, event)

        assert routed == [(tray, event)]

    def test_start_subscribes_both_fallback_handlers(self):
        from voice_typer.server.tray import TrayIcon

        src = inspect.getsource(TrayIcon.start)
        assert "_event_bus.subscribe(self._on_parakeet_cpu_fallback)" in src
        assert "_event_bus.subscribe(self._on_gpu_cpu_fallback)" in src
        # The new subscription must sit inside the same guarded block
        # (a bare unguarded second subscribe would regress the
        # WARNING-on-failure promotion).
        parakeet_idx = src.index("subscribe(self._on_parakeet_cpu_fallback)")
        gpu_idx = src.index("subscribe(self._on_gpu_cpu_fallback)")
        try_idx = src.index("try:", 0, parakeet_idx)
        except_idx = src.index("except Exception:", parakeet_idx)
        assert try_idx < gpu_idx < except_idx

    def test_stop_unsubscribes_both_fallback_handlers(self):
        from voice_typer.server import tray_lifecycle

        src = inspect.getsource(tray_lifecycle)
        assert "_event_bus.unsubscribe(tray._on_parakeet_cpu_fallback)" in src
        assert "_event_bus.unsubscribe(tray._on_gpu_cpu_fallback)" in src

    def test_real_event_bus_delivers_published_event_to_subscribed_handler(self, monkeypatch):
        """Round-trip over the REAL bus: publish → subscriber fan-out →
        handler side effects (flag flip + toast request)."""
        from voice_typer.server import event_bus, tray_notifications as tn

        tray = MagicMock()
        tray._state = "IDLE"
        tray._message = ""
        tray._cpu_fallback_active = False
        shown: list = []
        monkeypatch.setattr(tn, "notify", lambda t, title, message: shown.append(message))

        # Subscriber contract is callback(event); bind the tray here the
        # same way TrayIcon.start binds it via its delegate method.
        subscriber = lambda event: tn.on_gpu_cpu_fallback(tray, event)  # noqa: E731
        event_bus.subscribe(subscriber)
        try:
            delivered = event_bus.publish(
                {
                    "type": "gpu_cpu_fallback",
                    "data": {"device": "cpu", "reason": "cuda oom"},
                }
            )
        finally:
            event_bus.unsubscribe(subscriber)

        assert delivered is True
        assert tray._cpu_fallback_active is True
        assert len(shown) == 1
