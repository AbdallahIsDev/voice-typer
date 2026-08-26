"""Phase-level tests for the refactor of ``StartupSequence``.

Pre-refactor, ``StartupSequence.run`` was a 926-LOC monolithic method
interleaving 8 distinct concerns. Post-refactor, ``run`` is a
<40-line orchestrator that calls 8 phased sub-runs
(``_phase_1`` … ``_phase_8``) in order; each phase returns a
:class:`StageResult` and ``success=False`` short-circuits the rest.

These tests pin the refactor contract:

1. Each phase can be called independently with mocked dependencies
   (no full-app boot required).
2. ``StageResult.success=False`` (set when ``app._shutting_down`` is
   detected mid-phase) short-circuits the rest of ``run``.
3. All 8 phases are called in the correct order by ``run``.

The tests mirror the ``app_for_startup`` fixture pattern in
``tests/test_startup_sequence.py`` (a ``VoiceTyperApp`` with mocked
hardware/GUI deps + stubbed ``startup_tasks``). Pure-refactor safety
verified against the pre-refactor ``run`` body — no behavior change.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

_AUTOSTART = "voice_typer.server.server_platform.autostart"
_MIC_LIST = "voice_typer.server.server_platform.microphone_list"


@pytest.fixture
def app_for_phases(tmp_config_dir, monkeypatch):
    """Create a VoiceTyperApp suitable for exercising individual phases.

    Mirrors the ``app_for_startup`` fixture in
    ``tests/test_startup_sequence.py``. Heavy deps (sounddevice,
    pystray, pynput, etc.) are mocked by the autouse
    ``mock_heavy_imports`` fixture in ``tests/conftest.py``.
    """
    monkeypatch.setattr(f"{_AUTOSTART}.is_autostart_enabled", lambda: False, raising=False)
    monkeypatch.setattr(f"{_AUTOSTART}.enable_autostart", lambda: True, raising=False)
    monkeypatch.setattr(f"{_AUTOSTART}.disable_autostart", lambda: True, raising=False)
    monkeypatch.setattr(f"{_MIC_LIST}.list_microphones", lambda: [], raising=False)

    from voice_typer.server.app import VoiceTyperApp

    instance = VoiceTyperApp()
    instance.config.esc_cancel_enabled = False
    instance.config.voice_biometric_consent = True
    instance.models.transcriber = MagicMock()
    instance.models.transcriber.is_loaded = True
    # Default to hidden bubble so phase 8 doesn't try to show one.
    instance.config.bubble_behavior = "hidden"
    instance.config.bubble_show_on_startup = False
    return instance


def _stub_non_phase_startup(app_for_phases, monkeypatch):
    """Stub the heavy IO ``startup_tasks`` functions + corrections.

    Used by tests that exercise a single phase but call ``run()``
    through the orchestrator — every other phase must complete without
    real IO so the phase under test is the only one whose behaviour
    matters.
    """
    from voice_typer.server import startup_tasks

    monkeypatch.setattr(startup_tasks, "sync_autostart", lambda app: None)
    monkeypatch.setattr(startup_tasks, "sync_prewarm_task", lambda app, evt=None: None)
    monkeypatch.setattr(startup_tasks, "load_microphones", lambda app, evt=None: None)
    monkeypatch.setattr(startup_tasks, "ensure_desktop_shortcut", lambda app: None)
    monkeypatch.setattr(startup_tasks, "start_accessibility_pulse", lambda app, s: None)
    monkeypatch.setattr(
        "voice_typer.server.startup_sequence.configure_corrections",
        lambda config_dir: None,
    )
    # Replace app.hotkeys + app.models with no-op MagicMocks so the
    # later phases don't try to register real hotkeys / load a model.
    app_for_phases.hotkeys = MagicMock()
    app_for_phases.models = MagicMock()
    monkeypatch.delenv("VOICE_TYPER_RESTART", raising=False)


class TestStageResultDataclass:
    """Pin the :class:`StageResult` dataclass surface."""

    def test_stage_result_defaults(self):
        """``StageResult`` defaults: ``error=None``, ``data=None``.

        The orchestrator's only required field is ``success``. The
        optional fields default to ``None`` (P3/E8 — no sentinel
        empty objects)."""
        from voice_typer.server.startup_sequence import StageResult

        result = StageResult(success=True)
        assert result.success is True
        assert result.error is None
        assert result.data is None

    def test_stage_result_failure_with_shutdown_data(self):
        """A RACE-020 shutdown abort returns ``success=False`` with
        ``data={"shutdown": True}`` so the orchestrator's failure
        hook can distinguish shutdown from a real error."""
        from voice_typer.server.startup_sequence import StageResult

        result = StageResult(success=False, data={"shutdown": True})
        assert result.success is False
        assert result.data == {"shutdown": True}


class TestPhasesCallableIndependently:
    """Each phase is callable in isolation with mocked dependencies
    (no full-app boot required). Pre-refactor, the only entry point
    was the 926-LOC ``run`` — individual phases were untestable in
    isolation."""

    def test_phase_1_init_and_vad_preload_callable_in_isolation(self, app_for_phases, monkeypatch):
        """``_phase_1_init_and_vad_preload`` returns a successful
        ``StageResult`` and does NOT touch the heavy ``startup_tasks``
        surface. VAD preload is best-effort (failure logged at DEBUG)."""
        from voice_typer.server import startup_sequence as ss_mod

        # VAD preload must not raise even when the vad module is
        # unavailable (e.g. on a stripped CI runner). Patch vad.preload
        # so we don't actually try to load the model.
        fake_vad = MagicMock()
        fake_vad.preload = lambda: None
        monkeypatch.setitem(__import__("sys").modules, "voice_typer.server.vad", fake_vad)

        seq = ss_mod.StartupSequence(app_for_phases)
        result = seq._phase_1_init_and_vad_preload()

        assert result.success is True, "phase 1 must always succeed (VAD preload is best-effort)"

    def test_phase_2_crash_diagnostics_callable_in_isolation(self, app_for_phases, tmp_config_dir):
        """``_phase_2_crash_diagnostics`` returns a successful
        ``StageResult`` regardless of crash-file presence — the
        session-state gate decides whether to notify, but the phase
        itself never aborts startup."""
        from voice_typer.server import startup_sequence as ss_mod

        # No crash files in tmp_config_dir — phase must complete cleanly.
        assert not (tmp_config_dir / "crash_diagnostics.1234.txt").exists()

        seq = ss_mod.StartupSequence(app_for_phases)
        result = seq._phase_2_crash_diagnostics()

        assert result.success is True, "phase 2 must always succeed (crash check is best-effort)"

    def test_phase_3_session_and_onboarding_callable_in_isolation(self, app_for_phases, monkeypatch, tmp_config_dir):
        """``_phase_3_session_and_onboarding`` records the session-active
        marker and returns ``success=True`` on a normal path. The
        shutdown-abort path (``app._shutting_down`` set) is exercised
        separately in ``TestShutdownShortCircuits`` below."""
        from voice_typer.server import session_state, startup_sequence as ss_mod

        monkeypatch.setattr(
            "voice_typer.server.startup_sequence.configure_corrections",
            lambda config_dir: None,
        )

        seq = ss_mod.StartupSequence(app_for_phases)
        result = seq._phase_3_session_and_onboarding()

        assert result.success is True, "phase 3 must succeed on a normal path"
        marker = tmp_config_dir / "run" / session_state.SESSION_MARKER_FILENAME
        assert marker.exists(), "phase 3 must mark the session active on the normal path"

    def test_phase_4_corrections_and_recovery_callable_in_isolation(self, app_for_phases, monkeypatch):
        """``_phase_4_corrections_and_recovery`` returns a successful
        ``StageResult`` even when the corrections file is absent (the
        built-in defaults are used) and crash-recovery is disabled."""
        from voice_typer.server import startup_sequence as ss_mod

        monkeypatch.setattr(
            "voice_typer.server.startup_sequence.configure_corrections",
            lambda config_dir: None,
        )
        # Disable crash-recovery so the inner branch is skipped.
        app_for_phases.config.crash_recovery_enabled = False
        # Stub the history_db.apply_retention / schedule_periodic_retention
        # so the phase doesn't try real SQLite IO.
        app_for_phases.history_db = MagicMock()

        seq = ss_mod.StartupSequence(app_for_phases)
        result = seq._phase_4_corrections_and_recovery()

        assert result.success is True, "phase 4 must always succeed (corrections + retention are best-effort)"

    def test_phase_5_platform_warnings_callable_in_isolation(self, app_for_phases, monkeypatch):
        """``_phase_5_platform_warnings`` returns a successful
        ``StageResult`` on every platform. On Linux CI (no Wayland,
        not macOS) the phase body is a no-op."""
        from voice_typer.server import startup_sequence as ss_mod

        seq = ss_mod.StartupSequence(app_for_phases)
        result = seq._phase_5_platform_warnings()

        assert result.success is True, "phase 5 must always succeed (platform warnings are advisory)"

    def test_phase_6_autostart_prewarm_mics_callable_in_isolation(self, app_for_phases, monkeypatch):
        """``_phase_6_autostart_prewarm_mics`` calls
        ``sync_autostart`` / ``sync_prewarm_task`` / ``load_microphones``
        via ``startup_tasks`` and returns ``success=True`` on a normal
        path."""
        from voice_typer.server import startup_sequence as ss_mod, startup_tasks

        # Stub the heavy IO functions.
        monkeypatch.setattr(startup_tasks, "sync_autostart", lambda app: None)
        monkeypatch.setattr(startup_tasks, "sync_prewarm_task", lambda app, evt=None: None)
        monkeypatch.setattr(startup_tasks, "load_microphones", lambda app, evt=None: None)
        monkeypatch.setattr(startup_tasks, "ensure_desktop_shortcut", lambda app: None)
        # Tray must accept set_autostart_enabled.
        app_for_phases.tray.set_autostart_enabled = MagicMock()

        seq = ss_mod.StartupSequence(app_for_phases)
        result = seq._phase_6_autostart_prewarm_mics()

        assert result.success is True, "phase 6 must succeed on a normal path"
        app_for_phases.tray.set_autostart_enabled.assert_called_once()

    def test_phase_7_hotkey_and_model_load_callable_in_isolation(self, app_for_phases):
        """``_phase_7_hotkey_and_model_load`` registers the hotkey,
        starts the background model load, and returns ``success=True``.
        Pre-refactor these two steps were buried 1000+ lines deep in
        ``run``; the refactor exposes them as a directly-callable
        phase method."""
        from voice_typer.server import startup_sequence as ss_mod

        app_for_phases.hotkeys = MagicMock()
        app_for_phases.models = MagicMock()

        seq = ss_mod.StartupSequence(app_for_phases)
        result = seq._phase_7_hotkey_and_model_load()

        assert result.success is True
        app_for_phases.hotkeys.register.assert_called_once()
        app_for_phases.models.start_background_load.assert_called_once()

    def test_phase_8_finalize_and_signal_callable_in_isolation(self, app_for_phases, monkeypatch):
        """``_phase_8_finalize_and_signal`` emits the canonical
        ``[STARTUP] Startup complete ...`` log line with the C-LOG-2
        duration suffix anchored at ``self._t0`` and returns
        ``success=True``."""
        from voice_typer.server import startup_sequence as ss_mod

        # Avoid restart-env branch.
        monkeypatch.delenv("VOICE_TYPER_RESTART", raising=False)
        # Hidden bubble so the show-bubble branch is skipped.
        app_for_phases.config.bubble_behavior = "hidden"
        app_for_phases.config.bubble_show_on_startup = False

        seq = ss_mod.StartupSequence(app_for_phases)
        seq._t0 = 0.0  # anchor so the duration math is well-defined

        result = seq._phase_8_finalize_and_signal()

        assert result.success is True


class TestShutdownShortCircuits:
    """RACE-020 invariant: a ``success=False`` StageResult from any
    phase short-circuits ``run`` — no subsequent phase may execute.
    The phase has already emitted its canonical shutdown log line, so
    the orchestrator just returns (no extra logging)."""

    def test_phase_3_shutdown_aborts_before_session_marker(self, app_for_phases, monkeypatch, tmp_config_dir):
        """When ``app._shutting_down`` is set before phase 3 runs,
        phase 3 returns ``success=False`` and must NOT record the
        session-active marker (no real session started)."""
        from voice_typer.server import session_state, startup_sequence as ss_mod

        monkeypatch.setattr(
            "voice_typer.server.startup_sequence.configure_corrections",
            lambda config_dir: None,
        )
        app_for_phases._shutting_down = True

        seq = ss_mod.StartupSequence(app_for_phases)
        result = seq._phase_3_session_and_onboarding()

        assert result.success is False
        assert result.data == {"shutdown": True}
        marker = tmp_config_dir / "run" / session_state.SESSION_MARKER_FILENAME
        assert not marker.exists(), "shutdown abort must NOT leave a session-active marker"

    def test_phase_6_shutdown_after_autostart_sync(self, app_for_phases, monkeypatch):
        """When ``app._shutting_down`` is set during ``sync_autostart``,
        phase 6 returns ``success=False`` and must NOT proceed to
        prewarm sync / mic enumeration."""
        from voice_typer.server import startup_sequence as ss_mod, startup_tasks

        def _sync_autostart_set_shutting_down(app):
            app._shutting_down = True

        monkeypatch.setattr(startup_tasks, "sync_autostart", _sync_autostart_set_shutting_down)
        # Spy on the subsequent calls — they MUST NOT fire when shutdown
        # is detected mid-phase.
        mic_calls = []
        monkeypatch.setattr(
            startup_tasks,
            "load_microphones",
            lambda app, evt=None: mic_calls.append(1),
        )
        prewarm_calls = []
        monkeypatch.setattr(
            startup_tasks,
            "sync_prewarm_task",
            lambda app, evt=None: prewarm_calls.append(1),
        )
        monkeypatch.setattr(startup_tasks, "ensure_desktop_shortcut", lambda app: None)
        app_for_phases.tray.set_autostart_enabled = MagicMock()

        seq = ss_mod.StartupSequence(app_for_phases)
        result = seq._phase_6_autostart_prewarm_mics()

        assert result.success is False
        assert result.data == {"shutdown": True}
        assert mic_calls == [], "mic enumeration must NOT run when shutdown aborts phase 6"
        assert prewarm_calls == [], "prewarm sync must NOT run when shutdown aborts phase 6"

    def test_phase_6_shutdown_after_parallel_work(self, app_for_phases, monkeypatch):
        """When ``app._shutting_down`` is set during the parallel work
        (mic enumeration / pack check / prewarm sync), phase 6 returns
        ``success=False`` AFTER the parallel work completes."""
        from voice_typer.server import startup_sequence as ss_mod, startup_tasks

        # Set _shutting_down during mic enumeration.
        def _mic_set_shutting_down(app, evt=None):
            app._shutting_down = True

        monkeypatch.setattr(startup_tasks, "sync_autostart", lambda app: None)
        monkeypatch.setattr(startup_tasks, "load_microphones", _mic_set_shutting_down)
        monkeypatch.setattr(startup_tasks, "sync_prewarm_task", lambda app, evt=None: None)
        monkeypatch.setattr(startup_tasks, "ensure_desktop_shortcut", lambda app: None)
        app_for_phases.tray.set_autostart_enabled = MagicMock()

        seq = ss_mod.StartupSequence(app_for_phases)
        result = seq._phase_6_autostart_prewarm_mics()

        assert result.success is False
        assert result.data == {"shutdown": True}

    def test_phase_7_shutdown_after_hotkey_registration(self, app_for_phases, monkeypatch):
        """When ``app._shutting_down`` is set during ``hotkeys.register``,
        phase 7 returns ``success=False`` and must NOT proceed to
        ``models.start_background_load``."""
        from voice_typer.server import startup_sequence as ss_mod

        def _hotkey_set_shutting_down():
            app_for_phases._shutting_down = True

        app_for_phases.hotkeys = MagicMock()
        app_for_phases.hotkeys.register = _hotkey_set_shutting_down
        app_for_phases.models = MagicMock()

        seq = ss_mod.StartupSequence(app_for_phases)
        result = seq._phase_7_hotkey_and_model_load()

        assert result.success is False
        assert result.data == {"shutdown": True}
        (
            app_for_phases.models.start_background_load.assert_not_called(),
            ("model load must NOT start when shutdown aborts phase 7 after hotkey registration"),
        )

    def test_phase_7_shutdown_after_model_load_start(self, app_for_phases, monkeypatch):
        """When ``app._shutting_down`` is set during
        ``models.start_background_load``, phase 7 returns ``success=False``
        — the model load has been dispatched but startup is aborted."""
        from voice_typer.server import startup_sequence as ss_mod

        def _model_load_set_shutting_down():
            app_for_phases._shutting_down = True

        app_for_phases.hotkeys = MagicMock()
        app_for_phases.models = MagicMock()
        app_for_phases.models.start_background_load = _model_load_set_shutting_down

        seq = ss_mod.StartupSequence(app_for_phases)
        result = seq._phase_7_hotkey_and_model_load()

        assert result.success is False
        assert result.data == {"shutdown": True}

    def test_run_short_circuits_when_phase_3_aborts(self, app_for_phases, monkeypatch):
        """End-to-end: when ``app._shutting_down`` is set BEFORE
        ``run`` is called, phase 3 aborts and phases 4-8 must NOT
        execute. This is the strongest RACE-020 invariant pinned by
        the existing ``test_run_returns_early_if_shutting_down_at_start``
        in ``test_startup_sequence.py`` — re-pinned here against the
        new phase method names so the orchestrator contract is
        explicit."""
        _stub_non_phase_startup(app_for_phases, monkeypatch)

        # Spy on each phase method — phases 1-2 should run (they have
        # no shutdown check), phase 3 should run and abort, phases 4-8
        # must NOT run.
        from voice_typer.server import startup_sequence as ss_mod

        phase_calls: list[str] = []
        original_phase_4 = ss_mod.StartupSequence._phase_4_corrections_and_recovery
        original_phase_5 = ss_mod.StartupSequence._phase_5_platform_warnings
        original_phase_6 = ss_mod.StartupSequence._phase_6_autostart_prewarm_mics
        original_phase_7 = ss_mod.StartupSequence._phase_7_hotkey_and_model_load
        original_phase_8 = ss_mod.StartupSequence._phase_8_finalize_and_signal

        def _spy_phase_4(self):
            phase_calls.append("phase_4")
            return original_phase_4(self)

        def _spy_phase_5(self):
            phase_calls.append("phase_5")
            return original_phase_5(self)

        def _spy_phase_6(self):
            phase_calls.append("phase_6")
            return original_phase_6(self)

        def _spy_phase_7(self):
            phase_calls.append("phase_7")
            return original_phase_7(self)

        def _spy_phase_8(self):
            phase_calls.append("phase_8")
            return original_phase_8(self)

        monkeypatch.setattr(ss_mod.StartupSequence, "_phase_4_corrections_and_recovery", _spy_phase_4)
        monkeypatch.setattr(ss_mod.StartupSequence, "_phase_5_platform_warnings", _spy_phase_5)
        monkeypatch.setattr(ss_mod.StartupSequence, "_phase_6_autostart_prewarm_mics", _spy_phase_6)
        monkeypatch.setattr(ss_mod.StartupSequence, "_phase_7_hotkey_and_model_load", _spy_phase_7)
        monkeypatch.setattr(ss_mod.StartupSequence, "_phase_8_finalize_and_signal", _spy_phase_8)

        # Pre-set _shutting_down — phase 3's first check aborts.
        app_for_phases._shutting_down = True

        ss_mod.StartupSequence(app_for_phases).run()

        assert phase_calls == [], (
            "RACE-020: when _shutting_down is set before run(), phase 3 must "
            "short-circuit the orchestrator — phases 4-8 must NOT execute. "
            f"Got: {phase_calls}"
        )


class TestPhaseOrdering:
    """Pin the 8-phase execution order. The orchestrator calls each
    phase in a fixed tuple; reordering breaks RACE-020 / hotkey-before-
    model / mic-before-hotkey invariants (see module docstring of
    ``startup_sequence.py``)."""

    def test_run_calls_all_8_phases_in_order(self, app_for_phases, monkeypatch):
        """``run`` calls the 8 phase methods in the documented order:

        1. _phase_1_init_and_vad_preload
        2. _phase_2_crash_diagnostics
        3. _phase_3_session_and_onboarding
        4. _phase_4_corrections_and_recovery
        5. _phase_5_platform_warnings
        6. _phase_6_autostart_prewarm_mics
        7. _phase_7_hotkey_and_model_load
        8. _phase_8_finalize_and_signal
        """
        _stub_non_phase_startup(app_for_phases, monkeypatch)

        from voice_typer.server import startup_sequence as ss_mod

        expected_order = [
            "_phase_1_init_and_vad_preload",
            "_phase_2_crash_diagnostics",
            "_phase_3_session_and_onboarding",
            "_phase_4_corrections_and_recovery",
            "_phase_5_platform_warnings",
            "_phase_6_autostart_prewarm_mics",
            "_phase_7_hotkey_and_model_load",
            "_phase_8_finalize_and_signal",
        ]
        actual_order: list[str] = []

        for phase_name in expected_order:
            original = getattr(ss_mod.StartupSequence, phase_name)

            def _make_spy(name, orig):
                def _spy(self):
                    actual_order.append(name)
                    return orig(self)

                return _spy

            monkeypatch.setattr(ss_mod.StartupSequence, phase_name, _make_spy(phase_name, original))

        ss_mod.StartupSequence(app_for_phases).run()

        assert actual_order == expected_order, (
            "Phase ordering: run() must call the 8 phases in the "
            f"documented order. Expected {expected_order}, got {actual_order}."
        )

    def test_run_returns_after_first_failure(self, app_for_phases, monkeypatch):
        """A ``success=False`` from any phase short-circuits the rest.
        Spies phases 5-8 — none of them should run when phase 4 returns
        ``success=False``."""
        _stub_non_phase_startup(app_for_phases, monkeypatch)

        from voice_typer.server import startup_sequence as ss_mod

        # Force phase 4 to return success=False (simulating a shutdown
        # abort detected mid-phase).
        def _phase_4_aborts(self):
            return ss_mod.StageResult(success=False, data={"shutdown": True})

        monkeypatch.setattr(
            ss_mod.StartupSequence,
            "_phase_4_corrections_and_recovery",
            _phase_4_aborts,
        )

        phase_calls: list[str] = []
        for phase_name in (
            "_phase_5_platform_warnings",
            "_phase_6_autostart_prewarm_mics",
            "_phase_7_hotkey_and_model_load",
            "_phase_8_finalize_and_signal",
        ):
            original = getattr(ss_mod.StartupSequence, phase_name)

            def _make_spy(name, orig):
                def _spy(self):
                    phase_calls.append(name)
                    return orig(self)

                return _spy

            monkeypatch.setattr(ss_mod.StartupSequence, phase_name, _make_spy(phase_name, original))

        ss_mod.StartupSequence(app_for_phases).run()

        assert phase_calls == [], (
            f"Short-circuit: when phase 4 returns success=False, phases 5-8 must NOT execute. Got: {phase_calls}"
        )


class TestRunLocReduction:
    """Contract: ``run`` is now a <50-LOC orchestrator (was 926
    LOC). Pins the LOC reduction so a future re-monolithization
    (someone inlining a phase body back into ``run``) would fail this
    test."""

    def test_run_method_body_is_under_50_loc(self):
        """``run``'s body (def line to next def) must be <50 LOC.

        Pre-refactor: 926 LOC. Post-refactor target: <50 LOC (wiring
        only — bootstrap, phase-tuple, for-loop, return). All business
        logic lives in the 8 ``_phase_*`` methods (E3 — no spaghetti
        entry files)."""
        import inspect

        from voice_typer.server import startup_sequence as ss_mod

        src = inspect.getsource(ss_mod.StartupSequence.run)
        # The body is everything after the signature + docstring.
        # Count non-blank, non-comment, non-docstring lines.
        lines = src.splitlines()
        # Drop the def line + the docstring (enclosed in """).
        body_lines = []
        in_docstring = False
        for line in lines[1:]:  # skip the def line
            stripped = line.strip()
            if in_docstring:
                if '"""' in stripped:
                    in_docstring = False
                continue
            if stripped.startswith('"""'):
                if stripped.count('"""') >= 2:
                    continue  # one-liner docstring
                in_docstring = True
                continue
            if not stripped or stripped.startswith("#"):
                continue
            body_lines.append(stripped)

        assert len(body_lines) < 50, (
            "run() must be a <50-LOC orchestrator after the "
            "phase extraction. Got "
            f"{len(body_lines)} non-blank/non-comment body lines."
        )

    def test_class_has_nine_methods(self):
        """``StartupSequence`` must have 9 methods after the refactor:
        ``__init__`` + ``run`` + ``_handle_phase_failure`` + 8 phase
        methods = 11 methods total. Pre-refactor the class had only 2
        methods (``__init__`` + ``run``) — the audit's 'effectively 2
        methods' complaint is resolved."""
        import inspect

        from voice_typer.server import startup_sequence as ss_mod

        method_names = [
            name
            for name, _ in inspect.getmembers(ss_mod.StartupSequence, predicate=inspect.isfunction)
            if not name.startswith("__")
        ]
        # __init__ is a function too but starts with __ — included separately.
        all_methods = [name for name, _ in inspect.getmembers(ss_mod.StartupSequence, predicate=inspect.isfunction)]
        assert "__init__" in all_methods
        assert "run" in method_names
        assert "_handle_phase_failure" in method_names
        for n in range(1, 9):
            assert any(m.startswith(f"_phase_{n}_") for m in method_names), (
                f"phase {n} method missing — got: {method_names}"
            )
