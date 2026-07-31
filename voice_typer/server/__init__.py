"""Voice Typer server package.

Contains the Python backend: app orchestrator, ASR engines, recording,
IPC server, tray, etc. The entry point is
`voice_typer.server.ipc_server:main` (declared in pyproject.toml as the
`voice-typer` console script).

(fix): previously this docstring referenced
`voice_typer.server.app:main`, but `app.py` does not define `main`.
"""
