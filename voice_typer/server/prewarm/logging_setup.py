# SPLIT-4: extracted from the original ``prewarm.py`` god-module.
"""Logging setup + early-exit guards for the prewarm pipeline.

Phase 4.5 /  — this module holds the four guard helpers that
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
from pathlib import Path

from voice_typer.server.platform_utils import is_linux, is_macos, is_windows

log = logging.getLogger("voice_typer.server.prewarm")


def _setup_logging(*, debug: bool = False, prewarm_only: bool = False) -> None:
    """Wire up logging for the prewarm pipeline — a SINGLE ``prewarm.log``.

    Single-file policy: there is exactly ONE prewarm log file,
    ``prewarm.log`` (next to ``voice-typer.log``). Numbered backups
    (``prewarm.log.1``, ...) are NEVER created — the file truncates in
    place when it exceeds its size cap.

    Two call modes:

    - ``prewarm_only=True`` — the detached prewarm SUBPROCESS (the
      scheduled task / ``run_prewarm`` spawn). The shared
      :func:`log.setup_logging` runs normally (complete record in
      ``voice-typer.log``), but a ``_NotPrewarmFilter`` is attached to the
      shared handler so prewarm lines are EXCLUDED from ``voice-typer.log``,
      and the dedicated single-file ``prewarm.log`` handler captures them.
      Prewarm lines therefore land in exactly ONE file (``prewarm.log``),
      and ``voice-typer-prewarm.log`` no longer exists anywhere
      (eliminates the old double-sink).
    - ``prewarm_only=False`` — the main app process running the prewarm
      pipeline in-process (test fakes / future in-process invocation).
      The shared setup writes the complete record to ``voice-typer.log``
      (including prewarm lines — no exclusion filter) AND a dedicated
      single-file ``prewarm.log`` handler is installed so the About-page
      "Open prewarm log" button opens a real file.

    In both modes the shared :func:`log.setup_logging` format + PII /
    session / bubble-level filters apply, keeping prewarm output
    consistent with the main log.

    Parameters
    ----------
    debug:
        when ``True``, the prewarm handler emits DEBUG-level records.
        When ``False`` (default), sits at INFO so production runs do not
        flood ``prewarm.log`` with high-frequency model-warming traces.
    """
    from voice_typer.server import _paths
    from voice_typer.server.log import setup_logging as _setup_logging_shared

    # use the platform-aware config dir helper instead of the
    # previous hardcoded Path.home() / ".voice-typer".
    log_dir = _paths.config_dir()
    # tighten the process umask while creating prewarm.log so
    # it is world-unreadable on POSIX (mirrors the main setup_logging
    # umask wrap).  Restored in ``finally`` so the change does not leak.
    _old_umask = os.umask(0o077)
    try:
        if prewarm_only:
            # Prewarm SUBPROCESS (DJ-45): the shared setup runs normally
            # (complete record in voice-typer.log), then a
            # ``_NotPrewarmFilter`` is attached to the shared handler so
            # prewarm lines are excluded from voice-typer.log, and the
            # dedicated single-file prewarm.log handler captures them.
            # Prewarm lines land in exactly ONE file; the old
            # ``voice-typer-prewarm.log`` double-sink no longer exists.
            _setup_logging_shared(log_dir, debug=debug)
            _attach_not_prewarm_filter()
            _install_dedicated_prewarm_handler(log_dir, debug)
        else:
            # In-process (main app): complete record in voice-typer.log
            # (prewarm lines included — no exclusion filter), plus a
            # dedicated single-file prewarm.log for the UI button.
            _setup_logging_shared(log_dir, debug=debug)
            _install_dedicated_prewarm_handler(log_dir, debug)
        # chmod the config dir 0o700 on POSIX so co-located
        # users cannot read it (best-effort — silently no-op on Windows).
        if os.name == "posix":
            with contextlib.suppress(OSError):
                os.chmod(log_dir, 0o700)
    except Exception as _setup_exc:
        # replace the bare ``logging.basicConfig`` fallback
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


class _NotPrewarmFilter(logging.Filter):
    """Exclude prewarm-namespace records from the shared
    ``voice-typer.log`` handler (DJ-45).

    Attached to every shared handler whose target is ``voice-typer.log``
    when the prewarm subprocess runs (``prewarm_only=True``) so prewarm
    records only land in the dedicated ``prewarm.log`` — they never leak
    into the main app's log from a detached subprocess. The
    ``_vt_not_prewarm`` class attribute is the marker the DJ-45 tests and
    the idempotent attach helper use to avoid stacking duplicates.
    """

    # Marker used by ``_attach_not_prewarm_filter`` (idempotency) and by
    # the DJ-45 dedup tests.
    _vt_not_prewarm = True  # type: ignore[attr-defined]

    def filter(self, record: logging.LogRecord) -> bool:
        # Exclude the prewarm namespace (and its children) from the
        # shared handler. Everything else still flows through.
        return not record.name.startswith("voice_typer.server.prewarm")


def _attach_not_prewarm_filter() -> None:
    """Attach a :class:`_NotPrewarmFilter` to every shared
    ``voice-typer.log`` handler (prewarm-only subprocess path).

    Idempotent: a handler that already carries a ``_NotPrewarmFilter``
    (marker ``_vt_not_prewarm``) is skipped, so repeated
    ``_setup_logging(prewarm_only=True)`` calls never stack duplicates.
    """
    _vt_root = logging.getLogger("voice_typer")
    for h in _vt_root.handlers:
        if not isinstance(h, logging.handlers.RotatingFileHandler):
            continue
        if Path(h.baseFilename).name != "voice-typer.log":
            continue
        if any(getattr(f, "_vt_not_prewarm", False) for f in h.filters):
            continue
        h.addFilter(_NotPrewarmFilter())


def _install_dedicated_prewarm_handler(log_dir: Path, debug: bool) -> None:
    """Install the dedicated single-file ``prewarm.log`` handler.

    Only used by the in-process path (``prewarm_only=False``) so the
    About-page "Open prewarm log" button has a real file. The handler
    carries the same filter chain as the main log (PII redaction,
    session ID, bubble-level exclusion) plus a ``voice_typer.server.prewarm``
    namespace filter, and follows the single-file policy
    (``backupCount=0`` — truncate-in-place, never ``prewarm.log.1``).

    Idempotent: a repeated call never stacks a second handler (marked
    ``_vt_prewarm = True``; the dedup check skips re-adding, so repeated
    ``_setup_logging`` calls in the same process hold ONE file
    descriptor on ``prewarm.log`` instead of N).
    """
    from voice_typer.server.log import (
        _BubbleLevelExclusionFilter,
        _FileFormatter,
        _SecureTruncatingFileHandler,
        _SessionFilter,
    )
    from voice_typer.server.security import PIIRedactionFilter

    prewarm_log = log_dir / "prewarm.log"
    # Single-file policy: 1 MiB cap, ZERO backups — truncate in place.
    prewarm_handler = _SecureTruncatingFileHandler(
        prewarm_log,
        maxBytes=1 * 1024 * 1024,
        backupCount=0,
        encoding="utf-8",
        errors="backslashreplace",
    )
    # lock down prewarm.log (0o600 — only the owning user
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
    # gate the prewarm handler on the ``debug`` flag so
    # production runs do not flood prewarm.log with DEBUG noise from
    # the model-warming pipeline (was hardcoded DEBUG).
    prewarm_handler.setLevel(logging.DEBUG if debug else logging.INFO)
    # dedup: mark the handler with ``_vt_prewarm = True`` and skip
    # re-adding when a prewarm handler already exists. Repeated
    # ``_setup_logging`` calls in the same process previously stacked
    # N prewarm handlers — multiplying each prewarm line N times AND
    # holding N file descriptors on ``prewarm.log`` (locking it on
    # Windows).
    _vt_root = logging.getLogger("voice_typer")
    if not any(
        getattr(h, "_vt_prewarm", False)
        for h in _vt_root.handlers
        if isinstance(h, logging.handlers.RotatingFileHandler)
        and Path(h.baseFilename).name == "prewarm.log"
    ):
        prewarm_handler._vt_prewarm = True  # type: ignore[attr-defined]
        _vt_root.addHandler(prewarm_handler)


# ─── Guards ──────────────────────────────────────────────────────────────


def _fast_startup_enabled() -> bool:
    """Return whether the user has enabled the prewarm scheduled task.

    reads ``Config.fast_startup`` (defaults to True). When False,
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


# battery guard. Prewarming reads ~4.5 GB of torch+transformers
# package files + ~2.4 GB of Parakeet weights off disk — ~2-3 Wh per run.
# On a laptop booted on battery at < 60% charge, that drain is perceptible
# (a user who reboots/registers 3-4×/day on battery wastes ~10 Wh/day).
# Skip prewarm when on battery + low charge; defer to the next AC-plug
# event. psutil is already a dependency (used by ``_free_ram_mb`` and
# ``paths._boot_time``). The 60% threshold matches the entry's spec.
_BATTERY_LOW_CHARGE_THRESHOLD_PERCENT = 60


def _on_battery_and_low_charge() -> bool:
    """Return True iff the host is on battery AND charge < 60%.

    skip prewarm with EXIT_ON_BATTERY when this returns True.
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
        # architecture-aware syscall number lookup. The
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

                # lookup syscall number by architecture
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
        # lower I/O priority on macOS via setiopolicy_np.
        # macOS does NOT support ioprio_set; instead libSystem exposes
        # setiopolicy_np(IOPOL_TYPE_DISK, IOPOL_SCOPE_PROCESS,
        # IOPOL_DISK_THROTTLE) which schedules this process's disk I/O
        # at the lowest priority — the macOS equivalent of Linux's
        # IOPRIO_CLASS_IDLE. Best-effort: silently no-ops if the call
        # fails (older macOS, sandbox restrictions, etc.).
        elif is_macos():
            try:
                import ctypes

                lib = ctypes.CDLL("/usr/lib/libSystem.B.dylib", use_errno=True)
                # IOPOL_TYPE_DISK=0, IOPOL_SCOPE_PROCESS=0, IOPOL_DISK_THROTTLE=3
                lib.setiopolicy_np.restype = ctypes.c_int
                lib.setiopolicy_np.argtypes = [
                    ctypes.c_int,
                    ctypes.c_int,
                    ctypes.c_int,
                ]
                result = lib.setiopolicy_np(0, 0, 3)
                if result == -1:
                    log.debug(
                        "[PREWARM] macOS: setiopolicy_np failed (errno=%d)",
                        ctypes.get_errno(),
                    )
                else:
                    log.debug("[PREWARM] macOS: lowered disk I/O priority to THROTTLE (IOPOL_DISK_THROTTLE)")
            except OSError as e:
                log.debug("[PREWARM] macOS: could not load libSystem.B.dylib: %s", e)
            except Exception as e:
                log.debug("[PREWARM] macOS: setiopolicy_np failed: %s", e)
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
