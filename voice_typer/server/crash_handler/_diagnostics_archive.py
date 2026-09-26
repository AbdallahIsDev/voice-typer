"""Diagnostics archive helpers for crash bundles."""

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
    _CODE_TO_INFO,
    _CODE_TO_USER_SUMMARY,
    _CRASH_DIAGNOSTICS_DIR,
    _HEADER_MAX_MODULES,
    _LEGACY_CRASH_DIAGNOSTICS_DIR,
    _MAX_ACTIVE_FILES,
    _MAX_AGE_SECONDS,
    _REPORTED_SIDECAR_SUFFIX,
)

log = logging.getLogger(__name__)


def _compute_crash_header() -> bytes:
    """Build the static header block for ``crash_diagnostics.<PID>.txt``.

    Returns
    """
    lines: list[str] = ["=== lausu CRASH DIAGNOSTICS HEADER ==="]
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
    # on Windows, ``sys.getwindowsversion()`` returns a tuple
    _get_win_ver = getattr(sys, "getwindowsversion", None)
    if _get_win_ver is not None:
        try:
            win_ver = _get_win_ver()
            lines.append(f"Windows version: {win_ver}")
        except Exception:
            lines.append("Windows version: <unknown>")
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
        # ALWAYS include the project's own top-level package
        if "voice_typer" in sys.modules and "voice_typer" not in seen:
            top_level.append("voice_typer")
            seen.add("voice_typer")
        total = len(sys.modules)
        lines.append(f"Loaded modules (total={total}, shown={len(top_level)}):")
        for m in top_level:
            lines.append(f"  {m}")
    except Exception:
        lines.append("Loaded modules: <unknown>")
    # reproduction hint so the user / support engineer knows
    lines.append(
        "Reproduction hint: run `python scripts/diagnostics.py export` "
        "to collect a full diagnostic bundle for a bug report."
    )
    lines.append("=== END HEADER ===")
    return ("\r\n".join(lines) + "\r\n").encode("utf-8", errors="replace")


def set_crash_handler_config_dir(config_dir: Path) -> None:
    """Cache the config directory path for the VEH callback."""
    from voice_typer.server import crash_handler as _ch

    try:
        resolved = Path(config_dir).resolve()
        _ch._PID = os.getpid()
        # write the VEH crash file DIRECTLY into the
        archive_dir = resolved / _CRASH_DIAGNOSTICS_DIR
        # O4: migrate a legacy ``crash_diagnostics_archive/`` dir BEFORE
        _migrate_legacy_archive_dir(resolved)
        # Pre-create the archive dir so the VEH callback (which cannot
        archive_ready = False
        _mkdir_exc: Exception | None = None
        try:
            archive_dir.mkdir(parents=True, exist_ok=True)
            if sys.platform != "win32":
                with contextlib.suppress(OSError):
                    os.chmod(archive_dir, 0o700)
        except Exception as exc:
            _mkdir_exc = exc
        with contextlib.suppress(Exception):
            archive_ready = archive_dir.is_dir()
        if archive_ready:
            _crash_target = archive_dir
        else:
            _suffix = f" ({_mkdir_exc})" if _mkdir_exc is not None else ""
            log.warning(
                "[CRASH] Could not pre-create crash diagnostics archive dir %s%s, falling back to the config root (%s)",
                archive_dir,
                _suffix,
                resolved,
            )
            _crash_target = resolved
        # Use os.path.join instead of a hardcoded backslash so the
        _ch._crash_file_path = os.path.join(str(_crash_target), f"crash_diagnostics.{_ch._PID}.txt") + "\0"
        _ch._python_crash_dir = resolved
        # Reset the rate-limit flag so a fresh process (or a re-init
        _ch._crash_written = False
        # Pre-compute the header once at config-dir cache time so the
        with contextlib.suppress(Exception):
            _ch._crash_header_bytes = _compute_crash_header()
        # refresh the cached ASR backend at config-dir cache
        with contextlib.suppress(Exception):
            from voice_typer.server.crash_handler._python_excepthook import (
                _refresh_cached_asr_backend,
            )

            _refresh_cached_asr_backend()
        # Install the in-memory log ring buffer (MemoryHandler) so the
        with contextlib.suppress(Exception):
            from voice_typer.server.crash_handler._memory_buffer import (
                install_memory_buffer,
            )

            install_memory_buffer(resolved)
    except Exception as exc:
        log.debug("[CRASH] Failed to cache config dir: %s", exc)
        _ch._crash_file_path = ""
        _ch._python_crash_dir = None
        _ch._crash_written = False
        _ch._crash_header_bytes = b""


def _migrate_legacy_archive_dir(config_dir: Path) -> None:
    """O4: rename legacy ``crash_diagnostics_archive/`` → ``crash_diagnostics/``."""
    legacy = Path(config_dir) / _LEGACY_CRASH_DIAGNOSTICS_DIR
    canonical = Path(config_dir) / _CRASH_DIAGNOSTICS_DIR
    if not legacy.is_dir():
        return
    if canonical.exists():
        return
    try:
        legacy.rename(canonical)
        log.info("[CRASH] Migrated legacy crash_diagnostics_archive/ -> crash_diagnostics/ (O4)")
    except OSError as exc:
        log.warning("[CRASH] Could not migrate legacy crash_diagnostics_archive/ -> crash_diagnostics/: %s", exc)


def _archive_crash_file(file_path: Path, config_dir: Path) -> Path | None:
    """Move a crash diagnostics / python_crash file to the archive.

    Returns the path to the archived file (which may differ from
    """
    archive_dir = Path(config_dir) / _CRASH_DIAGNOSTICS_DIR
    _migrate_legacy_archive_dir(config_dir)
    archive_dir.mkdir(parents=True, exist_ok=True)
    # Tighten archive dir perms on POSIX so crash records (which
    if sys.platform != "win32":
        with contextlib.suppress(OSError):
            os.chmod(archive_dir, 0o700)
    target = archive_dir / file_path.name
    # If the target already exists (e.g. a previous archive had the
    if target.exists():
        stem = file_path.stem
        suffix = file_path.suffix
        target = archive_dir / f"{stem}.{int(time.time() * 1000)}{suffix}"
    try:
        file_path.rename(target)
    except OSError as exc:
        # Cross-device rename or permission failure, best-effort.
        log.debug("[CRASH] Failed to archive %s: %s", file_path.name, exc)
        return None
    log.info("[CRASH] Archived diagnostics file: %s -> %s", file_path.name, target.name)
    _enforce_archive_retention(archive_dir)
    return target


def _mark_file_reported(file_path: Path) -> None:
    """Create a sidecar marker next to ``file_path``."""
    sidecar = file_path.with_name(file_path.name + _REPORTED_SIDECAR_SUFFIX)
    try:
        sidecar.touch(exist_ok=True)
    except OSError as exc:
        log.debug("[CRASH] Failed to create reported-marker for %s: %s", file_path.name, exc)


def _is_file_reported(file_path: Path) -> bool:
    """Check whether ``file_path`` has a reported-sidecar marker."""
    sidecar = file_path.with_name(file_path.name + _REPORTED_SIDECAR_SUFFIX)
    return sidecar.exists()


def _enforce_archive_retention(archive_dir: Path) -> None:
    """Keep only the last ``_ARCHIVE_RETENTION_KEEP`` crash-diagnostic files."""
    try:
        files = sorted(
            (p for p in archive_dir.glob("*.txt")),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
    except Exception:
        return
    for stale in files[_ARCHIVE_RETENTION_KEEP:]:
        with contextlib.suppress(Exception):
            stale.unlink()
        # Also delete the corresponding ``.reported`` sidecar marker
        sidecar = stale.with_name(stale.name + _REPORTED_SIDECAR_SUFFIX)
        with contextlib.suppress(Exception):
            sidecar.unlink()


def _sweep_stale_diagnostics(config_dir: Path) -> None:
    """Sweep stale crash diagnostics from the config_dir root."""
    try:
        diagnostics_dir = Path(config_dir).resolve()
        if not diagnostics_dir.is_dir():
            return
        files = list(diagnostics_dir.glob("crash_diagnostics.*.txt"))
        files.extend(diagnostics_dir.glob("python_crash.*.txt"))
        # Also walk the archive subdir. VEH writes crash files DIRECTLY
        archive_dir = diagnostics_dir / _CRASH_DIAGNOSTICS_DIR
        _migrate_legacy_archive_dir(diagnostics_dir)
        if archive_dir.is_dir():
            files.extend(archive_dir.glob("crash_diagnostics.*.txt"))
            files.extend(archive_dir.glob("python_crash.*.txt"))
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

    Returns ``None`` if no unreported crash diagnostics were found.
    """
    # The file pattern uses the process PID from the previous run, so we
    try:
        diagnostics_dir = Path(config_dir).resolve()
        if not diagnostics_dir.is_dir():
            return None
        # Collect all crash diagnostics files matching the pattern.
        crash_files = sorted(diagnostics_dir.glob("crash_diagnostics.*.txt"))
        python_crash_files = sorted(diagnostics_dir.glob("python_crash.*.txt"))
        # also scan the archive subdir. VEH now writes directly
        archive_dir = diagnostics_dir / _CRASH_DIAGNOSTICS_DIR
        _migrate_legacy_archive_dir(diagnostics_dir)
        if archive_dir.is_dir():
            # Sort archive files FIRST (root files have priority for the
            archived_crash_files = [
                f for f in sorted(archive_dir.glob("crash_diagnostics.*.txt")) if not _is_file_reported(f)
            ]
            archived_python_crash_files = [
                f for f in sorted(archive_dir.glob("python_crash.*.txt")) if not _is_file_reported(f)
            ]
        else:
            archived_crash_files = []
            archived_python_crash_files = []
    except Exception as exc:
        log.debug("[CRASH] Failed to scan for diagnostics files: %s", exc)
        return None

    # Short-circuit only when there are NO files to process in either
    if not crash_files and not python_crash_files and not archived_crash_files and not archived_python_crash_files:
        return None

    summary_parts: list[str] = []

    def _summarize_crash_file(crash_file: Path, *, already_archived: bool) -> None:
        """Surface one ``crash_diagnostics`` file's content + archive it."""
        try:
            # Read through ``_secure_read_text`` (POSIX
            from voice_typer.server.config import _secure_read_text

            try:
                content = _secure_read_text(crash_file).strip()
            except (OSError, ValueError) as secure_exc:
                # Secure read refused (symlink / reparse point / inode
                log.warning(
                    "[CRASH] Refusing to read diagnostics file %s (%s), treating as empty (symlink guard)",
                    crash_file.name,
                    secure_exc,
                )
                return
            if not content:
                log.debug(
                    "[CRASH] Found empty diagnostics file %s, cleaning up",
                    crash_file.name,
                )
                return
            # log the crash header at WARNING (1 line, visible
            log.warning("[CRASH] === Previous session crashed! Diagnostics follow ===")
            log.debug("[CRASH] Full crash diagnostics content for %s:\n%s", crash_file.name, content)
            # replace the 13-clause if/elif chain (which
            matched_summary: str | None = None
            for _code, (_name_bytes, _short) in _CODE_TO_INFO.items():
                # ``_name_bytes`` is the pre-encoded ASCII bytes the
                try:
                    name_text = _name_bytes.split(b":", 1)[0].decode("ascii", errors="replace")
                except Exception:
                    continue
                if name_text and name_text in content:
                    matched_summary = _CODE_TO_USER_SUMMARY.get(_code)
                    if matched_summary is not None:
                        summary_parts.append(matched_summary)
                        break
            if matched_summary is None:
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
            log.warning("[CRASH] Failed to read diagnostics file %s: %s", crash_file.name, exc)
        finally:
            # archive root-level files (existing behavior); for
            target: Path | None
            if already_archived:
                target = crash_file
            else:
                try:
                    target = _archive_crash_file(crash_file, diagnostics_dir)
                except Exception as exc:
                    log.debug(
                        "[CRASH] Failed to archive diagnostics file %s: %s",
                        crash_file.name,
                        exc,
                    )
                    target = None
            if target is not None:
                _mark_file_reported(target)

    # Process root-level files first (legacy location, pre-).
    for crash_file in crash_files:
        _summarize_crash_file(crash_file, already_archived=False)

    # then process archive-subdir files (new VEH write path).
    for crash_file in archived_crash_files:
        _summarize_crash_file(crash_file, already_archived=True)

    # Process python_crash marker files written by the Python-level
    def _summarize_python_crash(py_crash_file: Path, *, already_archived: bool) -> None:
        try:
            # Secure read (O_NOFOLLOW / reparse-point check /
            from voice_typer.server.config import _secure_read_text

            try:
                content = _secure_read_text(py_crash_file).strip()
            except (OSError, ValueError) as secure_exc:
                log.warning(
                    "[CRASH] Refusing to read python_crash file %s (%s), treating as empty (symlink guard)",
                    py_crash_file.name,
                    secure_exc,
                )
                return
            if not content:
                log.debug(
                    "[CRASH] Found empty python_crash file %s, cleaning up",
                    py_crash_file.name,
                )
                return
            # same demotion pattern as the VEH-crash path —
            log.warning("[CRASH] === Previous session crashed (Python exception)! ===")
            log.debug(
                "[CRASH] Full python_crash content for %s:\n%s",
                py_crash_file.name,
                content,
            )
            # Build a concise summary from the key=value lines.
            fields: dict[str, str] = {}
            for line in content.splitlines():
                if "=" in line:
                    key, _, value = line.partition("=")
                    fields[key.strip()] = value.strip()
            exc_type = fields.get("exc_type", "UnknownException")
            # drop ``exc_value`` from the user-facing summary
            thread_name = fields.get("thread", "?")
            timestamp = fields.get("timestamp", "?")
            summary_parts.append(
                f"Python crash: {exc_type} "
                f"(thread={thread_name}, at={timestamp}). "
                "Likely cause: an unhandled Python exception in the main "
                "thread or a daemon thread."
            )
        except Exception as exc:
            log.warning("[CRASH] Failed to read python_crash file %s: %s", py_crash_file.name, exc)
        finally:
            target: Path | None
            if already_archived:
                target = py_crash_file
            else:
                try:
                    target = _archive_crash_file(py_crash_file, diagnostics_dir)
                except Exception as exc:
                    log.debug(
                        "[CRASH] Failed to archive python_crash file %s: %s",
                        py_crash_file.name,
                        exc,
                    )
                    target = None
            if target is not None:
                _mark_file_reported(target)

    for py_crash_file in python_crash_files:
        _summarize_python_crash(py_crash_file, already_archived=False)
    for py_crash_file in archived_python_crash_files:
        _summarize_python_crash(py_crash_file, already_archived=True)

    # Sweep the config_dir root for any stale diagnostics files (e.g.
    with contextlib.suppress(Exception):
        _sweep_stale_diagnostics(diagnostics_dir)

    # Enforce the keep-last-N retention cap on the archive subdir
    with contextlib.suppress(Exception):
        if archive_dir.exists():
            _enforce_archive_retention(archive_dir)

    if not summary_parts:
        return None

    summary = "\n".join(summary_parts)
    # append a "Next steps" hint so an OPERATOR reading the log / the
    summary = summary + (
        "\nNext steps: run `python scripts/diagnostics.py export` to collect a full diagnostic bundle for a bug report."
    )
    # demote the summary log line from INFO to DEBUG so the
    log.debug("[CRASH] Crash summary (operator log only, not user-facing):\n%s", summary)
    return summary
