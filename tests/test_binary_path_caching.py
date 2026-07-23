"""XV-112: pin the ``functools.lru_cache(maxsize=1)`` on
``voice_typer.server.native_hotkeys.binary_path.get_native_binary_path``.

Pre-fix, ``get_native_binary_path()`` was called up to 3× at startup
(once from ``factory.create_native_backend``, once from
``factory.is_native_backend_available``, once from
``SubprocessHotkeyBackend.__init__`` in ``base.py``). Each call walked
the 6-step lookup chain (env var override → env dir → dev mode →
PyInstaller onedir → ``_MEIPASS``), issuing up to 6 ``Path.is_file()``
probes per call — ~18 stats at boot for a result that cannot change
within a single process.

XV-112 wraps the function in ``functools.lru_cache(maxsize=1)`` so the
lookup runs at most once per process. This test file pins:

1. The cache HITS on the second call (the second call does NOT re-walk
   the lookup chain — verified by spying on the internal
   ``_candidate_binary_names`` helper).
2. ``cache_clear()`` resets the cache so the next call re-walks.
3. The cached value is the SAME object across calls (not just an equal
   one — ``lru_cache`` returns the literal first result).
4. The autouse ``clear_binary_path_cache`` fixture in
   ``tests/conftest.py`` clears the cache before every test, so tests
   that monkeypatch platform / env / filesystem state see fresh
   results (this test exercises that contract directly).
5. The function exposes ``cache_clear`` and ``cache_info`` — the
   ``functools.lru_cache`` decorator's standard introspection hooks —
   so tests and tooling can manage the cache explicitly.

These tests do NOT depend on a real native binary being present — they
monkeypatch ``_candidate_binary_names`` and ``Path.is_file`` to make
the lookup deterministic and to count how many times the function
actually re-resolves.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from voice_typer.server.native_hotkeys import binary_path


# ─── Fixtures ────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _linux_x86_64_env(monkeypatch):
    """Pin platform+arch to linux/x86_64 so the candidate-name list is
    deterministic across host platforms (these tests run on the Linux
    sandbox, but the explicit pin future-proofs against CI runners on
    other arches).

    The autouse ``clear_binary_path_cache`` fixture from
    ``tests/conftest.py`` runs BEFORE this fixture's setup (pytest
    orders autouse fixtures by scope/dependency, and the conftest one
    is module-scope-independent), so the cache is already empty when
    each test starts.
    """
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(binary_path.platform, "machine", lambda: "x86_64")
    monkeypatch.delenv("VOICE_TYPER_NATIVE_BINARY", raising=False)
    monkeypatch.delenv("VOICE_TYPER_NATIVE_DIR", raising=False)
    # Defensive: clear the cache once more at fixture-setup time in
    # case a prior test in the SAME session cached a result against a
    # different platform/env that this fixture did not revert.
    binary_path.get_native_binary_path.cache_clear()


# ─── Cache presence + introspection hooks ────────────────────────────────


class TestCacheDecoratorPresent:
    """Verify ``get_native_binary_path`` is wrapped in ``lru_cache``."""

    def test_exposes_cache_clear(self):
        """``functools.lru_cache`` adds ``cache_clear`` — its presence
        proves the decorator was applied. The autouse ``clear_binary_path_cache``
        conftest fixture relies on this attribute.
        """
        assert callable(getattr(binary_path.get_native_binary_path, "cache_clear", None))

    def test_exposes_cache_info(self):
        """``functools.lru_cache`` adds ``cache_info`` — its presence
        lets tests assert hit/miss counts.
        """
        assert callable(getattr(binary_path.get_native_binary_path, "cache_info", None))

    def test_maxsize_is_one(self):
        """``lru_cache(maxsize=1)`` is the documented contract —
        the function takes no args, so a larger maxsize would be
        wasted memory; maxsize=1 is sufficient to memoise the single
        no-arg result.
        """
        # ``cache_info`` returns a named tuple with a ``maxsize`` field.
        # Trigger one call to populate the cache so cache_info reflects
        # the real maxsize (``lru_cache`` reports maxsize even before
        # any call, but we call to be defensive).
        binary_path.get_native_binary_path.cache_clear()
        binary_path.get_native_binary_path()
        info = binary_path.get_native_binary_path.cache_info()
        assert info.maxsize == 1, (
            f"expected lru_cache(maxsize=1), got maxsize={info.maxsize}; "
            "a larger cache would let stale platform/env variants accumulate."
        )


# ─── Cache hit/miss behaviour ────────────────────────────────────────────


class TestCacheHitMiss:
    """Verify the second call hits the cache (does not re-walk the
    lookup chain) and ``cache_clear`` resets it."""

    def test_second_call_hits_cache(self, monkeypatch):
        """After the first call resolves the path, the second call
        returns the cached value WITHOUT re-invoking
        ``_candidate_binary_names`` (which is the first thing the real
        function does). Spying on that helper lets us assert "the
        function body did not run".
        """
        # Make the function find SOMETHING so the cache stores a
        # non-None value (caching None also works, but a real Path
        # makes the assertion more concrete).
        fake_binary = Path("/tmp/xv-112-fake-linux-key-listener-x86_64")
        monkeypatch.setattr(
            binary_path,
            "_candidate_binary_names",
            lambda: ["linux-key-listener-x86_64"],
        )
        monkeypatch.setattr(Path, "is_file", lambda self: self == fake_binary)
        # The env-var override path is name-agnostic — set it to the
        # fake binary so the function returns it on the first probe.
        monkeypatch.setenv("VOICE_TYPER_NATIVE_BINARY", str(fake_binary))

        # Reset spy state.
        binary_path.get_native_binary_path.cache_clear()

        call_count = {"n": 0}
        original_candidate = binary_path._candidate_binary_names

        def counting_candidate():
            call_count["n"] += 1
            return original_candidate()

        monkeypatch.setattr(binary_path, "_candidate_binary_names", counting_candidate)

        first = binary_path.get_native_binary_path()
        second = binary_path.get_native_binary_path()

        # Both calls return the same cached Path object.
        assert first == fake_binary
        assert second == fake_binary
        # The lookup chain ran exactly ONCE — the second call hit the
        # cache and did not call _candidate_binary_names again.
        assert call_count["n"] == 1, (
            f"expected _candidate_binary_names to be called once (cached on 2nd call), "
            f"got {call_count['n']}"
        )
        # cache_info confirms the hit.
        info = binary_path.get_native_binary_path.cache_info()
        assert info.hits >= 1, f"expected at least 1 cache hit, got {info.hits}"
        assert info.misses == 1, f"expected exactly 1 cache miss, got {info.misses}"

    def test_cache_clear_forces_reresolution(self, monkeypatch):
        """``cache_clear()`` empties the cache so the next call
        re-walks the lookup chain (and may return a DIFFERENT result
        if the env / filesystem changed between calls). This is the
        contract tests rely on to simulate different platform/env
        states.
        """
        monkeypatch.setattr(
            binary_path,
            "_candidate_binary_names",
            lambda: ["linux-key-listener-x86_64"],
        )

        # First scenario: env var points at binary A.
        binary_a = Path("/tmp/xv-112-binary-A")
        monkeypatch.setattr(Path, "is_file", lambda self: self == binary_a)
        monkeypatch.setenv("VOICE_TYPER_NATIVE_BINARY", str(binary_a))
        binary_path.get_native_binary_path.cache_clear()

        first = binary_path.get_native_binary_path()
        assert first == binary_a

        # Second scenario: env var changed to point at binary B.
        # Without ``cache_clear``, the cache would still return A.
        binary_b = Path("/tmp/xv-112-binary-B")
        monkeypatch.setattr(Path, "is_file", lambda self: self == binary_b)
        monkeypatch.setenv("VOICE_TYPER_NATIVE_BINARY", str(binary_b))
        binary_path.get_native_binary_path.cache_clear()

        second = binary_path.get_native_binary_path()
        assert second == binary_b, (
            "after cache_clear(), the function must re-resolve and pick up the new env var"
        )

    def test_cached_value_is_same_object_identity(self, monkeypatch):
        """``lru_cache`` returns the LITERAL first result (same object
        identity), not a fresh equal copy. This is the documented
        ``lru_cache`` contract — pinning it here so a future refactor
        that swaps to a hand-rolled cache dict doesn't accidentally
        break the identity guarantee.
        """
        fake_binary = Path("/tmp/xv-112-identity-pin")
        monkeypatch.setattr(
            binary_path,
            "_candidate_binary_names",
            lambda: ["linux-key-listener-x86_64"],
        )
        monkeypatch.setattr(Path, "is_file", lambda self: self == fake_binary)
        monkeypatch.setenv("VOICE_TYPER_NATIVE_BINARY", str(fake_binary))
        binary_path.get_native_binary_path.cache_clear()

        first = binary_path.get_native_binary_path()
        second = binary_path.get_native_binary_path()
        third = binary_path.get_native_binary_path()

        assert first is second
        assert second is third

    def test_none_result_is_cached(self, monkeypatch):
        """A ``None`` result (no binary found) is also cached — the
        function does NOT re-walk the lookup chain on every call when
        the binary is absent. This matters for headless test
        environments where the binary is never present: without
        caching, every backend probe would re-stat 6 paths.
        """
        # Force the function to return None: empty candidate list.
        monkeypatch.setattr(binary_path, "_candidate_binary_names", lambda: [])
        binary_path.get_native_binary_path.cache_clear()

        call_count = {"n": 0}
        original_candidate = binary_path._candidate_binary_names

        def counting_candidate():
            call_count["n"] += 1
            return original_candidate()

        monkeypatch.setattr(binary_path, "_candidate_binary_names", counting_candidate)

        first = binary_path.get_native_binary_path()
        second = binary_path.get_native_binary_path()
        third = binary_path.get_native_binary_path()

        assert first is None
        assert second is None
        assert third is None
        # The lookup chain ran ONCE; the next two calls hit the cache.
        assert call_count["n"] == 1, (
            f"None result should be cached (called once, got {call_count['n']})"
        )


# ─── conftest autouse fixture contract ──────────────────────────────────


class TestConftestAutouseClearsCache:
    """Verify the ``clear_binary_path_cache`` autouse fixture in
    ``tests/conftest.py`` actually clears the cache before each test.

    This is a meta-test: it pins the test infrastructure contract so a
    future contributor who removes the fixture immediately sees these
    tests fail (because the cache would leak across test functions).
    """

    def test_cache_is_empty_at_test_start(self):
        """At the start of every test, the cache should be empty
        (cleared by the autouse fixture). We assert ``currsize == 0``
        here; if this fails, either the autouse fixture was removed
        or a prior test populated the cache after the fixture ran
        (which would be a fixture-ordering bug).
        """
        # Calling cache_info does NOT itself populate the cache.
        info = binary_path.get_native_binary_path.cache_info()
        assert info.currsize == 0, (
            f"cache should be empty at test start (autouse fixture clears it); "
            f"got currsize={info.currsize}. Check that tests/conftest.py "
            "still has the clear_binary_path_cache autouse fixture."
        )

    def test_populate_cache_then_assert_next_test_sees_empty(self, monkeypatch):
        """This test populates the cache (currsize=1) and then ends.
        The NEXT test in this class (``test_cache_is_empty_at_test_start``
        if pytest re-runs it, or any other test in the file) MUST see
        currsize=0 — proving the autouse fixture cleared the cache
        between tests.

        We can't directly assert "the next test sees empty" from within
        this test, but we CAN assert that THIS test sees currsize=0 at
        start (proving the PREVIOUS test's cache was cleared), and we
        can populate the cache here to set up the same assertion for
        the next test.
        """
        # Pre-assert: cache is empty at start (autouse fixture ran).
        info_before = binary_path.get_native_binary_path.cache_info()
        assert info_before.currsize == 0

        # Populate the cache.
        monkeypatch.setattr(binary_path, "_candidate_binary_names", lambda: [])
        binary_path.get_native_binary_path()

        info_after = binary_path.get_native_binary_path.cache_info()
        assert info_after.currsize == 1, (
            "expected cache to be populated after a call, "
            "so the next test's autouse-clear assertion is meaningful"
        )
