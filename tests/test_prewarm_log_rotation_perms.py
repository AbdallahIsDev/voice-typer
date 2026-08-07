"""the fix-4 regression: prewarm.log handler must be a
``_SecureTruncatingFileHandler`` (NOT the stock
``logging.handlers.RotatingFileHandler``) so the single-file
``chmod 0o600`` guarantee (the fix) and the inter-process truncation
lock extend to ``prewarm.log``.

Pre-fix, ``prewarm/logging_setup.py`` constructed the prewarm.log
handler as a stock ``logging.handlers.RotatingFileHandler``. The
initial ``os.chmod(prewarm_log, 0o600)`` at setup time locked the
file down, but the stock ``RotatingFileHandler.doRollover`` has NO
post-rotation chmod hook — after the first 5 MiB rotation the new
active ``prewarm.log`` was created at ``0o666 & ~umask = 0o644``
(world-readable on POSIX), leaking dictated-text-adjacent prewarm
traces to co-located users on multi-user systems. The single-file
policy (truncate-in-place, ``backupCount=0``) keeps the SAME file and
re-asserts 0o600 inside the inter-process lock after every truncation.

The existing ``test_logging_rotation_perms.py`` ONLY tests
``voice-typer.log`` perms — ``prewarm.log`` had NO perms regression
test. This file mirrors
``test_logging_rotation_perms.py::test_truncate_in_place_keeps_mode_0o600``
but targets ``prewarm.log``.

POSIX-only — on Windows the file mode is governed by ACLs, not the
POSIX mode bits (the chmod is a documented best-effort no-op there).
"""

from __future__ import annotations

import contextlib
import logging
import logging.handlers
import os
import stat
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    sys.platform == "win32",
    reason="the fix-4 is POSIX-only — Windows log file perms are governed by ACLs, not the POSIX mode bits",
)


@pytest.fixture(autouse=True)
def _restore_logging_state():
    """Snapshot + restore the ``voice_typer`` logger state.

    Mirrors the autouse fixture in ``tests/test_prewarm_logging_filter.py``
    so a ``_setup_logging`` call inside a test does not pollute subsequent
    tests in the same process.
    """
    vt_root = logging.getLogger("voice_typer")
    saved_handlers = list(vt_root.handlers)
    saved_filters = list(vt_root.filters)
    saved_level = vt_root.level
    true_root = logging.getLogger()
    saved_true_handlers = list(true_root.handlers)
    from voice_typer.server import log as vt_log

    saved_session_id = vt_log._session_id
    yield
    # Close any handlers we created so the prewarm.log file isn't locked
    # and the file descriptor is released.
    for h in vt_root.handlers:
        if h not in saved_handlers:
            with contextlib.suppress(Exception):
                h.close()
    vt_root.handlers = saved_handlers
    vt_root.filters = saved_filters
    vt_root.setLevel(saved_level)
    true_root.handlers = saved_true_handlers
    vt_log._session_id = saved_session_id
    vt_log.close_devnull_files()


def _setup_prewarm_to_tmp(tmp_path: Path, monkeypatch) -> None:
    """Run ``prewarm.logging_setup._setup_logging`` with config_dir pointed
    at ``tmp_path``, stubbing the shared main-app setup so only the
    prewarm.log handler is installed (no voice-typer.log handler to
    confuse truncate assertions).
    """
    from voice_typer.server import _paths
    from voice_typer.server.prewarm import logging_setup

    monkeypatch.setattr(_paths, "config_dir", lambda: tmp_path)
    # Stub the shared main-app setup so we don't create voice-typer.log
    # in the tmp dir — we only want the prewarm.log handler to exercise
    # the truncate-perms guarantee.
    monkeypatch.setattr(
        "voice_typer.server.log.setup_logging",
        lambda *args, **kwargs: "deadbeef",
        raising=True,
    )
    logging_setup._setup_logging(debug=True)


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


def test_prewarm_handler_is_secure_truncating_subclass(tmp_path, monkeypatch):
    """the ``prewarm.logging_setup._setup_logging`` installs a
    ``_SecureTruncatingFileHandler`` for ``prewarm.log``, NOT the stock
    ``logging.handlers.RotatingFileHandler``. The subclass overrides
    ``doRollover`` to truncate in place, re-chmod the active log file to
    0o600, and acquire an inter-process lock.
    """
    from voice_typer.server import log as vt_log

    _setup_prewarm_to_tmp(tmp_path, monkeypatch)

    prewarm_handlers = _prewarm_handlers()
    assert prewarm_handlers, (
        "no prewarm.log handler found on voice_typer root logger after _setup_logging() — the fix-4 fix not applied?"
    )
    for h in prewarm_handlers:
        assert isinstance(h, vt_log._SecureTruncatingFileHandler), (
            "the prewarm.log handler must be a _SecureTruncatingFileHandler "
            "(subclass that truncates in place, re-chmods to 0o600 AND acquires "
            "the inter-process lock), not the stock "
            "logging.handlers.RotatingFileHandler. Pre-fix the stock handler "
            "left the post-rotation active prewarm.log at 0o644 (world-readable)."
        )


def test_prewarm_truncate_in_place_keeps_mode_0o600(tmp_path, monkeypatch):
    """after a prewarm.log truncate-in-place fires, the active
    ``prewarm.log`` (the only file) is 0o600 and no numbered backup
    (``prewarm.log.1``) exists.

    Writes >5 MiB of records to a prewarm logger to force the truncate,
    then asserts the active ``prewarm.log`` mode is 0o600.
    """
    _setup_prewarm_to_tmp(tmp_path, monkeypatch)

    prewarm_log = tmp_path / "prewarm.log"
    assert prewarm_log.exists(), "prewarm.log was not created by _setup_logging()"
    # Sanity: initial mode is 0o600 (the explicit chmod at setup time).
    assert oct(stat.S_IMODE(os.stat(prewarm_log).st_mode)) == "0o600", (
        f"initial prewarm.log mode must be 0o600; got {oct(stat.S_IMODE(os.stat(prewarm_log).st_mode))}"
    )

    # Force a truncate-in-place by writing >5 MiB of records. Use a long
    # sentence per record so the PII redaction filter (which collapses
    # runs of identical chars to ``***``) doesn't shrink the payload.
    # 8 KiB per record -> ~700 records to hit 5 MiB.
    prewarm_logger = logging.getLogger("voice_typer.server.prewarm.rotation_test")
    prewarm_logger.setLevel(logging.DEBUG)
    payload = "the quick brown fox jumps over the lazy dog " * 180  # ~8 KiB
    assert len(payload) > 7000
    for _ in range(700):
        prewarm_logger.info(payload)

    # Flush all handlers so the truncate actually fires.
    for h in logging.getLogger("voice_typer").handlers:
        with contextlib.suppress(Exception):
            h.flush()

    # The active prewarm.log must still be 0o600 after the truncate.
    mode = stat.S_IMODE(os.stat(prewarm_log).st_mode)
    assert oct(mode) == "0o600", (
        f"the post-truncate prewarm.log mode must be 0o600 on POSIX; got {oct(mode)}. "
        "Pre-fix (stock RotatingFileHandler) this was 0o644 (world-readable) "
        "because the stock handler has no post-rotation chmod hook — the "
        "initial setup-time chmod did not survive the first 5 MiB rotation."
    )

    # Single-file policy: NO numbered backup (``prewarm.log.1``) exists.
    backup = tmp_path / "prewarm.log.1"
    assert not backup.exists(), (
        "single-file policy: prewarm.log.1 must NOT be created — prewarm.log "
        "truncates in place instead of rotating to numbered backups"
    )
