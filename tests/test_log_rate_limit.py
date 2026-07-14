"""Tests for :mod:`voice_typer.server.log_rate_limit` (B-5).

Verifies the contract documented in
:func:`voice_typer.server.log_rate_limit.log_rate_limited`:

- The 1st occurrence logs at the configured level with ``exc_info=True``.
- Occurrences 2-99 log at ``DEBUG`` without ``exc_info``.
- The 100th occurrence logs at the configured level with ``exc_info=True``.
- Counters are per-message-key (distinct messages / explicit ``key``
  overrides get independent counters).
- The function is thread-safe under concurrent access.
- ``*args`` %-format arguments are forwarded to the configured-level
  call and rendered into the DEBUG fallback message.
"""

from __future__ import annotations

import logging
import threading
from unittest.mock import MagicMock

import pytest
from voice_typer.server import log_rate_limit
from voice_typer.server.log_rate_limit import log_rate_limited, reset


class FakeLogger:
    """Minimal logger stub that records ``log`` and ``debug`` calls.

    A plain :class:`unittest.mock.MagicMock` does not work here because
    ``MagicMock(spec=logging.Logger)`` excludes the ``name`` attribute
    (it is an *instance* attribute on :class:`logging.Logger`, not a
    class attribute, so ``dir(logging.Logger)`` omits it and the spec
    rejects access).  This stub exposes a string ``name`` plus two
    recording ``MagicMock`` methods so the tests can assert on call
    args/counts precisely.
    """

    def __init__(self, name: str = "voice_typer.test.fake") -> None:
        self.name = name
        self.log = MagicMock()
        self.debug = MagicMock()


@pytest.fixture(autouse=True)
def _isolate_counters():
    """Clear the module-level counter dict before and after each test.

    Without this, test execution order would determine pass/fail because
    counters persist across calls.
    """
    reset()
    yield
    reset()


# ── 1. First occurrence ───────────────────────────────────────────────


def test_first_occurrence_logs_at_configured_level_with_exc_info():
    """1st call must log at *level* with exc_info forwarded."""
    logger = FakeLogger()
    try:
        raise RuntimeError("boom")
    except RuntimeError:
        log_rate_limited(
            logger,
            logging.ERROR,
            "test message",
            exc_info=True,
            every_n=100,
        )

    # The configured-level path goes through logger.log(level, ...).
    logger.log.assert_called_once()
    call = logger.log.call_args
    assert call.args[0] == logging.ERROR
    assert call.args[1] == "test message"
    assert call.kwargs.get("exc_info") is True
    # DEBUG path must NOT have fired on the first occurrence.
    logger.debug.assert_not_called()


def test_first_occurrence_forwards_args_and_kwargs():
    """Positional %-format args and extra kwargs are forwarded to log()."""
    logger = FakeLogger()
    log_rate_limited(
        logger,
        logging.WARNING,
        "value=%d code=%s",
        42,
        "X",
        every_n=100,
        extra={"tag": "B-5"},
    )

    logger.log.assert_called_once_with(
        logging.WARNING,
        "value=%d code=%s",
        42,
        "X",
        exc_info=False,
        extra={"tag": "B-5"},
    )
    logger.debug.assert_not_called()


# ── 2. Occurrences 2..N-1 ────────────────────────────────────────────


def test_occurrences_2_through_99_log_at_debug_without_exc_info():
    """Occurrences 2..99 must log at DEBUG and must not include exc_info."""
    logger = FakeLogger()
    msg = "rate-limit-test"

    # 99 calls → counter values 1..99 → 1st logs at ERROR (1) plus 98
    # suppressed occurrences (2..99) that must each go to DEBUG.
    for _ in range(99):
        log_rate_limited(logger, logging.ERROR, msg, exc_info=True, every_n=100)

    # 1 ERROR call (the 1st occurrence) + 98 DEBUG calls (2..99).
    assert logger.log.call_count == 1
    assert logger.debug.call_count == 98

    # Every DEBUG call must omit exc_info (the expensive traceback).
    for call in logger.debug.call_args_list:
        # The DEBUG branch always passes (format, rendered_msg, count)
        # positionally and never sets exc_info in kwargs.
        assert "exc_info" not in call.kwargs
        fmt, rendered, count = call.args
        assert fmt == "%s (suppressed occurrence %d)"
        assert rendered == msg
        # Suppressed-occurrence index runs 2..99 inclusive.
        assert 2 <= count <= 99


def test_suppressed_occurrence_renders_format_args():
    """When *args are passed, the DEBUG fallback renders the message."""
    logger = FakeLogger()

    # First call (1st occurrence) → logger.log at ERROR with raw msg + args.
    log_rate_limited(
        logger, logging.ERROR, "chunk %d failed", 7, every_n=100, exc_info=True
    )
    logger.log.assert_called_once_with(
        logging.ERROR, "chunk %d failed", 7, exc_info=True
    )

    # Second call (suppressed) → DEBUG with the rendered message.
    log_rate_limited(
        logger, logging.ERROR, "chunk %d failed", 7, every_n=100, exc_info=True
    )
    logger.debug.assert_called_once_with(
        "%s (suppressed occurrence %d)", "chunk 7 failed", 2
    )


# ── 3. Nth occurrence ────────────────────────────────────────────────


def test_occurrence_100_logs_at_configured_level_with_exc_info():
    """The 100th occurrence must log at *level* with exc_info."""
    logger = FakeLogger()
    msg = "hundredth-test"

    for _ in range(99):
        log_rate_limited(logger, logging.ERROR, msg, exc_info=True, every_n=100)
    # 99 calls so far: 1st logged at ERROR, 2-99 logged at DEBUG (98 calls).
    assert logger.log.call_count == 1
    assert logger.debug.call_count == 98

    logger.log.reset_mock()
    logger.debug.reset_mock()

    # 100th call → configured level with exc_info.
    log_rate_limited(logger, logging.ERROR, msg, exc_info=True, every_n=100)
    logger.log.assert_called_once_with(
        logging.ERROR, msg, exc_info=True
    )
    logger.debug.assert_not_called()


def test_occurrence_101_logs_at_debug_again():
    """After the 100th, the 101st goes back to DEBUG."""
    logger = FakeLogger()
    msg = "post-hundred-test"

    for _ in range(100):
        log_rate_limited(logger, logging.ERROR, msg, exc_info=True, every_n=100)
    logger.log.reset_mock()
    logger.debug.reset_mock()

    log_rate_limited(logger, logging.ERROR, msg, exc_info=True, every_n=100)
    logger.debug.assert_called_once_with(
        "%s (suppressed occurrence %d)", msg, 101
    )
    logger.log.assert_not_called()


def test_occurrence_200_logs_at_configured_level():
    """The 200th occurrence (next Nth after 100) logs at *level*."""
    logger = FakeLogger()
    msg = "two-hundredth-test"

    for _ in range(199):
        log_rate_limited(logger, logging.ERROR, msg, exc_info=True, every_n=100)
    logger.log.reset_mock()
    logger.debug.reset_mock()

    log_rate_limited(logger, logging.ERROR, msg, exc_info=True, every_n=100)
    logger.log.assert_called_once_with(logging.ERROR, msg, exc_info=True)
    logger.debug.assert_not_called()


# ── 4. Per-message-key counters ──────────────────────────────────────


def test_distinct_messages_get_independent_counters():
    """Two different message strings must not share a counter."""
    logger = FakeLogger()

    # Fire message A twice — second should be suppressed to DEBUG.
    log_rate_limited(logger, logging.ERROR, "message A", every_n=100)
    log_rate_limited(logger, logging.ERROR, "message A", every_n=100)
    assert logger.log.call_count == 1  # only the 1st
    assert logger.debug.call_count == 1  # the 2nd was suppressed

    # First occurrence of message B must log at the configured level,
    # independent of how many times message A has fired.
    logger.log.reset_mock()
    log_rate_limited(logger, logging.ERROR, "message B", every_n=100)
    logger.log.assert_called_once_with(logging.ERROR, "message B", exc_info=False)


def test_explicit_key_buckets_dynamic_messages():
    """An explicit ``key=`` overrides the message-based counter so
    dynamic message texts can be bucketed under a single counter."""
    logger = FakeLogger()

    # Same key, different message text → should share a counter.
    log_rate_limited(
        logger, logging.ERROR, "chunk %d failed", 1, key="chunk-failed", every_n=100
    )
    log_rate_limited(
        logger, logging.ERROR, "chunk %d failed", 2, key="chunk-failed", every_n=100
    )

    # 1st → log at level; 2nd → DEBUG.
    assert logger.log.call_count == 1
    assert logger.debug.call_count == 1


def test_same_message_under_different_keys_get_independent_counters():
    """Two call sites that happen to share a message string but pass
    different keys must get independent counters."""
    logger = FakeLogger()
    msg = "shared message text"

    log_rate_limited(logger, logging.ERROR, msg, key="site-A", every_n=100)
    log_rate_limited(logger, logging.ERROR, msg, key="site-A", every_n=100)
    log_rate_limited(logger, logging.ERROR, msg, key="site-B", every_n=100)

    # site-A: 1st logged at level, 2nd suppressed → 1 log + 1 debug.
    # site-B: 1st logged at level → 1 more log.
    assert logger.log.call_count == 2
    assert logger.debug.call_count == 1


# ── 5. Thread safety ────────────────────────────────────────────────


def test_concurrent_calls_do_not_crash_and_total_count_is_consistent():
    """Many threads calling concurrently must not raise and must produce
    a final counter equal to the total number of calls."""
    logger = FakeLogger(name="voice_typer.test.concurrent")
    msg = "concurrent-test"
    n_threads = 16
    calls_per_thread = 200

    barrier = threading.Barrier(n_threads)

    def worker():
        barrier.wait()
        for _ in range(calls_per_thread):
            try:
                log_rate_limited(logger, logging.ERROR, msg, exc_info=True, every_n=100)
            except Exception:
                pytest.fail("log_rate_limited raised under contention")

    threads = [threading.Thread(target=worker) for _ in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    expected_total = n_threads * calls_per_thread
    counter_key = (logger.name, msg)
    assert log_rate_limit._RATE_LIMIT_COUNTS[counter_key] == expected_total

    # The configured-level call should have fired roughly
    # (1 + every 100th) × n_threads times.  We don't assert an exact
    # count here because thread interleaving can cause two threads to
    # observe the same count value and both log at level — which is the
    # explicitly-documented acceptable trade-off.  The invariant we DO
    # assert is that the configured-level path fired at least once
    # (the very first call) and never more than 2× the expected count
    # (a generous upper bound that catches gross lock failures).
    expected_level_calls_lower_bound = n_threads  # ≥1 per thread
    expected_level_calls_upper_bound = 2 * (
        n_threads * (1 + calls_per_thread // 100)
    )
    actual_level_calls = logger.log.call_count
    assert actual_level_calls >= expected_level_calls_lower_bound
    assert actual_level_calls <= expected_level_calls_upper_bound


# ── 6. Edge cases ────────────────────────────────────────────────────


def test_every_n_le_1_disables_rate_limiting():
    """``every_n=1`` means every call logs at the configured level."""
    logger = FakeLogger()
    for _ in range(50):
        log_rate_limited(logger, logging.ERROR, "no-limit", every_n=1, exc_info=True)
    assert logger.log.call_count == 50
    logger.debug.assert_not_called()


def test_every_n_zero_logs_only_first():
    """``every_n=0`` (or negative) disables the modulo branch — only the
    1st occurrence logs at the configured level."""
    logger = FakeLogger()
    for _ in range(50):
        log_rate_limited(logger, logging.ERROR, "first-only", every_n=0)
    assert logger.log.call_count == 1
    assert logger.debug.call_count == 49


def test_reset_clears_counters():
    """After reset(), the next call is treated as the 1st occurrence."""
    logger = FakeLogger()
    msg = "reset-test"

    log_rate_limited(logger, logging.ERROR, msg, every_n=100)
    log_rate_limited(logger, logging.ERROR, msg, every_n=100)  # suppressed
    assert logger.log.call_count == 1
    assert logger.debug.call_count == 1

    reset()

    logger.log.reset_mock()
    logger.debug.reset_mock()
    # After reset, the next call is the 1st again.
    log_rate_limited(logger, logging.ERROR, msg, every_n=100)
    logger.log.assert_called_once_with(logging.ERROR, msg, exc_info=False)
    logger.debug.assert_not_called()


def test_level_is_forwarded_as_configured():
    """The caller chooses the level — WARNING, INFO, CRITICAL all work."""
    logger = FakeLogger()
    log_rate_limited(logger, logging.WARNING, "warn-test", every_n=100)
    logger.log.assert_called_once_with(
        logging.WARNING, "warn-test", exc_info=False
    )


def test_logger_name_is_part_of_counter_key():
    """Two loggers with the same message get independent counters."""
    logger_a = FakeLogger(name="voice_tyer.test.a")
    logger_b = FakeLogger(name="voice_tyer.test.b")
    msg = "shared-by-name"

    log_rate_limited(logger_a, logging.ERROR, msg, every_n=100)
    log_rate_limited(logger_a, logging.ERROR, msg, every_n=100)  # suppressed for A

    # B's first occurrence must log at level even though A has already fired.
    log_rate_limited(logger_b, logging.ERROR, msg, every_n=100)

    assert logger_a.log.call_count == 1
    assert logger_a.debug.call_count == 1
    assert logger_b.log.call_count == 1


# ── 7. Integration test with a real Logger + caplog ──────────────────


def test_integration_with_real_logger_and_caplog(caplog):
    """End-to-end: a real ``logging.Logger`` emits the expected records
    with the expected levels and ``exc_info`` attribute."""
    real_logger = logging.getLogger("voice_typer.test.log_rate_limit.integration")
    msg = "[RECORDING] Audio worker thread error processing chunk"

    # Capture everything (DEBUG and above).
    with caplog.at_level(logging.DEBUG, logger=real_logger.name):
        try:
            raise RuntimeError("synthetic chunk error")
        except RuntimeError:
            log_rate_limited(real_logger, logging.ERROR, msg, exc_info=True, every_n=100)

        # Occurrences 2..99 — all DEBUG.
        for _ in range(98):
            try:
                raise RuntimeError("synthetic chunk error")
            except RuntimeError:
                log_rate_limited(
                    real_logger, logging.ERROR, msg, exc_info=True, every_n=100
                )

        # Occurrence 100 — ERROR with exc_info again.
        try:
            raise RuntimeError("synthetic chunk error")
        except RuntimeError:
            log_rate_limited(real_logger, logging.ERROR, msg, exc_info=True, every_n=100)

    records = [r for r in caplog.records if r.message.startswith(msg)]
    # 1 ERROR + 98 DEBUG + 1 ERROR = 100 records.
    assert len(records) == 100

    error_records = [r for r in records if r.levelno == logging.ERROR]
    debug_records = [r for r in records if r.levelno == logging.DEBUG]
    assert len(error_records) == 2  # 1st + 100th
    assert len(debug_records) == 98

    # The two ERROR records must carry exc_info (a 3-tuple from sys.exc_info()).
    for r in error_records:
        assert r.exc_info is not None
        assert r.exc_info[0] is RuntimeError

    # DEBUG records must NOT carry exc_info (the whole point of the
    # rate-limit — avoid the expensive traceback capture on hot paths).
    for r in debug_records:
        assert r.exc_info is None

    # The DEBUG records should carry the suppressed-occurrence suffix.
    for i, r in enumerate(debug_records, start=2):
        assert f"(suppressed occurrence {i})" in r.message
