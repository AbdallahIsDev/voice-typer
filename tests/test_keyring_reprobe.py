"""DJ-27 — Stale keyring availability cache; re-probe on slow cadence.

``is_keyring_available()`` caches the probe result for the lifetime of
the process. On macOS with a locked keychain at app startup, the probe
read may raise → cached as ``False``. On Linux headless without
gnome-keyring-daemon, the probe returns ``(False, "fail", ...)``. In
both cases, the cache says "unavailable" for the entire process
lifetime — even if the user subsequently unlocks their keychain or
installs gnome-keyring-daemon mid-session. Every subsequent API key
operation routes to the plaintext fallback for the entire session.

DJ-27 fix: in ``is_keyring_available()``, when the cached result is
``False`` AND the last probe was more than 5 minutes ago, re-probe and
update the cache. The re-probe only fires on the unavailable path (a
backend that's already available doesn't need re-probing) to bound the
probe rate on a permanently-unavailable backend.

This test file asserts:

  1. The first ``is_keyring_available()`` call probes (cache is None).
  2. A second call within 5 minutes does NOT re-probe (cache hit).
  3. A call AFTER 5 minutes (with cache=False) DOES re-probe.
  4. A call AFTER 5 minutes (with cache=True) does NOT re-probe
     (available backends don't need re-probing).
  5. ``_reset_keyring_cache()`` resets the timestamp so the next call
     always re-probes (tests don't have to wait 5 minutes).
"""

from __future__ import annotations

import time

import pytest
from voice_typer.server import credential_store

# ── Fixtures ────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _reset_caches():
    """Reset the keyring cache + probe timestamp before AND after each test."""
    credential_store._reset_keyring_cache()
    yield
    credential_store._reset_keyring_cache()


def _install_fake_probe(monkeypatch, available: bool, reason: str | None = None):
    """Install a fake ``_probe_keyring`` that records every call.

    Returns a list that grows by one element per probe call. Tests
    assert on ``len(probe_calls)`` to verify re-probe behavior.

    The fake ALSO updates ``_keyring_last_probe_ts`` so the
    slow-cadence re-probe gate in ``is_keyring_available`` evaluates
    correctly — without this, the timestamp stays at ``0.0`` (from
    ``_reset_keyring_cache``) and every call would re-probe because
    ``time.time() - 0.0 > 300`` is always True.
    """
    probe_calls: list = []
    backend_name = "FakeKeyring" if available else "fail"
    if reason is None:
        reason = None if available else "no usable keyring backend (fail backend selected)"

    def _fake_probe():
        probe_calls.append(time.time())
        # Mirror the real _probe_keyring's timestamp update so the
        # slow-cadence re-probe gate evaluates correctly.
        credential_store._keyring_last_probe_ts = time.time()
        return (available, backend_name, reason)

    monkeypatch.setattr(credential_store, "_probe_keyring", _fake_probe)
    return probe_calls


# ── DJ-27: re-probe policy ──────────────────────────────────────────────


class TestReprobePolicy:
    """DJ-27: ``is_keyring_available()`` re-probes on a slow cadence
    when the cached result is ``False``."""

    def test_first_call_always_probes(self, monkeypatch):
        """DJ-27 sanity: the first call (cache is None) always probes."""
        probe_calls = _install_fake_probe(monkeypatch, available=True)
        assert credential_store._keyring_available_cache is None

        result = credential_store.is_keyring_available()

        assert result is True
        assert len(probe_calls) == 1, f"first call must probe exactly once, got {len(probe_calls)} probes"

    def test_second_call_within_5min_does_not_reprobe_available(self, monkeypatch):
        """DJ-27: when cache=True, no re-probe (available backends don't
        need re-probing — bounds probe rate on a permanently-available
        backend)."""
        probe_calls = _install_fake_probe(monkeypatch, available=True)
        credential_store.is_keyring_available()  # first probe
        assert len(probe_calls) == 1

        # Second call within 5 minutes — cache hit, no re-probe.
        credential_store.is_keyring_available()
        assert len(probe_calls) == 1, (
            "DJ-27: cache=True path must NOT re-probe (available backends don't need re-probing)"
        )

    def test_second_call_within_5min_does_not_reprobe_unavailable(self, monkeypatch):
        """DJ-27: when cache=False AND last probe was <5 min ago, no
        re-probe (bounds probe rate on a permanently-unavailable backend)."""
        probe_calls = _install_fake_probe(monkeypatch, available=False)
        credential_store.is_keyring_available()  # first probe → False
        assert len(probe_calls) == 1
        assert credential_store._keyring_available_cache is False

        # Second call within 5 minutes — cache hit, no re-probe.
        credential_store.is_keyring_available()
        assert len(probe_calls) == 1, "DJ-27: cache=False path within 5 min of last probe must NOT re-probe"

    def test_reprobe_after_5min_when_unavailable(self, monkeypatch):
        """DJ-27: when cache=False AND last probe was >5 min ago, re-probe.
        This is the core fix — a backend that appears mid-session
        (user unlocks keychain, installs gnome-keyring-daemon) is picked
        up without an app restart."""
        probe_calls = _install_fake_probe(monkeypatch, available=False)
        credential_store.is_keyring_available()  # first probe → False
        assert len(probe_calls) == 1
        assert credential_store._keyring_available_cache is False

        # Simulate 5+ minutes passing by rewinding the probe timestamp.
        # _probe_keyring sets _keyring_last_probe_ts = time.time() on
        # every call. We rewind it to 301 seconds ago so the next
        # is_keyring_available call's "time.time() - _keyring_last_probe_ts"
        # check evaluates > 300.
        credential_store._keyring_last_probe_ts = time.time() - 301.0

        # Second call: cache=False AND last probe >5 min ago → re-probe.
        credential_store.is_keyring_available()
        assert len(probe_calls) == 2, "DJ-27: cache=False path >5 min after last probe must re-probe"

    def test_reprobe_picks_up_backend_appearing_mid_session(self, monkeypatch):
        """DJ-27: the re-probe must OBSERVE a backend that appeared mid-
        session. We simulate: first probe returns False (keychain locked);
        re-probe (after 5 min) returns True (keychain now unlocked).
        ``is_keyring_available()`` must return True after the re-probe."""
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
        """DJ-27: ``_keyring_last_probe_ts`` starts at 0.0 so the first
        ``is_keyring_available()`` call always probes (belt-and-suspenders
        alongside the ``cache is None`` check)."""
        credential_store._reset_keyring_cache()
        assert credential_store._keyring_last_probe_ts == 0.0

    def test_reset_keyring_cache_resets_timestamp(self, monkeypatch):
        """DJ-27: ``_reset_keyring_cache()`` also resets the probe
        timestamp so tests don't have to wait 5 minutes for a re-probe."""
        # Populate the timestamp by probing.
        _install_fake_probe(monkeypatch, available=True)
        credential_store.is_keyring_available()
        assert credential_store._keyring_last_probe_ts > 0.0

        credential_store._reset_keyring_cache()

        assert credential_store._keyring_last_probe_ts == 0.0, (
            "DJ-27: _reset_keyring_cache must reset the probe timestamp"
        )
