"""MIG-1.7 Phase 0-L Gate Check 1 — Nuitka Linux build validation (x86_64 + aarch64).

This test file is the **first of 9 gate checks** in the Phase 0-L
Linux host validation gate (ADR-0020). It validates the *structure*
of:

  - ``scripts/build/build_sidecar_linux.sh`` — freezes
    ``voice_typer/server/ipc_server.py`` into
    ``python-sidecar-<arch>-unknown-linux-gnu`` via Nuitka, AND
  - ``scripts/build/build_prewarm_linux.sh`` — freezes
    ``voice_typer/server/prewarm.py`` into
    ``prewarm-<arch>-unknown-linux-gnu`` via Nuitka.

The Linux sandbox CANNOT run a real Nuitka Linux build here without
a full Linux host toolchain (build-essential, patchelf, qemu-user-static
for the aarch64 cross path, python-build-standalone
cpython-3.12.x+<arch>-unknown-linux-gnu). These tests therefore:

  - validate the bash scripts are syntactically valid (``bash -n``),
  - validate the sidecar script supports BOTH arches via a positional
    ``$1`` arg (``aarch64`` / ``x86_64``),
  - validate the sidecar script contains the ADR-0020 §4.4-mandated
    Nuitka flags (``--standalone``, ``--onefile``,
    ``--enable-plugin=numpy``, ``--include-package=faster_whisper``,
    ``--include-package=ctranslate2``),
  - validate the sidecar script HAS the XPLAT-3 ``ctranslate2/libs``
    (plural) existence guard (recently re-applied to the Linux script
    in XPLAT-3-ctranslate2-guard — the Linux script is the canonical
    reference source for this pattern),
  - validate the sidecar script produces the
    ``python-sidecar-x86_64-unknown-linux-gnu`` +
    ``python-sidecar-aarch64-unknown-linux-gnu`` output filenames,
  - validate the prewarm script produces the
    ``prewarm-x86_64-unknown-linux-gnu`` +
    ``prewarm-aarch64-unknown-linux-gnu`` output filenames,
  - validate the sidecar script handles Linux-specific concerns:
    glibc 2.35 baseline (Ubuntu 22.04) verification via
    ``ldd``/``objdump -p``, the ``patchelf`` requirement, and the
    qemu-user-static cross-build path for aarch64,
  - validate the sidecar script references ``python-build-standalone``
    cpython-3.12.x for BOTH arches (header comment + dynamic triple
    construction via ``${ARCH}-unknown-linux-gnu``),
  - document the exact ``VALIDATE ON LINUX HOST`` commands a human must
    run on a real Linux host for BOTH arches.

VALIDATE ON LINUX HOST (x86_64):
    1. sudo apt install build-essential libssl-dev libffi-dev python3.12 python3.12-venv
    2. curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh; rustup default stable-x86_64-unknown-linux-gnu
    3. pip install uv; uv venv; source .venv/bin/activate
    4. uv pip install -e ".[dev,test]" nuitka==2.5.4 zstandard ordered-set
    5. Download python-build-standalone cpython-3.12.x+x86_64-unknown-linux-gnu to /tmp/pybs/python
    6. ARCH=x86_64 bash scripts/build/build_sidecar_linux.sh
    Expected: python-sidecar-x86_64-unknown-linux-gnu (~150 MB) in src-tauri/bin/

VALIDATE ON LINUX HOST (aarch64 — ARM64):
    1. Same prerequisites (on an aarch64 host like Raspberry Pi 4 or Ampere VM)
    2. rustup default stable-aarch64-unknown-linux-gnu
    3. Download cpython-3.12.x+aarch64-unknown-linux-gnu to /tmp/pybs/python
    4. ARCH=aarch64 bash scripts/build/build_sidecar_linux.sh
    Expected: python-sidecar-aarch64-unknown-linux-gnu (~150 MB) in src-tauri/bin/

References:
  - ADR-0020 §4.4 — Nuitka Linux freeze spec (authoritative for both arches).
  - ADR-0020 §4.5 — Common Nuitka caveats (per-triple verify).
  - docs/migration/linux-validation-runbook.md §0 + §1 — exact host commands.
  - scripts/build/build_sidecar_macos.sh — sibling (XPLAT-3 guard mirror).
  - scripts/build/build_sidecar_windows.sh — sibling (XPLAT-3 guard mirror).

Gaps documented (report, do NOT fix — out of scope for this gate check):
  - GAP-1: ``build_sidecar_linux.sh`` does NOT support a ``--check``
    mode. Both the macOS sibling (``build_sidecar_macos.sh --check``)
    and the Windows sibling (``build_sidecar_windows.sh --check``) accept
    a ``--check`` arg that imports ``nuitka`` + ``faster_whisper`` +
    ``ctranslate2`` and verifies the host toolchain in <2 s. The Linux
    script does NOT — its first positional arg is parsed as ARCH and
    anything else (including ``--check``) hits the ``Usage`` error path
    with ``exit 1``. CI cannot pre-flight the toolchain without invoking
    a full Nuitka build (~10-15 min). See
    ``test_known_gap_no_check_mode``.
  - GAP-2: ``build_sidecar_linux.sh`` does NOT do a Python-level
    ``import faster_whisper, ctranslate2, websockets`` sanity check
    before invoking Nuitka. It only checks the *directory existence*
    of ``$SITE/faster_whisper`` / ``$SITE/ctranslate2`` /
    ``$SITE/websockets`` (lines 133-146). A partially-installed
    CTranslate2 wheel (e.g. missing ``.so`` files) would not be caught
    until ~10 minutes into the Nuitka build. The macOS sibling does
    ``"$PY" -c 'import faster_whisper, ctranslate2, websockets;
    print("ctranslate2", ctranslate2.__version__)'`` (line ~101). See
    ``test_known_gap_no_python_import_sanity_check``.
  - GAP-3: ``build_prewarm_linux.sh`` ``--check`` is a stub — it
    immediately exits 0 with a one-line message delegating to the
    sidecar check (same pattern as macOS GAP-3). See
    ``test_known_gap_prewarm_check_is_stub``.
  - GAP-4: ``build_sidecar_linux.sh`` invokes
    ``sudo update-binfmts --enable qemu-aarch64 || true`` (line ~83)
    in the cross-build path. The ``sudo`` can hang in CI (no tty for
    the password prompt), and the ``|| true`` swallows the failure
    so the cross-build proceeds with an unregistered binfmt_misc
    entry. See ``test_known_gap_sudo_binfmt_in_cross_path``.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

# ─── Project paths ───────────────────────────────────────────────────────────
# This test file lives at tests/tauri/mig17/test_nuitka_linux_build.py.
# Path from file → root:
#   parents[0] = mig17/
#   parents[1] = tauri/
#   parents[2] = tests/
#   parents[3] = <project root> (voice-typer/)
PROJECT_ROOT = Path(__file__).resolve().parents[3]
SIDECAR_SCRIPT = PROJECT_ROOT / "scripts" / "build" / "build_sidecar_linux.sh"
PREWARM_SCRIPT = PROJECT_ROOT / "scripts" / "build" / "build_prewarm_linux.sh"
MACOS_SIDECAR_SCRIPT = PROJECT_ROOT / "scripts" / "build" / "build_sidecar_macos.sh"
WINDOWS_SIDECAR_SCRIPT = PROJECT_ROOT / "scripts" / "build" / "build_sidecar_windows.sh"


# ─── Fixtures ────────────────────────────────────────────────────────────────
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


# ─── 1. Existence + bash syntax validation ───────────────────────────────────
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
    """``bash -n`` must parse the sidecar script without syntax errors.

    This is the only test that actually invokes bash; ``-n`` only parses,
    it does NOT execute the script, so no Nuitka / gcc / python is
    spawned. Safe to run on any host.
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
        "build_sidecar_linux.sh must start with `#!/usr/bin/env bash`"
    )
    assert "set -euo pipefail" in sidecar_text, (
        "build_sidecar_linux.sh must enable strict mode (`set -euo pipefail`) "
        "so a missing .so or failed import aborts the build instead of "
        "producing a broken sidecar."
    )


# ─── 2. Both arches supported via $1 positional arg ──────────────────────────
def test_sidecar_script_supports_both_arches_via_arg(sidecar_text: str):
    """The sidecar script must accept ``aarch64`` OR ``x86_64`` as ``$1``.

    ADR-0020 §4.4: Nuitka cannot cross-compile to a universal binary,
    so the same script must serve both arches via the first positional
    arg. The Linux validation runbook §1 invokes:
      ``scripts/build/build_sidecar_linux.sh x86_64``
      ``scripts/build/build_sidecar_linux.sh aarch64``
    """
    assert 'ARCH="${1:-}"' in sidecar_text, (
        "build_sidecar_linux.sh must read ARCH from the first positional "
        'arg: `ARCH="${1:-}"`. (Linux validation runbook §1.)'
    )
    # The arch case statement must accept BOTH arches (either via an OR
    # branch `x86_64|aarch64)` or via two separate case branches).
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
    """The script must normalize ``arm64`` → ``aarch64`` + ``amd64`` → ``x86_64``.

    Linux distros report ``uname -m`` inconsistently: Debian/Ubuntu
    aarch64 reports ``aarch64``; some macOS-influenced distros report
    ``arm64``; some container runtimes report ``amd64`` instead of
    ``x86_64``. The script must normalize both.
    """
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
    # The arch case statement must have a wildcard error branch OR a
    # negated `!= x86_64 && != aarch64` usage-error guard.
    assert '!= "x86_64" && "$ARCH" != "aarch64"' in sidecar_text or "*)" in sidecar_text, (
        "build_sidecar_linux.sh must print a clear usage error if ARCH is not x86_64 or aarch64."
    )
    assert "Usage:" in sidecar_text, "build_sidecar_linux.sh must print a `Usage:` line on arch error."


# ─── 3. ADR-0020 §4.4 mandated Nuitka flags ─────────────────────────────────
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
    """The script must ``--include-data-dir`` the ctranslate2/lib folder.

    Without this, ``libctranslate2.so`` + ``libiomp5.so`` + ``libgomp.so``
    are missing from the bundle and the frozen binary BUILDS but CRASHES
    on ``import ctranslate2`` (ADR-0020 §4.4 + §11 Known Issues).
    """
    assert "--include-data-dir" in sidecar_text
    assert "ctranslate2/lib" in sidecar_text, (
        "build_sidecar_linux.sh must include --include-data-dir for "
        "$SITE/ctranslate2/lib (captures libctranslate2.so + libiomp5.so + "
        "libgomp.so per ADR-0020 §4.4)."
    )


def test_sidecar_script_onefile_tempdir_pinned_to_cache(sidecar_text: str):
    """``--onefile-tempdir-spec`` must pin to ``$XDG_CACHE_HOME/voice-typer/onefile-tmp``.

    ADR-0020 §4.4: pinning the extract dir prevents tempdir bloat from
    onefile re-extractions across launches. The Linux convention is
    ``$XDG_CACHE_HOME/voice-typer/onefile-tmp`` (or
    ``$HOME/.cache/voice-typer/onefile-tmp`` if XDG_CACHE_HOME is unset).
    """
    assert "XDG_CACHE_HOME" in sidecar_text, (
        "build_sidecar_linux.sh --onefile-tempdir-spec must use "
        "$XDG_CACHE_HOME/voice-typer/onefile-tmp (Linux convention, "
        "ADR-0020 §4.4)."
    )
    assert "voice-typer/onefile-tmp" in sidecar_text


# ─── 4. ctranslate2/libs guard (plural — XPLAT-3 pattern, source on Linux) ──
def test_sidecar_script_has_xplat3_ctranslate2_libs_guard(sidecar_text: str):
    """The sidecar script must have the XPLAT-3 ``ctranslate2/libs`` guard.

    The XPLAT-3 pattern (canonical source on the Linux script, mirrored
    to macOS + Windows siblings after MIG-1.5 GAP-1) guards the optional
    ``--include-data-dir=$SITE/ctranslate2/libs=...`` flag with an
    ``if [[ -d "$CT2_LIBS_DIR" ]]`` block. CPU-only CTranslate2 wheels
    (e.g. aarch64 Linux) ship ``libctranslate2.so`` + ``libiomp5.so``
    under ``ctranslate2/lib/`` only, with NO ``ctranslate2/libs/``
    directory. Nuitka's ``--include-data-dir`` fails hard if the source
    path is missing, so the guard skips the flag when ``libs/`` is
    absent.

    This test was the trigger for re-applying the XPLAT-3 guard to the
    Linux script (the Linux script IS the canonical reference source).
    """
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
    """The script uses the ``NUITKA_ARGS`` bash array pattern.

    This is the cleanest way to conditionally append the XPLAT-3
    ``--include-data-dir=$SITE/ctranslate2/libs`` flag — bash arrays
    handle the quoting + the conditional ``+=`` append. The XPLAT-3 fix
    (re-applied in MIG-1.7 Phase 0-L gate check 1 prep) refactored the
    inline Nuitka command to use this array pattern.
    """
    assert "NUITKA_ARGS=(" in sidecar_text, "build_sidecar_linux.sh must declare the NUITKA_ARGS bash array."
    assert '"${NUITKA_ARGS[@]}"' in sidecar_text, (
        'build_sidecar_linux.sh must expand the NUITKA_ARGS array via "${NUITKA_ARGS[@]}" when invoking Nuitka.'
    )
    assert "NUITKA_ARGS+=" in sidecar_text, (
        "build_sidecar_linux.sh must use `NUITKA_ARGS+=(...)` to conditionally "
        "append the XPLAT-3 libs/ flag inside the guard block."
    )


def test_sidecar_script_documents_xplat3_guard_rationale(sidecar_text: str):
    """The script must document the XPLAT-3 guard rationale in a comment.

    The XPLAT-3-ctranslate2-guard comment explains WHY the libs/ include
    is guarded (CPU-only wheels lack the libs/ dir; Nuitka fails hard
    on missing source paths). Without the comment, a future reader may
    mistake the guard for dead code and remove it.
    """
    # The XPLAT-3 comment block in build_sidecar_linux.sh mentions both
    # the CPU-only aarch64 case and the macOS/Windows sibling reference.
    assert "ctranslate2/libs" in sidecar_text
    assert "XPLAT-3" in sidecar_text or "CPU-only" in sidecar_text or "fails hard" in sidecar_text, (
        "build_sidecar_linux.sh must document the XPLAT-3 ctranslate2/libs "
        "guard rationale (CPU-only wheels lack libs/; Nuitka fails hard on "
        "missing source paths)."
    )


# ─── 5. Output filenames for BOTH arches ────────────────────────────────────
def test_sidecar_script_uses_triple_variable_construction(sidecar_text: str):
    """The sidecar script must build TRIPLE from ARCH via ``${ARCH}-unknown-linux-gnu``.

    This is the pattern that lets a single script serve both x86_64 +
    aarch64 by passing the arch as the first positional arg.
    """
    assert "${ARCH}-unknown-linux-gnu" in sidecar_text, (
        'build_sidecar_linux.sh must construct TRIPLE dynamically: TRIPLE="${ARCH}-unknown-linux-gnu"'
    )
    # The TRIPLE variable must be assigned (not just referenced).
    assert 'TRIPLE="${ARCH}-unknown-linux-gnu"' in sidecar_text, (
        "build_sidecar_linux.sh must assign TRIPLE=${ARCH}-unknown-linux-gnu right after the ARCH case statement."
    )


def test_sidecar_script_output_filename_pattern(sidecar_text: str):
    """The output filename must match ``python-sidecar-<triple>``.

    Tauri v2's ``externalBin`` mechanism appends the Rust target triple
    to the base name at runtime; the frozen binary filename MUST end
    with the triple for Tauri to select it (ADR-0020 §4.1 + §7).
    """
    assert "python-sidecar-" in sidecar_text, (
        "build_sidecar_linux.sh output filename must start with `python-sidecar-` (Tauri externalBin base name)."
    )
    assert (
        "python-sidecar-${TRIPLE}" in sidecar_text
        or "python-sidecar-$TRIPLE" in sidecar_text
        or 'OUTPUT_BIN="$OUTPUT_DIR/python-sidecar-$TRIPLE"' in sidecar_text
    ), "build_sidecar_linux.sh must construct OUTPUT_BIN as python-sidecar-$TRIPLE (or equivalent)."


def test_sidecar_script_documents_both_arch_output_filenames(sidecar_text: str):
    """The script header must document BOTH arch output filenames.

    Per ADR-0020 §4.4 the script serves both x86_64 AND aarch64; the
    header comment must list both ``python-sidecar-x86_64-unknown-linux-gnu``
    and ``python-sidecar-aarch64-unknown-linux-gnu`` so operators can
    verify which file to expect from which arch invocation.
    """
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


# ─── 6. python-build-standalone cpython-3.12.x for both arches ──────────────
def test_sidecar_script_references_python_build_standalone(sidecar_text: str):
    """The script must reference ``python-build-standalone`` as the base interpreter.

    ADR-0020 §4.4: the Nuitka target interpreter is a clean
    python-build-standalone cpython-3.12.x build (not the system Python,
    not a distro Python). The script header must document this.
    """
    assert "python-build-standalone" in sidecar_text, (
        "build_sidecar_linux.sh must reference python-build-standalone "
        "(ADR-0020 §4.4 mandates a clean cpython-3.12.x install as the "
        "Nuitka target interpreter)."
    )


def test_sidecar_script_references_cpython_3_12(sidecar_text: str):
    """The script must pin the interpreter to ``cpython-3.12.x``.

    ADR-0020 §4.4: ``cpython-3.12.x+x86_64-unknown-linux-gnu`` or
    ``cpython-3.12.x+aarch64-unknown-linux-gnu``. The script must
    auto-discover the patch version (3.12.20, 3.12.21, ...) so a
    python-build-standalone release bump does NOT require a script edit.
    """
    assert "cpython-3.12" in sidecar_text, (
        "build_sidecar_linux.sh must reference cpython-3.12.x (the ADR-0020 "
        "§4.4 pinned interpreter version for BOTH arches)."
    )
    # The auto-discovery glob uses `cpython-3.12.*+<triple>` so a patch
    # version bump does not require a script edit.
    assert "cpython-3.12.*+" in sidecar_text, (
        "build_sidecar_linux.sh must auto-discover the patch version via the "
        "`cpython-3.12.*+<triple>` glob (so a python-build-standalone release "
        "bump does NOT require a script edit)."
    )


def test_sidecar_script_discovers_pybs_via_env_var(sidecar_text: str):
    """The script must discover the python-build-standalone install via env var.

    Discovery priority (per script header):
      1. ``$VOICE_TYPER_PYBS_DIR`` (CI workflow sets this)
      2. ``$PROJECT_ROOT/.python-build-standalone`` (dev fallback)

    The CI workflow unpacks the per-arch cpython-3.12.x tarball into
    ``$VOICE_TYPER_PYBS_DIR`` and the script picks it up from there.
    """
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
    """The script must reference the python-build-standalone install_only layout.

    python-build-standalone install_only tarballs extract to
    ``$DIR/python/bin/python3``. ADR-0020 §4.4 mandates this as the
    base interpreter for Nuitka freezing. The script ALSO supports the
    verbose layout ``$DIR/cpython-3.12.*+<triple>/python/bin/python3``.
    """
    assert "python/bin/python3" in sidecar_text, (
        "build_sidecar_linux.sh must reference python-build-standalone's "
        "install_only layout: $PYBS_DIR/python/bin/python3."
    )


def test_sidecar_script_supports_verbose_pybs_layout(sidecar_text: str):
    """The script must ALSO support the verbose ``cpython-3.12.*+<triple>`` layout.

    Some CI workflows download the verbose (non-install_only) tarball,
    which extracts to ``$DIR/cpython-3.12.*+<triple>/python/bin/python3``
    (or ``$DIR/cpython-3.12.*+<triple>/bin/python3``). The script's
    auto-discovery loop must try all three layouts.
    """
    assert 'cpython-3.12.*+"$TRIPLE"/python/bin/python3' in sidecar_text, (
        "build_sidecar_linux.sh auto-discovery must try "
        "$PYBS_DIR/cpython-3.12.*+<triple>/python/bin/python3 (verbose layout)."
    )
    assert 'cpython-3.12.*+"$TRIPLE"/bin/python3' in sidecar_text, (
        "build_sidecar_linux.sh auto-discovery must try "
        "$PYBS_DIR/cpython-3.12.*+<triple>/bin/python3 (alt verbose layout)."
    )


# ─── 7. Linux-specific flags: glibc 2.35 baseline (Ubuntu 22.04) ────────────
def test_sidecar_script_documents_glibc_2_35_baseline(sidecar_text: str):
    """The script header must document the glibc 2.35 (Ubuntu 22.04) baseline.

    ADR-0020 §4.4: the python-build-standalone Linux build must be
    pinned to glibc 2.35 so the resulting binary runs on Ubuntu 22.04+
    / Debian 12+ / Fedora 36+. Newer glibc builds (e.g. Ubuntu 24.04
    baseline) would break older distributions.
    """
    assert "glibc 2.35" in sidecar_text or "GLIBC_2.35" in sidecar_text, (
        "build_sidecar_linux.sh must document the glibc 2.35 baseline (ADR-0020 §4.4 — Ubuntu 22.04 floor)."
    )
    assert "Ubuntu 22.04" in sidecar_text, (
        "build_sidecar_linux.sh must reference Ubuntu 22.04 as the baseline distro for the glibc 2.35 pin."
    )


def test_sidecar_script_verifies_glibc_after_build(sidecar_text: str):
    """The script must verify the built binary's max glibc ≤ 2.35.

    ADR-0020 §4.4: after Nuitka produces the binary, the script runs
    ``ldd`` (native) or ``objdump -p`` (cross-build) and checks the
    max GLIBC requirement is ≤ ``GLIBC_2.35``. A binary requiring
    GLIBC_2.36+ would not run on Ubuntu 22.04 and must fail the build.
    """
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
    """The script must ``exit 1`` if the binary requires glibc > 2.35.

    The verify_glibc function parses the max GLIBC version requirement,
    splits it into major.minor, and hard-fails if the version is above
    2.35. This is the gate that prevents accidentally shipping a binary
    that won't run on Ubuntu 22.04.
    """
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
    """The script must verify ``patchelf`` is on PATH before invoking Nuitka.

    Nuitka ``--standalone`` on Linux requires ``patchelf`` to rewrite
    ELF ``DT_RUNPATH`` entries for the bundled libs. Without patchelf,
    Nuitka prints ``FATAL: Error, standalone mode on Linux requires
    'patchelf' to be installed`` ~30 seconds into the build.
    """
    assert "command -v patchelf" in sidecar_text, (
        "build_sidecar_linux.sh must verify patchelf is on PATH (Nuitka --standalone on Linux requires it)."
    )
    assert "patchelf not found" in sidecar_text or "patchelf" in sidecar_text


def test_sidecar_script_supports_qemu_cross_build(sidecar_text: str):
    """The script must support the aarch64 cross-build path via qemu-user-static.

    ADR-0020 §4.4 + Linux validation runbook §1: an x86_64 host can
    cross-build the aarch64 binary by running the aarch64
    python-build-standalone interpreter under ``qemu-aarch64-static``
    (registered via ``binfmt_misc``). The script must:

      - detect when ``ARCH=aarch64`` is requested on an x86_64 host,
      - verify ``qemu-aarch64-static`` is on PATH,
      - verify the aarch64 python-build-standalone interpreter is
        actually an ARM ELF (not an x86-64 ELF by mistake).
    """
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
    """The script must verify the cross-build interpreter is actually aarch64.

    A common failure: the operator points ``VOICE_TYPER_PYBS_DIR`` at
    an x86_64 python-build-standalone install but requests ``ARCH=aarch64``.
    Without an ELF-arch check, Nuitka would happily build an x86_64
    binary and name it ``python-sidecar-aarch64-unknown-linux-gnu``,
    which would crash on the aarch64 host. The script uses ``file -b``
    + greps for ``ARM`` / ``aarch64`` to verify.
    """
    assert "file -b" in sidecar_text or "file " in sidecar_text, (
        "build_sidecar_linux.sh must use `file` to check the interpreter's ELF arch in the cross-build path."
    )
    assert "ARM" in sidecar_text or "aarch64" in sidecar_text


def test_sidecar_script_smoke_runs_help_after_build(sidecar_text: str):
    """The script must smoke-test the built binary with ``--help``.

    ADR-0020 §4.5 Phase 0 gate: the built binary must print its help
    text without errors. This proves the Python interpreter +
    faster_whisper + ctranslate2 + websockets all loaded inside the
    Nuitka bundle. For cross-builds, the script uses
    ``qemu-aarch64-static`` explicitly (binfmt_misc may not be active
    in CI).
    """
    assert "--help" in sidecar_text, "build_sidecar_linux.sh must smoke-test the built binary with --help."
    assert "smoke" in sidecar_text.lower(), "build_sidecar_linux.sh must label the --help invocation as a smoke test."


# ─── 8. Prewarm build script produces prewarm-<triple> for both arches ──────
def test_prewarm_script_uses_triple_variable_construction(prewarm_text: str):
    """The prewarm script must build TRIPLE from ARCH via ``${ARCH}-unknown-linux-gnu``."""
    assert "${ARCH}-unknown-linux-gnu" in prewarm_text, (
        'build_prewarm_linux.sh must construct TRIPLE dynamically: TRIPLE="${ARCH}-unknown-linux-gnu"'
    )


def test_prewarm_script_output_filename_pattern(prewarm_text: str):
    """The prewarm output filename must match ``prewarm-<triple>``.

    ADR-0020 §5: the prewarm binary is a Tauri ``bundle.resource`` (NOT
    a Tauri ``externalBin``), launched by the Linux systemd user timer
    at ``~/.config/systemd/user/voice-typer-prewarm.timer`` via
    ``resolve_prewarm_exe()``. The triple suffix is required so the
    resolver picks the right arch at runtime.
    """
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
        "bundle.resource location — NOT src-tauri/bin, since prewarm is "
        "launched by the systemd user timer, not as a Tauri externalBin)."
    )


def test_prewarm_script_supports_both_arches_via_arg(prewarm_text: str):
    """The prewarm script must accept ``aarch64`` OR ``x86_64`` as ``$1``."""
    assert 'ARCH="${1:-}"' in prewarm_text
    assert "x86_64|aarch64)" in prewarm_text or ("x86_64)" in prewarm_text and "aarch64)" in prewarm_text), (
        "build_prewarm_linux.sh must accept both x86_64 + aarch64 arches."
    )


def test_prewarm_script_defaults_to_host_arch(prewarm_text: str):
    """When no arg is given, the prewarm script must default to ``uname -m``.

    ``aarch64`` host → ``aarch64``; ``x86_64`` host → ``x86_64``. This
    lets a dev run ``bash scripts/build/build_prewarm_linux.sh`` on
    either host without specifying the arch explicitly.
    """
    assert "uname -m" in prewarm_text, (
        "build_prewarm_linux.sh must default ARCH via `uname -m` when no positional arg is given."
    )


def test_prewarm_script_entry_point_is_prewarm_py(prewarm_text: str):
    """The Nuitka entry point must be ``voice_typer/server/prewarm.py``."""
    assert "voice_typer/server/prewarm.py" in prewarm_text, (
        "build_prewarm_linux.sh entry point must be voice_typer/server/prewarm.py (ADR-0011 + ADR-0020 §5)."
    )


def test_prewarm_script_has_xplat3_ctranslate2_libs_guard(prewarm_text: str):
    """The prewarm script must also have the XPLAT-3 ctranslate2/libs guard."""
    assert "CT2_LIBS_DIR" in prewarm_text
    assert 'if [[ -d "$CT2_LIBS_DIR" ]]' in prewarm_text
    assert '--include-data-dir="$CT2_LIBS_DIR=$CT2_LIBS_DIR"' in prewarm_text


# ─── 9. Sibling parity (macOS + Windows siblings have the XPLAT-3 guard) ────
def test_macos_sibling_has_xplat3_ctranslate2_libs_guard():
    """Sanity check: the macOS sibling MUST have the XPLAT-3 guard.

    This is a reference-pattern check — if the macOS sibling loses the
    guard, the XPLAT-3 pattern drifts and the Linux script (canonical
    source) becomes the lone reference.
    """
    if not MACOS_SIDECAR_SCRIPT.is_file():
        pytest.skip(f"build_sidecar_macos.sh missing ({MACOS_SIDECAR_SCRIPT}) — cannot verify macOS sibling parity.")
    macos_text = MACOS_SIDECAR_SCRIPT.read_text(encoding="utf-8")
    assert "CT2_LIBS_DIR" in macos_text
    assert '--include-data-dir="$CT2_LIBS_DIR=$CT2_LIBS_DIR"' in macos_text
    assert 'if [[ -d "$CT2_LIBS_DIR" ]]' in macos_text


def test_windows_sibling_known_gap_no_xplat3_ctranslate2_libs_guard():
    """BUILD-2 fix: the Windows sibling now HAS the XPLAT-3 guard.

    Previously a KNOWN GAP (MIG-1.5 GAP-1) — the Windows sibling
    ``build_sidecar_windows.sh`` only included the singular ``lib/``
    with no guard for the optional ``libs/`` dir. BUILD-2 back-filled
    the guard (mirroring the Linux + macOS XPLAT-3 pattern). This test
    now ASSERTS the guard IS present in the Windows sibling.
    """
    if not WINDOWS_SIDECAR_SCRIPT.is_file():
        pytest.skip(
            f"build_sidecar_windows.sh missing ({WINDOWS_SIDECAR_SCRIPT}) — cannot verify Windows sibling parity."
        )
    windows_text = WINDOWS_SIDECAR_SCRIPT.read_text(encoding="utf-8")
    # The Windows sibling has both CT2_LIB_DIR (singular, required) and
    # CT2_LIBS_DIR (plural, optional — BUILD-2 guard).
    assert "CT2_LIB_DIR=" in windows_text, (
        "build_sidecar_windows.sh must define CT2_LIB_DIR (singular — the required ctranslate2/lib dir)."
    )
    assert "CT2_LIBS_DIR" in windows_text, (
        "build_sidecar_windows.sh should define CT2_LIBS_DIR (plural — BUILD-2 guard)."
    )
    assert 'if [[ -d "$CT2_LIBS_DIR" ]]' in windows_text, (
        "build_sidecar_windows.sh should guard the libs include with if [[ -d (BUILD-2 fix)."
    )


# ─── 10. BUILD-1 fix: --check mode now supported ─────────────────────────────
def test_build_sidecar_linux_supports_check_mode(sidecar_text: str):
    """BUILD-1 fix: ``build_sidecar_linux.sh`` now supports ``--check``.

    Both the macOS sibling (``build_sidecar_macos.sh --check``) and the
    Windows sibling (``build_sidecar_windows.sh --check``) accept a
    ``--check`` arg. BUILD-1 added the same to the Linux script. This
    test ASSERTS the --check mode IS present.
    """
    assert 'if [[ "$ARCH" == "--check" ]]' in sidecar_text, (
        "build_sidecar_linux.sh should support --check mode (BUILD-1 fix)."
    )


def test_known_gap_no_python_import_sanity_check(sidecar_text: str):
    """KNOWN GAP (GAP-2): the script does NOT do a Python-level import sanity check.

    The Linux script checks the DIRECTORY existence of
    ``$SITE/faster_whisper`` / ``$SITE/ctranslate2`` /
    ``$SITE/websockets`` (lines 133-146), but does NOT invoke
    ``"$PYBS_PYTHON" -c 'import faster_whisper, ctranslate2, websockets'``
    before invoking Nuitka. A partially-installed CTranslate2 wheel
    (e.g. missing ``.so`` files, or a Python version mismatch) would
    not be caught until ~10 minutes into the Nuitka build.

    The macOS sibling does this check at line ~101:
    ``"$PY" -c 'import faster_whisper, ctranslate2, websockets; print
    ("ctranslate2", ctranslate2.__version__)'``.

    This test ASSERTS the gap is present. DO NOT fix this gap as part
    of MIG-1.7 gate check 1 — report it to the primary agent.
    """
    assert "import faster_whisper, ctranslate2, websockets" not in sidecar_text, (
        "build_sidecar_linux.sh now does a Python-level import sanity check "
        "— update this test to assert PRESENCE instead of absence, and "
        "remove GAP-2 from the module docstring."
    )
    # The script DOES check directory existence (so a fully-missing
    # wheel is still caught) — that's the partial mitigation.
    assert '! -d "$SITE/faster_whisper"' in sidecar_text, (
        "build_sidecar_linux.sh must still check the faster_whisper dir "
        "exists (partial mitigation for GAP-2 — directory check, not "
        "Python import)."
    )


def test_known_gap_prewarm_check_is_stub(prewarm_text: str):
    """KNOWN GAP (GAP-3): ``build_prewarm_linux.sh --check`` is a stub.

    Unlike a hypothetical full check (which would import nuitka +
    faster_whisper + ctranslate2), the prewarm script's ``--check``
    immediately exits 0 with a one-line message delegating to the
    sidecar check. This means CI cannot independently verify the
    prewarm build env without first running the sidecar check.

    This test ASSERTS the gap is present. DO NOT fix this gap as part
    of MIG-1.7 gate check 1 — report it to the primary agent.
    """
    # The prewarm --check path exits 0 without importing anything.
    assert "import nuitka" not in prewarm_text, (
        "build_prewarm_linux.sh --check now actually verifies the toolchain "
        "— update this test to assert PRESENCE instead of absence, and "
        "remove GAP-3 from the module docstring."
    )
    assert "same toolchain as build_sidecar_linux" in prewarm_text or ("OK if that passes" in prewarm_text), (
        "build_prewarm_linux.sh --check must still be the stub that "
        "delegates to build_sidecar_linux (GAP-3 expected pattern)."
    )


def test_known_gap_sudo_binfmt_in_cross_path(sidecar_text: str):
    """KNOWN GAP (GAP-4): the script invokes ``sudo update-binfmts`` in the cross path.

    In the aarch64 cross-build path (line ~83), the script runs:
      ``sudo update-binfmts --enable qemu-aarch64 || true``

    Two problems:
      1. ``sudo`` can hang in CI (no tty for the password prompt).
      2. The ``|| true`` swallows the failure, so the cross-build
         proceeds with an unregistered binfmt_misc entry. The
         ``qemu-aarch64-static`` invocation later in the script will
         then succeed (because the script calls it explicitly), but
         the ``$OUTPUT_BIN --help`` smoke test relies on binfmt_misc
         being active for direct aarch64 ELF execution.

    A cleaner pattern would be to check
    ``/proc/sys/fs/binfmt_misc/qemu-aarch64`` and FAIL HARD if it's
    missing (with a clear error message pointing to the
    ``sudo update-binfmts --enable`` command the operator must run
    manually), rather than silently attempting the registration.

    This test ASSERTS the gap is present. DO NOT fix this gap as part
    of MIG-1.7 gate check 1 — report it to the primary agent.
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
