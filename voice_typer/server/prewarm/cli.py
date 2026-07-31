# SPLIT-4: extracted from the original ``prewarm.py`` god-module.
"""CLI entry point for ``python -m voice_typer.server.prewarm``.

Phase 4.5 /  — this module holds the argparse-based CLI:

- :func:`_parse_args` — build the ``argparse.ArgumentParser`` and parse
  ``sys.argv`` (or the supplied ``argv``).
- :func:`_print_status` — print the prewarm cache status as JSON and
  return an exit code (used by ``--status``).
- :func:`main` — the CLI entry point.  Dispatches to ``--status``,
  ``--run --background``, or :func:`run` (in :mod:`.pipeline`).

Patch-path compatibility
------------------------
Tests patch ``run``, ``_sentinel_path``, and ``_pid_file_path`` on the
package namespace.  ``main`` looks up ``run`` via ``_pkg.run()`` and
``_print_status`` looks up the path helpers via ``_pkg._sentinel_path()``
/ ``_pkg._pid_file_path()`` so the patches take effect.
"""

from __future__ import annotations

import argparse
import json
import logging

# Patch-path bridge: route lookups of cross-submodule helpers through
# the package namespace so test patches of the form
# ``monkeypatch.setattr(prewarm, "run", ...)`` /
# ``monkeypatch.setattr(prewarm, "_sentinel_path", ...)`` /
# ``monkeypatch.setattr(prewarm, "_pid_file_path", ...)``
# keep affecting production code defined here.
from voice_typer.server import prewarm as _pkg

# Constants that are NOT patched via the package namespace
# by any test exercising this module can be bound directly.
from .pipeline import DEFAULT_MIN_FREE_RAM_MB, EXIT_DISABLED, EXIT_IMPORT_FAILED, EXIT_OK
from .process_tracker import get_prewarm_status

log = logging.getLogger("voice_typer.server.prewarm")


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="voice_typer.server.prewarm",
        description="Prewarm the OS file cache for fast Voice Typer startup.",
    )
    p.add_argument(
        "--min-ram-mb",
        type=int,
        default=DEFAULT_MIN_FREE_RAM_MB,
        help=f"Skip prewarm if free RAM is below this (default {DEFAULT_MIN_FREE_RAM_MB}).",
    )
    p.add_argument(
        "--force",
        action="store_true",
        help="Skip config and RAM guards (run unconditionally).",
    )
    p.add_argument(
        "--delay",
        type=float,
        default=0.0,
        help=(
            "Sleep this many seconds before starting.  Used by the HKCU "
            "Run-key fallback (which has no native delay) to let login settle."
        ),
    )
    # Task 4: --status prints the prewarm cache state and exits without
    # running the warming pipeline. Pure CLI — no Electron, no IPC.
    # Useful for remote diagnostics, SSH sessions, or automation scripts.
    p.add_argument(
        "--status",
        action="store_true",
        help=(
            "Print the prewarm cache status (last run, cache ratio, "
            "Hot/Partial/Cold label, etc.) as JSON and exit. Does NOT "
            "run the warming pipeline."
        ),
    )
    # Task 3: --run is a discoverable alias for --force. Both bypass
    # the sentinel + RAM guards and run the warming pipeline inline.
    # --run --background spawns a detached subprocess (matching
    # spawn_background_prewarm) and exits immediately, printing the PID.
    p.add_argument(
        "--run",
        action="store_true",
        help=(
            "Run prewarm unconditionally (alias for --force). Combine with --background to spawn a detached subprocess."
        ),
    )
    p.add_argument(
        "--background",
        action="store_true",
        help=(
            "With --run: spawn prewarm as a detached background subprocess "
            "and exit immediately (prints the spawned PID). Without --run: "
            "no effect. Useful for automation scripts that don't want to "
            "block on the ~50s warming pipeline."
        ),
    )
    p.add_argument(
        "--trigger",
        choices=["boot", "logon", "manual"],
        default="manual",
        help=(
            "Why this prewarm run was started. Logged so operators can "
            "verify which trigger fired (boot, logon, or manual). "
            "Default: manual (CLI / IPC button)."
        ),
    )
    return p.parse_args(argv)


def _print_status() -> int:
    """Task 4: print the prewarm cache status as JSON and return exit code.

    Calls ``get_prewarm_status()`` and prints the result as a JSON blob
    (with an added ``sentinel_path`` field for diagnostics). Exits with
    code 0 on success, 1 if the status probe raised.
    """
    try:
        status = get_prewarm_status()
        # Add the sentinel path for diagnostics (useful for support
        # engineers to verify the sentinel file location).
        status["sentinel_path"] = str(_pkg._sentinel_path())
        # Add the PID file path too (helps diagnose stale-PID issues).
        status["pid_file_path"] = str(_pkg._pid_file_path())
        print(json.dumps(status, indent=2, default=str))
        return 0
    except Exception as exc:
        log.error("[PREWARM] --status failed: %s", exc, exc_info=True)
        # Print a minimal error JSON so scripts can parse it.
        print(
            json.dumps(
                {
                    "error": str(exc),
                    "last_run": None,
                    "elapsed_s": None,
                    "cache_ratio": 0.0,
                    "cache_label": "unknown",
                    "cached_bytes": 0,
                    "total_bytes": 0,
                    "prewarm_running": False,
                    "sentinel_path": str(_pkg._sentinel_path()),
                    "pid_file_path": str(_pkg._pid_file_path()),
                },
                indent=2,
            )
        )
        return 1


def main() -> int:
    args = _parse_args()
    # Task 4: --status short-circuits before any warming work.
    if args.status:
        return _print_status()

    # Task 3: --run is an alias for --force. --background spawns a
    # detached subprocess and exits immediately (for automation scripts).
    # --background without --run is a no-op (we warn and exit).
    if args.background and not (args.run or args.force):
        log.warning("[PREWARM] --background requires --run or --force — ignoring")
        return EXIT_DISABLED

    if args.run or args.force:
        # --run and --force are equivalent — both bypass the guards.
        if args.background:
            # Task 3: spawn a detached subprocess and exit immediately.
            # Uses spawn_background_prewarm() for consistent detachment
            # behavior (CREATE_NO_WINDOW on Windows, start_new_session
            # on POSIX). Prints the spawned PID so scripts can track it.
            # Routed via _pkg so tests that patch
            # ``prewarm.spawn_background_prewarm`` see the patched value.
            pid = _pkg.spawn_background_prewarm(force=True, trigger=args.trigger)
            if pid is not None:
                print(f"prewarm spawned in background (pid={pid})")
                return EXIT_OK
            log.error("[PREWARM] --run --background: failed to spawn subprocess")
            return EXIT_IMPORT_FAILED  # no better code for "spawn failed"
        # --run without --background: run inline (same as --force).
        return _pkg.run(
            min_ram_mb=args.min_ram_mb,
            force=True,
            delay=args.delay,
            trigger=args.trigger,
        )

    return _pkg.run(
        min_ram_mb=args.min_ram_mb,
        force=args.force,
        delay=args.delay,
        trigger=args.trigger,
    )
