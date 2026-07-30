# ARCH-045 / SPLIT-4: extracted from the original ``prewarm.py`` god-module.
"""Prewarm pipeline orchestration.

Phase 4.5 / ARCH-045 — this module holds the top-level orchestration:

- :func:`run` — the prewarm pipeline entry point.  Runs the guards
  (config flag, sentinel, RAM), writes the PID file, calls
  :func:`_run_warming_pipeline`, and tears down the PID file + completion
  event in a ``finally`` block.
- :func:`_run_warming_pipeline` — the import + weights warming pipeline
  (extracted from ``run`` so the PID-file ``finally`` block can wrap the
  entire warming phase without duplicating the pipeline logic).

Patch-path compatibility
------------------------
Tests patch every guard helper (``_setup_logging``,
``_fast_startup_enabled``, ``_already_warmed``, ``_free_ram_mb``,
``_lower_io_priority``, ``_write_pid_file``, ``_remove_pid_file``) and
every warming helper (``_warm_imports``, ``_active_model_cache_dirs``,
``_warm_file``, ``_mark_warmed``) on the package namespace.  Both
``run`` and ``_run_warming_pipeline`` look those up via ``_pkg.X()`` at
call time so the patches take effect.
"""

from __future__ import annotations

import logging
import os
import time

# Patch-path bridge: route lookups of cross-submodule helpers through
# the package namespace so test patches of the form
# ``monkeypatch.setattr(prewarm, "_setup_logging", ...)`` /
# ``monkeypatch.setattr(prewarm, "_fast_startup_enabled", ...)`` /
# ``monkeypatch.setattr(prewarm, "_already_warmed", ...)`` /
# ``monkeypatch.setattr(prewarm, "_free_ram_mb", ...)`` /
# ``monkeypatch.setattr(prewarm, "_lower_io_priority", ...)`` /
# ``monkeypatch.setattr(prewarm, "_write_pid_file", ...)`` /
# ``monkeypatch.setattr(prewarm, "_remove_pid_file", ...)`` /
# ``monkeypatch.setattr(prewarm, "_create_completion_event", ...)`` /
# ``monkeypatch.setattr(prewarm, "_signal_completion_event", ...)`` /
# ``monkeypatch.setattr(prewarm, "_close_completion_event", ...)`` /
# ``monkeypatch.setattr(prewarm, "_run_warming_pipeline", ...)`` /
# ``monkeypatch.setattr(prewarm, "_warm_imports", ...)`` /
# ``monkeypatch.setattr(prewarm, "_active_model_cache_dirs", ...)`` /
# ``monkeypatch.setattr(prewarm, "_warm_file", ...)`` /
# ``monkeypatch.setattr(prewarm, "_mark_warmed", ...)``
# keep affecting production code defined here.
from voice_typer.server import prewarm as _pkg

# ER-15: import the battery-low-charge threshold constant directly from
# ``logging_setup`` (where ``_on_battery_and_low_charge`` is defined) so
# the log message in ``run()`` can reference the same threshold the
# guard uses. Direct import (rather than ``_pkg.X``) because the
# constant is a leaf value, not a test-patchable callable — tests patch
# ``prewarm._on_battery_and_low_charge`` (the callable), not the
# threshold constant.
from voice_typer.server.prewarm.logging_setup import (
    _BATTERY_LOW_CHARGE_THRESHOLD_PERCENT,
)

log = logging.getLogger("voice_typer.server.prewarm")

# Skip prewarming when free RAM is below this.  ~6 GB covers the torch
# package (4.2 GB) + Parakeet weights (2.4 GB) without catastrophically
# displacing the user's working set.  Tunable via --min-ram-mb.
DEFAULT_MIN_FREE_RAM_MB = 6 * 1024

# ─── Exit codes (distinct for diagnostics in Task Scheduler history) ─────

EXIT_OK = 0
EXIT_DISABLED = 10  # user turned fast_startup off
EXIT_LOW_RAM = 20  # not enough free RAM to prewarm safely
EXIT_NO_MODEL = 30  # model not cached yet (first-ever run)
EXIT_IMPORT_FAILED = 40  # torch/transformers missing or broken
# ER-15: skip prewarm when the host is on battery AND charge < 60%.
# Prewarming reads ~6 GB off disk (~2-3 Wh drain); on a laptop booted
# on battery at low charge, that drain is perceptible. Defer to the
# next AC-plug event. See ``_on_battery_and_low_charge`` in
# ``logging_setup.py`` and the regression tests in
# ``tests/test_prewarm_er_fix_e2.py::TestOnBatteryAndLowCharge``.
EXIT_ON_BATTERY = 50

# ER-68: skip warming a weights file when its cache ratio is already
# >= this threshold (i.e. the OS page cache already holds ~all of it).
# Avoids re-reading the entire 2.4 GB model.safetensors when a prior
# prewarm (or the app's own model load) already warmed it.
_CACHE_RATIO_SKIP_WARMING_THRESHOLD = 0.9
# ER-68: only probe cache ratio for files above this size — small files
# are cheap to re-warm (a single read() call) and the probe itself
# costs a few random reads, so the probe is pure overhead for them.
_CACHE_RATIO_PROBE_MIN_BYTES = 100 * 1024 * 1024  # 100 MB


# ─── Orchestration ───────────────────────────────────────────────────────


def run(
    min_ram_mb: int = DEFAULT_MIN_FREE_RAM_MB,
    force: bool = False,
    delay: float = 0.0,
    trigger: str = "manual",
) -> int:
    """Run the prewarm pipeline.  Returns an exit code (see module docstring).

    ``delay`` seconds sleeps before doing anything.  This replaces the
    Task Scheduler logon-trigger delay for the HKCU Run-key fallback
    path (which has no native delay), letting login settle so prewarm
    does not contend with every other startup program hitting disk.

    ADR-0009 Issue 2: the sentinel check now runs BEFORE the RAM check.
    The sentinel is the cheapest guard (one stat + one read of a tiny
    file) and produces the correct log message when the trigger re-fires
    (e.g. on Windows session unlock). The previous order — RAM first —
    logged "free RAM < budget — skipping" on re-fire, which misled users
    into thinking the sentinel had failed.

    ADR-0009 Issue 4: writes a PID file at the start of the warming
    phase and removes it in a finally block. The app's
    ``model_manager.try_load()`` polls this file via
    ``is_prewarm_running()`` to decide whether to wait for prewarm to
    finish before loading the model (avoids the disk-I/O fight when the
    user logs in faster than prewarm can warm the cache).
    """
    _pkg._setup_logging()
    if delay > 0:
        log.info("[PREWARM] delaying %.0fs to let login settle", delay)
        time.sleep(delay)
    log.info(
        "[PREWARM] starting (trigger=%s, force=%s, min_ram_mb=%d)",
        trigger,
        force,
        min_ram_mb,
    )
    t_start = time.perf_counter()

    if not force and not _pkg._fast_startup_enabled():
        log.info("[PREWARM] fast_startup disabled — exiting")
        return EXIT_DISABLED

    # SENTINEL FIRST — cheapest check, prevents all redundant work.
    # ADR-0009 Issue 2: this also produces the correct log message when
    # the trigger re-fires (e.g., on Windows session unlock).
    if not force and _pkg._already_warmed():
        log.info("[PREWARM] already ran this boot session — skipping")
        return EXIT_OK

    # RAM GUARD SECOND — only check if we're actually going to run.
    if not force:
        free = _pkg._free_ram_mb()
        if free is not None and free < min_ram_mb:
            log.info(
                "[PREWARM] free RAM %d MB < %d MB budget — skipping to avoid evicting the user's working set",
                free,
                min_ram_mb,
            )
            return EXIT_LOW_RAM

    # ER-15: BATTERY GUARD THIRD — skip prewarm when the host is on
    # battery AND charge < 60%. Prewarming reads ~6 GB off disk
    # (~2-3 Wh drain); on a laptop booted on battery at low charge,
    # that drain is perceptible. Defer to the next AC-plug event
    # (the next login/trigger after the user plugs in will re-run
    # prewarm via the sentinel-recheck path). The guard runs AFTER
    # the RAM check (cheaper than RAM probe is battery probe — both
    # ~1μs — but RAM is the more common guard so we keep it first for
    # log-message clarity on the common "low RAM" path).
    if not force and _pkg._on_battery_and_low_charge():
        log.info(
            "[PREWARM] host is on battery with charge < %d%%"
            " — skipping to avoid ~2-3 Wh drain (defer to next AC-plug event)",
            _BATTERY_LOW_CHARGE_THRESHOLD_PERCENT,
        )
        return EXIT_ON_BATTERY

    _pkg._lower_io_priority()

    # ADR-0009 Issue 4: write the PID file AFTER all the early-exit
    # guards so we don't leak a PID file for a process that bailed out
    # without doing any work. The finally block below removes it.
    _pkg._write_pid_file()
    _completion_event = _pkg._create_completion_event(os.getpid())

    # ADR-0009 Issue 4: ensure the PID file is always removed, even if
    # the warming pipeline raises or returns early. Without this, the
    # app's wait_for_prewarm() would block forever on a stale PID file
    # pointing at a dead process.
    try:
        return _pkg._run_warming_pipeline(min_ram_mb, force, t_start)
    finally:
        _pkg._signal_completion_event(_completion_event)
        _pkg._close_completion_event(_completion_event)
        _pkg._remove_pid_file()


def _run_warming_pipeline(
    min_ram_mb: int,
    force: bool,
    t_start: float,
) -> int:
    """Run the import + weights warming pipeline.

    ADR-0009 Issue 4: extracted from run() so the PID-file finally block
    in run() can wrap the entire warming phase without duplicating the
    pipeline logic. Returns the exit code.
    """
    # 1) Package files — read torch + transformers files into the OS page
    #    cache WITHOUT importing them (so we skip executing torch's code).
    #    Catches the bulk of the bytes; the app's own later import executes
    #    torch once and reads the warmed files from RAM.
    try:
        _pkg._warm_imports()
    except ImportError as exc:
        log.warning("[PREWARM] required import missing — aborting: %s", exc)
        return EXIT_IMPORT_FAILED
    except Exception:
        log.exception("[PREWARM] import stage failed — aborting")
        return EXIT_IMPORT_FAILED

    # 2) Model weights — only if already cached.  On the very first run
    #    (model not yet downloaded) there is nothing to warm; the app's
    #    normal download path will populate the cache, and subsequent
    #    prewarms will pick it up.
    #
    # STARTUP-4: previously this walked ALL models--* directories in the
    #    HF cache, warming ~2.1 GB of inactive Whisper variants when the
    #    active backend was parakeet. Now we only warm the ACTIVE model
    #    (config.asr_backend / config.model_size) plus the tiny.en
    #    Whisper model that the AsrBackendRegistry would actually fall
    #    back to on CUDA/init failure.
    warmed_any = False

    # Determine which model cache dirs are relevant to the active config.
    active_cache_dirs = _pkg._active_model_cache_dirs()
    log.info(
        "[PREWARM] Active model cache dirs to warm: %s",
        [d.name for d in active_cache_dirs] or "(none cached yet)",
    )

    # Walk only the active + fallback cache dirs.
    # Review fix H1: wrap _warm_file() per-file so one unreadable file
    # (permission denied, file locked, IO error, file deleted mid-walk)
    # doesn't abort the entire warming pipeline. Set warmed_any per-
    # snapshot so a fully-unreadable snapshot doesn't count as warmed.
    try:
        for model_dir in active_cache_dirs:
            log.info("[PREWARM] Warming model: %s", model_dir.name)
            snapshots_dir = model_dir / "snapshots"
            if not snapshots_dir.exists():
                continue
            try:
                for snapshot_dir in snapshots_dir.iterdir():
                    if not snapshot_dir.is_dir():
                        continue
                    snapshot_warmed_any = False
                    for f in snapshot_dir.rglob("*"):
                        if f.is_file() and f.suffix in (".bin", ".safetensors", ".pt", ".json", ".txt"):
                            # AB-17: skip warming when OS standby cache
                            # already holds >= 90% of the file. Avoids
                            # re-reading a 2.4 GB model.safetensors on every
                            # prewarm re-fire when the OS already has it cached.
                            try:
                                file_size = f.stat().st_size
                                if file_size >= _CACHE_RATIO_PROBE_MIN_BYTES:
                                    ratio = _pkg._cache_ratio(f, samples=10)
                                    if ratio >= _CACHE_RATIO_SKIP_WARMING_THRESHOLD:
                                        log.debug(
                                            "Skip warming %s (cache ratio %.2f >= %.2f)",
                                            f,
                                            ratio,
                                            _CACHE_RATIO_SKIP_WARMING_THRESHOLD,
                                        )
                                        snapshot_warmed_any = True
                                        continue
                            except Exception as e:
                                log.debug(
                                    "Cache ratio probe failed for %s, warming anyway: %s",
                                    f,
                                    e,
                                )
                            try:
                                _pkg._warm_file(f)
                                snapshot_warmed_any = True
                            except OSError as e:
                                log.debug(
                                    "[PREWARM] could not warm %s: %s",
                                    f,
                                    e,
                                )
                    if snapshot_warmed_any:
                        warmed_any = True
            except OSError as e:
                log.debug("[PREWARM] walk of %s failed: %s", model_dir.name, e)
    except Exception as e:
        log.debug("[PREWARM] HF cache walk failed: %s", e)

    # Review fix M6: distinguish "no model cache dirs" (first-ever run)
    # from "dirs exist but no files could be warmed" (permissions issue).
    if not active_cache_dirs:
        log.info("[PREWARM] No model cache dirs found — first-ever run")
        return EXIT_NO_MODEL
    if not warmed_any:
        log.warning(
            "[PREWARM] Model dirs exist but no files could be warmed "
            "(permissions? file locks?) — skipping sentinel write"
        )
        return EXIT_NO_MODEL

    elapsed = time.perf_counter() - t_start
    log.info("[PREWARM] complete (%.1fs)", elapsed)
    # ADR-0009 Issue 3: store the elapsed time in the sentinel so the
    # get_prewarm_status IPC endpoint can show "Last run: 20.4s" in the
    # About page without re-probing.
    _pkg._mark_warmed(elapsed)
    return EXIT_OK
