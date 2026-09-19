"""
UE-16 regression tests: summary-dict LRU cap + summary-severity preservation.
These tests are scoped to UE-16 -- they do NOT re-test the GT-B1-12
"""

from __future__ import annotations

import logging
from unittest.mock import MagicMock

import pytest
from voice_typer.server import log_rate_limit
from voice_typer.server.log_rate_limit import log_rate_limited, reset

pytestmark = pytest.mark.xdist_group("log_rate_limit")


class FakeLogger:
    """Minimal logger stub that records ``log`` and ``debug`` calls."""

    def __init__(self, name: str = "voice_typer.test.ue16_lru") -> None:
        self.name = name
        self.log = MagicMock()
        self.debug = MagicMock()


@pytest.fixture(autouse=True)
def _isolate_counters():
    """Clear all rate-limit state before and after each test."""
    reset()
    yield
    reset()


# (a): summary dicts bounded by the counter-dict LRU cap ────


class TestSummaryDictsBounded:
    """UE-16: the two GT-66 summary dicts MUST stay bounded at"""

    def test_next_summary_deadline_bounded_at_max_counters(self):
        """Driving >>_MAX_COUNTERS distinct keys must NOT cause"""
        logger = FakeLogger()
        # Two calls per key so the second one is a suppressed
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
        """Driving >>_MAX_COUNTERS distinct keys must NOT cause"""
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
        """When the LRU counter is evicted, its entries in BOTH summary"""
        logger = FakeLogger()
        # Fill the counter dict up to the cap.
        for i in range(log_rate_limit._MAX_COUNTERS):
            log_rate_limited(logger, logging.ERROR, f"ue16-prune-{i}", every_n=100)
        # The first-inserted key is the LRU eviction candidate.
        lru_key = (logger.name, "ue16-prune-0")
        assert lru_key in log_rate_limit._RATE_LIMIT_COUNTS, (
            "fixture setup: LRU key should still be present before eviction"
        )

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
        """A sustained stream of distinct dynamic messages must NOT"""
        logger = FakeLogger()
        # 3x the cap with TWO calls per message so suppressed-occurrence
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
    """UE-16: the GT-66 periodic summary severity is"""

    @staticmethod
    def _force_summary(monkeypatch, caplog, level: int, msg: str):
        """Helper: fire a suppressed occurrence then advance the clock"""
        logger = FakeLogger()
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
        """An ERROR-rate-limited caller's summary must be at ERROR --"""
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
        """historical baseline).  UE-16 does NOT escalate INFO summaries."""
        summaries = self._force_summary(monkeypatch, caplog, logging.INFO, "ue16-lru-info")
        assert len(summaries) == 1
        assert summaries[0].levelno == logging.INFO, (
            f"UE-16: INFO-caller summary should stay at INFO baseline, got {logging.getLevelName(summaries[0].levelno)}"
        )

    def test_debug_caller_clamped_up_to_info(self, monkeypatch, caplog):
        """A DEBUG-rate-limited caller's summary is clamped UP to INFO"""
        summaries = self._force_summary(monkeypatch, caplog, logging.DEBUG, "ue16-lru-debug")
        assert len(summaries) == 1
        assert summaries[0].levelno == logging.INFO, (
            "UE-16: DEBUG-caller summary should be clamped up to INFO "
            f"(file-handler default), got {logging.getLevelName(summaries[0].levelno)}"
        )

    def test_summary_not_demoted_to_info_for_error_caller(self, monkeypatch, caplog):
        """Explicit anti-regression: the summary's levelno must NOT be"""
        summaries = self._force_summary(monkeypatch, caplog, logging.ERROR, "ue16-lru-anti")
        assert len(summaries) == 1
        assert summaries[0].levelno != logging.INFO, (
            "UE-16 regression: ERROR-caller summary was demoted to INFO -- "
            "the summary is hiding the severity signal that operators' "
            "alerting rules (level>=ERROR) depend on"
        )
