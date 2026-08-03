"""Regression tests for the ``_hide_or_idle_bubble`` helper.

The 4-site ``set_state("idle") + hide()`` pattern in
``dictation_pipeline.py`` has been extracted into a single
``_hide_or_idle_bubble`` helper. These tests pin:

1. The helper exists and centralizes the ``always_visible`` /
   ``hide()`` branch logic.
2. Each of the 4 original call sites now delegates to the helper
   instead of duplicating the branch.
3. Behavioural test: the helper respects ``bubble_behavior`` and
   swallows teardown exceptions (so a bubble failure doesn't mask
   the real transcription result).
"""

from __future__ import annotations

import inspect
from unittest.mock import MagicMock

from voice_typer.server.dictation_pipeline import DictationPipeline


def test_hide_or_idle_bubble_helper_exists() -> None:
    """The ``_hide_or_idle_bubble`` helper must exist on DictationPipeline."""
    assert hasattr(DictationPipeline, "_hide_or_idle_bubble"), (
        "DictationPipeline must define a `_hide_or_idle_bubble` helper that "
        "centralizes the 4-site set_state('idle') + hide() pattern."
    )


def test_hide_or_idle_bubble_helper_contains_branch_logic() -> None:
    """The helper must contain the ``always_visible`` branch + ``hide()``
    fallback, wrapped in a best-effort try/except.
    """
    src = inspect.getsource(DictationPipeline._hide_or_idle_bubble)
    assert "always_visible" in src, "_hide_or_idle_bubble must check `bubble_behavior == 'always_visible'`."
    assert 'set_state("idle")' in src, "_hide_or_idle_bubble must call set_state('idle') for always_visible mode."
    assert ".hide()" in src, "_hide_or_idle_bubble must call hide() for non-always_visible mode."
    assert "except Exception" in src, (
        "_hide_or_idle_bubble must swallow teardown exceptions so a bubble "
        "failure doesn't mask the real transcription result."
    )


def test_run_exception_handler_uses_helper() -> None:
    """The error-recovery timer callback in ``run`` must delegate to the
    helper instead of duplicating the branch logic.
    """
    src = inspect.getsource(DictationPipeline.run)
    assert "_hide_or_idle_bubble" in src, (
        "run() must call _hide_or_idle_bubble for the error->idle transition (the 4-site pattern has been extracted)."
    )


def test_handle_empty_transcription_uses_helper() -> None:
    """``_handle_empty_transcription`` must delegate to the helper."""
    src = inspect.getsource(DictationPipeline._handle_empty_transcription)
    assert "_hide_or_idle_bubble" in src, (
        "_handle_empty_transcription must call _hide_or_idle_bubble instead "
        "of duplicating the set_state('idle') / hide() branch."
    )


def test_copy_and_paste_uses_helper() -> None:
    """``_copy_and_paste`` must delegate to the helper at both the
    clipboard-failure path and the success path.
    """
    src = inspect.getsource(DictationPipeline._copy_and_paste)
    # The helper must be called at least twice in this method
    # (clipboard-failure path + success path).
    count = src.count("_hide_or_idle_bubble")
    assert count >= 2, (
        f"_copy_and_paste must call _hide_or_idle_bubble at least twice "
        f"(clipboard-failure + success paths); found {count} calls."
    )


def test_hide_or_idle_bubble_respects_always_visible() -> None:
    """Behavioural: when ``bubble_behavior == 'always_visible'``, the
    helper calls ``set_state('idle')`` (NOT ``hide()``).
    """
    app = MagicMock()
    app.config.bubble_behavior = "always_visible"
    pipeline = DictationPipeline.__new__(DictationPipeline)
    pipeline._app = app

    pipeline._hide_or_idle_bubble("test")

    app._waveform_bubble.set_state.assert_called_once_with("idle")
    app._waveform_bubble.hide.assert_not_called()


def test_hide_or_idle_bubble_hides_when_not_always_visible() -> None:
    """Behavioural: when ``bubble_behavior != 'always_visible'``, the
    helper calls ``hide()`` (NOT ``set_state('idle')``).
    """
    app = MagicMock()
    app.config.bubble_behavior = "on_demand"
    pipeline = DictationPipeline.__new__(DictationPipeline)
    pipeline._app = app

    pipeline._hide_or_idle_bubble("test")

    app._waveform_bubble.hide.assert_called_once()
    app._waveform_bubble.set_state.assert_not_called()


def test_hide_or_idle_bubble_swallows_exceptions() -> None:
    """Behavioural: if the bubble raises, the helper must swallow the
    exception (so a bubble teardown failure doesn't mask the real
    transcription result).
    """
    app = MagicMock()
    app.config.bubble_behavior = "always_visible"
    app._waveform_bubble.set_state.side_effect = RuntimeError("bubble torn down")
    pipeline = DictationPipeline.__new__(DictationPipeline)
    pipeline._app = app

    # Must not raise.
    pipeline._hide_or_idle_bubble("test")
