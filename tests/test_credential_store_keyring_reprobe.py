"""Focused regression tests for the keyring availability cache re-probe policy.

Background
----------
``is_keyring_available()`` historically cached its first probe result for the
entire process lifetime. On a system where the keyring backend was
unavailable at startup (e.g. ``gnome-keyring-daemon`` not yet running on a
headless Linux session, or the macOS Keychain locked at login window), every
subsequent ``store_secret`` / ``load_secret`` call for the rest of the
session fell through to the plaintext fallback in ``config.json`` — even if
the backend appeared seconds later. A user who started the daemon mid-session
had to fully restart the app to get OS-keychain storage.

The fix introduces a rate-limited on-demand re-probe:

* When the cache says **available** (True), the result is cached for the
  process lifetime — a working backend doesn't suddenly disappear.
* When the cache says **unavailable** (False), the result is cached only
  for :data:`_KEYRING_REPROBE_INTERVAL_SECONDS` seconds. The next call after
  that interval re-probes.

These tests pin both branches plus the rate-limit (no back-to-back probes
inside the interval) and the test-helper ``_reset_keyring_cache`` clearing
the probe timestamp.
"""

from __future__ import annotations

import sys
import time
from unittest.mock import MagicMock

import pytest
from voice_typer.server import credential_store

# ── Fixtures ────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _isolated_cache():
    """Reset the keyring availability cache + probe timestamp around each test.

    Without this, the module-level cache leaks state across tests (the first
    test that probes "unavailable" would cause every subsequent test to see
    the cached False until the re-probe interval elapsed — which is 5
    minutes by default).
    """
    credential_store._reset_keyring_cache()
    yield
    credential_store._reset_keyring_cache()


def _stub_probe(available: bool, *, recorder: list[int] | None = None):
    """Return a probe stub that records each invocation.

    The stub returns a fixed ``(available, backend_name, reason)`` tuple
    and appends ``1`` to ``recorder`` on each call so the test can assert
    how many times the probe actually fired (vs. was short-circuited by
    the cache).
    """

    def _probe():
        if recorder is not None:
            recorder.append(1)
        if available:
            return (True, "FakeKeyring", None)
        return (False, "fail", "no usable keyring backend (fail backend selected)")

    return _probe


# Also install a dummy ``keyring`` module + fail backend so the stub
# never has to actually import the real library (the stub bypasses
# ``_probe_keyring`` entirely, but the production code still imports
# ``keyring`` lazily inside ``_probe_keyring`` for the non-stubbed path —
# we install the dummy to keep any incidental import side-effect clean).
@pytest.fixture(autouse=True)
def _dummy_keyring_module(monkeypatch):
    fake_keyring = MagicMock()
    fake_keyring.get_keyring.return_value = MagicMock(name="FakeBackend")
    monkeypatch.setitem(sys.modules, "keyring", fake_keyring)
    fail_module = MagicMock()
    fail_module.Keyring = type("FailKeyring", (), {})
    monkeypatch.setitem(sys.modules, "keyring.backends.fail", fail_module)


# ── Cache-forever branch: available=True ───────────────────────────────


class TestAvailableResultCachedForProcessLifetime:
    """A positive probe result must be cached for the process lifetime —
    a once-working backend doesn't suddenly break, and re-probing on every
    ``load_secret`` would trip the D-Bus round-trip cost 5x at startup."""

    def test_available_result_is_cached(self, monkeypatch):
        calls: list[int] = []
        monkeypatch.setattr(credential_store, "_probe_keyring", _stub_probe(True, recorder=calls))

        assert credential_store.is_keyring_available() is True
        assert credential_store.is_keyring_available() is True
        assert credential_store.is_keyring_available() is True

        # Only the first call probes — the next two hit the cache.
        assert len(calls) == 1

    def test_available_result_survives_long_time_travel(self, monkeypatch):
        """Even after a long simulated elapsed time, an available result
        is NOT re-probed (a working backend doesn't disappear)."""
        calls: list[int] = []
        monkeypatch.setattr(credential_store, "_probe_keyring", _stub_probe(True, recorder=calls))

        assert credential_store.is_keyring_available() is True
        # Pretend a day has passed.
        credential_store._keyring_last_probe_ts = time.time() - 86400.0
        assert credential_store.is_keyring_available() is True
        assert len(calls) == 1, "available result must not be re-probed regardless of elapsed time"


# ── Re-probe branch: unavailable=False ─────────────────────────────────


class TestUnavailableResultReprobedAfterInterval:
    """A negative probe result must be re-probed once the configured
    interval has elapsed — so a backend that appears mid-session is
    picked up on the next ``store_secret`` / ``load_secret`` call."""

    def test_unavailable_result_cached_within_interval(self, monkeypatch):
        calls: list[int] = []
        monkeypatch.setattr(credential_store, "_probe_keyring", _stub_probe(False, recorder=calls))

        assert credential_store.is_keyring_available() is False
        # Second call within the interval — should hit the cache.
        assert credential_store.is_keyring_available() is False
        assert len(calls) == 1, "second call inside interval must NOT re-probe"

    def test_unavailable_result_reprobed_after_interval(self, monkeypatch):
        """After the re-probe interval elapses, the next call must re-probe.

        Simulates: backend was unavailable at startup; 5 minutes later the
        user starts ``gnome-keyring-daemon`` and the next ``load_secret``
        picks it up.
        """
        calls: list[int] = []

        def _flap_probe():
            calls.append(1)
            # First probe: unavailable. Subsequent probes: available
            # (simulating the daemon appeared between calls).
            if len(calls) == 1:
                return (False, "fail", "no usable keyring backend (fail backend selected)")
            return (True, "SecretServiceKeyring", None)

        monkeypatch.setattr(credential_store, "_probe_keyring", _flap_probe)

        # First call: probes, returns False.
        assert credential_store.is_keyring_available() is False
        assert len(calls) == 1

        # Move the probe timestamp far into the past so the interval gate
        # opens. (We don't sleep — just rewrite the timestamp.)
        credential_store._keyring_last_probe_ts = time.time() - (
            credential_store._KEYRING_REPROBE_INTERVAL_SECONDS + 1.0
        )

        # Next call must re-probe and pick up the now-available backend.
        assert credential_store.is_keyring_available() is True
        assert len(calls) == 2

    def test_reprobe_interval_is_configurable(self, monkeypatch):
        """``_KEYRING_REPROBE_INTERVAL_SECONDS`` is a module-level constant that
        tests / operators can tune. Sanity-check that lowering it shortens
        the cache window."""
        original = credential_store._KEYRING_REPROBE_INTERVAL_SECONDS
        try:
            credential_store._KEYRING_REPROBE_INTERVAL_SECONDS = 0.0  # always re-probe
            calls: list[int] = []

            def _probe():
                calls.append(1)
                return (False, "fail", "no usable backend")

            monkeypatch.setattr(credential_store, "_probe_keyring", _probe)

            assert credential_store.is_keyring_available() is False
            assert credential_store.is_keyring_available() is False
            # Interval=0 means every call re-probes.
            assert len(calls) == 2
        finally:
            credential_store._KEYRING_REPROBE_INTERVAL_SECONDS = original

    def test_reprobe_records_probe_timestamp(self, monkeypatch):
        """The interval gate records a float timestamp close to ``time.time``.

        Production uses ``time.time`` for the re-probe interval gate (a
        wall-clock delta over a 300s window is immune to NTP steps that
        would otherwise trigger spurious re-probes). This pins the
        production attribute name (``_keyring_last_probe_ts``) and the
        ``0.0`` "never probed" sentinel.
        """
        # Set the cache to unavailable with a known timestamp.
        monkeypatch.setattr(credential_store, "_probe_keyring", _stub_probe(False))
        assert credential_store.is_keyring_available() is False
        # The recorded timestamp must be a float close to "now".
        ts = credential_store._keyring_last_probe_ts
        assert isinstance(ts, float)
        assert abs(ts - time.time()) < 5.0


# ── Test-helper contract: _reset_keyring_cache ─────────────────────────


class TestResetKeyringCacheClearsProbeTimestamp:
    """``_reset_keyring_cache`` is the test-only escape hatch. It MUST
    clear ``_keyring_last_probe_ts`` too — otherwise the re-probe
    interval gate would skip the probe even after the cache is cleared,
    breaking every test that relies on a forced re-probe."""

    def test_reset_clears_probe_timestamp(self, monkeypatch):
        monkeypatch.setattr(credential_store, "_probe_keyring", _stub_probe(False))
        # Populate the cache + timestamp.
        assert credential_store.is_keyring_available() is False
        assert credential_store._keyring_last_probe_ts != 0.0

        credential_store._reset_keyring_cache()

        assert credential_store._keyring_available_cache is None
        assert credential_store._keyring_backend_name_cache is None
        assert credential_store._keyring_reason_cache is None
        assert credential_store._keyring_last_probe_ts == 0.0

    def test_reset_forces_next_call_to_reprobe(self, monkeypatch):
        """After ``_reset_keyring_cache``, the next ``is_keyring_available``
        call must probe (not return the cached value)."""
        calls: list[int] = []
        monkeypatch.setattr(credential_store, "_probe_keyring", _stub_probe(False, recorder=calls))

        assert credential_store.is_keyring_available() is False
        assert len(calls) == 1

        # Without reset, the next call hits the cache.
        assert credential_store.is_keyring_available() is False
        assert len(calls) == 1

        # After reset, the next call MUST re-probe (this is the whole
        # point of the helper).
        credential_store._reset_keyring_cache()
        assert credential_store.is_keyring_available() is False
        assert len(calls) == 2


# ── Concurrency: probe serialization ──────────────────────────────────


class TestConcurrentReprobesAreSerialized:
    """The re-probe lock must serialize concurrent probes so two threads
    calling ``is_keyring_available`` at the same time (after the interval
    has elapsed) only fire ONE probe, not two."""

    def test_two_concurrent_calls_fire_one_probe(self, monkeypatch):
        import threading

        calls: list[int] = []
        call_lock = threading.Lock()

        def _slow_probe():
            with call_lock:
                calls.append(1)
            # Simulate a slow probe (D-Bus round-trip).
            time.sleep(0.05)
            return (False, "fail", "no usable backend")

        monkeypatch.setattr(credential_store, "_probe_keyring", _slow_probe)

        # Force the interval gate open so both threads enter the slow path.
        credential_store._keyring_available_cache = False
        credential_store._keyring_last_probe_ts = time.time() - 86400.0

        barrier = threading.Barrier(2)
        results: list[bool] = []
        result_lock = threading.Lock()

        def _worker():
            barrier.wait()
            r = credential_store.is_keyring_available()
            with result_lock:
                results.append(r)

        t1 = threading.Thread(target=_worker)
        t2 = threading.Thread(target=_worker)
        t1.start()
        t2.start()
        t1.join(timeout=5.0)
        t2.join(timeout=5.0)

        assert len(results) == 2
        # The probe should have fired exactly once — the second thread
        # observed the cache populated by the first under the lock.
        assert len(calls) == 1, f"concurrent re-probes must be serialized; saw {len(calls)} probes"
