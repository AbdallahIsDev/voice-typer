"""UE-16 regression tests: summary-dict LRU cap + summary-severity preservation.

These tests pin the two contracts added by UE-16 in
:mod:`voice_typer.server.log_rate_limit`:

1. **Summary dicts are bounded.**  Pre-UE-16 the two GT-66 summary dicts
   (``_RATE_LIMIT_NEXT_SUMMARY_DEADLINE`` and
   ``_RATE_LIMIT_SUPPRESSED_SINCE_SUMMARY``) were keyed by the same
   ``(logger.name, key_or_msg)`` tuple as ``_RATE_LIMIT_COUNTS`` but
   were never pruned on eviction.  A caller that drove >1024 distinct
   dynamic messages (without passing an explicit ``key=``) would leak
   summary state forever -- the summary dicts grew without bound.  UE-16
   ties the summary dicts to the ``_RATE_LIMIT_COUNTS`` LRU cap: when a
   counter is evicted, its entries in the summary dicts are pruned in
   the same critical section, so the summary dicts can never exceed
   ``_MAX_COUNTERS`` (1024) entries.

2. **Summary severity tracks the caller's configured level.**  Pre-UE-16
   the GT-66 periodic summary was hardcoded at ``_log.info(...)`` -- so
   an ERROR-rate-limited path that fired 1000x in 60s surfaced an INFO
   summary, losing the severity signal that operators' alerting rules
   key on (``level>=ERROR``).  UE-16 makes the summary severity
   ``max(logging.INFO, level)`` so ERROR/CRITICAL/WARNING callers get a
   summary at their configured level (DEBUG/INFO callers stay at the
   INFO baseline so the summary still surfaces at the file handler's
   default level).

These tests are scoped to UE-16 -- they do NOT re-test the GT-B1-12
counter-dict LRU eviction itself (covered in ``test_log_rate_limit.py``)
or the GT-66 summary cadence mechanics (also covered there).  They
focus on the two UE-16 deltas: (a) summary dicts share the counter
dict's LRU cap, and (b) the summary's levelno matches the caller's
configured level.
"""

from __future__ import annotations

import logging
from unittest.mock import MagicMock

import pytest
from voice_typer.server import log_rate_limit
from voice_typer.server.log_rate_limit import log_rate_limited, reset

# Hint for xdist schedulers that respect ``xdist_group`` (loadgroup /
# loadscope): pin every test in this module — and its sibling
# ``test_log_rate_limit.py`` — onto a single worker. Both modules
# mutate the process-wide module-level dicts in
# ``voice_typer.server.log_rate_limit`` (``_RATE_LIMIT_COUNTS`` and the
# summary dicts), reset via autouse fixtures; grouping them on one
# worker is defense-in-depth for that shared state. xdist's default
# ``load`` scheduler does NOT strictly honor this marker — it is a
# hint, not a correctness guarantee. No-op when xdist isn't active.
# (C-TEST-5.)
pytestmark = pytest.mark.xdist_group("log_rate_limit")


class FakeLogger:
    """Minimal logger stub that records ``log`` and ``debug`` calls.

    Mirrors the stub in ``test_log_rate_limit.py`` so the two suites
    stay consistent.  A plain :class:`unittest.mock.MagicMock` does
    not work here because ``MagicMock(spec=logging.Logger)`` excludes
    the ``name`` attribute (it is an *instance* attribute on
    :class:`logging.Logger`, not a class attribute).
    """

    def __init__(self, name: str = "voice_typer.test.ue16_lru") -> None:
        self.name = name
        self.log = MagicMock()
        self.debug = MagicMock()


@pytest.fixture(autouse=True)
def _isolate_counters():
    """Clear all rate-limit state before and after each test.

    UE-16 tests assert on the *size* of the module-level summary dicts,
    so any state leaked from a previous test would corrupt the size
    assertion.  ``reset()`` clears all three dicts (counter + both
    summary dicts) -- see ``log_rate_limit.reset``.
    """
    reset()
    yield
    reset()


# (a): summary dicts bounded by the counter-dict LRU cap ────


class TestSummaryDictsBounded:
    """UE-16: the two GT-66 summary dicts MUST stay bounded at
    ``_MAX_COUNTERS`` (1024) entries.

    Pre-UE-16 the summary dicts were never pruned -- they leaked an
    entry for every distinct ``(logger.name, key)`` pair that ever
    fired a suppressed occurrence, even after the corresponding counter
    was LRU-evicted.  In a long-running server with a high-cardinality
    dynamic-message caller this was an unbounded memory leak.

    UE-16 ties summary-dict pruning to the existing GT-B1-12 counter
    LRU eviction: when ``popitem(last=False)`` evicts the LRU counter
    key, the same key is popped from both summary dicts in the same
    critical section.  The summary dicts therefore can never exceed
    ``_MAX_COUNTERS`` entries.
    """

    def test_next_summary_deadline_bounded_at_max_counters(self):
        """Driving >>_MAX_COUNTERS distinct keys must NOT cause
        ``_RATE_LIMIT_NEXT_SUMMARY_DEADLINE`` to exceed the cap.
        """
        logger = FakeLogger()
        # Two calls per key so the second one is a suppressed
        # occurrence -- this is what seeds the summary dicts.  The
        # first call is the 1st-occurrence (logs at level, no summary
        # state touched); the second is suppressed (seeds the deadline
        # on the first suppressed occurrence).
        for i in range(log_rate_limit._MAX_COUNTERS * 3):
            msg = f"ue16-deadline-{i}"
            log_rate_limited(logger, logging.ERROR, msg, every_n=100)
            log_rate_limited(logger, logging.ERROR, msg, every_n=100)

        max_counters = log_rate_limit._MAX_COUNTERS
        assert len(log_rate_limit._RATE_LIMIT_COUNTS) == max_counters, (
            "GT-B1-12: _RATE_LIMIT_COUNTS should be capped at "
            f"{max_counters}, got {len(log_rate_limit._RATE_LIMIT_COUNTS)}"
        )
        assert len(log_rate_limit._RATE_LIMIT_NEXT_SUMMARY_DEADLINE) <= max_counters, (
            "UE-16 regression: _RATE_LIMIT_NEXT_SUMMARY_DEADLINE grew beyond "
            f"_MAX_COUNTERS ({max_counters}); got "
            f"{len(log_rate_limit._RATE_LIMIT_NEXT_SUMMARY_DEADLINE)} -- summary "
            "dict is leaking entries on counter eviction"
        )

    def test_suppressed_since_summary_bounded_at_max_counters(self):
        """Driving >>_MAX_COUNTERS distinct keys must NOT cause
        ``_RATE_LIMIT_SUPPRESSED_SINCE_SUMMARY`` to exceed the cap.
        """
        logger = FakeLogger()
        for i in range(log_rate_limit._MAX_COUNTERS * 3):
            msg = f"ue16-suppressed-{i}"
            log_rate_limited(logger, logging.ERROR, msg, every_n=100)
            log_rate_limited(logger, logging.ERROR, msg, every_n=100)

        max_counters = log_rate_limit._MAX_COUNTERS
        assert len(log_rate_limit._RATE_LIMIT_COUNTS) == max_counters, (
            "GT-B1-12: _RATE_LIMIT_COUNTS should be capped at "
            f"{max_counters}, got {len(log_rate_limit._RATE_LIMIT_COUNTS)}"
        )
        assert len(log_rate_limit._RATE_LIMIT_SUPPRESSED_SINCE_SUMMARY) <= max_counters, (
            "UE-16 regression: _RATE_LIMIT_SUPPRESSED_SINCE_SUMMARY grew beyond "
            f"_MAX_COUNTERS ({max_counters}); got "
            f"{len(log_rate_limit._RATE_LIMIT_SUPPRESSED_SINCE_SUMMARY)} -- summary "
            "dict is leaking entries on counter eviction"
        )

    def test_evicted_counter_pruned_from_both_summary_dicts(self):
        """When the LRU counter is evicted, its entries in BOTH summary
        dicts must be pruned in the same critical section.

        This is the core UE-16 invariant: the summary dicts are bounded
        *because* eviction from ``_RATE_LIMIT_COUNTS`` cleans up the
        correlated summary-dict entries.  If this invariant breaks, the
        summary dicts resume their pre-UE-16 unbounded growth.
        """
        logger = FakeLogger()
        # Fill the counter dict up to the cap.
        for i in range(log_rate_limit._MAX_COUNTERS):
            log_rate_limited(logger, logging.ERROR, f"ue16-prune-{i}", every_n=100)
        # The first-inserted key is the LRU eviction candidate.
        lru_key = (logger.name, "ue16-prune-0")
        assert lru_key in log_rate_limit._RATE_LIMIT_COUNTS, (
            "fixture setup: LRU key should still be present before eviction"
        )

        # Seed summary-dict state for the LRU key so we can assert it
        # gets pruned.  (In production this happens automatically on a
        # suppressed occurrence; here we set it directly to isolate the
        # pruning contract from the seeding cadence.)
        log_rate_limit._RATE_LIMIT_NEXT_SUMMARY_DEADLINE[lru_key] = 1.0
        log_rate_limit._RATE_LIMIT_SUPPRESSED_SINCE_SUMMARY[lru_key] = 5
        assert lru_key in log_rate_limit._RATE_LIMIT_NEXT_SUMMARY_DEADLINE
        assert lru_key in log_rate_limit._RATE_LIMIT_SUPPRESSED_SINCE_SUMMARY

        # Trigger an eviction by inserting one new key past the cap.
        log_rate_limited(logger, logging.ERROR, "ue16-prune-new", every_n=100)

        # The LRU key must be gone from all three dicts.
        assert lru_key not in log_rate_limit._RATE_LIMIT_COUNTS, (
            "GT-B1-12 regression: LRU key was not evicted from _RATE_LIMIT_COUNTS"
        )
        assert lru_key not in log_rate_limit._RATE_LIMIT_NEXT_SUMMARY_DEADLINE, (
            "UE-16 regression: evicted key was not pruned from _RATE_LIMIT_NEXT_SUMMARY_DEADLINE (summary dict leak)"
        )
        assert lru_key not in log_rate_limit._RATE_LIMIT_SUPPRESSED_SINCE_SUMMARY, (
            "UE-16 regression: evicted key was not pruned from _RATE_LIMIT_SUPPRESSED_SINCE_SUMMARY (summary dict leak)"
        )

    def test_no_unbounded_growth_under_sustained_dynamic_messages(self):
        """A sustained stream of distinct dynamic messages must NOT
        grow either summary dict beyond ``_MAX_COUNTERS``.

        This is the integration-level assertion: simulate the
        worst-case caller (high-cardinality dynamic messages without
        an explicit ``key=``) for 3x the cap and confirm the summary
        dicts are stable at the cap.  Pre-UE-16 this scenario produced
        an unbounded leak.
        """
        logger = FakeLogger()
        # 3x the cap with TWO calls per message so suppressed-occurrence
        # state is seeded for every key (worst case for summary-dict
        # growth).
        for i in range(log_rate_limit._MAX_COUNTERS * 3):
            msg = f"ue16-stress-{i}"
            log_rate_limited(logger, logging.ERROR, msg, every_n=100)
            log_rate_limited(logger, logging.ERROR, msg, every_n=100)

        max_counters = log_rate_limit._MAX_COUNTERS
        # All three dicts must be bounded at max_counters.
        assert len(log_rate_limit._RATE_LIMIT_COUNTS) == max_counters
        assert len(log_rate_limit._RATE_LIMIT_NEXT_SUMMARY_DEADLINE) <= max_counters, (
            f"UE-16: _RATE_LIMIT_NEXT_SUMMARY_DEADLINE leaked past cap "
            f"({len(log_rate_limit._RATE_LIMIT_NEXT_SUMMARY_DEADLINE)} > {max_counters})"
        )
        assert len(log_rate_limit._RATE_LIMIT_SUPPRESSED_SINCE_SUMMARY) <= max_counters, (
            f"UE-16: _RATE_LIMIT_SUPPRESSED_SINCE_SUMMARY leaked past cap "
            f"({len(log_rate_limit._RATE_LIMIT_SUPPRESSED_SINCE_SUMMARY)} > {max_counters})"
        )


# (b): summary severity tracks the caller's configured level ──


class TestSummarySeverity:
    """UE-16: the GT-66 periodic summary severity is
    ``max(logging.INFO, level)`` -- so an ERROR-rate-limited path
    surfaces an ERROR summary (not INFO).

    Pre-UE-16 the summary was hardcoded at ``_log.info(...)``, which
    meant an ERROR-rate-limited path that fired 1000x in 60s surfaced
    an INFO summary 60s later.  Operators' alerting rules keyed on
    ``level>=ERROR`` missed the recurrence entirely -- the rate-limit
    helper was hiding the very errors it was meant to surface.

    Post-UE-16 the summary escalates to the caller's configured level
    (clamped to >= INFO so a DEBUG-caller's summary still surfaces at
    the file handler's default level).  These tests pin the contract
    for each level.
    """

    @staticmethod
    def _force_summary(monkeypatch, caplog, level: int, msg: str):
        """Helper: fire a suppressed occurrence then advance the clock
        past the 60s summary deadline and fire another suppressed
        occurrence to trigger the summary emission.

        Returns the list of summary records emitted through the module
        logger (``voice_typer.server.log_rate_limit``).
        """
        logger = FakeLogger()
        # Use a deterministic fake clock so the test doesn't have to
        # sleep for 60s.  The first call is the 1st-occurrence (logs at
        # *level*); the second is suppressed (seeds the deadline at
        # ``now + 60s``); advancing fake_time past 60s and firing a
        # third suppressed occurrence triggers the summary emission.
        fake_time = [0.0]
        monkeypatch.setattr(
            "voice_typer.server.log_rate_limit.time.monotonic",
            lambda: fake_time[0],
        )
        with caplog.at_level(logging.DEBUG, logger="voice_typer.server.log_rate_limit"):
            log_rate_limited(logger, level, msg, every_n=100)  # 1st → level
            log_rate_limited(logger, level, msg, every_n=100)  # suppressed → seeds deadline
            fake_time[0] = 61.0
            log_rate_limited(logger, level, msg, every_n=100)  # suppressed → fires summary
        return [r for r in caplog.records if "[rate-limit]" in r.message and "suppressed occurrences" in r.message]

    def test_error_level_preserved_in_summary(self, monkeypatch, caplog):
        """An ERROR-rate-limited caller's summary must be at ERROR --
        NOT demoted to INFO.  This is the core UE-16 regression: the
        summary must preserve the ERROR severity so alerting rules
        keyed on ``level>=ERROR`` fire on the recurrence.
        """
        summaries = self._force_summary(monkeypatch, caplog, logging.ERROR, "ue16-lru-error")
        assert len(summaries) == 1, (
            f"expected exactly one summary record, got {len(summaries)}: {[r.message for r in summaries]!r}"
        )
        assert summaries[0].levelno == logging.ERROR, (
            "UE-16 regression: ERROR-caller summary was demoted to "
            f"{logging.getLevelName(summaries[0].levelno)} (expected ERROR) -- "
            "summary is hiding the severity signal operators' alerting rules key on"
        )

    def test_critical_level_preserved_in_summary(self, monkeypatch, caplog):
        """A CRITICAL-rate-limited caller's summary must be at CRITICAL."""
        summaries = self._force_summary(monkeypatch, caplog, logging.CRITICAL, "ue16-lru-critical")
        assert len(summaries) == 1
        assert summaries[0].levelno == logging.CRITICAL, (
            "UE-16 regression: CRITICAL-caller summary was demoted to "
            f"{logging.getLevelName(summaries[0].levelno)} (expected CRITICAL)"
        )

    def test_warning_level_preserved_in_summary(self, monkeypatch, caplog):
        """A WARNING-rate-limited caller's summary must be at WARNING."""
        summaries = self._force_summary(monkeypatch, caplog, logging.WARNING, "ue16-lru-warning")
        assert len(summaries) == 1
        assert summaries[0].levelno == logging.WARNING, (
            "UE-16 regression: WARNING-caller summary was demoted to "
            f"{logging.getLevelName(summaries[0].levelno)} (expected WARNING)"
        )

    def test_info_caller_stays_at_info_baseline(self, monkeypatch, caplog):
        """An INFO-rate-limited caller's summary stays at INFO (the
        historical baseline).  UE-16 does NOT escalate INFO summaries.
        """
        summaries = self._force_summary(monkeypatch, caplog, logging.INFO, "ue16-lru-info")
        assert len(summaries) == 1
        assert summaries[0].levelno == logging.INFO, (
            f"UE-16: INFO-caller summary should stay at INFO baseline, got {logging.getLevelName(summaries[0].levelno)}"
        )

    def test_debug_caller_clamped_up_to_info(self, monkeypatch, caplog):
        """A DEBUG-rate-limited caller's summary is clamped UP to INFO
        so it still surfaces at the file handler's default level.  This
        preserves the GT-66 goal (surface chronic conditions at INFO)
        while not re-introducing the "summary hides severity" bug for
        higher levels.
        """
        summaries = self._force_summary(monkeypatch, caplog, logging.DEBUG, "ue16-lru-debug")
        assert len(summaries) == 1
        assert summaries[0].levelno == logging.INFO, (
            "UE-16: DEBUG-caller summary should be clamped up to INFO "
            f"(file-handler default), got {logging.getLevelName(summaries[0].levelno)}"
        )

    def test_summary_not_demoted_to_info_for_error_caller(self, monkeypatch, caplog):
        """Explicit anti-regression: the summary's levelno must NOT be
        INFO when the caller's configured level is ERROR.  This is the
        precise bug UE-16 fixes -- pin it with a direct inequality
        assertion so a future revert (e.g. re-hardcoding
        ``_log.info(...)``) is caught even if the level-equality
        assertion above is somehow weakened.
        """
        summaries = self._force_summary(monkeypatch, caplog, logging.ERROR, "ue16-lru-anti")
        assert len(summaries) == 1
        assert summaries[0].levelno != logging.INFO, (
            "UE-16 regression: ERROR-caller summary was demoted to INFO -- "
            "the summary is hiding the severity signal that operators' "
            "alerting rules (level>=ERROR) depend on"
        )
