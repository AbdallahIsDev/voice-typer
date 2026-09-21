"""Tests for finally-block cleanup failures being logged, not silent."""

from __future__ import annotations

import contextlib
import logging
import threading
from unittest.mock import MagicMock

from voice_typer.server.dictation_pipeline import DictationPipeline

# ─── Test helpers (mirrors test_dictation_pipeline_lock_fixes) ─


class _TestApp:
    """Minimal non-magic test app for DictationPipeline ``run()`` tests."""

    def __init__(self) -> None:
        self.tray = MagicMock()
        self.tray.notify = MagicMock()
        self.config = MagicMock()
        self.config.bubble_behavior = "show_on_record"
        self.config.crash_recovery_enabled = False
        self.config.templates_enabled = True
        self.config.log_transcriptions = False
        self.config.model_size = "tiny.en"
        self.config.device = "cpu"
        self.config.llm_polish = False
        self.config.llm_api_key = ""
        self.config.llm_polish_consent = False
        self.config.llm_api_url = ""
        self.config.llm_model = ""
        self.config.llm_preset = "professional"
        self.history_db = MagicMock()
        self._vocabulary_manager: object = None
        self._template_manager: object = None
        self._llm_polisher: object = None
        self._crash_recovery = MagicMock()
        self._last_transcription: object = None
        self.models = MagicMock()
        # ``recording`` is a MagicMock, tests that need real lock
        self.recording = MagicMock()
        # ``recorder.recording`` is read by the finally block's
        self.recorder = MagicMock()
        self.recorder.recording = False
        self._busy_event = MagicMock()
        self._schedule_timer = MagicMock()
        self._waveform_bubble = MagicMock()
        self._lock = MagicMock()
        self._lock.__enter__ = MagicMock(return_value=self._lock)
        self._lock.__exit__ = MagicMock(return_value=False)

    def __getattr__(self, name: str) -> MagicMock:
        # Auto-mock unknown attributes (like MagicMock) but DO NOT
        if name in {
            "_vocab_fail_notified",
            "_template_fail_notified",
            "_history_fail_notified",
            "_crash_recovery_fail_notified",
            "_llm_consent_warned",
        }:
            raise AttributeError(name)
        mock = MagicMock()
        object.__setattr__(self, name, mock)
        return mock


def _new_pipeline(app: _TestApp) -> DictationPipeline:
    """Build a fresh DictationPipeline tied to ``app`` via ``__new__``."""
    pipeline = DictationPipeline.__new__(DictationPipeline)
    pipeline._app = app
    pipeline._duration = 1.0
    pipeline._cycle_id = "test-cycle"
    pipeline._audio = None
    pipeline._audio_stats = None
    pipeline._recorded_rms = 0.0
    pipeline._device_info = ""
    pipeline._last_resources_check_ts = 0.0
    pipeline._resources_check_interval = 60.0
    pipeline._templates_applied = False
    return pipeline


def _configure_recording_for_finally(app: _TestApp) -> None:
    """
    Configure ``app.recording`` so the finally block's watchdog-reset
    ``with _cancelled_lock:`` / ``_cancelled_set.discard()`` contract.
    """
    app.recording._cancelled_cycle_ids = set()
    app.recording._cancelled_cycle_ids_lock = threading.Lock()
    app.recording.pop_streaming_session = MagicMock(return_value=None)
    app.recording._reset_watchdog = MagicMock()
    app.recording._stop_watchdog_thread = MagicMock()
    app.recording._watchdog_lock = threading.Lock()
    app.recording._transcription_thread = None


class TestSentinelUnlinkFailureLogged:
    """``run()`` finally block, a DEBUG log line must be emitted (instead"""

    def test_sentinel_unlink_failure_emits_debug_log(self, caplog, monkeypatch):
        """``_sentinel.unlink()`` raising OSError → DEBUG log emitted."""

        class _FakeSentinelFile:
            """Stub for the sentinel Path."""

            def write_text(self, *args, **kwargs) -> None:
                pass

            def exists(self) -> bool:
                return True

            def unlink(self, *args, **kwargs) -> None:
                raise OSError("simulated read-only mount unlink failure")

        class _FakeConfigDir:
            """Stub for ``config_dir()`` return value."""

            def __truediv__(self, other: str) -> _FakeSentinelFile:
                return _FakeSentinelFile()

        # Patch the lazy import ``from voice_typer.server._paths
        monkeypatch.setattr(
            "voice_typer.server._paths.config_dir",
            lambda: _FakeConfigDir(),
        )

        app = _TestApp()
        _configure_recording_for_finally(app)
        pipeline = _new_pipeline(app)

        with (
            caplog.at_level(logging.DEBUG, logger="voice_typer.server.dictation_pipeline"),
            contextlib.suppress(Exception),
        ):
            # The body will fail (no real transcription backend) —
            pipeline.run(
                audio=None,
                duration=0.0,
                recorded_rms=0.0,
                cycle_id="test-cycle",
            )

        debug_logs = [
            r
            for r in caplog.records
            if r.levelno == logging.DEBUG and "finally cleanup step sentinel_unlink failed" in r.getMessage()
        ]
        assert debug_logs, (
            "When _sentinel.unlink() raises in the finally block, "
            "a DEBUG log line with 'finally cleanup step sentinel_unlink "
            "failed' must be emitted (pre-fix this was silently swallowed "
            "by contextlib.suppress)."
        )
        assert debug_logs[0].exc_info is not None, (
            "The DEBUG log for sentinel_unlink failure must carry exc_info=True so operators can see the traceback."
        )
        # The traceback should reference the simulated OSError.
        assert debug_logs[0].exc_info[0] is OSError, (
            "The logged exception should be the OSError raised by "
            f"_sentinel.unlink(); got {debug_logs[0].exc_info[0]!r}."
        )


class TestBusynessIdleFailureLogged:
    """app was torn down mid-cycle), a DEBUG log line must be emitted."""

    def test_busyness_idle_failure_emits_debug_log(self, caplog):
        """``_busyness.set_idle()`` raising RuntimeError → DEBUG log."""

        app = _TestApp()
        _configure_recording_for_finally(app)
        # Force the finally busyness-idle step to raise.
        app._busyness.set_idle.side_effect = RuntimeError("simulated torn-down coordinator")
        pipeline = _new_pipeline(app)

        with (
            caplog.at_level(logging.DEBUG, logger="voice_typer.server.dictation_pipeline"),
            contextlib.suppress(Exception),
        ):
            pipeline.run(
                audio=None,
                duration=0.0,
                recorded_rms=0.0,
                cycle_id="test-cycle",
            )

        debug_logs = [
            r
            for r in caplog.records
            if r.levelno == logging.DEBUG and "finally cleanup step busyness idle failed" in r.getMessage()
        ]
        assert debug_logs, (
            "When _busyness.set_idle() raises in the finally block, "
            "a DEBUG log line with 'finally cleanup step busyness idle "
            "failed' must be emitted (pre-fix this was silently swallowed)."
        )
        assert debug_logs[0].exc_info is not None, (
            "The DEBUG log for busyness idle failure must carry exc_info=True so operators can see the traceback."
        )


class TestGcCollectFailureLogged:
    """DEBUG log line must be emitted instead of silently swallowing."""

    def test_gc_collect_failure_emits_debug_log(self, caplog, monkeypatch):
        """``gc.collect(0)`` raising RuntimeError → DEBUG log."""

        # Patch the ``gc`` module so ``import gc; gc.collect(0)`` raises.
        import sys

        class _BrokenGC:
            def collect(self, generation: int = 2) -> int:
                raise RuntimeError("simulated broken gc.collect")

        original_gc = sys.modules.get("gc")
        sys.modules["gc"] = _BrokenGC()
        try:
            app = _TestApp()
            _configure_recording_for_finally(app)
            pipeline = _new_pipeline(app)

            with (
                caplog.at_level(
                    logging.DEBUG,
                    logger="voice_typer.server.dictation_pipeline",
                ),
                contextlib.suppress(Exception),
            ):
                pipeline.run(
                    audio=None,
                    duration=0.0,
                    recorded_rms=0.0,
                    cycle_id="test-cycle",
                )

            debug_logs = [
                r
                for r in caplog.records
                if r.levelno == logging.DEBUG and "finally cleanup step gc_collect failed" in r.getMessage()
            ]
            assert debug_logs, (
                "When gc.collect(0) raises in the finally block, "
                "a DEBUG log line with 'finally cleanup step gc_collect "
                "failed' must be emitted."
            )
            assert debug_logs[0].exc_info is not None, "The DEBUG log for gc_collect failure must carry exc_info=True."
        finally:
            if original_gc is not None:
                sys.modules["gc"] = original_gc
            else:
                sys.modules.pop("gc", None)


class TestFinallyBlockDoesNotRaise:
    """Non-regression: the finally block must NOT raise, even"""

    def test_finally_does_not_raise_when_sentinel_unlink_fails(self, monkeypatch):
        """``run()``, the finally block catches it and logs at DEBUG."""

        class _FakeSentinelFile:
            def write_text(self, *args, **kwargs) -> None:
                pass

            def exists(self) -> bool:
                return True

            def unlink(self, *args, **kwargs) -> None:
                raise OSError("simulated unlink failure")

        class _FakeConfigDir:
            def __truediv__(self, other: str) -> _FakeSentinelFile:
                return _FakeSentinelFile()

        monkeypatch.setattr(
            "voice_typer.server._paths.config_dir",
            lambda: _FakeConfigDir(),
        )

        app = _TestApp()
        _configure_recording_for_finally(app)
        pipeline = _new_pipeline(app)

        # ``run()`` should complete WITHOUT raising, the body's
        propagated: list[BaseException] = []
        try:
            pipeline.run(
                audio=None,
                duration=0.0,
                recorded_rms=0.0,
                cycle_id="test-cycle",
            )
        except BaseException as e:  # noqa: BLE001, we WANT to catch everything
            propagated.append(e)

        assert not propagated, (
            "Non-regression: the finally block must NOT raise when a "
            "cleanup step fails, the original exception path must be "
            f"preserved. Got propagated exception: {propagated!r}"
        )

    def test_finally_does_not_raise_when_busyness_idle_fails(self):
        """A busyness-idle failure must NOT propagate out of ``run()``"""

        app = _TestApp()
        _configure_recording_for_finally(app)
        app._busyness.set_idle.side_effect = RuntimeError("simulated coordinator failure")
        pipeline = _new_pipeline(app)

        propagated: list[BaseException] = []
        try:
            pipeline.run(
                audio=None,
                duration=0.0,
                recorded_rms=0.0,
                cycle_id="test-cycle",
            )
        except BaseException as e:  # noqa: BLE001
            propagated.append(e)

        assert not propagated, (
            "Non-regression: the finally block must NOT raise when "
            "_busyness.set_idle() fails, the original exception path must be "
            f"preserved. Got propagated exception: {propagated!r}"
        )

    def test_empty_cycle_id_does_not_raise_in_finally(self):
        """An empty ``cycle_id`` must not break the finally-block cleanup.

        The correlation-id token is only assigned when ``cycle_id`` is
        truthy, so reading it unconditionally in the finally block raised
        ``UnboundLocalError``, masking the very failure the cleanup was
        handling.
        """

        app = _TestApp()
        _configure_recording_for_finally(app)
        pipeline = _new_pipeline(app)

        propagated: list[BaseException] = []
        try:
            pipeline.run(
                audio=None,
                duration=0.0,
                recorded_rms=0.0,
                cycle_id="",
            )
        except BaseException as e:  # noqa: BLE001
            propagated.append(e)

        unbound = [e for e in propagated if isinstance(e, UnboundLocalError)]
        assert not unbound, (
            "run() with an empty cycle_id leaked UnboundLocalError from the "
            "finally block; the correlation-id token must be pre-initialized. "
            f"Got: {unbound!r}"
        )
