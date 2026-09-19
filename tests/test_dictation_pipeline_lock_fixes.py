"""Tests for the H-17 and fixes in ``dictation_pipeline.py``."""

from __future__ import annotations

import contextlib
import logging
import threading
from unittest.mock import MagicMock

from voice_typer.server.dictation_pipeline import DictationPipeline


class _TestApp:
    """Minimal non-magic test app for DictationPipeline tests."""

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
        self.recording = MagicMock()
        # ``recorder`` is read by the finally block in run(), make
        self.recorder = MagicMock()
        self.recorder.recording = False
        self._busy_event = MagicMock()
        self._schedule_timer = MagicMock()
        self._waveform_bubble = MagicMock()
        # ``_lock`` is kept for back-compat with any test that still
        self._lock = MagicMock()
        self._lock.__enter__ = MagicMock(return_value=self._lock)
        self._lock.__exit__ = MagicMock(return_value=False)
        # NOTE: the four notify-once flags are intentionally NOT

    # Auto-mock unknown attributes (like MagicMock) but DO NOT
    def __getattr__(self, name: str) -> MagicMock:
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
    """Build a fresh DictationPipeline tied to ``app``."""
    pipeline = DictationPipeline.__new__(DictationPipeline)
    pipeline._app = app
    pipeline._duration = 1.0
    pipeline._cycle_id = "test-cycle"
    pipeline._audio = None
    pipeline._audio_stats = None
    pipeline._recorded_rms = 0.0
    pipeline._device_info = ""
    # ``_check_resources_throttled`` reads these, they're normally
    pipeline._last_resources_check_ts = 0.0
    pipeline._resources_check_interval = 60.0
    # H-17 / : new attribute added in __init__, must be
    pipeline._templates_applied = False
    return pipeline


class _RecordingStub:
    """Real-object stub for ``app.recording`` with a real lock."""

    def __init__(self) -> None:
        self._watchdog_lock = threading.Lock()
        self._transcription_thread: threading.Thread | None = None
        # Track whether the lock was actually acquired by the clear.
        self._lock_acquired = False

    # ``threading.Lock`` doesn't expose an "is_held_by_current_thread"


class TestTranscriptionThreadClearUsesWatchdogLock:
    """H-17: ``DictationPipeline.run``'s finally block must clear"""

    def test_clear_uses_watchdog_lock_not_app_lock(self):
        """The finally-block clear acquires ``recording._watchdog_lock``,"""
        app = _TestApp()
        recording_stub = _RecordingStub()
        # Pre-populate the field so we can verify the clear ran.
        recording_stub._transcription_thread = MagicMock(name="old-thread")
        app.recording = recording_stub

        pipeline = _new_pipeline(app)
        # Mark the cycle as not cancelled so the run() body doesn't
        app.recording._cancelled_cycle_ids = set()
        app.recording._cancelled_cycle_ids_lock = threading.Lock()
        app.recording.pop_streaming_session = MagicMock(return_value=None)
        # ``_reset_watchdog`` / ``_stop_watchdog_thread`` are called
        app.recording._reset_watchdog = MagicMock()
        app.recording._stop_watchdog_thread = MagicMock()

        original_lock = recording_stub._watchdog_lock
        acquired: list[bool] = []

        class _RecordingLock:
            def __enter__(self):
                original_lock.acquire()
                acquired.append(True)
                return self

            def __exit__(self, *args):
                original_lock.release()
                return False

        recording_stub._watchdog_lock = _RecordingLock()

        # Run the pipeline. The body will fail early (no real
        with contextlib.suppress(Exception):
            pipeline.run(
                audio=None,
                duration=0.0,
                recorded_rms=0.0,
                cycle_id="test-cycle",
            )

        # Assert 1: _transcription_thread was cleared to None.
        assert recording_stub._transcription_thread is None, (
            "H-17: finally block must clear recording._transcription_thread to None"
        )
        # Assert 2: the watchdog lock was acquired (the clear used it).
        assert acquired, (
            "H-17: finally block must acquire recording._watchdog_lock, "
            "acquisition list is empty (clear likely used app._lock instead)."
        )
        # Assert 3: app._lock was NOT acquired by the clear. (It may

    def test_clear_falls_back_gracefully_when_watchdog_lock_missing(self):
        """If ``recording._watchdog_lock`` is missing (very old or stub"""
        app = _TestApp()
        recording_stub = _RecordingStub()
        # Remove _watchdog_lock to simulate a stub app without it.
        del recording_stub._watchdog_lock
        recording_stub._transcription_thread = MagicMock(name="old-thread")
        app.recording = recording_stub

        pipeline = _new_pipeline(app)
        app.recording._cancelled_cycle_ids = set()
        app.recording._cancelled_cycle_ids_lock = threading.Lock()
        # ``_transcribe`` and the finally block both call
        app.recording.pop_streaming_session = MagicMock(return_value=None)
        app.recording._reset_watchdog = MagicMock()
        app.recording._stop_watchdog_thread = MagicMock()

        with contextlib.suppress(Exception):
            pipeline.run(
                audio=None,
                duration=0.0,
                recorded_rms=0.0,
                cycle_id="test-cycle",
            )

        # The clear still ran (defensive fallback), the field is None.
        assert recording_stub._transcription_thread is None, (
            "H-17 defensive fallback: clear must still run when _watchdog_lock is missing (assigns without lock)"
        )


# _apply_templates sets _templates_applied flag ───────────


class TestTemplatesAppliedFlag:
    """(defense-in-depth observability): ``_apply_templates``"""

    def test_flag_set_when_template_match_modifies_text(self):
        """When ``template_manager.match`` returns a non-None expanded"""
        app = _make_app_with_template_manager(match_return="expanded output")
        pipeline = _new_pipeline(app)
        assert pipeline._templates_applied is False, "Flag must start False"

        pipeline._apply_templates("trigger phrase")

        assert pipeline._templates_applied is True, (
            "_apply_templates must set _templates_applied=True when a template match modifies the text"
        )

    def test_flag_not_set_when_no_template_match(self):
        """flag must remain False."""
        app = _make_app_with_template_manager(match_return=None)
        pipeline = _new_pipeline(app)
        assert pipeline._templates_applied is False

        pipeline._apply_templates("no matching trigger")

        assert pipeline._templates_applied is False, (
            "_apply_templates must NOT set _templates_applied when no template matched"
        )

    def test_flag_not_set_when_templates_disabled(self):
        """When ``templates_enabled`` is False, ``_apply_templates``"""
        app = _make_app_with_template_manager(match_return="expanded")
        app.config.templates_enabled = False
        pipeline = _new_pipeline(app)

        pipeline._apply_templates("trigger phrase")

        assert pipeline._templates_applied is False, (
            "_apply_templates must NOT set _templates_applied when templates_enabled is False"
        )

    def test_flag_not_set_when_template_manager_raises(self):
        """When ``template_manager.match`` raises, the exception is"""
        app = _make_app_with_template_manager(match_side_effect=RuntimeError("boom"))
        pipeline = _new_pipeline(app)

        pipeline._apply_templates("trigger phrase")

        assert pipeline._templates_applied is False, (
            "_apply_templates must NOT set _templates_applied "
            "when template_manager.match raised (we don't know if a "
            "match would have occurred)"
        )


def _make_app_with_template_manager(
    *,
    match_return: object = None,
    match_side_effect: object = None,
) -> _TestApp:
    """Build an app with a mock ``_template_manager``."""
    app = _TestApp()
    app._template_manager = MagicMock()
    if match_side_effect is not None:
        app._template_manager.match.side_effect = match_side_effect
    else:
        app._template_manager.match.return_value = match_return
    return app


# _apply_llm_polish logs privacy notice ───────────────────


class TestPolishPrivacyNotice:
    """(defense-in-depth observability): when templates were"""

    def _make_app_with_llm_polish_enabled(self) -> _TestApp:
        """Build an app with LLM polish enabled (consent + API key)."""
        app = _TestApp()
        app.config.llm_polish = True
        app.config.llm_api_key = "sk-test-key-1234567890abcdef"
        app.config.llm_polish_consent = True
        # Pre-build the polisher mock so ``_apply_llm_polish`` doesn't
        app._llm_polisher = MagicMock()
        app._llm_polisher.polish.return_value = "polished text"
        return app

    def test_notice_logged_when_templates_applied_and_polish_enabled(self, caplog):
        """Privacy NOTICE fires when templates were applied AND LLM"""
        app = self._make_app_with_llm_polish_enabled()
        pipeline = _new_pipeline(app)
        pipeline._templates_applied = True  # simulate templates ran

        with caplog.at_level(logging.INFO, logger="voice_typer.server.dictation_pipeline"):
            result = pipeline._apply_llm_polish("hello world")

        # Polish was called (returned "polished text").
        assert result == "polished text"
        # The privacy NOTICE was logged.
        notices = [
            r
            for r in caplog.records
            if r.levelno == logging.INFO and "Templates were applied before LLM polish" in r.getMessage()
        ]
        assert notices, (
            "_apply_llm_polish must log a privacy NOTICE when templates were applied and LLM polish is enabled"
        )
        # The notice must mention redact_pii so operators can
        assert "redact_pii" in notices[0].getMessage(), (
            "privacy NOTICE must reference redact_pii so operators can trace the defense-in-depth chain"
        )

    def test_no_notice_when_templates_not_applied(self, caplog):
        """Privacy NOTICE must NOT fire when templates were not applied"""
        app = self._make_app_with_llm_polish_enabled()
        pipeline = _new_pipeline(app)
        pipeline._templates_applied = False  # templates did NOT run

        with caplog.at_level(logging.INFO, logger="voice_typer.server.dictation_pipeline"):
            pipeline._apply_llm_polish("hello world")

        notices = [
            r
            for r in caplog.records
            if r.levelno == logging.INFO and "Templates were applied before LLM polish" in r.getMessage()
        ]
        assert not notices, "privacy NOTICE must NOT fire when templates were not applied"

    def test_no_notice_when_polish_disabled(self, caplog):
        """Privacy NOTICE must NOT fire when LLM polish is disabled"""
        app = _TestApp()
        app.config.llm_polish = False  # polish disabled
        pipeline = _new_pipeline(app)
        pipeline._templates_applied = True  # templates ran

        with caplog.at_level(logging.INFO, logger="voice_typer.server.dictation_pipeline"):
            pipeline._apply_llm_polish("hello world")

        notices = [r for r in caplog.records if "Templates were applied before LLM polish" in r.getMessage()]
        assert not notices, "privacy NOTICE must NOT fire when LLM polish is disabled"

    def test_no_notice_when_consent_not_given(self, caplog):
        """Privacy NOTICE must NOT fire when LLM polish consent is False"""
        app = _TestApp()
        app.config.llm_polish = True
        app.config.llm_api_key = "sk-test-key-1234567890abcdef"
        app.config.llm_polish_consent = False  # consent NOT given
        pipeline = _new_pipeline(app)
        pipeline._templates_applied = True

        with caplog.at_level(logging.INFO, logger="voice_typer.server.dictation_pipeline"):
            pipeline._apply_llm_polish("hello world")

        notices = [r for r in caplog.records if "Templates were applied before LLM polish" in r.getMessage()]
        assert not notices, "privacy NOTICE must NOT fire when llm_polish_consent is False"


class TestFailClosedOnRedactPiiUnavailable:
    """(defense-in-depth fail-closed): when templates were"""

    def _make_app_with_llm_polish_enabled(self) -> _TestApp:
        app = _TestApp()
        app.config.llm_polish = True
        app.config.llm_api_key = "sk-test-key-1234567890abcdef"
        app.config.llm_polish_consent = True
        app._llm_polisher = MagicMock()
        app._llm_polisher.polish.return_value = "polished text"
        return app

    def test_polish_skipped_when_redact_pii_unimportable_and_templates_applied(self, caplog, monkeypatch):
        """templates were applied, polish is skipped (returns original"""
        app = self._make_app_with_llm_polish_enabled()
        pipeline = _new_pipeline(app)
        pipeline._templates_applied = True

        # Force ``from voice_typer.server.security import redact_pii``
        import sys

        class _BrokenSecurityModule:
            """Stub module that raises ImportError when ``redact_pii``"""

            def __getattr__(self, name):
                if name == "redact_pii":
                    raise ImportError(f"cannot import name '{name}'")
                raise AttributeError(name)

        # Save and replace the security module.
        original_security = sys.modules.get("voice_typer.server.security")
        sys.modules["voice_typer.server.security"] = _BrokenSecurityModule()
        try:
            with caplog.at_level(logging.WARNING, logger="voice_typer.server.dictation_pipeline"):
                result = pipeline._apply_llm_polish("hello world")
        finally:
            if original_security is not None:
                sys.modules["voice_typer.server.security"] = original_security
            else:
                sys.modules.pop("voice_typer.server.security", None)

        # Fail-closed: polish was skipped, original text returned.
        assert result == "hello world", (
            "fail-closed: when redact_pii is unimportable AND "
            "templates were applied, polish must be skipped (return "
            f"original text). Got: {result!r}"
        )
        # The polisher's polish() was NOT called.
        app._llm_polisher.polish.assert_not_called()
        # A warning was logged explaining the fail-closed.
        warnings = [
            r
            for r in caplog.records
            if r.levelno == logging.WARNING
            and "redact_pii not importable" in r.getMessage()
            and "fail-closed" in r.getMessage()
        ]
        assert warnings, (
            "fail-closed: a WARNING must be logged explaining "
            "why polish was skipped (redact_pii unimportable + templates applied)"
        )

    def test_polish_not_skipped_when_redact_pii_unimportable_but_no_templates(self, caplog, monkeypatch):
        """When ``redact_pii`` is unimportable but templates were NOT"""
        app = self._make_app_with_llm_polish_enabled()
        pipeline = _new_pipeline(app)
        pipeline._templates_applied = False  # templates NOT applied

        import sys

        class _BrokenSecurityModule:
            def __getattr__(self, name):
                if name == "redact_pii":
                    raise ImportError(f"cannot import name '{name}'")
                raise AttributeError(name)

        original_security = sys.modules.get("voice_typer.server.security")
        sys.modules["voice_typer.server.security"] = _BrokenSecurityModule()
        try:
            result = pipeline._apply_llm_polish("hello world")
        finally:
            if original_security is not None:
                sys.modules["voice_typer.server.security"] = original_security
            else:
                sys.modules.pop("voice_typer.server.security", None)

        # Polish proceeded normally, the polisher was called.
        assert result == "polished text", (
            "when redact_pii is unimportable but templates were NOT applied, polish must proceed normally"
        )
        app._llm_polisher.polish.assert_called_once_with("hello world")

    def test_polish_proceeds_when_redact_pii_importable_and_templates_applied(self):
        """When ``redact_pii`` IS importable AND templates were applied,"""
        app = self._make_app_with_llm_polish_enabled()
        pipeline = _new_pipeline(app)
        pipeline._templates_applied = True

        # ``redact_pii`` is importable (the real security module is
        result = pipeline._apply_llm_polish("hello world")

        # Polish proceeded normally.
        assert result == "polished text"
        app._llm_polisher.polish.assert_called_once_with("hello world")


class TestEndToEndTemplateThenLLMPolish:
    """End-to-end: ``_apply_templates`` sets the flag, then"""

    def test_template_match_then_polish_logs_notice(self, caplog):
        """A template match in step 5 sets the flag; step 7's polish"""
        app = _TestApp()
        app.config.llm_polish = True
        app.config.llm_api_key = "sk-test-key-1234567890abcdef"
        app.config.llm_polish_consent = True
        app._template_manager = MagicMock()
        app._template_manager.match.return_value = "expanded from template"
        app._llm_polisher = MagicMock()
        app._llm_polisher.polish.return_value = "polished"

        pipeline = _new_pipeline(app)

        # Step 5: apply templates (sets the flag).
        text = pipeline._apply_templates("trigger phrase")
        assert text == "expanded from template"
        assert pipeline._templates_applied is True

        # Step 7: apply LLM polish (reads the flag, logs NOTICE).
        with caplog.at_level(logging.INFO, logger="voice_typer.server.dictation_pipeline"):
            result = pipeline._apply_llm_polish(text)

        assert result == "polished"
        notices = [
            r
            for r in caplog.records
            if r.levelno == logging.INFO and "Templates were applied before LLM polish" in r.getMessage()
        ]
        assert notices, "end-to-end: privacy NOTICE must fire after a template match followed by LLM polish"
