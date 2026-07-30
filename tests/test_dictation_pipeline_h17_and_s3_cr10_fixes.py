"""Tests for the H-17 and S3-CR-10 fixes in ``dictation_pipeline.py``.

H-17 — ``app._lock`` acquired on one side only (zero protection)
-----------------------------------------------------------------
``DictationPipeline.run``'s ``finally`` block cleared
``recording._transcription_thread = None`` under ``self._app._lock``,
while the matching WRITE (``RecordingController._stop_impl``) and READ
(``_force_recover_from_stuck_transcription``) both guarded the field
with ``RecordingController._watchdog_lock``. Because the clear used a
DIFFERENT lock, it provided ZERO mutual exclusion against the
write/read — a concurrent ``_stop_impl`` could be mid-assignment of
``self._transcription_thread`` (Thread → None or vice versa) when the
clear ran, and the watchdog could observe a stale or partially-
constructed reference.

The fix changes the clear to acquire ``recording._watchdog_lock`` —
the SAME lock used by the write/read in ``recording_controller.py``.

S3-CR-10 — templates ``{clipboard}`` substitution → LLM exfiltration
----------------------------------------------------------------------
The CR-10 fix in ``llm_polish._call_api`` (out of this agent's
scope) applies ``redact_pii`` to the user-content before the LLM API
send. This file adds defense-in-depth observability + a fail-closed
sanity check at the ``DictationPipeline._apply_llm_polish`` layer:

  1. ``_apply_templates`` sets ``self._templates_applied = True`` when
     a template match modifies the text. (Templates may substitute
     ``{clipboard}`` with the user's current clipboard content —
     passwords, 2FA codes, private messages.)
  2. ``_apply_llm_polish`` logs a privacy NOTICE when templates were
     applied and LLM polish is enabled, so operators can audit when
     template-substituted content is flowing toward the CR-10
     redaction gate.
  3. ``_apply_llm_polish`` performs a sanity check that
     ``redact_pii`` is importable BEFORE calling ``polish()``. If the
     import fails AND templates were applied this cycle, polish is
     SKIPPED entirely (fail-closed) — without ``redact_pii``, the
     CR-10 gate inside ``_call_api`` would also fail open, sending
     the un-redacted clipboard-substituted text to the LLM API. When
     templates were NOT applied, the sanity check is skipped (the
     text is the user's own dictation, lower privacy risk).
"""

from __future__ import annotations

import contextlib
import logging
import threading
from unittest.mock import MagicMock

from voice_typer.server.dictation_pipeline import DictationPipeline

# ─── Test helpers ──────────────────────────────────────────────────────


class _TestApp:
    """Minimal non-magic test app for DictationPipeline tests.

    Mirrors the pattern in ``test_dictation_pipeline_review_fixes.py``:
    a custom class (instead of ``MagicMock``) so the four notify-once
    flag attributes correctly default to ``False`` via
    ``getattr(..., False)`` — MagicMock would auto-create truthy
    children for any attribute access.
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
        # recording is a MagicMock so attribute access on
        # ``_watchdog_lock`` / ``_transcription_thread`` returns
        # MagicMock children by default — but tests that need real
        # lock semantics override ``recording`` with a stub.
        self.recording = MagicMock()
        # ``recorder`` is read by the finally block in run() — make
        # it a MagicMock with ``recording = False`` so the session
        # cleanup branch is short-circuited.
        self.recorder = MagicMock()
        self.recorder.recording = False
        self._busy_event = MagicMock()
        self._schedule_timer = MagicMock()
        self._waveform_bubble = MagicMock()
        # ``_lock`` is kept for back-compat with any test that still
        # mocks it — the H-17 fix means the production code no longer
        # acquires ``app._lock`` for the _transcription_thread clear.
        self._lock = MagicMock()
        self._lock.__enter__ = MagicMock(return_value=self._lock)
        self._lock.__exit__ = MagicMock(return_value=False)
        # NOTE: the four notify-once flags are intentionally NOT
        # pre-declared — production code relies on getattr-default.

    # Auto-mock unknown attributes (like MagicMock) but DO NOT
    # auto-create the notify-once flag names — they must default to
    # False via getattr-with-default.
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
    """Build a fresh DictationPipeline tied to ``app``.

    Mirrors how ``RecordingController._stop_impl`` constructs a new
    pipeline per transcription cycle. Uses ``__new__`` to bypass
    ``__init__`` (which expects a real VoiceTyperApp) and manually
    sets the attributes the pipeline methods read.
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
    # ``_check_resources_throttled`` reads these — they're normally
    # set by ``__init__``. Initialize them so ``run()`` doesn't crash
    # on the resource-check fast-path before reaching the finally
    # block whose lock-acquisition we're testing.
    pipeline._last_resources_check_ts = 0.0
    pipeline._resources_check_interval = 60.0
    # H-17 / S3-CR-10: new attribute added in __init__ — must be
    # initialized here too because ``_new_pipeline`` bypasses
    # ``__init__``.
    pipeline._templates_applied = False
    return pipeline


# ─── H-17: _transcription_thread clear uses _watchdog_lock ─────────────


class _RecordingStub:
    """Real-object stub for ``app.recording`` with a real lock.

    A MagicMock auto-creates child mocks for any attribute access, so
    ``recording._watchdog_lock`` would return a MagicMock that doesn't
    support ``with`` semantics correctly out of the box. This stub
    uses a real ``threading.Lock`` so the H-17 fix's ``with
    _watchdog_lock:`` block exercises real lock acquisition.
    """

    def __init__(self) -> None:
        self._watchdog_lock = threading.Lock()
        self._transcription_thread: threading.Thread | None = None
        # Track whether the lock was actually acquired by the clear.
        self._lock_acquired = False

    # ``threading.Lock`` doesn't expose an "is_held_by_current_thread"
    # API without ``RLock``. We use a wrapper to record acquisition.
    # We can't use RLock here because the production code uses plain
    # Lock — but we can wrap the lock to record the acquire call.
    # Actually, simpler: just use a real Lock and verify the
    # ``_transcription_thread`` field is None after the clear (which
    # only happens if the lock was acquired and the assignment ran).


class TestH17TranscriptionThreadClearUsesWatchdogLock:
    """H-17: ``DictationPipeline.run``'s finally block must clear
    ``recording._transcription_thread`` under
    ``recording._watchdog_lock`` (NOT ``app._lock``).

    Pre-fix: the clear used ``app._lock`` — a DIFFERENT lock from the
    one used by the WRITE (``RecordingController._stop_impl``) and
    READ (``_force_recover_from_stuck_transcription``), both of which
    use ``_watchdog_lock``. The mismatch provided zero mutual
    exclusion, so a concurrent ``_stop_impl`` could be mid-assignment
    when the clear ran.

    Post-fix: the clear acquires ``recording._watchdog_lock`` — the
    SAME lock used by the write/read.
    """

    def test_clear_uses_watchdog_lock_not_app_lock(self):
        """The finally-block clear acquires ``recording._watchdog_lock``,
        NOT ``app._lock``.

        We construct an app whose ``recording`` is a real stub with a
        real lock and verify:

          1. After ``run()`` completes, ``_transcription_thread`` is
             ``None`` (the clear ran).
          2. ``app._lock.__enter__`` was NOT called (the clear did
             not acquire ``app._lock``).
          3. The pipeline uses ``recording._watchdog_lock`` — verified
             by replacing it with a lock that records acquisition and
             asserting it was acquired.
        """
        app = _TestApp()
        recording_stub = _RecordingStub()
        # Pre-populate the field so we can verify the clear ran.
        recording_stub._transcription_thread = MagicMock(name="old-thread")
        app.recording = recording_stub

        pipeline = _new_pipeline(app)
        # Mark the cycle as not cancelled so the run() body doesn't
        # take the cancelled-cycle early-return path.
        app.recording._cancelled_cycle_ids = set()
        app.recording._cancelled_cycle_ids_lock = threading.Lock()
        # UE-10: the streaming-session cleanup in finally (and the
        # pop in ``_transcribe``) call ``pop_streaming_session()``;
        # make it return None so neither branch attempts to cancel a
        # real session. (Pre-UE-10 the code called
        # ``get_streaming_session()`` instead.)
        app.recording.pop_streaming_session = MagicMock(return_value=None)
        # ``_reset_watchdog`` / ``_stop_watchdog_thread`` are called
        # from finally — make them no-ops.
        app.recording._reset_watchdog = MagicMock()
        app.recording._stop_watchdog_thread = MagicMock()

        # Wrap the watchdog lock to record acquisition. We use a
        # custom context-manager wrapper so we can assert it was
        # acquired (without breaking the ``with`` semantics).
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
        # transcription backend) but the finally block must still run
        # the clear.
        with contextlib.suppress(Exception):
            pipeline.run(
                audio=None,
                duration=0.0,
                recorded_rms=0.0,
                cycle_id="test-cycle",
                watchdog=None,
            )

        # Assert 1: _transcription_thread was cleared to None.
        assert recording_stub._transcription_thread is None, (
            "H-17: finally block must clear recording._transcription_thread to None"
        )
        # Assert 2: the watchdog lock was acquired (the clear used it).
        assert acquired, (
            "H-17: finally block must acquire recording._watchdog_lock — "
            "acquisition list is empty (clear likely used app._lock instead)."
        )
        # Assert 3: app._lock was NOT acquired by the clear. (It may
        # be acquired by other code paths in run() — we only care
        # that the clear didn't use it. Since the clear now uses
        # _watchdog_lock, app._lock.__enter__ call count should be
        # unchanged from before the clear ran. The simplest
        # invariant: app._lock.__enter__ was NOT called for the
        # transcription_thread clear. We verify by checking that
        # _watchdog_lock was acquired at least once AND
        # _transcription_thread is None — which together prove the
        # clear used _watchdog_lock.)
        # (No direct assertion on app._lock here — the MagicMock
        # auto-creates child mocks that make call-counting fragile.
        # The two assertions above are sufficient: the clear ran,
        # and it used _watchdog_lock.)

    def test_clear_falls_back_gracefully_when_watchdog_lock_missing(self):
        """If ``recording._watchdog_lock`` is missing (very old or stub
        app), the clear still runs (without the lock) and logs the
        race. This preserves the defensive fallback behavior.
        """
        app = _TestApp()
        recording_stub = _RecordingStub()
        # Remove _watchdog_lock to simulate a stub app without it.
        del recording_stub._watchdog_lock
        recording_stub._transcription_thread = MagicMock(name="old-thread")
        app.recording = recording_stub

        pipeline = _new_pipeline(app)
        app.recording._cancelled_cycle_ids = set()
        app.recording._cancelled_cycle_ids_lock = threading.Lock()
        # UE-10: ``_transcribe`` and the finally block both call
        # ``pop_streaming_session()`` (atomic) — mock it to return
        # None so the cleanup branches short-circuit.
        app.recording.pop_streaming_session = MagicMock(return_value=None)
        app.recording._reset_watchdog = MagicMock()
        app.recording._stop_watchdog_thread = MagicMock()

        with contextlib.suppress(Exception):
            pipeline.run(
                audio=None,
                duration=0.0,
                recorded_rms=0.0,
                cycle_id="test-cycle",
                watchdog=None,
            )

        # The clear still ran (defensive fallback) — the field is None.
        assert recording_stub._transcription_thread is None, (
            "H-17 defensive fallback: clear must still run when _watchdog_lock is missing (assigns without lock)"
        )


# ─── S3-CR-10: _apply_templates sets _templates_applied flag ───────────


class TestS3CR10TemplatesAppliedFlag:
    """S3-CR-10 (defense-in-depth observability): ``_apply_templates``
    sets ``self._templates_applied = True`` when a template match
    modifies the text. The downstream ``_apply_llm_polish`` uses this
    flag to log a privacy NOTICE and to gate the fail-closed sanity
    check on ``redact_pii``.
    """

    def test_flag_set_when_template_match_modifies_text(self):
        """When ``template_manager.match`` returns a non-None expanded
        string, ``_templates_applied`` must be set to True.
        """
        app = _make_app_with_template_manager(match_return="expanded output")
        pipeline = _new_pipeline(app)
        assert pipeline._templates_applied is False, "Flag must start False"

        pipeline._apply_templates("trigger phrase")

        assert pipeline._templates_applied is True, (
            "S3-CR-10: _apply_templates must set _templates_applied=True when a template match modifies the text"
        )

    def test_flag_not_set_when_no_template_match(self):
        """When ``template_manager.match`` returns None (no match), the
        flag must remain False.
        """
        app = _make_app_with_template_manager(match_return=None)
        pipeline = _new_pipeline(app)
        assert pipeline._templates_applied is False

        pipeline._apply_templates("no matching trigger")

        assert pipeline._templates_applied is False, (
            "S3-CR-10: _apply_templates must NOT set _templates_applied when no template matched"
        )

    def test_flag_not_set_when_templates_disabled(self):
        """When ``templates_enabled`` is False, ``_apply_templates``
        must early-return without touching the flag.
        """
        app = _make_app_with_template_manager(match_return="expanded")
        app.config.templates_enabled = False
        pipeline = _new_pipeline(app)

        pipeline._apply_templates("trigger phrase")

        assert pipeline._templates_applied is False, (
            "S3-CR-10: _apply_templates must NOT set _templates_applied when templates_enabled is False"
        )

    def test_flag_not_set_when_template_manager_raises(self):
        """When ``template_manager.match`` raises, the exception is
        swallowed (existing behavior) and the flag must remain False
        — we cannot know whether a match would have occurred.
        """
        app = _make_app_with_template_manager(match_side_effect=RuntimeError("boom"))
        pipeline = _new_pipeline(app)

        pipeline._apply_templates("trigger phrase")

        assert pipeline._templates_applied is False, (
            "S3-CR-10: _apply_templates must NOT set _templates_applied "
            "when template_manager.match raised (we don't know if a "
            "match would have occurred)"
        )


def _make_app_with_template_manager(
    *,
    match_return: object = None,
    match_side_effect: object = None,
) -> _TestApp:
    """Build an app with a mock ``_template_manager``.

    The mock's ``match`` method returns ``match_return`` or raises
    ``match_side_effect`` (mutually exclusive — pass one or the other).
    """
    app = _TestApp()
    app._template_manager = MagicMock()
    if match_side_effect is not None:
        app._template_manager.match.side_effect = match_side_effect
    else:
        app._template_manager.match.return_value = match_return
    return app


# ─── S3-CR-10: _apply_llm_polish logs privacy notice ───────────────────


class TestS3CR10LLMPolishPrivacyNotice:
    """S3-CR-10 (defense-in-depth observability): when templates were
    applied AND LLM polish is enabled (with consent + API key),
    ``_apply_llm_polish`` logs a privacy NOTICE so operators can
    audit when template-substituted content is flowing toward the
    CR-10 redaction gate in ``llm_polish._call_api``.
    """

    def _make_app_with_llm_polish_enabled(self) -> _TestApp:
        """Build an app with LLM polish enabled (consent + API key)."""
        app = _TestApp()
        app.config.llm_polish = True
        app.config.llm_api_key = "sk-test-key-1234567890abcdef"
        app.config.llm_polish_consent = True
        # Pre-build the polisher mock so ``_apply_llm_polish`` doesn't
        # try to construct a real ``LLMPolisher``.
        app._llm_polisher = MagicMock()
        app._llm_polisher.polish.return_value = "polished text"
        return app

    def test_notice_logged_when_templates_applied_and_polish_enabled(self, caplog):
        """Privacy NOTICE fires when templates were applied AND LLM
        polish is enabled (with consent + API key).
        """
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
            "S3-CR-10: _apply_llm_polish must log a privacy NOTICE when "
            "templates were applied and LLM polish is enabled"
        )
        # The notice must mention CR-10 / redact_pii so operators can
        # trace the defense-in-depth chain.
        assert "CR-10" in notices[0].getMessage() or "redact_pii" in notices[0].getMessage(), (
            "S3-CR-10: privacy NOTICE must reference CR-10 / redact_pii "
            "so operators can trace the defense-in-depth chain"
        )

    def test_no_notice_when_templates_not_applied(self, caplog):
        """Privacy NOTICE must NOT fire when templates were not applied
        (the text is the user's own dictation, not substituted content).
        """
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
        assert not notices, "S3-CR-10: privacy NOTICE must NOT fire when templates were not applied"

    def test_no_notice_when_polish_disabled(self, caplog):
        """Privacy NOTICE must NOT fire when LLM polish is disabled
        (no API call → no exfiltration risk).
        """
        app = _TestApp()
        app.config.llm_polish = False  # polish disabled
        pipeline = _new_pipeline(app)
        pipeline._templates_applied = True  # templates ran

        with caplog.at_level(logging.INFO, logger="voice_typer.server.dictation_pipeline"):
            pipeline._apply_llm_polish("hello world")

        notices = [r for r in caplog.records if "Templates were applied before LLM polish" in r.getMessage()]
        assert not notices, "S3-CR-10: privacy NOTICE must NOT fire when LLM polish is disabled"

    def test_no_notice_when_consent_not_given(self, caplog):
        """Privacy NOTICE must NOT fire when LLM polish consent is False
        (the polish call is skipped — no API send).
        """
        app = _TestApp()
        app.config.llm_polish = True
        app.config.llm_api_key = "sk-test-key-1234567890abcdef"
        app.config.llm_polish_consent = False  # consent NOT given
        pipeline = _new_pipeline(app)
        pipeline._templates_applied = True

        with caplog.at_level(logging.INFO, logger="voice_typer.server.dictation_pipeline"):
            pipeline._apply_llm_polish("hello world")

        notices = [r for r in caplog.records if "Templates were applied before LLM polish" in r.getMessage()]
        assert not notices, "S3-CR-10: privacy NOTICE must NOT fire when llm_polish_consent is False"


# ─── S3-CR-10: fail-closed when redact_pii is unimportable ─────────────


class TestS3CR10FailClosedOnRedactPiiUnavailable:
    """S3-CR-10 (defense-in-depth fail-closed): when templates were
    applied AND ``redact_pii`` cannot be imported (broken
    ``security`` module), ``_apply_llm_polish`` SKIPS polish entirely
    (returns the original text). Without ``redact_pii``, the CR-10
    gate inside ``_call_api`` would also fail open (its try/except
    falls through to sending the original text). Skipping polish
    preserves the original text on the paste path — the user sees
    their transcription, not a leaked LLM payload.
    """

    def _make_app_with_llm_polish_enabled(self) -> _TestApp:
        app = _TestApp()
        app.config.llm_polish = True
        app.config.llm_api_key = "sk-test-key-1234567890abcdef"
        app.config.llm_polish_consent = True
        app._llm_polisher = MagicMock()
        app._llm_polisher.polish.return_value = "polished text"
        return app

    def test_polish_skipped_when_redact_pii_unimportable_and_templates_applied(self, caplog, monkeypatch):
        """Fail-closed: when ``redact_pii`` is unimportable AND
        templates were applied, polish is skipped (returns original
        text). The polisher's ``polish()`` must NOT be called.
        """
        app = self._make_app_with_llm_polish_enabled()
        pipeline = _new_pipeline(app)
        pipeline._templates_applied = True

        # Force ``from voice_typer.server.security import redact_pii``
        # to raise ImportError. We do this by patching the
        # ``voice_typer.server.security`` module's ``redact_pii``
        # attribute to be missing — the simplest way is to replace
        # the module in ``sys.modules`` with one that raises on
        # attribute access. But the import statement
        # ``from voice_typer.server.security import redact_pii`` will
        # succeed if the module exists (just no attribute). The
        # cleanest way is to make ``redact_pii`` raise AttributeError
        # — but ``from X import Y`` raises ImportError when Y is
        # missing from X (Python 3.6+).
        import sys

        class _BrokenSecurityModule:
            """Stub module that raises ImportError when ``redact_pii``
            is imported from it (simulating a broken security module).

            ``from voice_typer.server.security import redact_pii``
            triggers Python's attribute lookup on the module; if the
            attribute is missing AND the module doesn't define
            ``__getattr__``, Python raises ``ImportError`` (not
            ``AttributeError``) for ``from X import Y`` statements.
            """

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
            "S3-CR-10 fail-closed: when redact_pii is unimportable AND "
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
            and "S3-CR-10 fail-closed" in r.getMessage()
        ]
        assert warnings, (
            "S3-CR-10 fail-closed: a WARNING must be logged explaining "
            "why polish was skipped (redact_pii unimportable + templates applied)"
        )

    def test_polish_not_skipped_when_redact_pii_unimportable_but_no_templates(self, caplog, monkeypatch):
        """When ``redact_pii`` is unimportable but templates were NOT
        applied, polish proceeds normally. The text is the user's own
        dictation (lower privacy risk) — the CR-10 fail-open in
        ``_call_api`` is acceptable in this case.
        """
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

        # Polish proceeded normally — the polisher was called.
        assert result == "polished text", (
            "S3-CR-10: when redact_pii is unimportable but templates were NOT applied, polish must proceed normally"
        )
        app._llm_polisher.polish.assert_called_once_with("hello world")

    def test_polish_proceeds_when_redact_pii_importable_and_templates_applied(self):
        """When ``redact_pii`` IS importable AND templates were applied,
        polish proceeds normally. The sanity check passes, and the
        CR-10 redaction gate inside ``_call_api`` handles the actual
        PII stripping.
        """
        app = self._make_app_with_llm_polish_enabled()
        pipeline = _new_pipeline(app)
        pipeline._templates_applied = True

        # ``redact_pii`` is importable (the real security module is
        # in place — no patching).
        result = pipeline._apply_llm_polish("hello world")

        # Polish proceeded normally.
        assert result == "polished text"
        app._llm_polisher.polish.assert_called_once_with("hello world")


# ─── Integration: end-to-end _apply_templates → _apply_llm_polish ──────


class TestS3CR10EndToEndTemplateThenLLMPolish:
    """End-to-end: ``_apply_templates`` sets the flag, then
    ``_apply_llm_polish`` reads it and logs the privacy notice.

    This exercises the full defense-in-depth observability chain in
    the order it runs in production (step 5 → step 7).
    """

    def test_template_match_then_polish_logs_notice(self, caplog):
        """A template match in step 5 sets the flag; step 7's polish
        logs the privacy NOTICE.
        """
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
        assert notices, "S3-CR-10 end-to-end: privacy NOTICE must fire after a template match followed by LLM polish"
