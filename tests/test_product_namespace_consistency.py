"""Repo-wide reverse-DNS product-namespace consistency guard.

The canonical product root is ``com.voicetyper.*``:

- ``src-tauri/tauri.conf.json`` ``identifier`` = ``com.voicetyper.desktop``
- ``voice_typer/client/electron-builder.yml`` ``appId`` = ``com.voicetyper.desktop``
- polkit action ``com.voicetyper.install-permissions`` (policy installed
  to ``/usr/share/polkit-1/actions/com.voicetyper.policy``)
- macOS LaunchAgents ``com.voicetyper.plist`` / ``com.voicetyper.prewarm.plist``
- Nuitka sidecar / prewarm ``--macos-signed-app-name=com.voicetyper.*``

Legacy / wrong roots that must NOT reappear:

- ``org.voice-typer.*`` — the pre-Tauri Electron polkit namespace.
  Review finding #54 renamed it to ``com.voicetyper.*``; the
  uninstaller now removes the legacy policy file so uninstalled
  systems fully converge on ``com.voicetyper.*``.
- ``com.voice-typer`` / ``com.voice_typer`` / ``org.voicetyper`` /
  ``org.voice_typer`` — misspellings / alternative spellings of the
  product root (a real ``com.voice-typer`` once shipped in
  ``docs/permissions-per-os.md``).

This test scans every tracked text file and fails on any reverse-DNS
token in the ``voicetyper`` family whose root is not ``com.voicetyper``.
The only legacy tokens allowed to remain anywhere in the repo are:

- ``org.voice-typer.policy`` / ``org.voice-typer.install-permissions``
  / ``org.voice-typer.*`` — the legacy polkit artifacts referenced by
  the uninstaller's cleanup (``LEGACY_POLKIT_POLICY_DEST`` in
  ``scripts/linux/install_permissions.py``) and by the rename
  documentation (polkit header, ADR-0008).
- ``app.voicetyper`` — the OS keyring service name
  (``voice_typer/server/credential_store.py::KEYRING_SERVICE_NAME``).
  This is a runtime-stable, user-visible credential namespace:
  renaming it would orphan every existing user's stored API keys and
  the at-rest encryption key. It deliberately uses a different RDNN
  root and is not a bundle identifier.

One historical file is exempt for the BARE legacy root only:
``scripts/append_review_findings.py`` — a one-shot review log whose
finding-#54 entry snapshots the state at close time ("Repo-wide grep
confirms zero ``org.voice-typer`` references remain"). A historical
record is not rewritten; the guard still catches any NEW bare-legacy
root usage anywhere else in the repo, and the specific legacy artifact
tokens above are the only ones that may appear at all.

This test file itself is exempt from the scan: its docstring
necessarily spells out the exact banned spellings (``org.voicetyper``,
``com.voice-typer``, ...) so a human can see what is forbidden — those
are definitions, not product-namespace usages.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

# tests/test_product_namespace_consistency.py → repo root in 1 parent.
_REPO_ROOT = Path(__file__).resolve().parents[1]

# The canonical root: every product namespace must start with this.
CANONICAL_ROOT = "com.voicetyper"

# Legacy tokens that legitimately remain (see module docstring).
_ALLOWED_LEGACY_TOKENS = frozenset(
    {
        "org.voice-typer.policy",  # legacy polkit file the uninstaller removes
        "org.voice-typer.install-permissions",  # legacy polkit action ID (rename docs)
        "org.voice-typer.*",  # glob form used in rename docs / findings
        "app.voicetyper",  # OS keyring service name (runtime-stable credentials)
    }
)

# Historical findings log: exempt for the BARE legacy root only (its
# finding-#54 entry snapshots the "zero references remain" state at
# close time). The specific legacy artifact tokens are still governed
# by the global allowlist above.
_FINDINGS_LOG = Path("scripts/append_review_findings.py")

# This test module itself: its docstring defines the exact banned
# spellings, so it is exempt from the scan (definitions, not usages).
_SELF = Path("tests/test_product_namespace_consistency.py")

# Reverse-DNS roots plausible for a product namespace, in the
# ``voicetyper`` family (canonical + legacy spellings).
_RDNN_RE = re.compile(
    r"(?<![A-Za-z0-9_.-])"  # token boundary (not mid-identifier / after a dot)
    r"((?:com|org|io|net|dev|app|me|co|uk|us|xyz|ai|tech|so|cc|tv)\."
    r"(?:voicetyper|voice-typer|voice_typer)"
    r"(?:\.[A-Za-z0-9_*-]+)?)"  # optional rest of token (e.g. .desktop, .policy, .*)
)


def _tracked_text_files() -> list[Path]:
    """All tracked files that look like text (skip binaries via NUL sniff)."""
    result = subprocess.run(
        ["git", "-C", str(_REPO_ROOT), "ls-files"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        pytest.fail(f"`git ls-files` failed (exit {result.returncode}): {result.stderr.strip()}")
    files: list[Path] = []
    for rel in result.stdout.splitlines():
        if not rel:
            continue
        path = _REPO_ROOT / rel
        try:
            data = path.read_bytes()
        except OSError:
            continue
        # NUL in the first 8 KiB ⇒ binary (PNG/ICO/exe/db...). Text
        # files (including extension-less shell scripts like prerm)
        # are scanned regardless of extension.
        if b"\x00" in data[:8192]:
            continue
        files.append(path)
    return files


class TestProductNamespaceConsistency:
    """No non-``com.voicetyper.*`` product namespace anywhere in the repo."""

    def test_no_legacy_or_wrong_product_namespaces(self):
        """Every reverse-DNS token in the voicetyper family is canonical
        (or on the explicit legacy allowlist)."""
        violations: list[tuple[str, int, str]] = []
        for path in _tracked_text_files():
            rel = path.relative_to(_REPO_ROOT).as_posix()
            rel_path = Path(rel)
            if rel_path == _SELF:
                continue  # this module defines the banned spellings
            text = path.read_bytes().decode("utf-8", errors="replace")
            for match in _RDNN_RE.finditer(text):
                token = match.group(1)
                if token == CANONICAL_ROOT or token.startswith(CANONICAL_ROOT + "."):
                    continue  # canonical — allowed everywhere
                if token in _ALLOWED_LEGACY_TOKENS:
                    continue  # documented legacy artifact — allowed everywhere
                if token == "org.voice-typer" and rel_path == _FINDINGS_LOG:
                    continue  # historical findings snapshot — bare-root exempt
                line = text.count("\n", 0, match.start()) + 1
                violations.append((rel, line, token))

        assert not violations, (
            "Non-canonical product namespace(s) found. The canonical reverse-DNS "
            f"root is '{CANONICAL_ROOT}.*' (tauri.conf.json identifier, "
            "electron-builder.yml appId, polkit action, macOS LaunchAgents).\n"
            + "\n".join(f"  {rel}:{line}: {token}" for rel, line, token in violations)
            + "\n"
            "Fix: rename the token to the canonical com.voicetyper.* root (or, only "
            "for the documented legacy polkit artifacts the uninstaller removes / "
            "the keyring service name, extend the allowlist in "
            "tests/test_product_namespace_consistency.py with a reason)."
        )
