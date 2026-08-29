"""Startup sequence orchestration for VoiceTyperApp.

Phase 5: extracted from ``VoiceTyperApp._do_startup`` (~340 lines)
to reduce the god-class size. Each phase is gated by
``app._shutting_down`` so a ``quit()`` during startup short-circuits
cleanly.

Phase ordering is FIXED — see the dependency graph in worklog.md.
Reordering risks:

(a) Hotkey registration before model load means F2 works even if the
    model fails to load — without this, a model-load failure leaves the
    user with no way to interact with the app.
(b) Mic enumeration before hotkey registration means the tray menu has
    mics available when the hotkey is bound (the menu is built lazily
    on first show, but the mic list is captured at startup).
(c) Onboarding auto-heal must run before any ``config.save()`` to avoid
    clobbering user settings — the wizard's ``apply_settings()`` overwrites
    the user's hotkey, model, and microphone selections with onboarding
    defaults (``<caps_lock>``, ``tiny``, ``None``).

The class does NOT import ``app.py`` at module load (would create an
import cycle: ``app`` imports ``startup_sequence`` indirectly via the
``StartupSequence(self)`` call inside ``_do_startup``).  The runtime
import is local to ``_do_startup`` itself, so this module never
appears in ``app.py``'s import-time graph.

Package layout (behavior-preserving split of the former 1474-LOC
monolith — bodies moved verbatim, split by concern):

- :mod:`._maintenance`   -- stale backup / ``.tmp`` startup sweeps
- :mod:`._phases_early`  -- ``StageResult``, onboarding fail-counter
  helpers, phases 1-4 (banner/VAD preload, crash diagnostics,
  session+onboarding, corrections+recovery)
- :mod:`._phases_late`   -- Wayland-warning module state, phases 5-8
  (platform warnings, autostart/prewarm/mics, hotkey+model load,
  finalize)

``StartupSequence`` is assembled here from the two phase mixins, so
every re-exported pre-split attribute of
``voice_typer.server.startup_sequence`` (including ``monkeypatch``
seams re-exported below) keeps resolving through this package
``__init__`` — stdlib and cross-module names that the pre-split module
happened to bind (``os``, ``contextlib``, ``APP_NAME``, ...) are NOT
re-exported and live at their owning modules. Per C-ARCH-2, tests patch seam names at their OWNING
submodule (e.g. ``...startup_sequence._phases_early.configure_corrections``)
— the re-exports below exist for import-path parity, not as patch
targets.
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    # Type-only import to avoid the import cycle described in the module
    # docstring.  At runtime, ``app`` is whatever object was passed to
    # ``__init__`` (always a ``VoiceTyperApp`` in production, but tests
    # pass mocks that satisfy the same duck-typed surface).
    # NOTE: ``AppProtocol`` (providers) is deliberately NOT imported
    # here — the pack-check thread imports it at runtime (function
    # scope) because ``typing.cast`` evaluates its type argument at
    # runtime and a TYPE_CHECKING-only import caused a NameError in the
    # pack-check thread.
    from voice_typer.server.app import VoiceTyperApp

# Explicit re-exports (redundant aliases) so every pre-split attribute
# of this package keeps resolving at its historical import path.
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


class StartupSequence(EarlyPhases, LatePhases):
    """Orchestrates the multi-phase background startup of VoiceTyperApp.

    The previous monolithic ``VoiceTyperApp._do_startup`` (~340 lines)
    is now ``StartupSequence(app).run()``.  ``app`` is a back-reference
    so the sequence can read/write the app's state (config, tray, models,
    hotkeys, etc.) — same attribute surface as before, just renamed
    from ``self.X`` to ``self._app.X``.

    Phase decomposition: ``run`` is now a <40-line orchestrator
    that calls 8 phased sub-runs in order. Each phase returns a
    :class:`StageResult`; ``success=False`` short-circuits the rest (the
    phase has already emitted its own canonical shutdown log line).
    Every log line, exception swallow, and shutdown check from the
    pre-refactor ``run`` body is preserved verbatim in the
    corresponding phase method (C-LOG-1 / C-LOG-2 / RACE-020).
    """

    def __init__(self, app: VoiceTyperApp) -> None:
        self._app = app

    def run(self) -> None:
        """Top-level entry — equivalent to the old ``_do_startup`` body.

        RACE-020: checks ``self._app._shutting_down`` between each major
        step so that a ``quit()`` call during startup doesn't proceed
        with model downloads or background loads after the app has
        begun shutdown.

        Refactor: the previous 926-LOC monolithic body is now
        decomposed into 8 phased sub-runs (``_phase_1`` … ``_phase_8``).
        Each phase returns a :class:`StageResult`; ``success=False``
        (set when ``app._shutting_down`` is detected mid-phase)
        short-circuits the rest. Every log line, exception swallow,
        and RACE-020 shutdown check from the pre-refactor body is
        preserved verbatim in the corresponding phase method
        (C-LOG-1 / C-LOG-2).
        """
        # C-LOG-2: anchor the total startup duration, reported on the
        # "Startup complete" line emitted by ``_phase_8_finalize_and_signal``
        # (model load runs on a background thread and is measured
        # separately).
        self._t0 = time.perf_counter()
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
        """Hook for handling a phase that did not complete normally.

        Currently every ``success=False`` return is a RACE-020 shutdown
        abort: the phase already emitted the canonical
        "Interrupted after ..." / "_shutting_down is set, aborting
        startup" log line per the original monolithic ``run()`` body,
        so there is nothing more to do here but return. Kept as a
        dedicated method so future phases that fail for non-shutdown
        reasons have an obvious place to hook in additional handling
        (without rewriting the orchestrator).
        """
        # Intentionally a no-op for shutdown aborts (the phase logged).
        return
