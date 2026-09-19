"""
Nuitka Linux build validation (x86_64 + aarch64).
Gaps documented (report, do NOT fix, out of scope for this gate check):
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tests.fixtures.bash_utils import bash_usable

# Path from file → root:
PROJECT_ROOT = Path(__file__).resolve().parents[3]
SIDECAR_SCRIPT = PROJECT_ROOT / "scripts" / "build" / "build_sidecar_linux.sh"
PREWARM_SCRIPT = PROJECT_ROOT / "scripts" / "build" / "build_prewarm_linux.sh"
MACOS_SIDECAR_SCRIPT = PROJECT_ROOT / "scripts" / "build" / "build_sidecar_macos.sh"
WINDOWS_SIDECAR_SCRIPT = PROJECT_ROOT / "scripts" / "build" / "build_sidecar_windows.sh"


@pytest.fixture(scope="module")
def sidecar_text() -> str:
    """Read the sidecar build script once per module; fail fast if missing."""
    assert SIDECAR_SCRIPT.is_file(), (
        f"build_sidecar_linux.sh not found at {SIDECAR_SCRIPT}. Did the project layout change?"
    )
    return SIDECAR_SCRIPT.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def prewarm_text() -> str:
    """Read the prewarm build script once per module; fail fast if missing."""
    assert PREWARM_SCRIPT.is_file(), (
        f"build_prewarm_linux.sh not found at {PREWARM_SCRIPT}. Did the project layout change?"
    )
    return PREWARM_SCRIPT.read_text(encoding="utf-8")


def test_sidecar_build_script_exists():
    """The Linux sidecar build script must exist at the canonical path."""
    assert SIDECAR_SCRIPT.is_file(), f"missing: {SIDECAR_SCRIPT}"
    # Also assert it's non-empty (a stub would be a regression).
    assert SIDECAR_SCRIPT.stat().st_size > 1000, (
        f"{SIDECAR_SCRIPT} is suspiciously small ({SIDECAR_SCRIPT.stat().st_size} bytes); "
        "expected a full Nuitka invocation script (~6-10 KB)."
    )


def test_prewarm_build_script_exists():
    """The Linux prewarm build script must exist at the canonical path."""
    assert PREWARM_SCRIPT.is_file(), f"missing: {PREWARM_SCRIPT}"
    assert PREWARM_SCRIPT.stat().st_size > 1000, (
        f"{PREWARM_SCRIPT} is suspiciously small ({PREWARM_SCRIPT.stat().st_size} bytes); "
        "expected a full Nuitka invocation script (~3-5 KB)."
    )


def test_sidecar_script_is_bash_syntax_valid():
    """``bash -n`` must parse the sidecar script without syntax errors."""
    if not bash_usable():
        pytest.skip("bash not available or not usable on this host, cannot run `bash -n`.")
    result = subprocess.run(
        ["bash", "-n", str(SIDECAR_SCRIPT)],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, (
        f"bash -n failed on {SIDECAR_SCRIPT}:\n--- stderr ---\n{result.stderr}\n--- stdout ---\n{result.stdout}"
    )


def test_prewarm_script_is_bash_syntax_valid():
    """``bash -n`` must parse the prewarm script without syntax errors."""
    if not bash_usable():
        pytest.skip("bash not available or not usable on this host, cannot run `bash -n`.")
    result = subprocess.run(
        ["bash", "-n", str(PREWARM_SCRIPT)],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, (
        f"bash -n failed on {PREWARM_SCRIPT}:\n--- stderr ---\n{result.stderr}\n--- stdout ---\n{result.stdout}"
    )


def test_sidecar_script_has_shebang_and_strict_mode(sidecar_text: str):
    """The sidecar script must use ``#!/usr/bin/env bash`` + ``set -euo pipefail``."""
    assert sidecar_text.startswith("#!/usr/bin/env bash"), (
        "build_sidecar_linux.sh must start with `#!/usr/bin/env bash`"
    )
    assert "set -euo pipefail" in sidecar_text, (
        "build_sidecar_linux.sh must enable strict mode (`set -euo pipefail`) "
        "so a missing .so or failed import aborts the build instead of "
        "producing a broken sidecar."
    )


def test_sidecar_script_supports_both_arches_via_arg(sidecar_text: str):
    """The sidecar script must accept ``aarch64`` OR ``x86_64`` as ``$1``."""
    assert 'ARCH="${1:-}"' in sidecar_text, (
        "build_sidecar_linux.sh must read ARCH from the first positional "
        'arg: `ARCH="${1:-}"`. (Linux validation runbook §1.)'
    )
    assert (
        'x86_64" && "$ARCH" != "aarch64"' in sidecar_text
        or "x86_64|aarch64)" in sidecar_text
        or ("x86_64)" in sidecar_text and "aarch64)" in sidecar_text)
    ), (
        "build_sidecar_linux.sh must accept both x86_64 + aarch64 in the "
        "ARCH validation case statement (or via the negated `!= x86_64 "
        "&& != aarch64` usage-error guard)."
    )


def test_sidecar_script_normalizes_host_arch(sidecar_text: str):
    """The script must normalize ``arm64`` → ``aarch64`` + ``amd64`` → ``x86_64``."""
    assert "uname -m" in sidecar_text, "build_sidecar_linux.sh must read the host arch via `uname -m`."
    assert "aarch64|arm64)" in sidecar_text, (
        "build_sidecar_linux.sh must normalize arm64 → aarch64 in the host arch case statement."
    )
    assert "x86_64|amd64)" in sidecar_text, (
        "build_sidecar_linux.sh must normalize amd64 → x86_64 in the host arch case statement."
    )


def test_sidecar_script_rejects_unsupported_arch(sidecar_text: str):
    """The script must hard-fail with ``exit 1`` on an unsupported arch."""
    assert "exit 1" in sidecar_text
    assert '!= "x86_64" && "$ARCH" != "aarch64"' in sidecar_text or "*)" in sidecar_text, (
        "build_sidecar_linux.sh must print a clear usage error if ARCH is not x86_64 or aarch64."
    )
    assert "Usage:" in sidecar_text, "build_sidecar_linux.sh must print a `Usage:` line on arch error."


EXPECTED_NUITKA_FLAGS = [
    "--standalone",
    "--onefile",
    "--assume-yes-for-downloads",
    "--enable-plugin=numpy",
    "--include-package=faster_whisper",
    "--include-package=ctranslate2",
    "--include-package=voice_typer",
    "--include-package=websockets",
    "--include-package=numpy",
    "--onefile-tempdir-spec",
    "--output-filename",
    "--output-dir",
]


@pytest.mark.parametrize("flag", EXPECTED_NUITKA_FLAGS)
def test_sidecar_script_contains_expected_nuitka_flag(sidecar_text: str, flag: str):
    """Each ADR-0020 §4.4-mandated Nuitka flag must be present in the sidecar script."""
    assert flag in sidecar_text, (
        f"build_sidecar_linux.sh is missing required Nuitka flag `{flag}`. "
        "ADR-0020 §4.4 mandates this flag for the Linux sidecar freeze."
    )


def test_sidecar_script_includes_ctranslate2_data_dir(sidecar_text: str):
    """The script must ``--include-data-dir`` the ctranslate2/lib folder."""
    assert "--include-data-dir" in sidecar_text
    assert "ctranslate2/lib" in sidecar_text, (
        "build_sidecar_linux.sh must include --include-data-dir for "
        "$SITE/ctranslate2/lib (captures libctranslate2.so + libiomp5.so + "
        "libgomp.so per ADR-0020 §4.4)."
    )


def test_sidecar_script_onefile_tempdir_pinned_to_cache(sidecar_text: str):
    """
    ``--onefile-tempdir-spec`` must pin to ``$XDG_CACHE_HOME/voice-typer/onefile-tmp``.
    ADR-0020 §4.4: pinning the extract dir prevents tempdir bloat from
    """
    assert "XDG_CACHE_HOME" in sidecar_text, (
        "build_sidecar_linux.sh --onefile-tempdir-spec must use "
        "$XDG_CACHE_HOME/voice-typer/onefile-tmp (Linux convention, "
        "ADR-0020 §4.4)."
    )
    assert "voice-typer/onefile-tmp" in sidecar_text


# 4. ctranslate2/libs guard (plural, pattern, source on Linux) ──
def test_sidecar_script_has_xplat3_ctranslate2_libs_guard(sidecar_text: str):
    """The sidecar script must have the XPLAT-3 ``ctranslate2/libs`` guard."""
    assert "CT2_LIBS_DIR=" in sidecar_text, (
        "build_sidecar_linux.sh must define CT2_LIBS_DIR (the ctranslate2/libs plural path)."
    )
    assert 'if [[ -d "$CT2_LIBS_DIR" ]]' in sidecar_text, (
        "build_sidecar_linux.sh must guard the optional libs/ include with "
        '`if [[ -d "$CT2_LIBS_DIR" ]]; then ... fi` (XPLAT-3 pattern).'
    )
    assert '--include-data-dir="$CT2_LIBS_DIR=$CT2_LIBS_DIR"' in sidecar_text, (
        "build_sidecar_linux.sh must add --include-data-dir for CT2_LIBS_DIR inside the XPLAT-3 guard block."
    )


def test_sidecar_script_uses_nuitka_args_array(sidecar_text: str):
    """The script uses the ``NUITKA_ARGS`` bash array pattern."""
    assert "NUITKA_ARGS=(" in sidecar_text, "build_sidecar_linux.sh must declare the NUITKA_ARGS bash array."
    assert '"${NUITKA_ARGS[@]}"' in sidecar_text, (
        'build_sidecar_linux.sh must expand the NUITKA_ARGS array via "${NUITKA_ARGS[@]}" when invoking Nuitka.'
    )
    assert "NUITKA_ARGS+=" in sidecar_text, (
        "build_sidecar_linux.sh must use `NUITKA_ARGS+=(...)` to conditionally "
        "append the XPLAT-3 libs/ flag inside the guard block."
    )


def test_sidecar_script_documents_xplat3_guard_rationale(sidecar_text: str):
    """The script must document the XPLAT-3 guard rationale in a comment."""
    # The  comment block in build_sidecar_linux.sh mentions both
    assert "ctranslate2/libs" in sidecar_text
    assert "XPLAT-3" in sidecar_text or "CPU-only" in sidecar_text or "fails hard" in sidecar_text, (
        "build_sidecar_linux.sh must document the XPLAT-3 ctranslate2/libs "
        "guard rationale (CPU-only wheels lack libs/; Nuitka fails hard on "
        "missing source paths)."
    )


def test_sidecar_script_uses_triple_variable_construction(sidecar_text: str):
    """The sidecar script must build TRIPLE from ARCH via ``${ARCH}-unknown-linux-gnu``."""
    assert "${ARCH}-unknown-linux-gnu" in sidecar_text, (
        'build_sidecar_linux.sh must construct TRIPLE dynamically: TRIPLE="${ARCH}-unknown-linux-gnu"'
    )
    # The TRIPLE variable must be assigned (not just referenced).
    assert 'TRIPLE="${ARCH}-unknown-linux-gnu"' in sidecar_text, (
        "build_sidecar_linux.sh must assign TRIPLE=${ARCH}-unknown-linux-gnu right after the ARCH case statement."
    )


def test_sidecar_script_output_filename_pattern(sidecar_text: str):
    """The output filename must match ``python-sidecar-<triple>``."""
    assert "python-sidecar-" in sidecar_text, (
        "build_sidecar_linux.sh output filename must start with `python-sidecar-` (Tauri externalBin base name)."
    )
    assert (
        "python-sidecar-${TRIPLE}" in sidecar_text
        or "python-sidecar-$TRIPLE" in sidecar_text
        or 'OUTPUT_BIN="$OUTPUT_DIR/python-sidecar-$TRIPLE"' in sidecar_text
    ), "build_sidecar_linux.sh must construct OUTPUT_BIN as python-sidecar-$TRIPLE (or equivalent)."


def test_sidecar_script_documents_both_arch_output_filenames(sidecar_text: str):
    """The script header must document BOTH arch output filenames."""
    assert "x86_64-unknown-linux-gnu" in sidecar_text, (
        "build_sidecar_linux.sh header must reference the x86_64-unknown-linux-gnu "
        "triple (Intel/AMD python-build-standalone cpython-3.12.x)."
    )
    assert "aarch64-unknown-linux-gnu" in sidecar_text, (
        "build_sidecar_linux.sh header must reference the aarch64-unknown-linux-gnu "
        "triple (ARM64 python-build-standalone cpython-3.12.x)."
    )


def test_sidecar_script_outputs_to_src_tauri_bin(sidecar_text: str):
    """The output directory must be ``src-tauri/bin`` (Tauri externalBin location)."""
    assert "src-tauri/bin" in sidecar_text, (
        "build_sidecar_linux.sh must output to src-tauri/bin/ (the location "
        "Tauri's externalBin mechanism expects sidecar binaries)."
    )


def test_sidecar_script_verifies_output_after_build(sidecar_text: str):
    """The script must verify the output binary exists after Nuitka completes."""
    assert "OUTPUT_BIN" in sidecar_text
    assert '! -x "$OUTPUT_BIN"' in sidecar_text, (
        'build_sidecar_linux.sh must verify: `if [[ ! -x "$OUTPUT_BIN" ]]; then echo FAILED; exit 1; fi`'
    )


def test_sidecar_script_references_python_build_standalone(sidecar_text: str):
    """The script must reference ``python-build-standalone`` as the base interpreter."""
    assert "python-build-standalone" in sidecar_text, (
        "build_sidecar_linux.sh must reference python-build-standalone "
        "(ADR-0020 §4.4 mandates a clean cpython-3.12.x install as the "
        "Nuitka target interpreter)."
    )


def test_sidecar_script_references_cpython_3_12(sidecar_text: str):
    """The script must pin the interpreter to ``cpython-3.12.x``."""
    assert "cpython-3.12" in sidecar_text, (
        "build_sidecar_linux.sh must reference cpython-3.12.x (the ADR-0020 "
        "§4.4 pinned interpreter version for BOTH arches)."
    )
    # The auto-discovery glob uses `cpython-3.12.*+<triple>` so a patch
    assert "cpython-3.12.*+" in sidecar_text, (
        "build_sidecar_linux.sh must auto-discover the patch version via the "
        "`cpython-3.12.*+<triple>` glob (so a python-build-standalone release "
        "bump does NOT require a script edit)."
    )


def test_sidecar_script_discovers_pybs_via_env_var(sidecar_text: str):
    """The script must discover the python-build-standalone install via env var."""
    assert "VOICE_TYPER_PYBS_DIR" in sidecar_text, (
        "build_sidecar_linux.sh must discover python-build-standalone via $VOICE_TYPER_PYBS_DIR (set by CI workflow)."
    )
    # The default fallback path must be the project-local .python-build-standalone.
    assert "${PROJECT_ROOT}/.python-build-standalone" in sidecar_text or (
        "$PROJECT_ROOT/.python-build-standalone" in sidecar_text
    ), (
        "build_sidecar_linux.sh must default VOICE_TYPER_PYBS_DIR to "
        "$PROJECT_ROOT/.python-build-standalone (dev fallback)."
    )


def test_sidecar_script_uses_pybs_install_only_layout(sidecar_text: str):
    """The script must reference the python-build-standalone install_only layout."""
    assert "python/bin/python3" in sidecar_text, (
        "build_sidecar_linux.sh must reference python-build-standalone's "
        "install_only layout: $PYBS_DIR/python/bin/python3."
    )


def test_sidecar_script_supports_verbose_pybs_layout(sidecar_text: str):
    """The script must ALSO support the verbose ``cpython-3.12.*+<triple>`` layout."""
    assert 'cpython-3.12.*+"$TRIPLE"/python/bin/python3' in sidecar_text, (
        "build_sidecar_linux.sh auto-discovery must try "
        "$PYBS_DIR/cpython-3.12.*+<triple>/python/bin/python3 (verbose layout)."
    )
    assert 'cpython-3.12.*+"$TRIPLE"/bin/python3' in sidecar_text, (
        "build_sidecar_linux.sh auto-discovery must try "
        "$PYBS_DIR/cpython-3.12.*+<triple>/bin/python3 (alt verbose layout)."
    )


def test_sidecar_script_documents_glibc_2_35_baseline(sidecar_text: str):
    """The script header must document the glibc 2.35 (Ubuntu 22.04) baseline."""
    assert "glibc 2.35" in sidecar_text or "GLIBC_2.35" in sidecar_text, (
        "build_sidecar_linux.sh must document the glibc 2.35 baseline (ADR-0020 §4.4. Ubuntu 22.04 floor)."
    )
    assert "Ubuntu 22.04" in sidecar_text, (
        "build_sidecar_linux.sh must reference Ubuntu 22.04 as the baseline distro for the glibc 2.35 pin."
    )


def test_sidecar_script_verifies_glibc_after_build(sidecar_text: str):
    """The script must verify the built binary's max glibc ≤ 2.35."""
    assert "verify_glibc" in sidecar_text, "build_sidecar_linux.sh must define a verify_glibc function."
    assert "GLIBC_" in sidecar_text, (
        "build_sidecar_linux.sh verify_glibc must look for GLIBC_* version markers in the ldd/objdump output."
    )
    assert "ldd" in sidecar_text, "build_sidecar_linux.sh verify_glibc must use ldd for native binaries."
    assert "objdump -p" in sidecar_text, (
        "build_sidecar_linux.sh verify_glibc must use objdump -p for cross "
        "binaries (ldd won't run aarch64 ELF on an x86_64 host without "
        "qemu in the right mode)."
    )


def test_sidecar_script_fails_on_glibc_above_baseline(sidecar_text: str):
    """The script must ``exit 1`` if the binary requires glibc > 2.35."""
    # The script does integer comparison on major + minor glibc version.
    assert '"-gt 2"' in sidecar_text or "-gt 2" in sidecar_text, (
        "build_sidecar_linux.sh verify_glibc must compare the glibc major "
        "version against 2 (the Ubuntu 22.04 baseline major)."
    )
    assert '"-gt 35"' in sidecar_text or "-gt 35" in sidecar_text, (
        "build_sidecar_linux.sh verify_glibc must compare the glibc minor "
        "version against 35 (the Ubuntu 22.04 baseline minor)."
    )
    # And the failure message must mention the baseline.
    assert "GLIBC_2.35" in sidecar_text or "glibc 2.35" in sidecar_text


def test_sidecar_script_checks_patchelf_available(sidecar_text: str):
    """The script must verify ``patchelf`` is on PATH before invoking Nuitka."""
    assert "command -v patchelf" in sidecar_text, (
        "build_sidecar_linux.sh must verify patchelf is on PATH (Nuitka --standalone on Linux requires it)."
    )
    assert "patchelf not found" in sidecar_text or "patchelf" in sidecar_text


def test_sidecar_script_supports_qemu_cross_build(sidecar_text: str):
    """The script must support the aarch64 cross-build path via qemu-user-static."""
    assert "qemu-aarch64-static" in sidecar_text, (
        "build_sidecar_linux.sh must reference qemu-aarch64-static for the aarch64 cross-build path."
    )
    assert "CROSS_BUILD" in sidecar_text, (
        "build_sidecar_linux.sh must set a CROSS_BUILD flag when ARCH=aarch64 is requested on a non-aarch64 host."
    )
    assert "command -v qemu-aarch64-static" in sidecar_text, (
        "build_sidecar_linux.sh must verify qemu-aarch64-static is on PATH before attempting the cross-build."
    )
    assert "binfmt_misc" in sidecar_text, (
        "build_sidecar_linux.sh must reference binfmt_misc (the kernel "
        "feature that lets the host execute aarch64 ELF directly)."
    )


def test_sidecar_script_verifies_cross_interpreter_arch(sidecar_text: str):
    """The script must verify the cross-build interpreter is actually aarch64."""
    assert "file -b" in sidecar_text or "file " in sidecar_text, (
        "build_sidecar_linux.sh must use `file` to check the interpreter's ELF arch in the cross-build path."
    )
    assert "ARM" in sidecar_text or "aarch64" in sidecar_text


def test_sidecar_script_smoke_runs_help_after_build(sidecar_text: str):
    """The script must smoke-test the built binary with ``--help``."""
    assert "--help" in sidecar_text, "build_sidecar_linux.sh must smoke-test the built binary with --help."
    assert "smoke" in sidecar_text.lower(), "build_sidecar_linux.sh must label the --help invocation as a smoke test."


def test_prewarm_script_uses_triple_variable_construction(prewarm_text: str):
    """The prewarm script must build TRIPLE from ARCH via ``${ARCH}-unknown-linux-gnu``."""
    assert "${ARCH}-unknown-linux-gnu" in prewarm_text, (
        'build_prewarm_linux.sh must construct TRIPLE dynamically: TRIPLE="${ARCH}-unknown-linux-gnu"'
    )


def test_prewarm_script_output_filename_pattern(prewarm_text: str):
    """The prewarm output filename must match ``prewarm-<triple>``."""
    assert "prewarm-" in prewarm_text
    assert (
        "prewarm-${TRIPLE}" in prewarm_text
        or "prewarm-$TRIPLE" in prewarm_text
        or 'OUTPUT_NAME="prewarm-${TRIPLE}"' in prewarm_text
    ), "build_prewarm_linux.sh must construct OUTPUT_NAME as prewarm-${TRIPLE} (or equivalent)."


def test_prewarm_script_documents_both_arch_output_filenames(prewarm_text: str):
    """The prewarm script header must document BOTH arch output filenames."""
    assert "prewarm-x86_64-unknown-linux-gnu" in prewarm_text, (
        "build_prewarm_linux.sh header must document the x86_64-unknown-linux-gnu output filename."
    )
    assert "prewarm-aarch64-unknown-linux-gnu" in prewarm_text, (
        "build_prewarm_linux.sh header must document the aarch64-unknown-linux-gnu output filename."
    )


def test_prewarm_script_outputs_to_resources_dir(prewarm_text: str):
    """The prewarm output dir must be ``src-tauri/resources`` (bundle.resource)."""
    assert "src-tauri/resources" in prewarm_text, (
        "build_prewarm_linux.sh must output to src-tauri/resources/ (Tauri "
        "bundle.resource location, NOT src-tauri/bin, since prewarm is "
        "launched by the systemd user timer, not as a Tauri externalBin)."
    )


def test_prewarm_script_supports_both_arches_via_arg(prewarm_text: str):
    """The prewarm script must accept ``aarch64`` OR ``x86_64`` as ``$1``."""
    assert 'ARCH="${1:-}"' in prewarm_text
    assert "x86_64|aarch64)" in prewarm_text or ("x86_64)" in prewarm_text and "aarch64)" in prewarm_text), (
        "build_prewarm_linux.sh must accept both x86_64 + aarch64 arches."
    )


def test_prewarm_script_defaults_to_host_arch(prewarm_text: str):
    """When no arg is given, the prewarm script must default to ``uname -m``."""
    assert "uname -m" in prewarm_text, (
        "build_prewarm_linux.sh must default ARCH via `uname -m` when no positional arg is given."
    )


def test_prewarm_script_entry_point_is_prewarm_py(prewarm_text: str):
    """The Nuitka entry point must be ``voice_typer/server/prewarm/__main__.py``."""
    assert "voice_typer/server/prewarm/__main__.py" in prewarm_text, (
        "build_prewarm_linux.sh entry point must be voice_typer/server/prewarm/__main__.py (ADR-0011 + ADR-0020 §5)."
    )


def test_prewarm_script_has_xplat3_ctranslate2_libs_guard(prewarm_text: str):
    """The prewarm script must also have the XPLAT-3 ctranslate2/libs guard."""
    assert "CT2_LIBS_DIR" in prewarm_text
    assert 'if [[ -d "$CT2_LIBS_DIR" ]]' in prewarm_text
    assert '--include-data-dir="$CT2_LIBS_DIR=$CT2_LIBS_DIR"' in prewarm_text


# 9. Sibling parity (macOS + Windows siblings have the  guard) ────
def test_macos_sibling_has_xplat3_ctranslate2_libs_guard():
    """Sanity check: the macOS sibling MUST have the XPLAT-3 guard."""
    if not MACOS_SIDECAR_SCRIPT.is_file():
        pytest.skip(f"build_sidecar_macos.sh missing ({MACOS_SIDECAR_SCRIPT}), cannot verify macOS sibling parity.")
    macos_text = MACOS_SIDECAR_SCRIPT.read_text(encoding="utf-8")
    assert "CT2_LIBS_DIR" in macos_text
    assert '--include-data-dir="$CT2_LIBS_DIR=$CT2_LIBS_DIR"' in macos_text
    assert 'if [[ -d "$CT2_LIBS_DIR" ]]' in macos_text


def test_windows_sibling_known_gap_no_xplat3_ctranslate2_libs_guard():
    """BUILD-2 fix: the Windows sibling now HAS the XPLAT-3 guard."""
    if not WINDOWS_SIDECAR_SCRIPT.is_file():
        pytest.skip(
            f"build_sidecar_windows.sh missing ({WINDOWS_SIDECAR_SCRIPT}), cannot verify Windows sibling parity."
        )
    windows_text = WINDOWS_SIDECAR_SCRIPT.read_text(encoding="utf-8")
    assert "CT2_LIB_DIR=" in windows_text, (
        "build_sidecar_windows.sh must define CT2_LIB_DIR (singular, the required ctranslate2/lib dir)."
    )
    assert "CT2_LIBS_DIR" in windows_text, (
        "build_sidecar_windows.sh should define CT2_LIBS_DIR (plural. BUILD-2 guard)."
    )
    assert 'if [[ -d "$CT2_LIBS_DIR" ]]' in windows_text, (
        "build_sidecar_windows.sh should guard the libs include with if [[ -d (BUILD-2 fix)."
    )


def test_build_sidecar_linux_supports_check_mode(sidecar_text: str):
    """BUILD-1 fix: ``build_sidecar_linux.sh`` now supports ``--check``."""
    assert 'if [[ "$ARCH" == "--check" ]]' in sidecar_text, (
        "build_sidecar_linux.sh should support --check mode (BUILD-1 fix)."
    )


def test_known_gap_no_python_import_sanity_check(sidecar_text: str):
    """
    KNOWN GAP (GAP-2): the script does NOT do a Python-level import sanity check.
    This test ASSERTS the gap is present. DO NOT fix this gap as part
    """
    assert "import faster_whisper, ctranslate2, websockets" not in sidecar_text, (
        "build_sidecar_linux.sh now does a Python-level import sanity check "
        "— update this test to assert PRESENCE instead of absence, and "
        "remove GAP-2 from the module docstring."
    )
    # The script DOES check directory existence (so a fully-missing
    assert '! -d "$SITE/faster_whisper"' in sidecar_text, (
        "build_sidecar_linux.sh must still check the faster_whisper dir "
        "exists (partial mitigation for GAP-2, directory check, not "
        "Python import)."
    )


def test_known_gap_prewarm_check_is_stub(prewarm_text: str):
    """
    KNOWN GAP (GAP-3): ``build_prewarm_linux.sh --check`` is a stub.
    This test ASSERTS the gap is present. DO NOT fix this gap as part
    """
    assert "build_sidecar_linux.sh" in prewarm_text and "--check" in prewarm_text, (
        "build_prewarm_linux.sh --check must delegate to build_sidecar_linux.sh --check "
        "so the prewarm build env is actually verified (WR-18 fixed the stub)."
    )
    assert "import nuitka" not in prewarm_text, (
        "build_prewarm_linux.sh --check should delegate (not independently import nuitka) "
        "— the sidecar check covers the shared Nuitka toolchain."
    )


def test_known_gap_sudo_binfmt_in_cross_path(sidecar_text: str):
    """
    KNOWN GAP (GAP-4): the script invokes ``sudo update-binfmts`` in the cross path.
    This test ASSERTS the gap is present. DO NOT fix this gap as part
    """
    assert "sudo update-binfmts --enable qemu-aarch64" in sidecar_text, (
        "build_sidecar_linux.sh cross-build path still invokes "
        "`sudo update-binfmts --enable qemu-aarch64` (GAP-4 expected pattern)."
    )
    # The `|| true` swallow is the second half of the gap.
    assert "sudo update-binfmts --enable qemu-aarch64 || true" in sidecar_text, (
        "build_sidecar_linux.sh must still swallow the sudo update-binfmts "
        "failure with `|| true` (GAP-4 expected pattern)."
    )
