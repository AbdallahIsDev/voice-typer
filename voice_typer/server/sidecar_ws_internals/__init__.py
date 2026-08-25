"""Internal helpers for :mod:`voice_typer.server.sidecar_ws`.

This package exists to split the once-monolithic ``sidecar_ws.py``
(2081 LOC, 8+ concerns) into focused leaf modules without breaking the
canonical module's import surface. Deliberately a ``*_internals``
sibling package (mirroring the ``history_db.py`` /
``history_db_internals/`` precedent) rather than a ``sidecar_ws/``
package — converting ``sidecar_ws.py`` into a package would move the
file and break the ~14 test files that pin the literal
``voice_typer/server/sidecar_ws.py`` path (file-text reads, source
regexes, token-leak scans).

``voice_typer/server/sidecar_ws.py`` remains the CANONICAL module: it
keeps every module-level constant, the file-text-pinned functions
(``_safe_send``, ``_authenticate``, ``_make_dispatch``,
``_encode_ws_frame``, ``_emit_server_started``, ``_read_loop``,
``_start_writer``, ``_handle_connection_inner``, ``run``), and
re-exports every symbol defined here so existing import paths AND
monkeypatch targets (``monkeypatch.setattr(sidecar_ws, "...")``) keep
working unchanged.

Submodules:

- :mod:`voice_typer.server.sidecar_ws_internals.encode_pool` — the WS
  frame-encode ThreadPoolExecutor: module-level singleton cache, lazy
  accessor, and shutdown/drain helper.
- :mod:`voice_typer.server.sidecar_ws_internals.graceful_shutdown` —
  graceful WS shutdown: close-all-connections coroutine + the
  ``_attach_ws_graceful_shutdown`` server hook installer
  (``ws_graceful_shutdown`` + ``server.stop`` wrapper).
- :mod:`voice_typer.server.sidecar_ws_internals.stdout_banner` — stdout
  stream configuration (line buffering) for the sidecar handshake.
- :mod:`voice_typer.server.sidecar_ws_internals.connection` —
  per-connection helpers: duplicate-auth invariant, connection
  semaphore, browser-origin rejection, drop-oldest enqueue, ready
  event, event-bus subscriber, initial state snapshot.

Nothing in this package is part of the public API — callers should
always import from :mod:`voice_typer.server.sidecar_ws`.
"""
