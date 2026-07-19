"""MIG-1.6 Phase 0-M Gate Check 1 — Nuitka macOS build validation (x86_64 + aarch64).

This test file is the **first of 9 gate checks** in the Phase 0-M
macOS host validation gate (ADR-0020). It validates the *structure*
of:

  - ``scripts/build/build_sidecar_macos.sh`` — freezes
    ``voice_typer/server/ipc_server.py`` into
    ``python-sidecar-<arch>-apple-darwin`` via Nuitka, AND
  - ``scripts/build/build_prewarm_macos.sh`` — freezes
    ``voice_typer/server/prewarm.py`` into
    ``prewarm-<arch>-apple-darwin`` via Nuitka.

The Linux sandbox CANNOT run a real Nuitka macOS build (no Xcode /
swiftc, no Apple codesign, no python-build-standalone
cpython-3.12.x+<arch>-apple-darwin). These tests therefore:

  - validate the bash scripts are syntactically valid (``bash -n``),
  - validate the sidecar script supports BOTH arches via a positional
    ``$1`` arg (``aarch64`` / ``x86_64``), defaulting to host arch,
  - validate the sidecar script contains the ADR-0020 §4.3-mandated
    Nuitka flags (``--standalone``, ``--onefile``,
    ``--enable-plugin=numpy``, ``--include-package=faster_whisper``,
    ``--include-package=ctranslate2``),
  - validate the sidecar script has the XPLAT-3 ``ctranslate2/libs``
    (plural) existence guard,
  - validate the sidecar script produces the
    ``python-sidecar-x86_64-apple-darwin`` +
    ``python-sidecar-aarch64-apple-darwin`` output filenames,
  - validate the sidecar script references ``python-build-standalone``
    cpython-3.12.x for BOTH arches (header comment + dynamic triple
    construction),
  - validate the prewarm script produces the
    ``prewarm-x86_64-apple-darwin`` +
    ``prewarm-aarch64-apple-darwin`` output filenames,
  - validate the sidecar script handles the macOS-specific
    ``--macos-app-mode=background`` flag (sets ``LSUIElement=true`` in
    the bundle ``Info.plist`` — the macOS equivalent of Windows
    ``--windows-disable-console``),
  - document the exact ``VALIDATE ON MACOS HOST`` commands a human must
    run on a real macOS host for BOTH arches.

VALIDATE ON MACOS HOST (x86_64):
    1. xcode-select --install
    2. curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh; rustup default stable-x86_64-apple-darwin
    3. brew install python@3.12 uv
    4. uv venv; source .venv/bin/activate
    5. uv pip install -e ".[dev,test]" nuitka==2.5.4 zstandard ordered-set
    6. Download python-build-standalone cpython-3.12.x+x86_64-apple-darwin to /tmp/pybs/python
       (pinned: cpython-3.12.x install_only tarball — see docs/migration/macos-validation-runbook.md §0)
    7. ARCH=x86_64 bash scripts/build/build_sidecar_macos.sh x86_64
       (on Apple Silicon host: also `softwareupdate --install-rosetta --agree-to-license`)
    Expected: python-sidecar-x86_64-apple-darwin (~180 MB) in src-tauri/bin/

VALIDATE ON MACOS HOST (aarch64 — Apple Silicon):
    1. Same prerequisites as above
    2. rustup default stable-aarch64-apple-darwin
    3. Download cpython-3.12.x+aarch64-apple-darwin to /tmp/pybs/python
    4. ARCH=aarch64 bash scripts/build/build_sidecar_macos.sh aarch64
    Expected: python-sidecar-aarch64-apple-darwin (~180 MB) in src-tauri/bin/

References:
  - ADR-0020 §4.3 — Nuitka macOS freeze spec (authoritative for both arches).
  - ADR-0020 §4.5 — Common Nuitka caveats (per-triple verify).
  - docs/migration/macos-validation-runbook.md §0 + §1 — exact host commands.
  - scripts/build/build_sidecar_linux.sh — sibling (XPLAT-3 guard reference).
  - scripts/build/build_sidecar_windows.sh — sibling (XPLAT-3 gap, see MIG-1.5).

Gaps documented (report, do NOT fix — out of scope for this gate check):
  - GAP-1: ``build_sidecar_macos.sh`` does NOT invoke ``arch -x86_64`` to
    cross-build the Intel binary on an Apple Silicon host, NOR does it pass
    ``--target-arch x86_64`` to Nuitka. ADR-0020 §4.3 + the macOS
    validation runbook §1 (lines 95-97) say "the script auto-prepends
    `arch -x86_64` and passes `--target-arch x86_64` to Nuitka" but the
    actual script only relies on Rosetta 2 being installed by CI. The
    build still works on a real Intel macos-13 host; it just does not
    match the runbook's claim of Rosetta-based x86_64 builds on an
    Apple Silicon host. See ``test_known_gap_no_arch_x86_64_prefix``.
  - GAP-2: ``build_sidecar_macos.sh`` does NOT include
    ``--include-package=pyobjc`` (or the framework sub-packages
    ``pyobjc-core`` / ``pyobjc-framework-CoreAudio`` /
    ``pyobjc-framework-Cocoa``). ADR-0020 §4.3 (line ~434) requires
    these for volume ducking + tray. The dev ``pyproject.toml``
    declares the pyobjc deps for Darwin, so they will be present in
    the build env, but Nuitka may not bundle them without the explicit
    ``--include-package=pyobjc`` flag. See
    ``test_known_gap_no_pyobjc_include_flag``.
  - GAP-3: ``build_prewarm_macos.sh`` ``--check`` arg is a stub — it
    immediately exits 0 without verifying the toolchain (unlike the
    sidecar script's ``--check`` which actually imports nuitka +
    faster_whisper + ctranslate2 + checks swiftc). See
    ``test_known_gap_prewarm_check_is_stub``.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

# ─── Project paths ───────────────────────────────────────────────────────────
# This test file lives at tests/tauri/mig16/test_nuitka_macos_build.py.
# Path from file → root:
#   parents[0] = mig16/
#   parents[1] = tauri/
#   parents[2] = tests/
#   parents[3] = <project root> (voice-typer/)
PROJECT_ROOT = Path(__file__).resolve().parents[3]
SIDECAR_SCRIPT = PROJECT_ROOT / "scripts" / "build" / "build_sidecar_macos.sh"
PREWARM_SCRIPT = PROJECT_ROOT / "scripts" / "build" / "build_prewarm_macos.sh"
LINUX_SIDECAR_SCRIPT = PROJECT_ROOT / "scripts" / "build" / "build_sidecar_linux.sh"


# ─── Fixtures ────────────────────────────────────────────────────────────────
@pytest.fixture(scope="module")
def sidecar_text() -> str:
    """Read the sidecar build script once per module; fail fast if missing."""
    assert SIDECAR_SCRIPT.is_file(), (
        f"build_sidecar_macos.sh not found at {SIDECAR_SCRIPT}. Did the project layout change?"
    )
    return SIDECAR_SCRIPT.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def prewarm_text() -> str:
    """Read the prewarm build script once per module; fail fast if missing."""
    assert PREWARM_SCRIPT.is_file(), (
        f"build_prewarm_macos.sh not found at {PREWARM_SCRIPT}. Did the project layout change?"
    )
    return PREWARM_SCRIPT.read_text(encoding="utf-8")


# ─── 1. Existence + bash syntax validation ───────────────────────────────────
def test_sidecar_build_script_exists():
    """The macOS sidecar build script must exist at the canonical path."""
    assert SIDECAR_SCRIPT.is_file(), f"missing: {SIDECAR_SCRIPT}"
    # Also assert it's non-empty (a stub would be a regression).
    assert SIDECAR_SCRIPT.stat().st_size > 1000, (
        f"{SIDECAR_SCRIPT} is suspiciously small ({SIDECAR_SCRIPT.stat().st_size} bytes); "
        "expected a full Nuitka invocation script (~3-5 KB)."
    )


def test_prewarm_build_script_exists():
    """The macOS prewarm build script must exist at the canonical path."""
    assert PREWARM_SCRIPT.is_file(), f"missing: {PREWARM_SCRIPT}"
    assert PREWARM_SCRIPT.stat().st_size > 1000, (
        f"{PREWARM_SCRIPT} is suspiciously small ({PREWARM_SCRIPT.stat().st_size} bytes); "
        "expected a full Nuitka invocation script (~3-5 KB)."
    )


def test_sidecar_script_is_bash_syntax_valid():
    """``bash -n`` must parse the sidecar script without syntax errors.

    This is the only test that actually invokes bash; ``-n`` only parses,
    it does NOT execute the script, so no Nuitka / swiftc / python is
    spawned. Safe to run on the Linux sandbox.
    """
    if not shutil.which("bash"):
        pytest.skip("bash not available on this host — cannot run `bash -n`.")
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
    if not shutil.which("bash"):
        pytest.skip("bash not available on this host — cannot run `bash -n`.")
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
        "build_sidecar_macos.sh must start with `#!/usr/bin/env bash`"
    )
    assert "set -euo pipefail" in sidecar_text, (
        "build_sidecar_macos.sh must enable strict mode (`set -euo pipefail`) "
        "so a missing dylib or failed import aborts the build instead of "
        "producing a broken sidecar."
    )


# ─── 2. Both arches supported via $1 positional arg ──────────────────────────
def test_sidecar_script_supports_both_arches_via_arg(sidecar_text: str):
    """The sidecar script must accept ``aarch64`` OR ``x86_64`` as ``$1``.

    ADR-0020 §4.3: Nuitka cannot cross-compile to a universal binary,
    so the same script must serve both arches via the first positional
    arg. The macOS validation runbook §1 invokes:
      ``scripts/build/build_sidecar_macos.sh aarch64``
      ``scripts/build/build_sidecar_macos.sh x86_64``
    """
    assert 'ARCH="${1:-}"' in sidecar_text, (
        "build_sidecar_macos.sh must read ARCH from the first positional "
        'arg: `ARCH="${1:-}"`. (macOS validation runbook §1.)'
    )
    # The case statement must accept BOTH arches.
    assert "x86_64|aarch64)" in sidecar_text or ("x86_64)" in sidecar_text and "aarch64)" in sidecar_text), (
        "build_sidecar_macos.sh must accept both x86_64 + aarch64 in the ARCH validation case statement."
    )


def test_sidecar_script_defaults_to_host_arch(sidecar_text: str):
    """When no arg is given, the script must default to ``uname -m``.

    ``arm64`` host → ``aarch64``; ``x86_64`` host → ``x86_64``. This
    lets a dev run ``bash scripts/build/build_sidecar_macos.sh`` on
    either host without specifying the arch explicitly.
    """
    assert "uname -m" in sidecar_text, (
        "build_sidecar_macos.sh must default ARCH via `uname -m` when no positional arg is given."
    )
    assert "arm64)" in sidecar_text and ('ARCH="aarch64"' in sidecar_text or "ARCH=aarch64" in sidecar_text), (
        "build_sidecar_macos.sh must map arm64 → aarch64."
    )
    assert 'ARCH="x86_64"' in sidecar_text or "ARCH=x86_64" in sidecar_text, (
        "build_sidecar_macos.sh must map x86_64 host → x86_64 arch."
    )


def test_sidecar_script_rejects_unsupported_arch(sidecar_text: str):
    """The script must hard-fail with ``exit 1`` on an unsupported arch."""
    assert "exit 1" in sidecar_text
    # The arch case statement must have a `*)` wildcard error branch.
    assert "*)" in sidecar_text
    assert "arch must be x86_64 or aarch64" in sidecar_text or ("unsupported" in sidecar_text.lower()), (
        "build_sidecar_macos.sh must print a clear error if ARCH is not x86_64 or aarch64."
    )


# ─── 3. ADR-0020 §4.3 mandated Nuitka flags ─────────────────────────────────
EXPECTED_NUITKA_FLAGS = [
    "--standalone",
    "--onefile",
    "--assume-yes-for-downloads",
    "--enable-plugin=numpy",
    "--include-package=faster_whisper",
    "--include-package=ctranslate2",
    "--include-package=voice_typer",
    "--include-package=websockets",
    "--onefile-tempdir-spec",
    "--output-filename",
    "--output-dir",
]


@pytest.mark.parametrize("flag", EXPECTED_NUITKA_FLAGS)
def test_sidecar_script_contains_expected_nuitka_flag(sidecar_text: str, flag: str):
    """Each ADR-0020 §4.3-mandated Nuitka flag must be present in the sidecar script."""
    assert flag in sidecar_text, (
        f"build_sidecar_macos.sh is missing required Nuitka flag `{flag}`. "
        "ADR-0020 §4.3 mandates this flag for the macOS sidecar freeze."
    )


def test_sidecar_script_includes_ctranslate2_data_dir(sidecar_text: str):
    """The script must ``--include-data-dir`` the ctranslate2/lib folder.

    Without this, ``libctranslate2.dylib`` + ``libiomp5.dylib`` are
    missing from the bundle and the frozen binary BUILDS but CRASHES on
    ``import ctranslate2`` (ADR-0020 §4.3 + §11 Known Issues).
    """
    assert "--include-data-dir" in sidecar_text
    assert "ctranslate2/lib" in sidecar_text, (
        "build_sidecar_macos.sh must include --include-data-dir for "
        "$SITE/ctranslate2/lib (captures libctranslate2.dylib + libiomp5.dylib)."
    )


# ─── 4. ctranslate2/libs guard (plural — XPLAT-3 pattern, REQUIRED on macOS) ─
def test_sidecar_script_has_xplat3_ctranslate2_libs_guard(sidecar_text: str):
    """The sidecar script must have the XPLAT-3 ``ctranslate2/libs`` guard.

    The XPLAT-3 pattern (added to Linux + macOS siblings after MIG-1.5
    GAP-1 was filed against the Windows script) guards the optional
    ``--include-data-dir=$SITE/ctranslate2/libs=...`` flag with an
    ``if [[ -d "$CT2_LIBS_DIR" ]]`` block. Some CTranslate2 wheel
    variants ship extra native libs under ``libs/`` (plural) on macOS;
    the guard skips the flag if the dir doesn't exist.

    Reference: ``scripts/build/build_sidecar_linux.sh`` (XPLAT-3 source).
    """
    assert "CT2_LIBS_DIR=" in sidecar_text, (
        "build_sidecar_macos.sh must define CT2_LIBS_DIR (the ctranslate2/libs plural path)."
    )
    assert 'if [[ -d "$CT2_LIBS_DIR" ]]' in sidecar_text, (
        "build_sidecar_macos.sh must guard the optional libs/ include with "
        '`if [[ -d "$CT2_LIBS_DIR" ]]; then ... fi` (XPLAT-3 pattern).'
    )
    assert '--include-data-dir="$CT2_LIBS_DIR=$CT2_LIBS_DIR"' in sidecar_text, (
        "build_sidecar_macos.sh must add --include-data-dir for CT2_LIBS_DIR inside the XPLAT-3 guard block."
    )


def test_sidecar_script_has_ctranslate2_lib_guard_singular(sidecar_text: str):
    """The script must hard-fail if ``$SITE/ctranslate2/lib`` (singular) is missing.

    The singular ``lib/`` is REQUIRED (contains libctranslate2.dylib);
    the plural ``libs/`` is OPTIONAL (XPLAT-3 guard above).
    """
    assert "CT2_LIB_DIR=" in sidecar_text
    assert '! -d "$CT2_LIB_DIR"' in sidecar_text, (
        'build_sidecar_macos.sh must guard: `if [[ ! -d "$CT2_LIB_DIR" ]]; then echo ERROR ...; exit 1; fi`'
    )


# ─── 5. Output filenames for BOTH arches ────────────────────────────────────
def test_sidecar_script_uses_triple_variable_construction(sidecar_text: str):
    """The sidecar script must build TRIPLE from ARCH via ``${ARCH}-apple-darwin``.

    This is the pattern that lets a single script serve both x86_64 +
    aarch64 by passing the arch as the first positional arg.
    """
    assert "${ARCH}-apple-darwin" in sidecar_text, (
        'build_sidecar_macos.sh must construct TRIPLE dynamically: TRIPLE="${ARCH}-apple-darwin"'
    )


def test_sidecar_script_output_filename_pattern(sidecar_text: str):
    """The output filename must match ``python-sidecar-<triple>``.

    Tauri v2's ``externalBin`` mechanism appends the Rust target triple
    to the base name at runtime; the frozen binary filename MUST end
    with the triple for Tauri to select it (ADR-0020 §4.1 + §7).
    """
    assert "python-sidecar-" in sidecar_text, (
        "build_sidecar_macos.sh output filename must start with `python-sidecar-` (Tauri externalBin base name)."
    )
    assert "python-sidecar-${TRIPLE}" in sidecar_text, (
        "build_sidecar_macos.sh must construct OUTPUT_NAME as python-sidecar-${TRIPLE} (or equivalent)."
    )


def test_sidecar_script_documents_both_arch_output_filenames(sidecar_text: str):
    """The script header must document BOTH arch output filenames.

    Per ADR-0020 §4.3 the script serves both Apple Silicon AND Intel;
    the header comment must list both ``python-sidecar-aarch64-apple-darwin``
    and ``python-sidecar-x86_64-apple-darwin`` so operators can verify
    which file to expect from which arch invocation.
    """
    assert "python-sidecar-x86_64-apple-darwin" in sidecar_text, (
        "build_sidecar_macos.sh header must document the x86_64-apple-darwin output filename."
    )
    assert "python-sidecar-aarch64-apple-darwin" in sidecar_text, (
        "build_sidecar_macos.sh header must document the aarch64-apple-darwin output filename."
    )


def test_sidecar_script_outputs_to_src_tauri_bin(sidecar_text: str):
    """The output directory must be ``src-tauri/bin`` (Tauri externalBin location)."""
    assert "src-tauri/bin" in sidecar_text, (
        "build_sidecar_macos.sh must output to src-tauri/bin/ (the location "
        "Tauri's externalBin mechanism expects sidecar binaries)."
    )


def test_sidecar_script_verifies_output_after_build(sidecar_text: str):
    """The script must verify the output binary exists after Nuitka completes."""
    assert "OUTPUT_PATH" in sidecar_text
    assert '! -f "$OUTPUT_PATH"' in sidecar_text, (
        'build_sidecar_macos.sh must verify: `if [[ ! -f "$OUTPUT_PATH" ]]; then echo ERROR; exit 1; fi`'
    )


# ─── 6. python-build-standalone cpython-3.12.x for both arches ──────────────
def test_sidecar_script_references_python_build_standalone(sidecar_text: str):
    """The script must reference ``python-build-standalone`` as the base interpreter.

    ADR-0020 §4.3: the Nuitka target interpreter is a clean
    python-build-standalone cpython-3.12.x build (not the system Python,
    not a Homebrew Python). The script header must document this.
    """
    assert "python-build-standalone" in sidecar_text, (
        "build_sidecar_macos.sh must reference python-build-standalone "
        "(ADR-0020 §4.3 mandates a clean cpython-3.12.x install as the "
        "Nuitka target interpreter)."
    )


def test_sidecar_script_references_cpython_3_12(sidecar_text: str):
    """The script must pin the interpreter to ``cpython-3.12.x``.

    ADR-0020 §4.3: ``cpython-3.12.x+aarch64-apple-darwin`` (Apple
    Silicon) or ``cpython-3.12.x+x86_64-apple-darwin`` (Intel). The
    script header must document this version pin so operators download
    the matching python-build-standalone tarball.
    """
    assert "cpython-3.12" in sidecar_text, (
        "build_sidecar_macos.sh must reference cpython-3.12.x (the ADR-0020 "
        "§4.3 pinned interpreter version for BOTH arches)."
    )


def test_sidecar_script_documents_both_arch_interpreters(sidecar_text: str):
    """The script header must document BOTH per-arch interpreters.

    The python-build-standalone install_only tarball layout is
    per-arch: ``cpython-3.12.x+aarch64-apple-darwin`` and
    ``cpython-3.12.x+x86_64-apple-darwin``. The script header must
    reference the ``<arch>-apple-darwin`` triple so operators know to
    download the matching tarball.
    """
    # The header docstring mentions the per-arch python-build-standalone
    # builds. Both arches must appear in the script.
    assert "x86_64-apple-darwin" in sidecar_text, (
        "build_sidecar_macos.sh must reference the x86_64-apple-darwin "
        "triple (Intel python-build-standalone cpython-3.12.x)."
    )
    assert "aarch64-apple-darwin" in sidecar_text, (
        "build_sidecar_macos.sh must reference the aarch64-apple-darwin "
        "triple (Apple Silicon python-build-standalone cpython-3.12.x)."
    )


def test_sidecar_script_discovers_pybs_via_env_var(sidecar_text: str):
    """The script must discover the python-build-standalone install via env var.

    Discovery priority (per script header):
      1. ``$VOICE_TYPER_PYBS_DIR/python/bin/python3`` (CI workflow sets this)
      2. ``$PYBS`` (explicit path)
      3. ``command -v python3`` (dev fallback)

    The CI workflow unpacks the per-arch cpython-3.12.x tarball into
    ``$VOICE_TYPER_PYBS_DIR`` and the script picks it up from there.
    """
    assert "VOICE_TYPER_PYBS_DIR" in sidecar_text, (
        "build_sidecar_macos.sh must discover python-build-standalone via $VOICE_TYPER_PYBS_DIR (set by CI workflow)."
    )
    assert "${PYBS" in sidecar_text, "build_sidecar_macos.sh must support the $PYBS env var override."
    assert "command -v python3" in sidecar_text, "build_sidecar_macos.sh must fall back to `command -v python3`."


def test_sidecar_script_uses_pybs_install_only_layout(sidecar_text: str):
    """The script must reference the python-build-standalone install_only layout.

    python-build-standalone extracts to ``$DIR/python/bin/python3``
    (install_only tarball layout). ADR-0020 §4.3 mandates this as the
    base interpreter for Nuitka freezing.
    """
    assert "python/bin/python3" in sidecar_text, (
        "build_sidecar_macos.sh must reference python-build-standalone's "
        "install_only layout: $PYBS_DIR/python/bin/python3."
    )


# ─── 7. macOS-specific flags (LSUIElement) ──────────────────────────────────
def test_sidecar_script_uses_macos_app_mode_background(sidecar_text: str):
    """The script must pass ``--macos-app-mode=background`` (LSUIElement=true).

    ADR-0020 §4.3: ``--macos-app-mode=background`` sets
    ``LSUIElement=true`` in the bundle's ``Info.plist`` — the sidecar
    runs with no Dock icon, no menu bar item. This is the macOS
    equivalent of Windows ``--windows-disable-console``.

    (Nuitka also accepts ``--macos-app-mode=gui`` and
    ``--macos-app-mode=console``; only ``background`` produces the
    LSUIElement=true behavior the sidecar requires.)
    """
    assert "--macos-app-mode=background" in sidecar_text, (
        "build_sidecar_macos.sh must pass --macos-app-mode=background to "
        "Nuitka (ADR-0020 §4.3 — LSUIElement=true, no Dock icon)."
    )


def test_sidecar_script_uses_macos_create_bundle(sidecar_text: str):
    """The script must pass ``--macos-create-bundle`` (+ app name + signed name).

    ADR-0020 §4.3: ``--macos-create-bundle`` produces a ``.app`` bundle
    directory so codesign + notarization can attach to the bundle's
    ``Info.plist``. The ``--macos-signed-app-name`` must match the
    ``CFBundleIdentifier`` used for code signing (see signing-guide §13.2).
    """
    assert "--macos-create-bundle" in sidecar_text
    assert "--macos-app-name=" in sidecar_text
    assert "--macos-signed-app-name=" in sidecar_text, (
        "build_sidecar_macos.sh must pass --macos-signed-app-name (matches "
        "the CFBundleIdentifier used for codesign — see signing-guide.md §13.2)."
    )


def test_sidecar_script_onefile_tempdir_uses_app_support(sidecar_text: str):
    """``--onefile-tempdir-spec`` must pin to ``$HOME/Library/Application Support``.

    ADR-0020 §4.3: pinning the extract dir prevents tempdir bloat from
    onefile re-extractions across launches. The macOS convention is
    ``$HOME/Library/Application Support/voice-typer/onefile-tmp``.
    """
    assert "Library/Application Support" in sidecar_text, (
        "build_sidecar_macos.sh --onefile-tempdir-spec must use "
        "$HOME/Library/Application Support/voice-typer/onefile-tmp "
        "(macOS convention, ADR-0020 §4.3)."
    )


# ─── 8. Prewarm build script produces prewarm-<triple> for both arches ──────
def test_prewarm_script_uses_triple_variable_construction(prewarm_text: str):
    """The prewarm script must build TRIPLE from ARCH via ``${ARCH}-apple-darwin``."""
    assert "${ARCH}-apple-darwin" in prewarm_text, (
        'build_prewarm_macos.sh must construct TRIPLE dynamically: TRIPLE="${ARCH}-apple-darwin"'
    )


def test_prewarm_script_output_filename_pattern(prewarm_text: str):
    """The prewarm output filename must match ``prewarm-<triple>``.

    ADR-0020 §5: the prewarm binary is a Tauri ``bundle.resource`` (NOT
    a Tauri ``externalBin``), launched by the macOS LaunchAgent at
    ``~/Library/LaunchAgents/com.voicetyper.prewarm.plist`` via
    ``resolve_prewarm_exe()``. The triple suffix is required so the
    resolver picks the right arch at runtime.
    """
    assert "prewarm-" in prewarm_text
    assert "prewarm-${TRIPLE}" in prewarm_text, (
        "build_prewarm_macos.sh must construct OUTPUT_NAME as prewarm-${TRIPLE} (or equivalent)."
    )


def test_prewarm_script_documents_both_arch_output_filenames(prewarm_text: str):
    """The prewarm script header must document BOTH arch output filenames."""
    assert "prewarm-x86_64-apple-darwin" in prewarm_text, (
        "build_prewarm_macos.sh header must document the x86_64-apple-darwin output filename."
    )
    assert "prewarm-aarch64-apple-darwin" in prewarm_text, (
        "build_prewarm_macos.sh header must document the aarch64-apple-darwin output filename."
    )


def test_prewarm_script_outputs_to_resources_dir(prewarm_text: str):
    """The prewarm output dir must be ``src-tauri/resources`` (bundle.resource)."""
    assert "src-tauri/resources" in prewarm_text, (
        "build_prewarm_macos.sh must output to src-tauri/resources/ (Tauri "
        "bundle.resource location — NOT src-tauri/bin, since prewarm is "
        "launched by the LaunchAgent, not as a Tauri externalBin)."
    )


def test_prewarm_script_supports_both_arches_via_arg(prewarm_text: str):
    """The prewarm script must accept ``aarch64`` OR ``x86_64`` as ``$1``."""
    assert 'ARCH="${1:-}"' in prewarm_text
    assert "x86_64|aarch64)" in prewarm_text or ("x86_64)" in prewarm_text and "aarch64)" in prewarm_text), (
        "build_prewarm_macos.sh must accept both x86_64 + aarch64 arches."
    )


def test_prewarm_script_uses_macos_app_mode_background(prewarm_text: str):
    """The prewarm script must also pass ``--macos-app-mode=background``."""
    assert "--macos-app-mode=background" in prewarm_text, (
        "build_prewarm_macos.sh must pass --macos-app-mode=background "
        "(prewarm runs with no Dock icon — LSUIElement=true)."
    )


def test_prewarm_script_entry_point_is_prewarm_py(prewarm_text: str):
    """The Nuitka entry point must be ``voice_typer/server/prewarm/__main__.py``.

    Originally the entry point was ``voice_typer/server/prewarm.py`` (a single
    module). After the SPLIT-4 refactor (god-file split into a focused package),
    the entry point is ``voice_typer/server/prewarm/__main__.py`` (the package's
    ``__main__`` runner — equivalent to ``python -m voice_typer.server.prewarm``).
    """
    assert "voice_typer/server/prewarm/__main__.py" in prewarm_text, (
        "build_prewarm_macos.sh entry point must be voice_typer/server/prewarm/__main__.py (ADR-0011 + ADR-0020 §5)."
    )


def test_prewarm_script_has_xplat3_ctranslate2_libs_guard(prewarm_text: str):
    """The prewarm script must also have the XPLAT-3 ctranslate2/libs guard."""
    assert "CT2_LIBS_DIR" in prewarm_text
    assert 'if [[ -d "$CT2_LIBS_DIR" ]]' in prewarm_text
    assert '--include-data-dir="$CT2_LIBS_DIR=$CT2_LIBS_DIR"' in prewarm_text


# ─── 9. Sanity checks: sidecar env validation + entry point ─────────────────
def test_sidecar_script_supports_check_mode(sidecar_text: str):
    """The sidecar script must support a ``--check`` arg to verify the toolchain."""
    assert '"--check"' in sidecar_text or "--check" in sidecar_text
    assert "import nuitka" in sidecar_text, "build_sidecar_macos.sh --check must verify nuitka is importable."
    assert "import faster_whisper, ctranslate2" in sidecar_text or (
        "import faster_whisper" in sidecar_text and "import ctranslate2" in sidecar_text
    ), "build_sidecar_macos.sh --check must verify faster_whisper + ctranslate2."


def test_sidecar_script_checks_swiftc(sidecar_text: str):
    """The sidecar script ``--check`` must verify ``swiftc`` is on PATH.

    ADR-0020 §4.3 + macOS validation runbook §0: ``swiftc`` is required
    for the Nuitka ``--macos-create-bundle`` path (it shells out to
    Xcode's bundling tools). A missing ``swiftc`` surfaces as an opaque
    Nuitka failure ~10 minutes in.
    """
    assert "command -v swiftc" in sidecar_text, (
        "build_sidecar_macos.sh --check must verify swiftc (Xcode CLT) is on PATH — required for --macos-create-bundle."
    )


def test_sidecar_script_sanity_checks_ctranslate2_import(sidecar_text: str):
    """The sidecar script must run an import sanity check before invoking Nuitka."""
    assert "import faster_whisper, ctranslate2, websockets" in sidecar_text, (
        "build_sidecar_macos.sh must sanity-check that faster_whisper + "
        "ctranslate2 + websockets all import in the build env."
    )
    assert "ctranslate2.__version__" in sidecar_text, (
        "build_sidecar_macos.sh must print ctranslate2.__version__ on the sanity-check line."
    )


def test_sidecar_script_resolves_site_packages(sidecar_text: str):
    """The script must resolve the build env's site-packages dir."""
    assert "site.getsitepackages()" in sidecar_text


def test_sidecar_script_entry_point_is_ipc_server(sidecar_text: str):
    """The Nuitka entry point must be ``voice_typer/server/ipc_server.py``."""
    assert "voice_typer/server/ipc_server.py" in sidecar_text


def test_sidecar_script_uses_nuitka_args_array(sidecar_text: str):
    """The script uses the ``NUITKA_ARGS`` bash array pattern.

    This is the cleanest way to conditionally append the XPLAT-3
    ``--include-data-dir=$SITE/ctranslate2/libs`` flag — bash arrays
    handle the quoting + the conditional ``+=`` append.
    """
    assert "NUITKA_ARGS=(" in sidecar_text
    assert '"${NUITKA_ARGS[@]}"' in sidecar_text
    assert "NUITKA_ARGS+=" in sidecar_text, (
        "build_sidecar_macos.sh must use `NUITKA_ARGS+=(...)` to conditionally "
        "append the XPLAT-3 libs/ flag inside the guard block."
    )


def test_sidecar_script_runs_otool_verify(sidecar_text: str):
    """The script must run ``otool -L`` on the ctranslate2 dylib after build.

    ADR-0020 §4.3: "Verify with ``otool -L $SITE/ctranslate2/lib/
    libctranslate2.dylib`` that every @rpath dependency resolves in the
    build env." The script does this conditionally (``command -v otool``)
    so it still passes on a stub-host.
    """
    assert "otool -L" in sidecar_text or "otool " in sidecar_text, (
        "build_sidecar_macos.sh must run otool -L on libctranslate2.dylib "
        "after a successful build (ADR-0020 §4.3 dylib verify)."
    )


def test_sidecar_script_documents_signing_next_step(sidecar_text: str):
    """The script must point to the signing runbook after a successful build."""
    assert "signing-guide.md" in sidecar_text or "codesign" in sidecar_text, (
        "build_sidecar_macos.sh must document the next step (codesign + "
        "notarize / signing-guide.md §13.2) after a successful build."
    )


# ─── 10. Sibling parity (Linux script has the XPLAT-3 guard) ────────────────
def test_linux_sibling_has_xplat3_ctranslate2_libs_guard():
    """Sanity check: the Linux sibling MUST have the XPLAT-3 guard.

    This is a reference-pattern check — if the Linux sibling loses the
    guard, the macOS sibling becomes the lone reference and the XPLAT-3
    pattern needs re-evaluation.
    """
    if not LINUX_SIDECAR_SCRIPT.is_file():
        pytest.skip(f"build_sidecar_linux.sh missing ({LINUX_SIDECAR_SCRIPT}) — cannot verify Linux sibling parity.")
    linux_text = LINUX_SIDECAR_SCRIPT.read_text(encoding="utf-8")
    assert "CT2_LIBS_DIR" in linux_text
    assert '--include-data-dir="$CT2_LIBS_DIR=$CT2_LIBS_DIR"' in linux_text
    assert 'if [[ -d "$CT2_LIBS_DIR" ]]' in linux_text


# ─── 11. KNOWN GAPS (report, do NOT fix — out of scope for this gate check) ─
def test_known_gap_no_arch_x86_64_prefix(sidecar_text: str):
    """KNOWN GAP (GAP-1): the script does NOT invoke ``arch -x86_64`` for
    Rosetta-based Intel builds on an Apple Silicon host, NOR does it pass
    ``--target-arch x86_64`` to Nuitka.

    The macOS validation runbook §1 (lines 95-97) claims: "the script
    auto-prepends ``arch -x86_64`` and passes ``--target-arch x86_64``
    to Nuitka". The ACTUAL script does NEITHER — it only relies on
    Rosetta 2 being installed by CI (header comment lines 12-14).

    On a real Intel macos-13 host this gap is invisible (host arch is
    already x86_64). On an Apple Silicon host attempting an x86_64
    build via Rosetta, the build will silently produce an aarch64
    binary (because the python-build-standalone interpreter chosen by
    ``$VOICE_TYPER_PYBS_DIR`` is the host arch, not x86_64). The
    CI matrix in ADR-0020 §4.3 sidesteps this by running the x86_64
    build on a dedicated macos-13 Intel runner.

    This test ASSERTS the gap is present (so a future fix will flip it
    to a passing assertion). DO NOT fix this gap as part of MIG-1.6
    gate check 1 — report it to the primary agent.
    """
    assert "arch -x86_64" not in sidecar_text, (
        "build_sidecar_macos.sh now invokes `arch -x86_64` — update this "
        "test to assert PRESENCE instead of absence, and remove GAP-1 "
        "from the module docstring."
    )
    assert "--target-arch" not in sidecar_text, (
        "build_sidecar_macos.sh now passes --target-arch to Nuitka — "
        "update this test to assert PRESENCE instead of absence."
    )


def test_known_gap_no_pyobjc_include_flag(sidecar_text: str):
    """KNOWN GAP (GAP-2): the script does NOT include ``--include-package=pyobjc``.

    ADR-0020 §4.3 (line ~434): "pyobjc deps: pyobjc-core,
    pyobjc-framework-CoreAudio, pyobjc-framework-Cocoa are required
    (volume ducking + tray). Add ``--include-package=pyobjc`` (and the
    framework sub-packages). Nuitka's ``--include-package=pyobjc`` does
    not always pick up the framework bridges — run the sidecar once in
    dev mode and watch for ``ImportError: pyobjc-...`` to discover
    missing pieces."

    The dev ``pyproject.toml`` declares the pyobjc deps for Darwin, so
    they will be present in the build env, but Nuitka may not bundle
    them without the explicit ``--include-package=pyobjc`` flag(s).
    The macOS validation runbook does NOT explicitly require this flag
    in §1 (it relies on the dev-mode ImportError discovery loop), so
    this gap is a SOFT gap — the build may succeed but produce a
    sidecar that crashes on first tray / volume-ducking call.

    This test ASSERTS the gap is present. DO NOT fix this gap as part
    of MIG-1.6 gate check 1 — report it to the primary agent.
    """
    assert "--include-package=pyobjc" not in sidecar_text, (
        "build_sidecar_macos.sh now includes --include-package=pyobjc — "
        "update this test to assert PRESENCE instead of absence, and "
        "remove GAP-2 from the module docstring."
    )


def test_known_gap_prewarm_check_is_stub(prewarm_text: str):
    """KNOWN GAP (GAP-3): ``build_prewarm_macos.sh --check`` is a stub.

    Unlike the sidecar script's ``--check`` (which actually imports
    nuitka + faster_whisper + ctranslate2 + checks swiftc), the prewarm
    script's ``--check`` immediately exits 0 with a one-line message
    delegating to the sidecar check. This means CI cannot independently
    verify the prewarm build env without first running the sidecar check.

    This test ASSERTS the gap is present. DO NOT fix this gap as part
    of MIG-1.6 gate check 1 — report it to the primary agent.
    """
    # The prewarm --check path exits 0 without importing anything.
    # The sidecar --check path imports nuitka + faster_whisper +
    # ctranslate2 + checks swiftc.
    assert "import nuitka" not in prewarm_text, (
        "build_prewarm_macos.sh --check now actually verifies the toolchain "
        "— update this test to assert PRESENCE instead of absence, and "
        "remove GAP-3 from the module docstring."
    )
    assert "same toolchain as build_sidecar_macos" in prewarm_text or ("OK if that passes" in prewarm_text), (
        "build_prewarm_macos.sh --check must still be the stub that "
        "delegates to build_sidecar_macos (GAP-3 expected pattern)."
    )
