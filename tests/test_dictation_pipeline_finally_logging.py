"""Tests for finally-block cleanup failures being logged, not silent.

The ``DictationPipeline.run`` method's ``finally`` block runs 7 cleanup
steps (sentinel unlink, audio zero, watchdog reset, streaming-session
cancel, busy-event clear, transcription-thread clear, gc.collect). Each
step used to be wrapped in ``with contextlib.suppress(Exception):`` —
which silently swallowed ANY error. Operators could not diagnose why a
dictation cycle left the app stuck in the BUSY state (e.g. a busy_event
that never cleared because ``threading.Event.set`` raised on a torn-
down app, or a sentinel file that couldn't be unlinked because the
config dir was on a read-only mount).

The fix replaces each ``contextlib.suppress(Exception)`` with an
explicit ``try/except Exception`` that calls ``log.debug(...)`` with
``exc_info=True``. The cleanup behavior is preserved (the finally
block still does NOT raise — the original exception from the try block
is not masked), but the failure is now observable in the debug log.

These tests trigger a failure in each cleanup step (by mocking the
cleanup target to raise) and assert:
  1. A DEBUG log line with the expected step name is emitted.
  2. The ``finally`` block does NOT raise (the original exception
     path is preserved — run() returns normally after the body's
     ``except Exception`` handler runs).
  3. ``exc_info=True`` is attached (the traceback is in the log).
"""

from __future__ import annotations

import contextlib
import logging
import threading
from unittest.mock import MagicMock

from voice_typer.server.dictation_pipeline import DictationPipeline

# ─── Test helpers (mirrors test_dictation_pipeline_lock_fixes) ─


class _TestApp:
    """Minimal non-magic test app for DictationPipeline ``run()`` tests.

    Mirrors the stub pattern in
    ``test_dictation_pipeline_lock_fixes.py``: a custom class
    (not ``MagicMock``) so the four notify-once flag attributes default
    to ``False`` via ``getattr(..., False)`` — MagicMock would auto-create
    truthy children.
    """

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
        # ``recording`` is a MagicMock — tests that need real lock
        # semantics override it. Default MagicMock supports ``with``
        # via auto-created ``__enter__``/``__exit__`` children.
        self.recording = MagicMock()
        # ``recorder.recording`` is read by the finally block's
        # streaming-session cleanup branch — make it False so the
        # ``if session is not None and not recorder.recording``
        # branch short-circuits when ``pop_streaming_session`` returns
        # None (the default MagicMock return).
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
        # auto-create the notify-once flag names.
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
    """Build a fresh DictationPipeline tied to ``app`` via ``__new__``.

    Mirrors how ``RecordingController._stop_impl`` constructs a new
    pipeline per transcription cycle. Bypasses ``__init__`` (which
    expects a real VoiceTyperApp) and manually sets the attributes
    ``run()`` reads.
    """
    pipeline = DictationPipeline.__new__(DictationPipeline)
    pipeline._app = app
    pipeline._duration = 1.0
    pipeline._cycle_id = "test-cycle"
    pipeline._audio = None
    pipeline._audio_stats = None
    pipeline._recorded_rms = 0.0
    pipeline._device_info = ""
    pipeline._watchdog = None
    pipeline._last_resources_check_ts = 0.0
    pipeline._resources_check_interval = 60.0
    pipeline._templates_applied = False
    return pipeline


def _configure_recording_for_finally(app: _TestApp) -> None:
    """Configure ``app.recording`` so the finally block's watchdog-reset
    and streaming-session-cleanup branches short-circuit cleanly.

    Without this, the MagicMock auto-creates child mocks for
    ``_cancelled_cycle_ids`` (a set) and
    ``_cancelled_cycle_ids_lock`` (a lock) that don't support the
    ``with _cancelled_lock:`` / ``_cancelled_set.discard()`` contract.
    """
    app.recording._cancelled_cycle_ids = set()
    app.recording._cancelled_cycle_ids_lock = threading.Lock()
    app.recording.pop_streaming_session = MagicMock(return_value=None)
    app.recording._reset_watchdog = MagicMock()
    app.recording._stop_watchdog_thread = MagicMock()
    app.recording._watchdog_lock = threading.Lock()
    app.recording._transcription_thread = None


# ─── Sentinel unlink failure is logged at DEBUG ────────────────────────


class TestSentinelUnlinkFailureLogged:
    """When the in-flight sentinel's ``unlink()`` raises in the
    ``run()`` finally block, a DEBUG log line must be emitted (instead
    of the pre-fix silent ``contextlib.suppress(Exception)`` swallow).

    Pre-fix: operators could not tell why the sentinel file lingered
    after a dictation cycle (e.g. read-only mount, permission revoked
    mid-run). Crash-recovery would then falsely flag the next startup
    as "interrupted dictation" because the sentinel was never cleared.

    Post-fix: the failure is logged at DEBUG with ``exc_info=True`` so
    the traceback is in the log. The finally block still does NOT
    raise — the original exception path is preserved.
    """

    def test_sentinel_unlink_failure_emits_debug_log(self, caplog, monkeypatch):
        """``_sentinel.unlink()`` raising OSError → DEBUG log emitted."""

        class _FakeSentinelFile:
            """Stub for the sentinel Path.

            ``write_text`` is a no-op so the sentinel WRITE at the top
            of ``run()`` (also wrapped in suppress, but NOT in scope for
            the finally block) doesn't fail. ``exists`` returns True so the unlink
            branch runs. ``unlink`` raises OSError so the NEW try/except
            in the finally block logs it.
            """

            def write_text(self, *args, **kwargs) -> None:
                pass

            def exists(self) -> bool:
                return True

            def unlink(self, *args, **kwargs) -> None:
                raise OSError("simulated read-only mount unlink failure")

        class _FakeConfigDir:
            """Stub for ``config_dir()`` return value.

            ``__truediv__`` returns a fresh ``_FakeSentinelFile`` so
            both the write (top of ``run()``) and the clear (finally
            block) operate on a stub whose ``unlink`` raises.
            """

            def __truediv__(self, other: str) -> _FakeSentinelFile:
                return _FakeSentinelFile()

        # Patch the lazy import ``from voice_typer.server._paths
        # import config_dir as _config_dir`` — the import runs inside
        # the try block each call, so monkeypatching the module
        # attribute is sufficient.
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
            # ``run()``'s ``except Exception`` handler catches it and
            # runs the tray-error path. The finally block then runs
            # regardless. We suppress the body's exception to focus
            # the test on the finally-block logging behavior (mirrors
            # the H-17 test pattern).
            pipeline.run(
                audio=None,
                duration=0.0,
                recorded_rms=0.0,
                cycle_id="test-cycle",
                watchdog=None,
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
        # exc_info=True must be attached so the traceback is diagnosable.
        assert debug_logs[0].exc_info is not None, (
            "The DEBUG log for sentinel_unlink failure must carry exc_info=True so operators can see the traceback."
        )
        # The traceback should reference the simulated OSError.
        assert debug_logs[0].exc_info[0] is OSError, (
            "The logged exception should be the OSError raised by "
            f"_sentinel.unlink(); got {debug_logs[0].exc_info[0]!r}."
        )


# ─── Busy_event clear failure is logged at DEBUG ───────────────────────


class TestBusyEventClearFailureLogged:
    """When ``_busy_event.set()`` raises in the finally block
    (e.g. the app was torn down mid-cycle and the Event object is in a
    half-destroyed state), a DEBUG log line must be emitted.

    Pre-fix: the silent ``contextlib.suppress(Exception)`` meant the
    app stayed stuck in BUSY forever — the watchdog would eventually
    force-recover, but with no log entry explaining WHY the busy_event
    never cleared. Post-fix: the failure is logged at DEBUG.
    """

    def test_busy_event_set_failure_emits_debug_log(self, caplog):
        """``_busy_event.set()`` raising RuntimeError → DEBUG log."""

        app = _TestApp()
        _configure_recording_for_finally(app)
        # Force the busy_event clear step (line ~723) to raise.
        app._busy_event.set.side_effect = RuntimeError("simulated torn-down Event")
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
                watchdog=None,
            )

        debug_logs = [
            r
            for r in caplog.records
            if r.levelno == logging.DEBUG and "finally cleanup step busy_event_clear failed" in r.getMessage()
        ]
        assert debug_logs, (
            "When _busy_event.set() raises in the finally block, "
            "a DEBUG log line with 'finally cleanup step busy_event_clear "
            "failed' must be emitted (pre-fix this was silently swallowed)."
        )
        assert debug_logs[0].exc_info is not None, (
            "The DEBUG log for busy_event_clear failure must carry exc_info=True so operators can see the traceback."
        )


# ─── Gc.collect failure is logged at DEBUG ─────────────────────────────


class TestGcCollectFailureLogged:
    """When ``gc.collect(0)`` raises (extremely rare — e.g. a
    SIGINT during GC, or a broken gc module in a frozen build), a
    DEBUG log line must be emitted instead of silently swallowing.
    """

    def test_gc_collect_failure_emits_debug_log(self, caplog, monkeypatch):
        """``gc.collect(0)`` raising RuntimeError → DEBUG log."""

        # Patch the ``gc`` module so ``import gc; gc.collect(0)`` raises.
        # The import is lazy (inside the try block), so patching
        # ``sys.modules["gc"]`` is sufficient.
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
                    watchdog=None,
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


# ─── Finally block does NOT raise (preserves original exception) ───────


class TestFinallyBlockDoesNotRaise:
    """Non-regression: the finally block must NOT raise, even
    when a cleanup step fails. The original exception from the ``run()``
    body (or the normal success path) must be preserved.

    Pre-fix: ``contextlib.suppress(Exception)`` guaranteed this. The
    replacement ``try/except Exception: log.debug(...)`` must preserve
    the same contract — a finally block that raises would mask the
    original exception from the body's ``except Exception`` handler,
    which is exactly the bug the ``contextlib.suppress`` was guarding
    against.
    """

    def test_finally_does_not_raise_when_sentinel_unlink_fails(self, monkeypatch):
        """A sentinel unlink failure must NOT propagate out of
        ``run()`` — the finally block catches it and logs at DEBUG.
        """

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

        # ``run()`` should complete WITHOUT raising — the body's
        # ``except Exception`` handler catches the body failure, and
        # the finally block's new try/except catches the sentinel
        # unlink failure. No exception should propagate.
        # We use a flag to detect if anything propagated.
        propagated: list[BaseException] = []
        try:
            pipeline.run(
                audio=None,
                duration=0.0,
                recorded_rms=0.0,
                cycle_id="test-cycle",
                watchdog=None,
            )
        except BaseException as e:  # noqa: BLE001 — we WANT to catch everything
            propagated.append(e)

        assert not propagated, (
            "Non-regression: the finally block must NOT raise when a "
            "cleanup step fails — the original exception path must be "
            f"preserved. Got propagated exception: {propagated!r}"
        )

    def test_finally_does_not_raise_when_busy_event_set_fails(self):
        """A busy_event clear failure must NOT propagate out of
        ``run()`` — the finally block catches it and logs at DEBUG.
        """

        app = _TestApp()
        _configure_recording_for_finally(app)
        app._busy_event.set.side_effect = RuntimeError("simulated Event failure")
        pipeline = _new_pipeline(app)

        propagated: list[BaseException] = []
        try:
            pipeline.run(
                audio=None,
                duration=0.0,
                recorded_rms=0.0,
                cycle_id="test-cycle",
                watchdog=None,
            )
        except BaseException as e:  # noqa: BLE001
            propagated.append(e)

        assert not propagated, (
            "Non-regression: the finally block must NOT raise when "
            "_busy_event.set() fails — the original exception path must be "
            f"preserved. Got propagated exception: {propagated!r}"
        )
