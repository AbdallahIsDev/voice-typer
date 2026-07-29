"""Tests for ``voice_typer.server.recording.device_manager.DeviceManager``.

Covers the DJ (Group 2 — Performance & Resources) Phase 4 fixes owned by
fix-agent F2:

- **DJ-68**: ``_invalidate_device_cache`` also fires the registered
  service-layer cache invalidator (``set_service_cache_invalidator``).
- **DJ-69**: ``_resolve_device`` parses the compound
  ``"<index>|<name>|<host_api>"`` form, prefers name-based resolution via
  ``find_microphone_by_name``, falls back to the saved index, and emits
  a one-time name-mismatch warning.
- **DJ-70**: ``_get_max_retries_for_device`` returns 6 for Bluetooth
  devices (``bluetooth``/``hfp``/``hands-free`` in the name OR 8/16 kHz
  sample rate) and 3 for everything else; ``_get_retry_sleep_for_device``
  returns ``_bt_retry_sleep_seconds`` for BT and 0.0 otherwise.

The tests use a headless mock for ``sounddevice`` so they don't touch
real audio hardware and run on any platform.
"""

from __future__ import annotations

import logging
import sys
from unittest.mock import MagicMock

import pytest


@pytest.fixture(autouse=True)
def _mock_sounddevice(monkeypatch):
    """Headless mock for ``sounddevice`` so tests don't touch real audio HW."""
    mock_sd = MagicMock()
    mock_sd.query_devices.return_value = []
    monkeypatch.setitem(sys.modules, "sounddevice", mock_sd)


def _make_device_manager(recorder=None, config=None):
    """Construct a ``DeviceManager`` with a mocked ``recorder`` back-reference.

    ``DeviceManager.__init__`` tries to start a real
    ``MicrophoneDeviceWatcher`` — we patch that out so no thread is
    spawned. The watcher startup is exercised elsewhere (in
    ``tests/test_microphone_watcher.py``); here we only test the
    DeviceManager-specific methods.
    """
    from voice_typer.server.recording.device_manager import DeviceManager

    if recorder is None:
        recorder = MagicMock()
        if config is None:
            config = MagicMock(sample_rate=16000, microphone=None)
        recorder.config = config
        # ``_resolve_device`` reads ``recorder.config.microphone`` —
        # ``MagicMock`` returns a new MagicMock for that attribute by
        # default, which breaks the ``mic is None`` check. Force None.
        recorder.config.microphone = None
    return DeviceManager(recorder)


# ── DJ-68: service-layer cache invalidation callback ─────────────────


class TestServiceCacheInvalidator:
    """``_invalidate_device_cache`` also calls the registered service callback."""

    def test_invalidator_callback_fires_on_cache_invalidation(self):
        """When ``_invalidate_device_cache`` runs, the registered service
        cache invalidator is invoked (so the UI's mic dropdown refreshes
        immediately after a hot-plug, not 5s later)."""
        dm = _make_device_manager()
        called = {"count": 0}

        def fake_invalidator():
            called["count"] += 1

        dm.set_service_cache_invalidator(fake_invalidator)

        # Populate cache with stale data.
        dm._device_list_cache = [{"id": "0", "name": "stale"}]
        dm._device_list_cache_time = 12345.6

        dm._invalidate_device_cache()

        assert dm._device_list_cache is None
        assert dm._device_list_cache_time == 0.0
        assert called["count"] == 1, "DJ-68: service invalidator must fire"

    def test_invalidator_not_called_when_not_registered(self):
        """When no callback is registered, ``_invalidate_device_cache`` is
        a silent no-op for the service layer (preserves pre-fix behavior)."""
        dm = _make_device_manager()
        # No set_service_cache_invalidator call.
        assert not hasattr(dm, "_service_cache_invalidator") or dm._service_cache_invalidator is None
        # Must not raise.
        dm._invalidate_device_cache()
        assert dm._device_list_cache is None

    def test_invalidator_callback_exception_is_swallowed(self, caplog):
        """If the service invalidator raises, the exception is logged and
        swallowed (the DeviceManager cache was still invalidated)."""
        dm = _make_device_manager()

        def raising_invalidator():
            raise RuntimeError("service layer exploded")

        dm.set_service_cache_invalidator(raising_invalidator)

        with caplog.at_level(
            logging.DEBUG,
            logger="voice_typer.server.recording",
        ):
            dm._invalidate_device_cache()

        # DeviceManager cache was still invalidated.
        assert dm._device_list_cache is None
        assert dm._device_list_cache_time == 0.0

    def test_invalidator_can_be_unregistered(self):
        """Passing ``None`` to ``set_service_cache_invalidator`` unregisters."""
        dm = _make_device_manager()
        called = {"count": 0}

        def cb():
            called["count"] += 1

        dm.set_service_cache_invalidator(cb)
        dm._invalidate_device_cache()
        assert called["count"] == 1

        # Unregister.
        dm.set_service_cache_invalidator(None)
        dm._invalidate_device_cache()
        assert called["count"] == 1, "unregistered callback must NOT fire"


# ── DJ-69: name-based device resolution ──────────────────────────────


class TestNameBasedDeviceResolution:
    """``_resolve_device`` parses ``"<index>|<name>|<host_api>"`` and prefers
    name-based resolution (so a saved index that now points at a different
    physical device is not silently substituted)."""

    def test_none_microphone_returns_none(self):
        """``config.microphone is None`` → system default → return None."""
        dm = _make_device_manager()
        dm.recorder.config.microphone = None
        assert dm._resolve_device() is None

    def test_bare_index_string_still_works(self):
        """Legacy ``config.microphone = "5"`` → return int 5 (backward compat)."""
        dm = _make_device_manager()
        dm.recorder.config.microphone = "5"
        assert dm._resolve_device() == 5

    def test_compound_form_prefers_name_resolution(self, monkeypatch):
        """``"5|USB Mic A|CoreAudio"`` → name lookup returns index 7 → return 7
        (the saved index 5 is NOT used because name resolution succeeded)."""
        dm = _make_device_manager()
        dm.recorder.config.microphone = "5|USB Mic A|CoreAudio"

        # Patch find_microphone_by_name to return a different index.
        fake_match = {"id": "7", "index": 7, "name": "USB Mic A", "host_api": "CoreAudio"}
        import voice_typer.server.server_platform as server_platform_mod

        monkeypatch.setattr(server_platform_mod, "find_microphone_by_name", lambda name: fake_match)

        result = dm._resolve_device()
        assert result == 7, "DJ-69: name resolution must take precedence over saved index"

    def test_compound_form_falls_back_to_saved_index_when_name_not_found(self, monkeypatch):
        """If ``find_microphone_by_name`` returns None, fall back to the saved index."""
        dm = _make_device_manager()
        dm.recorder.config.microphone = "5|Gone Mic|CoreAudio"

        import voice_typer.server.server_platform as server_platform_mod

        monkeypatch.setattr(server_platform_mod, "find_microphone_by_name", lambda name: None)

        result = dm._resolve_device()
        assert result == 5, "DJ-69: must fall back to saved index when name lookup fails"

    def test_compound_form_warns_on_name_mismatch(self, monkeypatch, caplog):
        """When name resolution fails AND the saved index now points at a
        device with a different name, a one-time WARNING is logged."""
        dm = _make_device_manager()
        dm.recorder.config.microphone = "5|USB Mic A|CoreAudio"

        import voice_typer.server.recording.device_manager as dm_mod
        import voice_typer.server.server_platform as server_platform_mod

        # Name lookup fails.
        monkeypatch.setattr(server_platform_mod, "find_microphone_by_name", lambda name: None)
        # Saved index 5 now points to "Webcam Mic B" (different name).
        monkeypatch.setattr(
            dm_mod.sd,
            "query_devices",
            lambda *a, **k: {"name": "Webcam Mic B", "index": 5, "max_input_channels": 1},
        )

        with caplog.at_level(
            logging.WARNING,
            logger="voice_typer.server.recording",
        ):
            result1 = dm._resolve_device()
            # Second call should NOT re-warn (one-time).
            result2 = dm._resolve_device()

        assert result1 == 5
        assert result2 == 5
        warning_messages = [r.message for r in caplog.records if r.levelno >= logging.WARNING]
        assert sum("DJ-69" in m and "now points to" in m for m in warning_messages) == 1, (
            f"Expected exactly one DJ-69 mismatch warning, got: {warning_messages}"
        )

    def test_compound_form_no_warn_when_saved_index_gone(self, monkeypatch, caplog):
        """When the saved index is no longer queryable, no mismatch warning
        is emitted (name resolution was the right call)."""
        dm = _make_device_manager()
        dm.recorder.config.microphone = "5|Gone Mic|CoreAudio"

        import voice_typer.server.recording.device_manager as dm_mod
        import voice_typer.server.server_platform as server_platform_mod

        monkeypatch.setattr(server_platform_mod, "find_microphone_by_name", lambda name: None)

        def raising_query(*a, **k):
            raise RuntimeError("device gone")

        monkeypatch.setattr(dm_mod.sd, "query_devices", raising_query)

        with caplog.at_level(
            logging.WARNING,
            logger="voice_typer.server.recording",
        ):
            result = dm._resolve_device()

        assert result == 5  # falls back to int(saved_index_str)
        warning_messages = [r.message for r in caplog.records if r.levelno >= logging.WARNING]
        assert not any("DJ-69" in m and "now points to" in m for m in warning_messages), (
            f"Expected NO DJ-69 warning when saved index is gone, got: {warning_messages}"
        )


# ── DJ-70: BT-aware retry policy ──────────────────────────────────────


class TestBTAwareRetryPolicy:
    """``_get_max_retries_for_device`` returns 6 for Bluetooth devices and 3
    otherwise; ``_get_retry_sleep_for_device`` returns
    ``_bt_retry_sleep_seconds`` for BT and 0.0 otherwise."""

    def test_baseline_retries_for_non_bt_device(self):
        """A non-Bluetooth device (48 kHz, name without BT keywords) → 3 retries."""
        dm = _make_device_manager()
        info = {"name": "USB Mic", "default_samplerate": 48000}
        assert dm._get_max_retries_for_device(info) == 3
        assert dm._get_retry_sleep_for_device(info) == 0.0

    def test_baseline_retries_for_none_device_info(self):
        """``None`` device_info → 3 retries (can't tell if OS default is BT)."""
        dm = _make_device_manager()
        assert dm._get_max_retries_for_device(None) == 3
        assert dm._get_retry_sleep_for_device(None) == 0.0

    def test_bt_retries_for_name_keyword(self):
        """A device named ``"Bluetooth Headset"`` → 6 retries + 0.75s sleep."""
        dm = _make_device_manager()
        info = {"name": "Bluetooth Headset", "default_samplerate": 48000}
        assert dm._get_max_retries_for_device(info) == 6
        assert dm._get_retry_sleep_for_device(info) == pytest.approx(0.75)

    def test_bt_retries_for_hfp_keyword(self):
        """A device named ``"Headset HFP"`` → 6 retries (HFP keyword)."""
        dm = _make_device_manager()
        info = {"name": "Headset HFP", "default_samplerate": 48000}
        assert dm._get_max_retries_for_device(info) == 6

    def test_bt_retries_for_hands_free_keyword(self):
        """A device named ``"Hands-Free Device"`` → 6 retries."""
        dm = _make_device_manager()
        info = {"name": "Hands-Free Device", "default_samplerate": 48000}
        assert dm._get_max_retries_for_device(info) == 6

    def test_bt_retries_for_8khz_sample_rate(self):
        """A device at 8 kHz (HFP/HSP signature) → 6 retries, even without
        a BT keyword in the name."""
        dm = _make_device_manager()
        info = {"name": "Generic Mic", "default_samplerate": 8000}
        assert dm._get_max_retries_for_device(info) == 6
        assert dm._get_retry_sleep_for_device(info) == pytest.approx(0.75)

    def test_bt_retries_for_16khz_sample_rate(self):
        """A device at 16 kHz (HFP/HSP signature) → 6 retries."""
        dm = _make_device_manager()
        info = {"name": "Generic Mic", "default_samplerate": 16000}
        assert dm._get_max_retries_for_device(info) == 6

    def test_bt_retry_sleep_is_configurable(self):
        """``_bt_retry_sleep_seconds`` can be tuned at runtime."""
        dm = _make_device_manager()
        dm._bt_retry_sleep_seconds = 1.0
        info = {"name": "Bluetooth", "default_samplerate": 48000}
        assert dm._get_retry_sleep_for_device(info) == pytest.approx(1.0)

    def test_build_device_info_for_retry_policy_returns_none_on_error(self):
        """``_build_device_info_for_retry_policy`` returns None when
        ``sd.query_devices`` raises (so the retry policy falls back to
        the baseline budget)."""
        dm = _make_device_manager()
        # Default mock returns []; force a raise.
        import voice_typer.server.recording.device_manager as dm_mod

        def raising(*a, **k):
            raise RuntimeError("portaudio boom")

        # ``_resolve_device`` reads ``recorder.config.microphone`` —
        # set it to a valid bare index so ``_resolve_device`` returns
        # an int and we reach the ``sd.query_devices(device)`` call.
        dm.recorder.config.microphone = "0"
        monkeypatch_target = dm_mod.sd
        original = monkeypatch_target.query_devices
        monkeypatch_target.query_devices = raising
        try:
            assert dm._build_device_info_for_retry_policy() is None
        finally:
            monkeypatch_target.query_devices = original


# ── DJ-99 (helper) + DJ-67 + DJ-64: disconnect_handler unit tests ─────


class TestRetuneAudioProcessorHelper:
    """DJ-99: the ``retune_audio_processor`` helper consolidates the
    inline retune block from ``Recorder.start()`` and
    ``DisconnectHandler.restart_stream()``."""

    def test_no_op_when_proc_sr_matches_effective_sr(self, caplog):
        """When ``proc._sample_rate == effective_sr``, the helper is a no-op."""
        from voice_typer.server.recording.disconnect_handler import retune_audio_processor

        proc = MagicMock()
        proc._sample_rate = 48000
        config = MagicMock()

        with caplog.at_level(
            logging.INFO,
            logger="voice_typer.server.recording",
        ):
            retune_audio_processor(proc, 48000, config, context="on start")

        proc.set_sample_rate.assert_not_called()
        proc.rebuild_from_config.assert_not_called()

    def test_set_sample_rate_called_when_available(self, caplog):
        """When ``set_sample_rate`` exists and ``_sample_rate`` differs, it is
        called with the new rate."""
        from voice_typer.server.recording.disconnect_handler import retune_audio_processor

        proc = MagicMock()
        proc._sample_rate = 16000
        proc.set_sample_rate = MagicMock()
        config = MagicMock()

        with caplog.at_level(
            logging.INFO,
            logger="voice_typer.server.recording",
        ):
            retune_audio_processor(proc, 48000, config, context="on start")

        proc.set_sample_rate.assert_called_once_with(48000)
        proc.rebuild_from_config.assert_not_called()
        info_messages = [r.message for r in caplog.records if r.levelno >= logging.INFO]
        assert any("set_sample_rate(48000)" in m and "on start" in m for m in info_messages), (
            f"Expected set_sample_rate info log, got: {info_messages}"
        )

    def test_rebuild_from_config_fallback_when_set_sr_unavailable(self, caplog):
        """When ``set_sample_rate`` is not callable, ``rebuild_from_config``
        is the fallback."""
        from voice_typer.server.recording.disconnect_handler import retune_audio_processor

        proc = MagicMock()
        proc._sample_rate = 16000
        proc.set_sample_rate = None  # not callable
        proc.rebuild_from_config = MagicMock()
        config = MagicMock()

        with caplog.at_level(
            logging.INFO,
            logger="voice_typer.server.recording",
        ):
            retune_audio_processor(proc, 48000, config, context="on hot-plug restart")

        proc.rebuild_from_config.assert_called_once_with(config)

    def test_set_sample_rate_failure_is_logged(self, caplog):
        """When ``set_sample_rate`` raises, a WARNING is logged (and the
        helper does not re-raise)."""
        from voice_typer.server.recording.disconnect_handler import retune_audio_processor

        proc = MagicMock()
        proc._sample_rate = 16000
        proc.set_sample_rate = MagicMock(side_effect=RuntimeError("boom"))
        config = MagicMock()

        with caplog.at_level(
            logging.WARNING,
            logger="voice_typer.server.recording",
        ):
            retune_audio_processor(proc, 48000, config, context="on start")

        warning_messages = [r.message for r in caplog.records if r.levelno >= logging.WARNING]
        assert any("set_sample_rate(48000) failed" in m and "on start" in m for m in warning_messages), (
            f"Expected set_sample_rate failure warning, got: {warning_messages}"
        )

    def test_no_op_when_proc_sr_is_none(self):
        """When ``proc._sample_rate`` is None, the helper is a no-op
        (mirrors the original inline guard)."""
        from voice_typer.server.recording.disconnect_handler import retune_audio_processor

        proc = MagicMock()
        proc._sample_rate = None
        config = MagicMock()

        retune_audio_processor(proc, 48000, config)
        proc.set_sample_rate.assert_not_called()
        proc.rebuild_from_config.assert_not_called()
