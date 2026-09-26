"""
Nuitka Windows .exe build validation.
Gaps documented (report, do NOT fix, out of scope for this gate check):
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

from tests.fixtures.bash_utils import bash_usable

# Path from file → root:
PROJECT_ROOT = Path(__file__).resolve().parents[3]
BUILD_SCRIPT = PROJECT_ROOT / "scripts" / "build" / "build_sidecar_windows.sh"
LINUX_BUILD_SCRIPT = PROJECT_ROOT / "scripts" / "build" / "build_sidecar_linux.sh"
MACOS_BUILD_SCRIPT = PROJECT_ROOT / "scripts" / "build" / "build_sidecar_macos.sh"


@pytest.fixture(scope="module")
def script_text() -> str:
    """Read the build script once per module; fail fast if missing."""
    assert BUILD_SCRIPT.is_file(), (
        f"build_sidecar_windows.sh not found at {BUILD_SCRIPT}. Did the project layout change?"
    )
    return BUILD_SCRIPT.read_text(encoding="utf-8")


def test_build_script_exists():
    """The Windows build script must exist at the canonical path."""
    assert BUILD_SCRIPT.is_file(), f"missing: {BUILD_SCRIPT}"
    # Also assert it's non-empty (a stub would be a regression).
    assert BUILD_SCRIPT.stat().st_size > 1000, (
        f"{BUILD_SCRIPT} is suspiciously small ({BUILD_SCRIPT.stat().st_size} bytes); "
        "expected a full Nuitka invocation script (~3-5 KB)."
    )


def test_build_script_is_bash_syntax_valid():
    """``bash -n`` must parse the script without syntax errors."""
    if not bash_usable():
        pytest.skip("bash not available or not usable on this host, cannot run `bash -n`.")
    result = subprocess.run(
        ["bash", "-n", str(BUILD_SCRIPT)],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, (
        f"bash -n failed on {BUILD_SCRIPT}:\n--- stderr ---\n{result.stderr}\n--- stdout ---\n{result.stdout}"
    )


def test_build_script_has_shebang_and_strict_mode(script_text: str):
    """The script must use ``#!/usr/bin/env bash`` + ``set -euo pipefail``."""
    assert script_text.startswith("#!/usr/bin/env bash"), (
        "build_sidecar_windows.sh must start with `#!/usr/bin/env bash`"
    )
    assert "set -euo pipefail" in script_text, (
        "build_sidecar_windows.sh must enable strict mode (`set -euo pipefail`) "
        "so a missing DLL or failed import aborts the build instead of producing "
        "a broken .exe."
    )


EXPECTED_NUITKA_FLAGS = [
    "--standalone",
    "--onefile",
    "--assume-yes-for-downloads",
    "--enable-plugin=numpy",
    "--include-package=faster_whisper",
    "--include-package=ctranslate2",
    "--include-package=voice_typer",
    "--include-package=websockets",
    "--windows-disable-console",
    "--onefile-tempdir-spec",
    "--output-filename",
    "--output-dir",
]


@pytest.mark.parametrize("flag", EXPECTED_NUITKA_FLAGS)
def test_script_contains_expected_nuitka_flag(script_text: str, flag: str):
    """Each ADR-0020 §4.2-mandated Nuitka flag must be present in the script."""
    assert flag in script_text, (
        f"build_sidecar_windows.sh is missing required Nuitka flag `{flag}`. "
        "ADR-0020 §4.2 mandates this flag for the Windows sidecar freeze."
    )


def test_script_includes_ctranslate2_data_dir(script_text: str):
    """The script must ``--include-data-dir`` the ctranslate2/lib folder."""
    assert "--include-data-dir" in script_text
    assert "ctranslate2/lib" in script_text, (
        "build_sidecar_windows.sh must include --include-data-dir for "
        "$SITE/ctranslate2/lib (captures libiomp5md.dll + MKL/OpenMP DLLs)."
    )


def test_script_includes_ctranslate2_dll(script_text: str):
    """The script must ``--include-dll`` the ctranslate2.dll explicitly."""
    assert "--include-dll" in script_text
    assert "ctranslate2.dll" in script_text, (
        "build_sidecar_windows.sh must --include-dll ctranslate2.dll explicitly (Nuitka does not glob *.dll)."
    )


def test_script_onefile_tempdir_uses_supported_cache_dir_token(script_text: str):
    """``--onefile-tempdir-spec`` must pin to ``{CACHE_DIR}/lausu``."""
    assert "{CACHE_DIR}" in script_text, (
        "build_sidecar_windows.sh --onefile-tempdir-spec must use "
        "{CACHE_DIR} (Nuitka-supported token expanding to the user's "
        "AppData\\Local)."
    )
    # The legacy spec VALUE must be gone (ban the spec line, not prose —
    assert not re.search(r"--onefile-tempdir-spec=\"?%LOCALAPPDATA%", script_text), (
        "%LOCALAPPDATA% is not a supported --onefile-tempdir-spec variable "
        "(Nuitka FATAL 'Found unknown variable name'); use {CACHE_DIR}."
    )
    assert "lausu" in script_text


def test_script_references_x86_64_target_triple(script_text: str):
    """The script must reference ``x86_64-pc-windows-msvc`` (primary triple)."""
    assert "x86_64-pc-windows-msvc" in script_text, (
        "build_sidecar_windows.sh must reference the x86_64-pc-windows-msvc "
        "triple (primary Windows target per ADR-0020 §4.1)."
    )


def test_script_references_aarch64_target_triple(script_text: str):
    """The script must also support ``aarch64-pc-windows-msvc`` (Windows-on-ARM)."""
    assert "aarch64-pc-windows-msvc" in script_text, (
        "build_sidecar_windows.sh must reference aarch64-pc-windows-msvc "
        "(secondary Windows target per ADR-0020 §4.1 + script header)."
    )


def test_script_uses_triple_variable_construction(script_text: str):
    """The script must build TRIPLE from ARCH via ``${ARCH}-pc-windows-msvc``."""
    assert "${ARCH}-pc-windows-msvc" in script_text, (
        'build_sidecar_windows.sh must construct TRIPLE dynamically: TRIPLE="${ARCH}-pc-windows-msvc"'
    )


def test_script_output_filename_pattern(script_text: str):
    """The output filename must match ``python-sidecar-<triple>.exe``."""
    assert "python-sidecar-" in script_text, (
        "build_sidecar_windows.sh output filename must start with `python-sidecar-` (Tauri externalBin base name)."
    )
    assert "${TRIPLE}${EXE_SUFFIX}" in script_text or "python-sidecar-${TRIPLE}.exe" in script_text, (
        "build_sidecar_windows.sh must construct OUTPUT_NAME as python-sidecar-${TRIPLE}${EXE_SUFFIX} (or equivalent)."
    )


def test_script_outputs_to_src_tauri_bin(script_text: str):
    """The output directory must be ``src-tauri/bin`` (Tauri externalBin location)."""
    assert "src-tauri/bin" in script_text, (
        "build_sidecar_windows.sh must output to src-tauri/bin/ (the "
        "location Tauri's externalBin mechanism expects sidecar binaries)."
    )


def test_script_has_ctranslate2_lib_guard(script_text: str):
    """The script must bundle ctranslate2's native DLLs from EITHER layout."""
    # The script must define the lib/ path and resolve the native-DLL
    assert 'CT2_LIB_DIR="$CT2_DIR/lib"' in script_text, (
        "build_sidecar_windows.sh must define CT2_LIB_DIR as $CT2_DIR/lib (the ctranslate2/lib path)."
    )
    assert 'CT2_DATA_DIR_SRC="$CT2_LIB_DIR"' in script_text, (
        "build_sidecar_windows.sh must prefer the ctranslate2/lib layout when present."
    )
    assert 'CT2_DATA_DIR_SRC="$CT2_DIR"' in script_text, (
        "build_sidecar_windows.sh must fall back to the ctranslate2 package dir "
        "(modern wheels ship DLLs without a lib/ subdir)."
    )
    assert '! -f "$CT2_DLL"' in script_text, (
        'build_sidecar_windows.sh must guard: `if [[ ! -f "$CT2_DLL" ]]; then echo ERROR ...; exit 1; fi`'
    )


def test_script_has_ctranslate2_dll_guard(script_text: str):
    """The script must hard-fail if ``ctranslate2.dll`` is missing."""
    assert '! -f "$CT2_DLL"' in script_text or '! -f "$CT2_LIB_DIR/ctranslate2.dll"' in script_text, (
        'build_sidecar_windows.sh must guard: `if [[ ! -f "$CT2_DLL" ]]; then echo ERROR ...; exit 1; fi`'
    )


def test_known_gap_no_ctranslate2_libs_guard(script_text: str):
    """``ctranslate2/libs`` (plural) existence guard like the Linux + macOS"""
    # The Linux + macOS siblings MUST have the libs guard (sanity check
    linux_text = LINUX_BUILD_SCRIPT.read_text(encoding="utf-8")
    macos_text = MACOS_BUILD_SCRIPT.read_text(encoding="utf-8")
    assert "CT2_LIBS_DIR" in linux_text, (
        "Reference pattern broken: build_sidecar_linux.sh should have CT2_LIBS_DIR (XPLAT-3 guard)."
    )
    assert "CT2_LIBS_DIR" in macos_text, (
        "Reference pattern broken: build_sidecar_macos.sh should have CT2_LIBS_DIR guard."
    )

    # BUILD-2 fix: the Windows script now HAS the libs guard.
    assert "CT2_LIBS_DIR" in script_text, "build_sidecar_windows.sh should have CT2_LIBS_DIR guard (BUILD-2 fix)."
    assert "ctranslate2/libs" in script_text, (
        "build_sidecar_windows.sh should reference ctranslate2/libs (BUILD-2 fix)."
    )
    assert 'if [[ -d "$CT2_LIBS_DIR" ]]' in script_text, (
        "build_sidecar_windows.sh should guard the libs include with if [[ -d (BUILD-2 fix)."
    )


def test_script_supports_check_mode(script_text: str):
    """The script must support a ``--check`` arg to verify the toolchain."""
    assert '"--check"' in script_text or "--check" in script_text, (
        "build_sidecar_windows.sh must support a --check arg (toolchain verification without invoking Nuitka)."
    )
    assert "import nuitka" in script_text, "build_sidecar_windows.sh --check must verify nuitka is importable."
    assert "import faster_whisper, ctranslate2" in script_text or (
        "import faster_whisper" in script_text and "import ctranslate2" in script_text
    ), "build_sidecar_windows.sh --check must verify faster_whisper + ctranslate2."


def test_script_validates_python_interpreter(script_text: str):
    """The script must hard-fail if no Python interpreter is found."""
    assert "VOICE_TYPER_PYBS_DIR" in script_text, (
        "build_sidecar_windows.sh must discover python via $VOICE_TYPER_PYBS_DIR (set by CI workflow, ADR-0020 §4.2)."
    )
    assert "${PYBS" in script_text, "build_sidecar_windows.sh must support the $PYBS env var override."
    assert "command -v python" in script_text, "build_sidecar_windows.sh must fall back to `command -v python`."
    assert "no python interpreter found" in script_text, (
        "build_sidecar_windows.sh must emit a clear error if no python interpreter is discovered."
    )


def test_script_validates_python_build_standalone_layout(script_text: str):
    """The script must reference the python-build-standalone install layout."""
    assert "python/python.exe" in script_text, (
        "build_sidecar_windows.sh must reference python-build-standalone's "
        "install_only layout: $PYBS_DIR/python/python.exe."
    )


def test_script_sanity_checks_ctranslate2_import(script_text: str):
    """The script must run an ``import faster_whisper, ctranslate2, websockets``"""
    assert "import faster_whisper, ctranslate2, websockets" in script_text, (
        "build_sidecar_windows.sh must sanity-check that "
        "faster_whisper + ctranslate2 + websockets all import in the build env."
    )
    assert "ctranslate2.__version__" in script_text, (
        "build_sidecar_windows.sh must print ctranslate2.__version__ on the "
        "sanity-check line (proves the wheel is the real one, not a stub)."
    )


def test_script_resolves_site_packages(script_text: str):
    """The script must resolve the build env's site-packages dir."""
    assert "site.getsitepackages()" in script_text, (
        "build_sidecar_windows.sh must resolve $SITE via "
        "`site.getsitepackages()[0]` so --include-data-dir paths are correct."
    )


def test_script_entry_point_is_ipc_server(script_text: str):
    """The Nuitka entry point must be ``voice_typer/server/ipc_server.py``."""
    assert "voice_typer/server/ipc_server.py" in script_text, (
        "build_sidecar_windows.sh entry point must be "
        "voice_typer/server/ipc_server.py (matches predecessor + dev sidecar)."
    )


def test_script_verifies_output_after_build(script_text: str):
    """The script must verify the output .exe exists after Nuitka completes."""
    assert "OUTPUT_PATH" in script_text
    assert '! -f "$OUTPUT_PATH"' in script_text, (
        'build_sidecar_windows.sh must verify: `if [[ ! -f "$OUTPUT_PATH" ]]; then echo ERROR; exit 1; fi`'
    )


def test_script_documents_signing_next_step(script_text: str):
    """The script must point to the signing runbook after a successful build."""
    assert "signtool" in script_text.lower() or "signing-guide.md" in script_text, (
        "build_sidecar_windows.sh must document the next step (signtool / "
        "signing-guide.md §13.1) after a successful build."
    )


# 7. Sibling parity (Linux + macOS scripts have the  guard) ───────
def test_linux_sibling_has_xplat3_ctranslate2_libs_guard():
    """Sanity check: the Linux sibling MUST have the XPLAT-3 guard."""
    linux_text = LINUX_BUILD_SCRIPT.read_text(encoding="utf-8")
    assert "CT2_LIBS_DIR" in linux_text
    assert '--include-data-dir="$CT2_LIBS_DIR=$CT2_LIBS_DIR"' in linux_text
    assert 'if [[ -d "$CT2_LIBS_DIR" ]]' in linux_text


def test_macos_sibling_has_xplat3_ctranslate2_libs_guard():
    """Sanity check: the macOS sibling MUST have the XPLAT-3 guard."""
    macos_text = MACOS_BUILD_SCRIPT.read_text(encoding="utf-8")
    assert "CT2_LIBS_DIR" in macos_text
    assert '--include-data-dir="$CT2_LIBS_DIR=$CT2_LIBS_DIR"' in macos_text
    assert 'if [[ -d "$CT2_LIBS_DIR" ]]' in macos_text


def test_macos_sibling_uses_nuitka_args_array():
    """Sanity check: the macOS sibling uses the NUITKA_ARGS array pattern."""
    macos_text = MACOS_BUILD_SCRIPT.read_text(encoding="utf-8")
    assert "NUITKA_ARGS=(" in macos_text
    assert '"${NUITKA_ARGS[@]}"' in macos_text
