"""SU-35: regression tests for the ``_cache_probe_cache`` hard cap.

``voice_typer/server/prewarm/process_tracker.py`` memoizes
``_probe_cache_status`` results in a module-level ``_cache_probe_cache``
dict. The 30 s TTL at the read site governs whether a cached *result* is
reused, but the dict entry itself is never evicted — a process that
swaps models thousands of times leaks one fingerprint entry per swap.

These tests pin the hard-cap + reset fix that mirrors the
``streaming.py:385`` pattern (``_seen_timestamps`` 50 k cap):
when ``len(_cache_probe_cache) > _CACHE_PROBE_MAX_ENTRIES``, the dict is
cleared wholesale. The fix is wired into the write path of
``_probe_cache_status`` so the cap is enforced on every write, not just
on read.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest
from voice_typer.server import prewarm
from voice_typer.server.prewarm import process_tracker

# ─── autouse fixture: start each test with an empty cache ──────────────


@pytest.fixture(autouse=True)
def _clear_cache_probe_cache():
    """Reset ``_cache_probe_cache`` before and after each test."""
    process_tracker._invalidate_cache_probe_cache()
    yield
    process_tracker._invalidate_cache_probe_cache()


# ─── helpers ───────────────────────────────────────────────────────────


def _build_fake_cache(tmp_path: Path) -> list[Path]:
    """Build a fake HF cache with one model dir; return ``[model_dir]``.

    Mirrors the helper in ``tests/test_prewarm_perf_fixes.py`` so a single
    call to ``_probe_cache_status`` produces exactly one cache entry.
    """
    cache = tmp_path / "huggingface" / "hub"
    model_dir = cache / "models--test--model"
    snap = model_dir / "snapshots" / "abc"
    snap.mkdir(parents=True)
    weights = snap / "model.safetensors"
    weights.write_bytes(b"\x00" * (1024 * 1024))  # 1 MB
    return [model_dir]


def _seed_cache(n: int) -> None:
    """Insert ``n`` synthetic entries directly into ``_cache_probe_cache``."""
    now = 0.0
    result = (0.0, 0, 0)
    for i in range(n):
        process_tracker._cache_probe_cache[f"fp_{i}"] = (now, result)


# ─── test 1: cache cleared when cap exceeded ───────────────────────────


class TestCacheProbeCapClears:
    """SU-35 fix: ``_prune_stale_cache_probe_entries`` clears the dict
    when it exceeds ``_CACHE_PROBE_MAX_ENTRIES``."""

    def test_cache_cleared_when_exceeds_cap(self):
        """Inserting 257 entries (cap is 256) then pruning clears the dict."""
        cap = process_tracker._CACHE_PROBE_MAX_ENTRIES
        assert cap == 256, "test setup: cap must be 256"

        _seed_cache(cap + 1)
        assert len(process_tracker._cache_probe_cache) == cap + 1, (
            "test setup: cache must hold cap+1 entries before prune"
        )

        process_tracker._prune_stale_cache_probe_entries()

        assert len(process_tracker._cache_probe_cache) == 0, "SU-35: cache must be cleared when it exceeds the hard cap"

    def test_cache_not_cleared_at_or_below_cap(self):
        """At exactly the cap (256), the dict is left untouched."""
        cap = process_tracker._CACHE_PROBE_MAX_ENTRIES

        _seed_cache(cap)
        assert len(process_tracker._cache_probe_cache) == cap

        process_tracker._prune_stale_cache_probe_entries()

        assert len(process_tracker._cache_probe_cache) == cap, (
            "SU-35: cache at exactly the cap must NOT be cleared (only strictly greater than triggers the prune)"
        )


# ─── test 2: cap enforced on every write ───────────────────────────────


class TestCacheProbeCapEnforcedOnWrite:
    """SU-35 fix: the prune is wired into the ``_probe_cache_status``
    write path — the cap fires on every write, not just on read."""

    def test_write_path_triggers_prune_at_cap(self, monkeypatch, tmp_path):
        """When the cache is at the cap, the next ``_probe_cache_status``
        write triggers the prune — the cache is cleared on the write path,
        not deferred to the next read."""
        cap = process_tracker._CACHE_PROBE_MAX_ENTRIES

        # Pre-fill the cache to exactly the cap with synthetic fingerprints.
        _seed_cache(cap)
        assert len(process_tracker._cache_probe_cache) == cap

        # Patch _cache_ratio (used inside _probe_cache_status) so the
        # single new fingerprint produces a real cache entry write.
        monkeypatch.setattr(prewarm, "_cache_ratio", lambda p, samples=20: 0.5)

        active_dirs = _build_fake_cache(tmp_path)

        # This call writes one new entry (cap+1 total), then the prune
        # hook at the end of the write path must fire and clear the dict.
        result = process_tracker._probe_cache_status(active_dirs)

        # Sanity: the call returned a valid tuple.
        assert isinstance(result, tuple) and len(result) == 3

        # The write-path prune must have cleared the cache. With the
        # streaming.py:385 mirror, the just-inserted entry is also
        # discarded (next probe recomputes it) — that's the documented
        # trade-off for the hard-cap + reset pattern.
        assert len(process_tracker._cache_probe_cache) == 0, (
            "SU-35: cap must be enforced on every write — after a write "
            "that pushes the cache over the cap, the prune hook must "
            f"clear it (got len={len(process_tracker._cache_probe_cache)})"
        )

    def test_write_path_does_not_prune_below_cap(self, monkeypatch, tmp_path):
        """When the cache is below the cap, a write does NOT prune — the
        new entry stays cached so the 30 s TTL read-freshness contract
        is preserved."""
        cap = process_tracker._CACHE_PROBE_MAX_ENTRIES

        # Pre-fill to cap - 1 (one below the cap).
        _seed_cache(cap - 1)
        assert len(process_tracker._cache_probe_cache) == cap - 1

        monkeypatch.setattr(prewarm, "_cache_ratio", lambda p, samples=20: 0.5)
        active_dirs = _build_fake_cache(tmp_path)

        process_tracker._probe_cache_status(active_dirs)

        # The new entry was added and the prune did NOT fire — cache
        # now holds exactly cap entries (cap-1 synthetic + 1 new).
        assert len(process_tracker._cache_probe_cache) == cap, (
            "SU-35: a write that keeps the cache at or below the cap must "
            "NOT trigger a prune — the new entry must stay cached so the "
            "30 s TTL read-freshness contract is preserved"
        )


# ─── test 3: idempotent on empty cache ─────────────────────────────────


class TestCacheProbePruneIdempotent:
    """SU-35 fix: ``_prune_stale_cache_probe_entries`` is idempotent —
    calling it twice on an empty (or under-cap) cache is a no-op."""

    def test_idempotent_on_empty_cache(self):
        """Two consecutive prune calls on an empty cache are both no-ops."""
        assert len(process_tracker._cache_probe_cache) == 0

        # First call — no-op (cache is empty, well below cap).
        process_tracker._prune_stale_cache_probe_entries()
        assert len(process_tracker._cache_probe_cache) == 0

        # Second call — still a no-op.
        process_tracker._prune_stale_cache_probe_entries()
        assert len(process_tracker._cache_probe_cache) == 0

    def test_idempotent_on_under_cap_cache(self):
        """Two consecutive prune calls on an under-cap cache are no-ops;
        the cache contents are preserved."""
        cap = process_tracker._CACHE_PROBE_MAX_ENTRIES
        _seed_cache(cap - 10)  # comfortably under the cap
        before = dict(process_tracker._cache_probe_cache)

        process_tracker._prune_stale_cache_probe_entries()
        process_tracker._prune_stale_cache_probe_entries()

        assert process_tracker._cache_probe_cache == before, (
            "SU-35: pruning an under-cap cache twice must be a no-op — "
            "cache contents must be identical to before the calls"
        )


# ─── test 4: rate-limited WARNING logged when cap fires ────────────────


class TestCacheProbeCapWarning:
    """SU-35 fix: a WARNING is logged when the cap fires. The warning is
    naturally rate-limited — after a clear, ``len == 0`` so the next 256
    writes must accumulate before the warning can fire again."""

    def test_warning_logged_when_cap_fires(self, caplog):
        """Pruning a cache that exceeds the cap logs a WARNING with the
        cap message."""
        cap = process_tracker._CACHE_PROBE_MAX_ENTRIES
        _seed_cache(cap + 1)

        with caplog.at_level(logging.WARNING, logger="voice_typer.server.prewarm"):
            process_tracker._prune_stale_cache_probe_entries()

        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warnings) >= 1, "SU-35: a WARNING must be logged when the prune cap fires"
        assert any("_cache_probe_cache exceeded cap" in r.message for r in warnings), (
            "SU-35: the WARNING message must mention the cap — got: " + ", ".join(r.message for r in warnings)
        )

    def test_no_warning_when_prune_is_noop(self, caplog):
        """Pruning an under-cap cache logs NO warning."""
        _seed_cache(10)  # well below the cap

        with caplog.at_level(logging.WARNING, logger="voice_typer.server.prewarm"):
            process_tracker._prune_stale_cache_probe_entries()

        cap_warnings = [
            r for r in caplog.records if r.levelno == logging.WARNING and "_cache_probe_cache exceeded cap" in r.message
        ]
        assert not cap_warnings, "SU-35: no cap WARNING should be logged when the cache is under cap"

    def test_warning_rate_limited_naturally(self, caplog):
        """After a clear, the next prune call does NOT log another warning
        — the natural rate limit is 'once per cap-overflow event'.

        This is the streaming.py:385 pattern: ``set()`` / ``clear()``
        drops the count to zero, so the next overflow requires another
        ``cap`` writes to accumulate.
        """
        cap = process_tracker._CACHE_PROBE_MAX_ENTRIES
        _seed_cache(cap + 1)

        with caplog.at_level(logging.WARNING, logger="voice_typer.server.prewarm"):
            # First prune — cache is over cap → warning fires, cache cleared.
            process_tracker._prune_stale_cache_probe_entries()
            # Second prune — cache is now empty → no-op, NO second warning.
            process_tracker._prune_stale_cache_probe_entries()

        cap_warnings = [
            r for r in caplog.records if r.levelno == logging.WARNING and "_cache_probe_cache exceeded cap" in r.message
        ]
        assert len(cap_warnings) == 1, (
            "SU-35: the cap WARNING must be naturally rate-limited — exactly "
            f"one warning per overflow event (got {len(cap_warnings)}). "
            "After a clear, len(_cache_probe_cache)==0 so the next prune is "
            "a no-op and must NOT re-log the warning."
        )
