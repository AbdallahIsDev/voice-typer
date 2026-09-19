"""Regression tests for the the fix: LLM polish runs in a side-thread."""

from __future__ import annotations

import time
from unittest.mock import MagicMock

import pytest
from voice_typer.server.dictation_pipeline import DictationPipeline
from voice_typer.server.dictation_pipeline.enhancement_steps import (
    _reset_shared_polish_executor,
)


@pytest.fixture(autouse=True)
def _reset_shared_polish_executor_between_tests():
    """test-isolation fixture."""
    _reset_shared_polish_executor()
    yield
    _reset_shared_polish_executor()


def _make_app_with_polish(polish_side_effect) -> MagicMock:
    """Build a minimal app with LLM polish enabled + a configured polisher mock."""
    app = MagicMock()
    app.config.llm_polish = True
    app.config.llm_api_key = "sk-test-key-1234567890abcdef"
    app.config.llm_polish_consent = True
    app.config.llm_api_url = ""
    app.config.llm_model = ""
    app.config.llm_preset = "professional"
    app._llm_polisher = MagicMock()
    if callable(polish_side_effect):
        app._llm_polisher.polish.side_effect = polish_side_effect
    else:
        app._llm_polisher.polish.return_value = polish_side_effect
    return app


def _new_pipeline(app) -> DictationPipeline:
    """Build a fresh DictationPipeline tied to ``app`` (bypass __init__)."""
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


class TestLLMPolishPipelineTimeout:
    """the pipeline thread must NOT block for the full LLM timeout."""

    def test_constant_is_shorter_than_socket_timeout(self):
        """``_LLM_POLISH_PIPELINE_TIMEOUT_S`` must be < the 10s socket"""
        assert DictationPipeline._LLM_POLISH_PIPELINE_TIMEOUT_S < 10.0, (
            "_LLM_POLISH_PIPELINE_TIMEOUT_S must be shorter than the "
            "underlying 10s socket timeout, otherwise the pipeline still "
            "blocks for the full 10s on a stalled LLM endpoint."
        )
        assert DictationPipeline._LLM_POLISH_PIPELINE_TIMEOUT_S > 0.0

    def test_slow_polish_returns_original_text_within_timeout(self, monkeypatch):
        """pipeline returns the original (unpolished) text WITHOUT waiting"""
        app = _make_app_with_polish(lambda text: "polished-" + text)

        pipeline = _new_pipeline(app)
        # Shrink the timeout so the test runs in real-time without
        monkeypatch.setattr(pipeline, "_LLM_POLISH_PIPELINE_TIMEOUT_S", 0.1)

        def slow_polish(text):
            time.sleep(5.0)
            return "polished-" + text

        app._llm_polisher.polish.side_effect = slow_polish

        start = time.perf_counter()
        result = pipeline._call_polish_with_timeout(app._llm_polisher, "hello world")
        elapsed = time.perf_counter() - start

        # The pipeline returned the ORIGINAL text (not the polished one).
        assert result == "hello world", (
            f"on timeout, _call_polish_with_timeout must return the original text. Got: {result!r}"
        )
        # The pipeline returned well under the 5s sleep, bounded by
        assert elapsed < 1.0, (
            f"on timeout, _call_polish_with_timeout must return within ~the pipeline timeout. Elapsed: {elapsed:.2f}s"
        )

    def test_fast_polish_returns_polished_text(self):
        """When ``polish`` completes within the timeout, the polished"""
        app = _make_app_with_polish("polished text")

        pipeline = _new_pipeline(app)

        result = pipeline._call_polish_with_timeout(app._llm_polisher, "hello world")

        assert result == "polished text", (
            f"on success, _call_polish_with_timeout must return the polished text. Got: {result!r}"
        )
        # The polish mock was called once with the original text.
        app._llm_polisher.polish.assert_called_once_with("hello world")

    def test_polish_exception_propagates_to_apply_llm_polish_handler(self):
        """``_call_polish_with_timeout`` so ``_apply_llm_polish``'s"""

        class _PolishError(RuntimeError):
            pass

        def raising_polish(text):
            raise _PolishError("LLM API unreachable")

        app = _make_app_with_polish(raising_polish)

        pipeline = _new_pipeline(app)

        try:
            pipeline._call_polish_with_timeout(app._llm_polisher, "hello world")
        except _PolishError as exc:
            assert "LLM API unreachable" in str(exc), (
                "the original exception must propagate unchanged so "
                "_apply_llm_polish's except-Exception handler can redact "
                "and notify."
            )
        else:
            raise AssertionError(
                "_call_polish_with_timeout must re-raise the polish "
                "exception (so _apply_llm_polish's except-Exception handler "
                "runs the notification + event-bus-publish path)."
            )

    def test_apply_llm_polish_returns_unpolished_on_timeout(self, monkeypatch):
        """End-to-end: ``_apply_llm_polish`` returns the original text"""
        app = _make_app_with_polish("polished text")
        pipeline = _new_pipeline(app)
        pipeline._templates_applied = False
        monkeypatch.setattr(pipeline, "_LLM_POLISH_PIPELINE_TIMEOUT_S", 0.1)

        def slow_polish(text):
            time.sleep(5.0)
            return "polished-" + text

        app._llm_polisher.polish.side_effect = slow_polish

        result = pipeline._apply_llm_polish("hello world")

        assert result == "hello world", (
            "_apply_llm_polish must return the original text on "
            f"timeout (NOT raise, NOT return polished). Got: {result!r}"
        )

    def test_apply_llm_polish_returns_polished_on_success(self):
        """End-to-end: ``_apply_llm_polish`` returns the polished text"""
        app = _make_app_with_polish("polished text")
        pipeline = _new_pipeline(app)
        pipeline._templates_applied = False

        result = pipeline._apply_llm_polish("hello world")

        assert result == "polished text"
