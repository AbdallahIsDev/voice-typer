r"""MIG-1.8 Phase 1 — OpenMP runtimes bundling validation (Win + macOS + Linux).

This test file validates that the **OpenMP runtime libraries** that
CTranslate2 links against are correctly bundled into the Nuitka-frozen
sidecar binary on all 3 desktop platforms (Windows, macOS, Linux).

ADR-0020 §4 ("Nuitka freeze spec") — and §4.2 / §4.3 / §4.4 in
particular — call out the OpenMP runtimes explicitly:

  - **Windows** (§4.2): ``libiomp5md.dll`` (Intel OpenMP). Lives under
    ``$SITE/ctranslate2/lib/``. If missing, the frozen ``.exe`` BUILDS
    but **crashes instantly on ``import ctranslate2``** at launch.
  - **macOS**   (§4.3): ``libiomp5.dylib`` (Intel OpenMP). Lives under
    ``$SITE/ctranslate2/lib/``. Apple Silicon wheels are CPU-only
    (no CUDA), so OpenMP is the only parallel backend.
  - **Linux**   (§4.4): ``libgomp.so`` (GNU OpenMP) **and/or**
    ``libiomp5.so`` (Intel OpenMP). Lives under
    ``$SITE/ctranslate2/lib/``.

Because Nuitka does **not** auto-collect these runtimes, the build
scripts must bundle them explicitly. The reliable pattern (per
ADR-0020 §4.2 footnote: "Nuitka does NOT expand globs like *.dll")
is to ``--include-data-dir`` the **entire** ``ctranslate2/lib``
folder verbatim, which captures the OpenMP runtime + any MKL
redistributables present in the wheel.

The Linux sandbox CANNOT run a real Nuitka freeze here (no MSVC,
no Xcode, no python-build-standalone interpreter for any of the 3
targets). These tests therefore validate the **structure** of the
3 build scripts — they assert that each script:

  1. Bundles the platform-specific OpenMP runtime via
     ``--include-data-dir`` for ``ctranslate2/lib`` (the mandatory
     folder that contains the OpenMP ``.dll`` / ``.dylib`` / ``.so``).
  2. Documents the platform-specific OpenMP filename
     (``libiomp5md.dll`` / ``libiomp5.dylib`` / ``libgomp.so`` /
     ``libiomp5.so``) in its header comment so a future maintainer
     cannot accidentally delete the runtime.
  3. Includes ``ctranslate2/lib`` UNCONDITIONALLY (it is the
     mandatory dir — the OpenMP runtime + ctranslate2's own native
     lib both live there).
  4. Guards ``ctranslate2/libs`` (plural — the OPTIONAL secondary
     dir) with an ``if [[ -d ... ]]`` existence check (XPLAT-3
     pattern). Windows is a **known gap** — it does not have this
     guard. This test file documents the gap (asserts absence)
     rather than fixing it.

VALIDATE ON HOST:
    These commands must be run on a real host with a freshly-built
    sidecar binary to verify the OpenMP runtimes actually survived
    the Nuitka freeze (the structure tests below only prove the
    build script *tries* to bundle them — they do NOT prove the
    files landed inside the onefile archive).

    # ─── Windows (PowerShell on Windows 10 22H2 / Windows 11) ──────────────
    # 1. Build the sidecar:
    bash scripts/build/build_sidecar_windows.sh
    # 2. Extract the onefile archive to a temp dir (Nuitka --onefile
    #    extracts on every launch to %LOCALAPPDATA%\voice-typer\onefile-tmp):
    $bin = "src-tauri\bin\python-sidecar-x86_64-pc-windows-msvc.exe"
    & $bin --help   # forces onefile extraction
    $tmp = "$env:LOCALAPPDATA\voice-typer\onefile-tmp"
    # 3. Assert libiomp5md.dll (OpenMP) is present next to ctranslate2.dll:
    Get-ChildItem -Recurse $tmp -Filter "libiomp5md.dll" | Select-Object FullName
    Get-ChildItem -Recurse $tmp -Filter "ctranslate2.dll"  | Select-Object FullName
    # Expected: at least one libiomp5md.dll + one ctranslate2.dll.
    # 4. Enumerate loaded DLLs at runtime (Sysinternals listdlls):
    #    Start the sidecar, then:
    listdlls.exe python-sidecar-x86_64-pc-windows-msvc.exe | findstr /I "libiomp mkl"
    # Expected: libiomp5md.dll (and any mkl_*.dll) listed as loaded modules.

    # ─── macOS (zsh / bash on macOS 13+ on Apple Silicon OR Intel) ────────
    # 1. Build the sidecar (default = host arch):
    bash scripts/build/build_sidecar_macos.sh
    # 2. Extract the onefile archive (forces extraction):
    ./src-tauri/bin/python-sidecar-$(uname -m | sed 's/arm64/aarch64/')-apple-darwin --help
    tmp="$HOME/Library/Application Support/voice-typer/onefile-tmp"
    # 3. Assert libiomp5.dylib (OpenMP) is present:
    find "$tmp" -name "libiomp5.dylib" -print
    find "$tmp" -name "libctranslate2.dylib" -print
    # Expected: at least one libiomp5.dylib + one libctranslate2.dylib.
    # 4. Verify @rpath dependencies resolve in the frozen binary:
    otool -L "$tmp"/ctranslate2/lib/libctranslate2.dylib | grep -E "libiomp5|libgomp"
    # Expected: libiomp5.dylib listed as a load command.

    # ─── Linux (bash on Ubuntu 22.04+ / Debian 12+ / Fedora 36+) ──────────
    # 1. Build the sidecar:
    bash scripts/build/build_sidecar_linux.sh x86_64
    # 2. Extract the onefile archive (forces extraction):
    ./src-tauri/bin/python-sidecar-x86_64-unknown-linux-gnu --help
    tmp="${XDG_CACHE_HOME:-$HOME/.cache}/voice-typer/onefile-tmp"
    # 3. Assert libgomp.so OR libiomp5.so (OpenMP) is present:
    find "$tmp" -name "libgomp.so*" -o -name "libiomp5.so*" -print
    find "$tmp" -name "libctranslate2.so*" -print
    # Expected: at least one of {libgomp.so*, libiomp5.so*} + one libctranslate2.so*.
    # 4. Verify NEEDED dependencies resolve in the frozen binary:
    ldd "$tmp"/ctranslate2/lib/libctranslate2.so | grep -E "libgomp|libiomp5"
    # Expected: libgomp.so.1 OR libiomp5.so listed as a NEEDED library.

References:
  - ADR-0020 §4.2 — Windows Nuitka freeze spec (libiomp5md.dll).
  - ADR-0020 §4.3 — macOS Nuitka freeze spec (libiomp5.dylib).
  - ADR-0020 §4.4 — Linux Nuitka freeze spec (libgomp.so / libiomp5.so).
  - ADR-0020 §11 — Known Issues: "CPU inference runtimes (easy to miss,
    instant crash if absent)".
  - scripts/build/build_sidecar_{windows,macos,linux}.sh — the 3 scripts
    under test.
  - tests/tauri/mig15/test_nuitka_windows_build.py — sibling test that
    documents the GAP-1 (no ctranslate2/libs guard on Windows).
  - tests/tauri/mig16/test_nuitka_macos_build.py — sibling macOS test.
  - tests/tauri/mig17/test_nuitka_linux_build.py — sibling Linux test
    that documents the XPLAT-3 ctranslate2/libs guard pattern.

Gaps documented (report, do NOT fix — out of scope for MIG-1.8):
  - GAP-OpenMP-W1: ``build_sidecar_windows.sh`` does NOT have a
    ``ctranslate2/libs`` (plural) existence guard like the Linux +
    macOS siblings (XPLAT-3 pattern parity gap). The Windows wheel
    layout puts all DLLs under ``ctranslate2/lib`` (singular), so
    this is likely benign on Windows — but it is an inconsistency.
    See ``test_windows_known_gap_no_ctranslate2_libs_guard``.
  - GAP-OpenMP-W2: ``build_sidecar_windows.sh`` uses an explicit
    ``--include-dll=$CT2_DLL`` for ``ctranslate2.dll`` only — it
    does NOT explicitly name ``libiomp5md.dll`` in an
    ``--include-dll`` flag. Instead, the OpenMP runtime is captured
    implicitly via ``--include-data-dir=$CT2_LIB_DIR`` (the whole
    folder). This is acceptable (the folder is copied verbatim) but
    is a fragile coupling — if a future Nuitka version changes how
    ``--include-data-dir`` handles DLLs, the OpenMP runtime could
    silently drop out. Documented here, not fixed.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tests.fixtures.bash_utils import bash_usable

# ─── Project paths ───────────────────────────────────────────────────────────
# This test file lives at tests/tauri/mig18/test_openmp_runtimes.py.
# Path from file → root:
#   parents[0] = mig18/
#   parents[1] = tauri/
#   parents[2] = tests/
#   parents[3] = <project root> (voice-typer/)
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


# ─── Fixtures ────────────────────────────────────────────────────────────────
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


# ─── 1. All 3 build scripts exist + are bash-syntax-valid ────────────────────
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
    """``bash -n`` must parse each script without syntax errors.

    This is the only test that actually invokes bash; ``-n`` only parses,
    it does NOT execute the script, so no Nuitka / cl.exe / swiftc / gcc
    is spawned. Safe to run on the Linux sandbox.
    """
    if not bash_usable():
        pytest.skip("bash not available or not usable on this host — cannot run `bash -n`.")
    result = subprocess.run(
        ["bash", "-n", str(script)],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, (
        f"bash -n failed on {script}:\n--- stderr ---\n{result.stderr}\n--- stdout ---\n{result.stdout}"
    )


# ─── 2. Windows: libiomp5md.dll (Intel OpenMP) bundling ──────────────────────
def test_windows_bundles_libiomp5md_dll_via_include_data_dir(windows_text: str):
    """Windows must ``--include-data-dir`` the ``ctranslate2/lib`` folder.

    ADR-0020 §4.2: ``libiomp5md.dll`` lives under
    ``$SITE/ctranslate2/lib/``. Because Nuitka does NOT glob ``*.dll``,
    the reliable pattern is ``--include-data-dir=$SITE/ctranslate2/lib``
    which copies the whole folder verbatim — this captures
    ``libiomp5md.dll`` + any MKL redistributables present.

    Without this, the frozen .exe BUILDS but CRASHES on
    ``import ctranslate2`` at launch (ADR-0020 §11 "Known Issues:
    CPU inference runtimes (easy to miss, instant crash if absent)").
    """
    assert "--include-data-dir" in windows_text, (
        "build_sidecar_windows.sh must use --include-data-dir for the "
        "ctranslate2/lib folder (captures libiomp5md.dll + MKL/OpenMP DLLs)."
    )
    assert "ctranslate2/lib" in windows_text, (
        "build_sidecar_windows.sh must reference ctranslate2/lib in its --include-data-dir flag."
    )
    # The flag must be the UNCONDITIONAL form (in the main nuitka invocation),
    # not wrapped in an `if [[ -d ... ]]` guard — ctranslate2/lib is mandatory.
    assert '--include-data-dir="$CT2_LIB_DIR=$CT2_LIB_DIR"' in windows_text, (
        "build_sidecar_windows.sh must have the unconditional "
        '--include-data-dir="$CT2_LIB_DIR=$CT2_LIB_DIR" flag in the main '
        "Nuitka invocation (ctranslate2/lib is mandatory, not optional)."
    )


def test_windows_bundles_ctranslate2_dll_explicitly(windows_text: str):
    """Windows must also ``--include-dll`` the ``ctranslate2.dll`` explicitly.

    ADR-0020 §4.2: Nuitka does NOT expand ``*.dll`` globs, so the main
    CTranslate2 DLL must be named explicitly via ``--include-dll``.
    This is the entry point that links against ``libiomp5md.dll``.
    """
    assert "--include-dll" in windows_text, (
        "build_sidecar_windows.sh must use --include-dll for ctranslate2.dll (Nuitka does not glob *.dll)."
    )
    assert "ctranslate2.dll" in windows_text, "build_sidecar_windows.sh must --include-dll ctranslate2.dll explicitly."


def test_windows_documents_libiomp5md_dll_in_header(windows_text: str):
    """The Windows script header must mention ``libiomp5md.dll`` by name.

    ADR-0020 §4.2 + §11 warn that the OpenMP runtime is "easy to miss"
    because Nuitka does NOT auto-collect it. The script header must
    document the filename so a future maintainer cannot accidentally
    delete the ``--include-data-dir`` line that bundles it.
    """
    assert "libiomp5md.dll" in windows_text, (
        "build_sidecar_windows.sh must document libiomp5md.dll in its header "
        "comment (ADR-0020 §4.2 + §11 'easy to miss, instant crash if absent')."
    )


# ─── 3. macOS: libiomp5.dylib (Intel OpenMP) bundling ────────────────────────
def test_macos_bundles_libiomp5_dylib_via_include_data_dir(macos_text: str):
    """macOS must ``--include-data-dir`` the ``ctranslate2/lib`` folder.

    ADR-0020 §4.3: CTranslate2 on macOS ships ``libctranslate2.dylib`` +
    ``libiomp5.dylib`` (OpenMP) under ``$SITE/ctranslate2/lib/``.
    Apple Silicon wheels are CPU-only (no CUDA) — OpenMP is the only
    parallel backend.
    """
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
    """The macOS script header must mention ``libiomp5.dylib`` by name.

    ADR-0020 §4.3 + §11: the OpenMP runtime is "easy to miss" because
    Nuitka does NOT auto-collect it. The script header must document
    the filename so a future maintainer cannot accidentally delete the
    ``--include-data-dir`` line that bundles it.
    """
    assert "libiomp5.dylib" in macos_text, (
        "build_sidecar_macos.sh must document libiomp5.dylib in its header "
        "comment (ADR-0020 §4.3 — the macOS OpenMP runtime)."
    )


# ─── 4. Linux: libgomp.so OR libiomp5.so (OpenMP) bundling ───────────────────
def test_linux_bundles_openmp_runtime_via_include_data_dir(linux_text: str):
    """Linux must ``--include-data-dir`` the ``ctranslate2/lib`` folder.

    ADR-0020 §4.4: CTranslate2 on Linux ships ``libctranslate2.so`` +
    ``libiomp5.so`` + ``libgomp.so`` (OpenMP) under
    ``$SITE/ctranslate2/lib/``. CPU-only on most installs; CUDA wheels
    exist but are large.
    """
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
    """The Linux script header must mention BOTH ``libgomp.so`` AND ``libiomp5.so``.

    ADR-0020 §4.4: Linux wheels ship BOTH OpenMP runtimes
    (``libiomp5.so`` + ``libgomp.so``) under ``$SITE/ctranslate2/lib/``.
    CTranslate2 may link against either, depending on how the wheel was
    built — the script header must document BOTH filenames so a future
    maintainer cannot accidentally delete the ``--include-data-dir`` line.

    Note: ADR-0020 §4.4 also mentions ``libgomp.so`` in the same breath
    as ``libiomp5.so``, so we assert BOTH are mentioned (not just one).
    """
    assert "libgomp.so" in linux_text or "libgomp" in linux_text, (
        "build_sidecar_linux.sh must document libgomp.so (GNU OpenMP) in its "
        "header comment (ADR-0020 §4.4 — Linux OpenMP runtime)."
    )
    assert "libiomp5.so" in linux_text or "libiomp5" in linux_text, (
        "build_sidecar_linux.sh must document libiomp5.so (Intel OpenMP) in "
        "its header comment (ADR-0020 §4.4 — Linux OpenMP runtime)."
    )


# ─── 5. ctranslate2/lib data-dir is UNCONDITIONAL in all 3 scripts ───────────
@pytest.mark.parametrize("script", ALL_BUILD_SCRIPTS)
def test_ctranslate2_lib_data_dir_is_unconditional(script: Path):
    """``ctranslate2/lib`` (singular) must be bundled unconditionally.

    The OpenMP runtime (``libiomp5md.dll`` / ``libiomp5.dylib`` /
    ``libgomp.so`` / ``libiomp5.so``) lives under ``ctranslate2/lib``.
    This directory is **mandatory** — without it, the frozen binary
    BUILDS but CRASHES on ``import ctranslate2`` (ADR-0020 §11).

    The script must NOT wrap the ``--include-data-dir`` for
    ``ctranslate2/lib`` in an ``if [[ -d ... ]]`` guard. The guard
    pattern (XPLAT-3) is reserved for ``ctranslate2/libs`` (PLURAL —
    the optional secondary dir).
    """
    text = script.read_text(encoding="utf-8")
    # Sanity: the script must reference ctranslate2/lib somewhere.
    assert "ctranslate2/lib" in text, (
        f"{script.name} must reference ctranslate2/lib (the mandatory dir containing the OpenMP runtime)."
    )
    # Find every --include-data-dir line that references ctranslate2/lib.
    # We do this by walking the script line-by-line and checking that any
    # --include-data-dir line mentioning "ctranslate2/lib" (and NOT
    # "ctranslate2/libs" plural) is NOT inside an `if [[ -d ... ]]` block.
    lines = text.splitlines()
    in_guard_block = False
    for i, line in enumerate(lines):
        stripped = line.strip()
        # Track entry into the `if [[ -d "$CT2_LIBS_DIR" ]]` guard block.
        # This guard is ONLY for the plural form (libs), so any
        # --include-data-dir for the singular form (lib) inside this
        # block would be a bug.
        if 'if [[ -d "$CT2_LIBS_DIR"' in stripped or ("CT2_LIBS_DIR" in stripped and stripped.startswith("if ")):
            in_guard_block = True
        # The guard block ends at the matching `fi`.
        if in_guard_block and stripped == "fi":
            in_guard_block = False
        # Check: any --include-data-dir line mentioning ctranslate2/lib
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
                "folder must be bundled UNCONDITIONALLY — it contains "
                "the OpenMP runtime (libiomp5md.dll / libiomp5.dylib / "
                "libgomp.so). See ADR-0020 §11."
            )


# 6. ctranslate2/libs data-dir is GUARDED ( pattern) ───────────────
def test_macos_has_xplat3_ctranslate2_libs_guard(macos_text: str):
    """macOS must guard ``ctranslate2/libs`` (plural) with ``if [[ -d ... ]]``.

    ADR-0020 §4.3 + XPLAT-3 pattern: ``ctranslate2/libs`` is the
    OPTIONAL secondary dir. Some wheel variants ship extra native libs
    under ``libs/`` (plural); CPU-only wheels do NOT have it. Nuitka's
    ``--include-data-dir`` fails hard if the source path is missing, so
    the script must guard it with ``if [[ -d "$CT2_LIBS_DIR" ]]``.
    """
    assert "CT2_LIBS_DIR" in macos_text, "build_sidecar_macos.sh must define CT2_LIBS_DIR (XPLAT-3 pattern)."
    assert 'if [[ -d "$CT2_LIBS_DIR" ]]' in macos_text, (
        "build_sidecar_macos.sh must guard the ctranslate2/libs include with "
        '`if [[ -d "$CT2_LIBS_DIR" ]]; then ... fi` (XPLAT-3 pattern).'
    )
    assert '--include-data-dir="$CT2_LIBS_DIR=$CT2_LIBS_DIR"' in macos_text, (
        "build_sidecar_macos.sh must --include-data-dir for $CT2_LIBS_DIR inside the guard block."
    )


def test_linux_has_xplat3_ctranslate2_libs_guard(linux_text: str):
    """Linux must guard ``ctranslate2/libs`` (plural) with ``if [[ -d ... ]]``.

    ADR-0020 §4.4 + XPLAT-3 pattern: ``ctranslate2/libs`` is the
    OPTIONAL secondary dir. CPU-only wheels (e.g. aarch64) ship
    ``libctranslate2.so`` + ``libiomp5.so`` under ``ctranslate2/lib/``
    ONLY, with no ``ctranslate2/libs/`` directory. Nuitka's
    ``--include-data-dir`` fails hard if the source path is missing,
    so the script must guard it with ``if [[ -d "$CT2_LIBS_DIR" ]]``.

    The Linux script is the **canonical reference source** for this
    XPLAT-3 pattern (it has the most thorough comments — see the
    "XPLAT-3-ctranslate2-guard" reference in the script header).
    """
    assert "CT2_LIBS_DIR" in linux_text, "build_sidecar_linux.sh must define CT2_LIBS_DIR (XPLAT-3 pattern)."
    assert 'if [[ -d "$CT2_LIBS_DIR" ]]' in linux_text, (
        "build_sidecar_linux.sh must guard the ctranslate2/libs include with "
        '`if [[ -d "$CT2_LIBS_DIR" ]]; then ... fi` (XPLAT-3 pattern).'
    )
    assert '--include-data-dir="$CT2_LIBS_DIR=$CT2_LIBS_DIR"' in linux_text, (
        "build_sidecar_linux.sh must --include-data-dir for $CT2_LIBS_DIR inside the guard block."
    )


def test_windows_known_gap_no_ctranslate2_libs_guard(windows_text: str):
    """BUILD-2 fix: the Windows script now HAS the ctranslate2/libs guard.

    Previously a KNOWN GAP — the Windows script only included the singular
    ``lib/`` with no guard for the optional ``libs/`` dir. BUILD-2 added
    the guard (mirroring the Linux + macOS XPLAT-3 pattern). This test
    now ASSERTS the guard IS present.

    See:
      - build_sidecar_linux.sh lines ~213 + ~229 (the XPLAT-3 guard)
      - build_sidecar_macos.sh lines ~106 + ~137 (the same guard)
      - build_sidecar_windows.sh lines ~127 + ~144 (BUILD-2 guard)
    """
    # The Linux + macOS siblings MUST have the libs guard (sanity check
    # that our reference pattern is correct).
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


# ─── 7. Cross-platform sibling parity sanity checks ──────────────────────────
def test_macos_sibling_uses_nuitka_args_array_pattern(macos_text: str):
    """Sanity: macOS sibling uses the ``NUITKA_ARGS=(...)`` array pattern.

    The macOS + Linux scripts build the Nuitka arg list incrementally
    (the macOS script appends the optional ``--include-data-dir`` for
    ``ctranslate2/libs`` if it exists). The array pattern is what makes
    this conditional append possible.
    """
    assert "NUITKA_ARGS=(" in macos_text
    assert '"${NUITKA_ARGS[@]}"' in macos_text


def test_linux_sibling_uses_nuitka_args_array_pattern(linux_text: str):
    """Sanity: Linux sibling uses the ``NUITKA_ARGS=(...)`` array pattern."""
    assert "NUITKA_ARGS=(" in linux_text
    assert '"${NUITKA_ARGS[@]}"' in linux_text


def test_all_three_scripts_reference_ctranslate2_package(windows_text: str, macos_text: str, linux_text: str):
    """All 3 scripts must ``--include-package=ctranslate2``.

    Without this, Nuitka would not include the ctranslate2 Python
    package itself — the OpenMP runtime DLLs are useless if the
    Python module that loads them is absent.
    """
    for label, text in (("windows", windows_text), ("macos", macos_text), ("linux", linux_text)):
        assert "--include-package=ctranslate2" in text, (
            f"build_sidecar_{label}.sh must --include-package=ctranslate2 "
            "(the Python package — distinct from the data-dir that bundles "
            "the native DLLs)."
        )


def test_all_three_scripts_reference_faster_whisper_package(windows_text: str, macos_text: str, linux_text: str):
    """All 3 scripts must ``--include-package=faster_whisper``.

    faster-whisper is the consumer of ctranslate2 — without it, the
    OpenMP runtime is never loaded. ADR-0020 §4.2/§4.3/§4.4 all
    mandate this flag.
    """
    for label, text in (("windows", windows_text), ("macos", macos_text), ("linux", linux_text)):
        assert "--include-package=faster_whisper" in text, (
            f"build_sidecar_{label}.sh must --include-package=faster_whisper "
            "(the consumer of ctranslate2 that loads the OpenMP runtime)."
        )
