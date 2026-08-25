"""Notify-once flag semantics for ``DictationPipeline`` failure toasts.

Split from the former catch-all module
``tests/test_dictation_pipeline_review_fixes.py``. Covers a-review
Finding 2: the
notify-once deduplication flags (``_vocab_fail_notified``,
``_template_fail_notified``, ``_history_fail_notified``,
``_crash_recovery_fail_notified``) were stored on
``DictationPipeline`` (cycle-scoped — a fresh pipeline is constructed
per transcription cycle), so the user got a tray notification on EVERY
cycle where the failure occurred. The fix moves the flags to
``self._app`` (session-scoped). These tests verify the notify-once
semantics hold across two consecutive pipelines sharing the same
``_app``.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

# Shared non-magic app + per-cycle pipeline factories (single
# canonical definition in tests/fixtures/).
from tests.fixtures.dictation_pipeline_helpers import make_test_app as _make_app, new_pipeline as _new_pipeline


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

    def _count_notify_calls_with(self, app, needle: str) -> int:
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

    Replaced the source-text scan with a parametrized
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
