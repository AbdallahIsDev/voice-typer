"""H-20 (IMPROVE-2026-07-19): regression guard for requirements-lock.txt completeness.

Prior bug: ``websockets`` and ``keyring`` were declared in
``pyproject.toml`` [project.dependencies] but MISSING from
``requirements-lock.txt``. The documented reproducible-build command
``pip install --require-hashes -r requirements-lock.txt`` would
install successfully but the runtime would crash with
``ModuleNotFoundError`` on the first Tauri sidecar WS connect
(``sidecar_ws.py`` imports ``websockets``) and the first API-key
storage operation (``credential_store.py`` imports ``keyring``).

This test parses ``pyproject.toml`` [project.dependencies] and asserts
that each direct dependency appears as a hash-pinned entry in
``requirements-lock.txt``. Run via:

    python -m pytest tests/test_requirements_lock_completeness.py -v
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

if sys.version_info >= (3, 11):
    import tomllib  # type: ignore[import-not-found]
else:  # pragma: no cover — Python 3.10 fallback
    import tomli as tomllib  # type: ignore[import-not-found, no-redef]

REPO_ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = REPO_ROOT / "pyproject.toml"
LOCKFILE = REPO_ROOT / "requirements-lock.txt"


def _normalize(name: str) -> str:
    """PEP 503 normalisation: ``keyring-foo`` → ``keyring-foo`` (already canonical).

    PEP 503 says ``re.sub(r"[-_.]+", "-", name).lower()`` but pip's
    lockfile uses the canonical name already, so we just lowercase + dash.
    """
    return re.sub(r"[-_.]+", "-", name).lower()


def _direct_deps() -> set[str]:
    """Return the set of canonical names declared in pyproject.toml [project.dependencies]."""
    with PYPROJECT.open("rb") as fh:
        data = tomllib.load(fh)
    deps_raw: list[str] = data.get("project", {}).get("dependencies", [])
    names: set[str] = set()
    for line in deps_raw:
        # Strip environment markers + version specifiers.
        # ``"websockets>=12.0,<14.0 ; python_version >= '3.10'"`` → ``"websockets"``
        m = re.match(r"^\s*([A-Za-z0-9_.-]+)", line)
        if m:
            names.add(_normalize(m.group(1)))
    return names


def _lockfile_pinned_names() -> set[str]:
    """Return the set of canonical names that appear as ``<name>==<version>`` in the lockfile."""
    with LOCKFILE.open("r", encoding="utf-8") as fh:
        text = fh.read()
    # ``websockets==13.1 \`` → captures ``websockets``
    names: set[str] = set()
    for m in re.finditer(r"^([A-Za-z0-9_.-]+)==[A-Za-z0-9.+!]+", text, re.MULTILINE):
        names.add(_normalize(m.group(1)))
    return names


def test_every_direct_dep_is_pinned_in_lockfile() -> None:
    """Every dep declared in pyproject.toml MUST have a pinned entry in the lockfile."""
    direct = _direct_deps()
    pinned = _lockfile_pinned_names()
    missing = direct - pinned
    assert not missing, (
        "H-20 regression: these pyproject.toml direct dependencies are MISSING from "
        "requirements-lock.txt (the `pip install --require-hashes` flow would install "
        "successfully but the runtime would crash with ModuleNotFoundError):\n  "
        + "\n  ".join(sorted(missing))
        + "\n\nFix: add the missing entries to requirements-lock.txt with sha256 hashes "
        "(run `pip-compile --generate-hashes -o requirements-lock.txt pyproject.toml` or "
        "add them manually with `pip download <pkg>==<ver> --no-deps -d /tmp/whl && pip hash /tmp/whl/*.whl`)."
    )


def test_known_critical_deps_are_pinned() -> None:
    """Targeted sentinel test for the two deps that were missing in H-20.

    These have lazy imports (``try/except ImportError``) so a missing
    pin wouldn't surface at install time — only at first use. Keep this
    test even if the generic test above passes, so a future regression
    on these specific deps is caught loudly.
    """
    pinned = _lockfile_pinned_names()
    assert "websockets" in pinned, (
        "H-20 regression: `websockets` is missing from requirements-lock.txt. "
        "sidecar_ws.py imports it for the Tauri WS transport (ADR-0020 §14)."
    )
    assert "keyring" in pinned, (
        "H-20 regression: `keyring` is missing from requirements-lock.txt. "
        "credential_store.py imports it for OS-native credential storage (RW-01)."
    )


if __name__ == "__main__":
    # Manual run: ``python tests/test_requirements_lock_completeness.py``
    test_every_direct_dep_is_pinned_in_lockfile()
    test_known_critical_deps_are_pinned()
    print("OK: all direct deps are pinned in requirements-lock.txt")
