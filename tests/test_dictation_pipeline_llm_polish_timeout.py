"""Regression tests for the the fix: LLM polish runs in a side-thread.

Pre-fix, ``DictationPipeline._apply_llm_polish`` called
``self._app._llm_polisher.polish(text)`` synchronously on the
dictation pipeline thread. The underlying ``LLMPolisher._call_api``
uses a 10s socket timeout, so a stalled LLM endpoint blocked the
pipeline for up to 10s before the user saw any text. The pipeline
thread is the single bottleneck for the user's paste latency — while
``_apply_llm_polish`` is running, the pipeline cannot process new
dictation triggers (start/stop/cancel from the hotkey path) and the
text is not yet on the clipboard.

The fix wraps the polish call in a side-thread with a shorter
pipeline-side timeout (``DictationPipeline._LLM_POLISH_PIPELINE_TIMEOUT_S``,
4s by default). On timeout, the original (unpolished) text is returned
to the user; the polish thread keeps running in the background (Python
cannot cancel a blocking ``urlopen`` call) and self-terminates when
the inner 10s socket timeout fires or the LLM responds.

These tests exercise:
  * The timeout path: a slow polish call returns the original text
    within the pipeline-side timeout (NOT the slow polish's full
    duration).
  * The success path: a fast polish call returns the polished text
    (regression guard — the side-thread wrapper must not break the
    normal path).
  * The exception path: an exception inside ``polish`` propagates to
    ``_apply_llm_polish``'s ``except Exception`` block so the existing
    notification / event-bus-publish path runs unchanged.
  * The constant exists and is shorter than the underlying 10s socket
    timeout (the whole point of the fix).
"""

from __future__ import annotations

import time
from unittest.mock import MagicMock

from voice_typer.server.dictation_pipeline import DictationPipeline


def _make_app_with_polish(polish_side_effect) -> MagicMock:
    """Build a minimal app with LLM polish enabled + a configured polisher mock.

    The polisher's ``polish`` is wired to ``polish_side_effect`` (a
    callable, return value, or ``side_effect`` per ``MagicMock``
    semantics). ``_templates_applied`` is False so the
    ``redact_pii`` sanity check is skipped (the test focuses on the
    timeout wrapper, not the fail-closed gate).
    """
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
    pipeline._watchdog = None
    pipeline._last_resources_check_ts = 0.0
    pipeline._resources_check_interval = 60.0
    pipeline._templates_applied = False
    return pipeline


class TestLLMPolishPipelineTimeout:
    """the pipeline thread must NOT block for the full LLM timeout."""

    def test_constant_is_shorter_than_socket_timeout(self):
        """``_LLM_POLISH_PIPELINE_TIMEOUT_S`` must be < the 10s socket
        timeout in ``LLMPolisher._call_api`` — otherwise the fix is a
        no-op (the pipeline would still wait the full 10s)."""
        assert DictationPipeline._LLM_POLISH_PIPELINE_TIMEOUT_S < 10.0, (
            "_LLM_POLISH_PIPELINE_TIMEOUT_S must be shorter than the "
            "underlying 10s socket timeout — otherwise the pipeline still "
            "blocks for the full 10s on a stalled LLM endpoint."
        )
        assert DictationPipeline._LLM_POLISH_PIPELINE_TIMEOUT_S > 0.0

    def test_slow_polish_returns_original_text_within_timeout(self, monkeypatch):
        """When ``polish`` takes longer than the pipeline timeout, the
        pipeline returns the original (unpolished) text WITHOUT waiting
        for the slow polish to finish."""
        app = _make_app_with_polish(lambda text: "polished-" + text)

        pipeline = _new_pipeline(app)
        # Shrink the timeout so the test runs in real-time without
        # waiting 4s. 0.1s is well under the 5s sleep below.
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
        # The pipeline returned well under the 5s sleep — bounded by
        # the 0.1s timeout (plus a small grace margin for thread
        # scheduling / executor shutdown overhead).
        assert elapsed < 1.0, (
            f"on timeout, _call_polish_with_timeout must return within ~the pipeline timeout. Elapsed: {elapsed:.2f}s"
        )

    def test_fast_polish_returns_polished_text(self):
        """When ``polish`` completes within the timeout, the polished
        text is returned (regression guard for the success path)."""
        app = _make_app_with_polish("polished text")

        pipeline = _new_pipeline(app)

        result = pipeline._call_polish_with_timeout(app._llm_polisher, "hello world")

        assert result == "polished text", (
            f"on success, _call_polish_with_timeout must return the polished text. Got: {result!r}"
        )
        # The polish mock was called once with the original text.
        app._llm_polisher.polish.assert_called_once_with("hello world")

    def test_polish_exception_propagates_to_apply_llm_polish_handler(self):
        """When ``polish`` raises, the exception propagates out of
        ``_call_polish_with_timeout`` so ``_apply_llm_polish``'s
        ``except Exception`` block runs (notification + event-bus
        publish). Pre-fix, the exception propagated directly from
        ``polish()``; the side-thread wrapper must preserve this
        contract (the exception is re-raised on ``future.result()``)."""

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
        """End-to-end: ``_apply_llm_polish`` returns the original text
        when the side-thread polish times out (does NOT raise, does NOT
        trigger the except-Exception notification path)."""
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
        """End-to-end: ``_apply_llm_polish`` returns the polished text
        when the side-thread polish completes within the timeout."""
        app = _make_app_with_polish("polished text")
        pipeline = _new_pipeline(app)
        pipeline._templates_applied = False

        result = pipeline._apply_llm_polish("hello world")

        assert result == "polished text"
