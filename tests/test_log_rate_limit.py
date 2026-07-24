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
    log_rate_limited(logger, logging.ERROR, "chunk %d failed", 7, every_n=100, exc_info=True)
    logger.log.assert_called_once_with(logging.ERROR, "chunk %d failed", 7, exc_info=True)

    # Second call (suppressed) → DEBUG with the rendered message.
    log_rate_limited(logger, logging.ERROR, "chunk %d failed", 7, every_n=100, exc_info=True)
    logger.debug.assert_called_once_with("%s (suppressed occurrence %d)", "chunk 7 failed", 2)


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
    logger.log.assert_called_once_with(logging.ERROR, msg, exc_info=True)
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
    logger.debug.assert_called_once_with("%s (suppressed occurrence %d)", msg, 101)
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
    log_rate_limited(logger, logging.ERROR, "chunk %d failed", 1, key="chunk-failed", every_n=100)
    log_rate_limited(logger, logging.ERROR, "chunk %d failed", 2, key="chunk-failed", every_n=100)

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
    expected_level_calls_upper_bound = 2 * (n_threads * (1 + calls_per_thread // 100))
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
    logger.log.assert_called_once_with(logging.WARNING, "warn-test", exc_info=False)


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
                log_rate_limited(real_logger, logging.ERROR, msg, exc_info=True, every_n=100)

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


# ── 8. GT-66: periodic INFO summary for chronic suppressed conditions ──


class TestGt66PeriodicInfoSummary:
    """GT-66: emit a periodic INFO-level summary per counter key —
    every 60s of wall-clock time, if any counter incremented >0 since
    the last summary, log::

        log.info('[rate-limit] %d suppressed occurrences of %r in last 60s',
                 delta, key)

    The summary is routed through the module logger
    (``voice_typer.server.log_rate_limit``) so it's always visible at
    the file handler's INFO level regardless of the caller's logger
    level.  The first suppressed occurrence seeds the timer (so the
    first 60-second window starts ticking from the second call, not
    from process boot).
    """

    def test_first_suppressed_occurrence_does_not_emit_summary(self, caplog):
        """The first suppressed occurrence seeds the timer — no summary
        is emitted until 60s have elapsed.
        """
        logger = FakeLogger()
        msg = "gt-66-seed-test"

        with caplog.at_level(logging.INFO, logger="voice_typer.server.log_rate_limit"):
            log_rate_limited(logger, logging.ERROR, msg, every_n=100)
            log_rate_limited(logger, logging.ERROR, msg, every_n=100)

        summaries = [r for r in caplog.records if r.levelno == logging.INFO and "[rate-limit]" in r.message]
        assert summaries == [], (
            f"GT-66: no INFO summary should fire on the first suppressed "
            f"occurrence (timer just seeded); got {summaries!r}"
        )

    def test_summary_fires_after_interval_with_delta(self, monkeypatch, caplog):
        """When 60s+ of wall-clock has elapsed AND delta > 0 since the
        last summary, an INFO summary fires with the delta count and
        the counter key.
        """
        logger = FakeLogger()
        msg = "gt-66-summary-test"

        fake_time = [0.0]
        monkeypatch.setattr(
            "voice_typer.server.log_rate_limit.time.monotonic",
            lambda: fake_time[0],
        )

        with caplog.at_level(logging.INFO, logger="voice_typer.server.log_rate_limit"):
            log_rate_limited(logger, logging.ERROR, msg, every_n=100)
            log_rate_limited(logger, logging.ERROR, msg, every_n=100)
            for _ in range(9):
                log_rate_limited(logger, logging.ERROR, msg, every_n=100)

            fake_time[0] = 61.0
            log_rate_limited(logger, logging.ERROR, msg, every_n=100)

        summaries = [r for r in caplog.records if r.levelno == logging.INFO and "[rate-limit]" in r.message]
        assert len(summaries) == 1, (
            f"expected exactly 1 INFO summary after the 60s threshold; "
            f"got {len(summaries)}: {[r.message for r in summaries]!r}"
        )
        msg_text = summaries[0].getMessage()
        assert "11 suppressed occurrences" in msg_text, f"expected delta=11 in summary; got {msg_text!r}"
        assert repr(msg) in msg_text, f"expected counter key {msg!r} in summary; got {msg_text!r}"
        assert "in last 60s" in msg_text

    def test_summary_resets_delta_after_emission(self, monkeypatch, caplog):
        """After an INFO summary fires, the per-key delta is reset to 0.
        The next summary (after another 60s) reports only the delta
        accumulated SINCE the previous summary — not the cumulative
        total since process start.
        """
        logger = FakeLogger()
        msg = "gt-66-reset-test"

        fake_time = [0.0]
        monkeypatch.setattr(
            "voice_typer.server.log_rate_limit.time.monotonic",
            lambda: fake_time[0],
        )

        with caplog.at_level(logging.INFO, logger="voice_typer.server.log_rate_limit"):
            log_rate_limited(logger, logging.ERROR, msg, every_n=100)
            log_rate_limited(logger, logging.ERROR, msg, every_n=100)
            for _ in range(8):
                log_rate_limited(logger, logging.ERROR, msg, every_n=100)
            fake_time[0] = 61.0
            log_rate_limited(logger, logging.ERROR, msg, every_n=100)

            fake_time[0] = 122.0
            for _ in range(3):
                log_rate_limited(logger, logging.ERROR, msg, every_n=100)

        summaries = [r for r in caplog.records if r.levelno == logging.INFO and "[rate-limit]" in r.message]
        assert len(summaries) == 2, (
            f"expected 2 summaries (one per 60s window); got {len(summaries)}: {[r.message for r in summaries]!r}"
        )
        first = summaries[0].getMessage()
        second = summaries[1].getMessage()
        assert "10 suppressed occurrences" in first, f"first summary: {first!r}"
        assert "3 suppressed occurrences" in second, (
            f"GT-66 regression: delta not reset after first summary; second summary should report 3, got: {second!r}"
        )

    def test_summary_does_not_fire_within_same_window(self, monkeypatch, caplog):
        """Multiple suppressed occurrences within the same 60-second
        window do NOT each emit a summary.
        """
        logger = FakeLogger()
        msg = "gt-66-window-test"

        fake_time = [0.0]
        monkeypatch.setattr(
            "voice_typer.server.log_rate_limit.time.monotonic",
            lambda: fake_time[0],
        )

        with caplog.at_level(logging.INFO, logger="voice_typer.server.log_rate_limit"):
            log_rate_limited(logger, logging.ERROR, msg, every_n=100)
            log_rate_limited(logger, logging.ERROR, msg, every_n=100)

            fake_time[0] = 61.0
            log_rate_limited(logger, logging.ERROR, msg, every_n=100)
            for _ in range(5):
                log_rate_limited(logger, logging.ERROR, msg, every_n=100)

        summaries = [r for r in caplog.records if r.levelno == logging.INFO and "[rate-limit]" in r.message]
        assert len(summaries) == 1, (
            f"only one summary should fire per 60s window; got {len(summaries)}: {[r.message for r in summaries]!r}"
        )

    def test_summary_per_key_independent(self, monkeypatch, caplog):
        """Each counter key has its own summary cadence — a summary for
        key A does not reset key B's delta.
        """
        logger = FakeLogger()
        msg_a = "gt-66-key-a"
        msg_b = "gt-66-key-b"

        fake_time = [0.0]
        monkeypatch.setattr(
            "voice_typer.server.log_rate_limit.time.monotonic",
            lambda: fake_time[0],
        )

        with caplog.at_level(logging.INFO, logger="voice_typer.server.log_rate_limit"):
            log_rate_limited(logger, logging.ERROR, msg_a, every_n=100)
            log_rate_limited(logger, logging.ERROR, msg_a, every_n=100)
            log_rate_limited(logger, logging.ERROR, msg_b, every_n=100)
            log_rate_limited(logger, logging.ERROR, msg_b, every_n=100)

            fake_time[0] = 61.0
            log_rate_limited(logger, logging.ERROR, msg_a, every_n=100)
            log_rate_limited(logger, logging.ERROR, msg_b, every_n=100)

        summaries = [r for r in caplog.records if r.levelno == logging.INFO and "[rate-limit]" in r.message]
        assert len(summaries) == 1, (
            f"expected 1 summary (key A only); got {len(summaries)}: {[r.message for r in summaries]!r}"
        )
        assert repr(msg_a) in summaries[0].getMessage()


# ── 9. GT-B1-12: counter dict capped at 1024 with LRU eviction ────────


class TestGtB1_12LRUEviction:
    """GT-B1-12: ``_RATE_LIMIT_COUNTS`` is capped at ``_MAX_COUNTERS``
    (1024) entries with LRU eviction.  When eviction fires, a WARNING
    is logged through the module logger so the operator notices caller
    misuse (dynamic messages without an explicit ``key=``).
    """

    def test_dict_capped_at_max_counters(self):
        """After >_MAX_COUNTERS distinct keys, the dict size is exactly
        _MAX_COUNTERS (no unbounded growth).
        """
        logger = FakeLogger()
        for i in range(log_rate_limit._MAX_COUNTERS + 50):
            log_rate_limited(
                logger,
                logging.ERROR,
                f"dynamic-msg-{i}",
                every_n=100,
            )

        assert len(log_rate_limit._RATE_LIMIT_COUNTS) == log_rate_limit._MAX_COUNTERS, (
            f"GT-B1-12 regression: dict size = "
            f"{len(log_rate_limit._RATE_LIMIT_COUNTS)}, expected "
            f"{log_rate_limit._MAX_COUNTERS}"
        )

    def test_eviction_removes_least_recently_used(self):
        """The first-inserted key (least-recently-used) is evicted when
        the cap is exceeded; the most-recently-used key survives.
        """
        logger = FakeLogger()
        for i in range(log_rate_limit._MAX_COUNTERS):
            log_rate_limited(
                logger,
                logging.ERROR,
                f"msg-{i}",
                every_n=100,
            )
        first_key = (logger.name, "msg-0")
        assert first_key in log_rate_limit._RATE_LIMIT_COUNTS, (
            "first key should still be present before the cap is exceeded"
        )

        log_rate_limited(logger, logging.ERROR, "msg-new", every_n=100)

        assert first_key not in log_rate_limit._RATE_LIMIT_COUNTS, "GT-B1-12 regression: LRU key was not evicted"
        new_key = (logger.name, "msg-new")
        assert new_key in log_rate_limit._RATE_LIMIT_COUNTS, "newly-inserted key should be present"

    def test_access_marks_key_as_most_recently_used(self):
        """Re-accessing an existing key moves it to the MRU end so it
        survives eviction on the next insert.
        """
        logger = FakeLogger()
        for i in range(log_rate_limit._MAX_COUNTERS):
            log_rate_limited(
                logger,
                logging.ERROR,
                f"msg-{i}",
                every_n=100,
            )

        log_rate_limited(logger, logging.ERROR, "msg-0", every_n=100)
        log_rate_limited(logger, logging.ERROR, "msg-new", every_n=100)

        assert (logger.name, "msg-0") in log_rate_limit._RATE_LIMIT_COUNTS, (
            "GT-B1-12: re-accessed key should survive eviction (MRU)"
        )
        assert (logger.name, "msg-1") not in log_rate_limit._RATE_LIMIT_COUNTS, (
            "GT-B1-12: stale LRU key should have been evicted instead of the re-accessed one"
        )

    def test_eviction_logs_warning_through_module_logger(self, caplog):
        """When eviction fires, a WARNING is emitted through the module
        logger (``voice_typer.server.log_rate_limit``) so the operator
        notices the caller misuse.
        """
        logger = FakeLogger()
        with caplog.at_level(logging.WARNING, logger="voice_typer.server.log_rate_limit"):
            for i in range(log_rate_limit._MAX_COUNTERS + 5):
                log_rate_limited(
                    logger,
                    logging.ERROR,
                    f"eviction-warn-{i}",
                    every_n=100,
                )

        warnings = [r for r in caplog.records if r.levelno == logging.WARNING and "[rate-limit]" in r.message]
        assert warnings, (
            "GT-B1-12: expected at least one WARNING when eviction fires; "
            f"got records={[r.message for r in caplog.records]!r}"
        )
        msg_text = warnings[0].getMessage()
        assert "evicted" in msg_text
        assert str(log_rate_limit._MAX_COUNTERS) in msg_text
        assert "key=" in msg_text

    def test_no_eviction_no_warning(self, caplog):
        """When the dict stays under the cap, no eviction WARNING fires."""
        logger = FakeLogger()
        with caplog.at_level(logging.WARNING, logger="voice_typer.server.log_rate_limit"):
            for i in range(50):
                log_rate_limited(
                    logger,
                    logging.ERROR,
                    f"safe-msg-{i}",
                    every_n=100,
                )

        warnings = [r for r in caplog.records if r.levelno == logging.WARNING and "[rate-limit]" in r.message]
        assert warnings == [], (
            f"GT-B1-12: no eviction WARNING should fire when dict is under "
            f"the cap; got {[r.message for r in warnings]!r}"
        )

    def test_reset_clears_summary_and_eviction_state(self):
        """``reset()`` clears the LRU dict AND the GT-66 summary dicts
        so the next test starts from a clean slate.
        """
        logger = FakeLogger()
        for i in range(10):
            log_rate_limited(logger, logging.ERROR, f"reset-test-{i}", every_n=100)
        log_rate_limit._RATE_LIMIT_LAST_SUMMARY[(logger.name, "x")] = 1.0
        log_rate_limit._RATE_LIMIT_SUPPRESSED_SINCE_SUMMARY[(logger.name, "x")] = 5

        reset()

        assert not log_rate_limit._RATE_LIMIT_COUNTS
        assert not log_rate_limit._RATE_LIMIT_LAST_SUMMARY
        assert not log_rate_limit._RATE_LIMIT_SUPPRESSED_SINCE_SUMMARY
