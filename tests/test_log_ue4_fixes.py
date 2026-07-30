"""Tests for the UE-4 sub-findings fixed in ``voice_typer/server/log.py``.

Covers:

* **UE-4-F6** — ``_BubbleLevelExclusionFilter`` hybrid check: avoids the
  ``getMessage()`` call on every DEBUG record by checking ``record.msg``
  first, falling back to ``getMessage()`` only when args are present.
* **UE-4-F8** — ``quiet=True`` lowers the file handler to WARNING (not
  just the root logger) so the handler level matches the root logger
  level and the ``quiet`` contract is honoured end-to-end.
* **UE-4-F9** — File-handler dedup uses ``_SecureRotatingFileHandler``
  (not the parent ``RotatingFileHandler``) so a future caller that
  installs a stock ``RotatingFileHandler`` is NOT mistaken for the
  secure handler.
* **UE-4-F10** — ``_ensure_last_resort_redacted`` uses
  ``isinstance(f, type(pii_filter))`` instead of the string-based
  ``type(f).__name__ == "PIIRedactionFilter"`` check; a subclass of
  ``PIIRedactionFilter`` is recognized for idempotency.
* **UE-4-F13** — Rotation-lock failure DEBUG log emits only
  ``type(exc).__name__`` (not ``str(exc)``) so the user's home
  directory in the lock file path does not leak to stderr/debug logs.
* **UE-4-F15** — Stale RW-6 comment block (which contradicted XV-130)
  is deleted from the source.
"""

from __future__ import annotations

import logging
import logging.handlers
import os
from pathlib import Path

import pytest

# NOTE: the ``PIIRedactionFilter`` import is deferred to inside tests so
# that collection does not fail on minimal test environments where the
# security module has heavyweight imports. Importing it once at module
# load would also pollute the test-process logging config.


# ─── Shared test isolation ───────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _restore_logging_state():
    """Snapshot + restore the ``voice_typer`` logger + true root state.

    Mirrors the autouse fixture in ``tests/test_logging_setup.py`` so a
    ``setup_logging`` call inside a test does not pollute subsequent
    tests in the same process.
    """
    vt_root = logging.getLogger("voice_typer")
    saved_handlers = list(vt_root.handlers)
    saved_filters = list(vt_root.filters)
    saved_level = vt_root.level
    true_root = logging.getLogger()
    saved_true_handlers = list(true_root.handlers)
    saved_true_level = true_root.level
    from voice_typer.server import log as _log_module

    saved_session_id = _log_module._session_id
    yield
    vt_root.handlers = saved_handlers
    vt_root.filters = saved_filters
    vt_root.setLevel(saved_level)
    true_root.handlers = saved_true_handlers
    true_root.setLevel(saved_true_level)
    _log_module._session_id = saved_session_id
    _log_module.close_devnull_files()


@pytest.fixture
def clean_env(monkeypatch):
    """Clear VOICE_TYPER_* env vars that affect setup_logging."""
    for var in (
        "VOICE_TYPER_DEBUG",
        "VOICE_TYPER_QUIET",
        "VOICE_TYPER_LOG_JSON",
        "VOICE_TYPER_LOG_LEVEL_MODULES",
    ):
        monkeypatch.delenv(var, raising=False)


# ─── UE-4-F6: _BubbleLevelExclusionFilter hybrid check ──────────────────


class TestUe4F6BubbleFilterHybridCheck:
    """UE-4-F6: ``_BubbleLevelExclusionFilter.filter`` checks
    ``record.msg`` (raw template) first and only falls back to
    ``getMessage()`` when args are present. This avoids the
    ``getMessage()`` call on every DEBUG record (the hot path).
    """

    def _make_record(self, level: int, msg: str, args=()) -> logging.LogRecord:
        return logging.LogRecord(
            name="voice_typer.test",
            level=level,
            pathname=__file__,
            lineno=1,
            msg=msg,
            args=args,
            exc_info=None,
        )

    def test_warning_record_kept_unconditionally(self):
        """GT-62 (preserved): WARNING+ records are always kept, even if
        the message mentions the marker — no ``getMessage()`` call."""
        from voice_typer.server.log import _BubbleLevelExclusionFilter

        f = _BubbleLevelExclusionFilter()
        record = self._make_record(logging.WARNING, "bubble_level handler crashed")
        assert f.filter(record) is True

    def test_error_record_with_args_kept(self):
        """GT-62: ERROR+ records with args are kept unconditionally."""
        from voice_typer.server.log import _BubbleLevelExclusionFilter

        f = _BubbleLevelExclusionFilter()
        record = self._make_record(logging.ERROR, "bubble_level %s", ("crash",))
        assert f.filter(record) is True

    def test_debug_no_args_marker_in_template_dropped(self):
        """UE-4-F6 hot path: DEBUG record with no args and the marker
        in the raw template is dropped — WITHOUT calling getMessage().
        """
        from voice_typer.server.log import _BubbleLevelExclusionFilter

        f = _BubbleLevelExclusionFilter()
        record = self._make_record(logging.DEBUG, "bubble_level push")
        assert f.filter(record) is False

    def test_debug_no_args_marker_not_in_template_kept(self):
        """UE-4-F6: DEBUG record with no args and no marker in the
        template is kept — without calling getMessage()."""
        from voice_typer.server.log import _BubbleLevelExclusionFilter

        f = _BubbleLevelExclusionFilter()
        record = self._make_record(logging.DEBUG, "audio chunk processed")
        assert f.filter(record) is True

    def test_debug_with_args_marker_in_template_dropped(self):
        """UE-4-F6: when args ARE present, the filter still drops the
        record if the marker is in the raw template (the args would be
        substituted in, but the marker is already there)."""
        from voice_typer.server.log import _BubbleLevelExclusionFilter

        f = _BubbleLevelExclusionFilter()
        record = self._make_record(logging.DEBUG, "bubble_level %s", ("payload",))
        assert f.filter(record) is False

    def test_debug_with_args_marker_in_args_interpolation_dropped(self):
        """UE-4-F6 fallback: when args ARE present and the marker is
        NOT in the raw template but appears in the substituted output,
        the filter must fall back to ``getMessage()`` and drop the
        record. This is the correctness case that justifies the
        fallback path."""
        from voice_typer.server.log import _BubbleLevelExclusionFilter

        f = _BubbleLevelExclusionFilter()
        # Template has no marker; args interpolate to produce the marker.
        record = self._make_record(logging.DEBUG, "event=%s", ("bubble_level",))
        assert f.filter(record) is False, (
            "UE-4-F6: filter must fall back to getMessage() when args are "
            "present and the marker appears only in the substituted output"
        )

    def test_debug_with_args_marker_not_present_kept(self):
        """UE-4-F6: when args ARE present and the marker is in neither
        the template nor the substituted output, the record is kept."""
        from voice_typer.server.log import _BubbleLevelExclusionFilter

        f = _BubbleLevelExclusionFilter()
        record = self._make_record(logging.DEBUG, "chunk=%d", (42,))
        assert f.filter(record) is True

    def test_no_args_path_does_not_call_get_message(self, monkeypatch):
        """UE-4-F6 performance contract: when ``record.args`` is empty,
        the filter must NOT call ``record.getMessage()``. Verified by
        patching ``getMessage`` to raise — if the filter calls it, the
        test fails with the sentinel exception.
        """
        from voice_typer.server.log import _BubbleLevelExclusionFilter

        f = _BubbleLevelExclusionFilter()
        record = self._make_record(logging.DEBUG, "bubble_level push")

        def boom(*_a, **_kw):
            raise AssertionError(
                "UE-4-F6: filter called getMessage() on a no-args record; the "
                "hybrid check should use record.msg directly to avoid the "
                "format-string substitution cost on the hot path."
            )

        monkeypatch.setattr(record, "getMessage", boom)
        # Should not raise — the no-args path uses record.msg, not getMessage().
        assert f.filter(record) is False


# ─── UE-4-F8: quiet flag lowers file handler to WARNING ─────────────────


class TestUe4F8QuietFileHandlerLevel:
    """UE-4-F8: ``setup_logging(quiet=True)`` lowers BOTH the root
    logger AND the file handler to WARNING. Pre-UE-4-F8 only the root
    logger was lowered — the file handler stayed at INFO, so the
    handler still wanted INFO records but the root filtered them out
    before they reached any handler. Now the levels match.
    """

    def _file_handler(self) -> logging.Handler:
        from voice_typer.server.log import _SecureRotatingFileHandler

        handlers = logging.getLogger("voice_typer").handlers
        secure = [h for h in handlers if isinstance(h, _SecureRotatingFileHandler)]
        assert secure, f"no _SecureRotatingFileHandler installed; got {handlers!r}"
        return secure[0]

    def test_quiet_lowers_file_handler_to_warning(self, tmp_path, clean_env):
        """UE-4-F8: ``quiet=True`` sets the file handler level to
        WARNING (matching the root logger level)."""
        from voice_typer.server.log import reset, setup_logging

        reset()
        try:
            setup_logging(tmp_path, quiet=True)
            assert self._file_handler().level == logging.WARNING, (
                f"UE-4-F8: file handler level must be WARNING when quiet=True; "
                f"got {logging.getLevelName(self._file_handler().level)}"
            )
        finally:
            reset()

    def test_default_file_handler_level_is_info(self, tmp_path, clean_env):
        """UE-4-F8: default (no flags) keeps the file handler at INFO.
        Pre-existing behaviour, pinned here as a regression guard."""
        from voice_typer.server.log import reset, setup_logging

        reset()
        try:
            setup_logging(tmp_path)
            assert self._file_handler().level == logging.INFO, (
                f"UE-4-F8: default file handler level must be INFO; got "
                f"{logging.getLevelName(self._file_handler().level)}"
            )
        finally:
            reset()

    def test_debug_raises_file_handler_to_debug(self, tmp_path, clean_env):
        """UE-4-F8: ``debug=True`` raises the file handler to DEBUG
        (debug takes precedence over the default INFO)."""
        from voice_typer.server.log import reset, setup_logging

        reset()
        try:
            setup_logging(tmp_path, debug=True)
            assert self._file_handler().level == logging.DEBUG, (
                f"UE-4-F8: debug=True should raise file handler to DEBUG; got "
                f"{logging.getLevelName(self._file_handler().level)}"
            )
        finally:
            reset()

    def test_quiet_takes_precedence_over_debug(self, tmp_path, clean_env):
        """UE-4-F8: when BOTH ``quiet=True`` AND ``debug=True`` are
        passed, the file handler is at WARNING (quiet wins). The
        formula is ``WARNING if quiet else (DEBUG if debug else INFO)``
        — ``quiet`` short-circuits the ternary.
        """
        from voice_typer.server.log import reset, setup_logging

        reset()
        try:
            setup_logging(tmp_path, debug=True, quiet=True)
            assert self._file_handler().level == logging.WARNING, (
                f"UE-4-F8: quiet=True should take precedence over debug=True; "
                f"got {logging.getLevelName(self._file_handler().level)}"
            )
        finally:
            reset()


# ─── UE-4-F9: file-handler dedup uses _SecureRotatingFileHandler ────────


class TestUe4F9SecureHandlerDedup:
    """UE-4-F9: the ``setup_logging`` idempotency check uses
    ``isinstance(h, _SecureRotatingFileHandler)`` (not the parent
    ``RotatingFileHandler``) so a future caller that installs a stock
    ``RotatingFileHandler`` is NOT mistaken for the secure handler.
    """

    def test_setup_logging_idempotent_with_secure_handler(self, tmp_path, clean_env):
        """Calling ``setup_logging`` twice does NOT add a second
        ``_SecureRotatingFileHandler``."""
        from voice_typer.server.log import (
            _SecureRotatingFileHandler,
            reset,
            setup_logging,
        )

        reset()
        try:
            setup_logging(tmp_path)
            before = [h for h in logging.getLogger("voice_typer").handlers if isinstance(h, _SecureRotatingFileHandler)]
            setup_logging(tmp_path)
            after = [h for h in logging.getLogger("voice_typer").handlers if isinstance(h, _SecureRotatingFileHandler)]
            assert len(before) == 1
            assert len(after) == 1, (
                f"UE-4-F9: setup_logging should not install a duplicate "
                f"_SecureRotatingFileHandler; got {len(after)} after second call"
            )
        finally:
            reset()

    def test_stock_rotating_handler_does_not_count_as_secure(self, tmp_path, clean_env):
        """UE-4-F9: a stock ``RotatingFileHandler`` installed on the
        ``voice_typer`` logger does NOT satisfy the dedup check — the
        next ``setup_logging`` call still installs the secure handler
        because the secure handler is what guarantees the 0o600 perms
        and the inter-process rotation lock.
        """
        from voice_typer.server.log import (
            _SecureRotatingFileHandler,
            reset,
            setup_logging,
        )

        reset()
        try:
            # Install a stock RotatingFileHandler on the voice_typer logger
            # BEFORE setup_logging runs. Pre-UE-4-F9 the dedup check would
            # see this stock handler and skip installing the secure one.
            stock_log = tmp_path / "stock.log"
            stock = logging.handlers.RotatingFileHandler(stock_log)
            logging.getLogger("voice_typer").addHandler(stock)

            setup_logging(tmp_path)

            secure_handlers = [
                h for h in logging.getLogger("voice_typer").handlers if isinstance(h, _SecureRotatingFileHandler)
            ]
            assert len(secure_handlers) == 1, (
                f"UE-4-F9: setup_logging must install the _SecureRotatingFileHandler "
                f"even when a stock RotatingFileHandler is already present (the "
                f"stock handler does NOT satisfy the secure-handler dedup check); "
                f"got {len(secure_handlers)} secure handlers"
            )
        finally:
            # Close the stock handler so its FD doesn't leak.
            with __import__("contextlib").suppress(Exception):
                stock.close()
            reset()


# ─── UE-4-F10: _ensure_last_resort_redacted uses isinstance ────────────


class TestUe4F10LastResortIsinstance:
    """UE-4-F10: ``_ensure_last_resort_redacted`` uses
    ``isinstance(f, type(pii_filter))`` instead of the string-based
    ``type(f).__name__ == "PIIRedactionFilter"`` check.

    The string check is brittle: a subclass of ``PIIRedactionFilter``
    named differently (e.g. ``TestPIIRedactionFilter``) would be
    treated as a DIFFERENT filter and the function would double-attach.
    The isinstance check is type-safe and survives subclassing.
    """

    def _make_filter(self):
        from voice_typer.server.security import PIIRedactionFilter

        return PIIRedactionFilter()

    def test_idempotent_with_same_class(self):
        """Repeated calls do not double-attach the filter."""
        from voice_typer.server.log import _ensure_last_resort_redacted

        last_resort = logging.lastResort
        # Snapshot existing filters so we can restore them.
        saved_filters = list(last_resort.filters)
        try:
            last_resort.filters.clear()
            pii_filter = self._make_filter()
            _ensure_last_resort_redacted(pii_filter)
            _ensure_last_resort_redacted(pii_filter)
            assert last_resort.filters.count(pii_filter) == 1, (
                f"UE-4-F10: idempotency check failed; filter attached {len(last_resort.filters)} times, expected 1"
            )
        finally:
            last_resort.filters = saved_filters

    def test_idempotent_with_subclass(self):
        """UE-4-F10: a subclass of ``PIIRedactionFilter`` is recognized
        by the isinstance check — pre-UE-4-F10 the string-based check
        would have treated the subclass as a different filter and
        double-attached the parent class filter.
        """
        from voice_typer.server.log import _ensure_last_resort_redacted
        from voice_typer.server.security import PIIRedactionFilter

        class TestSubclassFilter(PIIRedactionFilter):
            """Subclass named differently from the parent — the
            string-based check would have missed this."""

        last_resort = logging.lastResort
        saved_filters = list(last_resort.filters)
        try:
            last_resort.filters.clear()
            # First attach: a TestSubclassFilter instance.
            subclass_filter = TestSubclassFilter()
            last_resort.addFilter(subclass_filter)
            # Second attach: a PIIRedactionFilter instance. The isinstance
            # check should recognize that a filter of the same class
            # (PIIRedactionFilter) is already attached (because
            # TestSubclassFilter IS-A PIIRedactionFilter) and skip.
            parent_filter = self._make_filter()
            _ensure_last_resort_redacted(parent_filter)
            # Only the subclass filter should be attached; the parent
            # was NOT re-added because isinstance(subclass_filter,
            # type(parent_filter)) == True.
            assert len(last_resort.filters) == 1, (
                f"UE-4-F10: a PIIRedactionFilter subclass on lastResort "
                f"should satisfy the isinstance idempotency check; got "
                f"{len(last_resort.filters)} filters: {last_resort.filters!r}"
            )
        finally:
            last_resort.filters = saved_filters

    def test_unrelated_filter_does_not_block_attach(self):
        """UE-4-F10: a filter of a DIFFERENT class does not satisfy the
        isinstance check — the PIIRedactionFilter IS still attached."""
        from voice_typer.server.log import _ensure_last_resort_redacted

        last_resort = logging.lastResort
        saved_filters = list(last_resort.filters)
        try:
            last_resort.filters.clear()
            # Attach an unrelated filter first.
            unrelated = logging.Filter()  # bare Filter, different class
            last_resort.addFilter(unrelated)
            pii_filter = self._make_filter()
            _ensure_last_resort_redacted(pii_filter)
            assert pii_filter in last_resort.filters, (
                f"UE-4-F10: an unrelated filter on lastResort should NOT "
                f"block the PIIRedactionFilter from being attached; got "
                f"{last_resort.filters!r}"
            )
        finally:
            last_resort.filters = saved_filters


# ─── UE-4-F13: rotation-lock failure log doesn't leak home path ─────────


class TestUe4F13LockFailureNoPathLeak:
    """UE-4-F13: when ``_acquire_rotation_lock`` fails, the DEBUG log
    emits only ``type(exc).__name__`` (e.g. ``PermissionError``) — NOT
    ``str(exc)`` which can include the lock file path (which contains
    the user's home directory).
    """

    def test_lock_failure_logs_exception_class_name_only(self, tmp_path, monkeypatch, caplog):
        """UE-4-F13: force ``_acquire_rotation_lock`` to raise an
        exception whose ``str()`` contains a path; assert the DEBUG log
        does NOT include the path string — only the exception class
        name."""
        from voice_typer.server import log as log_module

        # Path containing a "home directory" sentinel string.
        sensitive_path = str(tmp_path / "home" / "user" / ".voice-typer" / "lock")
        # An exception whose str() includes the path.
        exc_with_path = OSError(f"[Errno 13] Permission denied: '{sensitive_path}'")

        # Replace the platform branch with one that raises our crafted
        # exception. The simplest hook is to monkeypatch ``os.open`` so
        # it raises inside ``_acquire_rotation_lock``.
        def raising_open(*_a, **_kw):
            raise exc_with_path

        monkeypatch.setattr(os, "open", raising_open)

        handler = log_module._SecureRotatingFileHandler(tmp_path / "ue17.log", maxBytes=128, backupCount=1)
        try:
            with caplog.at_level(logging.DEBUG, logger=log_module.log.name):
                lock_fd = handler._acquire_rotation_lock()
            assert lock_fd is None, (
                "UE-4-F13 test setup failed: _acquire_rotation_lock should "
                "return None when the underlying open() raises"
            )
        finally:
            with __import__("contextlib").suppress(Exception):
                handler.close()

        # The DEBUG log must NOT contain the sensitive path. It SHOULD
        # contain the exception class name (``OSError``).
        debug_records = [r for r in caplog.records if r.levelno == logging.DEBUG and "[LOG-SETUP]" in r.message]
        assert debug_records, "UE-4-F13: expected a DEBUG log from _acquire_rotation_lock on failure"
        rendered = debug_records[0].getMessage()
        assert sensitive_path not in rendered, (
            f"UE-4-F13: lock-failure DEBUG log leaks the lock file path "
            f"(which contains the user's home directory); got: {rendered!r}"
        )
        assert "OSError" in rendered, (
            f"UE-4-F13: lock-failure DEBUG log must include the exception class name (``OSError``); got: {rendered!r}"
        )


# ─── UE-4-F15: stale RW-6 comment block deleted ─────────────────────────


class TestUe4F15Rw6CommentRemoved:
    """UE-4-F15: the stale RW-6 comment block (lines 1036-1047 in the
    pre-fix source) is deleted because it contradicted the XV-130 block
    that follows it. The RW-6 block claimed the PII filter was attached
    to BOTH the ``voice_typer`` logger AND each handler; XV-130 says
    (correctly, matching the actual code) that filters are attached to
    HANDLERS ONLY.

    Asserted by source-text inspection: the RW-6 block's distinctive
    phrasing ("the filter is attached to BOTH the ``voice_typer`` logger
    ... AND to each handler") must NOT appear in the source.
    """

    def test_rw6_dual_attachment_claim_is_gone(self):
        """The RW-6 block's distinctive dual-attachment claim must not
        appear in log.py source."""
        log_py = Path(__file__).resolve().parent.parent / "voice_typer" / "server" / "log.py"
        source = log_py.read_text(encoding="utf-8")
        # The exact phrasing of the contradicted RW-6 claim.
        forbidden_phrases = [
            "the filter is attached to BOTH the ``voice_typer`` logger",
            "RW-6: the filter is attached to BOTH",
        ]
        for phrase in forbidden_phrases:
            assert phrase not in source, (
                f"UE-4-F15: stale RW-6 comment block still present in log.py "
                f"(found forbidden phrase {phrase!r}). The RW-6 block claims "
                f"the PII filter is attached to BOTH the voice_typer logger "
                f"AND each handler, which contradicts the XV-130 block (and "
                f"the actual code, which only attaches to handlers). Delete "
                f"or rewrite the RW-6 block."
            )

    def test_xv130_block_still_present(self):
        """UE-4-F15 regression guard: the XV-130 block (which correctly
        documents the handler-only attachment) must still be present
        after the RW-6 deletion — we delete only the stale RW-6 block,
        not the XV-130 block that supersedes it.
        """
        log_py = Path(__file__).resolve().parent.parent / "voice_typer" / "server" / "log.py"
        source = log_py.read_text(encoding="utf-8")
        assert "XV-130" in source, (
            "UE-4-F15: XV-130 comment block must remain in log.py (only the "
            "stale RW-6 block should be deleted, not the XV-130 block that "
            "supersedes it)."
        )


# ─── UE-4-F15 (behavioural): PII filter attached to handlers only ──────


class TestUe4F15PiiFilterHandlerOnlyAttachment:
    """UE-4-F15 behavioural guard: ``setup_logging`` attaches the PII
    filter to each HANDLER (file + stderr) but NOT to the
    ``voice_typer`` root logger. The XV-130 block documents this;
    the deletion of the RW-6 block makes the source consistent with
    both the XV-130 docs and the actual code.
    """

    def test_pii_filter_not_attached_to_voice_typer_root(self, tmp_path, clean_env):
        """UE-4-F15 / XV-130: the ``voice_typer`` root logger has NO
        ``PIIRedactionFilter`` attached — only the handlers do."""
        from voice_typer.server.log import reset, setup_logging
        from voice_typer.server.security import PIIRedactionFilter

        reset()
        try:
            setup_logging(tmp_path)
            vt_root = logging.getLogger("voice_typer")
            pii_filters_on_logger = [f for f in vt_root.filters if isinstance(f, PIIRedactionFilter)]
            assert pii_filters_on_logger == [], (
                f"UE-4-F15 / XV-130: PIIRedactionFilter must NOT be attached "
                f"to the voice_typer root logger (handler-only attachment); "
                f"got {pii_filters_on_logger!r}"
            )
        finally:
            reset()

    def test_pii_filter_attached_to_handlers(self, tmp_path, clean_env):
        """UE-4-F15 / XV-130: each handler on the ``voice_typer`` logger
        has a ``PIIRedactionFilter`` attached (so records from child
        loggers like ``voice_typer.server.app`` are redacted via the
        handler filter, which fires for every record that reaches the
        handler regardless of which logger it was logged to)."""
        from voice_typer.server.log import reset, setup_logging
        from voice_typer.server.security import PIIRedactionFilter

        reset()
        try:
            setup_logging(tmp_path)
            vt_root = logging.getLogger("voice_typer")
            assert vt_root.handlers, "setup_logging did not install any handlers"
            for h in vt_root.handlers:
                pii_on_handler = [f for f in h.filters if isinstance(f, PIIRedactionFilter)]
                assert pii_on_handler, (
                    f"UE-4-F15 / XV-130: handler {h!r} must have a "
                    f"PIIRedactionFilter attached (handler-only attachment)"
                )
        finally:
            reset()
