"""XV-119: ``_config_dir()`` is memoized via ``@functools.lru_cache``.

Background
----------
``voice_typer/server/config.py:_config_dir()`` previously performed
live filesystem + env-var resolution on EVERY call: ``Path.resolve``,
``Path.exists``, and ``os.path.commonpath`` (via
``_validate_path_safety``) issued 30-50 ``stat()`` syscalls at startup
across ~29 call sites in ``config.py``, ``credential_store.py``,
``security.py``, and ``_paths.py``, plus 3+ per ``Config.save()``.

The fix wraps the function in ``@functools.lru_cache(maxsize=1)`` so
the resolution runs at most once per process.  The function is
deterministic w.r.t. ``os.environ`` + ``Path.home()`` + the existence
of the legacy ``~/.voice-typer`` directory, all of which are stable
for the process lifetime in production.

Tests that change those inputs (e.g. ``monkeypatch.setenv`` /
``monkeypatch.setattr(Path, "home", ...)``) must call the
``_reset_config_dir_cache()`` test helper — mirrors
``voice_typer.server.credential_store._reset_keyring_cache``.

What these tests verify
-----------------------
1. ``_config_dir`` exposes the ``functools.lru_cache`` API
   (``cache_info`` / ``cache_clear``) with ``maxsize=1``.
2. Repeated calls return the SAME ``Path`` object (cache hits — no
   re-allocation, no re-stat).
3. ``_reset_config_dir_cache()`` clears the cache (``currsize=0``,
   ``hits=0``, ``misses=0`` after the reset).
4. ``_reset_config_dir_cache()`` is idempotent (safe to call from an
   autouse fixture that doesn't know whether the cache has been
   populated).
5. The cache, not the function, is what returns stale state after an
   env-var change — and ``_reset_config_dir_cache()`` is the release
   valve (mirrors the ``_reset_keyring_cache`` contract).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from voice_typer.server import config


@pytest.fixture(autouse=True)
def _reset_cache_around_each_test():
    """Clear the ``_config_dir`` lru_cache before AND after each test.

    Ensures no test sees stale state from a prior test's call (in this
    file or any other), and that this file's tests don't leak cached
    state to subsequent test files in the same pytest session.
    """
    config._reset_config_dir_cache()
    yield
    config._reset_config_dir_cache()


# the function is wrapped in functools.lru_cache ──────────────


def test_config_dir_exposes_lru_cache_api():
    """XV-119: ``_config_dir`` carries the ``functools.lru_cache``
    marker attributes (``cache_info`` / ``cache_clear``).

    A plain function or a lambda does NOT expose these — their presence
    is the structural proof that the decorator was applied.
    """
    assert hasattr(config._config_dir, "cache_info"), (
        "_config_dir must be wrapped in functools.lru_cache (no cache_info attribute found)"
    )
    assert hasattr(config._config_dir, "cache_clear"), (
        "_config_dir must be wrapped in functools.lru_cache (no cache_clear attribute found)"
    )


def test_config_dir_lru_cache_maxsize_is_one():
    """XV-119: the cache is configured with ``maxsize=1``.

    The function takes no arguments, so a single-slot cache is
    sufficient — every call after the first is a hit.  A larger
    maxsize would be a waste of memory; a maxsize of ``None``
    (unbounded) or ``0`` (disabled) would defeat the fix.
    """
    info = config._config_dir.cache_info()
    assert info.maxsize == 1, f"_config_dir lru_cache maxsize must be 1 (got {info.maxsize})"


# repeated calls hit the cache (same Path object) ─────────────


def test_config_dir_returns_same_object_on_repeated_calls():
    """XV-119: repeated calls return the SAME ``Path`` object.

    ``functools.lru_cache`` returns the cached object on a hit (not a
    freshly-allocated equal one) — this is what makes the cache a perf
    win: no ``Path`` re-allocation and, crucially, no re-``stat()``
    inside ``_validate_path_safety`` / ``Path.resolve`` / ``Path.exists``.
    """
    first = config._config_dir()
    second = config._config_dir()
    third = config._config_dir()
    # Identity comparison — lru_cache returns the same object on hits.
    assert first is second, (
        "repeated _config_dir() calls must return the SAME Path object (lru_cache hit); got distinct objects"
    )
    assert second is third, (
        "repeated _config_dir() calls must return the SAME Path object (lru_cache hit); got distinct objects"
    )


def test_config_dir_cache_hit_miss_counts():
    """XV-119: ``cache_info()`` reports exactly one miss (the first
    call) and the rest as hits.

    This is the call-count assertion referenced in the XV-119 fix
    brief: the underlying function body (with all its ``stat()`` /
    ``resolve()`` / ``commonpath`` work) runs exactly ONCE across
    N calls.
    """
    # Fixture already cleared the cache — first call is a miss.
    config._config_dir()
    config._config_dir()
    config._config_dir()
    config._config_dir()
    info = config._config_dir.cache_info()
    assert info.misses == 1, f"expected exactly 1 cache miss (the first call), got {info.misses}"
    assert info.hits == 3, f"expected 3 cache hits (calls 2-4), got {info.hits}"
    assert info.currsize == 1, f"expected currsize=1 (one slot, one entry), got {info.currsize}"


# _reset_config_dir_cache() clears the cache ──────────────────


def test_reset_config_dir_cache_clears_cache():
    """XV-119: ``_reset_config_dir_cache()`` clears the lru_cache."""
    # Populate the cache.
    config._config_dir()
    assert config._config_dir.cache_info().currsize == 1, "cache should have currsize=1 after a call"

    # Clear it.
    config._reset_config_dir_cache()

    info = config._config_dir.cache_info()
    assert info.currsize == 0, f"expected currsize=0 after _reset_config_dir_cache, got {info.currsize}"
    assert info.hits == 0, f"expected hits=0 after _reset_config_dir_cache, got {info.hits}"
    assert info.misses == 0, f"expected misses=0 after _reset_config_dir_cache, got {info.misses}"


def test_reset_config_dir_cache_is_idempotent():
    """XV-119: calling ``_reset_config_dir_cache`` on an empty cache
    is a no-op (safe to call from autouse fixtures that don't know
    whether the cache has been populated)."""
    # Cache is already empty (autouse fixture cleared it).
    config._reset_config_dir_cache()  # should not raise
    config._reset_config_dir_cache()  # should not raise
    config._reset_config_dir_cache()  # should not raise
    assert config._config_dir.cache_info().currsize == 0


def test_reset_config_dir_cache_forces_re_resolution():
    """XV-119: after ``_reset_config_dir_cache()``, the next call
    re-invokes the underlying function (a fresh miss).

    This is what makes the helper useful for tests that change
    ``os.environ`` / ``Path.home()`` / the legacy-dir existence: the
    first call after the reset observes the new inputs.
    """
    # First population.
    config._config_dir()
    assert config._config_dir.cache_info().misses == 1

    # Reset — the next call must be a fresh miss.
    config._reset_config_dir_cache()
    config._config_dir()
    info = config._config_dir.cache_info()
    assert info.misses == 1, f"expected exactly 1 miss after reset + 1 call, got {info.misses}"
    assert info.hits == 0, f"expected 0 hits after reset + 1 call, got {info.hits}"


# env-var changes require a cache reset (mirrors keyring) ─────


def test_cache_returns_stale_value_until_reset(monkeypatch, tmp_path):
    """XV-119: ``VOICE_TYPER_CONFIG_DIR`` set AFTER the first call is
    NOT observed until ``_reset_config_dir_cache()`` is called.

    This is the documented contract: the function is deterministic
    w.r.t. ``os.environ`` + ``Path.home()`` + filesystem state at
    process lifetime, and tests that change any of those inputs must
    call ``_reset_config_dir_cache()`` — exactly mirroring
    ``credential_store._reset_keyring_cache()`` for the keyring-probe
    cache.
    """
    # Pin Path.home() to tmp_path so the SEC-005 path-traversal
    # validator accepts the custom config dirs below (they need to be
    # within Path.home()).  Restored by monkeypatch at teardown.
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    custom_a = tmp_path / "config_a"
    custom_a.mkdir()
    custom_b = tmp_path / "config_b"
    custom_b.mkdir()

    # First call with custom_a — populates the cache.
    monkeypatch.setenv("VOICE_TYPER_CONFIG_DIR", str(custom_a))
    config._reset_config_dir_cache()
    first = config._config_dir()
    assert first == custom_a, f"first call should return custom_a ({custom_a}), got {first}"

    # Change env to custom_b WITHOUT resetting the cache — the cache
    # must return the stale custom_a (this is the documented behavior).
    monkeypatch.setenv("VOICE_TYPER_CONFIG_DIR", str(custom_b))
    stale = config._config_dir()
    assert stale == custom_a, (
        "cache must return the stale value (custom_a) until "
        "_reset_config_dir_cache() is called; the function body must "
        "NOT re-run on a cache hit"
    )
    assert stale is first, "stale value must be the SAME object as the first call (lru_cache hit)"

    # After reset, the new env var takes effect.
    config._reset_config_dir_cache()
    fresh = config._config_dir()
    assert fresh == custom_b, (
        f"after _reset_config_dir_cache, the new VOICE_TYPER_CONFIG_DIR "
        f"value (custom_b={custom_b}) must be picked up; got {fresh}"
    )
    assert fresh is not first, (
        "after reset + env change, the new Path object must be distinct from the stale cached one"
    )


def test_reset_helper_mirrors_keyring_cache_convention():
    """XV-119: ``_reset_config_dir_cache`` follows the same naming +
    behavior convention as ``credential_store._reset_keyring_cache``.

    Both are test-only helpers that clear a module-level cache so
    tests that change the cached function's inputs can force
    re-evaluation.  This test pins the convention so a future
    refactor doesn't silently diverge the two APIs.
    """
    from voice_typer.server import credential_store

    # Both helpers must exist and be callable.
    assert callable(config._reset_config_dir_cache), "config._reset_config_dir_cache must be callable"
    assert callable(credential_store._reset_keyring_cache), (
        "credential_store._reset_keyring_cache must be callable "
        "(reference implementation for the test-helper convention)"
    )

    # Both must accept no arguments (they clear module-level state).
    import inspect

    sig_cfg = inspect.signature(config._reset_config_dir_cache)
    assert len(sig_cfg.parameters) == 0, (
        f"_reset_config_dir_cache must take no arguments (got params={list(sig_cfg.parameters)})"
    )
    sig_keyring = inspect.signature(credential_store._reset_keyring_cache)
    assert len(sig_keyring.parameters) == 0, (
        f"_reset_keyring_cache must take no arguments (got params={list(sig_keyring.parameters)})"
    )

    # Both must be safe to call with an empty cache (idempotent).
    config._reset_config_dir_cache()
    credential_store._reset_keyring_cache()
