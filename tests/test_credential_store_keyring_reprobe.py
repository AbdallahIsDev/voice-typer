"""Focused regression tests for the keyring availability cache re-probe policy."""

from __future__ import annotations

import sys
import time
from unittest.mock import MagicMock

import pytest
from voice_typer.server import credential_store


@pytest.fixture(autouse=True)
def _isolated_cache():
    """Reset the keyring availability cache + probe timestamp around each test."""
    credential_store._reset_keyring_cache()
    yield
    credential_store._reset_keyring_cache()


def _stub_probe(available: bool, *, recorder: list[int] | None = None):
    """Return a probe stub that records each invocation."""

    def _probe():
        if recorder is not None:
            recorder.append(1)
        if available:
            return (True, "FakeKeyring", None)
        return (False, "fail", "no usable keyring backend (fail backend selected)")

    return _probe


# Also install a dummy ``keyring`` module + fail backend so the stub
@pytest.fixture(autouse=True)
def _dummy_keyring_module(monkeypatch):
    fake_keyring = MagicMock()
    fake_keyring.get_keyring.return_value = MagicMock(name="FakeBackend")
    monkeypatch.setitem(sys.modules, "keyring", fake_keyring)
    fail_module = MagicMock()
    fail_module.Keyring = type("FailKeyring", (), {})
    monkeypatch.setitem(sys.modules, "keyring.backends.fail", fail_module)


class TestAvailableResultCachedForProcessLifetime:
    """A positive probe result must be cached for the process lifetime —"""

    def test_available_result_is_cached(self, monkeypatch):
        calls: list[int] = []
        monkeypatch.setattr(credential_store, "_probe_keyring", _stub_probe(True, recorder=calls))

        assert credential_store.is_keyring_available() is True
        assert credential_store.is_keyring_available() is True
        assert credential_store.is_keyring_available() is True

        # Only the first call probes, the next two hit the cache.
        assert len(calls) == 1

    def test_available_result_survives_long_time_travel(self, monkeypatch):
        """Even after a long simulated elapsed time, an available result"""
        calls: list[int] = []
        monkeypatch.setattr(credential_store, "_probe_keyring", _stub_probe(True, recorder=calls))

        assert credential_store.is_keyring_available() is True
        # Pretend a day has passed.
        credential_store._keyring_last_probe_ts = time.time() - 86400.0
        assert credential_store.is_keyring_available() is True
        assert len(calls) == 1, "available result must not be re-probed regardless of elapsed time"


class TestUnavailableResultReprobedAfterInterval:
    """A negative probe result must be re-probed once the configured"""

    def test_unavailable_result_cached_within_interval(self, monkeypatch):
        calls: list[int] = []
        monkeypatch.setattr(credential_store, "_probe_keyring", _stub_probe(False, recorder=calls))

        assert credential_store.is_keyring_available() is False
        # Second call within the interval, should hit the cache.
        assert credential_store.is_keyring_available() is False
        assert len(calls) == 1, "second call inside interval must NOT re-probe"

    def test_unavailable_result_reprobed_after_interval(self, monkeypatch):
        """After the re-probe interval elapses, the next call must re-probe."""
        calls: list[int] = []

        def _flap_probe():
            calls.append(1)
            # First probe: unavailable. Subsequent probes: available
            if len(calls) == 1:
                return (False, "fail", "no usable keyring backend (fail backend selected)")
            return (True, "SecretServiceKeyring", None)

        monkeypatch.setattr(credential_store, "_probe_keyring", _flap_probe)

        # First call: probes, returns False.
        assert credential_store.is_keyring_available() is False
        assert len(calls) == 1

        # Move the probe timestamp far into the past so the interval gate
        credential_store._keyring_last_probe_ts = time.time() - (
            credential_store._KEYRING_REPROBE_INTERVAL_SECONDS + 1.0
        )

        # Next call must re-probe and pick up the now-available backend.
        assert credential_store.is_keyring_available() is True
        assert len(calls) == 2

    def test_reprobe_interval_is_configurable(self, monkeypatch):
        """tests / operators can tune. Sanity-check that lowering it shortens"""
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
        """The interval gate records a float timestamp close to ``time.time``."""
        # Set the cache to unavailable with a known timestamp.
        monkeypatch.setattr(credential_store, "_probe_keyring", _stub_probe(False))
        assert credential_store.is_keyring_available() is False
        # The recorded timestamp must be a float close to "now".
        ts = credential_store._keyring_last_probe_ts
        assert isinstance(ts, float)
        assert abs(ts - time.time()) < 5.0


class TestResetKeyringCacheClearsProbeTimestamp:
    """``_reset_keyring_cache`` is the test-only escape hatch. It MUST"""

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
        """After ``_reset_keyring_cache``, the next ``is_keyring_available``"""
        calls: list[int] = []
        monkeypatch.setattr(credential_store, "_probe_keyring", _stub_probe(False, recorder=calls))

        assert credential_store.is_keyring_available() is False
        assert len(calls) == 1

        # Without reset, the next call hits the cache.
        assert credential_store.is_keyring_available() is False
        assert len(calls) == 1

        # After reset, the next call MUST re-probe (this is the whole
        credential_store._reset_keyring_cache()
        assert credential_store.is_keyring_available() is False
        assert len(calls) == 2


class TestConcurrentReprobesAreSerialized:
    """The re-probe lock must serialize concurrent probes so two threads"""

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
        # The probe should have fired exactly once, the second thread
        assert len(calls) == 1, f"concurrent re-probes must be serialized; saw {len(calls)} probes"
