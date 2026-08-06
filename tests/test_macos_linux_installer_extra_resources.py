"""regression tests for macOS/Linux installers embedding the Python backend.

Confirmed gap (d-review): CI built the Electron UI for macOS/Linux but NOT the
Python backend. The macOS/Linux installers contained only Electron — ship-blocker
for macOS/Linux users (the dev venv doesn't exist on a fresh install).

This module verifies the three wiring points that close the gap:

1. ``voice_typer/client/electron-builder.yml`` — both the ``mac:`` and ``linux:``
   sections declare an ``extraResources`` entry that pulls the PyInstaller-built
   backend into the packaged app.

2. ``.github/workflows/build.yml`` — both the ``build-macos`` and ``build-linux``
   jobs run a PyInstaller step (using ``scripts/build/voice-typer.spec``) AND an
   ``electron-builder --mac``/``--linux`` step, so the backend is actually built
   and packaged into the installer.

3. ``voice_typer/client/src/main/python/python-args.ts`` — ``pythonArgs()``
   (extracted from ``index.ts``) looks up the embedded backend
   under ``process.resourcesPath`` for macOS and Linux before falling back
   to the dev-mode venv.
"""

from __future__ import annotations

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
ELECTRON_BUILDER_YML = REPO_ROOT / "voice_typer" / "client" / "electron-builder.yml"
BUILD_YML = REPO_ROOT / ".github" / "workflows" / "build.yml"
# pythonArgs() was extracted from index.ts into python/python-args.ts
# during the wiring-only split; the contract (embedded backend
# lookup under process.resourcesPath + dev-venv fallback) is unchanged.
INDEX_TS = REPO_ROOT / "voice_typer" / "client" / "src" / "main" / "python" / "python-args.ts"


def _load_yml(path: Path):
    """Load a YAML file (returns dict-like)."""
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f)


def _steps_blob(steps: list[dict]) -> str:
    """Flatten a job's steps into a single searchable string.

    Includes both step names and the ``run``/``with``/``env`` payloads so a
    test can grep for substrings regardless of which field they live in.
    Wave 3 added ``env`` to the blob so code-signing env-var tests
    (WIN_CSC_LINK, MAC_SIGNING_IDENTITY, etc.) can be asserted.
    """
    blob = ""
    for step in steps:
        blob += step.get("name", "") + "\n"
        run = step.get("run", "")
        if isinstance(run, str):
            blob += run + "\n"
        with_data = step.get("with", {})
        if isinstance(with_data, dict):
            for value in with_data.values():
                blob += str(value) + "\n"
        env_data = step.get("env", {})
        if isinstance(env_data, dict):
            for key, value in env_data.items():
                blob += str(key) + "\n" + str(value) + "\n"
    return blob


# ---------------------------------------------------------------------------
# electron-builder.yml — mac: + linux: extraResources
# ---------------------------------------------------------------------------


class TestElectronBuilderHasMacExtraResources:
    """electron-builder.yml mac: section embeds the PyInstaller backend."""

    def test_mac_section_exists(self):
        cfg = _load_yml(ELECTRON_BUILDER_YML)
        assert "mac" in cfg, "electron-builder.yml must have a mac: section"

    def test_mac_has_extra_resources(self):
        cfg = _load_yml(ELECTRON_BUILDER_YML)
        mac = cfg["mac"]
        assert "extraResources" in mac, "mac: section must declare extraResources to embed the backend"
        assert isinstance(mac["extraResources"], list)
        assert len(mac["extraResources"]) > 0

    def test_mac_extra_resources_references_backend(self):
        cfg = _load_yml(ELECTRON_BUILDER_YML)
        mac = cfg["mac"]
        found = any(
            "voice-typer-backend" in str(entry.get("from", "")) or "voice-typer-backend" in str(entry.get("to", ""))
            for entry in mac["extraResources"]
        )
        assert found, f"mac: extraResources must reference voice-typer-backend; got: {mac['extraResources']}"

    def test_mac_extra_resources_references_app_bundle(self):
        """macOS PyInstaller output is a .app bundle — extraResources must
        reference the .app directory, not a bare executable."""
        cfg = _load_yml(ELECTRON_BUILDER_YML)
        mac = cfg["mac"]
        blob = str(mac["extraResources"])
        assert "voice-typer-backend.app" in blob, (
            "mac: extraResources must reference voice-typer-backend.app (PyInstaller produces a .app bundle on macOS)"
        )


class TestElectronBuilderHasLinuxExtraResources:
    """electron-builder.yml linux: section embeds the PyInstaller backend."""

    def test_linux_section_exists(self):
        cfg = _load_yml(ELECTRON_BUILDER_YML)
        assert "linux" in cfg, "electron-builder.yml must have a linux: section"

    def test_linux_has_extra_resources(self):
        cfg = _load_yml(ELECTRON_BUILDER_YML)
        linux = cfg["linux"]
        assert "extraResources" in linux, "linux: section must declare extraResources to embed the backend"
        assert isinstance(linux["extraResources"], list)
        assert len(linux["extraResources"]) > 0

    def test_linux_extra_resources_references_backend(self):
        cfg = _load_yml(ELECTRON_BUILDER_YML)
        linux = cfg["linux"]
        found = any(
            "voice-typer-backend" in str(entry.get("from", "")) or "voice-typer-backend" in str(entry.get("to", ""))
            for entry in linux["extraResources"]
        )
        assert found, f"linux: extraResources must reference voice-typer-backend; got: {linux['extraResources']}"


class TestElectronBuilderWindowsSectionUntouched:
    """Sanity: macOS/Linux extraResources entries must NOT leak into
    the win: section. The Windows installer is owned by sub-agent
    windows-installer (which may add its own win: extraResources with
    a different `from` path — that's their territory).

    We can't assert "win: has no extraResources" because may add one.
    Instead we verify the path tokens (voice-typer-backend.app
    and the ../dist/voice-typer-backend Linux path) are NOT in win:.
    """

    def test_win_section_does_not_have_macos_backend_path(self):
        cfg = _load_yml(ELECTRON_BUILDER_YML)
        win = cfg.get("win", {}) or {}
        win_blob = str(win)
        assert "voice-typer-backend.app" not in win_blob, (
            "win: section must NOT contain the macOS .app path — that's "
            "mac:-only entry. windows-installer owns win:."
        )

    def test_win_section_does_not_have_linux_backend_path(self):
        cfg = _load_yml(ELECTRON_BUILDER_YML)
        win = cfg.get("win", {}) or {}
        # The Linux entry uses `from: ../dist/voice-typer-backend` (no .app).
        # If adds a win: extraResources, it would use a Windows-style
        # path (e.g. ../../dist/VoiceTyper), not the Linux token.
        for entry in win.get("extraResources", []) or []:
            from_val = str(entry.get("from", ""))
            # Reject ONLY the exact Linux token "../dist/voice-typer-backend"
            # (the macOS token "../dist/voice-typer-backend.app" is already
            # rejected above). Windows path will look different.
            assert from_val != "../dist/voice-typer-backend", (
                "win: extraResources must NOT use the Linux path "
                "'../dist/voice-typer-backend' — that's linux:-only "
                "entry."
            )


# ---------------------------------------------------------------------------
# .github/workflows/build.yml — build-macos + build-linux jobs
# ---------------------------------------------------------------------------


class TestBuildYmlMacOsJobEmbedsBackend:
    """build.yml build-macos job runs PyInstaller + electron-builder."""

    @staticmethod
    def _macos_job():
        cfg = _load_yml(BUILD_YML)
        jobs = cfg["jobs"]
        assert "build-macos" in jobs, "build.yml must have a build-macos job"
        return jobs["build-macos"]

    def test_build_macos_job_exists(self):
        self._macos_job()

    def test_build_macos_has_pyinstaller_step(self):
        job = self._macos_job()
        blob = _steps_blob(job["steps"])
        assert "pyinstaller" in blob.lower(), (
            "build-macos must run PyInstaller (the d-review gap was that CI built Electron UI only, no Python backend)"
        )
        assert "voice-typer.spec" in blob, "build-macos must use scripts/build/voice-typer.spec"

    def test_build_macos_has_electron_builder_mac_step(self):
        job = self._macos_job()
        blob = _steps_blob(job["steps"])
        assert "electron-builder" in blob, "build-macos must run electron-builder"
        assert "--mac" in blob, "build-macos must pass --mac to electron-builder"

    def test_build_macos_uploads_dmg_artifact(self):
        job = self._macos_job()
        blob = _steps_blob(job["steps"])
        assert ".dmg" in blob, "build-macos must upload a .dmg artifact"

    def test_build_macos_stages_backend_for_embedding(self):
        """build-macos must have a step that stages the PyInstaller output
        into the voice-typer-backend.app/Contents/MacOS/voice-typer layout
        that pythonArgs() expects."""
        job = self._macos_job()
        blob = _steps_blob(job["steps"])
        assert "voice-typer-backend.app" in blob, (
            "build-macos must stage PyInstaller output as voice-typer-backend.app for embedding via extraResources"
        )

    def test_build_macos_pyinstaller_distpath_matches_extra_resources(self):
        """PyInstaller --distpath must point to voice_typer/dist so the
        mac.extraResources `from: ../dist/voice-typer-backend.app` (relative
        to voice_typer/client/) resolves correctly."""
        job = self._macos_job()
        blob = _steps_blob(job["steps"])
        assert "voice_typer/dist" in blob, (
            "build-macos PyInstaller --distpath must be voice_typer/dist so "
            "mac.extraResources `from: ../dist/voice-typer-backend.app` "
            "resolves to voice_typer/dist/voice-typer-backend.app"
        )


class TestBuildYmlLinuxJobEmbedsBackend:
    """build.yml build-linux job runs PyInstaller + electron-builder."""

    @staticmethod
    def _linux_job():
        cfg = _load_yml(BUILD_YML)
        jobs = cfg["jobs"]
        assert "build-linux" in jobs, "build.yml must have a build-linux job"
        return jobs["build-linux"]

    def test_build_linux_job_exists(self):
        self._linux_job()

    def test_build_linux_has_pyinstaller_step(self):
        job = self._linux_job()
        blob = _steps_blob(job["steps"])
        assert "pyinstaller" in blob.lower(), (
            "build-linux must run PyInstaller (the d-review gap was that CI built Electron UI only, no Python backend)"
        )
        assert "voice-typer.spec" in blob, "build-linux must use scripts/build/voice-typer.spec"

    def test_build_linux_has_electron_builder_linux_step(self):
        job = self._linux_job()
        blob = _steps_blob(job["steps"])
        assert "electron-builder" in blob, "build-linux must run electron-builder"
        assert "--linux" in blob, "build-linux must pass --linux to electron-builder"

    def test_build_linux_uploads_deb_or_appimage_artifact(self):
        job = self._linux_job()
        blob = _steps_blob(job["steps"])
        assert ".deb" in blob or ".AppImage" in blob, "build-linux must upload .deb and/or .AppImage artifact"

    def test_build_linux_stages_backend_for_embedding(self):
        """build-linux must have a step that stages the PyInstaller output
        into the voice-typer-backend/voice-typer layout that pythonArgs()
        expects."""
        job = self._linux_job()
        blob = _steps_blob(job["steps"])
        assert "voice-typer-backend" in blob, (
            "build-linux must stage PyInstaller output as voice-typer-backend/ for embedding via extraResources"
        )

    def test_build_linux_pyinstaller_distpath_matches_extra_resources(self):
        """PyInstaller --distpath must point to voice_typer/dist so the
        linux.extraResources `from: ../dist/voice-typer-backend` (relative
        to voice_typer/client/) resolves correctly."""
        job = self._linux_job()
        blob = _steps_blob(job["steps"])
        assert "voice_typer/dist" in blob, (
            "build-linux PyInstaller --distpath must be voice_typer/dist so "
            "linux.extraResources `from: ../dist/voice-typer-backend` "
            "resolves to voice_typer/dist/voice-typer-backend"
        )


# ---------------------------------------------------------------------------
# index.ts — pythonArgs() macOS + Linux embedded-backend branches
# ---------------------------------------------------------------------------


class TestPythonArgsLooksUpEmbeddedBackend:
    """pythonArgs() (python/python-args.ts) has macOS + Linux embedded-backend branches."""

    @staticmethod
    def _index_ts() -> str:
        return INDEX_TS.read_text(encoding="utf-8")

    def test_python_args_function_exists(self):
        src = self._index_ts()
        assert "function pythonArgs" in src, "pythonArgs() must exist"

    def test_python_args_uses_resources_path(self):
        src = self._index_ts()
        assert "process.resourcesPath" in src, "pythonArgs() must use process.resourcesPath for embedded lookup"

    def test_python_args_uses_platform_switch(self):
        src = self._index_ts()
        assert "process.platform" in src
        # coordination: each platform must have its OWN guarded branch
        # (independent, so adding a platform can't clobber the others).
        # The REF-2 implementation uses per-platform if/else-if guards in
        # resolveBundledBackend rather than a literal ``switch``.
        assert 'platform === "darwin"' in src, "pythonArgs() must have an independent darwin branch"
        assert 'platform === "linux"' in src, "pythonArgs() must have an independent linux branch"
        assert 'platform === "win32"' in src, "pythonArgs() must have an independent win32 branch"

    def test_python_args_has_macos_branch(self):
        """macOS: checks process.resourcesPath/voice-typer-backend.app/
        Contents/MacOS/voice-typer."""
        src = self._index_ts()
        assert "voice-typer-backend.app" in src, "pythonArgs() must reference voice-typer-backend.app for macOS"
        assert "Contents" in src, "pythonArgs() must reference the .app Contents/ subdir for macOS"
        assert "MacOS" in src, (
            "pythonArgs() must reference the .app Contents/MacOS/ subdir where the PyInstaller executable lives"
        )
        # The darwin case must be present in the switch.
        assert '"darwin"' in src or "'darwin'" in src, 'pythonArgs() switch must have a case "darwin" branch'

    def test_python_args_has_linux_branch(self):
        """Linux: checks process.resourcesPath/voice-typer-backend/voice-typer."""
        src = self._index_ts()
        # The Linux branch references the voice-typer-backend directory and
        # the voice-typer executable inside it.
        assert '"voice-typer-backend"' in src or "'voice-typer-backend'" in src, (
            "pythonArgs() must reference the voice-typer-backend/ directory for Linux"
        )
        assert '"voice-typer"' in src or "'voice-typer'" in src, (
            "pythonArgs() must reference the voice-typer executable inside the Linux bundle directory"
        )
        assert '"linux"' in src or "'linux'" in src, 'pythonArgs() switch must have a case "linux" branch'

    def test_python_args_falls_back_to_venv(self):
        """pythonArgs() must still fall back to the dev-mode venv when the
        embedded backend is not present (dev mode, missing bundle, etc.)."""
        src = self._index_ts()
        assert "venv" in src, "pythonArgs() must fall back to the dev venv in dev mode"
        assert "computeConfigDir" in src, "venv fallback must use computeConfigDir()"
        # The -m voice_typer.server.ipc_server module invocation must remain
        # for the venv fallback path (PyInstaller bundle doesn't need it).
        assert "voice_typer.server.ipc_server" in src, (
            "venv fallback must still launch voice_typer.server.ipc_server "
            "via -m (PyInstaller bundle is launched directly, no -m)"
        )

    def test_python_args_passes_port_to_embedded_backend(self):
        """The embedded backend (PyInstaller bundle) must receive --port so
        Electron can connect to it over TCP. The spec's entry point is
        voice_typer/server/ipc_server.py (Wave 3 fix — previously
        voice_typer/__main__.py, which used strict parse_args() and would
        REJECT --port). ipc_server.main() uses parse_known_args() so
        --port is honored and any unknown args are silently dropped."""
        src = self._index_ts()
        # Both branches must pass --port to the bundle (the embedded-backend
        # returns contain ["--port", String(IPC_PORT)]).
        assert '"--port"' in src or "'--port'" in src, (
            "pythonArgs() embedded-backend branches must pass --port to the "
            "PyInstaller bundle so Electron can connect over TCP"
        )


# ---------------------------------------------------------------------------
# YAML validity — make sure we didn't break the file structure
# ---------------------------------------------------------------------------


class TestYamlStillParses:
    """Both YAML files must still parse cleanly after edits."""

    def test_electron_builder_yml_parses(self):
        cfg = _load_yml(ELECTRON_BUILDER_YML)
        assert isinstance(cfg, dict)
        assert "mac" in cfg
        assert "linux" in cfg

    def test_build_yml_parses(self):
        cfg = _load_yml(BUILD_YML)
        assert isinstance(cfg, dict)
        assert "jobs" in cfg
        assert "build-macos" in cfg["jobs"]
        assert "build-linux" in cfg["jobs"]


# ---------------------------------------------------------------------------
# Wave 3 — cross-platform path consistency
# ---------------------------------------------------------------------------


class TestWave3PathConsistency:
    """Wave 3 installer review: all 3 platforms must use the SAME PyInstaller
    --distpath base so the electron-builder.yml `extraResources.from:` fields
    resolve consistently.

    Pre-Wave-3 bug: Windows used `--distpath dist` (→ <repo>/dist/) while
    macOS/Linux used `--distpath voice_typer/dist` (→ <repo>/voice_typer/dist/).
    The win.extraResources `from: ../../dist` and mac/linux `from: ../dist/...`
    were off-by-one in directory depth, making cross-platform maintenance
    fragile.

    Post-Wave-3: all 3 platforms use `--distpath voice_typer/dist`, and all
    `extraResources.from:` paths are `../dist...` (relative to
    `voice_typer/client/` where electron-builder runs).
    """

    def test_all_platforms_use_same_distpath_base(self):
        """build.yml: build-windows, build-macos, build-linux must all pass
        `--distpath voice_typer/dist` to pyinstaller."""
        cfg = _load_yml(BUILD_YML)
        jobs = cfg["jobs"]
        for job_name in ("build-windows", "build-macos", "build-linux"):
            assert job_name in jobs, f"{job_name} missing from build.yml"
            blob = _steps_blob(jobs[job_name]["steps"])
            assert "--distpath voice_typer/dist" in blob, (
                f"{job_name} must pass `--distpath voice_typer/dist` to "
                f"pyinstaller (Wave 3 path-consistency fix). Got blob: {blob[:500]}"
            )

    def test_all_platforms_extra_resources_from_uses_same_depth(self):
        """electron-builder.yml: win/mac/linux extraResources `from:` must all
        use `../dist` (relative to voice_typer/client/, resolving to
        <repo>/voice_typer/dist/). Pre-Wave-3 Windows used `../../dist`
        (resolving to <repo>/dist/) — divergent from macOS/Linux."""
        cfg = _load_yml(ELECTRON_BUILDER_YML)
        for section in ("win", "mac", "linux"):
            assert section in cfg, f"{section}: section missing"
            entries = cfg[section].get("extraResources") or []
            assert entries, f"{section}: extraResources must not be empty"
            for entry in entries:
                from_val = str(entry.get("from", ""))
                # All `from:` paths must start with `../dist` (resolves to
                # <repo>/voice_typer/dist/ from voice_typer/client/).
                # Reject `../../dist` (resolves to <repo>/dist/ — divergent).
                assert from_val.startswith("../dist"), (
                    f"{section}: extraResources `from: {from_val}` must start "
                    f"with `../dist` (Wave 3 path-consistency). Got: {from_val}"
                )
                assert not from_val.startswith("../../dist"), (
                    f"{section}: extraResources `from: {from_val}` must NOT "
                    f"use `../../dist` (resolves to <repo>/dist/, divergent "
                    f"from macOS/Linux <repo>/voice_typer/dist/). Wave 3 fix."
                )


# ---------------------------------------------------------------------------
# Wave 3 — PyInstaller spec entry point must accept --port
# ---------------------------------------------------------------------------


class TestWave3SpecEntryPointAcceptsPort:
    """Wave 3 installer review: the PyInstaller spec entry point must use
    ipc_server.py (parse_known_args — accepts --port), NOT __main__.py
    (parse_args — rejects --port with exit code 2).

    Pre-Wave-3 bug: spec used voice_typer/__main__.py as the entry. The
    frozen exe would crash on launch with
    "error: unrecognized arguments: --port 9876" because __main__.py uses
    strict parse_args() and the parser doesn't define --port. Electron's
    pythonArgs() passes ["--port", String(IPC_PORT)] to the bundled exe —
    so the backend would never start the TCP listener (ship-blocker on
    all 3 platforms).
    """

    SPEC_PATH = REPO_ROOT / "scripts" / "build" / "voice-typer.spec"

    def test_spec_entry_point_is_ipc_server_py(self):
        """Spec Analysis() first arg must be voice_typer/server/ipc_server.py,
        NOT voice_typer/__main__.py."""
        src = self.SPEC_PATH.read_text(encoding="utf-8")
        assert "voice_typer" in src and "ipc_server.py" in src, "spec must reference voice_typer/server/ipc_server.py"
        # The Analysis() call's first arg must include ipc_server.py.
        # Find the Analysis( call and inspect its first argument.
        import re

        m = re.search(r"Analysis\(\s*\[([^\]]+)\]", src)
        assert m, "Analysis() call not found in spec"
        first_arg = m.group(1)
        assert "ipc_server.py" in first_arg, f"Analysis() first arg must reference ipc_server.py. Got: {first_arg}"
        assert "__main__.py" not in first_arg, (
            f"Analysis() first arg must NOT reference __main__.py (it uses "
            f"strict parse_args and rejects --port — Wave 3 ship-blocker). "
            f"Got: {first_arg}"
        )

    def test_spec_entry_point_not_main_module(self):
        """Spec must not use voice_typer/__main__.py as the entry point."""
        src = self.SPEC_PATH.read_text(encoding="utf-8")
        import re

        m = re.search(r"Analysis\(\s*\[([^\]]+)\]", src)
        assert m, "Analysis() call not found in spec"
        first_arg = m.group(1)
        # Reject the pre-Wave-3 entry: voice_typer/__main__.py.
        assert '"__main__.py"' not in first_arg, f"spec entry must not be __main__.py (Wave 3 fix). Got: {first_arg}"
        assert "/__main__.py" not in first_arg.replace("\\", "/"), (
            f"spec entry must not be __main__.py (Wave 3 fix). Got: {first_arg}"
        )


# ---------------------------------------------------------------------------
# Wave 3 — Windows code-signing env vars passed through in CI
# ---------------------------------------------------------------------------


class TestWave3WindowsCodeSigningEnvVars:
    """Wave 3: build-windows electron-builder step must pass through
    WIN_CSC_LINK / WIN_CSC_KEY_PASSWORD (and the generic CSC_LINK fallback)
    from GitHub Actions secrets so release builds can be code-signed.

    When the secrets are empty (PR builds, forks), electron-builder skips
    signing — the installer is still produced, just unsigned.
    """

    def test_build_windows_passes_win_csc_link_env(self):
        cfg = _load_yml(BUILD_YML)
        win_job = cfg["jobs"]["build-windows"]
        blob = _steps_blob(win_job["steps"])
        assert "WIN_CSC_LINK" in blob, (
            "build-windows electron-builder step must pass through "
            "WIN_CSC_LINK env var (Wave 3 code-signing fix). Release "
            "builds require this to produce a signed, SmartScreen-trusted "
            "installer."
        )
        assert "WIN_CSC_KEY_PASSWORD" in blob, (
            "build-windows electron-builder step must pass through "
            "WIN_CSC_KEY_PASSWORD env var (Wave 3 code-signing fix)."
        )
