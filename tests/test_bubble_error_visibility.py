"""XA-6-3 / XA-6-19: bubble error-state visibility tests."""

from __future__ import annotations

import inspect

from voice_typer.server.dictation_pipeline import DictationPipeline


def test_run_exception_handler_calls_set_state_error() -> None:
    """XA-6-3: the exception handler in `run` must call"""
    src = inspect.getsource(DictationPipeline.run)
    assert 'set_state("error")' in src, (
        "XA-6-3: run's exception handler must call "
        'set_state("error") to surface the failure in the bubble '
        "(previously it called set_state('idle') or hide(), masking "
        "the symptom from the user)."
    )


def test_run_exception_handler_schedules_error_to_idle_timer() -> None:
    """XA-6-19: the error state must be time-bounded so the bubble"""
    src = inspect.getsource(DictationPipeline.run)
    assert "_schedule_timer(3.0" in src, (
        "XA-6-19: run's exception handler must schedule a 3s "
        "timer to transition the bubble out of error mode (matching "
        "the tray ERROR→IDLE timer)."
    )
    assert "always_visible" in src and "set_state" in src, (
        "XA-6-19: post-error cleanup must respect bubble_behavior "
        "(set_state('idle') for always_visible, hide() otherwise)."
    )


def test_run_exception_handler_does_not_immediately_hide_on_error() -> None:
    """XA-6-3 regression guard: the exception handler must NOT call"""
    src = inspect.getsource(DictationPipeline.run)
    # Find the `except Exception as e:` block.
    except_idx = src.find("except Exception as e:")
    assert except_idx >= 0, "expected an `except Exception as e:` block"
    # Slice from the except to the next `finally:` (or end of method).
    finally_idx = src.find("finally:", except_idx)
    block = src[except_idx : finally_idx if finally_idx > 0 else None]
    # The first bubble-related call in the block must be set_state("error").
    set_err_idx = block.find('set_state("error")')
    assert set_err_idx >= 0, "XA-6-3: the exception block must call set_state('error')."
    # Any hide()/idle call must come AFTER set_state("error") AND must
    if block.find(".hide()") >= 0:
        assert block.find(".hide()") > set_err_idx, (
            'XA-6-3 regression: hide() is called BEFORE set_state("error"), the failure would be masked.'
        )
    idle_idx = block.find('set_state("idle")')
    if idle_idx >= 0 and idle_idx < set_err_idx:
        pass
    # The key invariant: a `def` keyword (the scheduled callback) must
    def_idx = block.find("def ", set_err_idx)
    assert def_idx >= 0, (
        "XA-6-3: the exception block must define a scheduled callback (a `def`) for the deferred error->idle cleanup."
    )
