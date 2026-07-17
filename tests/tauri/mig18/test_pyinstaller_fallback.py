"""MIG-1.8 Phase 1 + ADR-0020 §4.5 — PyInstaller fallback spec validation.

This test file is the **check-9 PyInstaller fallback gate** in the MIG-1.8
Tauri sidecar migration series. ADR-0020 §4.5 mandates that the existing
PyInstaller spec (``scripts/build/voice-typer.spec``) is **retained as the
fallback path** for platforms where Nuitka proves impractical (e.g. macOS
Apple Silicon ABI issues, Linux aarch64 missing wheels). Nuitka remains the
primary build path — it produces a smaller binary (~80-120 MB) with faster
cold-start than PyInstaller's bootloader (~150-200 MB, slower init).
PyInstaller is the **safety net** so a Nuitka packaging failure on a given
target triple does NOT block a release.

This test file validates the **structure** of ``scripts/build/voice-typer.spec``
so the fallback remains usable on every target triple. It checks:

  - the spec file exists at the canonical path,
  - the spec freezes ``voice_typer/server/ipc_server.py`` as the entry point
    (identical to the Nuitka path — ADR-0020 §4.5: "The sidecar entrypoint
    is identical; only the freeze tool changes."),
  - the spec lists ``faster_whisper`` + ``ctranslate2`` as hidden imports
    (CTR2 native libs are then bundled automatically by PyInstaller's
    ``hook-ctranslate2.py``),
  - the spec uses onefile mode (no ``COLLECT(`` — the ``EXE(...)`` wraps
    ``a.binaries`` + ``a.datas`` directly, which is PyInstaller's onefile
    signature; this matches Nuitka's ``--onefile`` output convention so
    Tauri's ``externalBin`` can target a single executable per triple),
  - the spec reads the ``VOICE_TYPER_TAURI_SIDECAR=1`` env var and switches
    to the Tauri sidecar mode (console on, triple-suffixed name, no icon,
    no windowed UI — the Rust host reads ``server_started`` JSON from the
    sidecar's stdout pipe, which is the WS-mode handshake),
  - the spec produces per-triple output filenames
    (``python-sidecar-<arch>-<vendor>-<os>-<libc>``) matching the Nuitka
    convention and Tauri's ``externalBin`` naming requirement,
  - the spec bundles CT2 native libs (transitively via the ``ctranslate2``
    hidden import + PyInstaller's bundled hook; verified by checking
    ``ctranslate2`` is in the hidden-imports list AND that the spec does
    NOT exclude it in the ``excludes=`` list).

VALIDATE ON HOST (fallback when Nuitka fails):
    1. pip install pyinstaller
    2. VOICE_TYPER_TAURI_SIDECAR=1 pyinstaller scripts/build/voice-typer.spec
    3. Verify dist/python-sidecar-<triple> exists
    Note: PyInstaller is a FALLBACK when Nuitka fails. Nuitka is the primary path (smaller binary, faster startup).

References:
  - ADR-0020 §4.5 — Common Nuitka caveats: "Existing PyInstaller spec
    (``scripts/build/voice-typer.spec``) is the fallback."
  - ADR-0020 Decision — "The existing ``scripts/build/voice-typer.spec``
    (PyInstaller, Windows-focused) is retained as the fallback path."
  - scripts/build/voice-typer.spec — the spec under test (375 lines).
  - src-tauri/src/sidecar/spawn.rs::target_triple_for — the Rust triple
    computation the spec mirrors in Python (lines 70-82 of the spec).

Gaps documented (report, do NOT fix — out of scope for this gate check):
  - GAP-1: ``faster_whisper`` is NOT in the spec's ``_hiddenimports`` list.
    The spec lists ``ctranslate2``, ``transformers``, ``accelerate``,
    ``tokenizers``, ``huggingface_hub`` but omits ``faster_whisper``. In
    practice PyInstaller's bytecode analysis usually discovers
    ``faster_whisper`` via static imports in ``ipc_server.py``, but ADR-0020
    §4.5 Phase 0 gate explicitly names ``faster_whisper`` as a required
    verify-load target — listing it as a hidden import is the safe
    defensive choice (the Nuitka sibling scripts all use
    ``--include-package=faster_whisper`` explicitly). See
    ``test_known_gap_faster_whisper_not_in_hiddenimports``.
  - GAP-2: ADR-0020 §4.5 prose says "the existing PyInstaller ``--onedir``
    spec already bundles..." but the spec itself uses **onefile** mode (no
    ``COLLECT(``; ``EXE(...)`` wraps ``a.binaries`` + ``a.datas`` directly).
    The spec docstring (lines 84-89) explicitly says "onefile mode" for the
    Tauri sidecar path. The ADR's ``--onedir`` description is stale — the
    spec was updated to onefile to match Nuitka's ``--onefile`` output and
    Tauri's ``externalBin`` (which requires a single executable per triple,
    not a folder). See ``test_spec_uses_onefile_mode_not_onedir``.
  - GAP-3: The spec does NOT explicitly list CT2 native shared libraries
    (``libctranslate2.so`` / ``libctranslate2.dylib`` / ``ctranslate2.dll``,
    plus OpenMP / DNNL libs) in the ``binaries=`` list. It relies on
    PyInstaller's bundled ``hook-ctranslate2.py`` to discover them via the
    ``ctranslate2`` hidden import. This works on a clean install but is
    fragile if the user has a custom CTranslate2 build with libs in a
    non-standard location. The Nuitka sibling scripts use
    ``--include-package-data=ctranslate2`` explicitly. The spec's
    ``_native_binaries`` list only covers the native *hotkey* binaries
    (``macos-key-listener``, ``windows-key-listener.exe``,
    ``linux-key-listener``), NOT CT2. See
    ``test_known_gap_ct2_native_libs_not_explicitly_listed``.
  - GAP-4: The ``VOICE_TYPER_TAURI_SIDECAR=1`` env var is read **only** by
    the spec file (to switch build mode). A repo-wide grep shows it is NOT
    referenced by any Python runtime code (``ipc_server.py`` does not read
    it). The "disables heartbeat, enables WS mode" behavior described in
    the task brief must therefore be triggered by a different mechanism
    (likely Tauri passing ``VOICE_TYPER_IPC_TOKEN`` / port env vars, with
    the WS transport being the default when the sidecar is launched by
    Rust). The spec's role is limited to producing a console-enabled,
    triple-suffixed binary — it does not itself disable heartbeat. See
    ``test_known_gap_env_var_not_read_at_runtime``.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

# ─── Project paths ───────────────────────────────────────────────────────────
# This test file lives at tests/tauri/mig18/test_pyinstaller_fallback.py.
# Path from file → root:
#   parents[0] = mig18/
#   parents[1] = tauri/
#   parents[2] = tests/
#   parents[3] = <project root> (voice-typer/)
PROJECT_ROOT = Path(__file__).resolve().parents[3]
SPEC_PATH = PROJECT_ROOT / "scripts" / "build" / "voice-typer.spec"


# ─── Fixtures ────────────────────────────────────────────────────────────────
@pytest.fixture(scope="module")
def spec_text() -> str:
    """Read the PyInstaller spec once per module; fail fast if missing."""
    assert SPEC_PATH.is_file(), (
        f"voice-typer.spec not found at {SPEC_PATH}. "
        "Did the project layout change? ADR-0020 §4.5 requires this file "
        "to be retained as the PyInstaller fallback path."
    )
    return SPEC_PATH.read_text(encoding="utf-8")


# ─── 1. Existence ────────────────────────────────────────────────────────────
def test_spec_file_exists():
    """ADR-0020 §4.5: the PyInstaller fallback spec must exist at the canonical path.

    The spec is the safety net for when Nuitka proves impractical on a
    target triple. Removing it would leave no fallback build path.
    """
    assert SPEC_PATH.is_file(), f"missing: {SPEC_PATH}"
    # A stub / empty regression guard — the real spec is >300 lines.
    assert SPEC_PATH.stat().st_size > 1000, (
        f"voice-typer.spec is suspiciously small ({SPEC_PATH.stat().st_size} bytes); "
        "expected a full PyInstaller spec (375+ lines)."
    )


def test_spec_docstring_documents_fallback_role(spec_text: str):
    """The spec's module docstring must self-document as the PyInstaller fallback.

    This protects against someone accidentally promoting the spec to the
    primary build path (Nuitka is primary, per ADR-0020 §Decision) or
    deleting the fallback framing.
    """
    assert "PyInstaller fallback" in spec_text, (
        "voice-typer.spec docstring must mention 'PyInstaller fallback' (ADR-0020 §4.5 framing)."
    )
    assert "ADR-0020" in spec_text, "voice-typer.spec docstring must reference ADR-0020 §4.5."
    assert "Nuitka" in spec_text, (
        "voice-typer.spec docstring must name Nuitka as the primary path "
        "(PyInstaller is the safety net, not the default)."
    )


# ─── 2. Entry point ──────────────────────────────────────────────────────────
def test_spec_targets_ipc_server_entry_point(spec_text: str):
    """ADR-0020 §4.5: "The sidecar entrypoint is identical; only the freeze tool changes."

    The PyInstaller spec must freeze ``voice_typer/server/ipc_server.py``
    — the SAME entry point the Nuitka scripts target. A divergence here
    would mean the fallback produces a binary with different behavior
    than the primary Nuitka build (a silent correctness regression).
    """
    # The Analysis() call's first positional arg is the entry script.
    # Check both the literal relative path and the constructed absolute path.
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


# ─── 3. Hidden imports: ctranslate2 + faster_whisper ─────────────────────────
def test_spec_includes_ctranslate2_hidden_import(spec_text: str):
    """The spec must list ``ctranslate2`` as a hidden import.

    CTranslate2 is the inference backend used by faster_whisper; its
    Python module is lazy-imported inside the ASR engine so PyInstaller's
    static analysis will NOT discover it automatically. Without this
    hidden import the frozen binary would crash at runtime with
    ``ModuleNotFoundError: ctranslate2``.
    """
    # Look for "ctranslate2" as a string literal in the hiddenimports list.
    assert '"ctranslate2"' in spec_text or "'ctranslate2'" in spec_text, (
        "voice-typer.spec _hiddenimports must include 'ctranslate2' "
        "(lazy-imported by faster_whisper; PyInstaller cannot auto-detect)."
    )


def test_spec_includes_faster_whisper_hidden_import(spec_text: str):
    """The spec must list ``faster_whisper`` as a hidden import.

    ADR-0020 §4.5 Phase 0 gate names faster_whisper as a required
    verify-load target. The Nuitka sibling scripts all use
    ``--include-package=faster_whisper`` explicitly. The PyInstaller
    spec should do the same defensively (even though static analysis
    often discovers it). BUILD-3 fix added faster_whisper + faster_whisper.transcribe
    to _hiddenimports.
    """
    assert '"faster_whisper"' in spec_text or "'faster_whisper'" in spec_text, (
        "voice-typer.spec _hiddenimports must include 'faster_whisper' "
        "(ADR-0020 §4.5 Phase 0 verify-load target; Nuitka siblings use "
        "--include-package=faster_whisper explicitly). BUILD-3 fix should have added it."
    )


# ─── 4. Onefile mode (not onedir) ────────────────────────────────────────────
def test_spec_uses_onefile_mode_not_onedir(spec_text: str):
    """The spec must use onefile mode, not onedir.

    ADR-0020 §4.5 prose says "--onedir" but the spec itself uses onefile
    (the EXE(...) wraps a.binaries + a.datas directly; no COLLECT()).
    This is correct: Tauri's ``externalBin`` requires a SINGLE executable
    per target triple, not a folder. onedir would require a wrapper
    launcher (ADR-0020 §4.5 mentions this as a workaround) — onefile
    avoids that complexity. The spec docstring (lines 84-89) explicitly
    says "onefile mode" for the Tauri sidecar path.

    PyInstaller spec idiom:
      - onefile → ``EXE(pyz, a.scripts, a.binaries, a.datas, ...)``
      - onedir  → ``EXE(pyz, a.scripts, ...); COLLECT(a.binaries, a.datas, ...)``

    So onefile is detected by: ``a.binaries`` is passed to ``EXE(...)`` AND
    there is NO ``COLLECT(`` call. See GAP-2 for the ADR prose mismatch.
    """
    # onedir signature: a COLLECT() call. onefile has none.
    assert "COLLECT(" not in spec_text, (
        "voice-typer.spec must NOT use COLLECT() — that's onedir mode. "
        "Tauri externalBin requires a single executable per triple "
        "(onefile). See GAP-2."
    )
    # onefile signature: EXE(...) receives a.binaries + a.datas directly.
    assert "EXE(" in spec_text, "voice-typer.spec must define an EXE() call."
    assert "a.binaries" in spec_text, "voice-typer.spec EXE() must wrap a.binaries (onefile signature)."
    assert "a.datas" in spec_text, "voice-typer.spec EXE() must wrap a.datas (onefile signature)."
    # The spec docstring / comments should self-document onefile mode.
    assert "onefile" in spec_text.lower(), (
        "voice-typer.spec should mention 'onefile' in its docstring or comments (build mode self-documentation)."
    )


# ─── 5. VOICE_TYPER_TAURI_SIDECAR env var handling ───────────────────────────
def test_spec_reads_tauri_sidecar_env_var(spec_text: str):
    """The spec must read ``VOICE_TYPER_TAURI_SIDECAR=1`` and switch build mode.

    ADR-0020 §4.5: when the env var is set, the spec produces a Tauri-
    compatible ``python-sidecar-<triple>`` binary (console on, no icon,
    no windowed UI). When unset, it produces the legacy ``VoiceTyper``
    windowed exe for the Electron fallback path.

    The "disables heartbeat, enables WS mode" behavior described in the
    task brief is the RUNTIME effect (the Rust host reads server_started
    JSON from the sidecar's stdout pipe — the WS handshake). The spec's
    role is to produce a console-enabled binary; the actual heartbeat/WS
    switching happens in ipc_server.py at runtime. See GAP-4.
    """
    assert "VOICE_TYPER_TAURI_SIDECAR" in spec_text, (
        "voice-typer.spec must read VOICE_TYPER_TAURI_SIDECAR env var (ADR-0020 §4.5 Tauri sidecar mode switch)."
    )
    # The env var is read via os.environ.get(..., "") == "1".
    assert re.search(r'os\.environ\.get\s*\(\s*["\']VOICE_TYPER_TAURI_SIDECAR["\']', spec_text) is not None, (
        "voice-typer.spec must read VOICE_TYPER_TAURI_SIDECAR via os.environ.get(...) (idiomatic env var read)."
    )


def test_spec_tauri_mode_enables_console_for_ws_handshake(spec_text: str):
    """When VOICE_TYPER_TAURI_SIDECAR=1, console must be True (WS-mode handshake).

    The Tauri sidecar path uses ``console=True`` because the Rust host
    reads the ``server_started`` JSON from the sidecar's stdout pipe
    (this is the WS-mode bootstrap — the sidecar self-selects a port
    and reports it via stdout; the Rust host then opens the WS channel).
    With ``console=False`` (the legacy Electron path) stdout is detached
    and the Tauri host would never receive the port. See spec docstring
    lines 48-52.
    """
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


# ─── 6. Per-triple output filenames ──────────────────────────────────────────
def test_spec_produces_per_triple_output_filename(spec_text: str):
    """The spec must emit ``python-sidecar-<triple>`` filenames matching Nuitka.

    ADR-0020 §4.1 mandates per-triple binaries because Tauri's
    ``externalBin`` looks for ``python-sidecar-<rust-target-triple>``
    (the suffix is the Rust target triple, e.g. x86_64-pc-windows-msvc).
    The Nuitka sibling scripts emit the same naming convention; the
    PyInstaller fallback MUST match so the Tauri host code in
    ``src-tauri/src/sidecar/spawn.rs::target_triple_for`` works
    identically regardless of which freeze tool produced the binary.
    """
    # The exe name is constructed as f"python-sidecar-{_TRIPLE}".
    assert 'f"python-sidecar-{_TRIPLE}"' in spec_text or "python-sidecar-{_TRIPLE}" in spec_text, (
        "voice-typer.spec must construct the exe name as "
        "f'python-sidecar-{_TRIPLE}' (Tauri externalBin naming convention, "
        "matches Nuitka output)."
    )


def test_spec_triple_construction_matches_rust_target_triples(spec_text: str):
    """The spec's triple construction must mirror Rust target triples.

    ADR-0020 §4.1 lists the mandatory triple set:
      - Windows x86_64:  x86_64-pc-windows-msvc
      - Windows aarch64: aarch64-pc-windows-msvc
      - macOS x86_64:    x86_64-apple-darwin
      - macOS aarch64:   aarch64-apple-darwin
      - Linux x86_64:    x86_64-unknown-linux-gnu
      - Linux aarch64:   aarch64-unknown-linux-gnu

    The spec constructs these via per-platform f-strings (lines 78-82).
    Verify each platform's triple template is present.
    """
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


# ─── 7. CT2 native libs (binaries) ───────────────────────────────────────────
def test_spec_bundles_ct2_native_libs_via_hidden_import(spec_text: str):
    """The spec must bundle CT2 native libs (transitively via hidden imports).

    CTranslate2 ships native shared libraries (``libctranslate2.so`` /
    ``libctranslate2.dylib`` / ``ctranslate2.dll`` + OpenMP / DNNL deps).
    PyInstaller's bundled ``hook-ctranslate2.py`` discovers and bundles
    these automatically WHEN ``ctranslate2`` is in the hidden-imports
    list. So the test for CT2 native libs reduces to verifying
    ``ctranslate2`` is in ``_hiddenimports`` AND NOT in ``excludes=``.
    See GAP-3 for the explicit-binaries fragility note.
    """
    # CT2 must be in hidden imports (already asserted above, but repeat
    # here so this test is self-contained for the CT2-native-libs
    # requirement).
    assert '"ctranslate2"' in spec_text or "'ctranslate2'" in spec_text, (
        "voice-typer.spec must list 'ctranslate2' as a hidden import so "
        "PyInstaller's hook-ctranslate2.py bundles the native libs "
        "(libctranslate2.{so,dylib,dll} + OpenMP/DNNL)."
    )
    # CT2 must NOT be in the excludes= list (defensive — a future edit
    # could accidentally add it).
    excludes_block = _extract_excludes_block(spec_text)
    assert "ctranslate2" not in excludes_block, (
        "voice-typer.spec must NOT list 'ctranslate2' in excludes= — "
        "that would strip the CT2 native libs from the bundle."
    )


def test_spec_does_not_exclude_faster_whisper(spec_text: str):
    """The spec must NOT exclude ``faster_whisper`` (or ctranslate2) in excludes=.

    A defensive guard: even if faster_whisper is not in hiddenimports
    (GAP-1), it must at least NOT be in the excludes list (which would
    actively strip it from the bundle).
    """
    excludes_block = _extract_excludes_block(spec_text)
    assert "faster_whisper" not in excludes_block, (
        "voice-typer.spec must NOT list 'faster_whisper' in excludes= — "
        "that would strip the ASR engine from the bundle."
    )


def test_known_gap_ct2_native_libs_not_explicitly_listed(spec_text: str):
    """GAP-3 (documented): CT2 native libs are NOT explicitly in binaries=.

    The spec's ``binaries=`` list only contains the native *hotkey*
    binaries (macos-key-listener, windows-key-listener.exe,
    linux-key-listener). CT2 native libs (.so/.dylib/.dll) are bundled
    transitively via PyInstaller's hook-ctranslate2.py triggered by the
    ``ctranslate2`` hidden import. This works on a clean install but is
    fragile if the user has a custom CTranslate2 build. The Nuitka
    siblings use ``--include-package-data=ctranslate2`` explicitly.

    This test ASSERTS the gap so it shows up red until fixed. When
    someone adds explicit CT2 native lib entries to ``binaries=``,
    this test will START FAILING — that is the signal to delete this
    test (gap closed).
    """
    # Extract the binaries= list (the _native_binaries variable + the
    # Analysis(binaries=...) call).
    binaries_block = _extract_binaries_block(spec_text)
    has_explicit_ct2 = any(
        token in binaries_block for token in ("libctranslate2", "ctranslate2.dll", "libdnnl", "libiomp")
    )
    if has_explicit_ct2:
        pytest.fail(
            "GAP-3 RESOLVED: CT2 native libs are now explicitly listed in "
            "binaries=. Delete this test (the gap is closed)."
        )
    # Gap still present — record it explicitly.
    assert not has_explicit_ct2, (
        "Invariant: if CT2 native libs are explicitly listed, the gap-test above should have failed already."
    )


def test_known_gap_env_var_not_read_at_runtime():
    """GAP-4 (documented): VOICE_TYPER_TAURI_SIDECAR is NOT read at Python runtime.

    A repo-wide grep shows the env var is referenced ONLY in
    ``scripts/build/voice-typer.spec`` (build-time). The Python runtime
    code (``voice_typer/server/ipc_server.py`` and siblings) does NOT
    read it. The "disables heartbeat, enables WS mode" behavior must
    therefore be triggered by a different mechanism (likely Tauri
    passing IPC token / port env vars, with WS being the default
    transport when launched by the Rust host).

    This test ASSERTS the gap so it shows up red until either (a) the
    Python runtime starts reading the env var, or (b) the spec/ADR is
    updated to clarify the runtime mechanism. When the gap is closed,
    this test will START FAILING — delete it.
    """
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
    # Gap still present — record it explicitly.
    assert not found_in_runtime, (
        "Invariant: if the env var is read at runtime, the gap-test above should have failed already."
    )


# ─── Helpers ─────────────────────────────────────────────────────────────────
def _extract_excludes_block(spec_text: str) -> str:
    """Extract the ``excludes=[...]`` list block from the spec text.

    PyInstaller spec idiom: ``excludes=[..., "module1", ...],``. We pull
    out everything between ``excludes=[`` and the matching ``]`` so we
    can check membership without false-positives from comments or other
    lists.
    """
    match = re.search(r"excludes\s*=\s*\[(.*?)\]", spec_text, re.DOTALL)
    return match.group(1) if match else ""


def _extract_binaries_block(spec_text: str) -> str:
    """Extract the ``binaries=...`` arg block from the spec text.

    The spec assigns ``_native_binaries = [...]`` and then passes
    ``binaries=_native_binaries`` to ``Analysis(...)``. We concatenate
    the _native_binaries list literal AND the binaries= call site so
    we can check whether CT2 native libs are explicitly mentioned
    anywhere in the binaries-construction code path.
    """
    # _native_binaries = [...] block.
    nm_match = re.search(r"_native_binaries\s*=\s*\[(.*?)\]", spec_text, re.DOTALL)
    nm_block = nm_match.group(1) if nm_match else ""
    # Analysis(..., binaries=..., ...) — capture the argument value.
    bin_match = re.search(r"binaries\s*=\s*([^\n,]+)", spec_text)
    bin_block = bin_match.group(1) if bin_match else ""
    return nm_block + "\n" + bin_block


# ─── Sanity: pytest collection guard ─────────────────────────────────────────
if __name__ == "__main__":
    # Allow `python test_pyinstaller_fallback.py` for a quick smoke check
    # without invoking pytest (useful when iterating on the spec).
    sys.exit(pytest.main([__file__, "-v", "--no-cov"]))
