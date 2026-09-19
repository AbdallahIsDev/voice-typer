"""
RW-9 Phase 7 regression bundle: contract tests for the 5 controller extractions.
Independence: per the RW-9 Phase 7 task spec, these contract tests do NOT
"""

from __future__ import annotations

import inspect
import threading
from unittest.mock import MagicMock

import pytest


def _class_function_names(cls: type) -> set[str]:
    """Return the set of function names defined on ``cls``."""
    return {name for name, _ in inspect.getmembers(cls, predicate=inspect.isfunction)}


def _init_param_names(cls: type) -> list[str]:
    """Return the parameter names of ``cls.__init__`` excluding ``self``."""
    sig = inspect.signature(cls.__init__)
    return [name for name in sig.parameters if name != "self"]


def _assert_app_back_reference(cls: type, cls_label: str) -> None:
    """Construct ``cls`` with a sentinel MagicMock and verify ``_app``."""
    params = _init_param_names(cls)
    assert params == ["app"], (
        f"{cls_label}.__init__ must accept exactly one non-self parameter "
        f"named 'app' (consistent with SettingsController/RecordingController); "
        f"got params={params}"
    )

    sentinel_app = MagicMock(name=f"app_for_{cls_label}")
    instance = cls(sentinel_app)
    assert instance._app is sentinel_app, (
        f"{cls_label} must store `self._app = app` as a back-reference "
        f"(identity check) -- got {type(instance._app).__name__!r} instead "
        f"of the sentinel MagicMock"
    )


def _assert_methods_present(cls: type, expected: tuple[str, ...], cls_label: str) -> None:
    """Verify every name in ``expected`` is a function on ``cls``."""
    available = _class_function_names(cls)
    missing = [m for m in expected if m not in available]
    assert not missing, (
        f"{cls_label} is missing expected methods: {missing}. "
        f"Available function names on the class: {sorted(available)}"
    )


class TestShutdownControllerContract:
    """Pin the contract for ``ShutdownController`` (RW-9 §5.1, HIGHEST IMPACT)."""

    EXPECTED_METHODS: tuple[str, ...] = (
        "_do_cleanup",
        "quit",
        "_atexit_log",
        "_atexit_cleanup",
        "_install_signal_handlers",
        "_install_win32_console_handler",
        "_win32_console_handler",
    )

    def test_module_importable(self) -> None:
        """``voice_typer.server.shutdown_controller`` must be importable."""
        mod = pytest.importorskip("voice_typer.server.shutdown_controller")
        assert hasattr(mod, "ShutdownController"), "shutdown_controller module must export `ShutdownController`"

    def test_constructor_accepts_app_and_stores_back_reference(self) -> None:
        """Constructor signature is ``(self, app)``; stores ``self._app = app``."""
        mod = pytest.importorskip("voice_typer.server.shutdown_controller")
        _assert_app_back_reference(mod.ShutdownController, "ShutdownController")

    def test_expected_methods_present_as_functions(self) -> None:
        """All 7 expected methods must be defined as functions on the class."""
        mod = pytest.importorskip("voice_typer.server.shutdown_controller")
        _assert_methods_present(mod.ShutdownController, self.EXPECTED_METHODS, "ShutdownController")


class TestAudioQualityControllerContract:
    """Pin the contract for ``AudioQualityController`` (RW-9 §5.2, LOW risk)."""

    EXPECTED_METHODS: tuple[str, ...] = (
        "_on_audio_quality_chunk",
        "_rebuild_audio_processor",
        "_finalize_audio_quality_report",
    )

    def test_module_importable(self) -> None:
        mod = pytest.importorskip("voice_typer.server.audio_quality_controller")
        assert hasattr(mod, "AudioQualityController"), (
            "audio_quality_controller module must export `AudioQualityController`"
        )

    def test_constructor_accepts_app_and_stores_back_reference(self) -> None:
        mod = pytest.importorskip("voice_typer.server.audio_quality_controller")
        _assert_app_back_reference(mod.AudioQualityController, "AudioQualityController")

    def test_expected_methods_present_as_functions(self) -> None:
        mod = pytest.importorskip("voice_typer.server.audio_quality_controller")
        _assert_methods_present(mod.AudioQualityController, self.EXPECTED_METHODS, "AudioQualityController")


class TestVolumeControllerContract:
    """Pin the contract for ``VolumeController`` (RW-9 §5.3, LOW risk)."""

    EXPECTED_METHODS: tuple[str, ...] = (
        "_on_volume_crash_restore",
        "_duck_volume",
        "_restore_volume",
    )

    def test_module_importable(self) -> None:
        mod = pytest.importorskip("voice_typer.server.volume_controller")
        assert hasattr(mod, "VolumeController"), "volume_controller module must export `VolumeController`"

    def test_constructor_accepts_app_and_stores_back_reference(self) -> None:
        mod = pytest.importorskip("voice_typer.server.volume_controller")
        _assert_app_back_reference(mod.VolumeController, "VolumeController")

    def test_expected_methods_present_as_functions(self) -> None:
        mod = pytest.importorskip("voice_typer.server.volume_controller")
        _assert_methods_present(mod.VolumeController, self.EXPECTED_METHODS, "VolumeController")


class TestTimerCoordinatorContract:
    """Pin the contract for ``TimerCoordinator`` (RW-9 §5.4, LOW risk)."""

    EXPECTED_METHODS: tuple[str, ...] = (
        "_schedule_timer",
        "_cancel_pending_timers",
    )

    EXPECTED_STATE_ATTRS: tuple[str, ...] = (
        "_pending_timers",
        "_pending_timers_lock",
        "_timer_generation",
    )

    def test_module_importable(self) -> None:
        mod = pytest.importorskip("voice_typer.server.timer_coordinator")
        assert hasattr(mod, "TimerCoordinator"), "timer_coordinator module must export `TimerCoordinator`"

    def test_constructor_accepts_app_and_stores_back_reference(self) -> None:
        mod = pytest.importorskip("voice_typer.server.timer_coordinator")
        _assert_app_back_reference(mod.TimerCoordinator, "TimerCoordinator")

    def test_expected_methods_present_as_functions(self) -> None:
        mod = pytest.importorskip("voice_typer.server.timer_coordinator")
        _assert_methods_present(mod.TimerCoordinator, self.EXPECTED_METHODS, "TimerCoordinator")

    def test_state_attributes_live_on_coordinator(self) -> None:
        """``_pending_timers`` / ``_pending_timers_lock`` /"""
        mod = pytest.importorskip("voice_typer.server.timer_coordinator")
        cls = mod.TimerCoordinator
        instance = cls(MagicMock(name="app_for_timer_coordinator"))

        for attr in self.EXPECTED_STATE_ATTRS:
            assert hasattr(instance, attr), (
                f"TimerCoordinator instance must define `{attr}` -- "
                f"the timer state has not been migrated onto the coordinator"
            )

        # Type sanity-checks (best-effort: subclasses may use compatible
        assert isinstance(instance._pending_timers_lock, type(threading.Lock())), (
            "_pending_timers_lock must be a threading.Lock (or compatible) to enforce ARCH-022 list-guard invariant"
        )
        assert isinstance(instance._timer_generation, int), "_timer_generation must be an int counter"
        assert hasattr(instance._pending_timers, "__iter__"), (
            "_pending_timers must be a list (or iterable) of pending Timer objects"
        )


class TestWaveformBubbleWiringContract:
    """Pin the contract for ``WaveformBubbleWiring`` (RW-9 §5.5, MEDIUM risk)."""

    EXPECTED_METHODS: tuple[str, ...] = ("_wire_waveform_bubble",)

    def test_module_importable(self) -> None:
        mod = pytest.importorskip("voice_typer.server.waveform_bubble_wiring")
        assert hasattr(mod, "WaveformBubbleWiring"), "waveform_bubble_wiring module must export `WaveformBubbleWiring`"

    def test_constructor_accepts_app_and_stores_back_reference(self) -> None:
        mod = pytest.importorskip("voice_typer.server.waveform_bubble_wiring")
        _assert_app_back_reference(mod.WaveformBubbleWiring, "WaveformBubbleWiring")

    def test_expected_methods_present_as_functions(self) -> None:
        mod = pytest.importorskip("voice_typer.server.waveform_bubble_wiring")
        _assert_methods_present(mod.WaveformBubbleWiring, self.EXPECTED_METHODS, "WaveformBubbleWiring")


class TestExtractionPatternConsistency:
    """Verify all 5 new RW-9 Phase 7 classes follow the SAME constructor"""

    # Already-shipped reference extractions ( Phase 1 + Phase 6).
    REFERENCE_CLASSES: tuple[tuple[str, str], ...] = (
        ("voice_typer.server.settings_controller", "SettingsController"),
        ("voice_typer.server.recording_controller", "RecordingController"),
    )

    NEW_CLASSES: tuple[tuple[str, str], ...] = (
        ("voice_typer.server.shutdown_controller", "ShutdownController"),
        ("voice_typer.server.audio_quality_controller", "AudioQualityController"),
        ("voice_typer.server.volume_controller", "VolumeController"),
        ("voice_typer.server.timer_coordinator", "TimerCoordinator"),
        ("voice_typer.server.waveform_bubble_wiring", "WaveformBubbleWiring"),
    )

    def test_reference_classes_establish_the_pattern(self) -> None:
        """``SettingsController`` and ``RecordingController`` must both"""
        import importlib

        for mod_path, cls_name in self.REFERENCE_CLASSES:
            mod = importlib.import_module(mod_path)
            cls = getattr(mod, cls_name)
            params = _init_param_names(cls)
            assert params == ["app"], (
                f"Reference class {cls_name}.__init__ expected (self, app) "
                f"signature; got {params}. The RW-9 extraction pattern "
                f"itself has drifted -- update this test (and the new "
                f"extractions) to match."
            )
            sentinel = MagicMock(name=f"app_for_reference_{cls_name}")
            instance = cls(sentinel)
            assert instance._app is sentinel, (
                f"Reference class {cls_name} must store `self._app = app` "
                f"(back-reference). The RW-9 extraction pattern itself "
                f"has drifted."
            )

    def test_all_five_new_classes_follow_app_back_reference_pattern(self) -> None:
        """store ``self._app = app`` -- consistent with the reference"""
        for mod_path, cls_name in self.NEW_CLASSES:
            mod = pytest.importorskip(mod_path)
            cls = getattr(mod, cls_name)
            _assert_app_back_reference(cls, cls_name)
