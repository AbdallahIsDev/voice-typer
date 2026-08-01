"""RW-9 Phase 7 regression bundle: contract tests for the 5 controller extractions.

This is a HIGHER-LEVEL contract test file (vs. the 5 parallel module-specific
test files written by the 5 sub-agents). It verifies that all 5 new modules:

1. Exist and are importable from their expected locations.
2. Follow the SAME extraction pattern established by ``SettingsController``
   (RW-9 Phase 6) and ``RecordingController`` (RW-9 Phase 1) -- namely:
     - The class is named after its responsibility.
     - The constructor signature is ``(self, app: Any) -> None``.
     - The constructor stores ``self._app = app`` as a back-reference.
     - The expected method names are present as functions on the class.

The 5 extractions (specs in ``docs/rw9-god-class-decomposition.md`` §5.1-§5.5):

    - ``ShutdownController``       (§5.1)  ``voice_typer/server/shutdown_controller.py``
    - ``AudioQualityController``   (§5.2)  ``voice_typer/server/audio_quality_controller.py``
    - ``VolumeController``         (§5.3)  ``voice_typer/server/volume_controller.py``
    - ``TimerCoordinator``         (§5.4)  ``voice_typer/server/timer_coordinator.py``
    - ``WaveformBubbleWiring``     (§5.5)  ``voice_typer/server/waveform_bubble_wiring.py``

Robustness: every module import is wrapped in ``pytest.importorskip`` so the
file collects cleanly even when one or more parallel sub-agents have not yet
landed their module. Tests for missing modules SKIP; tests for present modules
run their contract assertions. This keeps CI green during the parallel
extraction rollout and turns red on a per-module basis as each sub-agent
finishes (then green again once the contracts are met).

Independence: per the RW-9 Phase 7 task spec, these contract tests do NOT
depend on ``VoiceTyperApp`` -- they exercise each new class directly with a
``MagicMock()`` standing in for the ``_app`` back-reference. This keeps the
test fast (no app boot), isolated (no cross-module side effects), and robust
to ``app.py`` refactors. The parallel sub-agents' own test files handle the
``VoiceTyperApp`` wiring side (e.g. verifying ``self.timer_coordinator`` is
constructed in ``__init__`` and that any state moved off ``VoiceTyperApp``
is gone from the app instance).
"""

from __future__ import annotations

import inspect
import threading
from unittest.mock import MagicMock

import pytest

# ── Helpers ────────────────────────────────────────────────────────────────


def _class_function_names(cls: type) -> set[str]:
    """Return the set of function names defined on ``cls``.

    Uses ``inspect.getmembers(cls, predicate=inspect.isfunction)`` so that
    plain instance methods, ``@staticmethod``-decorated functions, and
    inherited functions are all included. Callers should intersect this
    set with their expected method names.

    Note: ``inspect.isfunction`` is the correct predicate here (vs.
    ``inspect.ismethod``) because we are inspecting the *class* object,
    where methods appear as plain functions (not bound methods).
    ``inspect.ismethod`` would return an empty set for a class.
    """
    return {name for name, _ in inspect.getmembers(cls, predicate=inspect.isfunction)}


def _init_param_names(cls: type) -> list[str]:
    """Return the parameter names of ``cls.__init__`` excluding ``self``.

    Used to verify the ``(self, app)`` constructor signature pattern
    established by ``SettingsController`` / ``RecordingController``.

    We exclude ``self`` (always present on instance methods) and ignore
    any annotations / defaults -- the contract is purely structural:
    exactly one positional-or-keyword parameter named ``app``.
    """
    sig = inspect.signature(cls.__init__)
    return [name for name in sig.parameters if name != "self"]


def _assert_app_back_reference(cls: type, cls_label: str) -> None:
    """Construct ``cls`` with a sentinel MagicMock and verify ``_app``.

    The RW-9 extraction contract requires ``__init__`` to:
      1. Accept exactly one non-self parameter named ``app``.
      2. Store it as ``self._app = app`` (back-reference for state access).

    This helper enforces both invariants. It uses an ``identity``-check
    (``is``) rather than equality so that subclasses which accidentally
    wrap ``app`` in another object (e.g. ``self._app = Weakref(app)``)
    are caught.
    """
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
    """Verify every name in ``expected`` is a function on ``cls``.

    Uses ``inspect.getmembers(cls, predicate=inspect.isfunction)`` so the
    check is robust to inheritance and ``@staticmethod`` / ``@classmethod``
    decorations (instance methods appear as plain functions at the class
    level).
    """
    available = _class_function_names(cls)
    missing = [m for m in expected if m not in available]
    assert not missing, (
        f"{cls_label} is missing expected methods: {missing}. "
        f"Available function names on the class: {sorted(available)}"
    )


# ── §5.1 ShutdownController ────────────────────────────────────────────────


class TestShutdownControllerContract:
    """Pin the contract for ``ShutdownController`` (RW-9 §5.1, HIGHEST IMPACT).

    The shutdown lifecycle (~480 lines, 7 methods) was extracted from
    ``VoiceTyperApp``:
        - ``_do_cleanup`` (~265 lines) -- shared cleanup body used by
          ``quit()``, ``restart_app()``, and ``_atexit_cleanup()``.
        - ``quit`` (~60 lines) -- sets ``_shutting_down``, calls
          ``thread_registry.shutdown_all()``, calls ``_do_cleanup()``,
          then ``sys.exit(0)``.
        - ``_atexit_log`` / ``_atexit_cleanup`` -- atexit safety net.
        - ``_install_signal_handlers`` / ``_install_win32_console_handler``
          / ``_win32_console_handler`` -- POSIX signal + Windows console
          control handlers.

    These tests verify the extraction surface WITHOUT instantiating
    ``VoiceTyperApp`` -- a ``MagicMock`` stands in for the ``_app``
    back-reference. The parallel ``ShutdownController`` sub-agent's own
    test file handles the ``VoiceTyperApp`` wiring side (e.g. verifying
    ``app._do_cleanup`` delegates to ``self.shutdown._do_cleanup()``).
    """

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
        """``voice_typer.server.shutdown_controller`` must be importable.

        ``pytest.importorskip`` skips this test (and the rest of the
        methods in this class, which call it again) cleanly if the
        parallel sub-agent has not yet created the module.
        """
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


# ── §5.2 AudioQualityController ────────────────────────────────────────────


class TestAudioQualityControllerContract:
    """Pin the contract for ``AudioQualityController`` (RW-9 §5.2, LOW risk).

    Three cohesive methods extracted from ``VoiceTyperApp``:
        - ``_on_audio_quality_chunk`` -- per-chunk quality callback (runs in
          the PortAudio audio callback thread; MUST be non-blocking).
        - ``_rebuild_audio_processor`` -- rebuilds the audio filter chain on
          config change (called by ``service.apply_config_side_effects``).
        - ``_finalize_audio_quality_report`` -- runs final analysis after
          ``recorder.stop()`` and (optionally) surfaces warnings.

    Depends only on ``self._audio_quality`` / ``self._audio_processor`` /
    ``self.tray`` / ``self.recorder`` / ``self.config`` (all accessed via
    the ``_app`` back-reference).
    """

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


# ── §5.3 VolumeController ──────────────────────────────────────────────────


class TestVolumeControllerContract:
    """Pin the contract for ``VolumeController`` (RW-9 §5.3, LOW risk).

    Three cohesive methods extracted from ``VoiceTyperApp``:
        - ``_on_volume_crash_restore`` -- callback for stale duck
          crash-recovery file.
        - ``_duck_volume`` -- duck system volume at start of dictation.
        - ``_restore_volume`` -- restore system volume at end of dictation.

    Depends only on ``self._volume_ducker`` / ``self.config`` / ``self.tray``
    (all accessed via the ``_app`` back-reference).
    """

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


# ── §5.4 TimerCoordinator ──────────────────────────────────────────────────


class TestTimerCoordinatorContract:
    """Pin the contract for ``TimerCoordinator`` (RW-9 §5.4, LOW risk).

    Two cohesive methods + three state attributes extracted from
    ``VoiceTyperApp``:
        - ``_schedule_timer`` -- create/track/start a timer (with generation
          guard to prevent stale callbacks).
        - ``_cancel_pending_timers`` -- cancel and clear all pending timers
          (ARCH-022: list guarded by ``_pending_timers_lock``).
        - State: ``_pending_timers`` (list), ``_pending_timers_lock``
          (threading.Lock), ``_timer_generation`` (int counter).

    The state attributes (``_pending_timers`` / ``_pending_timers_lock`` /
    ``_timer_generation``) MUST live on the coordinator instance, NOT on
    ``VoiceTyperApp`` -- otherwise the extraction is incomplete and the
    race-safety invariants (ARCH-022) are broken. We verify they exist on
    the coordinator here; the parallel ``TimerCoordinator`` sub-agent's
    own test file verifies they have been removed from ``VoiceTyperApp``.
    """

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
        """``_pending_timers`` / ``_pending_timers_lock`` /
        ``_timer_generation`` must be instance attributes of the
        coordinator -- NOT of ``VoiceTyperApp``.

        We construct the coordinator with a ``MagicMock`` app and verify
        each attribute is present. Type sanity-checks (lock is a Lock,
        generation is an int, pending list is a list) catch subtle
        regressions where the attribute exists but is the wrong type
        (e.g. ``_pending_timers_lock`` set to ``None`` instead of a
        ``threading.Lock``).
        """
        mod = pytest.importorskip("voice_typer.server.timer_coordinator")
        cls = mod.TimerCoordinator
        instance = cls(MagicMock(name="app_for_timer_coordinator"))

        for attr in self.EXPECTED_STATE_ATTRS:
            assert hasattr(instance, attr), (
                f"TimerCoordinator instance must define `{attr}` -- "
                f"the timer state has not been migrated onto the coordinator"
            )

        # Type sanity-checks (best-effort: subclasses may use compatible
        # substitutes, so we accept anything that quacks like the intended
        # type).
        assert isinstance(instance._pending_timers_lock, type(threading.Lock())), (
            "_pending_timers_lock must be a threading.Lock (or compatible) to enforce ARCH-022 list-guard invariant"
        )
        assert isinstance(instance._timer_generation, int), "_timer_generation must be an int counter"
        assert hasattr(instance._pending_timers, "__iter__"), (
            "_pending_timers must be a list (or iterable) of pending Timer objects"
        )


# ── §5.5 WaveformBubbleWiring ──────────────────────────────────────────────


class TestWaveformBubbleWiringContract:
    """Pin the contract for ``WaveformBubbleWiring`` (RW-9 §5.5, MEDIUM risk).

    One method extracted from ``VoiceTyperApp``:
        - ``_wire_waveform_bubble`` -- forwards waveform bubble events to
          the IPC server. Wires 4 callbacks (``on_show``, ``on_hide``,
          ``on_level``, ``on_set_state``). Includes the bubble-level-pusher
          background worker (bounded queue + daemon thread + sentinel
          shutdown).

    The bubble level worker has threading concerns intertwined with
    ``_do_cleanup`` (which stops the worker on shutdown) -- the
    ``ShutdownController`` extraction (§5.1) must coordinate with this
    one. The parallel sub-agent's own test file covers the worker
    lifecycle integration.
    """

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


# ── Cross-cutting: pattern consistency vs. reference classes ───────────────


class TestExtractionPatternConsistency:
    """Verify all 5 new RW-9 Phase 7 classes follow the SAME constructor
    pattern as the two reference extractions (``SettingsController`` and
    ``RecordingController``): ``(self, app: Any) -> None`` with
    ``self._app = app``.

    Why a consistency test? The RW-9 extraction convention is a soft
    contract -- there's no abstract base class enforcing it. Without a
    test, a sub-agent could land ``TimerCoordinator(app, config)``
    (two params) or ``WaveformBubbleWiring(self, app, ipc_server)``
    (extra dependency) and break the uniform "app-as-back-reference"
    pattern. This test pins the convention across all 5 new classes
    using the 2 shipped reference classes as the source of truth.
    """

    # Already-shipped reference extractions ( Phase 1 + Phase 6).
    # These are NOT skipped via importorskip -- if either is missing,
    # the pattern itself has drifted and we want a hard failure.
    REFERENCE_CLASSES: tuple[tuple[str, str], ...] = (
        ("voice_typer.server.settings_controller", "SettingsController"),
        ("voice_typer.server.recording_controller", "RecordingController"),
    )

    # New  Phase 7 extractions. importorskip per module so the test
    # skips cleanly if any parallel sub-agent hasn't landed their module
    # yet (the per-class contract tests above already cover this case;
    # this test exists to enforce cross-class consistency once all 5
    # are present).
    NEW_CLASSES: tuple[tuple[str, str], ...] = (
        ("voice_typer.server.shutdown_controller", "ShutdownController"),
        ("voice_typer.server.audio_quality_controller", "AudioQualityController"),
        ("voice_typer.server.volume_controller", "VolumeController"),
        ("voice_typer.server.timer_coordinator", "TimerCoordinator"),
        ("voice_typer.server.waveform_bubble_wiring", "WaveformBubbleWiring"),
    )

    def test_reference_classes_establish_the_pattern(self) -> None:
        """``SettingsController`` and ``RecordingController`` must both
        use the ``(self, app)`` signature and store ``self._app = app``.

        This is a guard for the guard: if the reference pattern itself
        drifts (e.g. someone refactors SettingsController to take a
        ``config`` arg instead of ``app``), this test fails LOUDLY
        before the new-classes consistency check runs -- otherwise the
        new-classes check would silently "pass" against a broken
        reference.
        """
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
        """All 5 new RW-9 Phase 7 classes must use ``(self, app)`` and
        store ``self._app = app`` -- consistent with the reference
        pattern set by ``SettingsController`` and ``RecordingController``.

        ``pytest.importorskip`` is called per module so the test skips
        cleanly if any parallel sub-agent hasn't landed their module yet.
        Once all 5 are present, every class is checked against the same
        two invariants.
        """
        for mod_path, cls_name in self.NEW_CLASSES:
            mod = pytest.importorskip(mod_path)
            cls = getattr(mod, cls_name)
            _assert_app_back_reference(cls, cls_name)
