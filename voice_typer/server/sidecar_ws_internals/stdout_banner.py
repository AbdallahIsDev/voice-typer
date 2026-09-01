"""Stdout stream configuration for the sidecar handshake.

Extracted verbatim from :mod:`voice_typer.server.sidecar_ws`
(``_force_line_buffered_stdout``); the canonical module re-exports the
name so ``monkeypatch.setattr(sidecar_ws, "_force_line_buffered_stdout", ...)``
(the mig15/mig16/mig17 ws_hmac suites patch it to protect ``capsys``)
keeps working — the observer, ``run()``, stays in the canonical module
and resolves the name from the canonical module's globals at call time.

The sibling ``_emit_server_started`` stays in the canonical module:
``tests/tauri/mig15/test_externalbin_spawn_windows.py``,
``tests/tauri/mig16/test_externalbin_spawn_macos.py`` and
``tests/tauri/mig17/test_externalbin_spawn_linux.py`` pin the literal
string ``"def _emit_server_started"`` against the sidecar_ws.py FILE
text, so that function cannot move.
"""

from __future__ import annotations

import contextlib
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
