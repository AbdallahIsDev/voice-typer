"""Regression test for ``prewarm.log`` handler must have PIIRedactionFilter attached.

The prewarm pipeline runs detached from the main app and writes to a
dedicated ``prewarm.log`` (next to ``voice-typer.log``).  found
that this handler was missing the three filters the main log handler
attaches:

  - ``_SessionFilter`` — injects ``session_id`` for cross-process log
    correlation (prewarm is a separate process; without this, its log
    lines can't be tied back to the parent app session).
  - ``PIIRedactionFilter`` — scrubs PII (emails, phone numbers, SSNs,
    credit-card numbers, API keys, bearer tokens) from log messages and
    tracebacks.  Without this, ``prewarm.log`` leaks whatever the
    prewarm code happened to log.
  - ``_BubbleLevelExclusionFilter`` — keeps high-frequency
    ``bubble_level`` events out of the rotating file (ADR-0020 §11).

These tests pin the filter attachment so a future refactor that drops
one of them fails loudly.
"""

from __future__ import annotations

import contextlib
import logging
import logging.handlers
from pathlib import Path

import pytest

# ─── Test isolation ────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _restore_logging_state():
    """Snapshot and restore the ``voice_typer`` logger state.

    ``_setup_logging`` mutates the ``voice_typer`` root logger (adds
    handlers, may set level).  Without snapshot/restore, the prewarm
    handler (with an open file descriptor on ``prewarm.log``) would
    leak across tests and break test isolation — and on Windows would
    hold a lock on the file.
    """
    vt_root = logging.getLogger("voice_typer")
    saved_vt_handlers = list(vt_root.handlers)
    saved_vt_filters = list(vt_root.filters)
    saved_vt_level = vt_root.level
    true_root = logging.getLogger()
    saved_true_handlers = list(true_root.handlers)

    yield

    # Close any handlers we created so the prewarm.log file isn't
    # locked and the file descriptor is released.
    for h in vt_root.handlers:
        if h not in saved_vt_handlers:
            with contextlib.suppress(Exception):
                h.close()
    vt_root.handlers = saved_vt_handlers
    vt_root.filters = saved_vt_filters
    vt_root.setLevel(saved_vt_level)
    true_root.handlers = saved_true_handlers


# ─── Helpers ──────────────────────────────────────────────────────────────


def _prewarm_handlers() -> list[logging.Handler]:
    """Return all ``RotatingFileHandler`` instances whose target ends with
    ``prewarm.log`` — i.e. the handler(s) added by
    ``prewarm.logging_setup._setup_logging``.
    """
    vt_root = logging.getLogger("voice_typer")
    return [
        h
        for h in vt_root.handlers
        if isinstance(h, logging.handlers.RotatingFileHandler) and Path(h.baseFilename).name == "prewarm.log"
    ]


def _setup_prewarm_to_tmp(tmp_path: Path, monkeypatch) -> None:
    """Run ``_setup_logging`` with config_dir pointed at ``tmp_path``.

    Stubs ``log.setup_logging`` (the shared main-app setup) so the test
    only exercises the prewarm-specific handler attachment — we don't
    need a real ``voice-typer.log`` file to verify the prewarm handler's
    filter chain.
    """
    from voice_typer.server import _paths
    from voice_typer.server.prewarm import logging_setup

    monkeypatch.setattr(_paths, "config_dir", lambda: tmp_path)
    # Stub the shared main-app setup so we don't create voice-typer.log
    # in the tmp dir (which would add a second RotatingFileHandler and
    # confuse the prewarm-handler discovery).
    monkeypatch.setattr(
        "voice_typer.server.log.setup_logging",
        lambda *args, **kwargs: "deadbeef",
        raising=True,
    )
    logging_setup._setup_logging()


# ─── Tests ────────────────────────────────────────────────────────────────


class TestPrewarmLogFilter:
    """the prewarm.log handler must carry the same filters as the
    main log handler."""

    def test_prewarm_handler_has_pii_filter(self, tmp_path, monkeypatch):
        """PIIRedactionFilter must be attached to the prewarm.log handler.

        Without it, PII (emails, phone numbers, API keys) logged by the
        prewarm pipeline flows unredacted into ``prewarm.log``, which is
        surfaced to users via the About-page "open prewarm.log" button.
        """
        _setup_prewarm_to_tmp(tmp_path, monkeypatch)

        prewarm_handlers = _prewarm_handlers()
        assert prewarm_handlers, (
            "no prewarm.log handler found on voice_typer root logger after _setup_logging() — the fix not applied?"
        )
        for h in prewarm_handlers:
            filter_types = [type(f).__name__ for f in h.filters]
            assert "PIIRedactionFilter" in filter_types, (
                f"PIIRedactionFilter missing from prewarm handler filters: {filter_types}"
            )

    def test_prewarm_handler_has_session_filter(self, tmp_path, monkeypatch):
        """``_SessionFilter`` must be attached so the session_id bracket
        appears in prewarm.log lines, enabling cross-process correlation
        with voice-typer.log.
        """
        _setup_prewarm_to_tmp(tmp_path, monkeypatch)

        prewarm_handlers = _prewarm_handlers()
        assert prewarm_handlers
        for h in prewarm_handlers:
            filter_types = [type(f).__name__ for f in h.filters]
            assert "_SessionFilter" in filter_types or "SessionFilter" in filter_types, (
                f"_SessionFilter missing from prewarm handler filters: {filter_types}"
            )

    def test_prewarm_handler_has_bubble_exclusion_filter(self, tmp_path, monkeypatch):
        """``_BubbleLevelExclusionFilter`` must be attached so
        high-frequency ``bubble_level`` events don't fill the prewarm
        log buffer (ADR-0020 §11).
        """
        _setup_prewarm_to_tmp(tmp_path, monkeypatch)

        prewarm_handlers = _prewarm_handlers()
        assert prewarm_handlers
        for h in prewarm_handlers:
            filter_types = [type(f).__name__ for f in h.filters]
            assert "_BubbleLevelExclusionFilter" in filter_types or "BubbleLevelExclusionFilter" in filter_types, (
                f"_BubbleLevelExclusionFilter missing from prewarm handler filters: {filter_types}"
            )

    def test_prewarm_handler_keeps_namespace_filter(self, tmp_path, monkeypatch):
        """The original ``logging.Filter("voice_typer.server.prewarm")``
        must be preserved so the prewarm.log file still only contains
        prewarm-namespace records (that's the file's whole purpose).
        """
        _setup_prewarm_to_tmp(tmp_path, monkeypatch)

        prewarm_handlers = _prewarm_handlers()
        assert prewarm_handlers
        for h in prewarm_handlers:
            # Find the plain logging.Filter with name="voice_typer.server.prewarm".
            namespace_filters = [
                f
                for f in h.filters
                if isinstance(f, logging.Filter) and getattr(f, "name", "") == "voice_typer.server.prewarm"
            ]
            assert namespace_filters, (
                "prewarm namespace filter "
                "(logging.Filter('voice_typer.server.prewarm')) missing — "
                "prewarm.log would now contain non-prewarm records"
            )

    def test_prewarm_handler_uses_file_formatter(self, tmp_path, monkeypatch):
        """The handler's formatter must be ``_FileFormatter`` (the shared
        plain-text file formatter), not a bare ``logging.Formatter``.

        Without this, prewarm.log lines lack the session_id bracket and
        component field that voice-typer.log lines have — making the two
        logs visually inconsistent and harder to grep together.
        """
        from voice_typer.server.log import _FileFormatter

        _setup_prewarm_to_tmp(tmp_path, monkeypatch)

        prewarm_handlers = _prewarm_handlers()
        assert prewarm_handlers
        for h in prewarm_handlers:
            assert isinstance(h.formatter, _FileFormatter), (
                f"prewarm handler formatter is {type(h.formatter).__name__}, expected _FileFormatter"
            )

    def test_prewarm_handler_rotation_policy_matches_main(self, tmp_path, monkeypatch):
        """The prewarm handler uses reduced 1 MiB × 1 rotation because the
        shared _SecureRotatingFileHandler already writes all prewarm records
        to voice-typer-prewarm.log (5 MiB × 5). The dedicated prewarm.log
        handler is kept for backwards-compat (UI 'Open Prewarm Log' button)
        but with reduced rotation since it's a strict subset of the shared
        log.
        """
        _setup_prewarm_to_tmp(tmp_path, monkeypatch)

        prewarm_handlers = _prewarm_handlers()
        assert prewarm_handlers
        for h in prewarm_handlers:
            assert h.maxBytes == 1 * 1024 * 1024, f"prewarm handler maxBytes is {h.maxBytes}, expected 1 MiB"
            assert h.backupCount == 1, f"prewarm handler backupCount is {h.backupCount}, expected 1"

    def test_prewarm_handler_redacts_pii_in_messages(self, tmp_path, monkeypatch):
        """End-to-end: a log message containing an email actually gets
        redacted when written to prewarm.log.

        This catches the case where PIIRedactionFilter is attached but
        is somehow not effective (e.g. attached to the wrong logger
        level, or the message bypasses the filter via a child logger
        without propagation).
        """
        _setup_prewarm_to_tmp(tmp_path, monkeypatch)

        # We stubbed ``log.setup_logging`` so the voice_typer logger
        # level was never set to DEBUG by the shared setup.  Set it
        # explicitly so INFO records emitted below actually propagate
        # through to the prewarm handler.
        logging.getLogger("voice_typer").setLevel(logging.DEBUG)

        # Log a message via the prewarm logger.
        prewarm_logger = logging.getLogger("voice_typer.server.prewarm")
        prewarm_logger.info("[PREWARM] user email is leak@example.com")

        # Flush handlers so the message reaches disk.
        for h in _prewarm_handlers():
            h.flush()

        prewarm_log_path = tmp_path / "prewarm.log"
        assert prewarm_log_path.exists(), "prewarm.log was not created"
        content = prewarm_log_path.read_text(encoding="utf-8", errors="replace")

        # The email address must NOT appear verbatim — PIIRedactionFilter
        # replaces it with the ``[EMAIL]`` token.
        assert "leak@example.com" not in content, f"PII email leaked into prewarm.log:\n{content}"
        # The redaction token should be present (proving the filter
        # actually fired, not just that the message was dropped).
        assert "[EMAIL]" in content, f"PIIRedactionFilter did not emit [EMAIL] token:\n{content}"
