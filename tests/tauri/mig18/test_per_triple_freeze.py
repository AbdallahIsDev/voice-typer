"""
Per-triple Nuitka freeze configuration validation.
Gaps documented (report, do NOT fix, out of scope for MIG-1.8):
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from tests.fixtures.bash_utils import bash_usable

# Path from file → root:
PROJECT_ROOT = Path(__file__).resolve().parents[3]
BUILD_DIR = PROJECT_ROOT / "scripts" / "build"
BUILD_WINDOWS = BUILD_DIR / "build_sidecar_windows.sh"
BUILD_MACOS = BUILD_DIR / "build_sidecar_macos.sh"
BUILD_LINUX = BUILD_DIR / "build_sidecar_linux.sh"
NUITKA_FREEZE_WRAPPER = BUILD_DIR / "nuitka_freeze.sh"
PYINSTALLER_SPEC = BUILD_DIR / "lausu.spec"
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
    """Each script must produce ``python-sidecar-<triple>[.exe]`` for BOTH arches."""
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
    """Each script must output to ``src-tauri/bin/`` (Tauri externalBin location)."""
    assert "src-tauri/bin" in script_texts[platform], (
        f"build_sidecar_{platform}.sh must output to src-tauri/bin/ (Tauri externalBin location)."
    )


def test_tauri_conf_external_bin_uses_base_name():
    """``tauri.conf.json`` must declare ``externalBin`` with BASE names only."""
    assert TAURI_CONF.is_file(), f"missing: {TAURI_CONF}"
    conf = json.loads(TAURI_CONF.read_text(encoding="utf-8"))
    external_bin = conf.get("bundle", {}).get("externalBin", [])
    assert external_bin == ["bin/python-sidecar", "bin/lausu-worker"], (
        f"tauri.conf.json `bundle.externalBin` must be exactly "
        f'["bin/python-sidecar", "bin/lausu-worker"] '
        f"(Tauri appends the triple at runtime); got: {external_bin!r}"
    )


def test_tauri_conf_shell_config_is_v2_valid():
    """
    ``tauri.conf.json`` ``plugins.shell`` must be v2-valid.
    `scope`, expected `open`". The ``--ws`` spawn contract is owned by
    """
    conf = json.loads(TAURI_CONF.read_text(encoding="utf-8"))
    shell = conf.get("plugins", {}).get("shell")
    assert shell == {"open": False}, (
        "tauri.conf.json `plugins.shell` must be exactly {'open': false}, "
        "tauri-plugin-shell v2 rejects 'sidecar'/'scope' keys at startup "
        f"('unknown field `scope`, expected `open`'); got {shell!r}"
    )


@pytest.mark.parametrize("platform", ["windows", "macos", "linux"])
def test_script_references_python_build_standalone(script_texts: dict[str, str], platform: str):
    """Each script must reference ``python-build-standalone`` (ADR-0020 §4)."""
    text = script_texts[platform]
    assert "python-build-standalone" in text, (
        f"build_sidecar_{platform}.sh must reference python-build-standalone "
        "(ADR-0020 §4 base interpreter for Nuitka freezing)."
    )


@pytest.mark.parametrize("platform", ["windows", "macos", "linux"])
def test_script_pins_cpython_3_12(script_texts: dict[str, str], platform: str):
    """
    Each script must pin to ``cpython-3.12.x`` (NOT 3.13+).
    ADR-0020 §4.2: "Pin the build interpreter to python-build-standalone
    """
    text = script_texts[platform]
    assert "cpython-3.12" in text, (
        f"build_sidecar_{platform}.sh must reference cpython-3.12.x "
        "(ADR-0020 §4.2, wheel-tag compatibility with faster_whisper + "
        "ctranslate2; do NOT use 3.13+ yet)."
    )


@pytest.mark.parametrize("platform", ["windows", "macos", "linux"])
def test_script_uses_voice_typer_pybs_dir_env(script_texts: dict[str, str], platform: str):
    """Each script must honor the ``VOICE_TYPER_PYBS_DIR`` env var."""
    text = script_texts[platform]
    assert "VOICE_TYPER_PYBS_DIR" in text, (
        f"build_sidecar_{platform}.sh must honor the VOICE_TYPER_PYBS_DIR "
        "env var (set by the CI workflow to the python-build-standalone install)."
    )


@pytest.mark.parametrize("platform", ["windows", "macos", "linux"])
@pytest.mark.parametrize("flag", COMMON_NUITKA_FLAGS)
def test_script_contains_expected_nuitka_flag(script_texts: dict[str, str], platform: str, flag: str):
    """Each per-platform script must include every ADR-0020 §4-mandated Nuitka flag."""
    assert flag in script_texts[platform], (
        f"build_sidecar_{platform}.sh is missing required Nuitka flag `{flag}`. "
        "ADR-0020 §4 mandates this flag for every per-triple sidecar freeze."
    )


@pytest.mark.parametrize("platform", ["windows", "macos", "linux"])
def test_script_includes_ctranslate2_data_dir(script_texts: dict[str, str], platform: str):
    """Each script must ``--include-data-dir`` the ctranslate2/lib folder."""
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
    """The Nuitka entry point must be ``voice_typer/server/ipc_server.py``."""
    assert "voice_typer/server/ipc_server.py" in script_texts[platform], (
        f"build_sidecar_{platform}.sh entry point must be "
        "voice_typer/server/ipc_server.py (matches predecessor + dev sidecar)."
    )


@pytest.mark.parametrize("platform", ["windows", "macos", "linux"])
def test_script_verifies_output_after_build(script_texts: dict[str, str], platform: str):
    """Each script must verify the output binary exists after Nuitka completes."""
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
    """``--include-package=faster_whisper`` must be present (explicit re-assertion)."""
    assert "--include-package=faster_whisper" in script_texts[platform], (
        f"build_sidecar_{platform}.sh must --include-package=faster_whisper "
        "(lazy-imported ASR engine, Nuitka will not auto-discover it)."
    )


@pytest.mark.parametrize("platform", ["windows", "macos", "linux"])
def test_script_includes_ctranslate2_package(script_texts: dict[str, str], platform: str):
    """``--include-package=ctranslate2`` must be present (explicit re-assertion)."""
    assert "--include-package=ctranslate2" in script_texts[platform], (
        f"build_sidecar_{platform}.sh must --include-package=ctranslate2 "
        "(lazy-imported inference backend, Nuitka will not auto-discover it)."
    )


# 7.  ctranslate2/libs guard (Linux + macOS only) ─────────────────
def test_linux_script_has_xplat3_ctranslate2_libs_guard(script_texts: dict[str, str]):
    """The Linux script must carry the XPLAT-3 ``ctranslate2/libs`` (plural) guard."""
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
    """The macOS script must carry the XPLAT-3 ``ctranslate2/libs`` (plural) guard."""
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
    """BUILD-2 fix: the Windows script now HAS the ctranslate2/libs guard."""
    text = script_texts["windows"]
    assert "CT2_LIBS_DIR" in text, "build_sidecar_windows.sh should have CT2_LIBS_DIR guard (BUILD-2 fix)."
    assert "ctranslate2/libs" in text, "build_sidecar_windows.sh should reference ctranslate2/libs (BUILD-2 fix)."
    assert 'if [[ -d "$CT2_LIBS_DIR" ]]' in text, (
        "build_sidecar_windows.sh should guard the libs include with if [[ -d (BUILD-2 fix)."
    )


def test_pyinstaller_fallback_spec_exists():
    """``scripts/build/lausu.spec`` must exist as the safety-net build path."""
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
    """The PyInstaller fallback spec must compute the target triple."""
    text = PYINSTALLER_SPEC.read_text(encoding="utf-8")
    assert "VOICE_TYPER_TAURI_SIDECAR" in text, (
        "lausu.spec must check the VOICE_TYPER_TAURI_SIDECAR env var "
        "to switch between the Tauri sidecar path + the legacy predecessor path."
    )
    # Must compute the triple for all three platforms (mirror target_triple_for).
    assert "pc-windows-msvc" in text, (
        "lausu.spec must compute the Windows target triple (x86_64-pc-windows-msvc / aarch64-pc-windows-msvc)."
    )
    assert "apple-darwin" in text, "lausu.spec must compute the macOS target triple."
    assert "unknown-linux-gnu" in text, "lausu.spec must compute the Linux target triple."
    # Must construct the python-sidecar-<triple> name.
    assert "python-sidecar-" in text, (
        "lausu.spec must construct the output name as `python-sidecar-<triple>` in Tauri sidecar mode."
    )


def test_pyinstaller_fallback_spec_uses_same_entry_point():
    """The PyInstaller fallback spec must use the SAME entry point as Nuitka."""
    text = PYINSTALLER_SPEC.read_text(encoding="utf-8")
    assert "voice_typer" in text and "ipc_server.py" in text, (
        "lausu.spec must use voice_typer/server/ipc_server.py as the "
        "entry point (identical to the Nuitka scripts, ADR-0020 §4.5)."
    )


def test_nuitka_freeze_wrapper_exists_and_is_valid_bash():
    """``scripts/build/nuitka_freeze.sh`` must exist + be bash-syntax-valid."""
    assert NUITKA_FREEZE_WRAPPER.is_file(), f"missing unified Nuitka freeze wrapper: {NUITKA_FREEZE_WRAPPER}"
    if not bash_usable():
        pytest.skip("bash not available or not usable on this host, cannot run `bash -n`.")
    result = subprocess.run(
        ["bash", "-n", str(NUITKA_FREEZE_WRAPPER)],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, f"bash -n failed on {NUITKA_FREEZE_WRAPPER}:\n{result.stderr}"


def test_nuitka_freeze_wrapper_dispatches_to_per_platform_scripts():
    """The wrapper must dispatch to ``build_sidecar_<host>.sh`` based on host OS."""
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
    """The wrapper docstring must point at the PyInstaller fallback spec."""
    text = NUITKA_FREEZE_WRAPPER.read_text(encoding="utf-8")
    assert "lausu.spec" in text or "PyInstaller" in text, (
        "nuitka_freeze.sh must document the PyInstaller fallback (scripts/build/lausu.spec) per ADR-0020 §4.5."
    )
