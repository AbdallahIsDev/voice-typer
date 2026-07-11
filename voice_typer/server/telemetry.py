"""PROD-001: Minimal telemetry module with opt-in crash reporting.

On crash, writes a crash report to a local file in the config directory.
Does NOT auto-send anything — just collects locally. The config option
``telemetry_enabled`` (default False) controls whether crash reports
are written at all.

Crash reports include:
  - Timestamp
  - Exception type and message
  - Thread name
  - Python version
  - Platform info
  - Application version (if available)
"""

from __future__ import annotations

import logging
import platform
import sys
import threading
import traceback
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)

# Crash report subdirectory within the config directory
_CRASH_REPORTS_DIR = "crash_reports"

# Maximum number of crash reports to keep (oldest are pruned)
_MAX_CRASH_REPORTS = 10


def _crash_reports_dir() -> Path:
    """Return the path to the crash reports directory."""
    from voice_typer.server.config import _config_dir
    return _config_dir() / _CRASH_REPORTS_DIR


def write_crash_report(
    exc: BaseException,
    *,
    thread_name: str | None = None,
    telemetry_enabled: bool = False,
) -> Path | None:
    """Write a crash report to a local file.

    PROD-001: only writes if telemetry_enabled is True. The caller
    should read the config option and pass it here.

    Parameters
    ----------
    exc : BaseException
        The exception that caused the crash.
    thread_name : str, optional
        Name of the thread where the crash occurred.
    telemetry_enabled : bool
        Whether crash reporting is enabled (from config).

    Returns
    -------
    Path or None
        The path to the written crash report, or None if disabled.
    """
    if not telemetry_enabled:
        log.debug("[TELEMETRY] Crash report skipped (telemetry not enabled)")
        return None

    try:
        reports_dir = _crash_reports_dir()
        reports_dir.mkdir(parents=True, exist_ok=True)

        timestamp = datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        report_path = reports_dir / f"crash_{timestamp}.log"

        tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        thread = thread_name or threading.current_thread().name

        # Try to get app version
        version = "unknown"
        try:
            from importlib.metadata import version as get_version
            version = get_version("voice-typer")
        except Exception:
            pass

        lines = [
            "Voice Typer Crash Report",
            "========================",
            f"Timestamp: {timestamp}",
            f"Thread: {thread}",
            f"Python: {sys.version}",
            f"Platform: {platform.platform()}",
            f"Architecture: {platform.machine()}",
            f"App Version: {version}",
            "",
            f"Exception Type: {type(exc).__name__}",
            f"Exception Message: {str(exc)}",
            "",
            "Traceback:",
            "-----------",
            tb,
        ]

        report_path.write_text("\n".join(lines), encoding="utf-8")
        log.info("[TELEMETRY] Crash report written to %s", report_path)

        # Prune old reports
        _prune_old_reports(reports_dir)

        return report_path
    except Exception:
        log.debug("[TELEMETRY] Failed to write crash report", exc_info=True)
        return None


def _prune_old_reports(reports_dir: Path) -> None:
    """Remove oldest crash reports if count exceeds _MAX_CRASH_REPORTS."""
    try:
        reports = sorted(reports_dir.glob("crash_*.log"))
        while len(reports) > _MAX_CRASH_REPORTS:
            oldest = reports.pop(0)
            oldest.unlink(missing_ok=True)
    except Exception:
        log.debug("[TELEMETRY] Failed to prune old reports", exc_info=True)


def list_crash_reports() -> list[Path]:
    """List all crash report files, newest first."""
    try:
        reports_dir = _crash_reports_dir()
        if not reports_dir.exists():
            return []
        return sorted(reports_dir.glob("crash_*.log"), reverse=True)
    except Exception:
        return []


def read_crash_report(path: Path) -> str | None:
    """Read a crash report file."""
    try:
        return path.read_text(encoding="utf-8")
    except Exception:
        return None
