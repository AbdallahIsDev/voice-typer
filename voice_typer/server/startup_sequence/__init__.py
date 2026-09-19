"""Startup sequence orchestration for VoiceTyperApp.
Phase 5: extracted from ``VoiceTyperApp._do_startup`` (~340 lines)
re-exported and live at their owning modules. Per C-ARCH-2, tests patch seam names at their OWNING
``app._shutting_down`` so a ``quit()`` during startup short-circuits
(c) Onboarding auto-heal must run before any ``config.save()`` to avoid
    clobbering user settings, the wizard's ``apply_settings()`` overwrites
    defaults (``<caps_lock>``, ``tiny``, ``None``).
The class does NOT import ``app.py`` at module load (would create an
import cycle: ``app`` imports ``startup_sequence`` indirectly via the
``StartupSequence(self)`` call inside ``_do_startup``).  The runtime
import is local to ``_do_startup`` itself, so this module never
appears in ``app.py``'s import-time graph.
- :mod:`._maintenance`   -- stale backup / ``.tmp`` startup sweeps
- :mod:`._phases_early`  -- ``StageResult``, onboarding fail-counter
``StartupSequence`` is assembled here from the two phase mixins, so
``voice_typer.server.startup_sequence`` (including ``monkeypatch``
``__init__``: stdlib and cross-module names that the pre-split module
happened to bind (``os``, ``contextlib``, ``APP_NAME``, ...) are NOT
submodule (e.g. ``...startup_sequence._phases_early.configure_corrections``)
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    # Type-only import to avoid the import cycle described in the module
    from voice_typer.server.app import VoiceTyperApp

# Explicit re-exports (redundant aliases) so every pre-split attribute
from voice_typer.server.startup_sequence._maintenance import (
    _BACKUP_FILE_GLOBS as _BACKUP_FILE_GLOBS,
    _BACKUP_RETENTION_MAX_AGE_SECONDS as _BACKUP_RETENTION_MAX_AGE_SECONDS,
    _TMP_RETENTION_MAX_AGE_SECONDS as _TMP_RETENTION_MAX_AGE_SECONDS,
    _TMP_SWEEP_SUBDIRS as _TMP_SWEEP_SUBDIRS,
    _sweep_stale_backup_files as _sweep_stale_backup_files,
    _sweep_stale_tmp_files as _sweep_stale_tmp_files,
)
from voice_typer.server.startup_sequence._phases_early import (
    _ONBOARDING_FAIL_COUNTER_TTL_SECONDS as _ONBOARDING_FAIL_COUNTER_TTL_SECONDS,
    EarlyPhases as EarlyPhases,
    StageResult as StageResult,
    _config_dir as _config_dir,
    _onboarding_fail_counter_path as _onboarding_fail_counter_path,
    _read_onboarding_fail_count as _read_onboarding_fail_count,
    _reset_onboarding_fail_count as _reset_onboarding_fail_count,
    _write_onboarding_fail_count as _write_onboarding_fail_count,
    configure_corrections as configure_corrections,
)
from voice_typer.server.startup_sequence._phases_late import (
    _MODULE_STATE as _MODULE_STATE,
    LatePhases as LatePhases,
    _ModuleState as _ModuleState,
)

log = logging.getLogger(__name__)


def _anchor_startup_t0() -> float:
    """Monotonic anchor for the total-startup duration (C-LOG-2)."""
    from voice_typer.server import startup_timeline as _timeline

    spawned = _timeline.backend_spawn_monotonic()
    if spawned is not None:
        return spawned
    return time.perf_counter()


class StartupSequence(EarlyPhases, LatePhases):
    """Orchestrates the multi-phase background startup of VoiceTyperApp.
    The previous monolithic ``VoiceTyperApp._do_startup`` (~340 lines)
    corresponding phase method (C-LOG-1 / C-LOG-2 / RACE-020).
    """

    def __init__(self, app: VoiceTyperApp) -> None:
        self._app = app

    def run(self) -> None:
        """RACE-020: checks ``self._app._shutting_down`` between each major
        and RACE-020 shutdown check from the pre-refactor body is
        (C-LOG-1 / C-LOG-2).
        """
        # C-LOG-2: anchor the total startup duration, reported on the
        self._t0 = _anchor_startup_t0()
        for phase in (
            self._phase_1_init_and_vad_preload,
            self._phase_2_crash_diagnostics,
            self._phase_3_session_and_onboarding,
            self._phase_4_corrections_and_recovery,
            self._phase_5_platform_warnings,
            self._phase_6_autostart_prewarm_mics,
            self._phase_7_hotkey_and_model_load,
            self._phase_8_finalize_and_signal,
        ):
            result = phase()
            if not result.success:
                self._handle_phase_failure(result)
                return

    def _handle_phase_failure(self, result: StageResult) -> None:
        """Currently every ``success=False`` return is a RACE-020 shutdown"""
        # Intentionally a no-op for shutdown aborts (the phase logged).
        return
