"""Entry point for `python -m voice_typer`.

CQ-028: Adds CLI argument parsing for --help, --version, --debug.
"""

import argparse
import sys


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

    from voice_typer.app import main as app_main
    app_main()


if __name__ == "__main__":
    main()
