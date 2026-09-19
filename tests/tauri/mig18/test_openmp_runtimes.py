"""
OpenMP runtimes bundling validation (Win + macOS + Linux).
Gaps documented (report, do NOT fix, out of scope for MIG-1.8):
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tests.fixtures.bash_utils import bash_usable

# Path from file → root:
PROJECT_ROOT = Path(__file__).resolve().parents[3]
BUILD_DIR = PROJECT_ROOT / "scripts" / "build"

WINDOWS_SCRIPT = BUILD_DIR / "build_sidecar_windows.sh"
MACOS_SCRIPT = BUILD_DIR / "build_sidecar_macos.sh"
LINUX_SCRIPT = BUILD_DIR / "build_sidecar_linux.sh"

# All 3 scripts under test, for cross-platform parametrized checks.
ALL_BUILD_SCRIPTS = [
    pytest.param(WINDOWS_SCRIPT, id="windows"),
    pytest.param(MACOS_SCRIPT, id="macos"),
    pytest.param(LINUX_SCRIPT, id="linux"),
]


@pytest.fixture(scope="module")
def windows_text() -> str:
    """Read the Windows build script once; fail fast if missing."""
    assert WINDOWS_SCRIPT.is_file(), f"missing: {WINDOWS_SCRIPT}"
    return WINDOWS_SCRIPT.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def macos_text() -> str:
    """Read the macOS build script once; fail fast if missing."""
    assert MACOS_SCRIPT.is_file(), f"missing: {MACOS_SCRIPT}"
    return MACOS_SCRIPT.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def linux_text() -> str:
    """Read the Linux build script once; fail fast if missing."""
    assert LINUX_SCRIPT.is_file(), f"missing: {LINUX_SCRIPT}"
    return LINUX_SCRIPT.read_text(encoding="utf-8")


@pytest.mark.parametrize("script", ALL_BUILD_SCRIPTS)
def test_build_script_exists(script: Path):
    """Each platform's build_sidecar_*.sh must exist at the canonical path."""
    assert script.is_file(), f"missing build script: {script}. Did the project layout change?"
    # Also assert it's non-empty (a stub would be a regression).
    assert script.stat().st_size > 1000, (
        f"{script} is suspiciously small ({script.stat().st_size} bytes); "
        "expected a full Nuitka invocation script (~3-5 KB)."
    )


@pytest.mark.parametrize("script", ALL_BUILD_SCRIPTS)
def test_build_script_is_bash_syntax_valid(script: Path):
    """``bash -n`` must parse each script without syntax errors."""
    if not bash_usable():
        pytest.skip("bash not available or not usable on this host, cannot run `bash -n`.")
    result = subprocess.run(
        ["bash", "-n", str(script)],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, (
        f"bash -n failed on {script}:\n--- stderr ---\n{result.stderr}\n--- stdout ---\n{result.stdout}"
    )


def test_windows_bundles_libiomp5md_dll_via_include_data_dir(windows_text: str):
    """Windows must ``--include-data-dir`` the ctranslate2 native-DLL folder."""
    assert "--include-data-dir" in windows_text, (
        "build_sidecar_windows.sh must use --include-data-dir for the "
        "ctranslate2 native-DLL folder (captures libiomp5md.dll + MKL/OpenMP DLLs)."
    )
    assert '--include-data-dir="$CT2_DATA_DIR_SRC=$CT2_DATA_DIR_DEST"' in windows_text, (
        "build_sidecar_windows.sh must --include-data-dir the layout-resolved "
        "ctranslate2 native-DLL folder ($CT2_DATA_DIR_SRC=$CT2_DATA_DIR_DEST)."
    )
    # Both layouts must resolve a RELATIVE dest (absolute dests are silently
    assert 'CT2_DATA_DIR_DEST="ctranslate2/lib"' in windows_text, (
        "the lib/ layout must map to a RELATIVE dest (ctranslate2/lib)."
    )
    assert 'CT2_DATA_DIR_DEST="ctranslate2"' in windows_text, (
        "the package-root layout must map to a RELATIVE dest (ctranslate2)."
    )
    assert '--include-dll="$CT2_DLL"' in windows_text, (
        "build_sidecar_windows.sh must also --include-dll ctranslate2.dll explicitly."
    )


def test_windows_bundles_ctranslate2_dll_explicitly(windows_text: str):
    """Windows must also ``--include-dll`` the ``ctranslate2.dll`` explicitly."""
    assert "--include-dll" in windows_text, (
        "build_sidecar_windows.sh must use --include-dll for ctranslate2.dll (Nuitka does not glob *.dll)."
    )
    assert "ctranslate2.dll" in windows_text, "build_sidecar_windows.sh must --include-dll ctranslate2.dll explicitly."


def test_windows_documents_libiomp5md_dll_in_header(windows_text: str):
    """The Windows script header must mention ``libiomp5md.dll`` by name."""
    assert "libiomp5md.dll" in windows_text, (
        "build_sidecar_windows.sh must document libiomp5md.dll in its header "
        "comment (ADR-0020 §4.2 + §11 'easy to miss, instant crash if absent')."
    )


def test_macos_bundles_libiomp5_dylib_via_include_data_dir(macos_text: str):
    """macOS must ``--include-data-dir`` the ``ctranslate2/lib`` folder."""
    assert "--include-data-dir" in macos_text, (
        "build_sidecar_macos.sh must use --include-data-dir for the "
        "ctranslate2/lib folder (captures libiomp5.dylib OpenMP runtime)."
    )
    assert "ctranslate2/lib" in macos_text, (
        "build_sidecar_macos.sh must reference ctranslate2/lib in its --include-data-dir flag."
    )
    assert '--include-data-dir="$CT2_LIB_DIR=$CT2_LIB_DIR"' in macos_text, (
        "build_sidecar_macos.sh must have the unconditional "
        '--include-data-dir="$CT2_LIB_DIR=$CT2_LIB_DIR" flag in the main '
        "Nuitka invocation (ctranslate2/lib is mandatory, not optional)."
    )


def test_macos_documents_libiomp5_dylib_in_header(macos_text: str):
    """The macOS script header must mention ``libiomp5.dylib`` by name."""
    assert "libiomp5.dylib" in macos_text, (
        "build_sidecar_macos.sh must document libiomp5.dylib in its header "
        "comment (ADR-0020 §4.3, the macOS OpenMP runtime)."
    )


def test_linux_bundles_openmp_runtime_via_include_data_dir(linux_text: str):
    """Linux must ``--include-data-dir`` the ``ctranslate2/lib`` folder."""
    assert "--include-data-dir" in linux_text, (
        "build_sidecar_linux.sh must use --include-data-dir for the "
        "ctranslate2/lib folder (captures libgomp.so / libiomp5.so OpenMP runtime)."
    )
    assert "ctranslate2/lib" in linux_text, (
        "build_sidecar_linux.sh must reference ctranslate2/lib in its --include-data-dir flag."
    )
    assert '--include-data-dir="$SITE/ctranslate2/lib=$SITE/ctranslate2/lib"' in linux_text, (
        "build_sidecar_linux.sh must have the unconditional "
        '--include-data-dir="$SITE/ctranslate2/lib=$SITE/ctranslate2/lib" '
        "flag in the main Nuitka invocation (ctranslate2/lib is mandatory, "
        "not optional)."
    )


def test_linux_documents_both_libgomp_and_libiomp5_in_header(linux_text: str):
    """The Linux script header must mention BOTH ``libgomp.so`` AND ``libiomp5.so``."""
    assert "libgomp.so" in linux_text or "libgomp" in linux_text, (
        "build_sidecar_linux.sh must document libgomp.so (GNU OpenMP) in its "
        "header comment (ADR-0020 §4.4, Linux OpenMP runtime)."
    )
    assert "libiomp5.so" in linux_text or "libiomp5" in linux_text, (
        "build_sidecar_linux.sh must document libiomp5.so (Intel OpenMP) in "
        "its header comment (ADR-0020 §4.4, Linux OpenMP runtime)."
    )


@pytest.mark.parametrize("script", ALL_BUILD_SCRIPTS)
def test_ctranslate2_lib_data_dir_is_unconditional(script: Path):
    """``ctranslate2/lib`` (singular) must be bundled unconditionally."""
    text = script.read_text(encoding="utf-8")
    # Sanity: the script must reference ctranslate2/lib somewhere.
    assert "ctranslate2/lib" in text, (
        f"{script.name} must reference ctranslate2/lib (the mandatory dir containing the OpenMP runtime)."
    )
    # Find every --include-data-dir line that references ctranslate2/lib.
    lines = text.splitlines()
    in_guard_block = False
    for i, line in enumerate(lines):
        stripped = line.strip()
        # Track entry into the `if [[ -d "$CT2_LIBS_DIR" ]]` guard block.
        if 'if [[ -d "$CT2_LIBS_DIR"' in stripped or ("CT2_LIBS_DIR" in stripped and stripped.startswith("if ")):
            in_guard_block = True
        # The guard block ends at the matching `fi`.
        if in_guard_block and stripped == "fi":
            in_guard_block = False
        # (but NOT ctranslate2/libs plural) must NOT be in the guard block.
        if "--include-data-dir" in stripped and "ctranslate2/lib" in stripped:
            # Skip plural form (ctranslate2/libs).
            if "ctranslate2/libs" in stripped:
                continue
            # This is a singular ctranslate2/lib include-data-dir line.
            assert not in_guard_block, (
                f"{script.name} line {i + 1}: --include-data-dir for "
                "ctranslate2/lib (singular, mandatory) is INSIDE an "
                "`if [[ -d ... ]]` guard block. The ctranslate2/lib "
                "folder must be bundled UNCONDITIONALLY, it contains "
                "the OpenMP runtime (libiomp5md.dll / libiomp5.dylib / "
                "libgomp.so). See ADR-0020 §11."
            )


# 6. ctranslate2/libs data-dir is GUARDED ( pattern) ───────────────
def test_macos_has_xplat3_ctranslate2_libs_guard(macos_text: str):
    """macOS must guard ``ctranslate2/libs`` (plural) with ``if [[ -d ... ]]``."""
    assert "CT2_LIBS_DIR" in macos_text, "build_sidecar_macos.sh must define CT2_LIBS_DIR (XPLAT-3 pattern)."
    assert 'if [[ -d "$CT2_LIBS_DIR" ]]' in macos_text, (
        "build_sidecar_macos.sh must guard the ctranslate2/libs include with "
        '`if [[ -d "$CT2_LIBS_DIR" ]]; then ... fi` (XPLAT-3 pattern).'
    )
    assert '--include-data-dir="$CT2_LIBS_DIR=$CT2_LIBS_DIR"' in macos_text, (
        "build_sidecar_macos.sh must --include-data-dir for $CT2_LIBS_DIR inside the guard block."
    )


def test_linux_has_xplat3_ctranslate2_libs_guard(linux_text: str):
    """Linux must guard ``ctranslate2/libs`` (plural) with ``if [[ -d ... ]]``."""
    assert "CT2_LIBS_DIR" in linux_text, "build_sidecar_linux.sh must define CT2_LIBS_DIR (XPLAT-3 pattern)."
    assert 'if [[ -d "$CT2_LIBS_DIR" ]]' in linux_text, (
        "build_sidecar_linux.sh must guard the ctranslate2/libs include with "
        '`if [[ -d "$CT2_LIBS_DIR" ]]; then ... fi` (XPLAT-3 pattern).'
    )
    assert '--include-data-dir="$CT2_LIBS_DIR=$CT2_LIBS_DIR"' in linux_text, (
        "build_sidecar_linux.sh must --include-data-dir for $CT2_LIBS_DIR inside the guard block."
    )


def test_windows_known_gap_no_ctranslate2_libs_guard(windows_text: str):
    """BUILD-2 fix: the Windows script now HAS the ctranslate2/libs guard."""
    # The Linux + macOS siblings MUST have the libs guard (sanity check
    linux_text = LINUX_SCRIPT.read_text(encoding="utf-8")
    macos_text = MACOS_SCRIPT.read_text(encoding="utf-8")
    assert "CT2_LIBS_DIR" in linux_text, (
        "Reference pattern broken: build_sidecar_linux.sh should have CT2_LIBS_DIR (XPLAT-3 guard)."
    )
    assert "CT2_LIBS_DIR" in macos_text, (
        "Reference pattern broken: build_sidecar_macos.sh should have CT2_LIBS_DIR guard."
    )

    # BUILD-2 fix: the Windows script now HAS the libs guard.
    assert "CT2_LIBS_DIR" in windows_text, "build_sidecar_windows.sh should have CT2_LIBS_DIR guard (BUILD-2 fix)."
    assert "ctranslate2/libs" in windows_text, (
        "build_sidecar_windows.sh should reference ctranslate2/libs (BUILD-2 fix)."
    )
    assert 'if [[ -d "$CT2_LIBS_DIR" ]]' in windows_text, (
        "build_sidecar_windows.sh should guard the libs include with if [[ -d (BUILD-2 fix)."
    )


def test_macos_sibling_uses_nuitka_args_array_pattern(macos_text: str):
    """Sanity: macOS sibling uses the ``NUITKA_ARGS=(...)`` array pattern."""
    assert "NUITKA_ARGS=(" in macos_text
    assert '"${NUITKA_ARGS[@]}"' in macos_text


def test_linux_sibling_uses_nuitka_args_array_pattern(linux_text: str):
    """Sanity: Linux sibling uses the ``NUITKA_ARGS=(...)`` array pattern."""
    assert "NUITKA_ARGS=(" in linux_text
    assert '"${NUITKA_ARGS[@]}"' in linux_text


def test_all_three_scripts_reference_ctranslate2_package(windows_text: str, macos_text: str, linux_text: str):
    """All 3 scripts must ``--include-package=ctranslate2``."""
    for label, text in (("windows", windows_text), ("macos", macos_text), ("linux", linux_text)):
        assert "--include-package=ctranslate2" in text, (
            f"build_sidecar_{label}.sh must --include-package=ctranslate2 "
            "(the Python package, distinct from the data-dir that bundles "
            "the native DLLs)."
        )


def test_all_three_scripts_reference_faster_whisper_package(windows_text: str, macos_text: str, linux_text: str):
    """All 3 scripts must ``--include-package=faster_whisper``."""
    for label, text in (("windows", windows_text), ("macos", macos_text), ("linux", linux_text)):
        assert "--include-package=faster_whisper" in text, (
            f"build_sidecar_{label}.sh must --include-package=faster_whisper "
            "(the consumer of ctranslate2 that loads the OpenMP runtime)."
        )
