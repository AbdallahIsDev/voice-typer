"""
Repo-wide reverse-DNS product-namespace consistency guard.
Legacy / wrong roots that must NOT reappear:
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]

# The canonical root: every product namespace must start with this.
CANONICAL_ROOT = "com.Lausu"

_LEGACY_TOKEN_ALLOWLIST: dict[str, frozenset[str]] = {
    # Uninstaller/installer script (both copies), the explicit legacy
    "scripts/linux/install_permissions.py": frozenset({"org.lausu.policy", "org.lausu.*"}),
    "src-tauri/resources/linux-scripts/install_permissions.py": frozenset({"org.lausu.policy", "org.lausu.*"}),
    # Tests pinning that cleanup.
    "tests/test_install_permissions_polkit_stable.py": frozenset({"org.lausu.policy"}),
    # to be meaningful.
    "scripts/linux/lausu.polkit": frozenset({"org.lausu.policy", "org.lausu.install-permissions"}),
    "src-tauri/resources/linux-scripts/lausu.polkit": frozenset({"org.lausu.policy", "org.lausu.install-permissions"}),
    "voice_typer/server/credential_store/_schema.py": frozenset({"app.Lausu"}),
    "voice_typer/server/credential_store/_migration.py": frozenset({"app.Lausu"}),
    "docs/security/credential-store.md": frozenset({"app.Lausu"}),
    # The drift-guard test module pins the credential_store legacy
    "tests/tauri/test_config_script_drift.py": frozenset({"app.Lausu"}),
}

# This test module itself: its docstring defines the exact banned
_SELF = Path("tests/test_product_namespace_consistency.py")

_SESSION_LOG_FILES = frozenset(
    {
        "worklog.md",
        "SUMMARY.md",
        "review.md",
    }
)

_RDNN_RE = re.compile(
    r"(?<![A-Za-z0-9_.-])"  # token boundary (not mid-identifier / after a dot)
    r"((?:com|org|io|net|dev|app|me|co|uk|us|xyz|ai|tech|so|cc|tv)\."
    r"(?:Lausu|lausu|voice_typer)"
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
        if b"\x00" in data[:8192]:
            continue
        files.append(path)
    return files


class TestProductNamespaceConsistency:
    """No non-``com.Lausu.*`` product namespace anywhere in the repo."""

    def test_no_legacy_or_wrong_product_namespaces(self):
        """Every reverse-DNS token in the Lausu family is canonical"""
        violations: list[tuple[str, int, str]] = []
        for path in _tracked_text_files():
            rel = path.relative_to(_REPO_ROOT).as_posix()
            rel_path = Path(rel)
            if rel_path == _SELF:
                continue  # this module defines the banned spellings
            if rel in _SESSION_LOG_FILES:
                continue  # session metadata, not a namespace source of truth
            text = path.read_bytes().decode("utf-8", errors="replace")
            for match in _RDNN_RE.finditer(text):
                token = match.group(1)
                if token == CANONICAL_ROOT or token.startswith(CANONICAL_ROOT + "."):
                    continue  # canonical, allowed everywhere
                allowed = _LEGACY_TOKEN_ALLOWLIST.get(rel, frozenset())
                if token in allowed:
                    continue  # documented legacy artifact, scoped to this file
                line = text.count("\n", 0, match.start()) + 1
                violations.append((rel, line, token))

        assert not violations, (
            "Non-canonical product namespaces found. The canonical reverse-DNS "
            f"root is '{CANONICAL_ROOT}.*' (tauri.conf.json identifier, "
            "polkit action, macOS LaunchAgents, "
            "keyring service name).\n" + "\n".join(f"  {rel}:{line}: {token}" for rel, line, token in violations) + "\n"
            "Fix: rename the token to the canonical com.Lausu.* root (or, only "
            "for the legacy polkit artifacts the installer/uninstaller explicitly "
            "cleans up, extend the per-file scope in "
            "tests/test_product_namespace_consistency.py::_LEGACY_TOKEN_ALLOWLIST "
            "with a reason)."
        )
