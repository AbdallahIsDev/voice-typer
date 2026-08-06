"""Regression tests for ``scripts/gen_tauri_icons_stub.py``.

The script stands up the placeholder PNG icons + sidecar/native/prewarm
binary stubs that ``cargo tauri build`` needs to even start packaging.
These tests verify:

1. Generation creates every expected file at the right path.
2. Generated PNGs are valid (magic bytes + IHDR dimensions; Pillow decode
   if Pillow happens to be installed).
3. ``--check`` exits 0 when stubs are present and non-zero when missing.
4. ``--clean`` removes every stub file we generated (and never touches
   real binaries that a developer may have built at the same paths).
5. Stub sidecar scripts fail loudly (exit 1 + ``STUB`` marker on stderr)
   when executed — the safety feature that prevents accidentally shipping
   stubs.
"""

from __future__ import annotations

import os
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

import filelock
import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = PROJECT_ROOT / "scripts" / "gen_tauri_icons_stub.py"
SRC_TAURI = PROJECT_ROOT / "src-tauri"

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"

# Hint for xdist schedulers that DO respect ``xdist_group`` (loadgroup /
# loadscope). The hard cross-process serialization is the file lock in
# ``_serialize_and_cleanup`` below — xdist's default ``load`` scheduler
# does NOT strictly honor ``xdist_group`` (verified on xdist 3.8.0), so
# the marker alone is insufficient. Both are kept: the marker is a no-op
# when xdist isn't active, and the file lock covers every scheduler mode.
# (C-TEST-5: test isolation; C-STYLE-1: minimal, documented change.)
pytestmark = pytest.mark.xdist_group("gen_tauri_icons_stub")

# Cross-process lock file — lives in the per-user temp dir so concurrent
# CI runs by different users don't contend. Acquired by every test in
# this module via the autouse ``_serialize_and_cleanup`` fixture so the
# generate→read→clean cycle is atomic across xdist workers.
_LOCK_PATH = Path(tempfile.gettempdir()) / "voice-typer-gen-tauri-icons-stub.test.lock"

# Target triples (mirrors the script's SIDECAR_TRIPLES).
TRIPLES = [
    "x86_64-pc-windows-msvc",
    "aarch64-pc-windows-msvc",
    "x86_64-apple-darwin",
    "aarch64-apple-darwin",
    "x86_64-unknown-linux-gnu",
    "aarch64-unknown-linux-gnu",
]
WINDOWS_TRIPLES = {"x86_64-pc-windows-msvc", "aarch64-pc-windows-msvc"}


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    """Run the stub script with the given args; capture stdout/stderr/exit."""
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True,
        text=True,
        cwd=str(PROJECT_ROOT),
    )


def _stub_paths() -> list[Path]:
    """Mirror of the script's _all_stub_paths() — every file we expect to exist."""
    paths: list[Path] = [
        SRC_TAURI / "icons/32x32.png",
        SRC_TAURI / "icons/128x128.png",
        SRC_TAURI / "icons/128x128@2x.png",
        SRC_TAURI / "icons/icon.png",
    ]
    for triple in TRIPLES:
        ext = ".exe" if triple in WINDOWS_TRIPLES else ""
        paths.append(SRC_TAURI / "bin" / f"python-sidecar-{triple}{ext}")
    paths.extend(
        [
            SRC_TAURI / "resources/native/windows-key-listener.exe",
            SRC_TAURI / "resources/native/macos-key-listener",
            SRC_TAURI / "resources/native/linux-key-listener",
        ]
    )
    for triple in TRIPLES:
        ext = ".exe" if triple in WINDOWS_TRIPLES else ""
        paths.append(SRC_TAURI / "resources" / f"prewarm-{triple}{ext}")
    return paths


@pytest.fixture(autouse=True)
def _serialize_and_cleanup():
    """Acquire a cross-process file lock for the duration of each test, then
    clean up the generated stub files.

    WHY THE LOCK: every test in this module generates + verifies stub files
    in the shared on-disk ``src-tauri/`` tree, and the post-test cleanup
    runs ``scripts/gen_tauri_icons_stub.py --clean``. Under ``pytest -n
    auto`` (xdist) parallel workers race — one worker's ``--clean`` deletes
    a PNG another worker is mid-read, surfacing as intermittent
    ``FileNotFoundError`` on the icon / sidecar / prewarm paths.

    xdist's default ``load`` scheduler does NOT strictly honor
    ``@pytest.mark.xdist_group`` (verified on xdist 3.8.0 — tests with the
    same group still landed on different workers). The module-level
    ``pytestmark = pytest.mark.xdist_group(...)`` is kept as a hint for
    schedulers that DO respect it (``loadgroup`` / ``loadscope``), but the
    file lock is the actual cross-process guarantee. It serializes every
    test in this module so the generate → read → clean cycle is atomic.

    The lock lives in the per-user temp dir, so concurrent CI runs by
    different users don't contend. ``timeout=60`` bounds the wait so a
    crashed worker can't hang the suite forever. (C-TEST-5: test isolation.)
    """
    lock = filelock.FileLock(str(_LOCK_PATH), timeout=60)
    with lock:
        yield
        # Ensure stubs are cleaned up after each test (don't pollute the repo).
        _run("--clean")


def test_generate_creates_all_expected_stubs():
    """The script with no args should create every expected stub file."""
    _run("--clean")  # start from a clean state
    result = _run()
    assert result.returncode == 0, f"generate failed:\nstdout={result.stdout}\nstderr={result.stderr}"
    missing = [p for p in _stub_paths() if not p.exists()]
    assert not missing, f"missing stub files: {missing}"


def test_generate_stdout_lists_summary():
    """The generate output should clearly list what was generated + a warning."""
    _run("--clean")
    result = _run()
    assert result.returncode == 0
    assert "[gen_tauri_icons_stub]" in result.stdout
    assert "Generated stub files:" in result.stdout
    assert "Summary:" in result.stdout
    assert "WARNING" in result.stdout
    # Spot-check that every category is mentioned.
    assert "PNG icons:" in result.stdout
    assert "Sidecar binaries:" in result.stdout
    assert "Native resources:" in result.stdout
    assert "Prewarm resources:" in result.stdout


@pytest.mark.parametrize(
    "rel, expected",
    [
        ("icons/32x32.png", (32, 32)),
        ("icons/128x128.png", (128, 128)),
        ("icons/128x128@2x.png", (256, 256)),
        ("icons/icon.png", (512, 512)),
    ],
)
def test_generated_pngs_have_valid_signature_and_ihdr(rel, expected):
    """Each generated PNG must have the magic bytes + correct IHDR dimensions."""
    _run()
    path = SRC_TAURI / rel
    data = path.read_bytes()
    # Magic bytes.
    assert data.startswith(PNG_MAGIC), f"bad PNG magic in {rel}"
    # First chunk: length=13 (IHDR), type=IHDR.
    assert data[8:12] == b"\x00\x00\x00\x0d", f"bad IHDR length in {rel}"
    assert data[12:16] == b"IHDR", f"bad IHDR type in {rel}"
    # IHDR data: width (4 BE) + height (4 BE) + bit_depth (1) + color_type (1) + ...
    width, height = struct.unpack(">II", data[16:24])
    assert (width, height) == expected, f"{rel}: IHDR says {(width, height)}, expected {expected}"
    # bit_depth=8, color_type=6 (RGBA) — Tauri v2 generate_context!()
    # requires RGBA; RGB (color_type=2) is rejected with
    # "icon ... is not RGBA".
    assert data[24] == 8, f"{rel}: expected bit_depth=8, got {data[24]}"
    assert data[25] == 6, f"{rel}: expected color_type=6 (RGBA), got {data[25]}"
    # Must end with an IEND chunk.
    assert b"IEND" in data, f"{rel}: no IEND chunk found"


@pytest.mark.real_pil
def test_generated_pngs_decode_with_pillow_if_available():
    """If Pillow is installed, the PNGs must decode to the expected size.

    Marked ``real_pil`` because the project-wide ``tests/conftest.py``
    auto-mocks PIL by default; this test needs the real ``PIL.Image`` to
    actually decode the PNG bytes.
    """
    try:
        from PIL import Image
    except ImportError:
        pytest.skip("Pillow not installed — skipping decode check")
    _run()
    for rel, expected in [
        ("icons/32x32.png", (32, 32)),
        ("icons/128x128.png", (128, 128)),
        ("icons/128x128@2x.png", (256, 256)),
        ("icons/icon.png", (512, 512)),
    ]:
        path = SRC_TAURI / rel
        with Image.open(path) as img:
            assert img.size == expected, f"{rel}: Pillow read {img.size}"


def test_check_exits_zero_when_stubs_present():
    """``--check`` must exit 0 when all stubs are present."""
    _run()  # generate
    result = _run("--check")
    assert result.returncode == 0, f"--check should pass:\nstdout={result.stdout}\nstderr={result.stderr}"
    assert "OK" in result.stdout


def test_check_exits_nonzero_when_stubs_missing():
    """``--check`` must exit non-zero when stubs are missing (CI gate)."""
    _run("--clean")
    result = _run("--check")
    assert result.returncode != 0, "--check should fail when stubs are missing"
    assert "MISSING" in result.stderr or "MISSING" in result.stdout


def test_clean_removes_generated_stubs():
    """``--clean`` must remove every stub file the script generated.

    In the dev/CI environment there are no real binaries at any stub
    path, so every file we just generated must be gone after ``--clean``.
    (The ``test_clean_does_not_remove_real_binary`` test separately
    verifies that a planted real binary IS preserved.)
    """
    _run()  # generate
    result = _run("--clean")
    assert result.returncode == 0, f"--clean failed:\nstdout={result.stdout}\nstderr={result.stderr}"
    leftover = [p for p in _stub_paths() if p.exists()]
    assert not leftover, f"--clean left stub files behind: {[str(p) for p in leftover]}"


def test_clean_does_not_remove_real_binary():
    """``--clean`` must preserve a real (large, marker-free) binary at a stub path."""
    _run()  # generate stubs first
    # Plant a fake "real" binary at one of the sidecar paths: > 8 KB and
    # contains neither the PNG signature nor the STUB_MARKER string.
    real_binary_path = SRC_TAURI / "bin" / "python-sidecar-x86_64-unknown-linux-gnu"
    real_binary_path.write_bytes(b"\x7fELF" + b"\x00" * 32768)  # 32 KB, no marker
    os.chmod(real_binary_path, 0o755)

    result = _run("--clean")
    assert result.returncode == 0
    # The real binary must still exist.
    assert real_binary_path.exists(), "--clean deleted a real binary — heuristic failed"
    # But the other stubs must be gone.
    other_stub = SRC_TAURI / "bin" / "python-sidecar-x86_64-apple-darwin"
    assert not other_stub.exists(), "--clean did not remove a sibling stub"


def test_check_and_clean_are_mutually_exclusive():
    """``--check --clean`` together must fail (mutually exclusive group).."""
    result = _run("--check", "--clean")
    assert result.returncode != 0, "argparse should reject --check + --clean together"


def _posix_sidecar_stubs() -> list[Path]:
    """All POSIX (non-Windows) stub sidecar paths generated by the script."""
    return [
        SRC_TAURI / "bin/python-sidecar-x86_64-apple-darwin",
        SRC_TAURI / "bin/python-sidecar-aarch64-apple-darwin",
        SRC_TAURI / "bin/python-sidecar-x86_64-unknown-linux-gnu",
        SRC_TAURI / "bin/python-sidecar-aarch64-unknown-linux-gnu",
    ]


def _linux_sidecar_stubs() -> list[Path]:
    """Only the Linux-variant POSIX stub sidecars."""
    return [
        SRC_TAURI / "bin/python-sidecar-x86_64-unknown-linux-gnu",
        SRC_TAURI / "bin/python-sidecar-aarch64-unknown-linux-gnu",
    ]


def _macos_sidecar_stubs() -> list[Path]:
    """Only the macOS-variant POSIX stub sidecars."""
    return [
        SRC_TAURI / "bin/python-sidecar-x86_64-apple-darwin",
        SRC_TAURI / "bin/python-sidecar-aarch64-apple-darwin",
    ]


@pytest.mark.skipif(sys.platform != "linux", reason="linux-only POSIX sidecar exec")
def test_stub_sidecar_scripts_exit_nonzero_with_marker_linux():
    """Executable Linux stub sidecars must exit 1 + print STUB to stderr.

    This is the safety feature: a stub that accidentally got into a
    release build fails loudly at runtime instead of silently doing nothing.

    Replaces the previous EC-26 silent ``if sys.platform == "win32":``
    guard with an explicit per-platform test so non-Linux runs report
    SKIP (not silent PASS) — the orchestrator's acceptance criteria
    require visibility into which platform branches actually executed.
    """
    _run()
    for p in _linux_sidecar_stubs():
        if not p.exists():
            continue
        result = subprocess.run([str(p)], capture_output=True, text=True)
        assert result.returncode != 0, f"{p.name} should exit non-zero (got {result.returncode})"
        assert "STUB" in result.stderr, f"{p.name} should print STUB marker to stderr; got: {result.stderr!r}"


@pytest.mark.skipif(sys.platform != "darwin", reason="macos-only POSIX sidecar exec")
def test_stub_sidecar_scripts_exit_nonzero_with_marker_macos():
    """Executable macOS stub sidecars must exit 1 + print STUB to stderr.

    Mirrors the Linux variant but exercises the two ``*-apple-darwin``
    stubs so a macOS CI run reports the actual exec behavior on that
    platform.
    """
    _run()
    for p in _macos_sidecar_stubs():
        if not p.exists():
            continue
        result = subprocess.run([str(p)], capture_output=True, text=True)
        assert result.returncode != 0, f"{p.name} should exit non-zero (got {result.returncode})"
        assert "STUB" in result.stderr, f"{p.name} should print STUB marker to stderr; got: {result.stderr!r}"


@pytest.mark.skipif(sys.platform != "win32", reason="windows-only POSIX sidecar content check")
def test_stub_sidecar_scripts_exit_nonzero_with_marker_windows():
    """On Windows the POSIX stub files cannot be exec'd (``WinError 193``:
    not a valid Win32 application — there is no ``/bin/sh``), so we
    verify the marker is embedded in the stub content instead.

    The Windows .exe stubs fail at ``CreateProcess`` time, which is
    equally "loud" but not directly exec'-able from a Python test —
    this content check ensures a mis-packaged Windows build that picks
    the wrong binary would fail loudly when shipped.
    """
    _run()
    for p in _posix_sidecar_stubs():
        if not p.exists():
            continue
        content = p.read_text(encoding="utf-8", errors="replace")
        assert "STUB" in content, (
            f"{p.name} should embed the STUB marker so it fails "
            f"loudly if executed (Windows cannot run the POSIX "
            f"shell script directly)."
        )
