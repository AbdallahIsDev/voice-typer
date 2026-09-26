"""
Build-script glue validation.
Gaps documented (report, do NOT fix, out of scope for this glue test):
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

import filelock
import pytest

from tests.fixtures.bash_utils import bash_usable

# Path from file → root:
PROJECT_ROOT = Path(__file__).resolve().parents[3]
BUILD_DIR = PROJECT_ROOT / "scripts" / "build"
SCRIPTS_DIR = PROJECT_ROOT / "scripts"

ORCHESTRATOR = BUILD_DIR / "build_tauri_all.sh"
COMPILE_NATIVE = BUILD_DIR / "compile_native.sh"
ICON_STUB_GENERATOR = SCRIPTS_DIR / "gen_tauri_icons_stub.py"

pytestmark = pytest.mark.xdist_group("gen_tauri_icons_stub")
_ICON_STUB_LOCK_PATH = Path(tempfile.gettempdir()) / "lausu-gen-tauri-icons-stub.test.lock"


@pytest.fixture
def _serialize_icon_stub_generator():
    """Acquire the shared icon-stub lock for the generator e2e test."""
    lock = filelock.FileLock(str(_ICON_STUB_LOCK_PATH), timeout=60)
    with lock:
        yield


# Per-platform helpers invoked (directly or indirectly) by the orchestrator.
SIDECAR_SCRIPTS = {
    "windows": BUILD_DIR / "build_sidecar_windows.sh",
    "macos": BUILD_DIR / "build_sidecar_macos.sh",
    "linux": BUILD_DIR / "build_sidecar_linux.sh",
}
PREWARM_SCRIPTS = {
    "windows": BUILD_DIR / "build_prewarm_windows.sh",
    "macos": BUILD_DIR / "build_prewarm_macos.sh",
    "linux": BUILD_DIR / "build_prewarm_linux.sh",
}
NATIVE_LISTENER_SCRIPTS = {
    "windows": BUILD_DIR / "build_native_listener_windows.sh",
    "macos": BUILD_DIR / "build_native_listener_macos.sh",
    "linux": BUILD_DIR / "build_native_listener_linux.sh",
}


@pytest.fixture(scope="module")
def orchestrator_text() -> str:
    """Read the orchestrator script once per module; fail fast if missing."""
    assert ORCHESTRATOR.is_file(), f"build_tauri_all.sh not found at {ORCHESTRATOR}. Did the project layout change?"
    return ORCHESTRATOR.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def compile_native_text() -> str:
    """Read compile_native.sh once per module; fail fast if missing."""
    assert COMPILE_NATIVE.is_file(), f"compile_native.sh not found at {COMPILE_NATIVE}. Did the project layout change?"
    return COMPILE_NATIVE.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def icon_stub_text() -> str:
    """Read gen_tauri_icons_stub.py once per module; fail fast if missing."""
    assert ICON_STUB_GENERATOR.is_file(), (
        f"gen_tauri_icons_stub.py not found at {ICON_STUB_GENERATOR}. Did the project layout change?"
    )
    return ICON_STUB_GENERATOR.read_text(encoding="utf-8")


def test_orchestrator_exists():
    """``build_tauri_all.sh`` must exist at the canonical path."""
    assert ORCHESTRATOR.is_file(), f"missing: {ORCHESTRATOR}"
    # Also assert it's non-empty (a stub would be a regression).
    assert ORCHESTRATOR.stat().st_size > 1000, (
        f"{ORCHESTRATOR} is suspiciously small ({ORCHESTRATOR.stat().st_size} bytes); "
        "expected a full orchestrator script (~5-8 KB)."
    )


def test_orchestrator_is_bash_syntax_valid():
    """``bash -n`` must parse the orchestrator without syntax errors."""
    if not bash_usable():
        pytest.skip("bash not available or not usable on this host, cannot run `bash -n`.")
    result = subprocess.run(
        ["bash", "-n", str(ORCHESTRATOR)],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, (
        f"bash -n failed on {ORCHESTRATOR}:\n--- stderr ---\n{result.stderr}\n--- stdout ---\n{result.stdout}"
    )


def test_orchestrator_has_shebang_and_strict_mode(orchestrator_text: str):
    """The orchestrator must use ``#!/usr/bin/env bash`` + ``set -euo pipefail``."""
    assert orchestrator_text.startswith("#!/usr/bin/env bash"), (
        "build_tauri_all.sh must start with `#!/usr/bin/env bash`."
    )
    assert "set -euo pipefail" in orchestrator_text, "build_tauri_all.sh must enable strict mode (`set -euo pipefail`)."


@pytest.mark.parametrize("platform", ["windows", "macos", "linux"])
def test_orchestrator_runs_per_platform_sidecar_builds(orchestrator_text: str, platform: str):
    """The orchestrator must invoke ``build_sidecar_<platform>.sh`` for each platform."""
    script_name = f"build_sidecar_{platform}.sh"
    assert script_name in orchestrator_text, (
        f"build_tauri_all.sh must invoke `{script_name}` for the {platform} "
        f"platform (ADR-0020 §4). Missing reference in orchestrator."
    )
    assert (
        f'bash "$SCRIPT_DIR/{script_name}"' in orchestrator_text
        or f'bash "$SCRIPT_DIR/{script_name}"' in orchestrator_text
    ), (
        f"build_tauri_all.sh must invoke {script_name} via "
        f'`bash "$SCRIPT_DIR/{script_name}"`. (Comment-only references '
        "are not enough, the script must be dispatched.)"
    )
    # Sanity: the referenced script must actually exist.
    assert SIDECAR_SCRIPTS[platform].is_file(), (
        f"{script_name} referenced by orchestrator but not found at {SIDECAR_SCRIPTS[platform]}."
    )


@pytest.mark.parametrize("platform", ["windows", "macos", "linux"])
def test_orchestrator_runs_per_platform_prewarm_builds(orchestrator_text: str, platform: str):
    """The orchestrator must invoke ``build_prewarm_<platform>.sh`` for each platform."""
    script_name = f"build_prewarm_{platform}.sh"
    assert script_name in orchestrator_text, (
        f"build_tauri_all.sh must invoke `{script_name}` for the {platform} "
        f"platform (ADR-0020 §5). Missing reference in orchestrator."
    )
    assert f'bash "$SCRIPT_DIR/{script_name}"' in orchestrator_text, (
        f'build_tauri_all.sh must invoke {script_name} via `bash "$SCRIPT_DIR/{script_name}"`.'
    )
    assert PREWARM_SCRIPTS[platform].is_file(), (
        f"{script_name} referenced by orchestrator but not found at {PREWARM_SCRIPTS[platform]}."
    )


@pytest.mark.parametrize("platform", ["windows", "macos", "linux"])
def test_orchestrator_runs_native_listener_builds(orchestrator_text: str, platform: str):
    """The orchestrator must invoke ``build_native_listener_<platform>.sh``."""
    script_name = f"build_native_listener_{platform}.sh"
    assert script_name in orchestrator_text, (
        f"build_tauri_all.sh must invoke `{script_name}` for the {platform} "
        f"platform (ADR-0020 §6.4). Missing reference in orchestrator."
    )
    assert f'bash "$SCRIPT_DIR/{script_name}"' in orchestrator_text, (
        f'build_tauri_all.sh must invoke {script_name} via `bash "$SCRIPT_DIR/{script_name}"`.'
    )
    assert NATIVE_LISTENER_SCRIPTS[platform].is_file(), (
        f"{script_name} referenced by orchestrator but not found at {NATIVE_LISTENER_SCRIPTS[platform]}."
    )


def test_orchestrator_indirectly_invokes_compile_native_via_wrappers(
    orchestrator_text: str,
):
    """The native-listener wrappers must invoke ``compile_native.sh`` (macOS+Linux)"""
    # The macOS + Linux wrappers must invoke compile_native.sh.
    macos_wrapper = NATIVE_LISTENER_SCRIPTS["macos"].read_text(encoding="utf-8")
    linux_wrapper = NATIVE_LISTENER_SCRIPTS["linux"].read_text(encoding="utf-8")
    assert "compile_native.sh" in macos_wrapper, (
        "build_native_listener_macos.sh must invoke compile_native.sh (which detects macOS + runs swiftc)."
    )
    assert "compile_native.sh" in linux_wrapper, (
        "build_native_listener_linux.sh must invoke compile_native.sh (which detects Linux + runs gcc)."
    )
    # The Windows wrapper invokes compile_native.ps1 (PowerShell), NOT
    windows_wrapper = NATIVE_LISTENER_SCRIPTS["windows"].read_text(encoding="utf-8")
    assert "compile_native.ps1" in windows_wrapper, (
        "build_native_listener_windows.sh must invoke compile_native.ps1 "
        "(PowerShell, cl.exe needs the Developer Command Prompt env)."
    )


def test_orchestrator_references_compile_native_script(
    orchestrator_text: str,
):
    """The orchestrator (or its header docstring) must reference compile_native.sh"""
    # Either the orchestrator names compile_native.sh directly OR it
    direct = "compile_native.sh" in orchestrator_text
    indirect = all(f"build_native_listener_{p}.sh" in orchestrator_text for p in ("windows", "macos", "linux"))
    assert direct or indirect, (
        "build_tauri_all.sh must reference compile_native.sh (directly) "
        "OR build_native_listener_<platform>.sh for all 3 platforms "
        "(indirect, the wrappers invoke compile_native.sh)."
    )


def test_orchestrator_invokes_gen_tauri_icons_stub(orchestrator_text: str):
    """BUILD-4 fix: the orchestrator now invokes ``gen_tauri_icons_stub.py``."""
    invokes_directly = (
        "gen_tauri_icons_stub.py" in orchestrator_text
        and "python" in orchestrator_text
        and "gen_tauri_icons_stub" in orchestrator_text
    )
    assert invokes_directly, (
        "build_tauri_all.sh should invoke gen_tauri_icons_stub.py (BUILD-4 fix). "
        "The orchestrator must generate binary stubs before cargo tauri build."
    )


def test_orchestrator_runs_cargo_tauri_build(orchestrator_text: str):
    """The orchestrator must run ``cargo tauri build`` (Phase 1c)."""
    assert "cargo tauri build" in orchestrator_text, (
        "build_tauri_all.sh must invoke `cargo tauri build` (ADR-0020 §7). "
        "This is the final bundling step that produces the platform installer."
    )
    # Must accept --target triple (host triple by default).
    assert "--target" in orchestrator_text, (
        "build_tauri_all.sh must support `cargo tauri build --target <triple>` "
        "so the operator can build for a non-host triple (e.g. universal-apple-darwin)."
    )


def test_orchestrator_runs_cargo_tauri_build_after_sidecar_phase(
    orchestrator_text: str,
):
    """``cargo tauri build`` must run AFTER the sidecar + prewarm + native phase."""
    cargo_idx = orchestrator_text.find("cargo tauri build")
    sidecar_idx = orchestrator_text.find("build_sidecar_")
    assert cargo_idx > 0 and sidecar_idx > 0, (
        "Both `cargo tauri build` and `build_sidecar_*` must appear in the "
        "orchestrator (positions checked in separate tests)."
    )
    assert cargo_idx > sidecar_idx, (
        "build_tauri_all.sh: `cargo tauri build` must appear AFTER "
        "`build_sidecar_*` so the bundler picks up the freshly-built sidecar."
    )


def test_orchestrator_skips_sidecar_phase_with_flag(orchestrator_text: str):
    """The orchestrator must support ``--skip-sidecar`` for dev iteration."""
    assert "--skip-sidecar" in orchestrator_text, (
        "build_tauri_all.sh must support `--skip-sidecar` for dev iteration "
        "(re-run only cargo tauri build, skip the Nuitka freeze)."
    )
    # SKIP_SIDECAR flag must be checked before invoking the per-platform
    assert "SKIP_SIDECAR" in orchestrator_text, (
        "build_tauri_all.sh must declare a SKIP_SIDECAR variable + gate the per-platform build phase on it."
    )


def test_orchestrator_has_check_dry_run_mode(orchestrator_text: str):
    """The orchestrator must support ``--check`` (dry-run: print plan, exit 0)."""
    assert "--check" in orchestrator_text, "build_tauri_all.sh must support `--check` (dry-run: print plan, exit 0)."
    assert "CHECK_ONLY" in orchestrator_text, (
        "build_tauri_all.sh must declare a CHECK_ONLY variable + gate the actual build phases on it."
    )


def test_compile_native_script_exists():
    """``compile_native.sh`` must exist at the canonical path."""
    assert COMPILE_NATIVE.is_file(), f"missing: {COMPILE_NATIVE}"
    assert COMPILE_NATIVE.stat().st_size > 1000, (
        f"{COMPILE_NATIVE} is suspiciously small ({COMPILE_NATIVE.stat().st_size} bytes)."
    )


def test_compile_native_is_bash_syntax_valid():
    """``bash -n`` must parse compile_native.sh without syntax errors."""
    if not bash_usable():
        pytest.skip("bash not available or not usable on this host, cannot run `bash -n`.")
    result = subprocess.run(
        ["bash", "-n", str(COMPILE_NATIVE)],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, (
        f"bash -n failed on {COMPILE_NATIVE}:\n--- stderr ---\n{result.stderr}\n--- stdout ---\n{result.stdout}"
    )


def test_compile_native_has_shebang_and_strict_mode(compile_native_text: str):
    """compile_native.sh must use ``#!/usr/bin/env bash`` + ``set -euo pipefail``."""
    assert compile_native_text.startswith("#!/usr/bin/env bash"), (
        "compile_native.sh must start with `#!/usr/bin/env bash`."
    )
    assert "set -euo pipefail" in compile_native_text, "compile_native.sh must enable strict mode."


@pytest.mark.parametrize(
    "platform, source_file, out_file, compiler",
    [
        ("darwin", "macos-key-listener.swift", "macos-key-listener", "swiftc"),
        ("win32", "windows-key-listener.c", "windows-key-listener.exe", "cl.exe"),
        ("linux", "linux-key-listener.c", "linux-key-listener", "gcc"),
    ],
)
def test_compile_native_builds_all_three_listeners(
    compile_native_text: str, platform: str, source_file: str, out_file: str, compiler: str
):
    """each of the 3 native listeners (Windows + macOS + Linux)."""
    # The platform case branch must be present.
    assert f"{platform})" in compile_native_text, (
        f"compile_native.sh must have a `{platform})` case branch for the {platform} platform (ADR-0020 §6.4)."
    )
    # The source file must be referenced.
    assert source_file in compile_native_text, (
        f"compile_native.sh must reference the {platform} source file `{source_file}` (ADR-0020 §6.4)."
    )
    # The output binary name must be referenced.
    assert out_file in compile_native_text, (
        f"compile_native.sh must reference the {platform} output binary `{out_file}` (ADR-0020 §6.4)."
    )
    # The compiler must be referenced.
    assert compiler in compile_native_text, (
        f"compile_native.sh must reference the {platform} compiler `{compiler}` (ADR-0020 §6.4)."
    )


def test_compile_native_supports_check_mode(compile_native_text: str):
    """``compile_native.sh --check`` must verify the toolchain without building."""
    assert "--check" in compile_native_text, "compile_native.sh must support `--check` (verify toolchain, exit 0/1)."


# ─── 8. gen_tauri_icons_stub.py generates binary stubs (icons are committed) ─
def test_icon_stub_generator_exists():
    """``gen_tauri_icons_stub.py`` must exist at the canonical path."""
    assert ICON_STUB_GENERATOR.is_file(), f"missing: {ICON_STUB_GENERATOR}"
    assert ICON_STUB_GENERATOR.stat().st_size > 1000, (
        f"{ICON_STUB_GENERATOR} is suspiciously small ({ICON_STUB_GENERATOR.stat().st_size} bytes)."
    )


def test_icon_stub_generator_documents_icons_are_committed(icon_stub_text: str):
    """The icon set is committed real files; the generator only makes binary stubs."""
    src_tauri = PROJECT_ROOT / "src-tauri"
    missing = [
        rel
        for rel in ("32x32.png", "128x128.png", "128x128@2x.png", "icon.png", "icon.ico", "icon.icns")
        if not (src_tauri / "icons" / rel).is_file()
    ]
    assert not missing, (
        f"missing committed icons: {missing}, the icon set must be committed "
        "(generate once with `tauri icon` from voice_typer/client/scripts/logo.svg)."
    )
    # The generator must document that icons are committed (not generated).
    assert any(marker in icon_stub_text for marker in ("tauri icon", "logo.svg", "committed")), (
        "gen_tauri_icons_stub.py must document that the icons are committed "
        "real files and that it only generates binary stubs."
    )


def test_icon_stub_generator_generates_stub_sidecar_binaries(icon_stub_text: str):
    """The generator must produce stub sidecar binaries for all 6 triples."""
    # The 6 target triples.
    expected_triples = [
        "x86_64-pc-windows-msvc",
        "aarch64-pc-windows-msvc",
        "x86_64-apple-darwin",
        "aarch64-apple-darwin",
        "x86_64-unknown-linux-gnu",
        "aarch64-unknown-linux-gnu",
    ]
    for triple in expected_triples:
        assert triple in icon_stub_text, (
            f"gen_tauri_icons_stub.py must generate a stub sidecar for the `{triple}` target triple (ADR-0020 §4.1)."
        )
    # The stub marker (safety feature).
    assert "STUB" in icon_stub_text, (
        "gen_tauri_icons_stub.py must embed the STUB marker in every stub "
        "binary so a stub that accidentally ships fails loudly at runtime."
    )


def test_icon_stub_generator_generates_stub_native_binaries(icon_stub_text: str):
    """The generator must produce stub native-listener binaries for all 3 platforms."""
    for native_rel in (
        "windows-key-listener.exe",
        "macos-key-listener",
        "linux-key-listener",
    ):
        assert native_rel in icon_stub_text, (
            f"gen_tauri_icons_stub.py must generate a stub native-listener binary `{native_rel}` (ADR-0020 §6.4)."
        )


def test_icon_stub_generator_supports_check_mode(icon_stub_text: str):
    """``gen_tauri_icons_stub.py --check`` must exit 0 if stubs present, 1 if missing."""
    assert "--check" in icon_stub_text, (
        "gen_tauri_icons_stub.py must support `--check` (CI gate: exit 0 if all stubs present, 1 if any missing)."
    )


def test_icon_stub_generator_supports_clean_mode(icon_stub_text: str):
    """``gen_tauri_icons_stub.py --clean`` must remove stubs (preserving real artifacts)."""
    assert "--clean" in icon_stub_text, (
        "gen_tauri_icons_stub.py must support `--clean` (remove stubs, preserve real artifacts via heuristic)."
    )


def test_icon_stub_generator_run_produces_all_expected_files(_serialize_icon_stub_generator):
    """End-to-end smoke: generator produces binary stubs, preserves committed icons."""
    result = subprocess.run(
        [sys.executable, str(ICON_STUB_GENERATOR)],
        capture_output=True,
        text=True,
        cwd=str(PROJECT_ROOT),
        timeout=30,
    )
    assert result.returncode == 0, (
        f"gen_tauri_icons_stub.py failed:\n--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"
    )
    # The committed icons (4 PNGs + the Windows .ico + the macOS .icns)
    src_tauri = PROJECT_ROOT / "src-tauri"
    expected_icons = [
        src_tauri / "icons" / "32x32.png",
        src_tauri / "icons" / "128x128.png",
        src_tauri / "icons" / "128x128@2x.png",
        src_tauri / "icons" / "icon.png",
        src_tauri / "icons" / "icon.ico",
        src_tauri / "icons" / "icon.icns",
    ]
    missing = [p for p in expected_icons if not p.exists()]
    assert not missing, f"missing committed icons: {missing}"
    # Verify each icon is non-trivial (a valid PNG / ICO / ICNS container).
    for p in expected_icons:
        assert p.stat().st_size > 100, (
            f"{p.name} is suspiciously small ({p.stat().st_size} bytes); expected a valid icon container."
        )
    # Verify the 6 stub sidecar binaries were created.
    expected_sidecars = [
        src_tauri / "bin" / "python-sidecar-x86_64-pc-windows-msvc.exe",
        src_tauri / "bin" / "python-sidecar-aarch64-pc-windows-msvc.exe",
        src_tauri / "bin" / "python-sidecar-x86_64-apple-darwin",
        src_tauri / "bin" / "python-sidecar-aarch64-apple-darwin",
        src_tauri / "bin" / "python-sidecar-x86_64-unknown-linux-gnu",
        src_tauri / "bin" / "python-sidecar-aarch64-unknown-linux-gnu",
    ]
    missing_sidecars = [p for p in expected_sidecars if not p.exists()]
    assert not missing_sidecars, f"missing generated sidecar stubs: {missing_sidecars}"
    # Verify the 3 native-listener stubs were created.
    expected_native = [
        src_tauri / "resources" / "native" / "windows-key-listener.exe",
        src_tauri / "resources" / "native" / "macos-key-listener",
        src_tauri / "resources" / "native" / "linux-key-listener",
    ]
    missing_native = [p for p in expected_native if not p.exists()]
    assert not missing_native, f"missing generated native stubs: {missing_native}"
    cleanup = subprocess.run(
        [sys.executable, str(ICON_STUB_GENERATOR), "--clean"],
        capture_output=True,
        text=True,
        cwd=str(PROJECT_ROOT),
        timeout=30,
    )
    assert cleanup.returncode == 0, (
        f"gen_tauri_icons_stub.py --clean failed:\n--- stdout ---\n{cleanup.stdout}\n--- stderr ---\n{cleanup.stderr}"
    )
    leftover_icons = [p for p in expected_icons if not p.exists()]
    assert not leftover_icons, f"--clean deleted committed icons: {leftover_icons}"
    leftover_stubs = [p for p in expected_sidecars + expected_native if p.exists()]
    assert not leftover_stubs, f"--clean left binary stubs behind: {leftover_stubs}"
    # Restore the stubs this test just cleaned: later steps (cargo check /
    regen = subprocess.run(
        [sys.executable, str(ICON_STUB_GENERATOR)],
        capture_output=True,
        text=True,
        cwd=str(PROJECT_ROOT),
        timeout=30,
    )
    assert regen.returncode == 0, (
        f"post-clean stub restore failed:\n--- stdout ---\n{regen.stdout}\n--- stderr ---\n{regen.stderr}"
    )
    unrestored = [p for p in expected_sidecars + expected_native if not p.exists()]
    assert not unrestored, f"stubs still missing after restore: {unrestored}"


def test_known_gap_orchestrator_only_builds_host_platform(orchestrator_text: str):
    """the orchestrator only builds for the HOST platform."""
    # The orchestrator must dispatch on the host platform.
    assert "uname -s" in orchestrator_text, "build_tauri_all.sh must detect the host platform via `uname -s`."
    # The 3 platform case branches must be present.
    for host_pattern in ("Darwin*", "MINGW*|MSYS*|CYGWIN*", "Linux*"):
        assert host_pattern in orchestrator_text, (
            f"build_tauri_all.sh must have a `{host_pattern})` case branch for host platform detection."
        )


def test_known_gap_orchestrator_does_not_preflight_artifacts(orchestrator_text: str):
    """
    the orchestrator does NOT pre-flight the artifact set
    Phase 1c). DO NOT fix this, it's a polish item, not a correctness
    """
    # The orchestrator must NOT have a pre-flight check that verifies
    has_preflight = "python-sidecar-" in orchestrator_text and "test -f" in orchestrator_text
    assert not has_preflight, (
        "GAP-4 appears to be CLOSED: build_tauri_all.sh now pre-flights "
        "the sidecar artifact set. Remove the gap documentation from this "
        "test file's module docstring + this test."
    )
