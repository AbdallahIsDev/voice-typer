"""Phase-level tests for the refactor of ``StartupSequence``."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

_AUTOSTART = "voice_typer.server.server_platform.autostart"
_MIC_LIST = "voice_typer.server.server_platform.microphone_list"


@pytest.fixture
def app_for_phases(tmp_config_dir, monkeypatch):
    """Create a LausuApp suitable for exercising individual phases."""
    monkeypatch.setattr(f"{_AUTOSTART}.is_autostart_enabled", lambda: False, raising=False)
    monkeypatch.setattr(f"{_AUTOSTART}.enable_autostart", lambda: True, raising=False)
    monkeypatch.setattr(f"{_AUTOSTART}.disable_autostart", lambda: True, raising=False)
    monkeypatch.setattr(f"{_MIC_LIST}.list_microphones", lambda: [], raising=False)

    from voice_typer.server.app import LausuApp

    instance = LausuApp()
    instance.config.esc_cancel_enabled = False
    instance.config.voice_biometric_consent = True
    instance.models.transcriber = MagicMock()
    instance.models.transcriber.is_loaded = True
    # Default to hidden bubble so phase 8 doesn't try to show one.
    instance.config.bubble_behavior = "hidden"
    instance.config.bubble_show_on_startup = False
    return instance


def _stub_non_phase_startup(app_for_phases, monkeypatch):
    """Stub the heavy IO ``startup_tasks`` functions + corrections."""
    from voice_typer.server import startup_tasks

    monkeypatch.setattr(startup_tasks, "sync_autostart", lambda app: None)
    monkeypatch.setattr(startup_tasks, "sync_prewarm_task", lambda app, evt=None: None)
    monkeypatch.setattr(startup_tasks, "load_microphones", lambda app, evt=None: None)
    monkeypatch.setattr(startup_tasks, "ensure_desktop_shortcut", lambda app: None)
    monkeypatch.setattr(startup_tasks, "start_accessibility_pulse", lambda app, s: None)
    monkeypatch.setattr(
        "voice_typer.server.startup_sequence._phases_early.configure_corrections",
        lambda config_dir: None,
    )
    app_for_phases.hotkeys = MagicMock()
    app_for_phases.models = MagicMock()
    monkeypatch.delenv("VOICE_TYPER_RESTART", raising=False)


class TestStageResultDataclass:
    """Pin the :class:`StageResult` dataclass surface."""

    def test_stage_result_defaults(self):
        """``StageResult`` defaults: ``error=None``, ``data=None``."""
        from voice_typer.server.startup_sequence import StageResult

        result = StageResult(success=True)
        assert result.success is True
        assert result.error is None
        assert result.data is None

    def test_stage_result_failure_with_shutdown_data(self):
        """``data={\"shutdown\": True}`` so the orchestrator's failure"""
        from voice_typer.server.startup_sequence import StageResult

        result = StageResult(success=False, data={"shutdown": True})
        assert result.success is False
        assert result.data == {"shutdown": True}


class TestPhasesCallableIndependently:
    """Each phase is callable in isolation with mocked dependencies"""

    def test_phase_1_init_and_vad_preload_callable_in_isolation(self, app_for_phases, monkeypatch):
        """``_phase_1_init_and_vad_preload`` returns a successful"""
        from voice_typer.server import startup_sequence as ss_mod

        # VAD preload must not raise even when the vad module is
        fake_vad = MagicMock()
        fake_vad.preload = lambda: None
        monkeypatch.setitem(__import__("sys").modules, "voice_typer.server.vad", fake_vad)

        seq = ss_mod.StartupSequence(app_for_phases)
        result = seq._phase_1_init_and_vad_preload()

        assert result.success is True, "phase 1 must always succeed (VAD preload is best-effort)"

    def test_phase_2_crash_diagnostics_callable_in_isolation(self, app_for_phases, tmp_config_dir):
        """``_phase_2_crash_diagnostics`` returns a successful"""
        from voice_typer.server import startup_sequence as ss_mod

        # No crash files in tmp_config_dir, phase must complete cleanly.
        assert not (tmp_config_dir / "crash_diagnostics.1234.txt").exists()

        seq = ss_mod.StartupSequence(app_for_phases)
        result = seq._phase_2_crash_diagnostics()

        assert result.success is True, "phase 2 must always succeed (crash check is best-effort)"

    def test_phase_3_session_and_onboarding_callable_in_isolation(self, app_for_phases, monkeypatch, tmp_config_dir):
        """``_phase_3_session_and_onboarding`` records the session-active"""
        from voice_typer.server import session_state, startup_sequence as ss_mod

        monkeypatch.setattr(
            "voice_typer.server.startup_sequence._phases_early.configure_corrections",
            lambda config_dir: None,
        )

        seq = ss_mod.StartupSequence(app_for_phases)
        result = seq._phase_3_session_and_onboarding()

        assert result.success is True, "phase 3 must succeed on a normal path"
        marker = tmp_config_dir / "run" / session_state.SESSION_MARKER_FILENAME
        assert marker.exists(), "phase 3 must mark the session active on the normal path"

    def test_phase_4_corrections_and_recovery_callable_in_isolation(self, app_for_phases, monkeypatch):
        """``_phase_4_corrections_and_recovery`` returns a successful"""
        from voice_typer.server import startup_sequence as ss_mod

        monkeypatch.setattr(
            "voice_typer.server.startup_sequence._phases_early.configure_corrections",
            lambda config_dir: None,
        )
        # Disable crash-recovery so the inner branch is skipped.
        app_for_phases.config.crash_recovery_enabled = False
        # Stub the history_db.apply_retention / schedule_periodic_retention
        app_for_phases.history_db = MagicMock()

        seq = ss_mod.StartupSequence(app_for_phases)
        result = seq._phase_4_corrections_and_recovery()

        assert result.success is True, "phase 4 must always succeed (corrections + retention are best-effort)"

    def test_phase_5_platform_warnings_callable_in_isolation(self, app_for_phases, monkeypatch):
        """``_phase_5_platform_warnings`` returns a successful"""
        from voice_typer.server import startup_sequence as ss_mod

        seq = ss_mod.StartupSequence(app_for_phases)
        result = seq._phase_5_platform_warnings()

        assert result.success is True, "phase 5 must always succeed (platform warnings are advisory)"

    def test_phase_6_autostart_prewarm_mics_callable_in_isolation(self, app_for_phases, monkeypatch):
        """``_phase_6_autostart_prewarm_mics`` registers the dictation"""
        from voice_typer.server import startup_sequence as ss_mod, startup_tasks

        # Stub the heavy IO functions.
        monkeypatch.setattr(startup_tasks, "sync_autostart", lambda app: None)
        monkeypatch.setattr(startup_tasks, "sync_prewarm_task", lambda app, evt=None: None)
        monkeypatch.setattr(startup_tasks, "load_microphones", lambda app, evt=None: None)
        monkeypatch.setattr(startup_tasks, "ensure_desktop_shortcut", lambda app: None)
        # Tray must accept set_autostart_enabled; hotkeys must accept
        app_for_phases.tray.set_autostart_enabled = MagicMock()
        app_for_phases.hotkeys = MagicMock()

        seq = ss_mod.StartupSequence(app_for_phases)
        result = seq._phase_6_autostart_prewarm_mics()

        assert result.success is True, "phase 6 must succeed on a normal path"
        app_for_phases.tray.set_autostart_enabled.assert_called_once()
        app_for_phases.hotkeys.register.assert_called_once()

    def test_phase_7_hotkey_and_model_load_callable_in_isolation(self, app_for_phases):
        """``_phase_7_hotkey_and_model_load`` starts the background model"""
        from voice_typer.server import startup_sequence as ss_mod

        app_for_phases.hotkeys = MagicMock()
        app_for_phases.models = MagicMock()

        seq = ss_mod.StartupSequence(app_for_phases)
        result = seq._phase_7_hotkey_and_model_load()

        assert result.success is True
        app_for_phases.hotkeys.register.assert_not_called()
        app_for_phases.models.start_background_load.assert_called_once()

    def test_phase_8_finalize_and_signal_callable_in_isolation(self, app_for_phases, monkeypatch):
        """
        ``_phase_8_finalize_and_signal`` emits the canonical
        ``[STARTUP] Startup complete ...`` log line with the C-LOG-2
        """
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


class TestStartupT0Anchoring:
    """The \"Startup complete\" duration must cover the whole backend"""

    def test_anchor_uses_spawn_epoch_when_stamped(self, monkeypatch):
        """With the host's spawn marker present, the anchor is the"""
        import time

        from voice_typer.server import startup_sequence as ss_mod, startup_timeline as _timeline

        now_wall, now_mono = time.time(), time.monotonic()
        monkeypatch.setenv(_timeline.SPAWN_EPOCH_ENV, str(int(now_wall * 1000) - 4000))
        monkeypatch.setattr(time, "time", lambda: now_wall)
        monkeypatch.setattr(time, "monotonic", lambda: now_mono)

        assert ss_mod._anchor_startup_t0() == pytest.approx(now_mono - 4.0, abs=0.05)

    def test_anchor_falls_back_to_now_without_marker(self, monkeypatch):
        """Standalone runs (no marker) anchor at call time, the old"""
        import time

        from voice_typer.server import startup_sequence as ss_mod, startup_timeline as _timeline

        monkeypatch.delenv(_timeline.SPAWN_EPOCH_ENV, raising=False)
        before = time.perf_counter()
        assert before <= ss_mod._anchor_startup_t0() <= time.perf_counter()


class TestPhase8CompletionMessage:
    """The parenthetical on the \"Startup complete\" line must describe"""

    def _run_phase_8(self, app_for_phases, monkeypatch, caplog):
        from voice_typer.server import startup_sequence as ss_mod

        monkeypatch.delenv("VOICE_TYPER_RESTART", raising=False)
        app_for_phases.config.bubble_behavior = "hidden"
        app_for_phases.config.bubble_show_on_startup = False
        seq = ss_mod.StartupSequence(app_for_phases)
        seq._t0 = 0.0
        with caplog.at_level("INFO", logger="voice_typer.server.startup_sequence"):
            result = seq._phase_8_finalize_and_signal()
        assert result.success is True
        lines = [r.getMessage() for r in caplog.records if "Startup complete" in r.getMessage()]
        assert lines, "phase 8 must emit the Startup complete line"
        return lines[-1]

    def test_no_model_selected_message(self, app_for_phases, monkeypatch, caplog):
        """No configured model: the line must say so, never claim a"""
        from unittest.mock import MagicMock

        from voice_typer.server.model_registry import NO_MODEL_SIZE

        app_for_phases.config.model_size = NO_MODEL_SIZE
        app_for_phases.models = MagicMock()
        app_for_phases.models._model_load_thread = None

        line = self._run_phase_8(app_for_phases, monkeypatch, caplog)
        assert "no speech model selected" in line
        assert "still loading" not in line

    def test_still_loading_message_while_loader_alive(self, app_for_phases, monkeypatch, caplog):
        """Configured model + live loader thread: the classic line."""
        from unittest.mock import MagicMock

        app_for_phases.config.model_size = "tiny"
        app_for_phases.models = MagicMock()
        app_for_phases.models._model_load_thread.is_alive.return_value = True

        line = self._run_phase_8(app_for_phases, monkeypatch, caplog)
        assert "still loading" in line

    def test_settled_message_when_configured_and_idle(self, app_for_phases, monkeypatch, caplog):
        """Configured model + no live loader: plain line, no stale"""
        from unittest.mock import MagicMock

        app_for_phases.config.model_size = "tiny"
        app_for_phases.models = MagicMock()
        app_for_phases.models._model_load_thread = None

        line = self._run_phase_8(app_for_phases, monkeypatch, caplog)
        assert "still loading" not in line
        assert "no speech model selected" not in line


class TestPhase8TerminalTrayReconcile:
    """No-model boots must not leave the tray in boot"""

    def _phase_8(self, app_for_phases, monkeypatch):
        from voice_typer.server import startup_sequence as ss_mod

        monkeypatch.delenv("VOICE_TYPER_RESTART", raising=False)
        app_for_phases.config.bubble_behavior = "hidden"
        app_for_phases.config.bubble_show_on_startup = False
        seq = ss_mod.StartupSequence(app_for_phases)
        seq._t0 = 0.0
        return seq._phase_8_finalize_and_signal()

    def test_reconcile_heals_stuck_starting(self, app_for_phases, monkeypatch):
        """Tray still LOADING/\"Starting...\" + no model + no loader:"""
        from unittest.mock import MagicMock

        from voice_typer.server.model_registry import NO_MODEL_SIZE
        from voice_typer.server.tray_types import AppState

        app_for_phases.config.model_size = NO_MODEL_SIZE
        app_for_phases.models = MagicMock()
        app_for_phases.models._model_load_thread = None
        app_for_phases.tray._state = AppState.LOADING
        app_for_phases.tray._message = "Starting..."

        assert self._phase_8(app_for_phases, monkeypatch).success is True
        assert app_for_phases.tray._state == AppState.ERROR
        assert "No model selected" in app_for_phases.tray._message

    def test_reconcile_noop_when_refusal_landed(self, app_for_phases, monkeypatch):
        """Tray already ERROR/no-model: the reconcile must not clobber"""
        from unittest.mock import MagicMock

        from voice_typer.server import i18n
        from voice_typer.server.model_registry import NO_MODEL_SIZE
        from voice_typer.server.tray_types import AppState

        app_for_phases.config.model_size = NO_MODEL_SIZE
        app_for_phases.models = MagicMock()
        app_for_phases.models._model_load_thread = None
        reason = i18n.t("state.model_manager.no_model_selected")
        app_for_phases.tray._state = AppState.ERROR
        app_for_phases.tray._message = reason

        assert self._phase_8(app_for_phases, monkeypatch).success is True
        assert app_for_phases.tray._state == AppState.ERROR
        assert app_for_phases.tray._message == reason

    def test_reconcile_skipped_while_loading(self, app_for_phases, monkeypatch):
        """A live loader owns the tray state, phase 8 must not touch"""
        from unittest.mock import MagicMock

        from voice_typer.server.model_registry import NO_MODEL_SIZE
        from voice_typer.server.tray_types import AppState

        app_for_phases.config.model_size = NO_MODEL_SIZE
        app_for_phases.models = MagicMock()
        app_for_phases.models._model_load_thread.is_alive.return_value = True
        app_for_phases.tray._state = AppState.LOADING
        app_for_phases.tray._message = "Starting..."

        assert self._phase_8(app_for_phases, monkeypatch).success is True
        assert app_for_phases.tray._state == AppState.LOADING
        assert app_for_phases.tray._message == "Starting..."


class TestShutdownShortCircuits:
    """RACE-020 invariant: a ``success=False`` StageResult from any"""

    def test_phase_3_shutdown_aborts_before_session_marker(self, app_for_phases, monkeypatch, tmp_config_dir):
        """When ``app._shutting_down`` is set before phase 3 runs,"""
        from voice_typer.server import session_state, startup_sequence as ss_mod

        monkeypatch.setattr(
            "voice_typer.server.startup_sequence._phases_early.configure_corrections",
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
        """When ``app._shutting_down`` is set during ``sync_autostart``,"""
        from voice_typer.server import startup_sequence as ss_mod, startup_tasks

        def _sync_autostart_set_shutting_down(app):
            app._shutting_down = True

        monkeypatch.setattr(startup_tasks, "sync_autostart", _sync_autostart_set_shutting_down)
        # Spy on the subsequent calls, they MUST NOT fire when shutdown
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
        app_for_phases.hotkeys = MagicMock()

        seq = ss_mod.StartupSequence(app_for_phases)
        result = seq._phase_6_autostart_prewarm_mics()

        assert result.success is False
        assert result.data == {"shutdown": True}
        assert mic_calls == [], "mic enumeration must NOT run when shutdown aborts phase 6"
        assert prewarm_calls == [], "prewarm sync must NOT run when shutdown aborts phase 6"
        app_for_phases.hotkeys.register.assert_not_called()

    def test_phase_6_shutdown_after_parallel_work(self, app_for_phases, monkeypatch):
        """When ``app._shutting_down`` is set during the parallel work"""
        from voice_typer.server import startup_sequence as ss_mod, startup_tasks

        # Set _shutting_down during mic enumeration.
        def _mic_set_shutting_down(app, evt=None):
            app._shutting_down = True

        monkeypatch.setattr(startup_tasks, "sync_autostart", lambda app: None)
        monkeypatch.setattr(startup_tasks, "load_microphones", _mic_set_shutting_down)
        monkeypatch.setattr(startup_tasks, "sync_prewarm_task", lambda app, evt=None: None)
        monkeypatch.setattr(startup_tasks, "ensure_desktop_shortcut", lambda app: None)
        app_for_phases.tray.set_autostart_enabled = MagicMock()
        app_for_phases.hotkeys = MagicMock()

        seq = ss_mod.StartupSequence(app_for_phases)
        result = seq._phase_6_autostart_prewarm_mics()

        assert result.success is False
        assert result.data == {"shutdown": True}

    def test_phase_6_shutdown_after_hotkey_registration(self, app_for_phases, monkeypatch):
        """When ``app._shutting_down`` is set during"""
        from voice_typer.server import startup_sequence as ss_mod, startup_tasks

        def _hotkey_set_shutting_down():
            app_for_phases._shutting_down = True

        monkeypatch.setattr(startup_tasks, "sync_autostart", lambda app: None)
        monkeypatch.setattr(startup_tasks, "sync_prewarm_task", lambda app, evt=None: None)
        monkeypatch.setattr(startup_tasks, "ensure_desktop_shortcut", lambda app: None)
        mic_calls = []
        monkeypatch.setattr(
            startup_tasks,
            "load_microphones",
            lambda app, evt=None: mic_calls.append(1),
        )
        app_for_phases.tray.set_autostart_enabled = MagicMock()
        app_for_phases.hotkeys = MagicMock()
        app_for_phases.hotkeys.register = _hotkey_set_shutting_down

        seq = ss_mod.StartupSequence(app_for_phases)
        result = seq._phase_6_autostart_prewarm_mics()

        assert result.success is False
        assert result.data == {"shutdown": True}
        assert mic_calls == [], "mic enumeration must NOT run when shutdown aborts phase 6 after hotkey registration"

    def test_phase_7_shutdown_after_model_load_start(self, app_for_phases, monkeypatch):
        """When ``app._shutting_down`` is set during"""
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
        """
        End-to-end: when ``app._shutting_down`` is set BEFORE
        ``run`` is called, phase 3 aborts and phases 4-8 must NOT
        """
        _stub_non_phase_startup(app_for_phases, monkeypatch)

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

        # Pre-set _shutting_down, phase 3's first check aborts.
        app_for_phases._shutting_down = True

        ss_mod.StartupSequence(app_for_phases).run()

        assert phase_calls == [], (
            "RACE-020: when _shutting_down is set before run(), phase 3 must "
            "short-circuit the orchestrator, phases 4-8 must NOT execute. "
            f"Got: {phase_calls}"
        )


class TestPhaseOrdering:
    """Pin the 8-phase execution order. The orchestrator calls each"""

    def test_run_calls_all_8_phases_in_order(self, app_for_phases, monkeypatch):
        """``run`` calls the 8 phase methods in the documented order:"""
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
        """A ``success=False`` from any phase short-circuits the rest."""
        _stub_non_phase_startup(app_for_phases, monkeypatch)

        from voice_typer.server import startup_sequence as ss_mod

        # Force phase 4 to return success=False (simulating a shutdown
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
    """
    Contract: ``run`` is now a <50-LOC orchestrator (was 926
    LOC). Pins the LOC reduction so a future re-monolithization
    """

    def test_run_method_body_is_under_50_loc(self):
        """``run``'s body (def line to next def) must be <50 LOC."""
        import inspect

        from voice_typer.server import startup_sequence as ss_mod

        src = inspect.getsource(ss_mod.StartupSequence.run)
        # The body is everything after the signature + docstring.
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
        """``StartupSequence`` must have 9 methods after the refactor:"""
        import inspect

        from voice_typer.server import startup_sequence as ss_mod

        method_names = [
            name
            for name, _ in inspect.getmembers(ss_mod.StartupSequence, predicate=inspect.isfunction)
            if not name.startswith("__")
        ]
        # __init__ is a function too but starts with __, included separately.
        all_methods = [name for name, _ in inspect.getmembers(ss_mod.StartupSequence, predicate=inspect.isfunction)]
        assert "__init__" in all_methods
        assert "run" in method_names
        assert "_handle_phase_failure" in method_names
        for n in range(1, 9):
            assert any(m.startswith(f"_phase_{n}_") for m in method_names), (
                f"phase {n} method missing, got: {method_names}"
            )


class TestDesktopShortcutDispatch:
    """Desktop-shortcut creation is fire-and-forget."""

    def test_desktop_shortcut_dispatched_on_daemon_thread(self, app_for_phases, monkeypatch):
        import threading

        from voice_typer.server import startup_sequence as ss_mod, startup_tasks

        monkeypatch.setattr(startup_tasks, "sync_autostart", lambda app: None)
        monkeypatch.setattr(startup_tasks, "sync_prewarm_task", lambda app, evt=None: None)
        monkeypatch.setattr(startup_tasks, "load_microphones", lambda app, evt=None: None)
        app_for_phases.tray.set_autostart_enabled = MagicMock()

        calls = threading.Event()
        thread_names: list[str] = []

        def _record(app):
            thread_names.append(threading.current_thread().name)
            calls.set()

        monkeypatch.setattr(startup_tasks, "ensure_desktop_shortcut", _record)

        seq = ss_mod.StartupSequence(app_for_phases)
        result = seq._phase_6_autostart_prewarm_mics()

        assert result.success is True
        assert calls.wait(timeout=5.0), "ensure_desktop_shortcut was never invoked by the dispatched thread"
        assert thread_names == ["startup-desktop-shortcut"], (
            f"ensure_desktop_shortcut must run on the dedicated daemon thread, got: {thread_names}"
        )

    def test_desktop_shortcut_thread_is_daemon(self, app_for_phases, monkeypatch):
        """The dispatched thread must be a daemon so it never blocks"""
        import threading

        from voice_typer.server import startup_sequence as ss_mod, startup_tasks

        monkeypatch.setattr(startup_tasks, "sync_autostart", lambda app: None)
        monkeypatch.setattr(startup_tasks, "sync_prewarm_task", lambda app, evt=None: None)
        monkeypatch.setattr(startup_tasks, "load_microphones", lambda app, evt=None: None)
        monkeypatch.setattr(startup_tasks, "ensure_desktop_shortcut", lambda app: None)
        app_for_phases.tray.set_autostart_enabled = MagicMock()

        threads_before = set(threading.enumerate())
        seq = ss_mod.StartupSequence(app_for_phases)
        seq._phase_6_autostart_prewarm_mics()

        deadline = __import__("time").monotonic() + 5.0
        new_threads = []
        while __import__("time").monotonic() < deadline:
            new_threads = [
                t for t in set(threading.enumerate()) - threads_before if t.name == "startup-desktop-shortcut"
            ]
            if new_threads or not any(t.name == "startup-desktop-shortcut" for t in threading.enumerate()):
                break
            __import__("time").sleep(0.05)
        if new_threads:
            assert new_threads[0].daemon is True
