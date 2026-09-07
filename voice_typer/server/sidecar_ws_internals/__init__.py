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
keeps the entrypoint (``run``), the connection orchestrators
(``_handle_connection`` / ``_handle_connection_inner``) and
``_is_graceful_loop_stop``, plus the module-level constants whose
source greps read the canonical file, and re-exports every symbol
defined here so existing import paths AND monkeypatch targets
(``monkeypatch.setattr(sidecar_ws, "...")``) keep working unchanged.

Submodules:

- :mod:`voice_typer.server.sidecar_ws_internals.connection` —
  per-connection helpers: duplicate-auth invariant, connection
  semaphore, browser-origin rejection, drop-oldest enqueue, ready
  event, event-bus subscriber, initial state snapshot.
- :mod:`voice_typer.server.sidecar_ws_internals.dispatch` — the WS
  dispatch factory (``_make_dispatch``): per-frame rate-limit gate,
  cooperative-shutdown gates, dedicated dispatch thread pool, and
  the in-flight drain coordination the shutdown controller waits on.
- :mod:`voice_typer.server.sidecar_ws_internals.encode_pool` — the WS
  frame-encode ThreadPoolExecutor: module-level singleton cache, lazy
  accessor, and shutdown/drain helper.
- :mod:`voice_typer.server.sidecar_ws_internals.graceful_shutdown` —
  graceful WS shutdown: close-all-connections coroutine + the
  ``_attach_ws_graceful_shutdown`` server hook installer
  (``ws_graceful_shutdown`` + ``server.stop`` wrapper).
- :mod:`voice_typer.server.sidecar_ws_internals.handshake` — the
  one-shot bearer-token auth handshake (``_authenticate``); resolves
  the auth-read deadline from the canonical module's
  ``_AUTH_TIMEOUT_SECONDS`` alias at call time.
- :mod:`voice_typer.server.sidecar_ws_internals.outbound` — the
  outbound frame path: single-pass encode (``_encode_ws_frame``),
  size-capped + send-timeout-protected send (``_safe_send``), the
  per-connection writer task (``_start_writer``), the 1 MiB frame
  cap and the send timeout constants.
- :mod:`voice_typer.server.sidecar_ws_internals.read_loop` — the
  inbound read/dispatch loop (``_read_loop`` +
  ``_dispatch_and_respond``) with the heartbeat fast-path and its
  per-connection sliding-window rate-cap constants.
- :mod:`voice_typer.server.sidecar_ws_internals.stdout_banner` — the
  stdout handshake: ``_emit_server_started`` (the ``server_started``
  JSON banner) + ``_force_line_buffered_stdout`` (line buffering for
  it).

Nothing in this package is part of the public API — callers should
always import from :mod:`voice_typer.server.sidecar_ws`.
"""
