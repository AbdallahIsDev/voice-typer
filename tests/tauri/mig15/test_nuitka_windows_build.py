"""MIG-1.5 Phase 0-W Gate Check 1 — Nuitka Windows .exe build validation.

This test file is the **first of 9 gate checks** in the Phase 0-W
Windows host validation gate (ADR-0020). It validates the *structure*
of ``scripts/build/build_sidecar_windows.sh`` — the bash entrypoint
that freezes ``voice_typer/server/ipc_server.py`` into
``python-sidecar-x86_64-pc-windows-msvc.exe`` via Nuitka.

The Linux sandbox CANNOT run a real Nuitka Windows build (no MSVC,
no Windows SDK, no python-build-standalone cpython-3.12.x+x86_64-pc-
windows-msvc). These tests therefore:
  - validate the bash script is syntactically valid (``bash -n``),
  - validate the script contains the ADR-0020 §4.2-mandated Nuitka
    flags,
  - validate the script references the correct target triple + output
    filename pattern,
  - validate the script has the ``ctranslate2/lib`` existence guard,
  - document (and assert) the known gap that the Windows script does
    NOT have a ``ctranslate2/libs`` (plural) guard like the Linux +
    macOS siblings (XPLAT-3 pattern), and
  - document the exact ``VALIDATE ON WINDOWS HOST`` commands a human
    must run on a real Windows 10 22H2 / Windows 11 host.

VALIDATE ON WINDOWS HOST:
    1. winget install Microsoft.VisualStudio.2022.BuildTools --override "--add Microsoft.VisualStudio.Workload.VCTools"
    2. rustup default stable-x86_64-pc-windows-msvc
    3. pip install uv; uv venv; .venv\\Scripts\\activate
    4. uv pip install -e ".[dev,test]" nuitka==2.5.4 zstandard ordered-set
    5. Download python-build-standalone cpython-3.12.x+x86_64-pc-windows-msvc to C:\\tools\\pybs\\python
       (pinned: cpython-3.12.8+20241219 — see docs/migration/windows-validation-runbook.md §0.7)
    6. bash scripts/build/build_sidecar_windows.sh
    Expected: python-sidecar-x86_64-pc-windows-msvc.exe (~150-200 MB) produced in src-tauri/bin/

References:
  - ADR-0020 §4.2 — Nuitka Windows freeze spec (authoritative).
  - docs/migration/windows-validation-runbook.md §1 — exact host commands.
  - scripts/build/build_sidecar_linux.sh — sibling with XPLAT-3
    ctranslate2/libs guard pattern.
  - scripts/build/build_sidecar_macos.sh — sibling with NUITKA_ARGS array
    pattern.

Gaps documented (report, do NOT fix — out of scope for this gate check):
  - GAP-1: ``build_sidecar_windows.sh`` does NOT include a
    ``ctranslate2/libs`` (plural) existence guard like the Linux +
    macOS siblings. The Windows wheel layout puts all DLLs under
    ``ctranslate2/lib`` (singular), so this is likely benign on
    Windows, but for XPLAT-3 pattern parity the script could grow a
    defensive ``if [[ -d "$CT2_LIBS_DIR" ]]; then ...`` block.
    See ``test_known_gap_no_ctranslate2_libs_guard``.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tests.fixtures.bash_utils import bash_usable

# ─── Project paths ───────────────────────────────────────────────────────────
# This test file lives at tests/tauri/mig15/test_nuitka_windows_build.py.
# Path from file → root:
#   parents[0] = mig15/
#   parents[1] = tauri/
#   parents[2] = tests/
#   parents[3] = <project root> (voice-typer/)
PROJECT_ROOT = Path(__file__).resolve().parents[3]
BUILD_SCRIPT = PROJECT_ROOT / "scripts" / "build" / "build_sidecar_windows.sh"
LINUX_BUILD_SCRIPT = PROJECT_ROOT / "scripts" / "build" / "build_sidecar_linux.sh"
MACOS_BUILD_SCRIPT = PROJECT_ROOT / "scripts" / "build" / "build_sidecar_macos.sh"


# ─── Fixtures ────────────────────────────────────────────────────────────────
@pytest.fixture(scope="module")
def script_text() -> str:
    """Read the build script once per module; fail fast if missing."""
    assert BUILD_SCRIPT.is_file(), (
        f"build_sidecar_windows.sh not found at {BUILD_SCRIPT}. Did the project layout change?"
    )
    return BUILD_SCRIPT.read_text(encoding="utf-8")


# ─── 1. Existence + bash syntax validation ───────────────────────────────────
def test_build_script_exists():
    """The Windows build script must exist at the canonical path."""
    assert BUILD_SCRIPT.is_file(), f"missing: {BUILD_SCRIPT}"
    # Also assert it's non-empty (a stub would be a regression).
    assert BUILD_SCRIPT.stat().st_size > 1000, (
        f"{BUILD_SCRIPT} is suspiciously small ({BUILD_SCRIPT.stat().st_size} bytes); "
        "expected a full Nuitka invocation script (~3-5 KB)."
    )


def test_build_script_is_bash_syntax_valid():
    """``bash -n`` must parse the script without syntax errors.

    This is the only test that actually invokes bash; ``-n`` only parses,
    it does NOT execute the script, so no Nuitka / cl.exe / python is
    spawned. Safe to run on the Linux sandbox.
    """
    if not bash_usable():
        pytest.skip("bash not available or not usable on this host — cannot run `bash -n`.")
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


# ─── 2. ADR-0020 §4.2 mandated Nuitka flags ──────────────────────────────────
# The authoritative flag list is in ADR-0020 §4.2 + the module docstring of
# build_sidecar_windows.sh. Each flag below is asserted to appear verbatim in
# the script text.
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
    """The script must ``--include-data-dir`` the ctranslate2/lib folder.

    Without this, ``libiomp5md.dll`` + MKL/OpenMP DLLs are missing from
    the bundle and the frozen exe BUILDS but CRASHES on
    ``import ctranslate2`` (ADR-0020 §4.2 + §11 Known Issues).
    """
    assert "--include-data-dir" in script_text
    assert "ctranslate2/lib" in script_text, (
        "build_sidecar_windows.sh must include --include-data-dir for "
        "$SITE/ctranslate2/lib (captures libiomp5md.dll + MKL/OpenMP DLLs)."
    )


def test_script_includes_ctranslate2_dll(script_text: str):
    """The script must ``--include-dll`` the ctranslate2.dll explicitly.

    Nuitka does NOT expand ``*.dll`` globs, so the main CTranslate2 DLL
    must be named explicitly (ADR-0020 §4.2).
    """
    assert "--include-dll" in script_text
    assert "ctranslate2.dll" in script_text, (
        "build_sidecar_windows.sh must --include-dll ctranslate2.dll explicitly (Nuitka does not glob *.dll)."
    )


def test_script_onefile_tempdir_uses_localappdata(script_text: str):
    """``--onefile-tempdir-spec`` must pin to ``%LOCALAPPDATA%\\voice-typer``.

    ADR-0020 §4.2 + §11 Known Issues: pinning the extract dir prevents
    tempdir bloat from onefile re-extractions across launches.
    """
    assert "%LOCALAPPDATA%" in script_text, (
        "build_sidecar_windows.sh --onefile-tempdir-spec must use "
        "%LOCALAPPDATA% (Windows env var expanded by Nuitka at runtime)."
    )
    assert "voice-typer" in script_text


# ─── 3. Target triple + output filename pattern ──────────────────────────────
def test_script_references_x86_64_target_triple(script_text: str):
    """The script must reference ``x86_64-pc-windows-msvc`` (primary triple)."""
    assert "x86_64-pc-windows-msvc" in script_text, (
        "build_sidecar_windows.sh must reference the x86_64-pc-windows-msvc "
        "triple (primary Windows target per ADR-0020 §4.1)."
    )


def test_script_references_aarch64_target_triple(script_text: str):
    """The script must also support ``aarch64-pc-windows-msvc`` (Windows-on-ARM).

    ADR-0020 §4.1 lists both Windows triples; the script header documents
    both as supported outputs. (Phase 0-W v1 gate is x86_64-only, but the
    script must not have lost aarch64 support.)
    """
    assert "aarch64-pc-windows-msvc" in script_text, (
        "build_sidecar_windows.sh must reference aarch64-pc-windows-msvc "
        "(secondary Windows target per ADR-0020 §4.1 + script header)."
    )


def test_script_uses_triple_variable_construction(script_text: str):
    """The script must build TRIPLE from ARCH via ``${ARCH}-pc-windows-msvc``.

    This is the pattern that lets a single script serve both x86_64 + aarch64
    by passing the arch as the first positional arg.
    """
    assert "${ARCH}-pc-windows-msvc" in script_text, (
        'build_sidecar_windows.sh must construct TRIPLE dynamically: TRIPLE="${ARCH}-pc-windows-msvc"'
    )


def test_script_output_filename_pattern(script_text: str):
    """The output filename must match ``python-sidecar-<triple>.exe``.

    Tauri v2's ``externalBin`` mechanism appends the Rust target triple to
    the base name at runtime; the frozen .exe filename MUST end with the
    triple + ``.exe`` for Tauri to select it (ADR-0020 §4.1 + §7).
    """
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


# ─── 4. ctranslate2/lib guard (singular — layout-aware, REQUIRED) ───────────
def test_script_has_ctranslate2_lib_guard(script_text: str):
    """The script must bundle ctranslate2's native DLLs from EITHER layout.

    ctranslate2 ships its DLLs either under ``ctranslate2/lib`` (older
    wheels) or directly in ``ctranslate2/`` (modern wheels — e.g. the
    cp312 win_amd64 wheel has ctranslate2.dll + cudnn64_9.dll +
    libiomp5md.dll at the package root). The script prefers the
    ``lib/`` layout and falls back to the package dir; it hard-fails
    only when the ctranslate2 package itself is missing or
    ``ctranslate2.dll`` is absent from the resolved layout. Without
    this, the build would produce a broken .exe (no libiomp5md.dll /
    ctranslate2.dll) that crashes on ``import ctranslate2`` at launch.
    """
    # The script must define the lib/ path and resolve the native-DLL
    # location from EITHER layout (old lib/ wheels OR modern
    # package-root wheels — mirrors the inline command in
    # .github/workflows/tauri-windows-build.yml).
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
    # ctranslate2.dll is mandatory in the resolved layout — hard-fail when absent.
    assert '! -f "$CT2_DLL"' in script_text, (
        'build_sidecar_windows.sh must guard: `if [[ ! -f "$CT2_DLL" ]]; then echo ERROR ...; exit 1; fi`'
    )


def test_script_has_ctranslate2_dll_guard(script_text: str):
    """The script must hard-fail if ``ctranslate2.dll`` is missing."""
    assert '! -f "$CT2_DLL"' in script_text or '! -f "$CT2_LIB_DIR/ctranslate2.dll"' in script_text, (
        'build_sidecar_windows.sh must guard: `if [[ ! -f "$CT2_DLL" ]]; then echo ERROR ...; exit 1; fi`'
    )


# ─── 5. ctranslate2/libs guard (plural — KNOWN GAP) ──────────────────────────
def test_known_gap_no_ctranslate2_libs_guard(script_text: str):
    """XPLAT-3 / BUILD-2 parity: the Windows script now HAS a
    ``ctranslate2/libs`` (plural) existence guard like the Linux + macOS
    siblings.

    Previously a KNOWN GAP — the Windows script only included the singular
    ``lib/`` with no guard for the optional ``libs/`` dir. BUILD-2 added
    the guard (mirroring the Linux + macOS XPLAT-3 pattern). This test
    now ASSERTS the guard IS present.

    See:
      - build_sidecar_linux.sh lines ~213 + ~229 (the XPLAT-3 guard)
      - build_sidecar_macos.sh lines ~106 + ~137 (the same guard)
      - build_sidecar_windows.sh lines ~127 + ~144 (BUILD-2 guard — added this run)
    """
    # The Linux + macOS siblings MUST have the libs guard (sanity check
    # that our reference pattern is correct).
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


# ─── 6. Env var / path validation before Nuitka invocation ──────────────────
def test_script_supports_check_mode(script_text: str):
    """The script must support a ``--check`` arg to verify the toolchain.

    This is the dry-run path: it confirms python + nuitka +
    faster_whisper + ctranslate2 are all importable in the build env
    WITHOUT invoking Nuitka. Used by CI to fail fast on a misconfigured
    host.
    """
    assert '"--check"' in script_text or "--check" in script_text, (
        "build_sidecar_windows.sh must support a --check arg (toolchain verification without invoking Nuitka)."
    )
    assert "import nuitka" in script_text, "build_sidecar_windows.sh --check must verify nuitka is importable."
    assert "import faster_whisper, ctranslate2" in script_text or (
        "import faster_whisper" in script_text and "import ctranslate2" in script_text
    ), "build_sidecar_windows.sh --check must verify faster_whisper + ctranslate2."


def test_script_validates_python_interpreter(script_text: str):
    """The script must hard-fail if no Python interpreter is found.

    The discovery priority is:
      1. ``$VOICE_TYPER_PYBS_DIR/python/python.exe`` (CI workflow)
      2. ``$PYBS`` env var (explicit path)
      3. ``command -v python`` (dev fallback)

    All three paths must be present; if none resolve, the script must
    ``exit 1`` with a clear error.
    """
    assert "VOICE_TYPER_PYBS_DIR" in script_text, (
        "build_sidecar_windows.sh must discover python via $VOICE_TYPER_PYBS_DIR (set by CI workflow, ADR-0020 §4.2)."
    )
    assert "${PYBS" in script_text, "build_sidecar_windows.sh must support the $PYBS env var override."
    assert "command -v python" in script_text, "build_sidecar_windows.sh must fall back to `command -v python`."
    assert "no python interpreter found" in script_text, (
        "build_sidecar_windows.sh must emit a clear error if no python interpreter is discovered."
    )


def test_script_validates_python_build_standalone_layout(script_text: str):
    """The script must reference the python-build-standalone install layout.

    python-build-standalone extracts to ``$DIR/python/python.exe``
    (install_only tarball layout). ADR-0020 §4.2 mandates this as the
    base interpreter for Nuitka freezing.
    """
    assert "python/python.exe" in script_text, (
        "build_sidecar_windows.sh must reference python-build-standalone's "
        "install_only layout: $PYBS_DIR/python/python.exe."
    )


def test_script_sanity_checks_ctranslate2_import(script_text: str):
    """The script must run an ``import faster_whisper, ctranslate2, websockets``
    sanity check in the build env before invoking Nuitka.

    This catches a misconfigured build env early (saves 10-15 min of
    Nuitka compile time before the failure surfaces).
    """
    assert "import faster_whisper, ctranslate2, websockets" in script_text, (
        "build_sidecar_windows.sh must sanity-check that "
        "faster_whisper + ctranslate2 + websockets all import in the build env."
    )
    assert "ctranslate2.__version__" in script_text, (
        "build_sidecar_windows.sh must print ctranslate2.__version__ on the "
        "sanity-check line (proves the wheel is the real one, not a stub)."
    )


def test_script_resolves_site_packages(script_text: str):
    """The script must resolve the build env's site-packages dir.

    Needed to construct the ``--include-data-dir=$SITE/ctranslate2/lib``
    and ``--include-dll=$SITE/ctranslate2/lib/ctranslate2.dll`` paths.
    """
    assert "site.getsitepackages()" in script_text, (
        "build_sidecar_windows.sh must resolve $SITE via "
        "`site.getsitepackages()[0]` so --include-data-dir paths are correct."
    )


def test_script_entry_point_is_ipc_server(script_text: str):
    """The Nuitka entry point must be ``voice_typer/server/ipc_server.py``.

    This is the same entry point used by the Electron path + the dev
    sidecar — only the freeze tool changes (ADR-0020 §4.2).
    """
    assert "voice_typer/server/ipc_server.py" in script_text, (
        "build_sidecar_windows.sh entry point must be "
        "voice_typer/server/ipc_server.py (matches Electron + dev sidecar)."
    )


def test_script_verifies_output_after_build(script_text: str):
    """The script must verify the output .exe exists after Nuitka completes.

    A silent Nuitka failure (e.g. onefile compression error) can leave
    no output file; the script must hard-fail in that case rather than
    report success.
    """
    assert "OUTPUT_PATH" in script_text
    assert '! -f "$OUTPUT_PATH"' in script_text, (
        'build_sidecar_windows.sh must verify: `if [[ ! -f "$OUTPUT_PATH" ]]; then echo ERROR; exit 1; fi`'
    )


def test_script_documents_signing_next_step(script_text: str):
    """The script must point to the signing runbook after a successful build.

    ADR-0020 §13.1 — Windows Authenticode signing is the next step
    after the .exe is produced. The script's final echo must reference
    signing-guide.md.
    """
    assert "signtool" in script_text.lower() or "signing-guide.md" in script_text, (
        "build_sidecar_windows.sh must document the next step (signtool / "
        "signing-guide.md §13.1) after a successful build."
    )


# 7. Sibling parity (Linux + macOS scripts have the  guard) ───────
def test_linux_sibling_has_xplat3_ctranslate2_libs_guard():
    """Sanity check: the Linux sibling MUST have the XPLAT-3 guard.

    This is a reference-pattern check — if the Linux sibling loses the
    guard, the GAP-1 note for Windows becomes invalid (we'd be measuring
    against a moving target).
    """
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
    """Sanity check: the macOS sibling uses the NUITKA_ARGS array pattern.

    The Windows script does NOT use an array (it inlines the args
    directly in the ``python -m nuitka ...`` invocation). Both patterns
    are valid; this test just documents that the macOS sibling is the
    reference for the array pattern.
    """
    macos_text = MACOS_BUILD_SCRIPT.read_text(encoding="utf-8")
    assert "NUITKA_ARGS=(" in macos_text
    assert '"${NUITKA_ARGS[@]}"' in macos_text
