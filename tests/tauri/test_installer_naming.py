"""
Installer-config tests for the slim-core / runtime-pack split.
C-CI-13 forbids RENAMING the existing artifacts; these are NEW names
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import stat
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_TAURI = PROJECT_ROOT / "src-tauri"
INSTALLER_HOOKS_NSH = PROJECT_ROOT / "scripts" / "windows" / "installer-hooks.nsh"
UNINSTALLER_NSH = PROJECT_ROOT / "scripts" / "windows" / "uninstaller.nsh"
FULL_OFFLINE_NSI = PROJECT_ROOT / "scripts" / "windows" / "full-offline-installer.nsi"
FULL_OFFLINE_BUILD_SH = PROJECT_ROOT / "scripts" / "build" / "build_full_offline_installer_windows.sh"
ARTIFACT_NAMES_PY = PROJECT_ROOT / "scripts" / "build" / "artifact_names.py"
STUB_SCRIPT = PROJECT_ROOT / "scripts" / "gen_tauri_icons_stub.py"


def _strip_nsis_comments(text: str) -> str:
    """
    Strip ``;``-comments from NSIS source so directive checks don't
    SectionIn RO" must NOT trigger the SectionIn RO directive check).
    """
    out_lines: list[str] = []
    for line in text.splitlines():
        idx = line.find(";")
        if idx >= 0:
            line = line[:idx]
        out_lines.append(line)
    return "\n".join(out_lines)


def _tauri_conf() -> dict:
    return json.loads((SRC_TAURI / "tauri.conf.json").read_text(encoding="utf-8"))


def _installer_hooks_list() -> list[str]:
    """Return the ``bundle.windows.nsis.installerHooks`` entries as a list."""
    nsis = _tauri_conf()["bundle"]["windows"]["nsis"]
    hooks = nsis.get("installerHooks", [])
    if isinstance(hooks, str):
        hooks = [hooks]
    return list(hooks)


# File-existence gates, these scripts are owned by a separate workstream
_MISSING_ARTIFACT_NAMES_RSN = (
    f"scripts/build/artifact_names.py not present at {ARTIFACT_NAMES_PY}, "
    "this file is the §11.9 naming-contract source of truth and is owned by "
    "the installer-build workstream. Skipping until the file lands."
)
_MISSING_FULL_OFFLINE_BUILD_SH_RSN = (
    f"scripts/build/build_full_offline_installer_windows.sh not present at "
    f"{FULL_OFFLINE_BUILD_SH}, this script is owned by the installer-build "
    "workstream (it wires makensis + the .nsi template + the §11.9 "
    "artifact-name module). Skipping until the file lands."
)


def _skip_if_missing(path: Path, reason: str) -> None:
    """Skip the calling test when ``path`` does not exist."""
    if not path.is_file():
        pytest.skip(reason)


def _load_artifact_names_module():
    _skip_if_missing(ARTIFACT_NAMES_PY, _MISSING_ARTIFACT_NAMES_RSN)
    spec = importlib.util.spec_from_file_location("_vt_artifact_names_test", ARTIFACT_NAMES_PY)
    assert spec is not None and spec.loader is not None, f"cannot load {ARTIFACT_NAMES_PY}"
    module = importlib.util.module_from_spec(spec)
    # Register in sys.modules BEFORE exec so ``@dataclass`` can resolve
    sys.modules["_vt_artifact_names_test"] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop("_vt_artifact_names_test", None)
    return module


class TestInstallerHooksNshRegistered:
    """``installer-hooks.nsh`` must be reachable via ``installerHooks``."""

    def test_installer_hooks_is_a_single_registered_nsh(self) -> None:
        nsis = _tauri_conf()["bundle"]["windows"]["nsis"]
        hooks = nsis.get("installerHooks")
        assert isinstance(hooks, str) and hooks, (
            "bundle.windows.nsis.installerHooks must be a single .nsh path "
            "string (tauri-utils deserializes it as Option<PathBuf>, a "
            "list fails the config parse, breaking the build)."
        )
        assert hooks.endswith(".nsh"), (
            f"installerHooks entry {hooks!r} must be an NSIS script (.nsh), "
            "Tauri !includes it into installer.nsi; a non-NSIS file aborts "
            "makensis."
        )
        # The path in tauri.conf.json is relative to src-tauri/ (Tauri's
        target = (SRC_TAURI / hooks).resolve()
        assert target.is_file(), f"installerHooks entry {hooks!r} does not exist at {target}."

    def test_wrapper_includes_both_install_and_uninstall_hooks(self) -> None:
        """The registered wrapper must compose BOTH hook files."""
        nsis = _tauri_conf()["bundle"]["windows"]["nsis"]
        hooks = nsis.get("installerHooks")
        assert isinstance(hooks, str) and hooks, "installerHooks must be set."
        wrapper = (SRC_TAURI / hooks).resolve()
        text = wrapper.read_text(encoding="utf-8")
        hook_names = {Path(h).name for h in _installer_hooks_list()}
        assert "uninstaller.nsh" in text, (
            "the hooks wrapper must !include uninstaller.nsh, defines "
            "customUnInstall for CR-69/CR-70 cleanup (autostart Run keys, "
            "Task Scheduler tasks, %APPDATA%\\lausu data dir)."
        )
        assert "installer-hooks.nsh" in text, (
            "the hooks wrapper must !include installer-hooks.nsh, defines "
            "the Include offline engine pack checkbox Section and the "
            "customInstall macro that writes installer-state.json (plan "
            "§4.8 consent gate)."
        )
        assert hook_names, "installerHooks must resolve to at least one .nsh."

    def test_every_hook_ends_in_nsh_and_exists(self) -> None:
        """Mirror the contract in TestTauriNsisInstallerHooks for new entries."""
        for hook in _installer_hooks_list():
            assert hook.endswith(".nsh"), (
                f"installerHooks entry {hook!r} must be an NSIS script (.nsh), "
                "Tauri !includes it into installer.nsi; a non-NSIS file aborts "
                "makensis."
            )
            # The path in tauri.conf.json is relative to src-tauri/ (Tauri's
            target = (SRC_TAURI / hook).resolve()
            assert target.is_file(), f"installerHooks entry {hook!r} does not exist at {target}."


class TestInstallerHooksNshSection:
    """The .nsh must define the \"Include offline engine pack\" Section."""

    def test_section_definition_present(self) -> None:
        text = INSTALLER_HOOKS_NSH.read_text(encoding="utf-8")
        # The Section line uses the canonical name + section index var.
        assert 'Section "Include offline engine pack" SecIncludePack' in text, (
            'installer-hooks.nsh must define `Section "Include offline engine pack" '
            "SecIncludePack`, Tauri v2's bundle.windows.nsis has NO checkbox option, "
            "so a custom NSIS Section is the only way to surface the per-feature "
            "checkbox on the Components page (plan §4.8 / §9)."
        )

    def test_section_is_optional_not_read_only(self) -> None:
        """The Section must NOT be marked ``SectionIn RO`` (read-only)."""
        text = _strip_nsis_comments(INSTALLER_HOOKS_NSH.read_text(encoding="utf-8"))
        # Extract the Section ... SectionEnd block for SecIncludePack so we
        match = re.search(
            r'Section\s+"Include offline engine pack"\s+SecIncludePack\b.*?SectionEnd',
            text,
            re.DOTALL,
        )
        assert match is not None, "Section block for SecIncludePack not found."
        section_body = match.group(0)
        assert "SectionIn RO" not in section_body, (
            "SecIncludePack must NOT be marked `SectionIn RO`, that suppresses "
            "the checkbox on the Components page. The whole point of the section "
            "is to give the user a checkbox (plan §4.8 consent gate)."
        )

    def test_section_does_not_deselect_itself_by_default(self) -> None:
        """The section must default to SELECTED."""
        text = _strip_nsis_comments(INSTALLER_HOOKS_NSH.read_text(encoding="utf-8"))
        match = re.search(
            r'Section\s+"Include offline engine pack"\s+SecIncludePack\b.*?SectionEnd',
            text,
            re.DOTALL,
        )
        assert match is not None
        section_body = match.group(0)
        bad = re.search(r"SectionSetFlags\s+\$\{SecIncludePack\}\s+[0-9]+", section_body)
        assert bad is None, (
            "SecIncludePack body must NOT call SectionSetFlags, that would "
            "override the default-selected state. The section must be selected "
            "by default (NSIS contract) so the checkbox starts ticked (plan §4.8)."
        )

    def test_section_has_langstring_description(self) -> None:
        """The Section has a LangString description for the Components page."""
        text = INSTALLER_HOOKS_NSH.read_text(encoding="utf-8")
        assert "LangString DESC_SecIncludePack" in text, (
            "installer-hooks.nsh must declare `LangString DESC_SecIncludePack`, "
            "the Components page shows this description under the section list. "
            "Plan §9.3 adds 8 locale strings for the pack UI; this is one of them."
        )


class TestInstallerHooksCustomInstallMacro:
    """The ``customInstall`` macro writes installer-state.json."""

    def test_custom_install_macro_defined(self) -> None:
        text = INSTALLER_HOOKS_NSH.read_text(encoding="utf-8")
        assert "!macro customInstall" in text, (
            "installer-hooks.nsh must define `!macro customInstall`, Tauri v2 "
            "invokes this macro in the -post Section to write installer-state.json."
        )

    def test_installer_state_json_path_pinned(self) -> None:
        """The macro must write to the canonical installer-state.json path."""
        text = INSTALLER_HOOKS_NSH.read_text(encoding="utf-8")
        # The path is $LOCALAPPDATA\lausu\installer-state.json —
        assert r"$LOCALAPPDATA\lausu\installer-state.json" in text, (
            "installer-hooks.nsh must write installer-state.json to "
            "%LOCALAPPDATA%\\lausu\\, the SAME per-user data root the "
            "Python backend uses (voice_typer/server/_paths.py). A different "
            "path breaks the first-launch consent read (plan §4.7 / §4.8)."
        )

    def test_installer_state_json_schema_pinned(self) -> None:
        """The JSON shape written by the macro is pinned here."""
        text = INSTALLER_HOOKS_NSH.read_text(encoding="utf-8")
        # Both branches (true / false) must carry the same field set.
        for required_field in (
            "include_offline_engine_pack",
            "installer_version",
            "pack_bundled",
        ):
            assert required_field in text, (
                f"installer-hooks.nsh must write the `{required_field}` field to "
                "installer-state.json, the slim-core Python reader pins the schema. "
                "See plan-runtime-pack-split.md §4.8."
            )

    def test_custom_install_reads_section_selection(self) -> None:
        """The macro must consult the Section's selected state, not hardcode."""
        text = INSTALLER_HOOKS_NSH.read_text(encoding="utf-8")
        assert "SectionGetFlags ${SecIncludePack}" in text, (
            "customInstall must call `SectionGetFlags ${SecIncludePack}` to read "
            "the checkbox state, a hardcoded value defeats the consent gate."
        )
        assert "${SF_SELECTED}" in text, (
            "customInstall must AND the section flags with ${SF_SELECTED} to "
            "isolate the selection bit (NSIS section flags are a bitmask)."
        )


class TestArtifactNames:
    """``scripts/build/artifact_names.py`` owns the §11.9 naming contract."""

    def test_slim_core_name_format(self) -> None:

        # Import via path so the test doesn't require the module on sys.path.
        mod = _load_artifact_names_module()
        name = mod.slim_core_installer_name("1.0.0", "x86_64-pc-windows-msvc")
        assert name == "lausu-slim-core-1.0.0-x86_64-pc-windows-msvc.exe", (
            "slim-core installer name must match §11.9: lausu-slim-core-<version>-<triple>.exe"
        )

    def test_runtime_pack_name_format(self) -> None:
        mod = _load_artifact_names_module()
        name = mod.runtime_pack_name("3", "x86_64-pc-windows-msvc")
        assert name == "lausu-runtime-pack-3-x86_64-pc-windows-msvc.zip", (
            "runtime-pack name must match §11.9: lausu-runtime-pack-<pack-version>-<triple>.zip"
        )

    def test_pack_manifest_name(self) -> None:
        mod = _load_artifact_names_module()
        assert mod.pack_manifest_name() == "pack-manifest.json", (
            "pack-manifest.json is the §11.9 manifest release-asset filename (platform-AGNOSTIC, no triple suffix)."
        )

    def test_full_offline_installer_name_format(self) -> None:
        mod = _load_artifact_names_module()
        name = mod.full_offline_installer_name("1.0.0", "x86_64-pc-windows-msvc")
        assert name == "lausu-full-offline-1.0.0-x86_64-pc-windows-msvc.exe", (
            "full-offline installer name must match the §11.9 addendum: lausu-full-offline-<app-version>-<triple>.exe"
        )

    def test_cli_round_trip(self) -> None:
        """The CLI prints the same name the library function returns."""
        _skip_if_missing(ARTIFACT_NAMES_PY, _MISSING_ARTIFACT_NAMES_RSN)
        import subprocess

        result = subprocess.run(
            [
                sys.executable,  # ``python3`` is not portable (Windows: ``python``)
                str(ARTIFACT_NAMES_PY),
                "--slim-core",
                "--app-version",
                "1.0.0",
                "--triple",
                "aarch64-pc-windows-msvc",
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        assert result.stdout.strip() == "lausu-slim-core-1.0.0-aarch64-pc-windows-msvc.exe"

    def test_invalid_triple_rejected(self) -> None:
        """Unknown triples must raise, prevents typos in CI yaml."""
        mod = _load_artifact_names_module()
        with pytest.raises(ValueError, match="unsupported target triple"):
            mod.slim_core_installer_name("1.0.0", "invalid-triple")

    def test_invalid_version_rejected(self) -> None:
        """Malformed versions must raise, prevents bad release bumps."""
        mod = _load_artifact_names_module()
        with pytest.raises(ValueError, match="malformed app-version"):
            mod.slim_core_installer_name("not-a-version", "x86_64-pc-windows-msvc")


class TestNoRenameOfExistingArtifacts:
    """
    C-CI-13: never rename EXISTING artifacts.
    C-CI-13-protected existing names (a collision would be a silent
    """

    def test_new_names_do_not_collide_with_protected(self) -> None:
        mod = _load_artifact_names_module()
        protected = set(mod.EXISTING_PROTECTED_NAMES)
        # Sample new-name artifacts across all triples and a few versions.
        new_names: set[str] = set()
        for triple in mod.SUPPORTED_TRIPLES:
            new_names.add(mod.slim_core_installer_name("1.0.0", triple))
            new_names.add(mod.runtime_pack_name("3", triple))
            new_names.add(mod.full_offline_installer_name("1.0.0", triple))
        new_names.add(mod.pack_manifest_name())
        overlap = protected & new_names
        assert not overlap, (
            "C-CI-13 violation: new §11.9 artifact names overlap with the "
            f"protected existing names: {sorted(overlap)}. The new names must "
            "be ADDITIVE, they must NOT rename or overwrite the existing "
            "tauri-windows-installer / Lausu-Tauri-* / python-sidecar-* "
            "artifact names."
        )

    def test_existing_protected_names_listed(self) -> None:
        """
        The protected names list must enumerate every C-CI-13 entry.
        C-CI-13 enumerates: ``tauri-windows-installer``, ``Lausu-Tauri-MSI``,
        """
        mod = _load_artifact_names_module()
        expected = {
            "tauri-windows-installer",
            "Lausu-Tauri-MSI",
            "Lausu-Tauri-Sidecar-Binaries",
            "Lausu-Tauri-SHA256SUMS",
            "tauri-binaries-manifest-windows",
        }
        assert expected.issubset(set(mod.EXISTING_PROTECTED_NAMES)), (
            "EXISTING_PROTECTED_NAMES missing entries from C-CI-13: "
            f"{sorted(expected - set(mod.EXISTING_PROTECTED_NAMES))}"
        )


class TestFullOfflineInstallerTemplate:
    """The full-offline installer .nsi template must require its !defines."""

    def test_template_exists(self) -> None:
        assert FULL_OFFLINE_NSI.is_file(), (
            f"full-offline-installer.nsi must exist at {FULL_OFFLINE_NSI}, "
            "the full-offline installer artifact is the second Windows installer "
            "alongside the slim core (plan §4.1 / §14.5)."
        )

    @pytest.mark.parametrize(
        "define",
        ["SLIM_CORE_EXE", "PACK_ZIP", "PACK_VERSION", "APP_VERSION", "PRODUCT_TRIPLE"],
    )
    def test_template_requires_each_define(self, define: str) -> None:
        text = FULL_OFFLINE_NSI.read_text(encoding="utf-8")
        # Each !define must be guarded by an !ifndef block that !errors
        pattern = rf"!ifndef\s+{define}\b"
        assert re.search(pattern, text), (
            f"full-offline-installer.nsi must guard {define} with `!ifndef {define}` "
            f"and `!error`, without the guard, makensis silently produces a "
            "broken installer when the build script forgets to pass -D{define}=..."
        )

    def test_template_outfile_uses_canonical_name(self) -> None:
        """The OutFile directive must produce the §11.9 name."""
        text = FULL_OFFLINE_NSI.read_text(encoding="utf-8")
        # The OutFile uses ${APP_VERSION} and ${PRODUCT_TRIPLE} !defines
        assert "lausu-full-offline-${APP_VERSION}-${PRODUCT_TRIPLE}.exe" in text, (
            "full-offline-installer.nsi OutFile must be "
            "`lausu-full-offline-${APP_VERSION}-${PRODUCT_TRIPLE}.exe`, "
            "the §11.9 canonical name (built from the build-time !defines so it "
            "cannot drift from artifact_names.py)."
        )

    def test_template_extracts_pack_to_runtime_pack_dir(self) -> None:
        """The pack zip must be extracted to the per-user runtime-pack dir."""
        text = FULL_OFFLINE_NSI.read_text(encoding="utf-8")
        assert r"$LOCALAPPDATA\lausu\runtime-pack\${PACK_VERSION}" in text, (
            "full-offline-installer.nsi must extract the pack to "
            "%LOCALAPPDATA%\\lausu\\runtime-pack\\<PACK_VERSION>\\, the "
            "SAME path the slim-core app's runtime-pack resolver scans (plan §4.7)."
        )

    def test_template_writes_installer_state_with_pack_bundled_true(self) -> None:
        """The wrapper writes installer-state.json with pack_bundled=true."""
        text = FULL_OFFLINE_NSI.read_text(encoding="utf-8")
        assert '"pack_bundled": true' in text, (
            "full-offline-installer.nsi must write `pack_bundled: true` to "
            "installer-state.json, the slim-core app's first-launch consent "
            "gate checks this flag to skip the silent background download "
            "(plan §4.8)."
        )

    def test_template_runs_slim_core_installer(self) -> None:
        """The wrapper invokes the bundled slim-core installer."""
        text = FULL_OFFLINE_NSI.read_text(encoding="utf-8")
        assert "ExecWait" in text, (
            "full-offline-installer.nsi must ExecWait the bundled slim-core "
            "installer, without it, the slim-core app is never installed."
        )
        assert "$CMDLINE" in text, (
            "full-offline-installer.nsi must forward $CMDLINE to the slim-core "
            "installer so silent installs (/S) and install-dir overrides (/D=path) "
            "propagate."
        )


class TestFullOfflineBuildScript:
    """The build script wires makensis + the .nsi template + the inputs."""

    _SKIP_REASON = (
        f"build_full_offline_installer_windows.sh not present at "
        f"{FULL_OFFLINE_BUILD_SH}, this script is owned by the installer-build "
        "workstream. Skipping until the file lands."
    )

    def test_script_exists_and_is_executable(self) -> None:
        _skip_if_missing(FULL_OFFLINE_BUILD_SH, self._SKIP_REASON)
        assert FULL_OFFLINE_BUILD_SH.is_file(), (
            f"build_full_offline_installer_windows.sh must exist at {FULL_OFFLINE_BUILD_SH}."
        )
        # On Windows CI the script runs under Git Bash, the executable
        if os.name == "posix":
            mode = FULL_OFFLINE_BUILD_SH.stat().st_mode
            assert mode & stat.S_IXUSR, (
                f"{FULL_OFFLINE_BUILD_SH.name} must be executable (chmod +x), the CI YAML invokes it directly."
            )

    def test_script_requires_all_inputs(self) -> None:
        _skip_if_missing(FULL_OFFLINE_BUILD_SH, self._SKIP_REASON)
        """The script must reject missing inputs at arg-parse time."""
        text = FULL_OFFLINE_BUILD_SH.read_text(encoding="utf-8")
        for flag in ("--slim-core", "--pack-zip", "--pack-version", "--app-version", "--triple"):
            assert flag in text, (
                f"build_full_offline_installer_windows.sh must accept {flag}, "
                "the build script composes the slim-core installer + pack zip "
                "into the full-offline artifact; each input is required."
            )

    def test_script_invokes_artifact_names_py_for_output_name(self) -> None:
        _skip_if_missing(FULL_OFFLINE_BUILD_SH, self._SKIP_REASON)
        """The script must NOT hardcode the output filename.

        The §11.9 name is owned by artifact_names.py; the build script
        must call the Python module so the name cannot drift. A
        hardcoded name in the shell script would silently diverge from
        the Python contract.
        """
        text = FULL_OFFLINE_BUILD_SH.read_text(encoding="utf-8")
        assert "artifact_names.py" in text, (
            "build_full_offline_installer_windows.sh must invoke "
            "artifact_names.py to compute the output filename, the §11.9 "
            "name is owned by that module so it cannot drift between the "
            "Python and shell sides."
        )

    def test_script_passes_all_defines_to_makensis(self) -> None:
        _skip_if_missing(FULL_OFFLINE_BUILD_SH, self._SKIP_REASON)
        text = FULL_OFFLINE_BUILD_SH.read_text(encoding="utf-8")
        for define in ("SLIM_CORE_EXE", "PACK_ZIP", "PACK_VERSION", "APP_VERSION", "PRODUCT_TRIPLE"):
            assert f"-D{define}=" in text, (
                f"build_full_offline_installer_windows.sh must pass -D{define}=... "
                f"to makensis, the .nsi template !errors on missing {define}."
            )


class TestSupportedTriplesMatchesStubGenerator:
    """artifact_names.py::SUPPORTED_TRIPLES ↔ gen_tauri_icons_stub.py::SIDECAR_TRIPLES."""

    def test_supported_triples_equals_stub_generator_sidecar_triples(self) -> None:
        mod = _load_artifact_names_module()
        # Load the stub generator module the same way test_config_script_drift does.
        spec = importlib.util.spec_from_file_location("_vt_stub_triples_test", STUB_SCRIPT)
        assert spec is not None and spec.loader is not None, f"cannot load {STUB_SCRIPT}"
        stub = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(stub)
        assert set(mod.SUPPORTED_TRIPLES) == set(stub.SIDECAR_TRIPLES), (
            "artifact_names.py::SUPPORTED_TRIPLES drifted from "
            "gen_tauri_icons_stub.py::SIDECAR_TRIPLES, both must enumerate "
            "the canonical build triples. Add new triples to BOTH in the same "
            f"commit.\n  artifact_names only: {sorted(set(mod.SUPPORTED_TRIPLES) - set(stub.SIDECAR_TRIPLES))}\n"
            f"  stub only: {sorted(set(stub.SIDECAR_TRIPLES) - set(mod.SUPPORTED_TRIPLES))}"
        )


class TestUninstallerNshNotRegressed:
    """Adding installer-hooks.nsh must NOT break the existing uninstaller."""

    def test_uninstaller_nsh_still_defines_custom_uninstall(self) -> None:
        text = UNINSTALLER_NSH.read_text(encoding="utf-8")
        assert "!macro customUnInstall" in text, (
            "uninstaller.nsh must still define `!macro customUnInstall`, the "
            "CR-69 / CR-70 cleanup (autostart Run keys, Task Scheduler tasks, "
            "%APPDATA%\\lausu data dir) depends on it. Adding "
            "installer-hooks.nsh must NOT remove the existing uninstall hooks."
        )

    def test_uninstaller_nsh_still_cleans_appdata(self) -> None:
        text = UNINSTALLER_NSH.read_text(encoding="utf-8")
        # The path is quoted in the .nsh (NSIS literal: `"$APPDATA\lausu"`).
        assert r'RMDir /r "$APPDATA\lausu"' in text, (
            'uninstaller.nsh must still RMDir /r "%APPDATA%\\lausu", CR-70 '
            "per-user data dir cleanup (settings JSON, history DB, vocabularies)."
        )
