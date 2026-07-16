"""d-review Finding 5 regression guard.

SECURITY.md documents the number of IPC commands in the Electron main
process's ``ALLOWED_COMMANDS`` allowlist. Finding 5 noted the doc had
stale "~35 commands" while the real allowlist had grown. This test
parses the documented count out of SECURITY.md and asserts it matches
the actual ``ALLOWED_COMMANDS`` ``Set`` entries in
``voice_typer/client/src/main/index.ts``, so the doc can't silently
drift again when commands are added or removed.

The same count is also asserted in the allowlist parity test
(``tests/test_electron_ipc_and_build.py``), which cross-checks the
renderer allowlist against the server command registry — together they
keep the security docs, the renderer allowlist, and the server registry
in lockstep.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SECURITY_MD = REPO_ROOT / "SECURITY.md"
INDEX_TS = REPO_ROOT / "voice_typer" / "client" / "src" / "main" / "index.ts"


def _count_allowed_commands() -> int:
    """Count quoted command strings inside the ALLOWED_COMMANDS Set block."""
    lines = INDEX_TS.read_text(encoding="utf-8").splitlines()
    start = next(i for i, line in enumerate(lines) if "ALLOWED_COMMANDS = new Set" in line)
    count = 0
    for line in lines[start:]:
        if line.strip().startswith("]);"):
            break
        if re.match(r'\s*"([a-z_]+)"', line):
            count += 1
    return count


def _documented_count() -> int | None:
    """Extract the documented allowlist count from SECURITY.md.

    Matches prose like "only the 68 commands listed in ALLOWED_COMMANDS"
    or "only the 35 commands".
    """
    text = SECURITY_MD.read_text(encoding="utf-8")
    # Strip Markdown emphasis markers so "**68**" matches like "68".
    text = text.replace("**", "")
    m = re.search(r"(\d+)\s+commands?\s+(listed\s+in\s+)?`?ALLOWED_COMMANDS`?", text)
    return int(m.group(1)) if m else None


def test_security_md_allowlist_count_matches_source() -> None:
    actual = _count_allowed_commands()
    documented = _documented_count()
    assert documented is not None, (
        "SECURITY.md no longer documents the ALLOWED_COMMANDS count in a "
        "parseable form (expected: 'only the N commands listed in "
        "ALLOWED_COMMANDS')."
    )
    assert documented == actual, (
        f"SECURITY.md documents {documented} ALLOWED_COMMANDS but the "
        f"renderer source defines {actual}. Update SECURITY.md and this "
        f"assertion together (d-review Finding 5)."
    )


def test_allowed_commands_nonempty() -> None:
    # Sanity: the allowlist must never be empty.
    assert _count_allowed_commands() > 0
