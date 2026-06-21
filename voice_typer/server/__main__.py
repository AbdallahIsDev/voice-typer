"""DEAD-004: enable `python -m voice_typer.server`.

Re-exports the main entry point from `voice_typer.server.app` so
``python -m voice_typer.server`` works the same as ``python -m voice_typer``
(both delegate to the IPC server's ``main()``).
"""

from voice_typer.server.app import main


if __name__ == "__main__":
    main()
