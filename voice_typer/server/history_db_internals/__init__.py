"""Internal helpers for :mod:`voice_typer.server.history_db`.

This package exists to split the once-monolithic ``history_db.py`` into
focused submodules. The public API (``HistoryDB`` class, ``HistoryDBError``)
still lives in :mod:`voice_typer.server.history_db`; the modules here are
free-function helpers called by the public class via thin delegating
methods.

Nothing in this package is part of the public API — callers should always
import from :mod:`voice_typer.server.history_db`.
"""
