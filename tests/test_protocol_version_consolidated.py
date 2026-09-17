"""``PROTOCOL_VERSION`` is consolidated into a single shared module.

Previously the IPC protocol version was duplicated as two local
literals in ``sidecar_ws.py`` and ``ipc/transport_tcp.py`` (TCP
transport deleted). The canonical literal now lives in
:mod:`voice_typer.server.ipc.protocol_version` and is imported by the
WS transport so the value is shared by construction.

These tests pin the consolidation: the shared module is the single
source of truth, and the value itself is still ``1`` (DO NOT bump
without a cross-language parity update: see
``tests/test_ipc_protocol_cross_language_parity.py`` and AGENTS.md E12).
"""

from __future__ import annotations

from voice_typer.server.ipc import protocol_version as protocol_version_module
from voice_typer.server.ipc.protocol_version import PROTOCOL_VERSION
from voice_typer.server.sidecar_ws import PROTOCOL_VERSION as WS_PROTOCOL_VERSION


def test_shared_module_exposes_protocol_version_one() -> None:
    """The canonical value in :mod:`protocol_version` is ``1``.

    Bumping requires a coordinated cross-language update, Rust
    ``EXPECTED_PROTOCOL_VERSION``, TypeScript ``IPC_PROTOCOL_VERSION``,
    and this test. See ``tests/test_ipc_protocol_cross_language_parity.py``.
    """
    assert PROTOCOL_VERSION == 1, (
        "PROTOCOL_VERSION must be 1. Bumping requires a coordinated "
        "cross-language update (Rust EXPECTED_PROTOCOL_VERSION, "
        "TypeScript IPC_PROTOCOL_VERSION). Got: " + repr(PROTOCOL_VERSION)
    )


def test_ws_transport_uses_shared_protocol_version() -> None:
    """``sidecar_ws.PROTOCOL_VERSION`` is the SAME object as the shared
    module's ``PROTOCOL_VERSION``. The WS transport MUST import from
    the shared module, not re-define a local literal.
    """
    assert WS_PROTOCOL_VERSION is PROTOCOL_VERSION, (
        "sidecar_ws.PROTOCOL_VERSION must be an import of protocol_version.PROTOCOL_VERSION, not a local literal."
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
    pattern = re.compile(
        r"^PROTOCOL_VERSION\s*:\s*int\s*=\s*\d+\s*$",
        re.MULTILINE,
    )
    matches = pattern.findall(source_text)
    assert len(matches) == 1, (
        f"Expected exactly one ``PROTOCOL_VERSION: int = <int>`` "
        f"definition in {source_path}, found {len(matches)}. "
        "The shared module must be the SINGLE source of truth."
    )


if __name__ == "__main__":
    import pytest

    pytest.main([__file__, "-v"])
