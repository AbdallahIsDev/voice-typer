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


def _marker_applies_current_platform(marker_str: str) -> bool:
    """Return True if a PEP 508 environment marker matches the running platform.

    WR-20: ``requirements-lock.txt`` is generated on Linux via
    ``uv pip compile``, so platform-conditional deps (pycaw, comtypes,
    pyobjc-*) are correctly excluded by pip-tools. We skip the
    lockfile-completeness check for deps whose ``sys_platform`` marker
    doesn't match the current platform.

    Uses ``packaging.markers.Marker`` (a pip / setuptools transitive
    dep, always available in a Python environment that has pip). Falls
    back to a naive ``sys_platform == 'X'`` regex if ``packaging`` is
    not importable.
    """
    if not marker_str.strip():
        return True  # no marker → always applies
    try:
        from packaging.markers import Marker  # type: ignore[import-not-found]

        return Marker(marker_str).evaluate()
    except ImportError:  # pragma: no cover — packaging is a pip dep
        # Naive fallback: handle the common ``sys_platform == 'X'`` case.
        m = re.search(r"sys_platform\s*==\s*['\"]([^'\"]+)['\"]", marker_str)
        if m:
            return sys.platform == m.group(1)
        # For more complex markers (e.g. ``platform_machine != 'arm64' or
        # sys_platform != 'darwin'``), assume the dep applies — this is
        # conservative (might flag a real miss, but won't false-pass).
        return True


def _direct_deps() -> set[str]:
    """Return the set of canonical names declared in pyproject.toml [project.dependencies].

    Includes platform-conditional deps regardless of marker — callers
    that need to skip non-matching platform deps should use
    :func:`_direct_deps_for_current_platform` instead.
    """
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


def _direct_deps_for_current_platform() -> set[str]:
    """Return direct deps whose environment marker matches the current platform.

    WR-20: filters out deps with ``sys_platform`` markers that don't
    match the current platform (e.g. ``pycaw`` on Linux, ``pyobjc-*``
    on Windows). The lockfile is generated on Linux via pip-tools, so
    these platform-conditional deps are correctly absent from
    ``requirements-lock.txt`` on non-target platforms and the
    completeness check must not flag them as missing.
    """
    with PYPROJECT.open("rb") as fh:
        data = tomllib.load(fh)
    deps_raw: list[str] = data.get("project", {}).get("dependencies", [])
    names: set[str] = set()
    for line in deps_raw:
        # Split off the environment marker (everything after the first ``;``).
        # ``"pycaw>=20230407; sys_platform == 'win32'"`` →
        # dep_part="pycaw>=20230407", marker_part="sys_platform == 'win32'"
        if ";" in line:
            dep_part, marker_part = line.split(";", 1)
        else:
            dep_part, marker_part = line, ""
        if not _marker_applies_current_platform(marker_part):
            continue
        m = re.match(r"^\s*([A-Za-z0-9_.-]+)", dep_part)
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
    """Every dep declared in pyproject.toml MUST have a pinned entry in the lockfile.

    WR-20: platform-conditional deps (``sys_platform == 'win32'`` /
    ``sys_platform == 'darwin'``) are skipped when the lockfile is
    generated on a non-matching platform. ``requirements-lock.txt``
    is generated on Linux via ``uv pip compile``, so pycaw, comtypes,
    and the pyobjc-* deps are correctly absent on Linux — flagging
    them as missing would be a false positive. They ARE checked on
    their respective target platforms (Windows / macOS CI runners).
    """
    direct = _direct_deps_for_current_platform()
    pinned = _lockfile_pinned_names()
    missing = direct - pinned
    assert not missing, (
        "H-20 regression: these pyproject.toml direct dependencies are MISSING from "
        "requirements-lock.txt (the `pip install --require-hashes` flow would install "
        "successfully but the runtime would crash with ModuleNotFoundError):\n  "
        + "\n  ".join(sorted(missing))
        + "\n\nFix: add the missing entries to requirements-lock.txt with sha256 hashes "
        "(run `uv pip compile --generate-hashes --universal --python-version 3.12 "
        "pyproject.toml -o requirements-lock.txt` or "
        "add them manually with `pip download <pkg>==<ver> --no-deps -d /tmp/whl "
        "&& pip hash /tmp/whl/*.whl`)."
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
