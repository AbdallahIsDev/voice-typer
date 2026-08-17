"""regression guard for requirements-lock.txt completeness.

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

The module also includes a version-drift guard
(:func:`test_lockfile_pinned_versions_satisfy_pyproject_constraints`)
that parses every ``<name>==<version>`` entry in the lockfile and
verifies the pinned version satisfies the version specifier declared
in ``pyproject.toml``. This catches the class of bug where a dep is
bumped in ``pyproject.toml`` but the lockfile is not regenerated —
the documented ``pip install --require-hashes`` path would then
install a stale (or constraint-violating) version while CI's
``uv pip install --system ".[test]"`` resolver picks the newer one.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

if sys.version_info >= (3, 11):
    import tomllib  # type: ignore[import-not-found]
else:  # pragma: no cover — Python 3.10 fallback
    try:
        import tomli as tomllib  # type: ignore[import-not-found, no-redef]
    except ImportError:  # pragma: no cover — tomli not in the lock
        pytest.skip(
            "tomli backport not installed on Python 3.10 — skipping lock-completeness check",
            allow_module_level=True,
        )

REPO_ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = REPO_ROOT / "pyproject.toml"
LOCKFILE = REPO_ROOT / "requirements-lock.txt"


def _normalize(name: str) -> str:
    """normalisation: ``keyring-foo`` → ``keyring-foo`` (already canonical).

    says ``re.sub(r"[-_.]+", "-", name).lower()`` but pip's
    lockfile uses the canonical name already, so we just lowercase + dash.
    """
    return re.sub(r"[-_.]+", "-", name).lower()


def _marker_applies_current_platform(marker_str: str) -> bool:
    """Return True if a PEP 508 environment marker matches the running platform.

    ``requirements-lock.txt`` is generated on Linux via
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

    filters out deps with ``sys_platform`` markers that don't
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

    platform-conditional deps (``sys_platform == 'win32'`` /
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
        "regression: these pyproject.toml direct dependencies are MISSING from "
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
        "regression: `websockets` is missing from requirements-lock.txt. "
        "sidecar_ws.py imports it for the Tauri WS transport (ADR-0020 §14)."
    )
    assert "keyring" in pinned, (
        "regression: `keyring` is missing from requirements-lock.txt. "
        "credential_store.py imports it for OS-native credential storage."
    )


# ─── Version-drift guard ─────────────────────────────────────────────────────
def _pyproject_dep_specifiers() -> dict[str, str]:
    """Return ``{normalized_name: specifier_str}`` for every direct dep.

    Each entry's value is the version specifier clause (e.g.
    ``">=5.9,<8.0"``) stripped of environment markers. Deps with no
    version specifier are omitted — there is nothing to check against.
    """
    with PYPROJECT.open("rb") as fh:
        data = tomllib.load(fh)
    deps_raw: list[str] = data.get("project", {}).get("dependencies", [])
    out: dict[str, str] = {}
    for line in deps_raw:
        # Split off environment markers.
        if ";" in line:
            dep_part, _ = line.split(";", 1)
        else:
            dep_part = line
        # ``"psutil>=5.9,<8.0"`` → name="psutil", spec=">=5.9,<8.0".
        m = re.match(r"^\s*([A-Za-z0-9_.-]+)\s*(.*)$", dep_part)
        if not m:
            continue
        name = _normalize(m.group(1))
        spec = m.group(2).strip()
        if spec:
            out[name] = spec
    return out


def _lockfile_pinned_versions() -> dict[str, str]:
    """Return ``{normalized_name: pinned_version}`` for every ``<name>==<version>`` line.

    Only the FIRST pin per package is recorded (lockfiles are expected
    to have exactly one pin per package; a duplicate would be a bug
    caught elsewhere).
    """
    with LOCKFILE.open("r", encoding="utf-8") as fh:
        text = fh.read()
    out: dict[str, str] = {}
    for m in re.finditer(r"^([A-Za-z0-9_.-]+)==([A-Za-z0-9.+!]+)", text, re.MULTILINE):
        name = _normalize(m.group(1))
        if name not in out:
            out[name] = m.group(2)
    return out


def test_lockfile_pinned_versions_satisfy_pyproject_constraints() -> None:
    """Every lockfile pin MUST satisfy the version specifier in pyproject.toml.

    CI installs deps via ``uv pip install --system ".[test]"`` (resolves
    from pyproject.toml) while the documented reproducible-build path
    uses ``pip install --require-hashes -r requirements-lock.txt``
    (installs the exact pinned versions). If a dep is bumped in
    pyproject.toml but the lockfile is NOT regenerated, the two paths
    silently diverge — CI runs the new version, the reproducible-build
    path runs the old (potentially constraint-violating) version.

    This test catches that drift: for every direct dep that has BOTH a
    version specifier in pyproject.toml AND a pin in the lockfile, the
    pinned version must satisfy the specifier.
    """
    try:
        from packaging.specifiers import SpecifierSet
        from packaging.version import Version
    except ImportError:  # pragma: no cover — packaging is a pip dep
        pytest.skip("packaging library not available — cannot check version constraints")
    specs = _pyproject_dep_specifiers()
    pins = _lockfile_pinned_versions()
    violations: list[str] = []
    for name, spec_str in sorted(specs.items()):
        pin = pins.get(name)
        if pin is None:
            # Completeness is checked by test_every_direct_dep_is_pinned_in_lockfile.
            continue
        try:
            spec = SpecifierSet(spec_str)
            ver = Version(pin)
        except Exception as exc:  # pragma: no cover — defensive parse
            violations.append(f"  {name}: failed to parse spec={spec_str!r} or pin={pin!r} ({exc})")
            continue
        if ver not in spec:
            violations.append(f"  {name}: lockfile pins {pin} but pyproject.toml requires {spec_str}")
    assert not violations, (
        "Lockfile drift detected — the lockfile pin violates the version "
        "specifier declared in pyproject.toml. CI (which resolves from "
        "pyproject.toml) and the documented `pip install --require-hashes "
        "-r requirements-lock.txt` path now install DIFFERENT versions. "
        "Regenerate the lockfile with:\n"
        "  uv pip compile --generate-hashes --universal --python-version 3.12 "
        "pyproject.toml -o requirements-lock.txt\n\nViolations:\n" + "\n".join(violations)
    )


def test_lockfile_psutil_pin_matches_pyproject_constraint() -> None:
    """Sentinel test for the psutil major-version drift class.

    Specifically guards against the regression: lockfile pinned
    psutil==6.1.1 while pyproject allowed >=5.9,<8.0 (so 6.1.1 was
    technically valid) BUT the live venv had 7.2.2 — the lockfile and
    the venv silently diverged because CI resolved from pyproject.toml
    (picking 7.2.2) while the documented reproducible-build path used
    the lockfile (picking 6.1.1). The lockfile has since been
    regenerated to psutil==7.2.2; this test catches any future
    divergence by asserting the lockfile pin matches the live venv's
    installed psutil version.
    """
    try:
        import psutil
    except ImportError:  # pragma: no cover — psutil is a hard dep
        pytest.skip("psutil not installed in this environment")
    installed = psutil.__version__
    pins = _lockfile_pinned_versions()
    pinned = pins.get("psutil")
    assert pinned is not None, (
        "psutil is missing from requirements-lock.txt — the reproducible-build "
        "path would crash with ModuleNotFoundError on the first "
        "`_another_voice_typer_alive` call."
    )
    assert pinned == installed, (
        f"psutil version drift: requirements-lock.txt pins psutil=={pinned} "
        f"but the live venv has psutil=={installed}. CI resolves from "
        f"pyproject.toml (picking {installed}) while the documented "
        f"`pip install --require-hashes -r requirements-lock.txt` path "
        f"would install {pinned}. Regenerate the lockfile with "
        f"`uv pip compile --generate-hashes --universal "
        f"--python-version 3.12 pyproject.toml -o requirements-lock.txt`."
    )


if __name__ == "__main__":
    # Manual run: ``python tests/test_requirements_lock_completeness.py``
    test_every_direct_dep_is_pinned_in_lockfile()
    test_known_critical_deps_are_pinned()
    test_lockfile_pinned_versions_satisfy_pyproject_constraints()
    test_lockfile_psutil_pin_matches_pyproject_constraint()
    print("OK: all direct deps are pinned in requirements-lock.txt (no drift)")
