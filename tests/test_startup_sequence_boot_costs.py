"""Startup-sequence per-boot cost reductions: behavioral contracts.

Three contracts pinned here (all hermetic: heavy imports mocked by the
autouse ``mock_heavy_imports`` fixture, no real audio/model/network):

1. ONE Silero VAD preload spawn per boot. The eager preload runs from
   ``StartupSequence`` phase 1 only; the recorder-init construction
   path no longer arms a second duplicate worker. Both spawn sites
   firing per boot was verified from code (the then-live app.py
   construction → ``_init_recording`` → ``_preload_vad_model`` site,
   since removed; phase 1 → ``_vad_preload_worker``) and from
   tests/conftest.py's ``_drain_thread_registries`` docstring, which
   documents both thread names.

2. Stale backup/``.tmp`` maintenance sweeps run AFTER ready. Phase 2
   (crash diagnostics) must NOT sweep synchronously; the sweep is
   dispatched on a fire-and-forget daemon thread after the
   ``Startup complete`` (ready) line emitted by phase 8. The sweep
   worker is spawned via the app's thread registry when reachable
   (``spawn_and_register``, mirroring the phase-1 vad-preload worker)
   so shutdown gets a clean bounded join, never an untracked thread;
   the fallback without a registry stays a bare daemon thread. The
   sweep bodies themselves are unchanged (see test_startup_sweep.py /
   test_startup_sweep_tmp_files.py for those).

3. The dictation hotkey registers BEFORE the mic-enumeration task.
   The mic task runs under a 5 s timeout budget; a hung audio stack
   must not keep the dictation hotkey dead for that window. Late mic
   results already arrive via ``microphones_changed`` +
   ``app.tray.set_microphones`` (startup_tasks.load_microphones), so
   nothing requires mics to finish first.
"""

from __future__ import annotations

import logging
import threading
from unittest.mock import MagicMock

import pytest

_AUTOSTART = "voice_typer.server.server_platform.autostart"
_MIC_LIST = "voice_typer.server.server_platform.microphone_list"


@pytest.fixture
def app_for_boot_costs(tmp_config_dir, monkeypatch):
    """A real VoiceTyperApp with hardware/GUI deps mocked (mirrors the
    ``app_for_phases`` fixture pattern in test_startup_sequence_phases.py)."""
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
    instance.config.bubble_behavior = "hidden"
    instance.config.bubble_show_on_startup = False
    return instance


def _stub_startup_io(app, monkeypatch):
    """Stub the heavy IO ``startup_tasks`` surface so a full ``run()``
    completes without real disk/audio work (mirrors the
    ``_stub_non_phase_startup`` helper in test_startup_sequence_phases.py)."""
    from voice_typer.server import startup_tasks

    monkeypatch.setattr(startup_tasks, "sync_autostart", lambda app: None)
    monkeypatch.setattr(startup_tasks, "sync_prewarm_task", lambda app, evt=None: None)
    monkeypatch.setattr(startup_tasks, "load_microphones", lambda app, evt=None: None)
    monkeypatch.setattr(startup_tasks, "ensure_desktop_shortcut", lambda app: None)
    monkeypatch.setattr(startup_tasks, "start_accessibility_pulse", lambda app, s: None)
    monkeypatch.setattr(startup_tasks, "check_offline_pack_on_launch", lambda app, evt=None: None)
    monkeypatch.setattr(
        "voice_typer.server.startup_sequence._phases_early.configure_corrections",
        lambda config_dir: None,
    )
    app.hotkeys = MagicMock()
    app.models = MagicMock()
    monkeypatch.delenv("VOICE_TYPER_RESTART", raising=False)


# ── (1) single VAD preload spawn per boot ──────────────────────────────


def _stub_platform_helpers(monkeypatch):
    """Stub the platform autostart/microphone helpers so a bare
    ``VoiceTyperApp()`` construction is hermetic (same targets as the
    ``app_for_boot_costs`` fixture, applied BEFORE construction)."""
    monkeypatch.setattr(f"{_AUTOSTART}.is_autostart_enabled", lambda: False, raising=False)
    monkeypatch.setattr(f"{_AUTOSTART}.enable_autostart", lambda: True, raising=False)
    monkeypatch.setattr(f"{_AUTOSTART}.disable_autostart", lambda: True, raising=False)
    monkeypatch.setattr(f"{_MIC_LIST}.list_microphones", lambda: [], raising=False)


class TestSingleVadPreloadSpawn:
    """``vad.preload()`` runs exactly once per boot, armed ONLY by
    StartupSequence phase 1 (``vad-preload-startup``). The former
    construction-time ``vad-preload`` worker in the recorder-init path
    was a duplicate of the same best-effort preload."""

    def test_vad_preload_spawns_exactly_one_worker_per_boot(self, tmp_config_dir, monkeypatch):
        """Construct a fresh app AND run the full startup sequence with
        ``vad.preload`` replaced by a counting stub: exactly ONE worker
        thread must invoke it per boot.

        Before the fix the count was 2 (one worker armed by the app
        construction path, one by phase 1), reproduced by this test
        failing with ``count=2``; this assertion pins the fixed
        contract. The stub is armed BEFORE the app is constructed so
        both spawn sites are counted deterministically.
        """
        from voice_typer.server import vad

        calls: list[str] = []
        monkeypatch.setattr(vad, "preload", lambda: calls.append("preload"))
        _stub_platform_helpers(monkeypatch)

        threads_before = set(threading.enumerate())

        from voice_typer.server.app import VoiceTyperApp

        app = VoiceTyperApp()
        app.config.esc_cancel_enabled = False
        app.config.voice_biometric_consent = True
        app.config.bubble_behavior = "hidden"
        app.config.bubble_show_on_startup = False
        app.hotkeys = MagicMock()
        app.models = MagicMock()

        _stub_startup_io(app, monkeypatch)

        from voice_typer.server.startup_sequence import StartupSequence

        StartupSequence(app).run()

        # Join every vad-preload worker spawned since the stub was armed
        # so the call count is final before asserting (the workers are
        # daemon threads; a direct join avoids timing flakiness).
        preload_threads = [t for t in threading.enumerate() if t not in threads_before and "vad-preload" in t.name]
        for t in preload_threads:
            t.join(timeout=5.0)

        assert len(calls) == 1, (
            "vad.preload() must be invoked by exactly ONE worker per boot "
            f"(observed {len(calls)} invocations from threads "
            f"{[t.name for t in preload_threads]}). The eager Silero VAD "
            "preload is owned by StartupSequence phase 1; the app "
            "construction path must not arm a duplicate worker."
        )

    def test_app_construction_alone_does_not_spawn_vad_preload(self, tmp_config_dir, monkeypatch):
        """A bare ``VoiceTyperApp()`` construction (startup sequence not
        run) must NOT arm any vad-preload worker, the preload belongs
        to the startup sequence, and construction-only consumers (tests,
        tooling) must not pay for it."""
        from voice_typer.server import vad
        from voice_typer.server.app import VoiceTyperApp

        calls: list[str] = []
        monkeypatch.setattr(vad, "preload", lambda: calls.append("preload"))
        _stub_platform_helpers(monkeypatch)

        threads_before = set(threading.enumerate())
        app = VoiceTyperApp()
        app.config.bubble_behavior = "hidden"

        preload_threads = [t for t in threading.enumerate() if t not in threads_before and "vad-preload" in t.name]
        for t in preload_threads:
            t.join(timeout=5.0)

        assert calls == [], (
            "VoiceTyperApp construction must not invoke vad.preload(), "
            f"observed {len(calls)} invocation(s) from "
            f"{[t.name for t in preload_threads]}."
        )


# ── (2) maintenance sweeps run after ready ─────────────────────────────


class TestPostReadyMaintenanceSweeps:
    """The stale backup/``.tmp`` sweeps must not sit on the pre-ready
    critical path: phase 2 returns without sweeping, and the sweep is
    dispatched on a fire-and-forget daemon thread after the
    ``Startup complete`` (ready) line."""

    def test_phase_2_does_not_sweep_synchronously(self, app_for_boot_costs, monkeypatch):
        """``_phase_2_crash_diagnostics`` must NOT call
        ``_sweep_stale_backup_files``, the sweep moved off the
        pre-ready critical path."""
        from voice_typer.server.startup_sequence import _maintenance

        sweep_calls: list[object] = []
        monkeypatch.setattr(
            _maintenance, "_sweep_stale_backup_files", lambda config_dir: sweep_calls.append(config_dir)
        )

        from voice_typer.server.startup_sequence import StartupSequence

        result = StartupSequence(app_for_boot_costs)._phase_2_crash_diagnostics()

        assert result.success is True
        assert sweep_calls == [], (
            "phase 2 must not run the stale-file sweep synchronously, the "
            "sweep belongs to a post-ready fire-and-forget daemon thread."
        )

    def test_sweep_dispatched_after_ready_line_on_daemon_thread(self, app_for_boot_costs, monkeypatch, caplog):
        """A full ``run()`` must dispatch the sweep exactly once, on a
        background (non-main) thread, AFTER the ``Startup complete``
        ready line has been emitted."""
        from voice_typer.server.startup_sequence import _maintenance

        sweep_events: list[dict] = []
        invoked = threading.Event()

        def _spy_sweep(config_dir):
            sweep_events.append(
                {
                    "is_main_thread": threading.current_thread() is threading.main_thread(),
                    "ready_line_logged": any("Startup complete" in r.getMessage() for r in caplog.records),
                }
            )
            invoked.set()

        monkeypatch.setattr(_maintenance, "_sweep_stale_backup_files", _spy_sweep)
        _stub_startup_io(app_for_boot_costs, monkeypatch)

        from voice_typer.server.startup_sequence import StartupSequence

        with caplog.at_level(logging.INFO, logger="voice_typer.server.startup_sequence"):
            StartupSequence(app_for_boot_costs).run()

        assert invoked.wait(timeout=5.0), (
            "the stale-file sweep must be dispatched (and run) after "
            "startup completes, it never fired within the timeout."
        )
        assert len(sweep_events) == 1, f"expected exactly one sweep, got {sweep_events}"
        event = sweep_events[0]
        assert event["is_main_thread"] is False, (
            "the sweep ran on the startup (main) thread, it must run on a "
            "fire-and-forget daemon thread, off the startup critical path."
        )
        assert event["ready_line_logged"] is True, (
            "the sweep ran BEFORE the 'Startup complete' ready line, it must be dispatched only after ready."
        )

    def test_sweep_thread_is_daemon(self, tmp_path, monkeypatch):
        """Without a thread registry (the fallback path, helper called
        with no registry), the post-ready sweep spawns a bare DAEMON
        thread (never blocks process exit) with a stable, greppable
        name."""
        from voice_typer.server.startup_sequence import _maintenance

        started = threading.Event()
        seen: dict[str, object] = {}

        def _worker(config_dir):
            seen["daemon"] = threading.current_thread().daemon
            seen["name"] = threading.current_thread().name
            started.set()

        # Reuse the helper's thread mechanics by patching the sweep body
        # it runs, the helper must call _sweep_stale_backup_files on the
        # spawned worker.
        monkeypatch.setattr(_maintenance, "_sweep_stale_backup_files", _worker)
        _maintenance._sweep_stale_files_after_ready(tmp_path)
        assert started.wait(timeout=5.0), "sweep worker never started"

        assert seen["daemon"] is True, "the post-ready sweep thread must be a daemon"
        assert isinstance(seen["name"], str) and "sweep" in str(seen["name"]), (
            f"sweep thread name should identify it (got {seen['name']!r})"
        )


class TestPostReadySweepThreadRegistry:
    """When the app's thread registry is reachable, the post-ready sweep
    worker is spawned via ``spawn_and_register`` (the phase-1
    vad-preload worker's pattern) so ``shutdown_all()`` joins it with a
    short bounded timeout, shutdown gets clean join semantics without
    the sweep ever gaining the power to block or break it."""

    def test_sweep_worker_registered_when_registry_passed(self, tmp_path, monkeypatch):
        """A registry passed to the dispatch is used: the worker runs
        under the canonical name, is a daemon, is tracked by the
        registry, and ``shutdown_all()`` joins it cleanly."""
        from voice_typer.server.startup_sequence import _maintenance
        from voice_typer.server.thread_registry import ThreadRegistry

        started = threading.Event()
        release = threading.Event()

        def _hold_worker(config_dir):
            started.set()
            release.wait(timeout=5.0)

        # Hold the worker mid-run so its thread attributes are
        # deterministically observable (a fast worker could exit before
        # the assertions below run).
        monkeypatch.setattr(_maintenance, "_sweep_stale_backup_files", _hold_worker)

        registry = ThreadRegistry()
        _maintenance._sweep_stale_files_after_ready(tmp_path, registry)
        assert started.wait(timeout=5.0), "sweep worker never started"

        assert registry.list_all() == ["startup-post-ready-sweep"], (
            "the sweep worker must be tracked by the registry under the "
            f"canonical 'startup-post-ready-sweep' name (got {registry.list_all()})"
        )
        assert _maintenance._SWEEP_JOIN_TIMEOUT_SECONDS <= 1.0, (
            "the sweep worker's registry join budget must stay SHORT "
            f"(got {_maintenance._SWEEP_JOIN_TIMEOUT_SECONDS}s), a sweep "
            "must never block or break shutdown (best-effort housekeeping)."
        )
        sweep_thread = next(
            (t for t in threading.enumerate() if t.name == "startup-post-ready-sweep"),
            None,
        )
        assert sweep_thread is not None, "sweep worker thread should still be alive"
        assert sweep_thread.daemon is True, (
            "the registry-tracked sweep worker must remain a daemon, "
            "tracking must not change fire-and-forget semantics."
        )

        release.set()
        sweep_thread.join(timeout=5.0)
        registry.shutdown_all()
        assert "startup-post-ready-sweep" not in registry.list_active(), (
            "shutdown_all() must leave no live sweep worker, the clean-join contract the registry exists for."
        )

    def test_full_run_registers_sweep_on_app_thread_registry(self, app_for_boot_costs, monkeypatch):
        """A full ``run()`` dispatches the sweep through the app's real
        thread registry: phase 8 passes it in, so the worker is
        registered on ``app._thread_registry`` alongside the phase-1
        vad-preload worker."""
        from voice_typer.server.startup_sequence import _maintenance

        invoked = threading.Event()
        monkeypatch.setattr(_maintenance, "_sweep_stale_backup_files", lambda config_dir: invoked.set())
        _stub_startup_io(app_for_boot_costs, monkeypatch)

        from voice_typer.server.startup_sequence import StartupSequence

        StartupSequence(app_for_boot_costs).run()

        assert invoked.wait(timeout=5.0), "the stale-file sweep never ran"
        registered = app_for_boot_costs._thread_registry.list_all()
        assert "startup-post-ready-sweep" in registered, (
            "the post-ready sweep worker must be registered on the app's "
            "thread registry (the phase-8 dispatch passes it in), got "
            f"{registered}."
        )


# ── (3) hotkey registration before the mic task ────────────────────────


class TestHotkeyRegistersBeforeMicTask:
    """The dictation hotkey must be live BEFORE the 5 s-budget
    mic-enumeration task can delay it; late mics already arrive via the
    ``microphones_changed`` push + tray rebuild inside
    ``startup_tasks.load_microphones``."""

    def test_phase_6_registers_hotkey_before_mic_task(self, app_for_boot_costs, monkeypatch):
        """In phase 6, ``app.hotkeys.register()`` must be called before
        the mic task is submitted to the bounded pool."""
        from voice_typer.server import startup_tasks

        order: list[str] = []

        monkeypatch.setattr(startup_tasks, "sync_autostart", lambda app: None)
        monkeypatch.setattr(startup_tasks, "load_microphones", lambda app, evt=None: order.append("mic"))
        monkeypatch.setattr(startup_tasks, "ensure_desktop_shortcut", lambda app: None)
        monkeypatch.setattr(startup_tasks, "check_offline_pack_on_launch", lambda app, evt=None: None)
        app_for_boot_costs.tray.set_autostart_enabled = MagicMock()
        app_for_boot_costs.hotkeys = MagicMock()
        app_for_boot_costs.hotkeys.register = MagicMock(side_effect=lambda: order.append("hotkey"))

        from voice_typer.server.startup_sequence import StartupSequence

        result = StartupSequence(app_for_boot_costs)._phase_6_autostart_prewarm_mics()

        assert result.success is True
        assert "hotkey" in order and "mic" in order, f"both steps must run (got {order})"
        assert order.index("hotkey") < order.index("mic"), (
            "phase 6 must register the dictation hotkey BEFORE the mic "
            f"enumeration task (got order {order}), a hung audio stack "
            "must not keep the hotkey dead through the 5 s mic budget."
        )
        app_for_boot_costs.hotkeys.register.assert_called_once()

    def test_shutdown_during_hotkey_registration_skips_mic_task(self, app_for_boot_costs, monkeypatch):
        """RACE-020 invariant moved with the hotkey: when
        ``app._shutting_down`` is set during ``hotkeys.register``, phase 6
        aborts (``success=False``) and the mic task must NOT run."""
        from voice_typer.server import startup_tasks

        mic_calls: list[int] = []
        monkeypatch.setattr(startup_tasks, "sync_autostart", lambda app: None)
        monkeypatch.setattr(startup_tasks, "load_microphones", lambda app, evt=None: mic_calls.append(1))
        monkeypatch.setattr(startup_tasks, "ensure_desktop_shortcut", lambda app: None)
        monkeypatch.setattr(startup_tasks, "check_offline_pack_on_launch", lambda app, evt=None: None)
        app_for_boot_costs.tray.set_autostart_enabled = MagicMock()
        app_for_boot_costs.hotkeys = MagicMock()

        def _hotkey_sets_shutdown():
            app_for_boot_costs._shutting_down = True

        app_for_boot_costs.hotkeys.register = _hotkey_sets_shutdown

        from voice_typer.server.startup_sequence import StartupSequence

        result = StartupSequence(app_for_boot_costs)._phase_6_autostart_prewarm_mics()

        assert result.success is False
        assert result.data == {"shutdown": True}
        assert mic_calls == [], "mic enumeration must NOT run when shutdown aborts phase 6 after hotkey registration."
