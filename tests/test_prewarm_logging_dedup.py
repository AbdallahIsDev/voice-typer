"""DJ-45: prewarm-logging dedup regression tests.

When the prewarm subprocess runs, it currently writes every prewarm log
line to BOTH ``prewarm.log`` (its dedicated sink) AND ``voice-typer.log``
(the shared app log). Each prewarm run emits ~hundreds of INFO lines
(per-file warming traces, cache-probe results, etc.), so the duplicate
write halves the per-line disk-write throughput and adds ~1 MiB of
duplicate content per run to ``voice-typer.log``.

DJ-45 introduces a ``prewarm_only`` flag on ``_setup_logging``:

- When ``True`` (prewarm subprocess), attach a ``_NotPrewarmFilter``
  exclusion filter to the shared ``voice-typer.log`` handler so prewarm
  records only land in ``prewarm.log``.
- When ``False`` (default — main app process), prewarm lines still flow
  to both files (preserving the "voice-typer.log is the complete record"
  contract).

Also adds a dedup check so a repeated ``_setup_logging`` call in the
same process does NOT stack multiple ``prewarm.log`` handlers (which
would multiply each prewarm line N times AND hold N file descriptors
on the file, locking it on Windows).

These tests pin both behaviours so a future revert fails loudly.
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
    handlers, attaches filters). Without snapshot/restore, the prewarm
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
    for h in vt_root.handlers:
        if h not in saved_vt_handlers:
            with contextlib.suppress(Exception):
                h.close()
    vt_root.handlers = saved_vt_handlers
    vt_root.filters = saved_vt_filters
    vt_root.setLevel(saved_vt_level)
    true_root.handlers = saved_true_handlers


# ─── Helpers ──────────────────────────────────────────────────────────────


def _vt_handlers(tmp_path: Path) -> list[logging.Handler]:
    """Return all ``RotatingFileHandler`` instances whose target ends with
    ``voice-typer.log`` — i.e. the shared main-app handler(s).
    """
    vt_root = logging.getLogger("voice_typer")
    return [
        h
        for h in vt_root.handlers
        if isinstance(h, logging.handlers.RotatingFileHandler) and Path(h.baseFilename).name == "voice-typer.log"
    ]


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


def _setup_prewarm_to_tmp(tmp_path: Path, monkeypatch, *, prewarm_only: bool = False) -> None:
    """Run ``_setup_logging`` with config_dir pointed at ``tmp_path``.

    Stubs the shared main-app setup with a minimal RotatingFileHandler
    install so the prewarm exclusion filter has something to attach to
    (the real ``log.setup_logging`` adds StreamHandlers and other
    filters that confuse the per-handler assertions).
    """
    from voice_typer.server import _paths
    from voice_typer.server.prewarm import logging_setup

    monkeypatch.setattr(_paths, "config_dir", lambda: tmp_path)

    def fake_shared_setup(log_dir, *, debug=False, quiet=False, port_mode=False):
        """Install a minimal RotatingFileHandler on the voice_typer root
        so the prewarm exclusion filter has something to attach to."""
        handler = logging.handlers.RotatingFileHandler(
            log_dir / "voice-typer.log",
            maxBytes=5 * 1024 * 1024,
            backupCount=5,
            encoding="utf-8",
            errors="backslashreplace",
        )
        handler.setLevel(logging.DEBUG if debug else logging.INFO)
        vt = logging.getLogger("voice_typer")
        # Avoid stacking duplicates if the test calls setup multiple times.
        if not any(
            isinstance(h, logging.handlers.RotatingFileHandler) and Path(h.baseFilename).name == "voice-typer.log"
            for h in vt.handlers
        ):
            vt.addHandler(handler)
        vt.setLevel(logging.DEBUG)
        return "deadbeef"

    monkeypatch.setattr(
        "voice_typer.server.log.setup_logging",
        fake_shared_setup,
        raising=True,
    )
    logging_setup._setup_logging(prewarm_only=prewarm_only)


# ─── Tests ────────────────────────────────────────────────────────────────


class TestPrewarmLoggingDedup:
    """DJ-45: prewarm lines must only go to prewarm.log when prewarm_only=True,
    and the prewarm handler must not be duplicated on repeated calls.
    """

    def test_prewarm_only_attaches_exclusion_filter_to_shared_handler(self, tmp_path, monkeypatch):
        """When ``prewarm_only=True``, the shared voice-typer.log handler
        must have a ``_NotPrewarmFilter`` attached so prewarm records are
        excluded from voice-typer.log.
        """
        _setup_prewarm_to_tmp(tmp_path, monkeypatch, prewarm_only=True)

        shared = _vt_handlers(tmp_path)
        assert shared, "no voice-typer.log handler found on voice_typer root"
        for h in shared:
            assert any(getattr(f, "_vt_not_prewarm", False) for f in h.filters), (
                f"DJ-45: shared voice-typer.log handler missing _NotPrewarmFilter "
                f"when prewarm_only=True; filters: {[type(f).__name__ for f in h.filters]}"
            )

    def test_prewarm_only_false_does_not_attach_exclusion_filter(self, tmp_path, monkeypatch):
        """When ``prewarm_only=False`` (default), the shared handler must NOT
        have the exclusion filter — prewarm lines should still flow to both
        files (preserving the "voice-typer.log is the complete record"
        contract for the main app process).
        """
        _setup_prewarm_to_tmp(tmp_path, monkeypatch, prewarm_only=False)

        shared = _vt_handlers(tmp_path)
        assert shared
        for h in shared:
            assert not any(getattr(f, "_vt_not_prewarm", False) for f in h.filters), (
                "DJ-45: _NotPrewarmFilter must NOT be attached when prewarm_only=False — "
                "the main app process must keep prewarm lines in voice-typer.log."
            )

    def test_prewarm_handler_dedup_does_not_stack(self, tmp_path, monkeypatch):
        """A repeated ``_setup_logging`` call must NOT add a second prewarm
        handler — the dedup check via ``_vt_prewarm = True`` should skip
        re-adding.
        """
        _setup_prewarm_to_tmp(tmp_path, monkeypatch)

        prewarm_count_before = sum(1 for h in _prewarm_handlers() if getattr(h, "_vt_prewarm", False))
        assert prewarm_count_before == 1, (
            f"DJ-45: expected exactly 1 prewarm handler after first call, got {prewarm_count_before}"
        )

        # Call again — should NOT add a second prewarm handler.
        from voice_typer.server.prewarm import logging_setup

        logging_setup._setup_logging()

        prewarm_count_after = sum(1 for h in _prewarm_handlers() if getattr(h, "_vt_prewarm", False))
        assert prewarm_count_after == 1, (
            f"DJ-45: prewarm handler count grew from 1 to {prewarm_count_after} "
            f"after second _setup_logging call — dedup check failed. This would "
            f"multiply each prewarm line N times in prewarm.log AND hold N file "
            f"descriptors on the file (locking it on Windows)."
        )

    def test_prewarm_only_routes_prewarm_lines_to_prewarm_log_only(self, tmp_path, monkeypatch):
        """End-to-end: with ``prewarm_only=True``, a prewarm log line lands
        in ``prewarm.log`` but NOT in ``voice-typer.log``.
        """
        _setup_prewarm_to_tmp(tmp_path, monkeypatch, prewarm_only=True)

        vt_root = logging.getLogger("voice_typer")
        vt_root.setLevel(logging.DEBUG)
        prewarm_logger = logging.getLogger("voice_typer.server.prewarm")
        app_logger = logging.getLogger("voice_typer.server.app")

        prewarm_logger.info("[PREWARM] dj45 prewarm-only test line")
        app_logger.info("[APP] dj45 app-side test line")

        for h in vt_root.handlers:
            h.flush()

        prewarm_log = tmp_path / "prewarm.log"
        assert prewarm_log.exists(), "prewarm.log was not created"
        prewarm_content = prewarm_log.read_text(encoding="utf-8", errors="replace")
        assert "[PREWARM] dj45 prewarm-only test line" in prewarm_content, (
            f"prewarm line missing from prewarm.log:\n{prewarm_content}"
        )
        assert "[APP] dj45 app-side test line" not in prewarm_content, (
            f"non-prewarm line leaked into prewarm.log:\n{prewarm_content}"
        )

        vt_log = tmp_path / "voice-typer.log"
        assert vt_log.exists(), "voice-typer.log was not created"
        vt_content = vt_log.read_text(encoding="utf-8", errors="replace")
        assert "[APP] dj45 app-side test line" in vt_content, f"app line missing from voice-typer.log:\n{vt_content}"
        assert "[PREWARM] dj45 prewarm-only test line" not in vt_content, (
            f"DJ-45: prewarm line leaked into voice-typer.log despite "
            f"prewarm_only=True — the _NotPrewarmFilter is not working:\n{vt_content}"
        )

    def test_prewarm_only_false_keeps_prewarm_lines_in_both(self, tmp_path, monkeypatch):
        """End-to-end: with ``prewarm_only=False``, a prewarm log line lands
        in BOTH ``prewarm.log`` AND ``voice-typer.log`` (the legacy
        "complete record" behaviour for the main app process).
        """
        _setup_prewarm_to_tmp(tmp_path, monkeypatch, prewarm_only=False)

        vt_root = logging.getLogger("voice_typer")
        vt_root.setLevel(logging.DEBUG)
        prewarm_logger = logging.getLogger("voice_typer.server.prewarm")
        prewarm_logger.info("[PREWARM] dj45 shared test line")
        for h in vt_root.handlers:
            h.flush()

        prewarm_log = tmp_path / "prewarm.log"
        vt_log = tmp_path / "voice-typer.log"
        assert prewarm_log.exists()
        assert vt_log.exists()

        prewarm_content = prewarm_log.read_text(encoding="utf-8", errors="replace")
        vt_content = vt_log.read_text(encoding="utf-8", errors="replace")
        assert "[PREWARM] dj45 shared test line" in prewarm_content
        assert "[PREWARM] dj45 shared test line" in vt_content, (
            f"DJ-45: with prewarm_only=False, prewarm line should still appear "
            f"in voice-typer.log (complete-record contract):\n{vt_content}"
        )

    def test_exclusion_filter_is_idempotent(self, tmp_path, monkeypatch):
        """Calling ``_setup_logging(prewarm_only=True)`` twice must NOT stack
        two ``_NotPrewarmFilter`` instances on the shared handler.
        """
        _setup_prewarm_to_tmp(tmp_path, monkeypatch, prewarm_only=True)
        from voice_typer.server.prewarm import logging_setup

        # Second call with prewarm_only=True — should not add a second filter.
        logging_setup._setup_logging(prewarm_only=True)

        shared = _vt_handlers(tmp_path)
        assert shared
        for h in shared:
            not_prewarm_count = sum(1 for f in h.filters if getattr(f, "_vt_not_prewarm", False))
            assert not_prewarm_count == 1, (
                f"DJ-45: stacked {not_prewarm_count} _NotPrewarmFilter instances "
                f"on the shared handler — should be exactly 1 (idempotent attach)."
            )
