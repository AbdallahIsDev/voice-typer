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
    _CODE_TO_INFO,
    _CODE_TO_USER_SUMMARY,
    _CRASH_DIAGNOSTICS_ARCHIVE,
    _HEADER_MAX_MODULES,
    _MAX_ACTIVE_FILES,
    _MAX_AGE_SECONDS,
    _REPORTED_SIDECAR_SUFFIX,
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
          - Windows version (``sys.getwindowsversion()`` on Windows only —
            includes the OS build number, service pack, and suite mask in
            a single struct that the Windows kernel exposes directly, which
            is more precise than ``platform.release()`` for triaging SEH
            crashes tied to a specific Windows patch level)
          - Python version (``sys.version``)
          - Loaded-module snapshot (top-level package names from
            ``sys.modules``, capped to keep the file size reasonable)
          - Reproduction hint pointing the user at the diagnostics-export
            CLI so the support engineer can request a full bundle without
    a second round-trip ()

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
    # on Windows, ``sys.getwindowsversion()`` returns a tuple
    # namedtuple with (major, minor, build, platform, service_pack,
    # service_pack_major, service_pack_minor, suite_mask, product_type)
    # — this is the OS-reported build number that ``platform.release()``
    # does not surface. We capture it in a separate try/except so a
    # failure here (e.g. on Wine / ReactOS where the API behaves oddly)
    # does NOT suppress the ``OS:`` / ``OS build:`` lines above.
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
        # (``voice_typer``) in the snapshot, even if it falls beyond
        # the ``_HEADER_MAX_MODULES`` cap. The cap exists to bound
        # PII / install-fingerprint exposure (); the project's
        # own package name is the same across installs and is the
        # single most relevant entry for debugging a crash — without
        # it, a support engineer reading the header can't even
        # confirm the crash originated from this codebase. Allowed
        # to overshoot the cap by 1 (101 entries max) — a negligible
        # PII delta vs the 100-cap baseline.
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
    # how to capture the full diagnostic bundle (logs, config, crash
    # archive, OS / Python / app version snapshot) for a bug report
    # without a second round-trip.  Inline in the header (rather than
    # appended at crash-summary time) so the hint is present even when
    # the file is read directly off disk by an operator (e.g. via
    # ``cat``) without going through ``report_pending_crash``.
    lines.append(
        "Reproduction hint: run `python scripts/diagnostics.py export` "
        "to collect a full diagnostic bundle for a bug report."
    )
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
        # write the VEH crash file DIRECTLY into the
        # ``<config_dir>/crash_diagnostics_archive/`` subdir instead
        # of the config_dir root. Pre-fix, the file sat in the config_dir
        # root between the crash (T0) and the next startup (T1),
        # exposing a 500-module fingerprint (now capped to 100 — see
        # ``_HEADER_MAX_MODULES``) at the same path the user opens for
        # ``config.toml`` / ``voice-typer.log`` inspection. Writing
        # directly to the archive subdir bounds the exposure window to
        # ~0 (the file is created in its final archived location).
        # ``report_pending_crash`` now ALSO scans the archive subdir
        # (using a ``.reported`` sidecar marker to prevent re-reporting
        # on every startup) so the user-facing notification still fires.
        archive_dir = resolved / _CRASH_DIAGNOSTICS_ARCHIVE
        # Pre-create the archive dir so the VEH callback (which cannot
        # safely mkdir during heap corruption) can write directly to
        # ``<archive>/crash_diagnostics.<PID>.txt``. Best-effort — a
        # mkdir failure (e.g. read-only config_dir) is logged at debug
        # and the VEH callback's CreateFileW call will fail silently
        # (existing behavior for an unwritable path).
        with contextlib.suppress(Exception):
            archive_dir.mkdir(parents=True, exist_ok=True)
            if sys.platform != "win32":
                with contextlib.suppress(OSError):
                    os.chmod(archive_dir, 0o700)
        # Use os.path.join instead of a hardcoded backslash so the
        # cached path is correct on both Windows and POSIX (POSIX never
        # invokes the VEH callback, but tests still inspect the cached
        # path).  Preserve the trailing NUL terminator required by
        # CreateFileW.
        _ch._crash_file_path = os.path.join(str(archive_dir), f"crash_diagnostics.{_ch._PID}.txt") + "\0"
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
        # refresh the cached ASR backend at config-dir cache
        # time so the excepthook can read it without disk I/O on the
        # crashing thread. Best-effort — a refresh failure leaves the
        # cache untouched (the excepthook falls back to ``"<unknown>"``).
        with contextlib.suppress(Exception):
            from voice_typer.server.crash_handler._python_excepthook import (
                _refresh_cached_asr_backend,
            )

            _refresh_cached_asr_backend()
        # Install the in-memory log ring buffer (MemoryHandler) so the
        # VEH callback can flush the most-recent ~200 log records to
        # ``<config_dir>/voice-typer-crash-buffer.log`` after writing
        # the crash-diagnostics body. Best-effort — a failure here
        # leaves the buffer uninstalled (the VEH callback's
        # ``flush_memory_handler`` call is a no-op when the buffer is
        # missing).
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


def _archive_crash_file(file_path: Path, config_dir: Path) -> Path | None:
    """Move a crash diagnostics / python_crash file to the archive.

    The archive lives at ``<config_dir>/crash_diagnostics_archive/`` and
    is created with ``0o700`` perms on POSIX so the archived crash
    records (which may include exception addresses, thread IDs, etc.)
    are not world-readable.

    After moving, applies the retention policy (keep last
    ``_ARCHIVE_RETENTION_KEEP`` files in the archive, delete older).

    Returns the path to the archived file (which may differ from
    ``archive_dir / file_path.name`` if a name collision was resolved
    by appending a millisecond timestamp), or ``None`` if the move
    failed.
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
    try:
        file_path.rename(target)
    except OSError as exc:
        # Cross-device rename or permission failure — best-effort.
        log.debug("[CRASH] Failed to archive %s: %s", file_path.name, exc)
        return None
    log.info("[CRASH] Archived diagnostics file: %s -> %s", file_path.name, target.name)
    _enforce_archive_retention(archive_dir)
    return target


def _mark_file_reported(file_path: Path) -> None:
    """Create a sidecar marker next to ``file_path``.

    VEH-written crash files land DIRECTLY in the archive subdir
        (no longer in the config_dir root). ``report_pending_crash`` scans
        the archive subdir to surface them to the user — but without a
        marker, the same file would be re-surfaced on every startup.

        The marker is an empty file named ``<filename>.reported`` sitting
        next to the crash file. On the next scan, the sidecar's existence
        causes the file to be skipped (already-reported).

        Best-effort: a marker-creation failure (e.g. read-only archive
        dir) is logged at debug and swallowed — the worst case is the
        crash file gets re-reported on the next startup, which is annoying
        but not unsafe.
    """
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
    """Keep only the last ``_ARCHIVE_RETENTION_KEEP`` crash-diagnostic files.

    Files are sorted by mtime (newest first); older files beyond the
    retention cap are deleted.  All errors are suppressed (best-effort).

    Only ``*.txt`` files are counted toward the cap — ``.reported``
    sidecar markers (which have newer mtimes than their corresponding
    .txt files, since the sidecar is created by ``_mark_file_reported``
    AFTER the .txt is written) are NOT counted. Without this exclusion,
    retention would preferentially keep sidecars over .txt files
    (because sidecars are newer) and delete the .txt files, leaving
    orphan sidecars behind AND losing the crash record. When a .txt
    file is deleted, its corresponding ``.reported`` sidecar (if any)
    is also deleted so we don't leave orphan markers.
    """
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
        # (if any) so we don't leave an orphan marker pointing at a
        # deleted .txt file. Best-effort — a missing sidecar is fine.
        sidecar = stale.with_name(stale.name + _REPORTED_SIDECAR_SUFFIX)
        with contextlib.suppress(Exception):
            sidecar.unlink()


def _sweep_stale_diagnostics(config_dir: Path) -> None:
    """Sweep stale crash diagnostics from the config_dir root.

    After ``report_pending_crash`` moves all current files into the
    archive, the config_dir root should normally be empty of
    ``crash_diagnostics.*.txt`` / ``python_crash.*.txt`` files.  This
    sweep is a safety net for files that were left behind by an older
    version (pre-archiving) or by a failed move.

    The sweep ALSO walks the archive subdir (``crash_diagnostics_archive/``)
    because VEH now writes crash files directly there — a sweep that
    only walked the root would miss every post-fix crash file and the
    archive would grow unbounded across crashes. The 30-day mtime
    cutoff + keep-last-``_MAX_ACTIVE_FILES`` cap are applied uniformly
    to both locations so the archive cannot accumulate more than
    ``_MAX_ACTIVE_FILES`` stale files even if ``_enforce_archive_retention``
    was never invoked (e.g. an old version wrote directly into the
    archive without going through ``_archive_crash_file``).

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
        # Also walk the archive subdir — VEH writes crash files DIRECTLY
        # there, so a root-only sweep would miss every post-fix crash
        # file and the archive would grow unbounded across crashes.
        # The guard handles the first-run case where the archive subdir
        # doesn't exist yet.
        archive_dir = diagnostics_dir / _CRASH_DIAGNOSTICS_ARCHIVE
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

        Scans the config directory for:
          * ``crash_diagnostics.*.txt`` files written by the VEH callback
            when a previous process crashed silently (heap corruption,
            access violation, etc.).
          * ``python_crash.*.txt`` marker files written by
            ``_crash_excepthook`` when an unhandled Python exception
            terminated the previous process.

    VEH now writes crash files DIRECTLY to
        ``<config_dir>/crash_diagnostics_archive/`` (no longer to the
        config_dir root). This function scans BOTH the config_dir root
        (for legacy files left by a pre-fix version) AND the archive
        subdir (for new VEH-written files).

        If any are found:
    1. : logs a single-line WARNING header ("Previous session
             crashed! ...") so operators see the crash signal in the
             WARN-filtered production log; logs the full crash content at
             DEBUG (visible when ``VOICE_TYPER_DEBUG=1``); logs the
    human-readable summary at INFO. Pre- the full content
             was logged line-by-line at WARNING (50+ records per crash),
             drowning real warnings.
          2. Moves the file to ``<config_dir>/crash_diagnostics_archive/``
             instead of deleting it, so the diagnostic bundle can include it
             later. (Files already in the archive stay there.)
          3. Creates a ``<filename>.reported`` sidecar marker next to the
             archived file so the next startup doesn't re-surface it.
          4. Returns a human-readable summary for the caller to LOG /
             include in diagnostics. CRASH-NOTIFY: the caller no longer
             embeds this summary in the user-facing notification —
             technical crash details (including the "Next steps: run
             python …" hint) stay in the log / diagnostics surface only.

        After processing, applies the sweep (30-day mtime cutoff + keep last
        10) to the config_dir root as a safety net for files left behind by
        failed moves or older versions.

        Returns ``None`` if no unreported crash diagnostics were found.
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
        # also scan the archive subdir — VEH now writes directly
        # there, so legacy root-scanning alone misses all new crashes.
        archive_dir = diagnostics_dir / _CRASH_DIAGNOSTICS_ARCHIVE
        if archive_dir.is_dir():
            # Sort archive files FIRST (root files have priority for the
            # archive move — if a root file and an archive file have the
            # same PID-derived name, the archive move will disambiguate
            # via a millisecond timestamp suffix in ``_archive_crash_file``).
            # Skip files with a ``.reported`` sidecar marker — they were
            # already surfaced on a previous startup.
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
    # location. (We can't short-circuit on `not crash_files` alone
    # anymore because archive-subdir files might still need surfacing.)
    if not crash_files and not python_crash_files and not archived_crash_files and not archived_python_crash_files:
        return None

    summary_parts: list[str] = []

    def _summarize_crash_file(crash_file: Path, *, already_archived: bool) -> None:
        """Surface one ``crash_diagnostics`` file's content + archive it.

        if ``already_archived`` is True (file came from the
                archive subdir), don't try to archive it again — just create
                a ``.reported`` sidecar so the next scan skips it. If False
                (file came from the config_dir root), archive it (existing
                behavior) and create the sidecar next to the moved file.
        """
        try:
            # HU-9: read through ``_secure_read_text`` (POSIX
            # ``O_NOFOLLOW``, Windows reparse-point check, bounded
            # read) — same helper the recovery-file load path
            # (``crash_recovery.py``) uses — so a symlink planted at a
            # crash-file path can never exfiltrate an arbitrary file's
            # content into the log. Imported lazily (function scope) to
            # avoid a module-level circular import.
            from voice_typer.server.config import _secure_read_text

            try:
                content = _secure_read_text(crash_file).strip()
            except (OSError, ValueError) as secure_exc:
                # Secure read refused (symlink / reparse point / inode
                # changed mid-read / oversized file). Fail closed:
                # treat as an empty file — unverifiable content must
                # never be surfaced, logged, or summarized.
                log.warning(
                    "[CRASH] Refusing to read diagnostics file %s (%s) — "
                    "treating as empty (HU-9 symlink guard)",
                    crash_file.name,
                    secure_exc,
                )
                return
            if not content:
                log.debug(
                    "[CRASH] Found empty diagnostics file %s — cleaning up",
                    crash_file.name,
                )
                return
            # log the crash header at WARNING (1 line — visible
            # in the WARN-filtered production log so operators see that
            # a previous session crashed), then log the full crash
            # content at DEBUG (visible only when VOICE_TYPER_DEBUG=1).
            # Pre- the full content was logged line-by-line at
            # WARNING (50+ records per crash), polluting the WARN
            # filter and drowning real warnings.
            log.warning("[CRASH] === Previous session crashed! Diagnostics follow ===")
            log.debug("[CRASH] Full crash diagnostics content for %s:\n%s", crash_file.name, content)
            # replace the 13-clause if/elif chain (which
            # duplicated the code → message mapping that already lived
            # in ``_constants._CODE_TO_INFO`` / ``_CODE_TO_USER_SUMMARY``)
            # with a single table-driven lookup. Drift between the VEH
            # write-side and the report-side is now impossible: adding a
            # new code requires editing ONE module (``_constants``),
            # not three.
            #
            # The matching logic scans the crash file's CONTENT for the
            # friendly-name token (e.g. the ASCII text
            # ``STATUS_HEAP_CORRUPTION``) using the name-bytes from
            # ``_CODE_TO_INFO[code][0]``; on the FIRST match, it
            # appends the corresponding ``_CODE_TO_USER_SUMMARY[code]``
            # to the user-facing summary. Iteration order follows
            # ``_CODE_TO_INFO`` insertion order (Python 3.7+ dict
            # ordering), which mirrors the original if/elif precedence
            # (HEAP first, GUARD_PAGE last). Unknown codes fall through
            # to the ``code=0x`` extraction + generic-fallback path
            # (unchanged).
            #
            # Each message now includes possible causes: low memory
            # (RAM) and low disk space — the two most common triggers
            # for silent heap corruption / access violation crashes.
            matched_summary: str | None = None
            for _code, (_name_bytes, _short) in _CODE_TO_INFO.items():
                # ``_name_bytes`` is the pre-encoded ASCII bytes the
                # VEH callback writes (e.g.
                # ``b"STATUS_HEAP_CORRUPTION: the process heap..."``).
                # Match the bare status name (the part before the first
                # colon) so the summary fires even if the rest of the
                # line is truncated or the file is partially corrupted.
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
            # Path) so the log doesn't include the user's home
            # directory path.
            log.warning("[CRASH] Failed to read diagnostics file %s: %s", crash_file.name, exc)
        finally:
            # archive root-level files (existing behavior); for
            # archive-subdir files, just create the reported-sidecar
            # marker (they're already in the right place). Both paths
            # end with the file having a sidecar marker so the next
            # startup scan skips it.
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
    # excepthook.  These capture unhandled Python exceptions (e.g. in
    # daemon threads) that would otherwise only appear on stderr.
    def _summarize_python_crash(py_crash_file: Path, *, already_archived: bool) -> None:
        try:
            # HU-9: secure read (O_NOFOLLOW / reparse-point check /
            # bounded) — a symlink planted at a python_crash path must
            # not exfiltrate an arbitrary file's content into the log.
            # Fail closed: on refusal, treat as an empty file.
            from voice_typer.server.config import _secure_read_text

            try:
                content = _secure_read_text(py_crash_file).strip()
            except (OSError, ValueError) as secure_exc:
                log.warning(
                    "[CRASH] Refusing to read python_crash file %s (%s) — "
                    "treating as empty (HU-9 symlink guard)",
                    py_crash_file.name,
                    secure_exc,
                )
                return
            if not content:
                log.debug(
                    "[CRASH] Found empty python_crash file %s — cleaning up",
                    py_crash_file.name,
                )
                return
            # same demotion pattern as the VEH-crash path —
            # WARNING header (1 line) + DEBUG full content (visible only
            # when VOICE_TYPER_DEBUG=1). Pre- each line of the
            # python_crash marker was logged at WARNING, polluting the
            # WARN filter and drowning real warnings.
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
            # entirely. Pre-fix, the (redacted) ``exc_value`` was
            # interpolated into the summary string at INFO level, which
            # shipped dictated speech and any PII-shaped text that
            # slipped past the redactor into the diagnostic bundle.
            # The full (redacted) ``exc_value`` remains in the marker
            # file on disk for support engineers with disk access —
            # surfacing it in the operator-visible summary is the leak.
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
    # left behind by a failed move or by an older version that unlinked
    # instead of archiving).  Applies a 30-day mtime cutoff and a
    # keep-last-10 cap. The sweep ALSO walks the archive subdir (see
    # ``_sweep_stale_diagnostics``) so VEH-written files there are
    # bounded even when this function did not surface them.
    with contextlib.suppress(Exception):
        _sweep_stale_diagnostics(diagnostics_dir)

    # Enforce the keep-last-N retention cap on the archive subdir
    # explicitly. VEH writes crash files DIRECTLY into the archive
    # subdir, so for archive-subdir files ``_summarize_crash_file``
    # only creates a ``.reported`` sidecar (it does NOT call
    # ``_archive_crash_file``, which is where the retention cap was
    # previously enforced). Without this call the archive subdir would
    # grow unbounded — every crash adds a new ``crash_diagnostics.<PID>.txt``
    # and nothing deletes the old ones. The ``if archive_dir.exists():``
    # guard handles the first-run case where the subdir doesn't exist
    # yet (no crashes recorded).
    with contextlib.suppress(Exception):
        if archive_dir.exists():
            _enforce_archive_retention(archive_dir)

    if not summary_parts:
        return None

    summary = "\n".join(summary_parts)
    # append a "Next steps" hint so an OPERATOR reading the log / the
    # diagnostics bundle knows how to capture a full diagnostic
    # archive for a bug report. CRASH-NOTIFY: this summary (with the
    # developer CLI hint) is consumed by ``startup_sequence`` for
    # logging only — it is NEVER embedded in the user-facing
    # notification (which carries calm, non-technical copy and points
    # to Settings → Privacy → Diagnostics). Appended to the summary
    # (NOT the per-file ``summary_parts`` list) so the hint appears
    # EXACTLY ONCE even when multiple crash files were surfaced in the
    # same startup scan.
    summary = summary + (
        "\nNext steps: run `python scripts/diagnostics.py export` to collect a full diagnostic bundle for a bug report."
    )
    # demote the summary log line from INFO to DEBUG so the
    # reduced (exc_value-less) summary only ships in the bundle when
    # ``VOICE_TYPER_DEBUG=1``. Pre-fix the summary was logged at INFO,
    # which meant even the reduced (exc_type + thread + timestamp only)
    # summary landed in the default production log — visible to any
    # operator reading voice-typer.log. The summary is returned to
    # the caller for logging only (the user notification carries calm,
    # non-technical copy — see ``startup_sequence``), so the demotion
    # keeps the operator-visible signal in the rotating log without
    # leaking PII-shaped text into the default log level.
    log.debug("[CRASH] Crash summary (operator log only, not user-facing):\n%s", summary)
    return summary
