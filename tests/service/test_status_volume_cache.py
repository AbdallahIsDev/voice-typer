"""regression tests for the volume-backend status cache in
``voice_typer/server/service/status.py``.

Previously :meth:`StatusMixin.get_volume_backend_status` called
``ducker.initialize()`` on every 2s status poll — wasted work because
``initialize()`` is idempotent (it short-circuits on
``self._initialized``) and the backend name / availability flags
don't change after the first successful init (the platform backend
selection is deterministic for the process lifetime).

The fix introduces a per-instance cache
(``_volume_backend_status_cache``) populated on the first call:
subsequent polls return the cached dict without re-invoking
``initialize()``. The cache is invalidated only on an explicit
``_force_refresh=True`` call (the UI's "Refresh" button).

These tests pin:

1. **Cache population** — the first call invokes ``initialize()``
   once and populates the cache.
2. **Cache hit** — subsequent calls do NOT invoke ``initialize()``
   and return the same dict.
3. **Force refresh** — ``_force_refresh=True`` re-invokes
   ``initialize()`` and refreshes the cache.
4. **Init-failure retry** — when ``initialize()`` raises on the
   first call, the cache is NOT populated so the next poll retries
   (preserving the "retry until init succeeds" behaviour for users
   who install a missing dependency mid-session).
5. **Mutation isolation** — the returned dict is a copy, so callers
   (e.g. the IPC handler that adds ``is_windows``) can't corrupt the
   cached state.
6. **Missing ducker** — when ``_volume_ducker`` is absent, the
   method returns the ``disabled`` sentinel (no cache populated).
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from voice_typer.server.service.status import StatusMixin


@pytest.fixture
def status_mixin_with_mock_ducker():
    """Build a minimal ``StatusMixin`` instance backed by a MagicMock
    ``_app._volume_ducker``.

    The fixture constructs a bare ``StatusMixin`` (no
    ``VoiceTyperService`` wrapper) and manually binds the attributes
    that ``VoiceTyperService.__init__`` would normally bind. This
    isolates the test from the rest of the service layer's
    construction cost.
    """
    mixin = StatusMixin.__new__(StatusMixin)
    # Reset the per-instance cache to its class-level default. The
    # class-level default is ``None`` (sentinel for "no cache yet").
    mixin._volume_backend_status_cache = None
    # Reset the class-level notify-once guard so each test starts
    # from a clean WARNING-not-yet-logged state.
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

    def test_first_call_invokes_initialize_once(
        self, status_mixin_with_mock_ducker
    ):
        """The first call to ``get_volume_backend_status`` invokes
        ``ducker.initialize()`` exactly once and populates the cache."""
        mixin, ducker = status_mixin_with_mock_ducker
        assert mixin._volume_backend_status_cache is None

        result = mixin.get_volume_backend_status()

        assert ducker.initialize.call_count == 1
        assert mixin._volume_backend_status_cache is not None
        assert result["name"] == "fake (test)"
        assert result["available"] is True
        assert result["supports_per_session"] is False
        assert result["backend"] == "MagicMock"

    def test_subsequent_calls_use_cache_without_reinvoking_initialize(
        self, status_mixin_with_mock_ducker
    ):
        """Subsequent calls return the cached dict and do NOT invoke
        ``ducker.initialize()`` again.

        This is the load-bearing  assertion: the status endpoint
        is polled every ~2s; without the cache, ``initialize()`` would
        be called ~1800 times per hour. With the cache, it's called
        exactly once per instance lifetime.
        """
        mixin, ducker = status_mixin_with_mock_ducker
        # Prime the cache with one call.
        mixin.get_volume_backend_status()
        assert ducker.initialize.call_count == 1

        # Mutate the mock's backend_name to prove subsequent calls
        # return the CACHED value (not the current mock state).
        ducker.backend_name = "changed-after-cache"

        # Second call: should NOT invoke initialize() and should
        # return the CACHED backend_name (not the mutated value).
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
        # The returned dicts should be equal in content but the
        # cache itself should be the same object (we return a copy
        # to callers, but the cached dict reference is stable).
        assert mixin._volume_backend_status_cache["name"] == "fake (test)"

        # A few more calls — initialize count stays at 1.
        for _ in range(5):
            mixin.get_volume_backend_status()
        assert ducker.initialize.call_count == 1

    def test_force_refresh_reinvokes_initialize_and_updates_cache(
        self, status_mixin_with_mock_ducker
    ):
        """``_force_refresh=True`` bypasses the cache, re-runs
        ``initialize()``, and refreshes the cached status.

        This is the UI's "Refresh Volume Backend" button contract:
        after the user installs a missing dependency (e.g.
        ``pyobjc-framework-CoreAudio`` mid-session), clicking Refresh
        re-detects the backend and updates the cached ``backend_name``.
        """
        mixin, ducker = status_mixin_with_mock_ducker
        # Prime the cache.
        first = mixin.get_volume_backend_status()
        assert first["name"] == "fake (test)"
        assert ducker.initialize.call_count == 1

        # Simulate a backend change (e.g. user installed the
        # CoreAudio dependency, switching from osascript to CoreAudio).
        ducker.backend_name = "CoreAudio (pyobjc)"

        # Force refresh — bypasses the cache, re-invokes initialize,
        # and updates the cache with the new backend_name.
        refreshed = mixin.get_volume_backend_status(_force_refresh=True)
        assert ducker.initialize.call_count == 2, (
            " regression: _force_refresh=True did not re-invoke "
            "ducker.initialize(). The cache should have been bypassed."
        )
        assert refreshed["name"] == "CoreAudio (pyobjc)"
        # The cache should now reflect the refreshed state.
        assert mixin._volume_backend_status_cache["name"] == "CoreAudio (pyobjc)"

        # A subsequent default call should NOT re-invoke initialize
        # (the refreshed cache is now in effect).
        again = mixin.get_volume_backend_status()
        assert ducker.initialize.call_count == 2
        assert again["name"] == "CoreAudio (pyobjc)"

    def test_init_failure_does_not_populate_cache(
        self, status_mixin_with_mock_ducker
    ):
        """When ``initialize()`` raises on the first call, the cache
        is NOT populated so the next poll retries.

        This preserves the previous "retry every poll until init
        succeeds" behaviour: a user who installs a missing dependency
        mid-session (without clicking the Refresh button) sees the
        backend detected on the next 2s poll, not stuck on the
        cached failed state.
        """
        mixin, ducker = status_mixin_with_mock_ducker
        ducker.initialize.side_effect = RuntimeError("init failed")

        # First call: initialize() raises, status dict is still
        # computed from the ducker's current (default) state.
        first = mixin.get_volume_backend_status()
        assert ducker.initialize.call_count == 1
        # Status is still returned (best-effort) — the existing
        # test_handles_initialize_exception dispatch test pins this.
        assert first["name"] == "fake (test)"
        # The cache should NOT be populated (init failed).
        assert mixin._volume_backend_status_cache is None, (
            " regression: cache was populated despite "
            "initialize() raising. The next poll should retry "
            "initialize() — caching the failed state would prevent "
            "auto-recovery when the user installs a missing "
            "dependency mid-session."
        )

        # Second call: should retry initialize (cache was not
        # populated). Reset the side_effect so init succeeds this time.
        ducker.initialize.side_effect = None
        mixin.get_volume_backend_status()
        assert ducker.initialize.call_count == 2, (
            " regression: the second call did not retry "
            "initialize() after the first call failed. The cache "
            "should have been empty so the retry could fire."
        )
        # Now the cache should be populated.
        assert mixin._volume_backend_status_cache is not None

    def test_force_refresh_caches_even_on_init_failure(
        self, status_mixin_with_mock_ducker
    ):
        """``_force_refresh=True`` caches the best-effort status even
        when ``initialize()`` raises.

        Rationale: when the user explicitly clicks "Refresh", they're
        asking for the current state — even if init fails, the
        best-effort status (backend_name from the ducker's current
        ``_backend`` attribute) is what they want to see. Caching it
        prevents the next 2s poll from re-invoking the failing init
        until the user clicks Refresh again.
        """
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
            "explicitly asked for the current state — caching it "
            "prevents the next poll from re-invoking the failing init."
        )

        # Next default poll uses the cache (no re-init).
        mixin.get_volume_backend_status()
        assert ducker.initialize.call_count == 1

    def test_returned_dict_is_a_copy_not_the_cached_reference(
        self, status_mixin_with_mock_ducker
    ):
        """The returned dict is a shallow copy of the cache, so callers
        can't mutate the cached state.

        The IPC handler (``_handle_get_volume_backend_status``) adds
        ``is_windows`` to the returned dict. Without a copy, that
        mutation would leak into the cache and show up on the next
        poll — corrupting the cached state with handler-specific
        fields.
        """
        mixin, ducker = status_mixin_with_mock_ducker
        first = mixin.get_volume_backend_status()
        # Mutate the returned dict (simulating the IPC handler
        # adding ``is_windows``).
        first["is_windows"] = True
        first["name"] = "MUTATED"

        # The cache should NOT reflect the mutation.
        cached = mixin._volume_backend_status_cache
        assert "is_windows" not in cached, (
            " regression: mutation on the returned dict leaked "
            "into the cache. The method should return a copy."
        )
        assert cached["name"] == "fake (test)", (
            " regression: name mutation on the returned dict "
            "leaked into the cache."
        )

        # The next call returns a fresh copy without the mutation.
        second = mixin.get_volume_backend_status()
        assert "is_windows" not in second
        assert second["name"] == "fake (test)"

    def test_missing_volume_ducker_returns_disabled_sentinel(
        self, status_mixin_with_mock_ducker
    ):
        """When ``_app._volume_ducker`` is absent (early startup,
        test fixtures), the method returns the ``disabled`` sentinel
        and does NOT populate the cache.
        """
        mixin, ducker = status_mixin_with_mock_ducker
        # Remove the _volume_ducker attribute entirely (simulating
        # early startup before app.__init__ completes).
        del mixin._app._volume_ducker

        result = mixin.get_volume_backend_status()
        assert result == {
            "available": False,
            "name": "disabled",
            "supports_per_session": False,
        }
        # Cache should NOT be populated (we returned early).
        assert mixin._volume_backend_status_cache is None
        # initialize() was never called.
        assert ducker.initialize.call_count == 0

    def test_cache_is_per_instance_not_shared_across_instances(
        self, status_mixin_with_mock_ducker
    ):
        """Each :class:`StatusMixin` instance has its own cache.

        Two separate ``VoiceTyperService`` instances (e.g. in a
        multi-window scenario) must NOT share the volume-backend
        cache — each service's ducker is independent.
        """
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

        # mixin_b's cache should be None initially (per-instance).
        assert mixin_b._volume_backend_status_cache is None
        # mixin_a's cache should be unaffected by mixin_b's existence.
        assert mixin_a._volume_backend_status_cache is not None

        # Prime mixin_b's cache — should reflect mixin_b's ducker,
        # not mixin_a's.
        result_b = mixin_b.get_volume_backend_status()
        assert result_b["name"] == "different-backend"
        assert result_b["available"] is False
        assert result_b["supports_per_session"] is True
        # mixin_a's cache is unchanged.
        assert mixin_a._volume_backend_status_cache["name"] == "fake (test)"

    def test_force_refresh_default_is_false(
        self, status_mixin_with_mock_ducker
    ):
        """The ``_force_refresh`` parameter defaults to ``False``,
        preserving the poll-path caching contract.

        The IPC ``get_volume_backend_status`` handler calls this
        method with no arguments — the default ``False`` applies,
        so the 2s status poll takes the cache fast path. This test
        pins the default so a future signature change (e.g. flipping
        the default to ``True``) doesn't silently re-introduce the
        per-poll ``initialize()`` call.
        """
        import inspect

        sig = inspect.signature(StatusMixin.get_volume_backend_status)
        param = sig.parameters["_force_refresh"]
        assert param.default is False, (
            " regression: _force_refresh default changed from "
            f"False to {param.default!r}. The default MUST be False "
            "so the 2s status poll takes the cache fast path."
        )
