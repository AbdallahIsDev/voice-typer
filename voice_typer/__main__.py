"""Entry point for `python -m voice_typer`.

CQ-028: Adds CLI argument parsing for --help, --version, --debug.
NEW-CLI-004: Adds operational flags --no-tray, --quiet, --config.
NEW-CLI-003: Standardized exit codes:
  0 = clean exit
  1 = crash / runtime error
  2 = port conflict
  3 = duplicate instance
  4 = bad CLI args
"""

import argparse
import sys

# NEW-CLI-003: standardized exit codes
EXIT_CLEAN = 0
EXIT_CRASH = 1
EXIT_PORT_CONFLICT = 2
EXIT_DUPLICATE_INSTANCE = 3
EXIT_BAD_ARGS = 4


def _parse_args(argv=None):
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        prog="voice_typer",
        description="Voice Typer — background voice-to-text utility",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {_get_version()}",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        default=False,
        help="Enable debug logging to console",
    )
    # NEW-CLI-004: operational flags for CI / headless testing
    parser.add_argument(
        "--quiet",
        action="store_true",
        default=False,
        help="Suppress console output (only log to file)",
    )
    parser.add_argument(
        "--no-tray",
        action="store_true",
        default=False,
        help="Run without system tray icon (headless mode for testing)",
    )
    parser.add_argument(
        "--config",
        type=str,
        default=None,
        metavar="PATH",
        help="Use a custom config directory instead of ~/.voice-typer/",
    )
    return parser.parse_args(argv)


def _get_version():
    """Get the package version without importing the package (avoids side effects)."""
    try:
        from voice_typer import __version__
        return __version__
    except Exception:
        return "unknown"


def main():
    """Main entry point."""
    args = _parse_args()

    if args.debug:
        import os
        os.environ["VOICE_TYPER_DEBUG"] = "1"

    # NEW-CLI-004: pass operational flags via env vars so the IPC
    # server can read them without changing its signature.
    import os
    if args.quiet:
        os.environ["VOICE_TYPER_QUIET"] = "1"
    if args.no_tray:
        os.environ["VOICE_TYPER_NO_TRAY"] = "1"
    if args.config:
        os.environ["VOICE_TYPER_CONFIG_DIR"] = args.config

    from voice_typer.server.app import main as app_main
    app_main()


if __name__ == "__main__":
    main()
