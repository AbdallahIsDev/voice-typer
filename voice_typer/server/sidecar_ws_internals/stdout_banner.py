"""time (C-ARCH-2 canonical form) and always passes
Stdout handshake: the ``server_started`` banner + stream configuration.
(``_emit_server_started`` + ``_force_line_buffered_stdout``); the
(``sidecar_ws._emit_server_started(port, protocol)``, the mig15-17
- ``_force_line_buffered_stdout`` is PATCHED by the mig15/mig16/mig17
  ws_hmac suites (``monkeypatch.setattr(sw, ...)``, protecting
  ``capsys``); the observer, ``run()``, stays in the canonical module
- ``_emit_server_started`` is OWNED by this module. The mig15/16/17
  ``"def _emit_server_started"`` + the payload-shape greps against
  ``sidecar_ws_source`` fixture reads both since the split); the
  ``getsockname`` / bind-address greps keep reading the canonical
  file (``run()`` stays there). The canonical ``run()`` resolves the
  emit through the ``_stdout_banner_mod`` module-object read at call
  ``PROTOCOL_VERSION``.
"""

from __future__ import annotations

import contextlib
import json
import sys


def _force_line_buffered_stdout() -> None:
    """so this is always available, but the guard is defensive)."""
    try:
        sys.stdout.reconfigure(line_buffering=True)  # type: ignore[attr-defined, union-attr]
    except (AttributeError, ValueError):
        # Fallback: reopen stdout with buffering=1 (line-buffered).
        with contextlib.suppress(Exception):
            sys.stdout = open(  # noqa: SIM115 - intentional reopen
                sys.stdout.fileno(),
                "w",
                buffering=1,
                encoding="utf-8",
                closefd=False,
            )


def _emit_server_started(port: int, protocol: int | None = None) -> None:
    """Write the one structured stdout line the host is parsing for."""
    if protocol is not None:
        print(
            json.dumps({"event": "server_started", "port": int(port), "protocol": int(protocol)}),
            flush=True,
        )
    else:
        print(json.dumps({"event": "server_started", "port": int(port)}), flush=True)
