"""regression tests for the Windows Electron installer PyInstaller bundling.

The d-review found that CI builds the Python backend (PyInstaller) for
Windows but NOT the Electron UI — the Windows installer contained only
Python, with no Electron shell. Dev-mode from source worked, but
end-users installing the released Windows package got a broken app
(ship-blocker).

These tests pin the three pieces that close the gap:

1. ``voice_typer/client/electron-builder.yml`` has an ``extraResources``
   entry (top-level or under ``win:``) that bundles the PyInstaller
   backend output dir into the installer's ``resources/`` folder.
2. ``voice_typer/client/src/main/index.ts`` ``pythonArgs()`` looks for
   the bundled backend at
   ``<resourcesPath>/voice-typer-backend/VoiceTyper.exe`` before
   falling back to the dev-mode venv.
3. ``.github/workflows/build.yml`` ``build-windows`` job runs
   ``electron-builder --win`` AFTER the PyInstaller step (so the
   backend exists when electron-builder copies it into the installer),
   and uploads the resulting NSIS installer as an artifact.
"""

from __future__ import annotations

import pathlib
import re
from typing import Any

import pytest
import yaml

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
ELECTRON_BUILDER_YML = REPO_ROOT / "voice_typer" / "client" / "electron-builder.yml"
BUILD_YML = REPO_ROOT / ".github" / "workflows" / "build.yml"
INDEX_TS = REPO_ROOT / "voice_typer" / "client" / "src" / "main" / "index.ts"
# REF-2 extracted pythonArgs() from index.ts into main/python/python-args.ts
# (re-exported from the `python` module); the packaged-mode lookup lives in
# its resolveBundledBackend helper.
PYTHON_ARGS_TS = REPO_ROOT / "voice_typer" / "client" / "src" / "main" / "python" / "python-args.ts"


def _load_yaml(path: pathlib.Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    assert isinstance(data, dict), f"{path} did not parse to a dict"
    return data


def _extra_resources_entries(config: dict[str, Any]) -> list[dict[str, Any]]:
    """Return ``extraResources`` entries from both top-level and ``win:``.

    electron-builder accepts ``extraResources`` either at the top level
    (applies to all platforms) or per-platform under ``win:``/``mac:``/
    ``linux:``. puts it under ``win:`` so the PyInstaller backend
    is bundled only for the Windows installer (macOS/Linux have their
    own sub-agents), but we accept either form for robustness.
    """
    entries: list[dict[str, Any]] = []
    top = config.get("extraResources")
    if top:
        entries.extend(top if isinstance(top, list) else [top])
    win = config.get("win") or {}
    assert isinstance(win, dict), "win: section must be a dict"
    win_extra = win.get("extraResources")
    if win_extra:
        entries.extend(win_extra if isinstance(win_extra, list) else [win_extra])
    return entries


def test_electron_builder_yml_has_win_section() -> None:
    """the win: section must exist (electron-builder needs it)."""
    config = _load_yaml(ELECTRON_BUILDER_YML)
    assert "win" in config, (
        "electron-builder.yml is missing the `win:` section. The Windows NSIS target cannot be built without it."
    )
    win = config["win"]
    assert isinstance(win, dict), "`win:` must be a dict"
    targets = win.get("target")
    assert targets, "`win.target` must specify at least one target (e.g. nsis)"


def test_win_section_has_extra_resources_for_backend() -> None:
    """win.extraResources must bundle the PyInstaller backend dir."""
    config = _load_yaml(ELECTRON_BUILDER_YML)
    entries = _extra_resources_entries(config)
    assert entries, (
        "electron-builder.yml must define `extraResources` (top-level or "
        "under `win:`) to bundle the PyInstaller backend in the Windows "
        "installer. Without it, the installer ships Electron only with no "
        "Python backend — ship-blocker for Windows users."
    )
    # At least one entry must point at the PyInstaller backend output dir.
    # CI runs `pyinstaller --distpath voice_typer/dist` from the repo root
    # (Wave 3 path-consistency fix — matches macOS/Linux), so the PyInstaller
    # output lands at <repo>/voice_typer/dist/. electron-builder runs from
    # voice_typer/client/, so the `from:` field is `../dist` (or equivalent).
    # The `to:` field is `voice-typer-backend` so the bundle lands at
    # <resourcesPath>/voice-typer-backend/ at install time.
    matched: dict[str, Any] | None = None
    for entry in entries:
        from_path = str(entry.get("from", ""))
        to_path = str(entry.get("to", ""))
        if "dist" in from_path and "voice-typer-backend" in to_path:
            matched = entry
            break
    assert matched is not None, (
        "No `extraResources` entry bundles the PyInstaller backend. "
        "Expected an entry with `from: ../dist` (or similar — must "
        "reference the PyInstaller `dist/` output dir) and "
        "`to: voice-typer-backend`. Got entries: "
        f"{entries}"
    )


def test_win_section_keeps_nsis_target() -> None:
    """the win: target must remain nsis (we did not change it)."""
    config = _load_yaml(ELECTRON_BUILDER_YML)
    win = config.get("win") or {}
    targets = win.get("target") or []
    # Accept both list-of-strings and list-of-dicts forms.
    flat: list[str] = []
    for t in targets if isinstance(targets, list) else [targets]:
        if isinstance(t, str):
            flat.append(t)
        elif isinstance(t, dict):
            tname = t.get("target") or t.get("name")
            if isinstance(tname, str):
                flat.append(tname)
    assert "nsis" in flat, (
        f"win.target must include 'nsis' (the NSIS installer produces the .exe installer users run). Got: {flat}"
    )


def test_index_ts_pythonargs_looks_up_embedded_backend() -> None:
    """pythonArgs() must check resourcesPath/voice-typer-backend.

    REF-2 extracted ``pythonArgs()`` from ``index.ts`` into
    ``main/python/python-args.ts`` (re-exported from the ``python``
    module) — the packaged-mode lookup lives in its
    ``resolveBundledBackend`` helper. This test therefore reads the
    extracted module.
    """
    src = PYTHON_ARGS_TS.read_text(encoding="utf-8")
    # pythonArgs() must exist and delegate the packaged-mode lookup to
    # resolveBundledBackend.
    m = re.search(
        r"^export function pythonArgs\(\).*?^}$",
        src,
        re.DOTALL | re.MULTILINE,
    )
    assert m, "pythonArgs() function not found in python-args.ts"
    assert "resolveBundledBackend" in m.group(0), (
        "pythonArgs() must delegate the packaged-mode lookup to resolveBundledBackend"
    )

    # Must reference process.resourcesPath (the Electron packaged-resources
    # dir where electron-builder's extraResources lands) — inside the
    # resolveBundledBackend helper.
    assert "process.resourcesPath" in src, (
        "pythonArgs() must check process.resourcesPath for the embedded PyInstaller backend (packaged mode)."
    )
    # Must reference the voice-typer-backend subdirectory (matches the
    # `to: voice-typer-backend` in electron-builder.yml's extraResources).
    assert "voice-typer-backend" in src, (
        "pythonArgs() must look for the backend under the "
        "`voice-typer-backend` subdirectory of process.resourcesPath "
        "(matches electron-builder.yml's `extraResources.to`)."
    )
    # Must be Windows-gated (either `case "win32":` or a
    # `platform === "win32"` branch — the helper's platform param).
    assert 'case "win32"' in src or 'platform === "win32"' in src, (
        "pythonArgs() packaged-mode lookup must be Windows-gated so macOS/Linux branches are not affected."
    )
    # Must spawn the bundled exe with --port (no -m flag — the frozen
    # exe is already the IPC server entry point).
    assert '"--port"' in src or "'--port'" in src, (
        "pythonArgs() must pass `--port <N>` to the bundled backend so "
        "ipc_server.main() binds the TCP listener (no `-m` flag — the "
        "frozen exe already imports voice_typer.server.ipc_server)."
    )


def test_build_windows_job_runs_electron_builder_win_after_pyinstaller() -> None:
    """build-windows must run `electron-builder --win` after PyInstaller."""
    config = _load_yaml(BUILD_YML)
    jobs = config.get("jobs") or {}
    build_windows = jobs.get("build-windows")
    assert build_windows is not None, "build-windows job missing from .github/workflows/build.yml"
    assert isinstance(build_windows, dict)
    steps = build_windows.get("steps") or []
    assert steps, "build-windows job has no steps"

    # Walk the steps in order; track when we see the PyInstaller step
    # and when we see the electron-builder --win step. The latter MUST
    # come after the former (PyInstaller produces the backend that
    # electron-builder bundles into the installer).
    seen_pyinstaller = False
    seen_electron_builder_win = False
    for step in steps:
        if not isinstance(step, dict):
            continue
        run = step.get("run", "")
        if not isinstance(run, str):
            continue
        # The PyInstaller step runs `pyinstaller scripts/build/voice-typer.spec ...`.
        if "pyinstaller" in run and "voice-typer.spec" in run:
            seen_pyinstaller = True
        # The electron-builder step runs `electron-builder --win ...`.
        if "electron-builder" in run and "--win" in run:
            assert seen_pyinstaller, (
                "The `electron-builder --win` step must come AFTER the "
                "PyInstaller step in build-windows (PyInstaller produces "
                "dist/VoiceTyper.exe which electron-builder bundles into "
                "the NSIS installer via win.extraResources)."
            )
            seen_electron_builder_win = True

    assert seen_pyinstaller, (
        "build-windows job is missing the PyInstaller step (expected `pyinstaller scripts/build/voice-typer.spec ...`)."
    )
    assert seen_electron_builder_win, (
        "build-windows job is missing the `electron-builder --win` step. "
        "Without it, the Windows installer contains only the PyInstaller "
        "backend (no Electron UI) — ship-blocker. See RW-4."
    )


def test_build_windows_job_uploads_electron_installer_artifact() -> None:
    """build-windows must upload the NSIS installer as an artifact."""
    config = _load_yaml(BUILD_YML)
    jobs = config.get("jobs") or {}
    build_windows = jobs.get("build-windows") or {}
    assert isinstance(build_windows, dict)
    steps = build_windows.get("steps") or []

    # Find an upload-artifact step whose `path` references the
    # electron-builder output (voice_typer/client/dist/*-setup.exe).
    found = False
    for step in steps:
        if not isinstance(step, dict):
            continue
        uses = step.get("uses", "")
        if not isinstance(uses, str):
            continue
        if not uses.startswith("actions/upload-artifact"):
            continue
        with_cfg = step.get("with") or {}
        if not isinstance(with_cfg, dict):
            continue
        path = str(with_cfg.get("path", ""))
        # electron-builder's NSIS artifactName is
        # `${productName}-${version}-${arch}-setup.${ext}` → matches
        # `*-setup.exe`. The output dir is `voice_typer/client/dist/`.
        if "voice_typer/client/dist" in path and "setup" in path.lower():
            found = True
            break
    assert found, (
        "build-windows job must upload the electron-builder NSIS installer "
        "as an artifact (expected an `actions/upload-artifact` step with "
        "`path: voice_typer/client/dist/*-setup.exe` or similar). Without "
        "this, CI builds the installer but never publishes it."
    )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
