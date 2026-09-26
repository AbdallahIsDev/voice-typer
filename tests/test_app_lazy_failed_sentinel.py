"""Sentinel + TTL regression tests for the lazy ``@property`` accessors"""

from __future__ import annotations

import logging
import time
from unittest.mock import MagicMock

import pytest


@pytest.fixture
def app(tmp_config_dir, monkeypatch):
    """Create a LausuApp with mocked dependencies for sentinel tests."""
    monkeypatch.setattr("voice_typer.server.server_platform.autostart.is_autostart_enabled", lambda: False)
    monkeypatch.setattr("voice_typer.server.server_platform.autostart.enable_autostart", lambda: True)
    monkeypatch.setattr("voice_typer.server.server_platform.autostart.disable_autostart", lambda: True)
    monkeypatch.setattr("voice_typer.server.server_platform.microphone_list.list_microphones", lambda: [])

    from voice_typer.server.app import LausuApp

    instance = LausuApp()
    instance.config.esc_cancel_enabled = False
    instance.config.voice_biometric_consent = True
    return instance


def _make_failing_constructor(module_path: str, class_name: str, monkeypatch):
    """call counter exposed on the patched function (``fn.call_count``)."""

    def _boom(self_inner, *args, **kwargs):
        _boom.call_count += 1
        raise RuntimeError(f"simulated {class_name} lazy-init failure (attempt #{_boom.call_count})")

    _boom.call_count = 0
    monkeypatch.setattr(f"{module_path}.{class_name}.__init__", _boom)
    return _boom


def _make_succeed_then_fail_constructor(module_path: str, class_name: str, monkeypatch):
    """Patch the constructor so the FIRST call succeeds (returns a mock"""

    fake_instance = MagicMock(name=f"fake_{class_name}")

    def _maybe(self_inner, *args, **kwargs):
        _maybe.call_count += 1
        if _maybe.call_count == 1:
            # First attempt succeeds, sets the instance.
            return None  # __init__ returns None; the instance is bound already
        raise RuntimeError(f"simulated {class_name} lazy-init failure on retry #{_maybe.call_count}")

    _maybe.call_count = 0
    _maybe.fake_instance = fake_instance
    monkeypatch.setattr(f"{module_path}.{class_name}.__init__", _maybe)
    return _maybe


class TestAudioQualitySentinelTtl:
    """``audio_quality`` is the hot-path property (~94 Hz per-chunk audio"""

    def test_returns_none_on_construction_failure(self, app, monkeypatch):
        """(a): On construction failure, the property returns ``None``"""
        _make_failing_constructor(
            "voice_typer.server.audio_quality_controller",
            "AudioQualityController",
            monkeypatch,
        )

        assert app.audio_quality is None
        # The backing is now the sentinel, NOT ``None``, internal state.
        from voice_typer.server.app import _LAZY_FAILED

        assert app._audio_quality_backing is _LAZY_FAILED
        assert app._audio_quality_failed_at is not None

    def test_no_construction_reattempt_within_ttl(self, app, monkeypatch):
        """(b): Within the TTL, repeated access does NOT re-attempt"""
        boom = _make_failing_constructor(
            "voice_typer.server.audio_quality_controller",
            "AudioQualityController",
            monkeypatch,
        )

        # First access, one construction attempt (fails).
        assert app.audio_quality is None
        assert boom.call_count == 1

        # Simulate the per-chunk hot path, 100 rapid accesses.
        for _ in range(100):
            assert app.audio_quality is None

        # Construction MUST NOT have been re-attempted, call_count is
        assert boom.call_count == 1, (
            f"Construction was re-attempted {boom.call_count - 1} times "
            f"within the TTL, the sentinel must suppress re-attempts to "
            f"avoid 94 Hz construction + log spam on the hot path."
        )

    def test_warning_fires_once_per_fresh_failure(self, app, monkeypatch, caplog):
        """(e): The WARNING log fires EXACTLY ONCE per fresh failure"""
        _make_failing_constructor(
            "voice_typer.server.audio_quality_controller",
            "AudioQualityController",
            monkeypatch,
        )

        with caplog.at_level(logging.WARNING, logger="voice_typer.server.app"):
            # First access. WARNING fires.
            assert app.audio_quality is None
            # 100 more accesses within TTL, no additional WARNINGs.
            for _ in range(100):
                assert app.audio_quality is None

        lazy_init_warnings = [
            rec for rec in caplog.records if "AudioQualityController lazy-init failed" in rec.getMessage()
        ]
        assert len(lazy_init_warnings) == 1, (
            f"Expected exactly 1 'AudioQualityController lazy-init failed' "
            f"WARNING per TTL window; got {len(lazy_init_warnings)}. The "
            f"sentinel must suppress re-logging within the TTL to avoid "
            f"94 Hz log spam."
        )

    def test_construction_retried_after_ttl_expires(self, app, monkeypatch):
        """(c): After ``RETRY_TTL_SECONDS`` elapses, the sentinel is"""
        boom = _make_failing_constructor(
            "voice_typer.server.audio_quality_controller",
            "AudioQualityController",
            monkeypatch,
        )

        # First access, fails, caches sentinel.
        assert app.audio_quality is None
        assert boom.call_count == 1

        # Move the failed-at timestamp into the distant past, simulate
        app._audio_quality_failed_at = time.monotonic() - 31.0  # 31s ago > RETRY_TTL_SECONDS (30s)

        # Second access, sentinel TTL expired, construction retried.
        assert app.audio_quality is None
        assert boom.call_count == 2, (
            "Construction must be re-attempted after the TTL expires, "
            "transient failures should get a chance to recover."
        )

        # one), the next access within TTL must NOT re-attempt.
        fresh_failed_at = app._audio_quality_failed_at
        assert fresh_failed_at is not None
        assert fresh_failed_at != (time.monotonic() - 31.0)  # the old timestamp was overwritten

        # Third access, fresh sentinel, within TTL, no re-attempt.
        assert app.audio_quality is None
        assert boom.call_count == 2, (
            "After a fresh failure, the sentinel must again suppress re-attempts within the new TTL window."
        )

    def test_sentinel_cleared_on_construction_success(self, app, monkeypatch):
        """(d): When construction succeeds on a retry (after a prior"""
        # First, simulate a failure to cache the sentinel.
        boom = _make_failing_constructor(
            "voice_typer.server.audio_quality_controller",
            "AudioQualityController",
            monkeypatch,
        )
        assert app.audio_quality is None
        assert boom.call_count == 1

        # Now expire the TTL and switch the constructor to succeed.
        app._audio_quality_failed_at = time.monotonic() - 31.0
        real_instance = MagicMock(name="real_AudioQualityController")

        def _ok(self_inner, *args, **kwargs):
            _ok.call_count += 1
            object.__setattr__(self_inner, "_test_marker", real_instance)

        _ok.call_count = 0
        monkeypatch.setattr(
            "voice_typer.server.audio_quality_controller.AudioQualityController.__init__",
            _ok,
        )

        # Retry, sentinel TTL expired, construction succeeds.
        result = app.audio_quality
        assert _ok.call_count == 1, "Construction must be re-attempted after TTL expiry."
        assert result is not None, "Successful construction must return the instance."

        # The sentinel + timestamp MUST be cleared.
        from voice_typer.server.app import _LAZY_FAILED

        assert app._audio_quality_backing is not _LAZY_FAILED, (
            "The sentinel must be cleared on construction success, "
            "subsequent accesses should see the cached real instance, "
            "not the failure sentinel."
        )
        assert app._audio_quality_backing is result
        assert app._audio_quality_failed_at is None, "The failure timestamp must be cleared on construction success."

        # Subsequent access, returns the cached instance, NO re-construction.
        result2 = app.audio_quality
        assert result2 is result
        assert _ok.call_count == 1, "Successful construction must be cached, no re-construction."

    def test_setter_bypasses_sentinel(self, app, monkeypatch):
        """Sanity: the setter bypasses the sentinel entirely (mirrors"""
        # Pre-load the sentinel.
        _make_failing_constructor(
            "voice_typer.server.audio_quality_controller",
            "AudioQualityController",
            monkeypatch,
        )
        assert app.audio_quality is None
        from voice_typer.server.app import _LAZY_FAILED

        assert app._audio_quality_backing is _LAZY_FAILED

        # Inject a mock via the setter, bypasses sentinel logic.
        fake = MagicMock(name="fake_AudioQualityController")
        app.audio_quality = fake

        # Getter must return the mock, NOT trigger construction retry.
        assert app.audio_quality is fake
        # The sentinel + timestamp are no longer in effect (the setter
        assert app._audio_quality_backing is fake


# The sentinel pattern is identical across all 5 properties, these


class TestSentinelWiredAcrossAllLazyProperties:
    """The ``_LAZY_FAILED`` sentinel + TTL pattern must be wired into"""

    @pytest.mark.parametrize(
        "prop_name, backing_attr, failed_at_attr, module_path, class_name",
        [
            (
                "undo",
                "_undo_backing",
                "_undo_failed_at",
                "voice_typer.server.app_undo",
                "UndoRepasteController",
            ),
            (
                "audio_quality",
                "_audio_quality_backing",
                "_audio_quality_failed_at",
                "voice_typer.server.audio_quality_controller",
                "AudioQualityController",
            ),
            (
                "_duck_crash_recovery",
                "_duck_crash_recovery_backing",
                "_duck_crash_recovery_failed_at",
                "voice_typer.server.duck_crash_recovery",
                "DuckCrashRecovery",
            ),
            (
                "_volume_ducker",
                "_volume_ducker_backing",
                "_volume_ducker_failed_at",
                "voice_typer.server.volume_ducker",
                "VolumeDucker",
            ),
            (
                "history_db",
                "_history_db_backing",
                "_history_db_failed_at",
                "voice_typer.server.history_db",
                "HistoryDB",
            ),
        ],
    )
    def test_sentinel_cached_on_failure(
        self,
        app,
        monkeypatch,
        prop_name,
        backing_attr,
        failed_at_attr,
        module_path,
        class_name,
    ):
        """For each of the 5 lazy properties: on construction failure,"""
        boom = _make_failing_constructor(module_path, class_name, monkeypatch)

        # First access, fails, caches sentinel.
        result = getattr(app, prop_name)
        assert result is None, f"{prop_name}: construction failure must return ``None`` to callers."
        assert boom.call_count == 1, f"{prop_name}: construction must be attempted exactly once on first access."

        from voice_typer.server.app import _LAZY_FAILED

        backing = getattr(app, backing_attr)
        assert backing is _LAZY_FAILED, (
            f"{prop_name}: backing must be the ``_LAZY_FAILED`` sentinel after "
            f"construction failure (got {backing!r}). Without the sentinel, "
            f"every subsequent access would re-attempt construction + re-log "
            f"the WARNING, the 94 Hz log spam bug."
        )
        failed_at = getattr(app, failed_at_attr)
        assert failed_at is not None, (
            f"{prop_name}: ``{failed_at_attr}`` must be a monotonic timestamp after construction failure (got None)."
        )

        # 50 more accesses within TTL, no re-attempt.
        for _ in range(50):
            assert getattr(app, prop_name) is None
        assert boom.call_count == 1, f"{prop_name}: construction must NOT be re-attempted within the TTL."

    def test_history_db_sentinel_respects_shutdown_guard(self, app, monkeypatch):
        """Edge case for ``history_db``: when ``_shutting_down_event`` is"""
        boom = _make_failing_constructor(
            "voice_typer.server.history_db",
            "HistoryDB",
            monkeypatch,
        )

        # First access, fails, caches sentinel.
        assert app.history_db is None
        assert boom.call_count == 1

        # Expire the TTL, would normally trigger a retry.
        app._history_db_failed_at = time.monotonic() - 31.0

        # But set shutdown, the getter must NOT retry, just return None.
        app._shutting_down_event.set()
        assert app.history_db is None
        assert boom.call_count == 1, (
            "history_db: when ``_shutting_down_event`` is set, the getter "
            "must NOT retry construction (even if the sentinel TTL has "
            "expired), mirrors the None-backing shutdown guard."
        )


class TestModuleLevelConstants:
    """The ``_LAZY_FAILED`` sentinel and ``RETRY_TTL_SECONDS`` TTL are"""

    def test_lazy_failed_is_object_singleton(self):
        """``_LAZY_FAILED`` is a module-level ``object()`` singleton —"""
        from voice_typer.server import app as _app_mod

        assert _app_mod._LAZY_FAILED is _app_mod._LAZY_FAILED, (
            "``_LAZY_FAILED`` must be a stable singleton (identity-stable across module attribute accesses)."
        )
        assert _app_mod._LAZY_FAILED is not None, (
            "``_LAZY_FAILED`` must NOT be ``None``: ``None`` is the initial state, distinct from the failure state."
        )

    def test_retry_ttl_seconds_is_30(self):
        """``RETRY_TTL_SECONDS`` is 30.0, a bounded TTL that balances"""
        from voice_typer.server import app as _app_mod

        assert _app_mod.RETRY_TTL_SECONDS == 30.0
        assert isinstance(_app_mod.RETRY_TTL_SECONDS, float)

    def test_lazy_failed_distinct_from_recorder_missing(self):
        """``_LAZY_FAILED`` and ``_RECORDER_MISSING`` are distinct"""
        from voice_typer.server import app as _app_mod

        assert _app_mod._LAZY_FAILED is not _app_mod._RECORDER_MISSING, (
            "``_LAZY_FAILED`` and ``_RECORDER_MISSING`` are distinct "
            "sentinels representing different states, they must not collide."
        )
