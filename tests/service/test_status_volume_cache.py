"""``voice_typer/server/service/status.py``."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from voice_typer.server.service.status import StatusMixin


@pytest.fixture
def status_mixin_with_mock_ducker():
    """Build a minimal ``StatusMixin`` instance backed by a MagicMock"""
    mixin = StatusMixin.__new__(StatusMixin)
    mixin._volume_backend_status_cache = None
    # Reset the class-level notify-once guard so each test starts
    StatusMixin._volume_ducker_init_warned = False
    # Build the mock app + ducker.
    app = MagicMock()
    ducker = MagicMock()
    ducker.is_available = True
    ducker.backend_name = "fake (test)"
    ducker.supports_per_session = False
    ducker.initialize = MagicMock(return_value=True)
    app._volume_ducker = ducker
    mixin._app = app
    return mixin, ducker


class TestVolumeBackendStatusCache:
    """Tests for the per-instance ``_volume_backend_status_cache``."""

    def test_first_call_invokes_initialize_once(self, status_mixin_with_mock_ducker):
        """The first call to ``get_volume_backend_status`` invokes"""
        mixin, ducker = status_mixin_with_mock_ducker
        assert mixin._volume_backend_status_cache is None

        result = mixin.get_volume_backend_status()

        assert ducker.initialize.call_count == 1
        assert mixin._volume_backend_status_cache is not None
        assert result["name"] == "fake (test)"
        assert result["available"] is True
        assert result["supports_per_session"] is False
        assert result["backend"] == "MagicMock"

    def test_subsequent_calls_use_cache_without_reinvoking_initialize(self, status_mixin_with_mock_ducker):
        """Subsequent calls return the cached dict and do NOT invoke"""
        mixin, ducker = status_mixin_with_mock_ducker
        # Prime the cache with one call.
        mixin.get_volume_backend_status()
        assert ducker.initialize.call_count == 1

        # Mutate the mock's backend_name to prove subsequent calls
        ducker.backend_name = "changed-after-cache"

        # Second call: should NOT invoke initialize() and should
        second = mixin.get_volume_backend_status()
        assert ducker.initialize.call_count == 1, (
            " regression: subsequent get_volume_backend_status "
            "call re-invoked ducker.initialize(). The cache should "
            "have short-circuited the call."
        )
        assert second["name"] == "fake (test)", (
            " regression: subsequent call returned the mutated "
            "backend_name instead of the cached value. The cache "
            "should have returned the original dict."
        )
        assert mixin._volume_backend_status_cache["name"] == "fake (test)"

        # A few more calls, initialize count stays at 1.
        for _ in range(5):
            mixin.get_volume_backend_status()
        assert ducker.initialize.call_count == 1

    def test_force_refresh_reinvokes_initialize_and_updates_cache(self, status_mixin_with_mock_ducker):
        """
        ``_force_refresh=True`` bypasses the cache, re-runs
        This is the UI's "Refresh Volume Backend" button contract:
        """
        mixin, ducker = status_mixin_with_mock_ducker
        # Prime the cache.
        first = mixin.get_volume_backend_status()
        assert first["name"] == "fake (test)"
        assert ducker.initialize.call_count == 1

        ducker.backend_name = "CoreAudio (pyobjc)"

        # Force refresh, bypasses the cache, re-invokes initialize,
        refreshed = mixin.get_volume_backend_status(_force_refresh=True)
        assert ducker.initialize.call_count == 2, (
            " regression: _force_refresh=True did not re-invoke "
            "ducker.initialize(). The cache should have been bypassed."
        )
        assert refreshed["name"] == "CoreAudio (pyobjc)"
        # The cache should now reflect the refreshed state.
        assert mixin._volume_backend_status_cache["name"] == "CoreAudio (pyobjc)"

        # A subsequent default call should NOT re-invoke initialize
        again = mixin.get_volume_backend_status()
        assert ducker.initialize.call_count == 2
        assert again["name"] == "CoreAudio (pyobjc)"

    def test_init_failure_does_not_populate_cache(self, status_mixin_with_mock_ducker):
        """When ``initialize()`` raises on the first call, the cache"""
        mixin, ducker = status_mixin_with_mock_ducker
        ducker.initialize.side_effect = RuntimeError("init failed")

        # First call: initialize() raises, status dict is still
        first = mixin.get_volume_backend_status()
        assert ducker.initialize.call_count == 1
        assert first["name"] == "fake (test)"
        # The cache should NOT be populated (init failed).
        assert mixin._volume_backend_status_cache is None, (
            " regression: cache was populated despite "
            "initialize() raising. The next poll should retry "
            "initialize(), caching the failed state would prevent "
            "auto-recovery when the user installs a missing "
            "dependency mid-session."
        )

        # Second call: should retry initialize (cache was not
        ducker.initialize.side_effect = None
        mixin.get_volume_backend_status()
        assert ducker.initialize.call_count == 2, (
            " regression: the second call did not retry "
            "initialize() after the first call failed. The cache "
            "should have been empty so the retry could fire."
        )
        # Now the cache should be populated.
        assert mixin._volume_backend_status_cache is not None

    def test_force_refresh_caches_even_on_init_failure(self, status_mixin_with_mock_ducker):
        """``_force_refresh=True`` caches the best-effort status even"""
        mixin, ducker = status_mixin_with_mock_ducker
        ducker.initialize.side_effect = RuntimeError("init failed")

        refreshed = mixin.get_volume_backend_status(_force_refresh=True)
        assert ducker.initialize.call_count == 1
        # Best-effort status is still returned.
        assert refreshed["name"] == "fake (test)"
        # Cache IS populated on explicit refresh (even with init failure).
        assert mixin._volume_backend_status_cache is not None, (
            " regression: _force_refresh=True did not cache the "
            "best-effort status when initialize() raised. The user "
            "explicitly asked for the current state, caching it "
            "prevents the next poll from re-invoking the failing init."
        )

        # Next default poll uses the cache (no re-init).
        mixin.get_volume_backend_status()
        assert ducker.initialize.call_count == 1

    def test_returned_dict_is_a_copy_not_the_cached_reference(self, status_mixin_with_mock_ducker):
        """The returned dict is a shallow copy of the cache, so callers"""
        mixin, ducker = status_mixin_with_mock_ducker
        first = mixin.get_volume_backend_status()
        # Mutate the returned dict (simulating the IPC handler
        first["is_windows"] = True
        first["name"] = "MUTATED"

        # The cache should NOT reflect the mutation.
        cached = mixin._volume_backend_status_cache
        assert "is_windows" not in cached, (
            " regression: mutation on the returned dict leaked into the cache. The method should return a copy."
        )
        assert cached["name"] == "fake (test)", " regression: name mutation on the returned dict leaked into the cache."

        # The next call returns a fresh copy without the mutation.
        second = mixin.get_volume_backend_status()
        assert "is_windows" not in second
        assert second["name"] == "fake (test)"

    def test_missing_volume_ducker_returns_disabled_sentinel(self, status_mixin_with_mock_ducker):
        """When ``_app._volume_ducker`` is absent (early startup,"""
        mixin, ducker = status_mixin_with_mock_ducker
        # Remove the _volume_ducker attribute entirely (simulating
        del mixin._app._volume_ducker

        result = mixin.get_volume_backend_status()
        assert result == {
            "available": False,
            "name": "disabled",
            "supports_per_session": False,
        }
        # Cache should NOT be populated (we returned early).
        assert mixin._volume_backend_status_cache is None
        assert ducker.initialize.call_count == 0

    def test_cache_is_per_instance_not_shared_across_instances(self, status_mixin_with_mock_ducker):
        """Each :class:`StatusMixin` instance has its own cache."""
        mixin_a, ducker_a = status_mixin_with_mock_ducker
        # Prime mixin_a's cache.
        mixin_a.get_volume_backend_status()
        assert mixin_a._volume_backend_status_cache is not None

        # Build a second instance with a different ducker.
        mixin_b = StatusMixin.__new__(StatusMixin)
        mixin_b._volume_backend_status_cache = None
        StatusMixin._volume_ducker_init_warned = False
        app_b = MagicMock()
        ducker_b = MagicMock()
        ducker_b.is_available = False
        ducker_b.backend_name = "different-backend"
        ducker_b.supports_per_session = True
        ducker_b.initialize = MagicMock(return_value=True)
        app_b._volume_ducker = ducker_b
        mixin_b._app = app_b

        assert mixin_b._volume_backend_status_cache is None
        assert mixin_a._volume_backend_status_cache is not None

        # Prime mixin_b's cache, should reflect mixin_b's ducker,
        result_b = mixin_b.get_volume_backend_status()
        assert result_b["name"] == "different-backend"
        assert result_b["available"] is False
        assert result_b["supports_per_session"] is True
        assert mixin_a._volume_backend_status_cache["name"] == "fake (test)"

    def test_force_refresh_default_is_false(self, status_mixin_with_mock_ducker):
        """The ``_force_refresh`` parameter defaults to ``False``,"""
        import inspect

        sig = inspect.signature(StatusMixin.get_volume_backend_status)
        param = sig.parameters["_force_refresh"]
        assert param.default is False, (
            " regression: _force_refresh default changed from "
            f"False to {param.default!r}. The default MUST be False "
            "so the 2s status poll takes the cache fast path."
        )

    def test_ttl_expiry_recomputes_status(self, status_mixin_with_mock_ducker, monkeypatch):
        """Past ``_VOLUME_BACKEND_STATUS_TTL_S`` the status is recomputed."""
        mixin, ducker = status_mixin_with_mock_ducker
        # Prime the cache.
        mixin.get_volume_backend_status()
        assert ducker.initialize.call_count == 1

        # Simulate a mid-session backend change.
        ducker.backend_name = "CoreAudio (pyobjc)"

        # Within the TTL: still cached.
        within = mixin.get_volume_backend_status()
        assert ducker.initialize.call_count == 1
        assert within["name"] == "fake (test)"

        # Age the cache past the TTL.
        monkeypatch.setattr(mixin, "_volume_backend_status_cached_at", 0.0)
        expired = mixin.get_volume_backend_status()
        assert ducker.initialize.call_count == 2, (
            " regression: the cache lived past its TTL, the status "
            "was never recomputed, freezing the Settings display for "
            "the process lifetime."
        )
        assert expired["name"] == "CoreAudio (pyobjc)"
        again = mixin.get_volume_backend_status()
        assert ducker.initialize.call_count == 2
        assert again["name"] == "CoreAudio (pyobjc)"
