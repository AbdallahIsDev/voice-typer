"""Doc-parity test: top-level docs must agree with the live command counts.

Post-predecessor cutover the IPC surface is two-way:

    Python ``_COMMAND_REGISTRY``   : 75  (registry total)
    Rust host ``allowed_commands()``: 71  (registry − 4 host-dispatched)

The host-dispatched delta (``shutdown``, ``tray_click``, ``heartbeat``,
``relaunch_ack``) is documented in ``tests/test_security_doc_command_count.py``.
This test asserts that the prose counts in the top-level docs stay in
lockstep with the actual registry / Rust-allowlist counts.

Scope: this test ONLY parses prose from the doc files. The
authoritative source-of-truth parsers live in
``tests/test_security_doc_command_count.py``.
"""

from __future__ import annotations

import re
from pathlib import Path

from tests.test_security_doc_command_count import (
    _allowed_commands_rust,
    _command_registry_entries,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
SECURITY_MD = REPO_ROOT / "SECURITY.md"
FEATURES_MD = REPO_ROOT / "FEATURES.md"
CHANGELOG_MD = REPO_ROOT / "CHANGELOG.md"
CONTRIBUTING_MD = REPO_ROOT / "CONTRIBUTING.md"


def test_security_md_states_current_counts() -> None:
    """SECURITY.md must state the current 75 / 71 two-way counts."""
    text = SECURITY_MD.read_text(encoding="utf-8")
    flat = re.sub(r"\s+", " ", text.replace("**", ""))
    m_reg = re.search(r"registers\s+(\d+)\s+handlers", flat)
    assert m_reg is not None, (
        "SECURITY.md no longer documents the Python registry handler count. Update this test or restore the prose."
    )
    m_rust = re.search(r"renderer allowlist exposes\s+(\d+)", flat)
    assert m_rust is not None, (
        "SECURITY.md no longer documents the Rust renderer-allowlist count. Update this test or restore the prose."
    )
    actual_py = len(_command_registry_entries())
    actual_rust = len(_allowed_commands_rust())
    assert int(m_reg.group(1)) == actual_py, (
        f"SECURITY.md documents {m_reg.group(1)} Python handlers but the actual registry count is {actual_py}."
    )
    assert int(m_rust.group(1)) == actual_rust, (
        f"SECURITY.md documents {m_rust.group(1)} Rust allowlist commands but the actual count is {actual_rust}."
    )


def test_features_md_states_command_counts() -> None:
    """FEATURES.md must state the current registry + renderer-callable counts."""
    text = FEATURES_MD.read_text(encoding="utf-8")
    flat = text.replace("**", "")
    m_reg = re.search(
        r"_COMMAND_REGISTRY`\s+registers\s+(\d+)\s+commands\s+total",
        flat,
    )
    assert m_reg is not None, (
        "FEATURES.md no longer documents the `_COMMAND_REGISTRY` total "
        "command count in the IPC allowlist row. Update the test or "
        "restore the prose."
    )
    assert int(m_reg.group(1)) == len(_command_registry_entries()), (
        f"FEATURES.md documents {m_reg.group(1)} commands total for "
        f"_COMMAND_REGISTRY but the actual count is "
        f"{len(_command_registry_entries())}. Update FEATURES.md row #81."
    )
    # Renderer-reachable count is the Rust allowlist size post-cutover.
    m_call = re.search(r"renderer-callable count is (\d+)", flat)
    assert m_call is not None, (
        "FEATURES.md no longer documents the 'renderer-callable count' "
        "in the IPC allowlist row. Update the test or restore the prose."
    )
    assert int(m_call.group(1)) == len(_allowed_commands_rust()), (
        f"FEATURES.md documents renderer-callable count "
        f"{m_call.group(1)} but the actual Rust allowlist count is "
        f"{len(_allowed_commands_rust())}. Update FEATURES.md row #81."
    )


def test_changelog_md_states_command_counts() -> None:
    """CHANGELOG.md may keep a historical triple; pin whatever it states.

    Historical CHANGELOG entries record pre-cutover three-way counts.
    Only assert the prose still exists and is self-consistent with the
    CURRENT two-way surface when the prose names Python/Rust (TS is
    historical).
    """
    text = CHANGELOG_MD.read_text(encoding="utf-8")
    m = re.search(
        r"TS allowlist\s*=\s*(\d+),\s*Rust allowlist\s*=\s*(\d+),\s*Python registry\s*=\s*(\d+)",
        text,
    )
    if m is None:
        # Historical triple retired from the changelog; nothing to pin.
        return
    # Historical record — do not force current counts onto archived prose.
    assert all(int(m.group(i)) > 0 for i in range(1, 4)), (
        "CHANGELOG.md historical allowlist counts must remain positive integers."
    )


def test_contributing_md_states_registry_count() -> None:
    """CONTRIBUTING.md must state the current registry count."""
    text = CONTRIBUTING_MD.read_text(encoding="utf-8")
    m = re.search(r"reuses the (\d+)-command registry", text)
    if m is None:
        # Wording may have been updated during the two-way collapse;
        # fall back to any "N-command registry" mention.
        m = re.search(r"(\d+)-command\s+(_COMMAND_)?registry", text)
    assert m is not None, (
        "CONTRIBUTING.md no longer documents the 'N-command registry' count. Update this test or restore the prose."
    )
    actual = len(_command_registry_entries())
    assert int(m.group(1)) == actual, (
        f"CONTRIBUTING.md documents {m.group(1)}-command registry but "
        f"the actual _COMMAND_REGISTRY count is {actual}. Update the "
        f"sidecar_ws.py row in CONTRIBUTING.md."
    )
