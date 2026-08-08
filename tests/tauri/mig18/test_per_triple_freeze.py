"""MIG-1.8 Phase 1 — Per-triple Nuitka freeze configuration validation.

ADR-0020 §4 mandates that the Python sidecar (``voice_typer.server.ipc_server``)
be frozen into a Nuitka ``--onefile`` binary **per Rust target triple**, because
Nuitka cannot cross-compile. The mandatory triple set (ADR-0020 §4.1) is:

    Windows x86_64   : x86_64-pc-windows-msvc       → python-sidecar-x86_64-pc-windows-msvc.exe
    Windows aarch64  : aarch64-pc-windows-msvc      → python-sidecar-aarch64-pc-windows-msvc.exe
    macOS Intel      : x86_64-apple-darwin          → python-sidecar-x86_64-apple-darwin
    macOS Apple Silicon : aarch64-apple-darwin      → python-sidecar-aarch64-apple-darwin
    Linux x86_64     : x86_64-unknown-linux-gnu     → python-sidecar-x86_64-unknown-linux-gnu
    Linux aarch64    : aarch64-unknown-linux-gnu    → python-sidecar-aarch64-unknown-linux-gnu

This test file validates the **structure** of the three per-platform build
scripts + the unified wrapper:

    scripts/build/build_sidecar_windows.sh   — Windows x86_64 + aarch64
    scripts/build/build_sidecar_macos.sh     — macOS x86_64 + aarch64
    scripts/build/build_sidecar_linux.sh     — Linux x86_64 + aarch64 (qemu cross)
    scripts/build/nuitka_freeze.sh           — unified wrapper, dispatches by host OS

The Linux sandbox CANNOT run a real Nuitka freeze for any triple (no MSVC,
no Xcode, no python-build-standalone cpython-3.12.x+<triple>). These tests
therefore validate *configuration*:

  - each per-platform script exists + is bash-syntax-valid (``bash -n``),
  - each script produces the correct ``python-sidecar-<triple>[.exe]``
    filename pattern for BOTH arches (x86_64 + aarch64),
  - the Tauri ``externalBin`` config declares ``bin/python-sidecar`` as the
    single base name (Tauri v2 appends the Rust target triple at runtime —
    ADR-0020 §7),
  - each script references ``python-build-standalone`` ``cpython-3.12.x``
    as the base interpreter (ADR-0020 §4.2 / §4.3 / §4.4),
  - each script includes the ADR-0020 §4-mandated Nuitka flags
    (``--standalone``, ``--onefile``, ``--enable-plugin=numpy``,
    ``--include-package=faster_whisper``, ``--include-package=ctranslate2``,
    ``--include-package=voice_typer``, ``--include-package=websockets``),
  - the Linux + macOS scripts carry the XPLAT-3 ``ctranslate2/libs`` (plural)
    existence guard (the Windows wheel layout puts all DLLs under
    ``ctranslate2/lib`` singular, so the Windows script intentionally omits
    the libs guard — documented as GAP-1 in tests/tauri/mig15/test_nuitka_windows_build.py),
  - the PyInstaller fallback spec (``scripts/build/voice-typer.spec``) exists
    as the safety-net build path (ADR-0020 §4.5),
  - the unified ``nuitka_freeze.sh`` wrapper dispatches to the right
    per-platform script based on the host OS.

=====================================================================
VALIDATE ON HOST — exact commands a human must run on each platform
=====================================================================

These commands MUST be run on a real host of the matching OS + arch; the
Linux sandbox cannot execute them. They mirror the ADR-0020 §4.5 Phase 0
gate (run sidecar ``--help`` to prove the Python interpreter + faster_whisper
+ ctranslate2 + websockets all loaded inside Nuitka) plus the full per-triple
freeze + Tauri bundle.

---------------------------------------------------------------------
VALIDATE ON WINDOWS HOST (x86_64-pc-windows-msvc):
---------------------------------------------------------------------
    1. winget install Microsoft.VisualStudio.2022.BuildTools `
         --override "--add Microsoft.VisualStudio.Workload.VCTools"
    2. rustup default stable-x86_64-pc-windows-msvc
    3. pip install uv; uv venv; .venv\\Scripts\\activate
    4. uv pip install -e ".[dev,test]" nuitka==2.5.4 zstandard ordered-set
    5. Download python-build-standalone cpython-3.12.x+x86_64-pc-windows-msvc
       to C:\\tools\\pybs\\python (install_only layout — see
       docs/migration/windows-validation-runbook.md §0.7 for the pinned patch)
    6. set VOICE_TYPER_PYBS_DIR=C:\\tools\\pybs
    7. bash scripts/build/build_sidecar_windows.sh x86_64
       Expected: src-tauri/bin/python-sidecar-x86_64-pc-windows-msvc.exe (~150-200 MB)
    8. .\\src-tauri\\bin\\python-sidecar-x86_64-pc-windows-msvc.exe --help
       Expected: prints usage + exits 0 (proves faster_whisper + ctranslate2 +
       websockets all loaded inside Nuitka).
    9. cargo tauri build --target x86_64-pc-windows-msvc
       Expected: bundles a Windows MSI + NSIS installer embedding the sidecar.

VALIDATE ON WINDOWS HOST (aarch64-pc-windows-msvc — Windows-on-ARM):
    Same as above but: rustup default stable-aarch64-pc-windows-msvc,
    Download cpython-3.12.x+aarch64-pc-windows-msvc,
    bash scripts/build/build_sidecar_windows.sh aarch64,
    Expected: src-tauri/bin/python-sidecar-aarch64-pc-windows-msvc.exe.
    (Run on a Windows 11 ARM64 host — qemu cross-build is NOT supported
     for Windows in the script.)

---------------------------------------------------------------------
VALIDATE ON macOS HOST (aarch64-apple-darwin — Apple Silicon):
---------------------------------------------------------------------
    1. xcode-select --install
    2. rustup default stable-aarch64-apple-darwin
    3. pip install uv; uv venv; source .venv/bin/activate
    4. uv pip install -e ".[dev,test]" nuitka==2.5.4 zstandard ordered-set
    5. Download python-build-standalone cpython-3.12.x+aarch64-apple-darwin
       to /tmp/pybs (install_only layout).
    6. export VOICE_TYPER_PYBS_DIR=/tmp/pybs
    7. bash scripts/build/build_sidecar_macos.sh aarch64
       Expected: src-tauri/bin/python-sidecar-aarch64-apple-darwin (~150-200 MB)
    8. ./src-tauri/bin/python-sidecar-aarch64-apple-darwin --help
       Expected: prints usage + exits 0.
    9. codesign --force --deep --sign "Developer ID Application: ..." \\
         src-tauri/bin/python-sidecar-aarch64-apple-darwin
       (see docs/migration/signing-guide.md §13.2)
    10. cargo tauri build --target aarch64-apple-darwin
        Expected: bundles a .app + .dmg embedding the sidecar.

VALIDATE ON macOS HOST (x86_64-apple-darwin — Intel, via Rosetta 2 on Apple Silicon):
    Same as above but on an Intel Mac (macos-13 runner) OR an Apple Silicon
    Mac with Rosetta 2 installed:
    rustup default stable-x86_64-apple-darwin,
    Download cpython-3.12.x+x86_64-apple-darwin,
    bash scripts/build/build_sidecar_macos.sh x86_64,
    Expected: src-tauri/bin/python-sidecar-x86_64-apple-darwin.

---------------------------------------------------------------------
VALIDATE ON LINUX HOST (x86_64-unknown-linux-gnu):
---------------------------------------------------------------------
    1. sudo apt install build-essential libssl-dev libffi-dev patchelf \\
         python3.12 python3.12-venv
    2. curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh;
       rustup default stable-x86_64-unknown-linux-gnu
    3. pip install uv; uv venv; source .venv/bin/activate
    4. uv pip install -e ".[dev,test]" nuitka==2.5.4 zstandard ordered-set
    5. Download python-build-standalone cpython-3.12.x+x86_64-unknown-linux-gnu
       (built against glibc 2.35 — Ubuntu 22.04 baseline, ADR-0020 §4.4)
       to $REPO/.python-build-standalone
    6. bash scripts/build/build_sidecar_linux.sh x86_64
       Expected: src-tauri/bin/python-sidecar-x86_64-unknown-linux-gnu (~150 MB)
    7. ./src-tauri/bin/python-sidecar-x86_64-unknown-linux-gnu --help
       Expected: prints usage + exits 0.
    8. cargo tauri build --target x86_64-unknown-linux-gnu
       Expected: bundles a .deb + .rpm + AppImage embedding the sidecar.

VALIDATE ON LINUX HOST (aarch64-unknown-linux-gnu — ARM64):
    Option A (native aarch64 host — e.g. Ampere VM, Raspberry Pi 4):
        rustup default stable-aarch64-unknown-linux-gnu,
        Download cpython-3.12.x+aarch64-unknown-linux-gnu,
        bash scripts/build/build_sidecar_linux.sh aarch64,
        Expected: src-tauri/bin/python-sidecar-aarch64-unknown-linux-gnu.
    Option B (cross-build on x86_64 host via qemu-user-static):
        sudo apt install qemu-user-static binfmt-support
        sudo update-binfmts --enable qemu-aarch64
        Download cpython-3.12.x+aarch64-unknown-linux-gnu to
        $REPO/.python-build-standalone,
        bash scripts/build/build_sidecar_linux.sh aarch64
        Expected: src-tauri/bin/python-sidecar-aarch64-unknown-linux-gnu.
        Verify on a real aarch64 host (qemu smoke is not authoritative).

References:
  - ADR-0020 §4   — Nuitka build per platform (authoritative).
  - ADR-0020 §4.1 — mandatory target triple set.
  - ADR-0020 §4.5 — common Nuitka caveats + Phase 0 verify gate.
  - ADR-0020 §7   — Tauri externalBin naming convention.
  - ADR-0020 §13  — per-platform signing runbooks.
  - docs/migration/{windows,macos,linux}-validation-runbook.md — host commands.
  - tests/tauri/mig15/test_nuitka_windows_build.py — Windows-only structure test.
  - tests/tauri/mig16/test_nuitka_macos_build.py   — macOS-only structure test.
  - tests/tauri/mig17/test_nuitka_linux_build.py   — Linux-only structure test.

Gaps documented (report, do NOT fix — out of scope for MIG-1.8):
  - GAP-1: ``build_sidecar_windows.sh`` does NOT carry the XPLAT-3
    ``ctranslate2/libs`` (plural) existence guard. The Windows wheel
    layout puts all DLLs under ``ctranslate2/lib`` (singular), so this
    is benign on Windows — but for XPLAT-3 pattern parity the script
    could grow a defensive ``if [[ -d "$CT2_LIBS_DIR" ]]; then ...``
    block. (Already documented in
    tests/tauri/mig15/test_nuitka_windows_build.py::test_known_gap_no_ctranslate2_libs_guard.)
  - GAP-2: ``build_sidecar_linux.sh`` does NOT support a ``--check``
    dry-run mode (the macOS + Windows siblings do). Pre-flight toolchain
    verification on Linux requires invoking a full Nuitka build.
    (Already documented in tests/tauri/mig17/test_nuitka_linux_build.py.)
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from tests.fixtures.bash_utils import bash_usable

# ─── Project paths ───────────────────────────────────────────────────────────
# This test file lives at tests/tauri/mig18/test_per_triple_freeze.py.
# Path from file → root:
#   parents[0] = mig18/
#   parents[1] = tauri/
#   parents[2] = tests/
#   parents[3] = <project root> (voice-typer/)
PROJECT_ROOT = Path(__file__).resolve().parents[3]
BUILD_DIR = PROJECT_ROOT / "scripts" / "build"
BUILD_WINDOWS = BUILD_DIR / "build_sidecar_windows.sh"
BUILD_MACOS = BUILD_DIR / "build_sidecar_macos.sh"
BUILD_LINUX = BUILD_DIR / "build_sidecar_linux.sh"
NUITKA_FREEZE_WRAPPER = BUILD_DIR / "nuitka_freeze.sh"
PYINSTALLER_SPEC = BUILD_DIR / "voice-typer.spec"
TAURI_CONF = PROJECT_ROOT / "src-tauri" / "tauri.conf.json"

# Per-platform mandatory target triples (ADR-0020 §4.1).
PLATFORM_TRIPLES = {
    "windows": {
        "x86_64": "x86_64-pc-windows-msvc",
        "aarch64": "aarch64-pc-windows-msvc",
        "exe_suffix": ".exe",
        "script": BUILD_WINDOWS,
    },
    "macos": {
        "x86_64": "x86_64-apple-darwin",
        "aarch64": "aarch64-apple-darwin",
        "exe_suffix": "",
        "script": BUILD_MACOS,
    },
    "linux": {
        "x86_64": "x86_64-unknown-linux-gnu",
        "aarch64": "aarch64-unknown-linux-gnu",
        "exe_suffix": "",
        "script": BUILD_LINUX,
    },
}

# ADR-0020 §4 mandated Nuitka flags that EVERY per-platform script must include.
COMMON_NUITKA_FLAGS = [
    "--standalone",
    "--onefile",
    "--assume-yes-for-downloads",
    "--enable-plugin=numpy",
    "--include-package=faster_whisper",
    "--include-package=ctranslate2",
    "--include-package=voice_typer",
    "--include-package=websockets",
]


# ─── Fixtures ────────────────────────────────────────────────────────────────
@pytest.fixture(scope="module")
def script_texts() -> dict[str, str]:
    """Read all three per-platform scripts once per module; fail fast if missing."""
    texts: dict[str, str] = {}
    for platform, path in [
        ("windows", BUILD_WINDOWS),
        ("macos", BUILD_MACOS),
        ("linux", BUILD_LINUX),
    ]:
        assert path.is_file(), f"{path.name} not found at {path}. Did the project layout change?"
        texts[platform] = path.read_text(encoding="utf-8")
    return texts


# ─── 1. Per-platform script existence + bash syntax validation ──────────────
@pytest.mark.parametrize(
    "platform,script",
    [
        ("windows", BUILD_WINDOWS),
        ("macos", BUILD_MACOS),
        ("linux", BUILD_LINUX),
    ],
)
def test_build_script_exists(platform: str, script: Path):
    """Each per-platform build script must exist + be non-stub."""
    assert script.is_file(), f"missing: {script}"
    assert script.stat().st_size > 1000, (
        f"{script} is suspiciously small ({script.stat().st_size} bytes); "
        "expected a full Nuitka invocation script (~3-8 KB)."
    )


@pytest.mark.parametrize(
    "script",
    [BUILD_WINDOWS, BUILD_MACOS, BUILD_LINUX, NUITKA_FREEZE_WRAPPER],
    ids=["windows", "macos", "linux", "wrapper"],
)
def test_build_script_is_bash_syntax_valid(script: Path):
    """``bash -n`` must parse each script without syntax errors.

    ``-n`` only parses; it does NOT execute the script, so no Nuitka /
    cl.exe / python-build-standalone is spawned. Safe to run on the
    Linux sandbox.
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


@pytest.mark.parametrize(
    "platform,script",
    [
        ("windows", BUILD_WINDOWS),
        ("macos", BUILD_MACOS),
        ("linux", BUILD_LINUX),
    ],
)
def test_build_script_has_shebang_and_strict_mode(platform: str, script: Path):
    """Each script must start with ``#!/usr/bin/env bash`` + use ``set -euo pipefail``."""
    text = script.read_text(encoding="utf-8")
    assert text.startswith("#!/usr/bin/env bash"), f"{script.name} must start with `#!/usr/bin/env bash`"
    assert "set -euo pipefail" in text, (
        f"{script.name} must enable strict mode (`set -euo pipefail`) so a "
        "missing DLL or failed import aborts the build instead of producing "
        "a broken binary."
    )


# ─── 2. Output filename pattern per triple ──────────────────────────────────
@pytest.mark.parametrize(
    "platform,arch,triple,exe_suffix",
    [
        ("windows", "x86_64", "x86_64-pc-windows-msvc", ".exe"),
        ("windows", "aarch64", "aarch64-pc-windows-msvc", ".exe"),
        ("macos", "x86_64", "x86_64-apple-darwin", ""),
        ("macos", "aarch64", "aarch64-apple-darwin", ""),
        ("linux", "x86_64", "x86_64-unknown-linux-gnu", ""),
        ("linux", "aarch64", "aarch64-unknown-linux-gnu", ""),
    ],
    ids=[
        "windows-x86_64",
        "windows-aarch64",
        "macos-x86_64",
        "macos-aarch64",
        "linux-x86_64",
        "linux-aarch64",
    ],
)
def test_script_produces_correct_output_filename(
    script_texts: dict[str, str],
    platform: str,
    arch: str,
    triple: str,
    exe_suffix: str,
):
    """Each script must produce ``python-sidecar-<triple>[.exe]`` for BOTH arches.

    ADR-0020 §4.1 + §7 mandate that the frozen sidecar filename ends with
    the Rust target triple (``.exe`` only on Windows) so Tauri's
    ``externalBin`` mechanism selects the right binary at runtime via
    ``std::env::consts::ARCH`` + ``std::env::consts::OS``.

    Each per-platform script accepts a positional ``$1`` arch arg
    (``x86_64`` / ``aarch64``) and constructs the triple dynamically, so
    we verify BOTH the dynamic ``${ARCH}`` construction AND the literal
    triple string appear in the script (header doc + arg-parsing case
    statement).
    """
    text = script_texts[platform]
    expected_filename = f"python-sidecar-{triple}{exe_suffix}"

    # The literal triple must appear at least once (header comment + script body).
    assert triple in text, f"{platform} script must reference triple `{triple}` (ADR-0020 §4.1)."
    # The base name pattern must appear (header or output construction).
    assert "python-sidecar-" in text, (
        f"{platform} script must construct output filename starting with "
        "`python-sidecar-` (Tauri externalBin base name)."
    )
    # The arch must be a recognized positional arg.
    assert arch in text, f"{platform} script must accept `{arch}` as a positional arch arg."

    # The full triple-suffix pattern must be constructible. We accept either:
    #   (a) the literal expected filename appears (header doc), OR
    #   (b) the script constructs it dynamically via ${TRIPLE}${EXE_SUFFIX}
    #       (Windows) or ${TRIPLE} (macOS/Linux), OR
    #   (c) the triple appears inside an --output-filename pattern.
    triple_suffix_per_platform = {
        "windows": "${TRIPLE}${EXE_SUFFIX}",
        "macos": "${TRIPLE}",
        "linux": "${TRIPLE}",
    }
    dynamic_pattern = triple_suffix_per_platform[platform]
    assert (
        expected_filename in text
        or dynamic_pattern in text
        or "python-sidecar-${TRIPLE}" in text
        or f"python-sidecar-{triple}" in text
    ), (
        f"{platform} script must construct the output filename as "
        f"`python-sidecar-{dynamic_pattern}` (or emit the literal "
        f"`{expected_filename}` in a header doc)."
    )


@pytest.mark.parametrize("platform", ["windows", "macos", "linux"])
def test_script_outputs_to_src_tauri_bin(script_texts: dict[str, str], platform: str):
    """Each script must output to ``src-tauri/bin/`` (Tauri externalBin location).

    ADR-0020 §7 — Tauri's ``externalBin`` mechanism expects the sidecar
    binary at ``src-tauri/bin/<base-name>-<triple>[.exe]`` at build time;
    the per-platform Nuitka scripts must drop their output there directly
    (no manual copy step).
    """
    assert "src-tauri/bin" in script_texts[platform], (
        f"build_sidecar_{platform}.sh must output to src-tauri/bin/ (Tauri externalBin location)."
    )


# ─── 3. Tauri externalBin config ────────────────────────────────────────────
def test_tauri_conf_external_bin_uses_base_name():
    """``tauri.conf.json`` must declare ``externalBin: ["bin/python-sidecar"]``.

    ADR-0020 §7 + Tauri v2 docs: Tauri appends the Rust target triple to
    the base name at runtime, so we declare ONLY the base name (no arch /
    triple suffix). Listing the triple-suffixed name here would break
    Tauri's auto-selection.
    """
    assert TAURI_CONF.is_file(), f"missing: {TAURI_CONF}"
    conf = json.loads(TAURI_CONF.read_text(encoding="utf-8"))
    external_bin = conf.get("bundle", {}).get("externalBin", [])
    assert external_bin == ["bin/python-sidecar"], (
        f"tauri.conf.json `bundle.externalBin` must be exactly "
        f'["bin/python-sidecar"] (Tauri appends the triple at runtime); '
        f"got: {external_bin!r}"
    )


def test_tauri_conf_shell_scope_declares_sidecar():
    """``tauri.conf.json`` must declare the sidecar in ``plugins.shell.scope``.

    ADR-0020 §7 + Tauri v2 capability model: the sidecar must be
    explicitly scoped under ``plugins.shell.scope`` with ``sidecar: true``
    + ``args: true`` so the Rust host can spawn it.
    """
    conf = json.loads(TAURI_CONF.read_text(encoding="utf-8"))
    scope = conf.get("plugins", {}).get("shell", {}).get("scope", [])
    sidecar_entries = [e for e in scope if e.get("name") == "bin/python-sidecar" and e.get("sidecar") is True]
    assert sidecar_entries, (
        "tauri.conf.json `plugins.shell.scope` must include an entry "
        "with name=`bin/python-sidecar` + sidecar=true so the Rust host "
        "can spawn the sidecar."
    )
    entry = sidecar_entries[0]
    assert entry.get("cmd") == "bin/python-sidecar"
    # #299: changed ``args: true`` → ``args: ["--ws"]`` in
    # src-tauri/tauri.conf.json so the sidecar is always spawned with
    # the WebSocket mode flag (the new IPC default). The args list
    # is no longer a free-form bool — it pins the --ws contract.
    assert entry.get("args") == ["--ws"], (
        'sidecar scope entry must have args=["--ws"] so the Rust host '
        "spawns the Python sidecar in WebSocket mode (FIX-5 #299)."
    )


# ─── 4. python-build-standalone cpython-3.12.x usage ────────────────────────
@pytest.mark.parametrize("platform", ["windows", "macos", "linux"])
def test_script_references_python_build_standalone(script_texts: dict[str, str], platform: str):
    """Each script must reference ``python-build-standalone`` (ADR-0020 §4).

    ADR-0020 §4 mandates a clean ``python-build-standalone`` interpreter
    as the Nuitka base (no embedded-PE contradiction, no system Python
    ABI drift). Each per-platform script must reference it in the header
    doc + the interpreter discovery logic.
    """
    text = script_texts[platform]
    assert "python-build-standalone" in text, (
        f"build_sidecar_{platform}.sh must reference python-build-standalone "
        "(ADR-0020 §4 base interpreter for Nuitka freezing)."
    )


@pytest.mark.parametrize("platform", ["windows", "macos", "linux"])
def test_script_pins_cpython_3_12(script_texts: dict[str, str], platform: str):
    """Each script must pin to ``cpython-3.12.x`` (NOT 3.13+).

    ADR-0020 §4.2: "Pin the build interpreter to python-build-standalone
    cpython-3.12.x (matches faster-whisper / ctranslate2 wheel tags — do
    NOT use 3.13+ yet)." Each per-platform script's header + interpreter
    discovery must reference cpython-3.12 explicitly.
    """
    text = script_texts[platform]
    assert "cpython-3.12" in text, (
        f"build_sidecar_{platform}.sh must reference cpython-3.12.x "
        "(ADR-0020 §4.2 — wheel-tag compatibility with faster_whisper + "
        "ctranslate2; do NOT use 3.13+ yet)."
    )


@pytest.mark.parametrize("platform", ["windows", "macos", "linux"])
def test_script_uses_voice_typer_pybs_dir_env(script_texts: dict[str, str], platform: str):
    """Each script must honor the ``VOICE_TYPER_PYBS_DIR`` env var.

    This is the CI workflow's primary mechanism for pointing the script
    at the right python-build-standalone install (ADR-0020 §4.2 / §4.3 / §4.4).
    """
    text = script_texts[platform]
    assert "VOICE_TYPER_PYBS_DIR" in text, (
        f"build_sidecar_{platform}.sh must honor the VOICE_TYPER_PYBS_DIR "
        "env var (set by the CI workflow to the python-build-standalone install)."
    )


# ─── 5. ADR-0020 §4 mandated Nuitka flags ───────────────────────────────────
@pytest.mark.parametrize("platform", ["windows", "macos", "linux"])
@pytest.mark.parametrize("flag", COMMON_NUITKA_FLAGS)
def test_script_contains_expected_nuitka_flag(script_texts: dict[str, str], platform: str, flag: str):
    """Each per-platform script must include every ADR-0020 §4-mandated Nuitka flag.

    These flags are the common set required on ALL three platforms:
      - ``--standalone --onefile``   — single self-extracting binary
      - ``--assume-yes-for-downloads`` — non-interactive CI builds
      - ``--enable-plugin=numpy``    — numpy hidden imports (faster_whisper dep)
      - ``--include-package=faster_whisper`` — the ASR engine
      - ``--include-package=ctranslate2``    — the inference backend
      - ``--include-package=voice_typer``    — the app package
      - ``--include-package=websockets``     — the WS server transport
    """
    assert flag in script_texts[platform], (
        f"build_sidecar_{platform}.sh is missing required Nuitka flag `{flag}`. "
        "ADR-0020 §4 mandates this flag for every per-triple sidecar freeze."
    )


@pytest.mark.parametrize("platform", ["windows", "macos", "linux"])
def test_script_includes_ctranslate2_data_dir(script_texts: dict[str, str], platform: str):
    """Each script must ``--include-data-dir`` the ctranslate2/lib folder.

    Without this, ``libiomp5md.dll`` (Windows) / ``libiomp5.dylib`` (macOS) /
    ``libiomp5.so`` + ``libgomp.so`` (Linux) + the MKL/OpenMP runtimes are
    missing from the bundle and the frozen binary BUILDS but CRASHES on
    ``import ctranslate2`` (ADR-0020 §4.2 "CPU inference runtimes" + §11
    Known Issues).
    """
    text = script_texts[platform]
    assert "--include-data-dir" in text, (
        f"build_sidecar_{platform}.sh must use --include-data-dir to bundle "
        "the ctranslate2/lib folder (OpenMP + MKL runtimes)."
    )
    assert "ctranslate2/lib" in text, (
        f"build_sidecar_{platform}.sh must --include-data-dir the "
        "$SITE/ctranslate2/lib folder (captures OpenMP + MKL runtimes)."
    )


@pytest.mark.parametrize("platform", ["windows", "macos", "linux"])
def test_script_entry_point_is_ipc_server(script_texts: dict[str, str], platform: str):
    """The Nuitka entry point must be ``voice_typer/server/ipc_server.py``.

    ADR-0020 §4 — this is the same entry point used by the Electron path +
    the dev sidecar; only the freeze tool changes.
    """
    assert "voice_typer/server/ipc_server.py" in script_texts[platform], (
        f"build_sidecar_{platform}.sh entry point must be "
        "voice_typer/server/ipc_server.py (matches Electron + dev sidecar)."
    )


@pytest.mark.parametrize("platform", ["windows", "macos", "linux"])
def test_script_verifies_output_after_build(script_texts: dict[str, str], platform: str):
    """Each script must verify the output binary exists after Nuitka completes.

    A silent Nuitka failure (e.g. onefile compression error) can leave no
    output file; the script must hard-fail in that case rather than
    report success.
    """
    text = script_texts[platform]
    # The script must define OUTPUT_PATH or OUTPUT_BIN and check its existence.
    assert "OUTPUT_PATH" in text or "OUTPUT_BIN" in text, (
        f"build_sidecar_{platform}.sh must define OUTPUT_PATH / OUTPUT_BIN."
    )
    assert '! -f "$OUTPUT_PATH"' in text or '! -x "$OUTPUT_BIN"' in text, (
        f"build_sidecar_{platform}.sh must verify the output binary exists: "
        '`if [[ ! -f "$OUTPUT_PATH" ]]; then echo ERROR; exit 1; fi` '
        "(or `! -x $OUTPUT_BIN` for the macOS/Linux executable-bit case)."
    )


# ─── 6. faster_whisper + ctranslate2 include-package (explicit re-assertion) ─
@pytest.mark.parametrize("platform", ["windows", "macos", "linux"])
def test_script_includes_faster_whisper_package(script_texts: dict[str, str], platform: str):
    """``--include-package=faster_whisper`` must be present (explicit re-assertion).

    ADR-0020 §4.2 / §4.3 / §4.4 all mandate this flag — faster_whisper is
    the ASR engine and is lazy-imported inside the server, so Nuitka's
    auto-discovery does NOT pick it up. Without this flag, the frozen
    binary crashes at first transcription request.
    """
    assert "--include-package=faster_whisper" in script_texts[platform], (
        f"build_sidecar_{platform}.sh must --include-package=faster_whisper "
        "(lazy-imported ASR engine — Nuitka will not auto-discover it)."
    )


@pytest.mark.parametrize("platform", ["windows", "macos", "linux"])
def test_script_includes_ctranslate2_package(script_texts: dict[str, str], platform: str):
    """``--include-package=ctranslate2`` must be present (explicit re-assertion).

    ADR-0020 §4.2 / §4.3 / §4.4 all mandate this flag — ctranslate2 is
    the inference backend used by faster_whisper and is lazy-imported,
    so Nuitka's auto-discovery does NOT pick it up.
    """
    assert "--include-package=ctranslate2" in script_texts[platform], (
        f"build_sidecar_{platform}.sh must --include-package=ctranslate2 "
        "(lazy-imported inference backend — Nuitka will not auto-discover it)."
    )


# 7.  ctranslate2/libs guard (Linux + macOS only) ─────────────────
def test_linux_script_has_xplat3_ctranslate2_libs_guard(script_texts: dict[str, str]):
    """The Linux script must carry the XPLAT-3 ``ctranslate2/libs`` (plural) guard.

    Some ctranslate2 wheel variants ship extra native libs under
    ``$SITE/ctranslate2/libs`` (plural) in addition to ``lib/`` (singular).
    Nuitka's ``--include-data-dir`` fails HARD if the source path is
    missing, so the Linux script must guard the optional ``--include-data-dir``
    for ``ctranslate2/libs`` behind an ``if [[ -d "$CT2_LIBS_DIR" ]]`` check
    (XPLAT-3-ctranslate2-guard pattern). The Linux script is the canonical
    reference source for this pattern.

    See: ``build_sidecar_linux.sh`` lines ~213 + ~229 (CT2_LIBS_DIR definition
    + the guard block).
    """
    text = script_texts["linux"]
    assert "CT2_LIBS_DIR" in text, "build_sidecar_linux.sh must define CT2_LIBS_DIR (XPLAT-3 ctranslate2/libs guard)."
    assert 'if [[ -d "$CT2_LIBS_DIR" ]]' in text, (
        "build_sidecar_linux.sh must guard the optional --include-data-dir "
        'for ctranslate2/libs behind `if [[ -d "$CT2_LIBS_DIR" ]]`.'
    )
    assert '--include-data-dir="$CT2_LIBS_DIR=$CT2_LIBS_DIR"' in text, (
        "build_sidecar_linux.sh must add the ctranslate2/libs data dir inside the guard block."
    )


def test_macos_script_has_xplat3_ctranslate2_libs_guard(script_texts: dict[str, str]):
    """The macOS script must carry the XPLAT-3 ``ctranslate2/libs`` (plural) guard.

    Mirrors the Linux script's XPLAT-3 guard. The macOS wheels sometimes
    ship a ``libs/`` folder alongside ``lib/``; Nuitka's
    ``--include-data-dir`` fails hard if the source is missing, so the
    macOS script must guard the optional ``--include-data-dir`` for
    ``ctranslate2/libs`` behind an ``if [[ -d "$CT2_LIBS_DIR" ]]`` check.

    See: ``build_sidecar_macos.sh`` lines ~106 + ~137.
    """
    text = script_texts["macos"]
    assert "CT2_LIBS_DIR" in text, "build_sidecar_macos.sh must define CT2_LIBS_DIR (XPLAT-3 ctranslate2/libs guard)."
    assert 'if [[ -d "$CT2_LIBS_DIR" ]]' in text, (
        "build_sidecar_macos.sh must guard the optional --include-data-dir "
        'for ctranslate2/libs behind `if [[ -d "$CT2_LIBS_DIR" ]]`.'
    )
    assert '--include-data-dir="$CT2_LIBS_DIR=$CT2_LIBS_DIR"' in text, (
        "build_sidecar_macos.sh must add the ctranslate2/libs data dir inside the guard block."
    )


def test_windows_script_known_gap_no_ctranslate2_libs_guard(script_texts: dict[str, str]):
    """BUILD-2 fix: the Windows script now HAS the ctranslate2/libs guard.

    Previously a KNOWN GAP — the Windows script only included the singular
    ``lib/`` with no guard for the optional ``libs/`` dir. BUILD-2 added
    the guard (mirroring the Linux + macOS XPLAT-3 pattern). This test
    now ASSERTS the guard IS present.

    See: ``tests/tauri/mig15/test_nuitka_windows_build.py``
    ::test_known_gap_no_ctranslate2_libs_guard for the canonical Windows
    guard test (also updated).
    """
    text = script_texts["windows"]
    assert "CT2_LIBS_DIR" in text, "build_sidecar_windows.sh should have CT2_LIBS_DIR guard (BUILD-2 fix)."
    assert "ctranslate2/libs" in text, "build_sidecar_windows.sh should reference ctranslate2/libs (BUILD-2 fix)."
    assert 'if [[ -d "$CT2_LIBS_DIR" ]]' in text, (
        "build_sidecar_windows.sh should guard the libs include with if [[ -d (BUILD-2 fix)."
    )


# ─── 8. PyInstaller fallback spec exists (ADR-0020 §4.5) ────────────────────
def test_pyinstaller_fallback_spec_exists():
    """``scripts/build/voice-typer.spec`` must exist as the safety-net build path.

    ADR-0020 §4.5: "Existing PyInstaller spec (`scripts/build/voice-typer.spec`)
    is the fallback. If Nuitka proves impractical on a target (e.g. macOS
    Apple Silicon ABI issues, Linux aarch64 missing wheels), the existing
    PyInstaller `--onedir` spec already bundles the native hotkey binaries,
    Linux permission scripts, data files, and platform-specific hidden
    imports. The sidecar entrypoint is identical; only the freeze tool
    changes."

    This spec MUST exist alongside the Nuitka scripts so a Nuitka failure
    on any single triple does NOT block a release.
    """
    assert PYINSTALLER_SPEC.is_file(), (
        f"missing PyInstaller fallback spec: {PYINSTALLER_SPEC}. "
        "ADR-0020 §4.5 mandates this as the safety-net build path."
    )
    assert PYINSTALLER_SPEC.stat().st_size > 1000, (
        f"{PYINSTALLER_SPEC} is suspiciously small "
        f"({PYINSTALLER_SPEC.stat().st_size} bytes); expected a full "
        "PyInstaller spec (~10+ KB)."
    )


def test_pyinstaller_fallback_spec_references_target_triple():
    """The PyInstaller fallback spec must compute the target triple.

    ADR-0020 §4.5 — when ``VOICE_TYPER_TAURI_SIDECAR=1``, the spec must
    emit ``python-sidecar-<triple>[.exe]`` (same filename pattern as the
    Nuitka scripts) so the resulting binary can be dropped into
    ``src-tauri/bin/`` and Tauri's ``externalBin`` mechanism picks it up
    identically.
    """
    text = PYINSTALLER_SPEC.read_text(encoding="utf-8")
    assert "VOICE_TYPER_TAURI_SIDECAR" in text, (
        "voice-typer.spec must check the VOICE_TYPER_TAURI_SIDECAR env var "
        "to switch between the Tauri sidecar path + the legacy Electron path."
    )
    # Must compute the triple for all three platforms (mirror target_triple_for).
    assert "pc-windows-msvc" in text, (
        "voice-typer.spec must compute the Windows target triple (x86_64-pc-windows-msvc / aarch64-pc-windows-msvc)."
    )
    assert "apple-darwin" in text, "voice-typer.spec must compute the macOS target triple."
    assert "unknown-linux-gnu" in text, "voice-typer.spec must compute the Linux target triple."
    # Must construct the python-sidecar-<triple> name.
    assert "python-sidecar-" in text, (
        "voice-typer.spec must construct the output name as `python-sidecar-<triple>` in Tauri sidecar mode."
    )


def test_pyinstaller_fallback_spec_uses_same_entry_point():
    """The PyInstaller fallback spec must use the SAME entry point as Nuitka.

    ADR-0020 §4.5: "the sidecar entrypoint is identical; only the freeze
    tool changes." This is ``voice_typer/server/ipc_server.py`` for both
    Nuitka (per-platform scripts) and PyInstaller (fallback spec).
    """
    text = PYINSTALLER_SPEC.read_text(encoding="utf-8")
    assert "voice_typer" in text and "ipc_server.py" in text, (
        "voice-typer.spec must use voice_typer/server/ipc_server.py as the "
        "entry point (identical to the Nuitka scripts — ADR-0020 §4.5)."
    )


# ─── 9. Unified nuitka_freeze.sh wrapper dispatch ───────────────────────────
def test_nuitka_freeze_wrapper_exists_and_is_valid_bash():
    """``scripts/build/nuitka_freeze.sh`` must exist + be bash-syntax-valid.

    This is the platform-agnostic entry point for the freeze; it dispatches
    to the right per-platform script based on the host OS (ADR-0020 §4).
    """
    assert NUITKA_FREEZE_WRAPPER.is_file(), f"missing unified Nuitka freeze wrapper: {NUITKA_FREEZE_WRAPPER}"
    if not bash_usable():
        pytest.skip("bash not available or not usable on this host — cannot run `bash -n`.")
    result = subprocess.run(
        ["bash", "-n", str(NUITKA_FREEZE_WRAPPER)],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, f"bash -n failed on {NUITKA_FREEZE_WRAPPER}:\n{result.stderr}"


def test_nuitka_freeze_wrapper_dispatches_to_per_platform_scripts():
    """The wrapper must dispatch to ``build_sidecar_<host>.sh`` based on host OS.

    ADR-0020 §4 — Nuitka cannot cross-compile, so the wrapper detects the
    host OS (Darwin → macos, MINGW/MSYS/CYGWIN → windows, Linux → linux)
    + dispatches to the matching per-platform script with the target arch
    as the positional arg.
    """
    text = NUITKA_FREEZE_WRAPPER.read_text(encoding="utf-8")
    # Host OS detection cases.
    assert "Darwin" in text, "nuitka_freeze.sh must detect macOS hosts via `uname -s` == Darwin."
    assert "MINGW" in text or "MSYS" in text, (
        "nuitka_freeze.sh must detect Windows hosts via `uname -s` matching MINGW*/MSYS*/CYGWIN*."
    )
    assert "Linux" in text, "nuitka_freeze.sh must detect Linux hosts via `uname -s` == Linux."
    # Dispatch via build_sidecar_${HOST_PLATFORM}.sh.
    assert "build_sidecar_${HOST_PLATFORM}.sh" in text, (
        "nuitka_freeze.sh must construct the dispatch script path as "
        "build_sidecar_${HOST_PLATFORM}.sh (single dispatch table)."
    )
    # Triple construction mirrors target_triple_for in spawn.rs.
    assert "pc-windows-msvc" in text
    assert "apple-darwin" in text
    assert "unknown-linux-gnu" in text
    # --check dry-run mode.
    assert "--check" in text, (
        "nuitka_freeze.sh must support a --check dry-run mode (prints build plan + exits 0 without invoking Nuitka)."
    )


def test_nuitka_freeze_wrapper_documents_pyinstaller_fallback():
    """The wrapper docstring must point at the PyInstaller fallback spec.

    ADR-0020 §4.5 — the PyInstaller fallback is the safety-net path. The
    unified wrapper's docstring should reference it so an operator who
    hits a Nuitka failure on a single triple knows where to fall back.
    """
    text = NUITKA_FREEZE_WRAPPER.read_text(encoding="utf-8")
    assert "voice-typer.spec" in text or "PyInstaller" in text, (
        "nuitka_freeze.sh must document the PyInstaller fallback (scripts/build/voice-typer.spec) per ADR-0020 §4.5."
    )
