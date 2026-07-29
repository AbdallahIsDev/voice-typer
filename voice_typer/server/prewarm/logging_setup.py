# ARCH-045 / SPLIT-4: extracted from the original ``prewarm.py`` god-module.
"""Logging setup + early-exit guards for the prewarm pipeline.

Phase 4.5 / ARCH-045 — this module holds the four guard helpers that
``run()`` (in :mod:`.pipeline`) calls before doing any real work:

- :func:`_setup_logging` — wire up the prewarm log file.
- :func:`_fast_startup_enabled` — read the user's config flag.
- :func:`_free_ram_mb` — query available physical RAM.
- :func:`_lower_io_priority` — drop CPU + I/O priority so prewarm never
  competes with the user's real work.

Patch-path compatibility
------------------------
Tests heavily patch these names on the *package* namespace via
``monkeypatch.setattr(prewarm, "_setup_logging", ...)`` etc.  ``run()`` (in
:mod:`.pipeline`) looks them up via ``_pkg.X()`` at call time so the
patches take effect; the function bodies here are only exercised when
the tests *don't* patch them (e.g. ``test_ioprio_set_actually_runs_on_linux``
calls ``prewarm._lower_io_priority()`` directly).

``inspect.getsource`` compatibility
-----------------------------------
:func:`_lower_io_priority` is genuinely defined here, so
``inspect.getsource(prewarm._lower_io_priority)`` reads from this file
(tests/test_platform_and_config.py::TestIoprioSetUsesSyscallNotLibcSymbol).
"""

from __future__ import annotations

import contextlib
import logging
import logging.handlers
import os
import sys

from voice_typer.server.platform_utils import is_linux, is_windows

log = logging.getLogger("voice_typer.server.prewarm")


def _setup_logging(*, debug: bool = False) -> None:
    """Minimal logging — prewarm runs detached, so log to the app log file.

    Uses the shared :func:`log.setup_logging` so the format is
    consistent with the main app.  Avoids importing app.py to keep
    prewarm's cold-start cost minimal.

    Also writes to a dedicated ``prewarm.log`` (next to ``voice-typer.log``)
    that contains only ``[PREWARM]`` messages via a logger-name filter.
    The button in the About page opens this file so users can inspect
    prewarm behaviour without scrolling through the main log.

    Prewarm messages still flow to the shared ``voice-typer.log`` as well
    (via the handler added by ``log.setup_logging``), so the main log
    remains the complete record.

    Parameters
    ----------
    debug:
        G4-M-29: when ``True``, the prewarm handler emits DEBUG-level
        records (matches the main handler's ``debug`` gating).  When
        ``False`` (default), sits at INFO so production runs do not
        flood ``prewarm.log`` with high-frequency model-warming traces.
    """
    from voice_typer.server import _paths
    from voice_typer.server.log import setup_logging as _setup_logging_shared

    # RW-7: use the platform-aware config dir helper instead of the
    # previous hardcoded Path.home() / ".voice-typer".
    log_dir = _paths.config_dir()
    # G4-H-07: tighten the process umask while creating prewarm.log so
    # it is world-unreadable on POSIX (mirrors the main setup_logging
    # umask wrap).  Restored in ``finally`` so the change does not leak.
    _old_umask = os.umask(0o077)
    try:
        _setup_logging_shared(log_dir, debug=debug)
        # G4-H-07: chmod the config dir 0o700 on POSIX so co-located
        # users cannot read it (best-effort — silently no-op on Windows).
        if os.name == "posix":
            with contextlib.suppress(OSError):
                os.chmod(log_dir, 0o700)

        prewarm_log = log_dir / "prewarm.log"
        # CR-42 fix: align prewarm.log handler with the main voice-typer.log
        # handler's filtering and rotation policy. Previously this handler
        # used a plain Formatter (no session_id for cross-process correlation),
        # 1 MB × 2 rotation (vs main's 5 MB × 5 per ADR-0020 §11), and was
        # missing PIIRedactionFilter (defence-in-depth gap — any config field,
        # HF token, or transcription-adjacent value logged by prewarm code
        # would land unredacted in prewarm.log), _SessionFilter (no
        # cross-process correlation), and _BubbleLevelExclusionFilter (could
        # fill with bubble_level noise).
        from voice_typer.server.log import (
            _BubbleLevelExclusionFilter,
            _FileFormatter,
            _SessionFilter,
        )
        from voice_typer.server.security import PIIRedactionFilter

        prewarm_handler = logging.handlers.RotatingFileHandler(
            prewarm_log,
            maxBytes=5 * 1024 * 1024,  # 5 MB — align with main handler (was 1 MB)
            backupCount=5,  # was 2 — align with ADR-0020 §11 spec
            encoding="utf-8",
            errors="backslashreplace",
        )
        # G4-H-07: lock down prewarm.log (0o600 — only the owning user
        # can read it).  Best-effort on POSIX; silently no-op on Windows
        # where the umask already enforced 0o600 at creation time.
        if os.name == "posix":
            with contextlib.suppress(OSError):
                os.chmod(prewarm_log, 0o600)
        prewarm_handler.setFormatter(_FileFormatter())
        prewarm_handler.addFilter(logging.Filter("voice_typer.server.prewarm"))
        prewarm_handler.addFilter(_SessionFilter())
        prewarm_handler.addFilter(PIIRedactionFilter())
        prewarm_handler.addFilter(_BubbleLevelExclusionFilter())
        # G4-M-29: gate the prewarm handler on the ``debug`` flag so
        # production runs do not flood prewarm.log with DEBUG noise from
        # the model-warming pipeline (was hardcoded DEBUG).
        prewarm_handler.setLevel(logging.DEBUG if debug else logging.INFO)
        logging.getLogger("voice_typer").addHandler(prewarm_handler)
    except Exception as _setup_exc:
        # G4-M-30: replace the bare ``logging.basicConfig`` fallback
        # (which used a divergent format string and had no PII
        # redaction) with a minimal ``_FileFormatter``-using
        # ``StreamHandler`` that also carries ``PIIRedactionFilter``.
        # Keeps the format consistent with the main log file and
        # prevents PII from leaking through the fallback path.
        log.warning(
            "[PREWARM] shared logging setup failed — using minimal fallback: %s",
            _setup_exc,
        )
        _fallback_handler = logging.StreamHandler(sys.stderr)
        _fallback_handler.setLevel(logging.INFO)
        try:
            from voice_typer.server.log import _FileFormatter
            from voice_typer.server.security import PIIRedactionFilter

            _fallback_handler.setFormatter(_FileFormatter())
            _fallback_handler.addFilter(PIIRedactionFilter())
        except Exception as _fmt_exc:
            # Last-ditch: bare StreamHandler without formatter/filter
            # is still better than nothing (e.g. circular import on
            # cold start).  Logged at WARNING so the operator can see
            # why the format diverged.
            log.warning(
                "[PREWARM] could not attach _FileFormatter/PIIRedactionFilter to fallback: %s",
                _fmt_exc,
            )
        _root = logging.getLogger()
        if not any(isinstance(h, logging.StreamHandler) for h in _root.handlers):
            _root.addHandler(_fallback_handler)
        _root.setLevel(logging.INFO)
    finally:
        os.umask(_old_umask)


# ─── Guards ──────────────────────────────────────────────────────────────


def _fast_startup_enabled() -> bool:
    """Return whether the user has enabled the prewarm scheduled task.

    PW-3: reads ``Config.fast_startup`` (defaults to True). When False,
    the prewarm entrypoint exits early with :data:`EXIT_DISABLED` so the
    OS scheduled task fires but does nothing — keeping the startup
    contract simple (the task always exists; whether it does work is
    controlled by the config flag).

    On any read error (corrupt config, missing file, etc.) we fall back
    to True so a broken config never silently disables prewarm for
    users who rely on it. The error is logged for diagnosis.
    """
    try:
        from voice_typer.server.config import Config

        cfg = Config.load()
        enabled = bool(getattr(cfg, "fast_startup", True))
        if not enabled:
            log.info("[PREWARM] fast_startup disabled by user — skipping")
        return enabled
    except Exception as e:
        log.warning(
            "[PREWARM] Failed to read fast_startup from config, defaulting to True: %s",
            e,
        )
        return True


def _free_ram_mb() -> int | None:
    """Return available physical RAM in MB, or None if it can't be queried."""
    try:
        import psutil  # type: ignore[import-untyped]

        return int(psutil.virtual_memory().available / (1024 * 1024))
    except ImportError:
        pass
    # Windows fallback via ctypes (no extra dependency).
    if is_windows():
        try:
            import ctypes

            class _MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            stat = _MEMORYSTATUSEX()
            stat.dwLength = ctypes.sizeof(stat)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat))
            return int(stat.ullAvailPhys / (1024 * 1024))
        except Exception as exc:
            log.debug("[PREWARM] ctypes RAM query failed: %s", exc)
    return None


# ER-15: battery guard. Prewarming reads ~4.5 GB of torch+transformers
# package files + ~2.4 GB of Parakeet weights off disk — ~2-3 Wh per run.
# On a laptop booted on battery at < 60% charge, that drain is perceptible
# (a user who reboots/registers 3-4×/day on battery wastes ~10 Wh/day).
# Skip prewarm when on battery + low charge; defer to the next AC-plug
# event. psutil is already a dependency (used by ``_free_ram_mb`` and
# ``paths._boot_time``). The 60% threshold matches the entry's spec.
_BATTERY_LOW_CHARGE_THRESHOLD_PERCENT = 60


def _on_battery_and_low_charge() -> bool:
    """Return True iff the host is on battery AND charge < 60%.

    ER-15: skip prewarm with EXIT_ON_BATTERY when this returns True.
    Returns False (don't skip) in any of these cases:
      - The host is plugged in (``power_plugged is True``).
      - The host is on battery but charge >= 60% (enough headroom to
        absorb the ~2-3 Wh prewarm drain without materially shortening
        the user's session).
      - The host has no battery (desktop / server / VM —
        ``sensors_battery()`` returns None).
      - ``psutil`` is not installed (legacy fallback — don't block
        prewarm on a missing optional dependency).
      - ``psutil.sensors_battery()`` raises (some platforms / VMs
        expose a broken ACPI battery interface).

    The threshold (60%) and the (unplugged + low-charge) conjunction
    are pinned by ``tests/test_prewarm_er_fix_e2.py`` — see
    ``TestOnBatteryAndLowCharge``.
    """
    try:
        import psutil  # type: ignore[import-untyped]
    except ImportError:
        # psutil is in requirements-lock.txt but defensively treat its
        # absence as "not on battery" so prewarm still runs.
        return False
    try:
        battery = psutil.sensors_battery()
    except Exception as exc:  # noqa: BLE001 — broken ACPI can raise weirdly
        log.debug("[PREWARM] psutil.sensors_battery() raised: %s", exc)
        return False
    if battery is None:
        # Desktop / server / VM with no battery — don't skip.
        return False
    # psutil.sensors_battery() returns a 3-tuple/namedtuple
    # ``(percent, secsleft, power_plugged)`` — unpack positionally so
    # both the namedtuple and a plain tuple work (tests use a
    # ``namedtuple(_FakeBattery, [...])`` stand-in).
    try:
        percent, _secs_left, power_plugged = battery  # noqa: F841 — _secs_left unused
    except (TypeError, ValueError):
        log.debug("[PREWARM] psutil.sensors_battery() returned non-3-tuple: %r", battery)
        return False
    if power_plugged:
        return False
    if percent is None:
        # Some ACPI drivers return None for percent while still reporting
        # power_plugged=False — treat as "not low" rather than blocking.
        return False
    return percent < _BATTERY_LOW_CHARGE_THRESHOLD_PERCENT


def _lower_io_priority() -> None:
    """Drop this process's I/O and CPU priority so prewarming never
    competes with real user work.  Best-effort — silently no-op off Windows
    or on older builds.

    Uses ``SetPriorityClass(process_mode_background_begin)``, which is the
    API Microsoft recommends for background maintenance tasks: it lowers
    CPU, I/O, and memory scheduling priorities atomically.  Falls back to
    ``BELOW_NORMAL_PRIORITY_CLASS`` if background mode is unavailable
    (e.g. the process is already background, or an older Windows build).

    STARTUP-5: on POSIX, lowers CPU priority via os.nice() and I/O priority
    via ionice (Linux only). Best-effort — silently no-ops if the calls
    fail (e.g. insufficient permissions).
    """
    if not is_windows():
        # STARTUP-5: POSIX priority lowering.
        try:
            # Lower CPU priority (POSIX nice). +10 = below normal.
            os.nice(10)
            log.debug("[PREWARM] POSIX: lowered CPU priority via os.nice(10)")
        except (OSError, PermissionError) as e:
            log.debug("[PREWARM] POSIX: os.nice failed: %s", e)
        # On Linux, also try to lower I/O priority to "idle" (class 3).
        # Bug fix: ioprio_set is a SYSCALL, not a libc exported function.
        # The previous code checked hasattr(libc, "ioprio_set") which always
        # returned False (the symbol doesn't exist in libc), so the I/O
        # priority lowering silently no-opped. The correct way is to call
        # syscall(SYS_ioprio_set, which, who, ioprio) via libc.syscall.
        # IOPRIO_WHO_PROCESS=1, IOPRIO_CLASS_IDLE=3,
        # IOPRIO_PRIO_VALUE(class, level) = (class << 13) | level
        #
        # XPLAT-04: architecture-aware syscall number lookup. The
        # ioprio_set syscall number varies by architecture:
        #   x86_64      : 251
        #   aarch64     : 314
        #   i386        : 289
        #   ppc64le     : 345
        #   s390x       : 378
        #   riscv64     : IORING_SETUP_IOPOLL (not yet stable)
        # Unrecognized architectures fall back to trying 251 then 314
        # (the two most common) and log a debug message if both fail.
        if is_linux():
            try:
                import ctypes

                # XPLAT-04: lookup syscall number by architecture
                _machine_to_ioprio_set = {
                    "x86_64": 251,
                    "amd64": 251,
                    "aarch64": 314,
                    "arm64": 314,
                    "i386": 289,
                    "i686": 289,
                    "ppc64le": 345,
                    "ppc64": 345,
                    "s390x": 378,
                }
                _sys_num = _machine_to_ioprio_set.get(os.uname().machine.lower(), 0)
                libc = ctypes.CDLL("libc.so.6", use_errno=True)
                libc.syscall.restype = ctypes.c_long
                libc.syscall.argtypes = [
                    ctypes.c_long,
                    ctypes.c_uint,
                    ctypes.c_int,
                    ctypes.c_uint,
                ]
                ioprio_who_process = 1
                ioprio_class_idle = 3
                ioprio = (ioprio_class_idle << 13) | 0
                if _sys_num:
                    rc = libc.syscall(_sys_num, ioprio_who_process, 0, ioprio)
                    if rc == 0:
                        log.debug(
                            "[PREWARM] Linux: set I/O priority to idle (syscall %d, arch=%s)",
                            _sys_num,
                            os.uname().machine,
                        )
                    else:
                        log.debug(
                            "[PREWARM] Linux: ioprio_set syscall %d failed (arch=%s)",
                            _sys_num,
                            os.uname().machine,
                        )
                else:
                    # Unrecognized architecture — try the two most common
                    # syscall numbers as a best-effort fallback.
                    log.debug(
                        "[PREWARM] Linux: unrecognized arch %s — trying fallback syscall numbers 251, 314",
                        os.uname().machine,
                    )
                    for sys_num in (251, 314):
                        rc = libc.syscall(sys_num, ioprio_who_process, 0, ioprio)
                        if rc == 0:
                            log.debug(
                                "[PREWARM] Linux: set I/O priority to idle (syscall %d)",
                                sys_num,
                            )
                            break
                    else:
                        log.debug(
                            "[PREWARM] Linux: ioprio_set syscall failed for both 251 and 314 (arch=%s)",
                            os.uname().machine,
                        )
            except Exception as e:
                log.debug("[PREWARM] Linux: ioprio_set failed: %s", e)
        return
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.windll.kernel32
        kernel32.SetPriorityClass.restype = wintypes.BOOL
        kernel32.SetPriorityClass.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE

        hproc = kernel32.GetCurrentProcess()

        # PROCESS_MODE_BACKGROUND_BEGIN = 0x00100000.  This is a
        # SetPriorityClass value (NOT SetProcessInformation) and is exactly
        # what Microsoft recommends for background I/O-heavy work.
        process_mode_background_begin = 0x00100000
        if kernel32.SetPriorityClass(hproc, process_mode_background_begin):
            log.debug("[PREWARM] Set process to BACKGROUND priority mode")
            return

        # Fallback: below-normal priority.  Less aggressive (doesn't lower
        # I/O priority) but works everywhere and never raises.
        below_normal_priority_class = 0x00004000
        if kernel32.SetPriorityClass(hproc, below_normal_priority_class):
            log.debug("[PREWARM] Set process priority to BELOW_NORMAL")
            return

        log.debug(
            "[PREWARM] could not lower process priority (err=%d)",
            kernel32.GetLastError(),
        )
    except Exception as exc:
        log.debug("[PREWARM] could not lower process priority: %s", exc)
