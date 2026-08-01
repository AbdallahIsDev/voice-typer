"""Tests for the a-review R8 fixes in ``dictation_pipeline.py``.

Covers:

* a-review Finding 2 (HIGH) — notify-once deduplication flags
  (``_vocab_fail_notified``, ``_template_fail_notified``,
  ``_history_fail_notified``, ``_crash_recovery_fail_notified``)
  were stored on ``DictationPipeline`` (cycle-scoped — a fresh
  pipeline is constructed per transcription cycle), so the user
  got a tray notification on EVERY cycle where the failure
  occurred. The fix moves the flags to ``self._app`` (session-
  scoped). These tests verify the notify-once semantics hold
  across two consecutive pipelines sharing the same ``_app``.

* a-review Finding 8 (MEDIUM) — ``_transcribe`` had a broad
  ``try/except TypeError`` to handle backends that lacked the
  ``audio_stats`` kwarg. The catch was too broad: a TypeError
  inside the function body (``None.lower()``, bad indexing,
  etc.) was also caught, masking real bugs. The fix adds
  ``audio_stats=None`` to ``CloudEngine.transcribe_with_fallback``
  (the only backend that lacked it) and removes the broad catch.
  These tests verify all four backends accept ``audio_stats`` and
  that a real TypeError from the engine body propagates.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import numpy as np
import pytest
from voice_typer.server.dictation_pipeline import DictationPipeline

# ─── Helpers ─────────────────────────────────────────────────────────────


class _TestApp:
    """Minimal non-magic test app for DictationPipeline tests.

    Why a custom class instead of ``MagicMock``? MagicMock auto-creates
    a child mock for ANY attribute access, so ``getattr(app, "_flag",
    False)`` returns a truthy MagicMock rather than the ``False``
    default. The production code relies on the default —
    ``VoiceTyperApp`` does NOT pre-create the four notify-once flag
    attributes, so ``getattr(self._app, "_vocab_fail_notified",
    False)`` correctly defaults to ``False`` on a fresh app.

    Using this class lets the tests exercise that default-False
    semantics faithfully, and also lets us verify that the flag is
    set on the app (not the pipeline) after the first failure.
    """

    def __init__(self) -> None:
        # Attributes the pipeline reads — typed as MagicMock so we
        # can assert on call_args_list etc.
        self.tray = MagicMock()
        self.tray.notify = MagicMock()
        self.config = MagicMock()
        self.config.crash_recovery_enabled = False
        self.config.templates_enabled = True
        self.config.log_transcriptions = False
        self.config.model_size = "tiny.en"
        self.config.device = "cpu"
        self.history_db = MagicMock()
        self._vocabulary_manager: object = None
        self._template_manager: object = None
        self._crash_recovery = MagicMock()
        self._last_transcription: object = None
        self.models = MagicMock()
        self.recording = MagicMock()
        # NOTE: the four notify-once flags are intentionally NOT
        # pre-declared — production code relies on getattr-default.

    # The remaining attributes the pipeline touches in the success
    # path (event_bus publish, etc.) are MagicMock-accessed via
    # __getattr__ fallback to keep this class small. We delegate
    # unknown attribute access to a per-instance MagicMock.
    def __getattr__(self, name: str) -> MagicMock:
        # Only called when the attribute is genuinely absent (i.e.
        # not declared in __init__). We do NOT want this for the
        # four notify-once flag names — they must default to False
        # via getattr-with-default, which requires AttributeError to
        # be raised when absent. So we re-raise AttributeError for
        # any name matching the flag pattern.
        if name in {
            "_vocab_fail_notified",
            "_template_fail_notified",
            "_history_fail_notified",
            "_crash_recovery_fail_notified",
        }:
            raise AttributeError(name)
        # For other attributes, return a fresh MagicMock (auto-mock
        # behavior, like MagicMock itself).
        mock = MagicMock()
        # Cache it so subsequent accesses return the same mock.
        object.__setattr__(self, name, mock)
        return mock


def _make_app() -> _TestApp:
    """Build a minimal test app for DictationPipeline.

    Unlike a bare ``MagicMock()``, this class does NOT auto-create
    the four notify-once flag attributes — so
    ``getattr(app, "_flag", False)`` correctly defaults to ``False``
    when the flag has never been set (mirroring production behavior
    on ``VoiceTyperApp``).
    """
    return _TestApp()


def _new_pipeline(app: _TestApp) -> DictationPipeline:
    """Build a fresh DictationPipeline tied to ``app``.

    Mirrors how ``RecordingController._stop_dictation`` constructs a
    new pipeline per transcription cycle (a-review Finding 2 root
    cause).
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
    return pipeline


# ─── B1: notify-once flags survive across pipeline instances ────────────


class TestNotifyOnceFlagsAreSessionScoped:
    """a-review Finding 2: notify-once flags live on ``self._app``.

    A fresh ``DictationPipeline`` is built per transcription cycle
    (``recording_controller.py:481``). If the flags lived on the
    pipeline, they reset every cycle and the user got a tray
    notification on EVERY cycle where the failure occurred. The fix
    moves them to ``self._app`` so they survive for the app's
    lifetime.

    Each test constructs two consecutive pipelines sharing the same
    app, triggers the same failure on both, and asserts only the
    first pipeline fires a tray notification.
    """

    def _count_notify_calls_with(self, app: _TestApp, needle: str) -> int:
        return sum(1 for c in app.tray.notify.call_args_list if needle.lower() in str(c.args).lower())

    def test_vocab_fail_notifies_only_on_first_cycle(self):
        app = _make_app()
        app._vocabulary_manager = MagicMock()
        app._vocabulary_manager.apply_to_text.side_effect = RuntimeError("vocab boom")
        # Flag is absent on app initially — first pipeline must
        # default to "not yet notified" and fire the tray notify.

        pipeline1 = _new_pipeline(app)
        pipeline1._apply_vocabulary("hello world")

        pipeline2 = _new_pipeline(app)
        pipeline2._apply_vocabulary("hello world")

        assert self._count_notify_calls_with(app, "Vocabulary") == 1, (
            "Vocabulary failure should notify exactly once across two "
            "consecutive pipelines sharing the same _app (a-review "
            "Finding 2). Got: "
            f"{[c.args for c in app.tray.notify.call_args_list]}"
        )
        # Flag must be True on the app after the first failure —
        # this is what suppresses the second notification.
        assert app._vocab_fail_notified is True

    def test_template_fail_notifies_only_on_first_cycle(self):
        app = _make_app()
        app._template_manager = MagicMock()
        app._template_manager.match.side_effect = RuntimeError("template boom")

        pipeline1 = _new_pipeline(app)
        pipeline1._apply_templates("hello world")

        pipeline2 = _new_pipeline(app)
        pipeline2._apply_templates("hello world")

        assert self._count_notify_calls_with(app, "Template") == 1, (
            "Template failure should notify exactly once across two "
            "consecutive pipelines sharing the same _app (a-review "
            "Finding 2)."
        )
        assert app._template_fail_notified is True

    def test_history_fail_notifies_only_on_first_cycle(self):
        app = _make_app()
        app.history_db.add_transcription.side_effect = RuntimeError("DB locked")

        pipeline1 = _new_pipeline(app)
        pipeline1._store_result("hello world")

        pipeline2 = _new_pipeline(app)
        pipeline2._store_result("hello world")

        assert self._count_notify_calls_with(app, "history") == 1, (
            "History DB failure should notify exactly once across two "
            "consecutive pipelines sharing the same _app (a-review "
            "Finding 2)."
        )
        assert app._history_fail_notified is True

    def test_crash_recovery_fail_notifies_only_on_first_cycle(self):
        app = _make_app()
        app.config.crash_recovery_enabled = True
        app._crash_recovery = MagicMock()
        app._crash_recovery.add.side_effect = RuntimeError("crash boom")

        pipeline1 = _new_pipeline(app)
        pipeline1._store_result("hello world")

        pipeline2 = _new_pipeline(app)
        pipeline2._store_result("hello world")

        assert self._count_notify_calls_with(app, "crash-recovery") == 1, (
            "Crash recovery failure should notify exactly once across "
            "two consecutive pipelines sharing the same _app (a-review "
            "Finding 2)."
        )
        assert app._crash_recovery_fail_notified is True


class TestNotifyOnceFlagsDefaultToFalseOnFreshApp:
    """a-review Finding 2: ``getattr(self._app, "_flag", False)`` must
    default to False on a fresh app so the first failure notifies.
    """

    def test_vocab_flag_defaults_false(self):
        app = _make_app()
        app._vocabulary_manager = MagicMock()
        app._vocabulary_manager.apply_to_text.side_effect = RuntimeError("vocab boom")
        # Deliberately do NOT seed app._vocab_fail_notified — verify
        # the production code's getattr-default-to-False semantics
        # work correctly on a non-MagicMock app object.

        pipeline = _new_pipeline(app)
        pipeline._apply_vocabulary("hello world")

        assert any("Vocabulary" in str(c.args) for c in app.tray.notify.call_args_list), (
            "First vocab failure must notify when flag is unset on app."
        )

    def test_history_flag_defaults_false(self):
        app = _make_app()
        app.history_db.add_transcription.side_effect = RuntimeError("DB locked")
        # Deliberately do NOT seed app._history_fail_notified.

        pipeline = _new_pipeline(app)
        pipeline._store_result("hello world")

        assert any("history" in str(c.args).lower() for c in app.tray.notify.call_args_list), (
            "First history failure must notify when flag is unset on app."
        )


class TestNotifyOnceFlagsAreNotOnPipeline:
    """a-review Finding 2 (regression guard): the flags must NOT be
    read or written on the pipeline instance — that's the bug we
    fixed. The original test inspected the pipeline source code for
    ``self._<flag>`` patterns, which is brittle: cosmetic refactor
    breaks the test on false positives while functional regressions
    via different patterns (e.g. ``getattr(self._app, "_flag")``
    swapped to ``getattr(self, "_flag")``) slip through.

    S2-CR-64: replaced the source-text scan with a parametrized
    behavioral test that triggers each of the 4 failures and asserts
    (a) the flag is set on the *app* (``hasattr(app, flag) is True``)
    and (b) the flag is absent on the *pipeline* (``hasattr(pipeline,
    flag) is False``). This catches the actual runtime invariant
    directly — no source-text introspection.
    """

    @pytest.mark.parametrize(
        "flag,trigger",
        [
            (
                "_vocab_fail_notified",
                lambda app, pipeline: pipeline._apply_vocabulary("hello world"),
            ),
            (
                "_template_fail_notified",
                lambda app, pipeline: pipeline._apply_templates("hello world"),
            ),
            (
                "_history_fail_notified",
                lambda app, pipeline: pipeline._store_result("hello world"),
            ),
            (
                "_crash_recovery_fail_notified",
                lambda app, pipeline: pipeline._store_result("hello world"),
            ),
        ],
    )
    def test_flag_lives_on_app_not_pipeline(self, flag: str, trigger):
        """After the failure fires, the flag must be set on ``app``
        and absent on ``pipeline`` — i.e. the cycle-scoped pipeline
        does NOT carry the notify-once state. This catches a
        regression that re-introduces ``self._<flag>`` on the
        pipeline directly via the runtime invariant, regardless of
        how the source code is structured.
        """
        app = _make_app()
        # Configure the failure trigger for each flag.
        if flag == "_vocab_fail_notified":
            app._vocabulary_manager = MagicMock()
            app._vocabulary_manager.apply_to_text.side_effect = RuntimeError("vocab boom")
        elif flag == "_template_fail_notified":
            app._template_manager = MagicMock()
            app._template_manager.match.side_effect = RuntimeError("template boom")
        elif flag == "_history_fail_notified":
            app.history_db.add_transcription.side_effect = RuntimeError("DB locked")
        elif flag == "_crash_recovery_fail_notified":
            app.config.crash_recovery_enabled = True
            app._crash_recovery = MagicMock()
            app._crash_recovery.add.side_effect = RuntimeError("crash boom")

        pipeline = _new_pipeline(app)

        # Before the failure fires, neither app nor pipeline carries
        # the flag (default-False via getattr-with-default in
        # production code).
        assert not hasattr(pipeline, flag), (
            f"Pipeline should not carry {flag} before failure — the flag "
            f"belongs on the session-scoped app, not the cycle-scoped "
            f"pipeline (a-review Finding 2)."
        )

        # Fire the failure.
        trigger(app, pipeline)

        # (a) The flag must now be set on the *app* (the bug fix
        # stores it there so it survives across pipeline cycles).
        assert hasattr(app, flag) is True, (
            f"After triggering the failure, app must carry {flag} — "
            f"the notify-once flag must live on the session-scoped app "
            f"so it survives across pipeline cycles (a-review Finding 2)."
        )
        assert getattr(app, flag) is True, (
            f"app.{flag} must be True after the first failure — this is what suppresses subsequent notifications."
        )
        # (b) The flag must NOT be set on the *pipeline* — that was
        # the original bug (cycle-scoped flag reset every cycle).
        assert not hasattr(pipeline, flag), (
            f"Pipeline must NOT carry {flag} — the notify-once flag "
            f"lives on the session-scoped app, not the cycle-scoped "
            f"pipeline. Storing it on the pipeline resets every cycle "
            f"and the user gets a tray notification on every failure "
            f"(a-review Finding 2 regression)."
        )


# ─── B2: TypeError fallback removed; all backends accept audio_stats ────


class TestAllBackendsAcceptAudioStatsKwarg:
    """a-review Finding 8: all four ASR backends must accept the
    ``audio_stats`` keyword argument on ``transcribe_with_fallback``.

    Pre-fix, only the three local engines (Whisper/Parakeet/Qwen)
    accepted it; ``CloudEngine.transcribe_with_fallback`` did not,
    which forced ``DictationPipeline._transcribe`` to wrap the call
    in a broad ``try/except TypeError`` fallback. The fix adds the
    parameter to CloudEngine (default None, ignored) so the broad
    catch can be removed.
    """

    def test_cloud_engine_transcribe_with_fallback_accepts_audio_stats(self):
        import inspect

        from voice_typer.server.cloud_engines import CloudEngine

        sig = inspect.signature(CloudEngine.transcribe_with_fallback)
        assert "audio_stats" in sig.parameters, (
            "CloudEngine.transcribe_with_fallback must accept audio_stats (a-review Finding 8)."
        )
        assert sig.parameters["audio_stats"].default is None, (
            "audio_stats on CloudEngine.transcribe_with_fallback must default to None for backwards compatibility."
        )

    def test_whisper_transcribe_with_fallback_accepts_audio_stats(self):
        import inspect

        from voice_typer.server.transcription import TranscriptionEngine

        sig = inspect.signature(TranscriptionEngine.transcribe_with_fallback)
        assert "audio_stats" in sig.parameters

    def test_parakeet_transcribe_with_fallback_accepts_audio_stats(self):
        import inspect

        from voice_typer.server.parakeet_engine import ParakeetEngine

        sig = inspect.signature(ParakeetEngine.transcribe_with_fallback)
        assert "audio_stats" in sig.parameters

    def test_qwen_transcribe_with_fallback_accepts_audio_stats(self):
        import inspect

        from voice_typer.server.qwen_engine import QwenEngine

        sig = inspect.signature(QwenEngine.transcribe_with_fallback)
        assert "audio_stats" in sig.parameters


class TestTranscribeNoBroadTypeErrorCatch:
    """a-review Finding 8: ``DictationPipeline._transcribe`` must NOT
    wrap the ``transcribe_with_fallback`` call in a broad
    ``try/except TypeError``. A TypeError raised inside the engine
    body (e.g. ``None.lower()``, bad indexing) must propagate so the
    real bug surfaces in the log/traceback instead of being masked
    by a retry that fails the same way.

    S2-CR-64: the original source-text scan
    (``"except TypeError:" not in inspect.getsource(...)``) was
    brittle — a cosmetic refactor (e.g. catching ``TypeError`` as
    ``Exception`` subclass, or extracting the call into a helper)
    would break the test on false positives while functional
    regressions via different patterns (e.g. ``except Exception:``
    that still catches TypeError) would slip through. Removed in
    favor of the two behavioral tests below
    (``test_real_typeerror_propagates_from_engine`` and
    ``test_audio_stats_passed_through_to_engine``) which directly
    verify the runtime invariant: TypeError propagates and
    audio_stats is forwarded.
    """

    def test_real_typeerror_propagates_from_engine(self):
        """A TypeError raised inside the engine body must propagate
        out of ``_transcribe`` (not be swallowed by a broad catch).

        We mock the active transcriber so its
        ``transcribe_with_fallback`` raises TypeError — simulating
        a real bug like ``None.lower()`` inside the engine. The
        pre-fix broad catch would have retried and re-raised the
        same TypeError, producing a confusing trace. Post-fix, the
        original TypeError propagates directly.

        UE-10 sibling: ``_transcribe`` now pops the streaming
        session via ``pop_streaming_session()`` (atomic) instead of
        the racy get+set pair, so we mock the pop (not the get)
        to force the batch path.
        """
        app = _make_app()
        # No streaming session — forces the ``else`` branch which
        # calls active.transcribe_with_fallback.
        app.recording.pop_streaming_session.return_value = None

        active = MagicMock()
        sentinel = TypeError("simulated None.lower() bug")
        active.transcribe_with_fallback.side_effect = sentinel
        active.device_info = "mock"
        app.models.active_transcriber.return_value = active

        pipeline = _new_pipeline(app)
        with pytest.raises(TypeError, match="simulated None.lower"):
            pipeline._transcribe()

        # The engine must have been called exactly once — no retry.
        assert active.transcribe_with_fallback.call_count == 1, (
            "DictationPipeline._transcribe must not retry on "
            "TypeError (a-review Finding 8). Got call_count="
            f"{active.transcribe_with_fallback.call_count}."
        )
        # And the retry must have passed audio_stats (the new code).
        _, kwargs = active.transcribe_with_fallback.call_args
        assert "audio_stats" in kwargs

    def test_audio_stats_passed_through_to_engine(self):
        """The audio_stats tuple captured from the recorder must be
        forwarded to the engine's transcribe_with_fallback.

        UE-10 sibling: ``_transcribe`` now pops the streaming
        session via ``pop_streaming_session()`` (atomic) instead of
        the racy get+set pair, so we mock the pop (not the get)
        to force the batch path.
        """
        app = _make_app()
        app.recording.pop_streaming_session.return_value = None

        active = MagicMock()
        active.transcribe_with_fallback.return_value = "hello"
        active.device_info = "mock"
        app.models.active_transcriber.return_value = active

        pipeline = _new_pipeline(app)
        pipeline._audio_stats = (0.123, 0.456, 25.0)
        result = pipeline._transcribe()

        assert result == "hello"
        _, kwargs = active.transcribe_with_fallback.call_args
        assert kwargs.get("audio_stats") == (0.123, 0.456, 25.0), (
            "_transcribe must forward the pre-computed audio_stats tuple to transcribe_with_fallback."
        )


class TestCloudEngineIgnoresAudioStats:
    """a-review Finding 8: when audio_stats is passed to
    ``CloudEngine.transcribe_with_fallback``, the value is ignored
    on the cloud path (cloud APIs don't use RMS/peak/silence) but
    forwarded to the local_engine fallback if one is provided.
    """

    def test_cloud_path_ignores_audio_stats(self):
        from unittest.mock import patch

        from voice_typer.server.cloud_engines import CloudEngine

        engine = CloudEngine(provider="openai", api_key="test-key", consent_given=True)
        audio = np.zeros(16000, dtype=np.float32)
        with patch.object(engine, "_send_request", return_value="cloud text"):
            result = engine.transcribe_with_fallback(audio, audio_stats=(0.1, 0.5, 50.0))
        assert result == "cloud text"

    def test_local_fallback_forwards_audio_stats(self):
        """When the cloud fails and a local_engine is provided,
        ``audio_stats`` must be forwarded to the local engine's
        ``transcribe`` call.
        """
        from unittest.mock import patch

        from voice_typer.server.cloud_engines import CloudEngine

        engine = CloudEngine(provider="openai", api_key="test-key", consent_given=True)
        audio = np.zeros(16000, dtype=np.float32)

        # Force the cloud path to fail.
        with patch.object(engine, "transcribe", side_effect=RuntimeError("cloud down")):
            local_engine = MagicMock()
            local_engine.transcribe.return_value = "local text"
            result = engine.transcribe_with_fallback(
                audio,
                local_engine=local_engine,
                audio_stats=(0.7, 0.9, 10.0),
            )

        assert result == "local text"
        local_engine.transcribe.assert_called_once_with(audio, audio_stats=(0.7, 0.9, 10.0))

    def test_no_local_engine_still_works_without_audio_stats(self):
        """Backwards compat: calling without audio_stats must still
        work (existing callers like test_cloud_engines.py depend on it).
        """
        from unittest.mock import patch

        from voice_typer.server.cloud_engines import CloudEngine

        engine = CloudEngine(provider="openai", api_key="test-key", consent_given=True)
        audio = np.zeros(16000, dtype=np.float32)
        with patch.object(engine, "_send_request", return_value="text"):
            result = engine.transcribe_with_fallback(audio)
        assert result == "text"


# _timed_stage context manager ─────────────────────────────────


class TestTimedStageContextManager:
    """ZR-64: ``_timed_stage`` replaces the 10 duplicated
    ``_stage_t0 = time.perf_counter()`` / ``_<name>_ms = (...) * 1000``
    blocks in ``DictationPipeline.run`` with a single DRY primitive.

    These tests pin the contract: writes to the supplied dict,
    records a positive duration, preserves exception propagation
    (with timing still recorded up to the raise), and supports
    nested use across multiple stages.
    """

    def test_records_positive_duration_in_dict(self) -> None:
        import time

        from voice_typer.server.dictation_pipeline import _timed_stage

        timings: dict[str, float] = {}
        with _timed_stage(timings, "transcribe"):
            time.sleep(0.005)
        assert "transcribe" in timings
        # 5 ms sleep — allow generous lower bound (CPU contention)
        # and an upper bound that catches "forgot to subtract t0" bugs.
        assert 1.0 < timings["transcribe"] < 1000.0

    def test_exception_propagates_and_timing_still_recorded(self) -> None:
        # the ``finally`` clause runs before the exception
        # propagates so a ``[PIPE-PERF]`` log emitted from the
        # ``except`` block in ``run()`` has a best-effort timing for
        # the stage that failed.
        from voice_typer.server.dictation_pipeline import _timed_stage

        timings: dict[str, float] = {}
        # Combine into a single ``with`` so ruff SIM117 doesn't flag the
        # nested-context pattern (the test still asserts both that the
        # exception propagates AND that timing is recorded).
        with pytest.raises(RuntimeError, match="boom"), _timed_stage(timings, "store"):
            raise RuntimeError("boom")
        assert "store" in timings
        assert timings["store"] >= 0.0

    def test_multiple_stages_each_recorded(self) -> None:
        # Mirrors the actual usage in ``DictationPipeline.run``: a
        # single dict is reused across consecutive ``with`` blocks,
        # one entry per stage name.
        from voice_typer.server.dictation_pipeline import _timed_stage

        timings: dict[str, float] = {}
        with _timed_stage(timings, "clean"):
            pass
        with _timed_stage(timings, "vocab"):
            pass
        with _timed_stage(timings, "templates"):
            pass
        assert set(timings.keys()) == {"clean", "vocab", "templates"}
        # All three recorded with non-negative durations.
        assert all(v >= 0.0 for v in timings.values())
