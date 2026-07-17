"""MIG-1.6 Phase 0-M Gate Check 4 — faster-whisper transcribe validation (macOS).

These tests validate the ASR setup path for the Nuitka-frozen macOS
sidecar (ADR-0020 §4.3 + §6.2 "faster-whisper transcribes inside the
Nuitka bundle" gate point 3). They cover BOTH macOS arches:

  - ``aarch64-apple-darwin``  (Apple Silicon: M1/M2/M3/M4 — runs CT2 CPU mode)
  - ``x86_64-apple-darwin``   (Intel — runs CT2 CPU mode)

Coverage map (each item below maps to a test function):

1. The build script's ``--check`` flag asserts ``faster_whisper`` and
   ``ctranslate2`` are importable in the build env (the cross-platform
   CT2 backend gate that prevents shipping a broken sidecar).
2. ``asr_setup`` + ``transcription`` modules import cleanly when CT2
   stubs are in ``sys.modules`` (proves the backend gate is satisfiable
   on macOS without the real native extension).
3. The Nuitka invocation includes ``--include-package=faster_whisper``
   and ``--include-package=ctranslate2`` so the frozen binary can import
   them at runtime.
4. The Nuitka invocation includes ``--include-data-dir`` for
   ``ctranslate2/lib`` (the CT2 native dylibs: ``libctranslate2.dylib``,
   ``libiomp5.dylib`` — OpenMP runtime). This is the singular-layout
   directory mandated by ADR-0020 §4.3 for the pinned macOS wheel.
5. The build script also handles the plural ``ctranslate2/libs`` layout
   via a guarded conditional (``if [[ -d ... ]]``) so a wheel variant
   shipping only the plural form doesn't break the build.
6. The model path resolves to ``~/Library/Application Support/voice-typer/models``
   on macOS via :func:`voice_typer.server._paths.config_dir` (the
   canonical wrapper over :func:`config._config_dir`).
7. The transcription engine defaults to ``compute_type=int8`` on macOS
   (CT2 CPU mode — no MPS by default). ADR-0020 §6.2 fail scenarios:
   "CTranslate2 aarch64 wheels are CPU-only (no CUDA on macOS)".
8. The engine does NOT consult ``torch.backends.mps.is_available()`` for
   device resolution — CT2 has no MPS backend, so MPS detection is moot.
   The engine uses ``ctranslate2.get_cuda_device_count()`` (returns 0 on
   macOS) and falls back to CPU/int8.
9. The engine surfaces a helpful ``RuntimeError`` when ``transcribe()``
   is called before ``load()`` — not a NoneType crash.
10. The engine handles short audio (≤ 1 s) without crashing — both the
    empty-segment (VAD found no speech) and single-segment paths.
11. The build script accepts an ``ARCH`` argument (``aarch64`` or
    ``x86_64``) and resolves the Rust-style target triple
    (``<arch>-apple-darwin``) so the per-arch python-build-standalone
    install (``cpython-3.12.x+<arch>-apple-darwin``) is selected.

All tests mock ``faster_whisper.WhisperModel`` and ``ctranslate2`` —
no real model load and no CT2 native extension is exercised in the
Linux sandbox (the macOS dylib ABI can't load here).

VALIDATE ON MACOS HOST:
    1. Launch Voice Typer
    2. Press F8 — speak a 5-second test phrase
    3. Press F8 again to stop
    4. Check ~/Library/Logs/voice-typer/voice-typer.log for:
       - "[ASR] loading model small.en from ~/Library/Application Support/voice-typer/models"
       - "[ASR] model loaded in X.Xs (compute_type=int8)"
       - "[TRANSCRIBE] result: '<your text>' (latency=X.Xs)"
    5. Verify transcribed text appears in UI + History page
    Expected: transcription completes within 3s on both Intel + Apple Silicon
    (Apple Silicon may be faster due to M-series CPU efficiency, but CT2 uses CPU mode by default.)

    Companion gate: docs/migration/macos-validation-runbook.md §6.2
    (gate point 3 — faster-whisper transcribes inside the Nuitka bundle, BOTH arches).
    Run on BOTH arches:
      - Apple Silicon host:  scripts/build/build_sidecar_macos.sh aarch64
      - Intel host:          scripts/build/build_sidecar_macos.sh x86_64
      - Apple Silicon building x86_64 requires Rosetta 2:
        softwareupdate --install-rosetta --agree-to-license
    Log-tail companion command (per runbook §6.2):
      tail -f "$HOME/Library/Application Support/voice-typer/logs/voice-typer.log"
"""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
import pytest

# ─── Project root + build-script path ─────────────────────────────────────────
# parents[0]=mig16, [1]=tauri, [2]=tests, [3]=voice-typer (project root).
PROJECT_ROOT = Path(__file__).resolve().parents[3]
BUILD_SCRIPT = PROJECT_ROOT / "scripts" / "build" / "build_sidecar_macos.sh"


# ─── Helpers ──────────────────────────────────────────────────────────────────
def _read_build_script() -> str:
    """Read the macOS sidecar build script as text (fails loud if missing)."""
    assert BUILD_SCRIPT.is_file(), f"build script not found at {BUILD_SCRIPT}"
    return BUILD_SCRIPT.read_text(encoding="utf-8")


def _install_fake_ct2_modules(monkeypatch) -> tuple[types.ModuleType, types.ModuleType]:
    """Inject minimal fake ``faster_whisper`` + ``ctranslate2`` modules.

    The transcription engine lazy-imports these inside ``_resolve_device``
    and ``_load_transcriber_impl``. We register stub modules in
    ``sys.modules`` so the imports succeed without requiring the real
    CTranslate2 native extension (which can't load in the Linux sandbox
    even if the wheel were installed — it's macOS-only ABI here).

    Returns the (faster_whisper, ctranslate2) stub modules so individual
    tests can wire return values on them.
    """
    # ctranslate2 stub — get_cuda_device_count() returns 0 to model the
    # macOS environment (no CUDA on macOS wheels; CT2 falls back to CPU).
    ct2 = types.ModuleType("ctranslate2")
    ct2.__version__ = "4.0.0-test"
    ct2.get_cuda_device_count = MagicMock(return_value=0)
    # find_spec() on a sys.modules entry returns the module's __spec__
    # attribute; set a minimal spec so importlib.util.find_spec() returns
    # a non-None value (some production code uses find_spec to gate
    # lazy imports).
    ct2.__spec__ = importlib.util.spec_from_loader("ctranslate2", loader=None)

    # faster_whisper stub with a WhisperModel attribute
    fw = types.ModuleType("faster_whisper")
    fw.WhisperModel = MagicMock(name="WhisperModel")
    fw.__version__ = "1.0.0-test"
    fw.__spec__ = importlib.util.spec_from_loader("faster_whisper", loader=None)

    # Pre-existing real modules (if any) are saved by monkeypatch.setitem
    # and restored on teardown.
    monkeypatch.setitem(sys.modules, "ctranslate2", ct2)
    monkeypatch.setitem(sys.modules, "faster_whisper", fw)
    return fw, ct2


# ─── Tests: CT2 backend importability gate ────────────────────────────────────
def test_build_script_check_flag_validates_ct2_backend_importable():
    """The build script's ``--check`` flag must assert ``faster_whisper``
    and ``ctranslate2`` are importable in the build env (cross-platform).

    This is the canonical pre-build gate: a macOS host runs
    ``bash scripts/build/build_sidecar_macos.sh --check`` before the
    long Nuitka freeze. If the gate fails, the build aborts with a clear
    "MISSING: faster_whisper/ctranslate2" message rather than producing
    a broken binary that crashes on ``import ctranslate2`` at startup.

    See ``build_sidecar_macos.sh`` line ~49 (the ``--check`` branch) and
    line ~101 (the pre-Nuitka re-validation).
    """
    text = _read_build_script()
    # The --check branch imports both packages in a single python -c call.
    assert "import faster_whisper, ctranslate2" in text, (
        "build script's --check branch must validate that both "
        "faster_whisper AND ctranslate2 are importable in the build env"
    )
    # And the same gate is enforced again right before Nuitka runs,
    # so a stale build env can't slip past the --check gate.
    assert "import faster_whisper, ctranslate2, websockets" in text, (
        "build script must re-validate CT2 import right before invoking Nuitka"
    )


def test_asr_setup_and_transcription_modules_load_with_ct2_stubs(monkeypatch):
    """``asr_setup`` + ``transcription`` must import cleanly when CT2
    backend modules are present (cross-platform).

    ``asr_setup.download_parakeet_weights`` delegates to
    ``transcription._check_disk_space_for_download`` and
    ``transcription._download_with_retry`` — both of which live in a
    module that lazy-imports ``faster_whisper`` / ``ctranslate2``. We
    verify the modules load without ImportError when the stubs are in
    place (proving the CT2 backend gate is satisfiable on macOS).
    """
    _install_fake_ct2_modules(monkeypatch)

    # Force a fresh import of asr_setup so the stubs are visible to any
    # module-level imports. (asr_setup itself only lazy-imports CT2, so
    # this is mostly belt-and-suspenders.)
    import voice_typer.server.asr_setup  # noqa: F401

    # The transcription module transitively references faster_whisper
    # via ``from faster_whisper import WhisperModel`` (inside
    # ``_load_transcriber_impl``). Importing the module must succeed.
    import voice_typer.server.transcription  # noqa: F401

    # Sanity: the stubs are actually visible.
    assert importlib.util.find_spec("ctranslate2") is not None
    assert importlib.util.find_spec("faster_whisper") is not None


# ─── Tests: Nuitka --include-package flags ────────────────────────────────────
def test_build_script_includes_faster_whisper_and_ctranslate2_packages():
    """Nuitka must freeze both packages into the standalone binary.

    Without ``--include-package=faster_whisper`` the frozen binary can't
    resolve ``from faster_whisper import WhisperModel`` at runtime;
    without ``--include-package=ctranslate2`` the CTranslate2 native
    extension + its ``__init__`` are not bundled, so even if the dylib
    loads, the Python wrapper fails.

    ADR-0020 §4.3 mandates both flags (verbatim).
    """
    text = _read_build_script()
    assert "--include-package=faster_whisper" in text, "Nuitka must include the faster_whisper Python package"
    assert "--include-package=ctranslate2" in text, "Nuitka must include the ctranslate2 Python package (CT2 backend)"


# ─── Tests: CT2 native libs for BOTH archs ────────────────────────────────────
def test_build_script_includes_ct2_native_libs_singular_layout():
    """Nuitka must bundle the entire ``ctranslate2/lib`` directory
    (singular layout — the pinned macOS wheel layout).

    ``ctranslate2/lib`` holds the native dylibs: ``libctranslate2.dylib``
    + ``libiomp5.dylib`` (Intel OpenMP) — both required by CT2's CPU
    inference path on macOS. Nuitka does NOT auto-collect these — they
    must be explicitly included via ``--include-data-dir`` or
    ``import ctranslate2`` crashes at startup with
    "dyld: Library not loaded: @rpath/libctranslate2.dylib"
    (see ADR-0020 §4.3 + runbook §1 fail scenarios).

    This flag is arch-agnostic — the same line ships the dylibs for
    both ``aarch64`` (Apple Silicon) and ``x86_64`` (Intel) because the
    build script is invoked once per arch with the matching
    python-build-standalone install.
    """
    text = _read_build_script()
    # The data-dir include maps <SITE>/ctranslate2/lib → <SITE>/ctranslate2/lib
    # so the in-binary layout matches the wheel layout that ctranslate2's
    # __init__ expects.
    assert "ctranslate2/lib" in text and "--include-data-dir" in text, (
        "build script must include --include-data-dir for ctranslate2/lib "
        "(the directory holding libctranslate2.dylib + libiomp5.dylib)"
    )
    # The script also enforces the dir exists pre-build — without this
    # check, a stale python-build-standalone install would silently
    # produce a broken binary.
    assert 'CT2_LIB_DIR="$SITE/ctranslate2/lib"' in text, (
        "build script must resolve CT2_LIB_DIR from $SITE/ctranslate2/lib"
    )


def test_build_script_includes_ct2_libs_plural_layout_guarded():
    """The build script must also handle the plural ``ctranslate2/libs``
    layout — guarded by a directory-existence check.

    ADR-0020 §4.3 mentions the singular ``ctranslate2/lib`` layout as
    the canonical one for the pinned macOS wheel, but some wheel
    variants ship native dylibs under ``ctranslate2/libs`` (plural)
    instead. The build script MUST NOT silently break if the plural
    form is the only one present — and it MUST NOT fail the build when
    the plural dir is absent (the singular-only wheel install case).

    The guarded conditional (``if [[ -d "$CT2_LIBS_DIR" ]]; then``)
    satisfies both: it appends the plural ``--include-data-dir`` only
    when the directory exists, so a singular-only install is unaffected.
    """
    text = _read_build_script()
    # The plural path is referenced + guarded.
    assert "ctranslate2/libs" in text, (
        "build script must reference the plural ctranslate2/libs path "
        "(some wheel variants ship dylibs there instead of ctranslate2/lib)"
    )
    # The guard: a conditional that only appends the plural data-dir
    # include if the directory exists. Without the guard, a singular-only
    # wheel install would fail the build (Nuitka errors on missing
    # --include-data-dir source).
    assert 'CT2_LIBS_DIR="$SITE/ctranslate2/libs"' in text, (
        "build script must resolve CT2_LIBS_DIR from $SITE/ctranslate2/libs"
    )
    # The guard itself — either [[ -d ... ]] or if [[ -d ... ]].
    assert '[[ -d "$CT2_LIBS_DIR" ]]' in text or "[[ ! -d" in text, (
        "build script must guard the plural ctranslate2/libs include with "
        "a directory-existence check so singular-only installs don't break"
    )


# ─── Tests: model path resolves to ~/Library/Application Support/voice-typer/models ──
def test_model_path_resolves_to_library_application_support_on_macos(monkeypatch):
    """The model download path MUST resolve to
    ``~/Library/Application Support/voice-typer/models`` on macOS.

    ``asr_setup.ensure_hf_env()`` redirects ``HF_HOME`` to
    ``_config_dir() / "huggingface"``, and the runbook (§6.2) documents
    that downloaded model files land under
    ``~/Library/Application Support/voice-typer/models/`` (the
    HuggingFace cache layout uses ``models--<org>--<repo>`` subdirs,
    so the user-visible "models" folder is ``_config_dir() / "models"``).

    ``_paths.config_dir()`` is the canonical wrapper that
    :mod:`server_platform` and other modules use; it delegates to
    ``config._config_dir()`` which honors the macOS branch
    (``Path.home() / "Library" / "Application Support" / "voice-typer"``)
    when ``is_macos()`` returns True.
    """
    # Force macOS platform detection. ``config._config_dir`` calls
    # ``is_macos()`` + ``is_windows()`` from ``platform_utils``. We
    # patch both functions in the canonical location AND the config
    # module's import bindings so the call chain sees a macOS env.
    monkeypatch.setattr("voice_typer.server.platform_utils.is_macos", lambda: True)
    monkeypatch.setattr("voice_typer.server.platform_utils.is_windows", lambda: False)
    # config.py imports is_macos + is_windows at module load — patch the
    # bound names so the already-imported references see the macOS env.
    import voice_typer.server.config as config_mod

    monkeypatch.setattr(config_mod, "is_macos", lambda: True)
    monkeypatch.setattr(config_mod, "is_windows", lambda: False)

    # No legacy ~/.voice-typer dir in the sandbox (we don't create one),
    # so _config_dir will fall through to the macOS branch.
    # Clear the override so we test the real macOS branch.
    monkeypatch.delenv("VOICE_TYPER_CONFIG_DIR", raising=False)
    # Also clear XDG_DATA_HOME (a Linux-only env var, but defensive).
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)

    from voice_typer.server import _paths

    models_dir = _paths.config_dir() / "models"

    expected = Path.home() / "Library" / "Application Support" / "voice-typer" / "models"
    assert models_dir == expected, (
        f"model path on macOS must resolve to "
        f"~/Library/Application Support/voice-typer/models "
        f"(got: {models_dir}, expected: {expected})"
    )
    # And the string form should contain the macOS literals so it's
    # visible to log greps (the runbook §6.2 references this path).
    s = str(models_dir)
    assert "Library" in s and "Application Support" in s and "voice-typer" in s, (
        f"macOS model path string must contain 'Library/Application Support/"
        f"voice-typer' for log-grep visibility; got: {s!r}"
    )


# ─── Tests: compute_type=int8 CPU default on macOS ────────────────────────────
def test_transcription_engine_defaults_to_int8_cpu_on_macos(monkeypatch):
    """The transcription engine MUST default to ``compute_type=int8``
    on macOS (CT2 CPU mode — no MPS by default).

    ADR-0020 §6.2 fail scenarios explicitly mention: "CTranslate2
    aarch64 wheels are CPU-only (no CUDA on macOS). Verify with
    ``otool -L $SITE/ctranslate2/lib/libctranslate2.dylib`` that every
    @rpath dependency resolves. Apple Silicon wheels ship
    ``libctranslate2.dylib`` + ``libiomp5.dylib`` (OpenMP) — no CUDA,
    no cuBLAS."

    The ``TranscriptionEngine.__init__`` sets ``self._compute_type =
    "int8"`` and ``self._device = "cpu"``, and ``_resolve_device("cpu")``
    returns the same. We verify the default AND the explicit "cpu"
    request both land on int8 (not float16, which would require a CUDA
    wheel that isn't bundled on macOS — and not "metal"/"mps", which
    CT2 doesn't support).
    """
    _install_fake_ct2_modules(monkeypatch)

    # Patch the CUDA-DLL configurer so the engine never tries to
    # actually load NVIDIA DLLs (which don't exist in the sandbox and
    # are irrelevant on macOS anyway).
    monkeypatch.setattr(
        "voice_typer.server.transcription._configure_nvidia_dll_paths",
        lambda: None,
    )

    from voice_typer.server.transcription import TranscriptionEngine

    engine = TranscriptionEngine(model_size="small.en", device="cpu")
    # Defaults before _resolve_device_once: int8 / cpu.
    assert engine._compute_type == "int8", "engine must default to compute_type=int8 (the macOS CPU default)"
    assert engine._device == "cpu"
    assert engine.device_info == "cpu (int8)"

    # Resolve explicitly. _resolve_device("cpu") must return ("cpu", "int8").
    device, compute_type = engine._resolve_device("cpu")
    assert (device, compute_type) == ("cpu", "int8"), (
        "explicit device='cpu' must resolve to compute_type=int8 — "
        "float16 would require the unbundled CUDA wheel; 'metal'/'mps' "
        "is not a CT2 backend"
    )

    # And the auto path with no CUDA device available (stub returns 0)
    # also lands on int8 (NOT float16, NOT mps) — the safe CPU fallback.
    # This models the macOS runtime: ctranslate2.get_cuda_device_count()
    # returns 0 on every macOS wheel (aarch64 + x86_64).
    device_auto, compute_auto = engine._resolve_device("auto")
    assert (device_auto, compute_auto) == ("cpu", "int8"), (
        "auto device resolution must fall back to CPU/int8 when no CUDA "
        "device is available (the macOS Nuitka bundle ships no CUDA wheel; "
        "CT2 has no MPS backend)"
    )


# ─── Tests: MPS availability is moot for CT2 ──────────────────────────────────
def test_engine_does_not_consult_torch_backends_mps_for_device_resolution(monkeypatch):
    """CTranslate2 has NO MPS (Metal Performance Shaders) backend — so
    ``torch.backends.mps.is_available()`` is irrelevant to the engine's
    device resolution.

    The engine's ``_resolve_device`` consults only:

      - ``ctranslate2.get_cuda_device_count()`` (returns 0 on macOS —
        no CUDA wheels shipped)
      - the requested ``device`` argument ("auto" | "cuda" | "cpu")

    Apple Silicon GPU acceleration via MPS is a PyTorch-only feature;
    faster-whisper (which wraps CT2) does not invoke it. So on macOS the
    engine ALWAYS lands on CPU/int8 regardless of whether
    ``torch.backends.mps.is_available()`` would return True on the host.

    This test enforces that contract by:
      1. Installing a fake ``torch.backends.mps.is_available()`` that
         returns True (to model an Apple Silicon host).
      2. Verifying the engine STILL resolves to CPU/int8 — proving
         MPS availability is not consulted.
    """
    _install_fake_ct2_modules(monkeypatch)
    monkeypatch.setattr(
        "voice_typer.server.transcription._configure_nvidia_dll_paths",
        lambda: None,
    )

    # Inject a fake torch module whose mps.is_available() returns True.
    # If the engine EVER consults this, the test would have to assert
    # the engine ignores it — which is what we're proving here.
    torch = types.ModuleType("torch")
    torch_backends = types.ModuleType("torch.backends")
    torch_mps = types.ModuleType("torch.backends.mps")
    torch_mps.is_available = MagicMock(return_value=True)
    torch_backends.mps = torch_mps
    torch.backends = torch_backends
    torch.cuda = MagicMock()
    torch.cuda.is_available = MagicMock(return_value=False)
    torch.cuda.empty_cache = MagicMock()
    torch.cuda.OutOfMemoryError = type("OutOfMemoryError", (RuntimeError,), {})
    monkeypatch.setitem(sys.modules, "torch", torch)
    monkeypatch.setitem(sys.modules, "torch.backends", torch_backends)
    monkeypatch.setitem(sys.modules, "torch.backends.mps", torch_mps)

    from voice_typer.server.transcription import TranscriptionEngine

    engine = TranscriptionEngine(model_size="small.en", device="auto")
    device, compute_type = engine._resolve_device("auto")

    # MPS available or not, the engine MUST land on CPU/int8 — CT2 has
    # no MPS backend, so MPS detection is moot.
    assert (device, compute_type) == ("cpu", "int8"), (
        "engine must NOT use MPS even when torch.backends.mps.is_available() "
        "is True — CT2 has no MPS backend; the engine resolves to CPU/int8 "
        f"(got: device={device!r}, compute_type={compute_type!r})"
    )
    # And the MPS probe was never called as part of device resolution
    # (it's only called by callers that explicitly want MPS — none do).
    # This is a negative-assertion guard: if a future refactor wires MPS
    # into _resolve_device, this assertion catches it before the macOS
    # host validation runs.
    assert not torch_mps.is_available.called, (
        "_resolve_device must not call torch.backends.mps.is_available() — "
        "CT2 has no MPS backend, so MPS detection is moot"
    )


# ─── Tests: missing-model graceful error ──────────────────────────────────────
def test_engine_surfaces_helpful_error_when_model_not_loaded(monkeypatch):
    """Calling ``transcribe()`` before ``load()`` MUST raise a helpful
    ``RuntimeError``, not a NoneType AttributeError crash.

    The frozen sidecar can reach this state if the model download
    fails or the user invokes dictation before the model finishes
    loading. A clear error message lets the IPC layer surface a toast
    ("Model not loaded — open Settings → Models to download") instead
    of a cryptic traceback.

    See ``transcription.py:_transcribe_unlocked`` — raises
    ``RuntimeError("Model not loaded. Call load() first.")``.
    """
    _install_fake_ct2_modules(monkeypatch)
    monkeypatch.setattr(
        "voice_typer.server.transcription._configure_nvidia_dll_paths",
        lambda: None,
    )

    from voice_typer.server.transcription import TranscriptionEngine

    engine = TranscriptionEngine(model_size="small.en", device="cpu")
    # Engine has NOT had load() called — _model is None.
    assert engine._model is None
    assert engine.is_loaded is False

    # 1 second of silence at 16 kHz (Whisper's expected sample rate).
    audio = np.zeros(16000, dtype=np.float32)

    with pytest.raises(RuntimeError) as exc_info:
        engine.transcribe(audio)

    msg = str(exc_info.value)
    # Must be a clear, actionable error — not "AttributeError: 'NoneType'
    # object has no attribute 'transcribe'".
    assert "Model not loaded" in msg, f"expected helpful 'Model not loaded' error, got: {msg!r}"
    # The IPC layer greps for "load" in the error to decide which toast
    # to show, so the message must mention loading.
    assert "load" in msg.lower(), (
        f"error message must reference load() so the IPC layer can route to the Models-page toast; got: {msg!r}"
    )


# ─── Tests: short audio (≤ 1s) doesn't crash ─────────────────────────────────
def test_engine_handles_short_audio_without_crashing(monkeypatch):
    """The engine MUST handle short audio (≤ 1 s) without crashing.

    VAD (voice activity detection) can produce zero segments on very
    short clips — especially when the user releases the hotkey quickly.
    The engine must return an empty string (no speech detected), not
    crash on an empty segment list or a duration-based assertion.

    See ``transcription.py:_transcribe_unlocked`` — returns ``""`` for
    empty audio; for non-empty short audio it iterates the (possibly
    empty) segment generator and joins the results.
    """
    _install_fake_ct2_modules(monkeypatch)
    monkeypatch.setattr(
        "voice_typer.server.transcription._configure_nvidia_dll_paths",
        lambda: None,
    )

    from voice_typer.server.transcription import TranscriptionEngine

    engine = TranscriptionEngine(model_size="small.en", device="cpu")

    # Wire a fake model whose transcribe() returns an EMPTY segment
    # list (VAD found no speech in the short clip). This is the most
    # common short-audio outcome.
    fake_model = MagicMock(name="WhisperModel")
    fake_info = MagicMock(name="TranscriptionInfo")
    fake_info.language = "en"
    fake_info.language_probability = 1.0
    # transcribe() returns a (segments_generator, info) tuple. We
    # return an empty list (iterable, finite) so the engine's
    # `for seg in segments` loop is a no-op.
    fake_model.transcribe.return_value = ([], fake_info)
    engine._model = fake_model
    engine._device = "cpu"
    engine._compute_type = "int8"

    # Exactly 1 second of audio at 16 kHz.
    short_audio = np.zeros(16000, dtype=np.float32)
    result = engine.transcribe(short_audio)

    # No segments → empty string. NOT a crash.
    assert result == "", f"short audio with no VAD segments must return empty string, not crash; got: {result!r}"

    # Verify the underlying model.transcribe was actually called with
    # the short audio (i.e. the engine didn't short-circuit before
    # the model call, which would hide a real bug).
    assert fake_model.transcribe.called, (
        "engine must call model.transcribe() even on short audio — short-circuiting would hide VAD / model bugs"
    )
    call_args = fake_model.transcribe.call_args
    # First positional arg is the audio array.
    passed_audio = call_args.args[0]
    assert len(passed_audio) == 16000, (
        f"engine must pass the full 1s audio to model.transcribe; got {len(passed_audio)} samples"
    )


def test_engine_handles_short_audio_with_one_segment(monkeypatch):
    """The engine MUST also handle short audio that DOES produce a
    segment (e.g. a single quick word). The segment iteration loop
    must not crash when the audio is ≤ 1 s.
    """
    _install_fake_ct2_modules(monkeypatch)
    monkeypatch.setattr(
        "voice_typer.server.transcription._configure_nvidia_dll_paths",
        lambda: None,
    )

    from voice_typer.server.transcription import TranscriptionEngine

    engine = TranscriptionEngine(model_size="small.en", device="cpu")

    # Build a fake segment with the attributes the engine reads.
    fake_segment = MagicMock(name="Segment")
    fake_segment.start = 0.0
    fake_segment.end = 0.5
    fake_segment.text = "hi"
    fake_segment.avg_logprob = -0.5
    fake_segment.no_speech_prob = 0.1

    fake_info = MagicMock(name="TranscriptionInfo")
    fake_info.language = "en"
    fake_info.language_probability = 0.95

    fake_model = MagicMock(name="WhisperModel")
    fake_model.transcribe.return_value = ([fake_segment], fake_info)
    engine._model = fake_model
    engine._device = "cpu"
    engine._compute_type = "int8"

    # 0.5 seconds of audio (well below the 1s threshold) with non-zero
    # amplitude so RMS > 0 (avoids the near-silence warning path).
    short_audio = np.ones(8000, dtype=np.float32) * 0.1
    result = engine.transcribe(short_audio)

    # The single segment's text was joined + stripped.
    assert result == "hi", f"engine must return the segment text for short audio; got: {result!r}"


# ─── Tests: build script per-arch python-build-standalone selection ───────────
def test_build_script_supports_both_arches_with_python_build_standalone():
    """The build script MUST accept an ``ARCH`` argument (``aarch64`` or
    ``x86_64``) and resolve the Rust-style target triple
    (``<arch>-apple-darwin``) so the per-arch python-build-standalone
    install (``cpython-3.12.x+<arch>-apple-darwin``) is selected.

    ADR-0020 §4.3 mandates:
      - ``python-build-standalone cpython-3.12.x+aarch64-apple-darwin``
        for Apple Silicon builds
      - ``python-build-standalone cpython-3.12.x+x86_64-apple-darwin``
        for Intel builds

    The build script doesn't hard-code the python-build-standalone URL —
    it accepts a ``$VOICE_TYPER_PYBS_DIR`` env var (set by CI to the
    extracted python-build-standalone install for the target arch) and
    falls back to ``$PYBS`` / ``python3`` for dev. The per-arch install
    is selected by the CI workflow running the script once per arch
    with the matching ``VOICE_TYPER_PYBS_DIR``.

    This test asserts the build script:
      1. Documents the python-build-standalone naming convention in its
         header comment.
      2. Accepts both ``aarch64`` and ``x86_64`` as the ``ARCH`` arg.
      3. Maps the arch to the Rust-style triple ``<arch>-apple-darwin``.
      4. Honors ``$VOICE_TYPER_PYBS_DIR`` for the per-arch interpreter.
    """
    text = _read_build_script()

    # 1. Header documents python-build-standalone + cpython-3.12.x naming.
    assert "python-build-standalone" in text, "build script must document the python-build-standalone toolchain"
    assert "cpython-3.12" in text, (
        "build script must document the cpython-3.12.x python-build-standalone naming convention (per ADR-0020 §4.3)"
    )

    # 2. Both arches are accepted (the case statement validates ARCH).
    assert "aarch64" in text and "x86_64" in text, (
        "build script must accept both aarch64 (Apple Silicon) and x86_64 (Intel) as the ARCH argument"
    )

    # 3. The Rust-style target triple is constructed from ARCH.
    assert 'TRIPLE="${ARCH}-apple-darwin"' in text, (
        "build script must construct the Rust-style target triple "
        "'${ARCH}-apple-darwin' so the output binary is named "
        "'python-sidecar-<arch>-apple-darwin'"
    )
    assert 'OUTPUT_NAME="python-sidecar-${TRIPLE}"' in text, (
        "build script must name the output binary 'python-sidecar-${TRIPLE}' "
        "so Tauri's externalBin resolver finds the per-arch binary"
    )

    # 4. The per-arch interpreter is selected via $VOICE_TYPER_PYBS_DIR.
    assert "VOICE_TYPER_PYBS_DIR" in text, (
        "build script must honor $VOICE_TYPER_PYBS_DIR so the CI workflow "
        "can pass the per-arch python-build-standalone install"
    )
    assert "$PYBS_DIR/python/bin/python3" in text, (
        "build script must resolve the interpreter from "
        "$VOICE_TYPER_PYBS_DIR/python/bin/python3 (the python-build-standalone "
        "install layout)"
    )


def test_build_script_targets_ipc_server_entry_point_and_macos_bundle_flags():
    """Nuitka must freeze ``ipc_server.py`` (the Tauri sidecar entry
    point, not ``main.py``) and set the macOS bundle flags mandated by
    ADR-0020 §4.3:
      - ``--macos-create-bundle`` (produce a .app bundle)
      - ``--macos-app-name=VoiceTyperSidecar``
      - ``--macos-signed-app-name=com.voicetyper.sidecar``
      - ``--macos-app-mode=background`` (LSUIElement=true — no Dock icon)
    """
    text = _read_build_script()
    assert "ipc_server.py" in text, (
        "Nuitka must target voice_typer/server/ipc_server.py (the Tauri WS sidecar entry point)"
    )
    assert "--macos-create-bundle" in text, "Nuitka must create a .app bundle (--macos-create-bundle)"
    assert "--macos-app-name=VoiceTyperSidecar" in text, (
        "Nuitka must set the sidecar .app name (--macos-app-name=VoiceTyperSidecar)"
    )
    assert "--macos-signed-app-name=com.voicetyper.sidecar" in text, (
        "Nuitka must set the sidecar signed-app name "
        "(--macos-signed-app-name=com.voicetyper.sidecar) for codesign + "
        "notarization continuity"
    )
    assert "--macos-app-mode=background" in text, (
        "sidecar must run in background mode (--macos-app-mode=background → LSUIElement=true → no Dock icon)"
    )
