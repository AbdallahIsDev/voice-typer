"""Tests for thesub-findings fixed in the logging package"""

from __future__ import annotations

import logging
import logging.handlers
import os

import pytest

# NOTE: the ``PIIRedactionFilter`` import is deferred to inside tests so


@pytest.fixture(autouse=True)
def _restore_logging_state():
    """Snapshot + restore the ``voice_typer`` logger + true root state."""
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


# _BubbleLevelExclusionFilter hybrid check ──────────────────


class TestUe4F6BubbleFilterHybridCheck:
    """``_BubbleLevelExclusionFilter.filter`` checks"""

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
        """WARNING+ records are always kept, even if"""
        from voice_typer.server.log import _BubbleLevelExclusionFilter

        f = _BubbleLevelExclusionFilter()
        record = self._make_record(logging.WARNING, "bubble_level handler crashed")
        assert f.filter(record) is True

    def test_error_record_with_args_kept(self):
        """ERROR+ records with args are kept unconditionally."""
        from voice_typer.server.log import _BubbleLevelExclusionFilter

        f = _BubbleLevelExclusionFilter()
        record = self._make_record(logging.ERROR, "bubble_level %s", ("crash",))
        assert f.filter(record) is True

    def test_debug_no_args_marker_in_template_dropped(self):
        """hot path: DEBUG record with no args and the marker"""
        from voice_typer.server.log import _BubbleLevelExclusionFilter

        f = _BubbleLevelExclusionFilter()
        record = self._make_record(logging.DEBUG, "bubble_level push")
        assert f.filter(record) is False

    def test_debug_no_args_marker_not_in_template_kept(self):
        """DEBUG record with no args and no marker in the"""
        from voice_typer.server.log import _BubbleLevelExclusionFilter

        f = _BubbleLevelExclusionFilter()
        record = self._make_record(logging.DEBUG, "audio chunk processed")
        assert f.filter(record) is True

    def test_debug_with_args_marker_in_template_dropped(self):
        """record if the marker is in the raw template (the args would be"""
        from voice_typer.server.log import _BubbleLevelExclusionFilter

        f = _BubbleLevelExclusionFilter()
        record = self._make_record(logging.DEBUG, "bubble_level %s", ("payload",))
        assert f.filter(record) is False

    def test_debug_with_args_marker_in_args_interpolation_dropped(self):
        """fallback: when args ARE present and the marker is"""
        from voice_typer.server.log import _BubbleLevelExclusionFilter

        f = _BubbleLevelExclusionFilter()
        # Template has no marker; args interpolate to produce the marker.
        record = self._make_record(logging.DEBUG, "event=%s", ("bubble_level",))
        assert f.filter(record) is False, (
            "filter must fall back to getMessage() when args are "
            "present and the marker appears only in the substituted output"
        )

    def test_debug_with_args_marker_not_present_kept(self):
        """when args ARE present and the marker is in neither"""
        from voice_typer.server.log import _BubbleLevelExclusionFilter

        f = _BubbleLevelExclusionFilter()
        record = self._make_record(logging.DEBUG, "chunk=%d", (42,))
        assert f.filter(record) is True

    def test_no_args_path_does_not_call_get_message(self, monkeypatch):
        """performance contract: when ``record.args`` is empty,"""
        from voice_typer.server.log import _BubbleLevelExclusionFilter

        f = _BubbleLevelExclusionFilter()
        record = self._make_record(logging.DEBUG, "bubble_level push")

        def boom(*_a, **_kw):
            raise AssertionError(
                "filter called getMessage() on a no-args record; the "
                "hybrid check should use record.msg directly to avoid the "
                "format-string substitution cost on the hot path."
            )

        monkeypatch.setattr(record, "getMessage", boom)
        # Should not raise, the no-args path uses record.msg, not getMessage().
        assert f.filter(record) is False


class TestUe4F8QuietFileHandlerLevel:
    """``setup_logging(quiet=True)`` lowers BOTH the root"""

    def _file_handler(self) -> logging.Handler:
        from voice_typer.server.log import _SecureTruncatingFileHandler

        handlers = logging.getLogger("voice_typer").handlers
        secure = [h for h in handlers if isinstance(h, _SecureTruncatingFileHandler)]
        assert secure, f"no _SecureTruncatingFileHandler installed; got {handlers!r}"
        return secure[0]

    def test_quiet_lowers_file_handler_to_warning(self, tmp_path, clean_env):
        """``quiet=True`` sets the file handler level to"""
        from voice_typer.server.log import reset, setup_logging

        reset()
        try:
            setup_logging(tmp_path, quiet=True)
            assert self._file_handler().level == logging.WARNING, (
                f"file handler level must be WARNING when quiet=True; "
                f"got {logging.getLevelName(self._file_handler().level)}"
            )
        finally:
            reset()

    def test_default_file_handler_level_is_info(self, tmp_path, clean_env):
        """default (no flags) keeps the file handler at INFO."""
        from voice_typer.server.log import reset, setup_logging

        reset()
        try:
            setup_logging(tmp_path)
            assert self._file_handler().level == logging.INFO, (
                f"default file handler level must be INFO; got {logging.getLevelName(self._file_handler().level)}"
            )
        finally:
            reset()

    def test_debug_raises_file_handler_to_debug(self, tmp_path, clean_env):
        """``debug=True`` raises the file handler to DEBUG"""
        from voice_typer.server.log import reset, setup_logging

        reset()
        try:
            setup_logging(tmp_path, debug=True)
            assert self._file_handler().level == logging.DEBUG, (
                f"debug=True should raise file handler to DEBUG; got {logging.getLevelName(self._file_handler().level)}"
            )
        finally:
            reset()

    def test_quiet_takes_precedence_over_debug(self, tmp_path, clean_env):
        """when BOTH ``quiet=True`` AND ``debug=True`` are"""
        from voice_typer.server.log import reset, setup_logging

        reset()
        try:
            setup_logging(tmp_path, debug=True, quiet=True)
            assert self._file_handler().level == logging.WARNING, (
                f"quiet=True should take precedence over debug=True; "
                f"got {logging.getLevelName(self._file_handler().level)}"
            )
        finally:
            reset()


class TestUe4F9SecureHandlerDedup:
    """the ``setup_logging`` idempotency check uses"""

    def test_setup_logging_idempotent_with_secure_handler(self, tmp_path, clean_env):
        """Calling ``setup_logging`` twice does NOT add a second"""
        from voice_typer.server.log import (
            _SecureTruncatingFileHandler,
            reset,
            setup_logging,
        )

        reset()
        try:
            setup_logging(tmp_path)
            before = [
                h for h in logging.getLogger("voice_typer").handlers if isinstance(h, _SecureTruncatingFileHandler)
            ]
            setup_logging(tmp_path)
            after = [
                h for h in logging.getLogger("voice_typer").handlers if isinstance(h, _SecureTruncatingFileHandler)
            ]
            assert len(before) == 1
            assert len(after) == 1, (
                f"setup_logging should not install a duplicate "
                f"_SecureTruncatingFileHandler; got {len(after)} after second call"
            )
        finally:
            reset()

    def test_stock_rotating_handler_does_not_count_as_secure(self, tmp_path, clean_env):
        """next ``setup_logging`` call still installs the secure handler"""
        from voice_typer.server.log import (
            _SecureTruncatingFileHandler,
            reset,
            setup_logging,
        )

        reset()
        try:
            # Install a stock RotatingFileHandler on the voice_typer logger
            stock_log = tmp_path / "stock.log"
            stock = logging.handlers.RotatingFileHandler(stock_log)
            logging.getLogger("voice_typer").addHandler(stock)

            setup_logging(tmp_path)

            secure_handlers = [
                h for h in logging.getLogger("voice_typer").handlers if isinstance(h, _SecureTruncatingFileHandler)
            ]
            assert len(secure_handlers) == 1, (
                f"setup_logging must install the _SecureTruncatingFileHandler "
                f"even when a stock RotatingFileHandler is already present (the "
                f"stock handler does NOT satisfy the secure-handler dedup check); "
                f"got {len(secure_handlers)} secure handlers"
            )
        finally:
            # Close the stock handler so its FD doesn't leak.
            with __import__("contextlib").suppress(Exception):
                stock.close()
            reset()


# _ensure_last_resort_redacted uses isinstance ────────────


class TestUe4F10LastResortIsinstance:
    """``_ensure_last_resort_redacted`` uses"""

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
                f"idempotency check failed; filter attached {len(last_resort.filters)} times, expected 1"
            )
        finally:
            last_resort.filters = saved_filters

    def test_idempotent_with_subclass(self):
        """a subclass of ``PIIRedactionFilter`` is recognized"""
        from voice_typer.server.log import _ensure_last_resort_redacted
        from voice_typer.server.security import PIIRedactionFilter

        class TestSubclassFilter(PIIRedactionFilter):
            """Subclass named differently from the parent, the"""

        last_resort = logging.lastResort
        saved_filters = list(last_resort.filters)
        try:
            last_resort.filters.clear()
            # First attach: a TestSubclassFilter instance.
            subclass_filter = TestSubclassFilter()
            last_resort.addFilter(subclass_filter)
            # Second attach: a PIIRedactionFilter instance. The isinstance
            parent_filter = self._make_filter()
            _ensure_last_resort_redacted(parent_filter)
            # Only the subclass filter should be attached; the parent
            assert len(last_resort.filters) == 1, (
                f"a PIIRedactionFilter subclass on lastResort "
                f"should satisfy the isinstance idempotency check; got "
                f"{len(last_resort.filters)} filters: {last_resort.filters!r}"
            )
        finally:
            last_resort.filters = saved_filters

    def test_unrelated_filter_does_not_block_attach(self):
        """a filter of a DIFFERENT class does not satisfy the"""
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
                f"an unrelated filter on lastResort should NOT "
                f"block the PIIRedactionFilter from being attached; got "
                f"{last_resort.filters!r}"
            )
        finally:
            last_resort.filters = saved_filters


class TestUe4F13LockFailureNoPathLeak:
    """when ``_acquire_rotation_lock`` fails, the DEBUG log"""

    def test_lock_failure_logs_exception_class_name_only(self, tmp_path, monkeypatch, caplog):
        """exception whose ``str()`` contains a path; assert the DEBUG log"""
        from voice_typer.server import log as log_module

        # Path containing a "home directory" sentinel string.
        sensitive_path = str(tmp_path / "home" / "user" / ".lausu" / "lock")
        # An exception whose str() includes the path.
        exc_with_path = OSError(f"[Errno 13] Permission denied: '{sensitive_path}'")

        # Replace the platform branch with one that raises our crafted
        def raising_open(*_a, **_kw):
            raise exc_with_path

        monkeypatch.setattr(os, "open", raising_open)

        handler = log_module._SecureTruncatingFileHandler(tmp_path / "ue17.log", maxBytes=128, backupCount=0)
        try:
            with caplog.at_level(logging.DEBUG, logger=log_module.log.name):
                lock_fd = handler._acquire_rotation_lock()
            assert lock_fd is None, (
                "test setup failed: _acquire_rotation_lock should return None when the underlying open() raises"
            )
        finally:
            with __import__("contextlib").suppress(Exception):
                handler.close()

        # The DEBUG log must NOT contain the sensitive path. It SHOULD
        debug_records = [r for r in caplog.records if r.levelno == logging.DEBUG and "[LOG-SETUP]" in r.message]
        assert debug_records, "expected a DEBUG log from _acquire_rotation_lock on failure"
        rendered = debug_records[0].getMessage()
        assert sensitive_path not in rendered, (
            f"lock-failure DEBUG log leaks the lock file path "
            f"(which contains the user's home directory); got: {rendered!r}"
        )
        assert "OSError" in rendered, (
            f"lock-failure DEBUG log must include the exception class name (``OSError``); got: {rendered!r}"
        )


# (behavioural): PII filter attached to handlers only ────────────────


class TestUe4F15PiiFilterHandlerOnlyAttachment:
    """behavioural guard: ``setup_logging`` attaches the PII"""

    def test_pii_filter_not_attached_to_voice_typer_root(self, tmp_path, clean_env):
        """the ``voice_typer`` root logger has NO"""
        from voice_typer.server.log import reset, setup_logging
        from voice_typer.server.security import PIIRedactionFilter

        reset()
        try:
            setup_logging(tmp_path)
            vt_root = logging.getLogger("voice_typer")
            pii_filters_on_logger = [f for f in vt_root.filters if isinstance(f, PIIRedactionFilter)]
            assert pii_filters_on_logger == [], (
                f"PIIRedactionFilter must NOT be attached "
                f"to the voice_typer root logger (handler-only attachment); "
                f"got {pii_filters_on_logger!r}"
            )
        finally:
            reset()

    def test_pii_filter_attached_to_handlers(self, tmp_path, clean_env):
        """each handler on the ``voice_typer`` logger"""
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
                    f"handler {h!r} must have a PIIRedactionFilter attached (handler-only attachment)"
                )
        finally:
            reset()
