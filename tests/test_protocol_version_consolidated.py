"""``PROTOCOL_VERSION`` is consolidated into a single shared module.

Previously the IPC protocol version was duplicated as two local
literals:

- ``voice_typer/server/sidecar_ws.py:PROTOCOL_VERSION: int = 1`` (WS
  transport — advisory-only mismatch handling).
- ``voice_typer/server/ipc/transport_tcp.py:IPC_PROTOCOL_VERSION: int = 1``
  (TCP transport — strict mismatch handling with a structured
  ``server.protocol_version_mismatch`` error envelope).

The two literals could silently drift, leaving the WS and TCP auth
paths with different protocol expectations. This consolidation
moves the canonical literal into
:mod:`voice_typer.server.ipc.protocol_version` and imports it from both
transports so the value is shared by construction.

These tests pin the consolidation: the shared module is the single
source of truth, both transports reference the SAME Python object, and
the value itself is still ``1`` (DO NOT bump without a cross-language
parity update — see ``tests/test_ipc_protocol_cross_language_parity.py``
and AGENTS.md E12).
"""

from __future__ import annotations

from voice_typer.server.ipc import protocol_version as protocol_version_module
from voice_typer.server.ipc.protocol_version import PROTOCOL_VERSION
from voice_typer.server.ipc.transport_tcp import (
    IPC_PROTOCOL_VERSION,
    PROTOCOL_VERSION as TCP_PROTOCOL_VERSION,
)
from voice_typer.server.sidecar_ws import PROTOCOL_VERSION as WS_PROTOCOL_VERSION


def test_shared_module_exposes_protocol_version_one() -> None:
    """The canonical value in :mod:`protocol_version` is ``1``.

    Bumping requires a coordinated cross-language update — Rust
    ``EXPECTED_PROTOCOL_VERSION``, TypeScript ``IPC_PROTOCOL_VERSION``,
    and the parity test. See the docstring in
    :mod:`voice_typer.server.ipc.protocol_version` and
    ``tests/test_app_sidecar_protocol.py`` for the cross-language pin.
    """
    assert PROTOCOL_VERSION == 1, (
        "PROTOCOL_VERSION must remain 1 until a cross-language parity "
        "bump is coordinated (Rust EXPECTED_PROTOCOL_VERSION, TS "
        "IPC_PROTOCOL_VERSION). Got: " + repr(PROTOCOL_VERSION)
    )
    assert isinstance(PROTOCOL_VERSION, int)
    assert PROTOCOL_VERSION > 0


def test_shared_module_attribute_matches_import() -> None:
    """``protocol_version.PROTOCOL_VERSION`` is the same object as the
    top-level import in this test (sanity check on the import path)."""
    assert protocol_version_module.PROTOCOL_VERSION is PROTOCOL_VERSION


def test_ws_transport_imports_shared_protocol_version() -> None:
    """``sidecar_ws.PROTOCOL_VERSION`` is the SAME object as the shared
    module's ``PROTOCOL_VERSION`` — the local literal was replaced with
    an import consolidation."""
    assert WS_PROTOCOL_VERSION is PROTOCOL_VERSION, (
        "sidecar_ws.PROTOCOL_VERSION must be imported from "
        "voice_typer.server.ipc.protocol_version consolidation. "
        "Got a different object — the WS transport may have re-introduced "
        "a local literal."
    )


def test_tcp_transport_imports_shared_protocol_version() -> None:
    """``transport_tcp.PROTOCOL_VERSION`` is the SAME object as the
    shared module's ``PROTOCOL_VERSION`` — the local literal was
    replaced with an import consolidation."""
    assert TCP_PROTOCOL_VERSION is PROTOCOL_VERSION, (
        "transport_tcp.PROTOCOL_VERSION must be imported from "
        "voice_typer.server.ipc.protocol_version consolidation. "
        "Got a different object — the TCP transport may have re-introduced "
        "a local literal."
    )


def test_tcp_alias_ipc_protocol_version_is_shared_protocol_version() -> None:
    """``transport_tcp.IPC_PROTOCOL_VERSION`` is the SAME object as the
    shared module's ``PROTOCOL_VERSION``. The alias is kept for
    backward-compat with code/tests that import ``IPC_PROTOCOL_VERSION``
    directly; the alias MUST be a re-binding of the imported value
    (``IPC_PROTOCOL_VERSION = PROTOCOL_VERSION``), not a fresh literal.
    """
    assert IPC_PROTOCOL_VERSION is PROTOCOL_VERSION, (
        "transport_tcp.IPC_PROTOCOL_VERSION must be a re-binding of "
        "the imported PROTOCOL_VERSION . Got a different object — "
        "the alias may have been re-defined as a local literal."
    )


def test_ws_and_tcp_transport_share_one_protocol_version_object() -> None:
    """Belt-and-suspenders: the WS transport's ``PROTOCOL_VERSION`` and
    the TCP transport's ``PROTOCOL_VERSION`` are the SAME Python object.
    This is the core invariant — both transports must agree by
    construction, not by coincidence.
    """
    assert WS_PROTOCOL_VERSION is TCP_PROTOCOL_VERSION, (
        "sidecar_ws.PROTOCOL_VERSION and transport_tcp.PROTOCOL_VERSION "
        "must be the same Python object (both imported from "
        "voice_typer.server.ipc.protocol_version). Got different objects."
    )


def test_ws_and_tcp_alias_share_one_protocol_version_object() -> None:
    """The WS transport's ``PROTOCOL_VERSION`` and the TCP transport's
    ``IPC_PROTOCOL_VERSION`` (legacy alias) are the SAME Python object.
    Verifies the alias doesn't accidentally re-bind to a fresh literal.
    """
    assert WS_PROTOCOL_VERSION is IPC_PROTOCOL_VERSION, (
        "sidecar_ws.PROTOCOL_VERSION and transport_tcp.IPC_PROTOCOL_VERSION "
        "must be the same Python object (both reference the shared "
        "voice_typer.server.ipc.protocol_version.PROTOCOL_VERSION)."
    )


def test_shared_module_source_contains_single_literal_definition() -> None:
    """The shared module's source text contains exactly ONE definition
    of ``PROTOCOL_VERSION`` (the canonical literal). Catches a future
    regression where a second definition is accidentally added.
    """
    import re
    from pathlib import Path

    source_path = Path(protocol_version_module.__file__)
    source_text = source_path.read_text(encoding="utf-8")
    # Match a top-level ``PROTOCOL_VERSION: int = <int>`` line. The
    # pattern intentionally requires a literal int on the RHS so an
    # accidental re-binding (e.g. ``PROTOCOL_VERSION = some_other_int``)
    # is detected.
    pattern = re.compile(
        r"^PROTOCOL_VERSION\s*:\s*int\s*=\s*\d+\s*$",
        re.MULTILINE,
    )
    matches = pattern.findall(source_text)
    assert len(matches) == 1, (
        f"Expected exactly one ``PROTOCOL_VERSION: int = <int>`` "
        f"definition in {source_path}, found {len(matches)}. "
        "The shared module must be the SINGLE source of truth ."
    )


if __name__ == "__main__":
    import pytest

    pytest.main([__file__, "-v"])
