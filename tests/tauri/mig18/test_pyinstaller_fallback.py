"""
Phase 1 + ADR-0020 §4.5: PyInstaller fallback spec validation.
Gaps documented (report, do NOT fix, out of scope for this gate check):
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

# Path from file → root:
PROJECT_ROOT = Path(__file__).resolve().parents[3]
SPEC_PATH = PROJECT_ROOT / "scripts" / "build" / "voice-typer.spec"


@pytest.fixture(scope="module")
def spec_text() -> str:
    """Read the PyInstaller spec once per module; fail fast if missing."""
    assert SPEC_PATH.is_file(), (
        f"voice-typer.spec not found at {SPEC_PATH}. "
        "Did the project layout change? ADR-0020 §4.5 requires this file "
        "to be retained as the PyInstaller fallback path."
    )
    return SPEC_PATH.read_text(encoding="utf-8")


def test_spec_file_exists():
    """ADR-0020 §4.5: the PyInstaller fallback spec must exist at the canonical path."""
    assert SPEC_PATH.is_file(), f"missing: {SPEC_PATH}"
    # A stub / empty regression guard, the real spec is >300 lines.
    assert SPEC_PATH.stat().st_size > 1000, (
        f"voice-typer.spec is suspiciously small ({SPEC_PATH.stat().st_size} bytes); "
        "expected a full PyInstaller spec (375+ lines)."
    )


def test_spec_docstring_documents_fallback_role(spec_text: str):
    """The spec's module docstring must self-document as the PyInstaller fallback."""
    assert "PyInstaller fallback" in spec_text, (
        "voice-typer.spec docstring must mention 'PyInstaller fallback' (ADR-0020 §4.5 framing)."
    )
    assert "ADR-0020" in spec_text, "voice-typer.spec docstring must reference ADR-0020 §4.5."
    assert "Nuitka" in spec_text, (
        "voice-typer.spec docstring must name Nuitka as the primary path "
        "(PyInstaller is the safety net, not the default)."
    )


def test_spec_targets_ipc_server_entry_point(spec_text: str):
    """ADR-0020 §4.5: \"The sidecar entrypoint is identical; only the freeze tool changes.\""""
    # The Analysis() call's first positional arg is the entry script.
    assert "ipc_server.py" in spec_text, (
        "voice-typer.spec must reference ipc_server.py as the entry point "
        "(ADR-0020 §4.5: sidecar entrypoint identical to Nuitka path)."
    )
    # Verify it's the voice_typer/server/ipc_server.py path specifically.
    assert (
        '"voice_typer" / "server" / "ipc_server.py"' in spec_text
        or "'voice_typer' / 'server' / 'ipc_server.py'" in spec_text
        or "voice_typer/server/ipc_server.py" in spec_text
    ), "voice-typer.spec must target voice_typer/server/ipc_server.py (the canonical sidecar entry point)."


def test_spec_includes_ctranslate2_hidden_import(spec_text: str):
    """The spec must list ``ctranslate2`` as a hidden import."""
    # Look for "ctranslate2" as a string literal in the hiddenimports list.
    assert '"ctranslate2"' in spec_text or "'ctranslate2'" in spec_text, (
        "voice-typer.spec _hiddenimports must include 'ctranslate2' "
        "(lazy-imported by faster_whisper; PyInstaller cannot auto-detect)."
    )


def test_spec_includes_faster_whisper_hidden_import(spec_text: str):
    """The spec must list ``faster_whisper`` as a hidden import."""
    assert '"faster_whisper"' in spec_text or "'faster_whisper'" in spec_text, (
        "voice-typer.spec _hiddenimports must include 'faster_whisper' "
        "(ADR-0020 §4.5 Phase 0 verify-load target; Nuitka siblings use "
        "--include-package=faster_whisper explicitly). BUILD-3 fix should have added it."
    )


def test_spec_uses_onefile_mode_not_onedir(spec_text: str):
    """The spec must use onefile mode, not onedir."""
    assert "COLLECT(" not in spec_text, (
        "voice-typer.spec must NOT use COLLECT(), that's onedir mode. "
        "Tauri externalBin requires a single executable per triple "
        "(onefile). See GAP-2."
    )
    assert "EXE(" in spec_text, "voice-typer.spec must define an EXE() call."
    assert "a.binaries" in spec_text, "voice-typer.spec EXE() must wrap a.binaries (onefile signature)."
    assert "a.datas" in spec_text, "voice-typer.spec EXE() must wrap a.datas (onefile signature)."
    # The spec docstring / comments should self-document onefile mode.
    assert "onefile" in spec_text.lower(), (
        "voice-typer.spec should mention 'onefile' in its docstring or comments (build mode self-documentation)."
    )


def test_spec_reads_tauri_sidecar_env_var(spec_text: str):
    """The spec must read ``VOICE_TYPER_TAURI_SIDECAR=1`` and switch build mode."""
    assert "VOICE_TYPER_TAURI_SIDECAR" in spec_text, (
        "voice-typer.spec must read VOICE_TYPER_TAURI_SIDECAR env var (ADR-0020 §4.5 Tauri sidecar mode switch)."
    )
    # The env var is read via os.environ.get(..., "") == "1".
    assert re.search(r'os\.environ\.get\s*\(\s*["\']VOICE_TYPER_TAURI_SIDECAR["\']', spec_text) is not None, (
        "voice-typer.spec must read VOICE_TYPER_TAURI_SIDECAR via os.environ.get(...) (idiomatic env var read)."
    )


def test_spec_tauri_mode_enables_console_for_ws_handshake(spec_text: str):
    """When VOICE_TYPER_TAURI_SIDECAR=1, console must be True (WS-mode handshake)."""
    # _CONSOLE = True in the Tauri sidecar branch.
    assert "_CONSOLE = True" in spec_text, (
        "voice-typer.spec must set _CONSOLE = True in the Tauri sidecar "
        "branch (Rust host reads server_started JSON from stdout)."
    )
    # The spec docstring must explain WHY console is on for Tauri.
    assert "server_started" in spec_text, (
        "voice-typer.spec docstring must mention server_started JSON "
        "(the WS-mode handshake payload the Rust host reads from stdout)."
    )


def test_spec_produces_per_triple_output_filename(spec_text: str):
    """The spec must emit ``python-sidecar-<triple>`` filenames matching Nuitka."""
    # The exe name is constructed as f"python-sidecar-{_TRIPLE}".
    assert 'f"python-sidecar-{_TRIPLE}"' in spec_text or "python-sidecar-{_TRIPLE}" in spec_text, (
        "voice-typer.spec must construct the exe name as "
        "f'python-sidecar-{_TRIPLE}' (Tauri externalBin naming convention, "
        "matches Nuitka output)."
    )


def test_spec_triple_construction_matches_rust_target_triples(spec_text: str):
    """The spec's triple construction must mirror Rust target triples."""
    # Windows triple.
    assert "pc-windows-msvc" in spec_text, (
        "voice-typer.spec must construct the Windows Rust target triple (<arch>-pc-windows-msvc)."
    )
    # macOS triple.
    assert "apple-darwin" in spec_text, (
        "voice-typer.spec must construct the macOS Rust target triple (<arch>-apple-darwin)."
    )
    # Linux triple.
    assert "unknown-linux-gnu" in spec_text, (
        "voice-typer.spec must construct the Linux Rust target triple (<arch>-unknown-linux-gnu)."
    )


def test_spec_bundles_ct2_native_libs_via_hidden_import(spec_text: str):
    """The spec must bundle CT2 native libs (transitively via hidden imports)."""
    # CT2 must be in hidden imports (already asserted above, but repeat
    assert '"ctranslate2"' in spec_text or "'ctranslate2'" in spec_text, (
        "voice-typer.spec must list 'ctranslate2' as a hidden import so "
        "PyInstaller's hook-ctranslate2.py bundles the native libs "
        "(libctranslate2.{so,dylib,dll} + OpenMP/DNNL)."
    )
    # CT2 must NOT be in the excludes= list (defensive, a future edit
    excludes_block = _extract_excludes_block(spec_text)
    assert "ctranslate2" not in excludes_block, (
        "voice-typer.spec must NOT list 'ctranslate2' in excludes=, "
        "that would strip the CT2 native libs from the bundle."
    )


def test_spec_does_not_exclude_faster_whisper(spec_text: str):
    """The spec must NOT exclude ``faster_whisper`` (or ctranslate2) in excludes=."""
    excludes_block = _extract_excludes_block(spec_text)
    assert "faster_whisper" not in excludes_block, (
        "voice-typer.spec must NOT list 'faster_whisper' in excludes=, that would strip the ASR engine from the bundle."
    )


def test_known_gap_ct2_native_libs_not_explicitly_listed(spec_text: str):
    """GAP-3 (documented): CT2 native libs are NOT explicitly in binaries=."""
    binaries_block = _extract_binaries_block(spec_text)
    has_explicit_ct2 = any(
        token in binaries_block for token in ("libctranslate2", "ctranslate2.dll", "libdnnl", "libiomp")
    )
    if has_explicit_ct2:
        pytest.fail(
            "GAP-3 RESOLVED: CT2 native libs are now explicitly listed in "
            "binaries=. Delete this test (the gap is closed)."
        )
    # Gap still present, record it explicitly.
    assert not has_explicit_ct2, (
        "Invariant: if CT2 native libs are explicitly listed, the gap-test above should have failed already."
    )


def test_known_gap_env_var_not_read_at_runtime():
    """GAP-4 (documented): VOICE_TYPER_TAURI_SIDECAR is NOT read at Python runtime."""
    # Search the voice_typer/server/ Python runtime for the env var.
    server_dir = PROJECT_ROOT / "voice_typer" / "server"
    assert server_dir.is_dir(), f"missing server dir: {server_dir}"
    py_files = list(server_dir.rglob("*.py"))
    assert py_files, f"no .py files under {server_dir}"
    found_in_runtime = []
    for py_file in py_files:
        try:
            text = py_file.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if "VOICE_TYPER_TAURI_SIDECAR" in text:
            found_in_runtime.append(py_file.relative_to(PROJECT_ROOT))
    if found_in_runtime:
        pytest.fail(
            "GAP-4 RESOLVED: VOICE_TYPER_TAURI_SIDECAR is now read at "
            f"runtime in: {found_in_runtime}. Delete this test "
            "(the gap is closed)."
        )
    # Gap still present, record it explicitly.
    assert not found_in_runtime, (
        "Invariant: if the env var is read at runtime, the gap-test above should have failed already."
    )


def _extract_excludes_block(spec_text: str) -> str:
    """Extract the ``excludes=[...]`` list block from the spec text."""
    match = re.search(r"excludes\s*=\s*\[(.*?)\]", spec_text, re.DOTALL)
    return match.group(1) if match else ""


def _extract_binaries_block(spec_text: str) -> str:
    """Extract the ``binaries=...`` arg block from the spec text."""
    # _native_binaries = [...] block.
    nm_match = re.search(r"_native_binaries\s*=\s*\[(.*?)\]", spec_text, re.DOTALL)
    nm_block = nm_match.group(1) if nm_match else ""
    # Analysis(..., binaries=..., ...), capture the argument value.
    bin_match = re.search(r"binaries\s*=\s*([^\n,]+)", spec_text)
    bin_block = bin_match.group(1) if bin_match else ""
    return nm_block + "\n" + bin_block


if __name__ == "__main__":
    # Allow `python test_pyinstaller_fallback.py` for a quick smoke check
    sys.exit(pytest.main([__file__, "-v", "--no-cov"]))
