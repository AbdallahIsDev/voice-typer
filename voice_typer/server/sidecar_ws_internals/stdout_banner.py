"""Stdout handshake: the ``server_started`` banner + stream configuration.

Extracted verbatim from :mod:`voice_typer.server.sidecar_ws`
(``_emit_server_started`` + ``_force_line_buffered_stdout``); the
canonical module re-exports both names so the direct-call surface
(``sidecar_ws._emit_server_started(port, protocol)`` — the mig15-17
ws_hmac / unit suites, tests/test_app_sidecar_protocol.py,
tests/tauri/mig19/test_phase4_validation.py) and the monkeypatch
seams keep working:

- ``_force_line_buffered_stdout`` is PATCHED by the mig15/mig16/mig17
  ws_hmac suites (``monkeypatch.setattr(sw, ...)``, protecting
  ``capsys``); the observer, ``run()``, stays in the canonical module
  and resolves the name from the canonical module's globals at call
  time — the re-export binding is exactly what it replaces.
- ``_emit_server_started`` is OWNED by this module. The mig15/16/17
  externalbin_spawn suites pin the literal string
  ``"def _emit_server_started"`` + the payload-shape greps against
  the canonical file text CONCATENATED with this leaf (their
  ``sidecar_ws_source`` fixture reads both since the split); the
  ``getsockname`` / bind-address greps keep reading the canonical
  file (``run()`` stays there). The canonical ``run()`` resolves the
  emit through the ``_stdout_banner_mod`` module-object read at call
  time (C-ARCH-2 canonical form) and always passes
  ``PROTOCOL_VERSION``.
"""

from __future__ import annotations

import contextlib
import json
import sys


def _force_line_buffered_stdout() -> None:
    """Force stdout to line buffering (ADR-0020 §1 Phase-0 blocker).

    When the Tauri host pipes the sidecar's stdout, CPython switches
    to block buffering, so the ``server_started`` JSON is held in
    the buffer and the host hangs forever waiting. ``reconfigure``
    flips the stream back to line buffering so each ``\\n`` flushes.

    Python 3.7+ supports ``sys.stdout.reconfigure``; we guard for
    older interpreters (the project floor is 3.10 per pyproject.toml
    so this is always available, but the guard is defensive).
    """
    try:
        sys.stdout.reconfigure(line_buffering=True)  # type: ignore[attr-defined, union-attr]
    except (AttributeError, ValueError):
        # Fallback: reopen stdout with buffering=1 (line-buffered).
        # This loses the original fd's write-only-on-flush semantics
        # but is the standard pattern for unbuffered stdio.
        with contextlib.suppress(Exception):
            sys.stdout = open(  # noqa: SIM115 - intentional reopen
                sys.stdout.fileno(),
                "w",
                buffering=1,
                encoding="utf-8",
                closefd=False,
            )


def _emit_server_started(port: int, protocol: int | None = None) -> None:
    """Write the one structured stdout line the host is parsing for.

    Per ADR-0020 §1, this is the ONLY thing that ever goes to stdout
    from the sidecar. Every other log goes to stderr / the rotating
    file log. The host blocks reading stdout until it parses this
    JSON, then opens a WS client to ws://127.0.0.1:<port>.

    when ``protocol`` is not ``None``, the payload
    additionally includes ``"protocol": <int>`` so the Rust host can
    detect version skew at handshake time. The Rust host's
    ``EXPECTED_PROTOCOL_VERSION`` constant in
    ``src-tauri/src/sidecar/ws.rs`` MUST match
    :data:`PROTOCOL_VERSION`; on mismatch, the host logs a clear
    ``protocol_mismatch`` error and refuses to spawn. Callers in the
    canonical module always pass :data:`PROTOCOL_VERSION`; the ``None`` default
    is preserved for backward compatibility with pre-negotiation tests
    that assert the exact two-field payload shape and with any
    hypothetical external caller of this helper (none exist in the
    codebase today, but the default keeps the function safe to call
    without forcing the caller to know the current protocol integer).
    """
    if protocol is not None:
        print(
            json.dumps({"event": "server_started", "port": int(port), "protocol": int(protocol)}),
            flush=True,
        )
    else:
        print(json.dumps({"event": "server_started", "port": int(port)}), flush=True)
