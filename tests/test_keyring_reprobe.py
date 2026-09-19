"""DJ-27: Stale keyring availability cache; re-probe on slow cadence."""

from __future__ import annotations

import time

import pytest
from voice_typer.server import credential_store


@pytest.fixture(autouse=True)
def _reset_caches():
    """Reset the keyring cache + probe timestamp before AND after each test."""
    credential_store._reset_keyring_cache()
    yield
    credential_store._reset_keyring_cache()


def _install_fake_probe(monkeypatch, available: bool, reason: str | None = None):
    """Install a fake ``_probe_keyring`` that records every call."""
    probe_calls: list = []
    backend_name = "FakeKeyring" if available else "fail"
    if reason is None:
        reason = None if available else "no usable keyring backend (fail backend selected)"

    def _fake_probe():
        probe_calls.append(time.time())
        credential_store._keyring_last_probe_ts = time.time()
        return (available, backend_name, reason)

    monkeypatch.setattr(credential_store, "_probe_keyring", _fake_probe)
    return probe_calls


class TestReprobePolicy:
    """DJ-27: ``is_keyring_available()`` re-probes on a slow cadence"""

    def test_first_call_always_probes(self, monkeypatch):
        """DJ-27 sanity: the first call (cache is None) always probes."""
        probe_calls = _install_fake_probe(monkeypatch, available=True)
        assert credential_store._keyring_available_cache is None

        result = credential_store.is_keyring_available()

        assert result is True
        assert len(probe_calls) == 1, f"first call must probe exactly once, got {len(probe_calls)} probes"

    def test_second_call_within_5min_does_not_reprobe_available(self, monkeypatch):
        """DJ-27: when cache=True, no re-probe (available backends don't"""
        probe_calls = _install_fake_probe(monkeypatch, available=True)
        credential_store.is_keyring_available()  # first probe
        assert len(probe_calls) == 1

        # Second call within 5 minutes, cache hit, no re-probe.
        credential_store.is_keyring_available()
        assert len(probe_calls) == 1, (
            "DJ-27: cache=True path must NOT re-probe (available backends don't need re-probing)"
        )

    def test_second_call_within_5min_does_not_reprobe_unavailable(self, monkeypatch):
        """DJ-27: when cache=False AND last probe was <5 min ago, no"""
        probe_calls = _install_fake_probe(monkeypatch, available=False)
        credential_store.is_keyring_available()  # first probe → False
        assert len(probe_calls) == 1
        assert credential_store._keyring_available_cache is False

        # Second call within 5 minutes, cache hit, no re-probe.
        credential_store.is_keyring_available()
        assert len(probe_calls) == 1, "DJ-27: cache=False path within 5 min of last probe must NOT re-probe"

    def test_reprobe_after_5min_when_unavailable(self, monkeypatch):
        """DJ-27: when cache=False AND last probe was >5 min ago, re-probe."""
        probe_calls = _install_fake_probe(monkeypatch, available=False)
        credential_store.is_keyring_available()  # first probe → False
        assert len(probe_calls) == 1
        assert credential_store._keyring_available_cache is False

        # Simulate 5+ minutes passing by rewinding the probe timestamp.
        credential_store._keyring_last_probe_ts = time.time() - 301.0

        # Second call: cache=False AND last probe >5 min ago → re-probe.
        credential_store.is_keyring_available()
        assert len(probe_calls) == 2, "DJ-27: cache=False path >5 min after last probe must re-probe"

    def test_reprobe_picks_up_backend_appearing_mid_session(self, monkeypatch):
        """DJ-27: the re-probe must OBSERVE a backend that appeared mid-"""
        # First probe returns False (keychain locked at boot).
        probe_count = {"n": 0}

        def _fake_probe():
            probe_count["n"] += 1
            if probe_count["n"] == 1:
                return (False, "fail", "keychain locked")
            # Subsequent probes return True (keychain now unlocked).
            return (True, "SecretServiceKeyring", None)

        monkeypatch.setattr(credential_store, "_probe_keyring", _fake_probe)

        # First call: probes, returns False, caches False.
        assert credential_store.is_keyring_available() is False
        assert credential_store._keyring_available_cache is False

        # Rewind timestamp to simulate 5+ minutes passing.
        credential_store._keyring_last_probe_ts = time.time() - 301.0

        # Second call: re-probes, observes True, caches True.
        assert credential_store.is_keyring_available() is True, (
            "DJ-27: re-probe must pick up a backend that appeared mid-session"
        )
        assert credential_store._keyring_available_cache is True
        # Subsequent calls do NOT re-probe (cache=True path).
        credential_store.is_keyring_available()
        assert probe_count["n"] == 2

    def test_reprobe_interval_is_300_seconds(self):
        """DJ-27: the re-probe interval is 300 seconds (5 minutes)."""
        assert credential_store._KEYRING_REPROBE_INTERVAL_SECONDS == 300.0

    def test_last_probe_timestamp_initialized_to_zero(self):
        """DJ-27: ``_keyring_last_probe_ts`` starts at 0.0 so the first"""
        credential_store._reset_keyring_cache()
        assert credential_store._keyring_last_probe_ts == 0.0

    def test_reset_keyring_cache_resets_timestamp(self, monkeypatch):
        """DJ-27: ``_reset_keyring_cache()`` also resets the probe"""
        # Populate the timestamp by probing.
        _install_fake_probe(monkeypatch, available=True)
        credential_store.is_keyring_available()
        assert credential_store._keyring_last_probe_ts > 0.0

        credential_store._reset_keyring_cache()

        assert credential_store._keyring_last_probe_ts == 0.0, (
            "DJ-27: _reset_keyring_cache must reset the probe timestamp"
        )
