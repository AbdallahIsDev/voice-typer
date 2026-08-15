"""Doc-parity test: top-level docs must agree with the live command counts.

the top-level documentation files
(``SECURITY.md``, ``FEATURES.md``, ``CHANGELOG.md``, ``CONTRIBUTING.md``)
all cite the IPC command surface counts. After the  narrowing
and the subsequent +1 reconciliation across all three allowlists
(+1 again 2026-08-13 for `transcribe_offline`), then −3 for the
2026-08-14 prewarm retirements (`get_prewarm_status` / `run_prewarm` /
`open_prewarm_log` — prewarm became a worker startup phase, master
plan §6.2 P-1), then +2 restored 2026-08-14 (plan §6.3 addendum —
`get_prewarm_status` / `open_prewarm_log` brought back for the
Settings → About Cache Status card, verbatim from 5a319872;
`run_prewarm` stays retired), the authoritative counts are:

    Python ``_COMMAND_REGISTRY``   : 69  (registry total)
    TS renderer ``ALLOWED_COMMANDS``: 67  (registry − 2 host-only)
    Rust host ``allowed_commands()``: 65  (TS − 2 TS-only exceptions)

The host-only delta (``shutdown`` + ``tray_click``) and the TS-only
delta (``heartbeat`` + ``relaunch_ack``) are documented in
``tests/test_security_doc_command_count.py``. This test asserts that
the prose counts in the four top-level docs stay in lockstep with the
actual registry count, so a contributor adding a command without
updating the docs is caught at CI time.

Scope: this test ONLY parses prose from the four doc files. The
authoritative source-of-truth parser already lives in
``tests/test_security_doc_command_count.py``; this file reuses that
parser's helpers to assert doc-parity without duplicating the parsing
logic.
"""

from __future__ import annotations

import re
from pathlib import Path

from tests.test_security_doc_command_count import (
    _allowed_commands_rust,
    _allowed_commands_ts,
    _command_registry_entries,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
SECURITY_MD = REPO_ROOT / "SECURITY.md"
FEATURES_MD = REPO_ROOT / "FEATURES.md"
CHANGELOG_MD = REPO_ROOT / "CHANGELOG.md"
CONTRIBUTING_MD = REPO_ROOT / "CONTRIBUTING.md"


def test_security_md_states_current_counts() -> None:
    """SECURITY.md must state the current 69 / 67 / 65 count triple.

    an earlier draft of SECURITY.md's
    reconciliation blockquote cited stale counts of "64 Python ↔ 62 TS
    ↔ 60 Rust". The actual counts (asserted by
    ``tests/test_security_doc_command_count.py``) are 69 / 67 / 65
    (2026-08-14: +2 restored prewarm status commands — plan §6.3
    addendum).
    This test pins the prose so a future drift is caught.
    """
    text = SECURITY_MD.read_text(encoding="utf-8")
    # Strip Markdown blockquote markers so the prose can be matched
    # across wrapped lines (the count triple spans a `> ` blockquote
    # that wraps mid-sentence).
    flat = "\n".join(
        line.lstrip().lstrip(">").strip() if line.lstrip().startswith(">") else line
        for line in text.splitlines()
    )
    flat = re.sub(r"\s+", " ", flat)
    # Look for the "N Python ↔ N TS ↔ N Rust" prose triple.
    m = re.search(
        r"(\d+)\s+Python\s*↔\s*(\d+)\s+TS\s*↔\s*(\d+)\s+Rust",
        flat,
    )
    assert m is not None, (
        "SECURITY.md no longer documents the 'N Python ↔ N TS ↔ N Rust' "
        "count triple. Update the test or restore the prose."
    )
    py_count, ts_count, rust_count = (int(m.group(1)), int(m.group(2)), int(m.group(3)))
    actual_py = len(_command_registry_entries())
    actual_ts = len(_allowed_commands_ts())
    actual_rust = len(_allowed_commands_rust())
    assert (py_count, ts_count, rust_count) == (actual_py, actual_ts, actual_rust), (
        f"SECURITY.md prose counts ({py_count}/{ts_count}/{rust_count}) "
        f"do not match the actual registry/TS/Rust counts "
        f"({actual_py}/{actual_ts}/{actual_rust}). Update the prose in "
        f"the 'Command Allowlist (SEC-019)' blockquote."
    )


def test_features_md_states_command_counts() -> None:
    """FEATURES.md must state the current registry + renderer-callable counts.

    the IPC allowlist row in the Developer/Build
    feature table previously stated "63 commands total" for the Python
    registry and "renderer-callable count is 61" — both stale. The
    actual counts are 69 (registry) and 67 (renderer-callable) as of
    2026-08-14 (prewarm status surface restored — plan §6.3 addendum).
    """
    text = FEATURES_MD.read_text(encoding="utf-8")
    # Strip Markdown emphasis so "**65**" parses as 65.
    flat = text.replace("**", "")
    # Find the IPC allowlist row's mention of "_COMMAND_REGISTRY` registers N commands total".
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
    # Find the "renderer-callable count is N" prose.
    m_call = re.search(r"renderer-callable count is (\d+)", flat)
    assert m_call is not None, (
        "FEATURES.md no longer documents the 'renderer-callable count' "
        "in the IPC allowlist row. Update the test or restore the prose."
    )
    assert int(m_call.group(1)) == len(_allowed_commands_ts()), (
        f"FEATURES.md documents renderer-callable count "
        f"{m_call.group(1)} but the actual TS allowlist count is "
        f"{len(_allowed_commands_ts())}. Update FEATURES.md row #81."
    )


def test_changelog_md_states_command_counts() -> None:
    """CHANGELOG.md must state the current 63/61/65 allowlist counts.

    the  reconciliation entry previously stated
    "TS allowlist = 61, Rust allowlist = 61, Python registry = 63".
    The actual counts are 67/65/69 (2026-08-14: +2 restored prewarm
    status commands — plan §6.3 addendum). This test pins the prose so
    the historical record stays accurate to the current state.
    """
    text = CHANGELOG_MD.read_text(encoding="utf-8")
    # The  reconciliation bullet states all three counts in one
    # sentence: "TS allowlist = N, Rust allowlist = N, Python registry = N".
    m = re.search(
        r"TS allowlist\s*=\s*(\d+),\s*Rust allowlist\s*=\s*(\d+),\s*Python registry\s*=\s*(\d+)",
        text,
    )
    assert m is not None, (
        "CHANGELOG.md no longer documents the "
        "'TS allowlist = N, Rust allowlist = N, Python registry = N' "
        "triple in the  reconciliation block. Update the test or "
        "restore the prose."
    )
    ts_doc, rust_doc, py_doc = (int(m.group(1)), int(m.group(2)), int(m.group(3)))
    actual_ts = len(_allowed_commands_ts())
    actual_rust = len(_allowed_commands_rust())
    actual_py = len(_command_registry_entries())
    assert (ts_doc, rust_doc, py_doc) == (actual_ts, actual_rust, actual_py), (
        f"CHANGELOG.md  reconciliation counts "
        f"({ts_doc}/{rust_doc}/{py_doc}) do not match actual counts "
        f"({actual_ts}/{actual_rust}/{actual_py}). Update the prose."
    )


def test_contributing_md_states_registry_count() -> None:
    """CONTRIBUTING.md must state the current registry count (69).

    the ``sidecar_ws.py`` module table row previously
    cited a "63-command registry" — stale. The actual count is 69
    (2026-08-14: prewarm status surface restored — plan §6.3 addendum).
    """
    text = CONTRIBUTING_MD.read_text(encoding="utf-8")
    # The sidecar_ws.py row says "reuses the N-command registry unchanged".
    m = re.search(r"reuses the (\d+)-command registry", text)
    assert m is not None, (
        "CONTRIBUTING.md no longer documents the 'N-command registry' "
        "count in the sidecar_ws.py module-table row. Update the test "
        "or restore the prose."
    )
    actual = len(_command_registry_entries())
    assert int(m.group(1)) == actual, (
        f"CONTRIBUTING.md documents {m.group(1)}-command registry but "
        f"the actual _COMMAND_REGISTRY count is {actual}. Update the "
        f"sidecar_ws.py row in CONTRIBUTING.md §2."
    )
