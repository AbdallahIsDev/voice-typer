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
  Review finding #54 renamed it to ``com.voicetyper.*``; the legacy
  policy file is removed at install/upgrade time by
  ``install_permissions.py::_install_polkit_policy`` AND at uninstall
  (via ``LEGACY_POLKIT_POLICY_DEST``), so converged systems never
  register the old action ID.
- ``app.voicetyper`` — the pre-migration OS keyring service name.
  ``voice_typer/server/credential_store.py::KEYRING_SERVICE_NAME`` now
  uses ``com.voicetyper.keyring``, and
  ``_migrate_legacy_service_names_locked()`` copies legacy entries
  forward at startup (gated on a per-hop config flag). No allowlisted
  token remains for it.
- ``com.voice-typer`` / ``com.voice_typer`` / ``org.voicetyper`` /
  ``org.voice_typer`` — misspellings / alternative spellings of the
  product root (a real ``com.voice-typer`` once shipped in
  ``docs/permissions-per-os.md``).

This test scans every tracked text file and fails on any reverse-DNS
token in the ``voicetyper`` family whose root is not ``com.voicetyper``.
The only legacy tokens allowed to remain are SCOPED TO SPECIFIC FILES
(see ``_LEGACY_TOKEN_ALLOWLIST`` — nothing is allowed globally):

- the uninstaller/installer script (both copies) — the explicit legacy
  cleanup itself (``LEGACY_POLKIT_POLICY_DEST`` removal at
  install/upgrade + uninstall);
- the tests that pin that cleanup;
- the polkit file headers — the rename history must spell the old name
  to be meaningful;
- ``scripts/append_review_findings.py`` — a one-shot review log whose
  finding-#54 entry snapshots the state at close time (bare root +
  artifact tokens). A historical record is not rewritten; the guard
  still catches any NEW legacy-root usage anywhere else in the repo.

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

# Legacy tokens that legitimately remain, SCOPED to the files that
# perform or document the explicit legacy cleanup (see module docstring).
# Nothing is allowed globally — every other file must use the canonical
# ``com.voicetyper.*`` root.
_LEGACY_TOKEN_ALLOWLIST: dict[str, frozenset[str]] = {
    # Uninstaller/installer script (both copies) — the explicit legacy
    # cleanup itself: removal of the legacy policy at install/upgrade
    # (``_install_polkit_policy``) and at uninstall (``uninstall()``).
    "scripts/linux/install_permissions.py": frozenset({"org.voice-typer.policy", "org.voice-typer.*"}),
    "src-tauri/resources/linux-scripts/install_permissions.py": frozenset(
        {"org.voice-typer.policy", "org.voice-typer.*"}
    ),
    # Tests pinning that cleanup.
    "tests/test_install_permissions_polkit_stable.py": frozenset({"org.voice-typer.policy"}),
    # Polkit file headers — the rename history must spell the old name
    # to be meaningful.
    "scripts/linux/voice-typer.polkit": frozenset({"org.voice-typer.policy", "org.voice-typer.install-permissions"}),
    "src-tauri/resources/linux-scripts/voice-typer.polkit": frozenset(
        {"org.voice-typer.policy", "org.voice-typer.install-permissions"}
    ),
    # Historical findings log — the finding-#54 entry snapshots the
    # close-time state (bare root + legacy artifact tokens). A one-shot
    # record is not rewritten.
    "scripts/append_review_findings.py": frozenset(
        {
            "org.voice-typer",
            "org.voice-typer.policy",
            "org.voice-typer.install-permissions",
            "org.voice-typer.*",
        }
    ),
    # The keyring half of the legacy namespace cleanup: the
    # ``_LEGACY_KEYRING_SERVICE_NAMES`` tuple + migration docstrings
    # must name the old service so ``_migrate_legacy_service_names_locked``
    # can re-register entries under ``KEYRING_SERVICE_NAME``.
    "voice_typer/server/credential_store.py": frozenset({"app.voicetyper"}),
    # The drift-guard test module pins the credential_store legacy
    # tuple STRING verbatim (``_LEGACY_KEYRING_SERVICE_NAMES: ... =
    # ("app.voicetyper", ...)``) and its allowlist docstring names the
    # token — definitions/pins, not usages.
    "tests/tauri/test_config_script_drift.py": frozenset({"app.voicetyper"}),
}

# This test module itself: its docstring defines the exact banned
# spellings, so it is exempt from the scan (definitions, not usages).
_SELF = Path("tests/test_product_namespace_consistency.py")

# Session metadata files (agent worklogs / handoff summaries) are NOT
# product code: they are live logs rewritten by every session, and the
# prose occasionally quotes non-canonical namespaces while discussing
# migration history. Exempting them keeps the guard effective for every
# product/script/test file while stopping the churn of "fix the worklog
# prose" commits (the session log is not a namespace source of truth).
_SESSION_LOG_FILES = frozenset(
    {
        "worklog.md",
        "SUMMARY.md",
        "review.md",
    }
)

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
        (or scoped per-file in ``_LEGACY_TOKEN_ALLOWLIST``)."""
        violations: list[tuple[str, int, str]] = []
        for path in _tracked_text_files():
            rel = path.relative_to(_REPO_ROOT).as_posix()
            rel_path = Path(rel)
            if rel_path == _SELF:
                continue  # this module defines the banned spellings
            if rel in _SESSION_LOG_FILES:
                continue  # session metadata — not a namespace source of truth
            text = path.read_bytes().decode("utf-8", errors="replace")
            for match in _RDNN_RE.finditer(text):
                token = match.group(1)
                if token == CANONICAL_ROOT or token.startswith(CANONICAL_ROOT + "."):
                    continue  # canonical — allowed everywhere
                allowed = _LEGACY_TOKEN_ALLOWLIST.get(rel, frozenset())
                if token in allowed:
                    continue  # documented legacy artifact — scoped to this file
                line = text.count("\n", 0, match.start()) + 1
                violations.append((rel, line, token))

        assert not violations, (
            "Non-canonical product namespaces found. The canonical reverse-DNS "
            f"root is '{CANONICAL_ROOT}.*' (tauri.conf.json identifier, "
            "electron-builder.yml appId, polkit action, macOS LaunchAgents, "
            "keyring service name).\n" + "\n".join(f"  {rel}:{line}: {token}" for rel, line, token in violations) + "\n"
            "Fix: rename the token to the canonical com.voicetyper.* root (or, only "
            "for the legacy polkit artifacts the installer/uninstaller explicitly "
            "cleans up, extend the per-file scope in "
            "tests/test_product_namespace_consistency.py::_LEGACY_TOKEN_ALLOWLIST "
            "with a reason)."
        )
