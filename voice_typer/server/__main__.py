"""DEAD-004: enable `python -m voice_typer.server`.

Re-exports the main entry point from `voice_typer.server.ipc_server` so
``python -m voice_typer.server`` works the same as ``python -m voice_typer``
(both delegate to the IPC server's ``main()``).

ERR-IPC-001 (fix): previously imported `main` from `voice_typer.server.app`,
but `app.py` does NOT define `main` (it was moved to `ipc_server.py:main`
in the Round 6 BUILD-002 refactor). Both startup paths (`voice-typer`
console script and `python -m voice_typer.server`) were broken.
"""

from voice_typer.server.ipc_server import main

if __name__ == "__main__":
    main()
