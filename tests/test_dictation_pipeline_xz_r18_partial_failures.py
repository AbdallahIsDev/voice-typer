"""XZ-R18-02 / XZ-R18-05: regression tests for partial-failure handling
in the dictation pipeline.

Background
----------
Two related findings from the XZ review:

* **XZ-R18-02 (Medium)** — ``_clean_text()`` and ``_apply_punctuation()``
  were the only two middle-pipeline steps NOT wrapped in try/except.
  If either threw, the exception propagated to the outer ``run()``
  ``except Exception`` block — the tray flipped to ERROR, the
  dictation was aborted, and the transcription was NEVER saved to
  crash recovery because ``_store_result()`` runs AFTER these steps.
  Fix: wrap in try/except matching the ``_apply_vocabulary`` pattern
  (``log.warning(...)`` + notify-once + return original text).

* **XZ-R18-05 (Medium)** — ``_apply_llm_polish``'s except block only
  logged a WARNING. The user paid for an LLM API call that never
  produced output (or believed the feature was broken) with NO
  diagnostic. Fix: add the notify-once pattern (tray notification on
  the FIRST failure per session) AND publish a ``llm_polish_failed``
  event to the in-process event bus so the renderer can surface a
  one-time toast.

The tests pin both fixes.
"""

from __future__ import annotations

import logging
from unittest.mock import MagicMock

import pytest
from voice_typer.server.dictation_pipeline import DictationPipeline

# ─── Helpers ────────────────────────────────────────────────────────────


class _TestApp:
    """Minimal non-magic test app for DictationPipeline tests.

    Mirrors the ``_TestApp`` in ``test_dictation_pipeline_review_fixes.py``.
    The four (now six, post-XZ-R18-02/05) notify-once flags are
    intentionally NOT pre-declared so ``getattr(app, "_flag", False)``
    defaults to ``False`` (matching ``VoiceTyperApp``'s behavior).
    """

    _NOTIFY_ONCE_FLAGS = {
        "_vocab_fail_notified",
        "_template_fail_notified",
        "_history_fail_notified",
        "_crash_recovery_fail_notified",
        # XZ-R18-02 / XZ-R18-05: three new session-scoped flags.
        "_clean_text_fail_notified",
        "_punct_fail_notified",
        "_llm_polish_fail_notified",
    }

    def __init__(self) -> None:
        self.tray = MagicMock()
        self.tray.notify = MagicMock()
        self.config = MagicMock()
        # Sensible defaults for the pipeline's middle-stage reads.
        self.config.text_cleanup_enabled = True
        self.config.vocabulary_enabled = True
        self.config.auto_punctuation = True
        self.config.llm_polish = False  # default OFF — per-test opt-in
        self.config.llm_api_key = ""
        self.config.llm_polish_consent = False
        self.config.crash_recovery_enabled = False
        self.config.templates_enabled = False  # off — not under test
        self.config.log_transcriptions = False
        self.config.model_size = "tiny.en"
        self.config.device = "cpu"
        self.history_db = MagicMock()
        self._vocabulary_manager: object = None
        self._template_manager: object = None
        self._llm_polisher: object = None
        self._crash_recovery = MagicMock()
        self._last_transcription: object = None
        self.models = MagicMock()
        self.recording = MagicMock()

    def __getattr__(self, name: str) -> MagicMock:
        # Re-raise AttributeError for the notify-once flag names so
        # getattr-with-default in the production code returns False.
        if name in self._NOTIFY_ONCE_FLAGS:
            raise AttributeError(name)
        mock = MagicMock()
        object.__setattr__(self, name, mock)
        return mock


def _make_app() -> _TestApp:
    return _TestApp()


def _new_pipeline(app: _TestApp) -> DictationPipeline:
    pipeline = DictationPipeline.__new__(DictationPipeline)
    pipeline._app = app
    pipeline._duration = 1.0
    pipeline._cycle_id = "test-cycle"
    pipeline._audio = None
    pipeline._audio_stats = None
    pipeline._recorded_rms = 0.0
    pipeline._device_info = ""
    pipeline._watchdog = None
    pipeline._templates_applied = False
    return pipeline


# ─── XZ-R18-02: _clean_text try/except ─────────────────────────────────


class TestCleanTextWrappedInTryExcept:
    """XZ-R18-02: ``_clean_text`` must NOT propagate exceptions to the
    outer ``run()`` block. On failure, log a WARNING, fire the
    notify-once tray notification, and return the original text so the
    dictation completes (the user sees their un-cleaned transcription
    instead of a tray error)."""

    def test_returns_original_text_on_clean_failure(self, monkeypatch: pytest.MonkeyPatch) -> None:
        app = _make_app()
        pipeline = _new_pipeline(app)

        def _boom(_text: str, **_kwargs: object) -> str:
            raise RuntimeError("clean_transcribed_text exploded")

        monkeypatch.setattr("voice_typer.server.text_cleanup.clean_transcribed_text", _boom)
        original = "hello world"
        result = pipeline._clean_text(original)
        assert result == original, f"XZ-R18-02: _clean_text must return the original text on failure, got {result!r}"

    def test_logs_warning_on_clean_failure(self, monkeypatch: pytest.MonkeyPatch, caplog) -> None:
        app = _make_app()
        pipeline = _new_pipeline(app)

        def _boom(_text: str, **_kwargs: object) -> str:
            raise RuntimeError("boom")

        monkeypatch.setattr("voice_typer.server.text_cleanup.clean_transcribed_text", _boom)
        with caplog.at_level(logging.WARNING, logger="voice_typer.server.dictation_pipeline"):
            pipeline._clean_text("hello")
        assert any("Text cleanup failed" in r.getMessage() for r in caplog.records), (
            "XZ-R18-02: _clean_text must log a WARNING on failure"
        )

    def test_notify_once_fires_only_on_first_failure(self, monkeypatch: pytest.MonkeyPatch) -> None:
        app = _make_app()
        pipeline1 = _new_pipeline(app)
        pipeline2 = _new_pipeline(app)

        def _boom(_text: str, **_kwargs: object) -> str:
            raise RuntimeError("boom")

        monkeypatch.setattr("voice_typer.server.text_cleanup.clean_transcribed_text", _boom)
        pipeline1._clean_text("hello")
        pipeline2._clean_text("world")
        # The tray.notify call count for the cleanup-failed message
        # must be exactly 1 (only the first cycle fires).
        notify_calls = [c for c in app.tray.notify.call_args_list if c.args and "Text cleanup failed" in str(c.args)]
        assert len(notify_calls) == 1, (
            "XZ-R18-02: tray.notify('Text cleanup failed') must fire EXACTLY "
            f"once across two consecutive failures (session-scoped flag); got {len(notify_calls)}"
        )

    def test_disabled_cleanup_does_not_call_clean_function(self, monkeypatch: pytest.MonkeyPatch) -> None:
        app = _make_app()
        app.config.text_cleanup_enabled = False
        pipeline = _new_pipeline(app)

        called = []

        def _should_not_be_called(_text: str, **_kwargs: object) -> str:
            called.append(True)
            return "should not reach"

        monkeypatch.setattr("voice_typer.server.text_cleanup.clean_transcribed_text", _should_not_be_called)
        result = pipeline._clean_text("hello world")
        assert result == "hello world"
        assert not called, "clean_transcribed_text must NOT be called when text_cleanup_enabled=False"


# ─── XZ-R18-02: _apply_punctuation try/except ──────────────────────────


class TestApplyPunctuationWrappedInTryExcept:
    """XZ-R18-02: ``_apply_punctuation`` must NOT propagate exceptions
    to the outer ``run()`` block. On failure, log a WARNING, fire the
    notify-once tray notification, and return the original text."""

    def test_returns_original_text_on_punctuation_failure(self, monkeypatch: pytest.MonkeyPatch) -> None:
        app = _make_app()
        pipeline = _new_pipeline(app)

        def _boom(_text: str) -> str:
            raise RuntimeError("punctuation boom")

        monkeypatch.setattr("voice_typer.server.text_cleanup._add_safe_terminal_punctuation", _boom)
        original = "hello world"
        result = pipeline._apply_punctuation(original)
        assert result == original, "XZ-R18-02: _apply_punctuation must return the original text on failure"

    def test_logs_warning_on_punctuation_failure(self, monkeypatch: pytest.MonkeyPatch, caplog) -> None:
        app = _make_app()
        pipeline = _new_pipeline(app)

        def _boom(_text: str) -> str:
            raise RuntimeError("boom")

        monkeypatch.setattr("voice_typer.server.text_cleanup._add_safe_terminal_punctuation", _boom)
        with caplog.at_level(logging.WARNING, logger="voice_typer.server.dictation_pipeline"):
            pipeline._apply_punctuation("hello")
        assert any("Auto-punctuation failed" in r.getMessage() for r in caplog.records), (
            "XZ-R18-02: _apply_punctuation must log a WARNING on failure"
        )

    def test_notify_once_fires_only_on_first_failure(self, monkeypatch: pytest.MonkeyPatch) -> None:
        app = _make_app()
        pipeline1 = _new_pipeline(app)
        pipeline2 = _new_pipeline(app)

        def _boom(_text: str) -> str:
            raise RuntimeError("boom")

        monkeypatch.setattr("voice_typer.server.text_cleanup._add_safe_terminal_punctuation", _boom)
        pipeline1._apply_punctuation("hello")
        pipeline2._apply_punctuation("world")
        notify_calls = [
            c for c in app.tray.notify.call_args_list if c.args and "Auto-punctuation failed" in str(c.args)
        ]
        assert len(notify_calls) == 1, "XZ-R18-02: tray.notify('Auto-punctuation failed') must fire EXACTLY once"

    def test_disabled_punctuation_does_not_call_helper(self, monkeypatch: pytest.MonkeyPatch) -> None:
        app = _make_app()
        app.config.auto_punctuation = False
        pipeline = _new_pipeline(app)

        called = []

        def _should_not_be_called(_text: str) -> str:
            called.append(True)
            return "should not reach"

        monkeypatch.setattr("voice_typer.server.text_cleanup._add_safe_terminal_punctuation", _should_not_be_called)
        result = pipeline._apply_punctuation("hello world")
        assert result == "hello world"
        assert not called


# ─── XZ-R18-05: _apply_llm_polish notify-once + event publish ──────────


class TestApplyLlmPolishNotifyOnceAndEventPublish:
    """XZ-R18-05: ``_apply_llm_polish``'s except block must (1) log a
    WARNING with the redacted exception, (2) fire a notify-once tray
    notification, and (3) publish a ``llm_polish_failed`` event to the
    in-process event bus so the renderer can surface a one-time
    toast. The transcription is still returned UN-polished."""

    def _make_llm_polish_pipeline(self, app: _TestApp) -> DictationPipeline:
        """Build a pipeline whose config enables LLM polish and whose
        ``_llm_polisher`` is a MagicMock that raises on ``polish()``."""
        app.config.llm_polish = True
        app.config.llm_api_key = "sk-" + "a" * 40
        app.config.openai_api_key = ""
        app.config.llm_polish_consent = True
        app.config.llm_api_url = "https://api.openai.com/v1/chat/completions"
        app.config.llm_model = "gpt-4o-mini"
        app.config.llm_preset = "professional"
        polisher = MagicMock()
        polisher.polish.side_effect = RuntimeError("LLM API 500")
        app._llm_polisher = polisher
        return _new_pipeline(app)

    def test_returns_unpolished_text_on_failure(self) -> None:
        app = _make_app()
        pipeline = self._make_llm_polish_pipeline(app)
        original = "hello world"
        result = pipeline._apply_llm_polish(original)
        assert result == original, "XZ-R18-05: _apply_llm_polish must return the original (un-polished) text on failure"

    def test_logs_warning_on_failure(self, caplog) -> None:
        app = _make_app()
        pipeline = self._make_llm_polish_pipeline(app)
        with caplog.at_level(logging.WARNING, logger="voice_typer.server.dictation_pipeline"):
            pipeline._apply_llm_polish("hello")
        assert any("Polish failed" in r.getMessage() for r in caplog.records), (
            "XZ-R18-05: must log WARNING with 'Polish failed' message"
        )

    def test_publishes_llm_polish_failed_event(self, monkeypatch: pytest.MonkeyPatch) -> None:
        app = _make_app()
        pipeline = self._make_llm_polish_pipeline(app)

        published_events: list[dict] = []

        def _capture_publish(event: dict) -> None:
            published_events.append(event)

        # The production code does ``from voice_typer.server import event_bus``
        # then ``event_bus.publish({"type": "llm_polish_failed"})``. Patch
        # the publish function on the event_bus module.
        import voice_typer.server.event_bus as event_bus_mod

        monkeypatch.setattr(event_bus_mod, "publish", _capture_publish)
        pipeline._apply_llm_polish("hello")
        assert any(e.get("type") == "llm_polish_failed" for e in published_events), (
            "XZ-R18-05: must publish a {'type': 'llm_polish_failed'} event to "
            f"the event bus. Published: {published_events}"
        )

    def test_notify_once_fires_only_on_first_failure(self) -> None:
        app = _make_app()
        pipeline1 = self._make_llm_polish_pipeline(app)
        pipeline2 = self._make_llm_polish_pipeline(app)
        pipeline1._apply_llm_polish("hello")
        pipeline2._apply_llm_polish("world")
        notify_calls = [c for c in app.tray.notify.call_args_list if c.args and "LLM polish failed" in str(c.args)]
        assert len(notify_calls) == 1, (
            "XZ-R18-05: tray.notify('LLM polish failed') must fire EXACTLY "
            f"once across two consecutive failures; got {len(notify_calls)}"
        )

    def test_event_published_on_every_failure_not_just_first(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The notify-once flag gates ONLY the tray notification (so the
        user isn't spammed). The event-bus publish must fire on every
        failure so the renderer can decide independently whether to
        surface a toast (e.g. suppress its own toast after the first
        one)."""
        app = _make_app()
        pipeline1 = self._make_llm_polish_pipeline(app)
        pipeline2 = self._make_llm_polish_pipeline(app)

        published_events: list[dict] = []
        import voice_typer.server.event_bus as event_bus_mod

        monkeypatch.setattr(event_bus_mod, "publish", lambda e: published_events.append(e))
        pipeline1._apply_llm_polish("hello")
        pipeline2._apply_llm_polish("world")
        llm_fail_events = [e for e in published_events if e.get("type") == "llm_polish_failed"]
        assert len(llm_fail_events) == 2, (
            "XZ-R18-05: event_bus.publish must fire on EVERY polish failure "
            f"(the renderer throttles toasts; the server must not pre-filter). "
            f"Got {len(llm_fail_events)} events."
        )


# ─── XZ-R18-05: event publish failure is swallowed ─────────────────────


class TestApplyLlmPolishEventBusFailureIsSwallowed:
    """If ``event_bus.publish`` raises (e.g. the bus is shutting down
    or the queue is full), the polish-failure path must NOT propagate
    the exception — the original text is still returned to the user
    and the tray notification (which has its own suppress(Exception)
    guard) is the user-visible signal."""

    def test_event_bus_publish_failure_does_not_propagate(self, monkeypatch: pytest.MonkeyPatch) -> None:
        app = _make_app()
        app.config.llm_polish = True
        app.config.llm_api_key = "sk-" + "a" * 40
        app.config.llm_polish_consent = True
        app.config.llm_api_url = "https://api.openai.com/v1/chat/completions"
        app.config.llm_model = "gpt-4o-mini"
        app.config.llm_preset = "professional"
        polisher = MagicMock()
        polisher.polish.side_effect = RuntimeError("LLM API 500")
        app._llm_polisher = polisher
        pipeline = _new_pipeline(app)

        def _boom_publish(_event: dict) -> None:
            raise RuntimeError("event bus broken")

        import voice_typer.server.event_bus as event_bus_mod

        monkeypatch.setattr(event_bus_mod, "publish", _boom_publish)
        # Must NOT raise — the publish is wrapped in contextlib.suppress(Exception).
        result = pipeline._apply_llm_polish("hello world")
        assert result == "hello world"
