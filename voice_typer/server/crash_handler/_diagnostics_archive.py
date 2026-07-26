"""Crash-diagnostics archive management.

``_compute_crash_header`` builds the static header block (app version,
OS build, Python version, loaded-module snapshot) prepended to every
``crash_diagnostics.<PID>.txt`` file.

``set_crash_handler_config_dir`` caches the config-dir path, pre-builds
the crash file path (with NUL terminator for ``CreateFileW``), resets
the rate-limit flag, and pre-computes the static header.

``_archive_crash_file`` / ``_enforce_archive_retention`` /
``_sweep_stale_diagnostics`` manage the
``<config_dir>/crash_diagnostics_archive/`` directory: move crash files
into the archive, enforce a keep-last-N retention policy, and sweep
stale files from the config_dir root.

``report_pending_crash`` scans the config_dir for leftover
``crash_diagnostics.*.txt`` / ``python_crash.*.txt`` files from a
previous session, logs their contents at WARNING level, archives them,
and returns a human-readable summary for the caller to surface.

Split out from the original monolithic ``crash_handler.py``.
"""

from __future__ import annotations

import contextlib
import logging
import os
import platform
import sys
import time
from pathlib import Path

from voice_typer.server.crash_handler._constants import (
    _ARCHIVE_RETENTION_KEEP,
    _CRASH_DIAGNOSTICS_ARCHIVE,
    _HEADER_MAX_MODULES,
    _MAX_ACTIVE_FILES,
    _MAX_AGE_SECONDS,
)

log = logging.getLogger(__name__)


def _compute_crash_header() -> bytes:
    """Build the static header block for ``crash_diagnostics.<PID>.txt``.

    Called once at ``set_crash_handler_config_dir()`` time so the VEH
    callback can write the header as a preamble without any heap
    allocations (the bytes are already encoded and cached in
    ``_crash_header_bytes``).

    The header carries enough static context for a support engineer to
    triage a silent SEH crash without asking the user to run
    ``--status`` manually:
      - App version (from ``voice_typer.__version__``)
      - OS / platform build (``platform.platform()`` + ``platform.version()``)
      - Python version (``sys.version``)
      - Loaded-module snapshot (top-level package names from
        ``sys.modules``, capped to keep the file size reasonable)

    All assembly is best-effort: any failure (e.g. ``voice_typer`` not
    yet importable during early bootstrap) is swallowed and the
    corresponding field is replaced with ``<unknown>`` so the header
    is always emitted.

    Returns
    -------
    bytes
        UTF-8 encoded header terminated with a trailing CRLF.
    """
    lines: list[str] = ["=== VOICE-TYPER CRASH DIAGNOSTICS HEADER ==="]
    try:
        import voice_typer

        app_version = getattr(voice_typer, "__version__", "<unknown>")
    except Exception:
        app_version = "<unknown>"
    lines.append(f"App version: {app_version}")
    try:
        lines.append(f"OS: {platform.platform()}")
        lines.append(f"OS build: {platform.version()}")
    except Exception:
        lines.append("OS: <unknown>")
    try:
        lines.append(f"Python: {sys.version}")
    except Exception:
        lines.append("Python: <unknown>")
    try:
        top_level: list[str] = []
        seen: set[str] = set()
        for name in sorted(sys.modules):
            top = name.split(".", 1)[0]
            if top in seen:
                continue
            seen.add(top)
            top_level.append(top)
            if len(top_level) >= _HEADER_MAX_MODULES:
                break
        total = len(sys.modules)
        lines.append(f"Loaded modules (total={total}, shown={len(top_level)}):")
        for m in top_level:
            lines.append(f"  {m}")
    except Exception:
        lines.append("Loaded modules: <unknown>")
    lines.append("=== END HEADER ===")
    return ("\r\n".join(lines) + "\r\n").encode("utf-8", errors="replace")


def set_crash_handler_config_dir(config_dir: Path) -> None:
    """Cache the config directory path for the VEH callback.

    Pre-builds the crash diagnostics file path as a Python str
    with a null terminator (\\0) for CreateFileW.  Stored as a str
    so we can pass it via c_wchar_p (which correctly marshals the
    internal UTF-16 buffer as LPCWSTR).

    The path is built via ``os.path.join`` (was a hardcoded ``\\``
    backslash, which broke on POSIX where the VEH callback is never
    invoked but the cached path is still asserted on by tests).

    The dead ``_config_dir_bytes`` / ``_CONFIG_DIR_BYTES`` dual binding
    was removed — only ``_crash_file_path`` is used downstream.

    Also caches the config dir for ``_crash_excepthook`` so the
    Python-level excepthook can write a ``python_crash.<PID>.txt``
    marker.

    Resets ``_crash_written`` so each test (and each process) starts
    with a clean rate-limit flag.

    Pre-computes the static crash-diagnostics header (app version, OS
    build, Python version, loaded-module snapshot) and caches it in
    ``_crash_header_bytes``.  The VEH callback writes this header as a
    preamble to ``crash_diagnostics.<PID>.txt`` when a crash occurs —
    see ``_vectored_handler_impl``.

    Mutable state (``_crash_file_path``, ``_PID``,
    ``_python_crash_dir``, ``_crash_written``, ``_crash_header_bytes``)
    lives on the ``crash_handler`` facade module so test mutations on
    ``crash_handler.<name>`` propagate. Accessed via ``_ch.<name>``.
    """
    from voice_typer.server import crash_handler as _ch

    try:
        resolved = Path(config_dir).resolve()
        _ch._PID = os.getpid()
        # Use os.path.join instead of a hardcoded backslash so the
        # cached path is correct on both Windows and POSIX (POSIX never
        # invokes the VEH callback, but tests still inspect the cached
        # path).  Preserve the trailing NUL terminator required by
        # CreateFileW.
        _ch._crash_file_path = os.path.join(str(resolved), f"crash_diagnostics.{_ch._PID}.txt") + "\0"
        _ch._python_crash_dir = resolved
        # Reset the rate-limit flag so a fresh process (or a re-init
        # in tests) can write a new crash record.
        _ch._crash_written = False
        # Pre-compute the header once at config-dir cache time so the
        # VEH callback doesn't have to allocate.  Best-effort — a
        # failure here leaves ``_crash_header_bytes`` empty and the VEH
        # callback falls back to writing only the crash body (preserving
        # the pre-fix behavior).
        with contextlib.suppress(Exception):
            _ch._crash_header_bytes = _compute_crash_header()
    except Exception as exc:
        log.debug("[CRASH] Failed to cache config dir: %s", exc)
        _ch._crash_file_path = ""
        _ch._python_crash_dir = None
        _ch._crash_written = False
        _ch._crash_header_bytes = b""


def _archive_crash_file(file_path: Path, config_dir: Path) -> None:
    """Move a crash diagnostics / python_crash file to the archive.

    The archive lives at ``<config_dir>/crash_diagnostics_archive/`` and
    is created with ``0o700`` perms on POSIX so the archived crash
    records (which may include exception addresses, thread IDs, etc.)
    are not world-readable.

    After moving, applies the retention policy (keep last
    ``_ARCHIVE_RETENTION_KEEP`` files in the archive, delete older).
    """
    archive_dir = Path(config_dir) / _CRASH_DIAGNOSTICS_ARCHIVE
    archive_dir.mkdir(parents=True, exist_ok=True)
    # Tighten archive dir perms on POSIX so crash records (which
    # contain process internals) are not world-readable.
    if sys.platform != "win32":
        with contextlib.suppress(OSError):
            os.chmod(archive_dir, 0o700)
    target = archive_dir / file_path.name
    # If the target already exists (e.g. a previous archive had the
    # same PID), disambiguate by appending a monotonic timestamp.
    if target.exists():
        stem = file_path.stem
        suffix = file_path.suffix
        target = archive_dir / f"{stem}.{int(time.time() * 1000)}{suffix}"
    file_path.rename(target)
    log.info("[CRASH] Archived diagnostics file: %s -> %s", file_path.name, target.name)
    _enforce_archive_retention(archive_dir)


def _enforce_archive_retention(archive_dir: Path) -> None:
    """Keep only the last ``_ARCHIVE_RETENTION_KEEP`` files.

    Files are sorted by mtime (newest first); older files beyond the
    retention cap are deleted.  All errors are suppressed (best-effort).
    """
    try:
        files = sorted(
            archive_dir.glob("*"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
    except Exception:
        return
    for stale in files[_ARCHIVE_RETENTION_KEEP:]:
        with contextlib.suppress(Exception):
            stale.unlink()


def _sweep_stale_diagnostics(config_dir: Path) -> None:
    """Sweep stale crash diagnostics from the config_dir root.

    After ``report_pending_crash`` moves all current files into the
    archive, the config_dir root should normally be empty of
    ``crash_diagnostics.*.txt`` / ``python_crash.*.txt`` files.  This
    sweep is a safety net for files that were left behind by an older
    version (pre-archiving) or by a failed move.

    Policy:
      * Delete any file older than ``_MAX_AGE_DAYS`` days (mtime).
      * If more than ``_MAX_ACTIVE_FILES`` files remain, delete the oldest
        beyond the cap.
    """
    try:
        diagnostics_dir = Path(config_dir).resolve()
        if not diagnostics_dir.is_dir():
            return
        files = list(diagnostics_dir.glob("crash_diagnostics.*.txt"))
        files.extend(diagnostics_dir.glob("python_crash.*.txt"))
        if not files:
            return
        now = time.time()
        # First pass: delete files older than the mtime cutoff.
        for f in files:
            try:
                if now - f.stat().st_mtime > _MAX_AGE_SECONDS:
                    f.unlink()
            except Exception as exc:
                log.debug("[CRASH] sweep: failed to delete stale %s: %s", f, exc)
        # Second pass: enforce the count cap on the remaining files.
        try:
            remaining = sorted(
                (f for f in files if f.exists()),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
        except Exception:
            return
        for stale in remaining[_MAX_ACTIVE_FILES:]:
            with contextlib.suppress(Exception):
                stale.unlink()
    except Exception as exc:
        log.debug("[CRASH] sweep: failed: %s", exc)


def report_pending_crash(config_dir: Path) -> str | None:
    """Check for leftover crash diagnostics from a previous session.

    Scans the config directory for:
      * ``crash_diagnostics.*.txt`` files written by the VEH callback
        when a previous process crashed silently (heap corruption,
        access violation, etc.).
      * ``python_crash.*.txt`` marker files written by
        ``_crash_excepthook`` when an unhandled Python exception
        terminated the previous process.

    If any are found:
      1. Logs the full contents at ``WARNING`` level to ``voice-typer.log``
      2. Moves the file to ``<config_dir>/crash_diagnostics_archive/``
         instead of deleting it, so the diagnostic bundle can include it
         later.
      3. Returns a human-readable summary for the caller to surface
         (e.g., as a tray notification).

    After processing, applies the sweep (30-day mtime cutoff + keep last
    10) to the config_dir root as a safety net for files left behind by
    failed moves or older versions.

    Returns ``None`` if no crash diagnostics were found.
    """
    # The file pattern uses the process PID from the previous run, so we
    # can't predict the exact filename.  Globbing is done via pathlib.
    try:
        diagnostics_dir = Path(config_dir).resolve()
        if not diagnostics_dir.is_dir():
            return None
        # Collect all crash diagnostics files matching the pattern.
        # Also collect python_crash.*.txt marker files written by the
        # Python excepthook so they are surfaced in the same startup
        # notification.
        crash_files = sorted(diagnostics_dir.glob("crash_diagnostics.*.txt"))
        python_crash_files = sorted(diagnostics_dir.glob("python_crash.*.txt"))
        if not crash_files and not python_crash_files:
            return None
    except Exception as exc:
        log.debug("[CRASH] Failed to scan for diagnostics files: %s", exc)
        return None

    summary_parts: list[str] = []
    for crash_file in crash_files:
        try:
            content = crash_file.read_text(encoding="utf-8").strip()
            if not content:
                log.debug(
                    "[CRASH] Found empty diagnostics file %s — cleaning up",
                    crash_file.name,
                )
                continue
            # Log each line of the crash diagnostics to voice-typer.log
            # at WARNING level so it appears clearly in the log file.
            log.warning("[CRASH] === Previous session crashed! Diagnostics follow ===")
            for line in content.split("\r\n"):
                line = line.strip()
                if line:
                    log.warning("[CRASH] %s", line)
            # Extract the exception code for a human-readable summary
            # Each message now includes possible causes: low memory (RAM)
            # and low disk space — the two most common triggers for
            # silent heap corruption / access violation crashes.
            if "STATUS_HEAP_CORRUPTION" in content:
                summary_parts.append(
                    "Heap corruption (0xC0000374). Likely cause: low memory (RAM), "
                    "low disk space, or a C extension bug."
                )
            elif "STATUS_ACCESS_VIOLATION" in content:
                summary_parts.append("Access violation (0xC0000005). Likely cause: low memory or a C extension bug.")
            elif "STATUS_STACK_BUFFER_OVERRUN" in content:
                summary_parts.append(
                    "Stack overrun (0xC0000409). Likely cause: low memory, stack overflow, or a C extension bug."
                )
            elif "STATUS_FATAL_APP_EXIT" in content:
                summary_parts.append(
                    "Fatal exit (0x40000015). The process detected a critical "
                    "error and terminated itself. Likely cause: low memory "
                    "or a C extension bug."
                )
            else:
                # Extract the crash code line for unknown codes
                for line in content.split("\r\n"):
                    if "code=0x" in line:
                        summary_parts.append(
                            f"Process crashed: {line.strip()}. Likely cause: low memory or low disk space."
                        )
                        break
                else:
                    summary_parts.append(
                        "Previous session ended unexpectedly. Likely cause: low memory or low disk space."
                    )
        except Exception as exc:
            # Use ``crash_file.name`` (not the full ``crash_file``
            # Path) so the log doesn't include the user's home
            # directory path.
            log.warning("[CRASH] Failed to read diagnostics file %s: %s", crash_file.name, exc)
        finally:
            # Move the file to the archive instead of unlinking, so
            # the diagnostic bundle can include it later.  Moving
            # (rather than deleting) also preserves the file for
            # forensic review if the user files a bug report.
            try:
                _archive_crash_file(crash_file, diagnostics_dir)
            except Exception as exc:
                log.debug("[CRASH] Failed to archive diagnostics file %s: %s", crash_file.name, exc)

    # Process python_crash marker files written by the Python-level
    # excepthook.  These capture unhandled Python exceptions (e.g. in
    # daemon threads) that would otherwise only appear on stderr.
    for py_crash_file in python_crash_files:
        try:
            content = py_crash_file.read_text(encoding="utf-8").strip()
            if not content:
                log.debug(
                    "[CRASH] Found empty python_crash file %s — cleaning up",
                    py_crash_file.name,
                )
                continue
            log.warning("[CRASH] === Previous session crashed (Python exception)! ===")
            for line in content.splitlines():
                line = line.strip()
                if line:
                    log.warning("[CRASH] %s", line)
            # Build a concise summary from the key=value lines.
            fields: dict[str, str] = {}
            for line in content.splitlines():
                if "=" in line:
                    key, _, value = line.partition("=")
                    fields[key.strip()] = value.strip()
            exc_type = fields.get("exc_type", "UnknownException")
            exc_value = fields.get("exc_value", "")
            thread_name = fields.get("thread", "?")
            timestamp = fields.get("timestamp", "?")
            summary_parts.append(
                f"Python crash: {exc_type}: {exc_value} "
                f"(thread={thread_name}, at={timestamp}). "
                "Likely cause: an unhandled Python exception in the main "
                "thread or a daemon thread."
            )
        except Exception as exc:
            log.warning("[CRASH] Failed to read python_crash file %s: %s", py_crash_file.name, exc)
        finally:
            # Archive python_crash files in the same archive directory
            # so they're included in the diagnostic bundle.
            try:
                _archive_crash_file(py_crash_file, diagnostics_dir)
            except Exception as exc:
                log.debug("[CRASH] Failed to archive python_crash file %s: %s", py_crash_file.name, exc)

    # Sweep the config_dir root for any stale diagnostics files (e.g.
    # left behind by a failed move or by an older version that unlinked
    # instead of archiving).  Applies a 30-day mtime cutoff and a
    # keep-last-10 cap.
    with contextlib.suppress(Exception):
        _sweep_stale_diagnostics(diagnostics_dir)

    if not summary_parts:
        return None

    summary = "\n".join(summary_parts)
    log.warning("[CRASH] Crash summary for user notification:\n%s", summary)
    return summary
