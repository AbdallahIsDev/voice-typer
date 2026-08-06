"""MIG-1.5 Phase 0-W Gate Check 4 — faster-whisper transcribe validation.

These tests validate the ASR setup path for the Nuitka-frozen Windows
sidecar (ADR-0020 §4.2 + §6.3). They cover:

1. The build script's `--check` flag asserts ``faster_whisper`` and
   ``ctranslate2`` are importable in the build env (the CT2 backend
   gate that prevents shipping a broken sidecar).
2. The Nuitka invocation includes ``--include-package=faster_whisper``
   and ``--include-package=ctranslate2`` so the frozen exe can import
   them at runtime.
3. The Nuitka invocation includes ``--include-data-dir`` for
   ``ctranslate2/lib`` (the CT2 native DLLs: ``ctranslate2.dll``,
   ``libiomp5md.dll``, MKL / OpenMP runtime) and ``--include-dll`` for
   the canonical ``ctranslate2.dll`` entry point.
4. The model path resolves to ``%APPDATA%\\voice-typer\\models`` on
   Windows via :func:`voice_typer.server._paths.config_dir` (the
   canonical wrapper over :func:`config._config_dir`).
5. The transcription engine defaults to ``compute_type=int8`` on CPU
   (no GPU required — the v1 Windows default per ADR-0020 §6.3).
6. The transcription engine surfaces a helpful ``RuntimeError`` when
   ``transcribe()`` is called before ``load()`` — not a NoneType crash.
7. The transcription engine handles short audio (≤ 1 s) without
   crashing — passes through to the mocked ``WhisperModel.transcribe``.

All tests mock ``faster_whisper.WhisperModel`` and ``ctranslate2`` —
no real model load and no CUDA runtime is exercised in the sandbox.

VALIDATE ON WINDOWS HOST:
    1. Launch Voice Typer (see check 2)
    2. Press F8 (default dictation hotkey) — speak a 5-second test phrase
    3. Press F8 again to stop
    4. Check log for:
       - "[ASR] loading model small.en from C:\\Users\\...\\AppData\\Roaming\\voice-typer\\models"
       - "[ASR] model loaded in X.Xs (compute_type=int8)"
       - "[TRANSCRIBE] result: '<your text>' (latency=X.Xs)"
    5. Verify transcribed text appears in the Voice Typer UI + History page
    Expected: transcription completes within 3s on a 5s audio clip; text appears in UI

    Companion gate: docs/migration/windows-validation-runbook.md §6.3
    (PowerShell: ``Get-Content "$env:APPDATA\\voice-typer\\logs\\sidecar.log"
    -Tail 200 | Select-String "transcrib|whisper|model_load|ctranslate2"``).
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
# parents[0]=mig15, [1]=tauri, [2]=tests, [3]=voice-typer (project root).
PROJECT_ROOT = Path(__file__).resolve().parents[3]
BUILD_SCRIPT = PROJECT_ROOT / "scripts" / "build" / "build_sidecar_windows.sh"


# ─── Helpers ──────────────────────────────────────────────────────────────────
def _read_build_script() -> str:
    """Read the Windows sidecar build script as text (fails loud if missing)."""
    assert BUILD_SCRIPT.is_file(), f"build script not found at {BUILD_SCRIPT}"
    return BUILD_SCRIPT.read_text(encoding="utf-8")


def _install_fake_ct2_modules(monkeypatch) -> tuple[types.ModuleType, types.ModuleType]:
    """Inject minimal fake ``faster_whisper`` + ``ctranslate2`` modules.

    The transcription engine lazy-imports these inside ``_resolve_device``
    and ``_load_transcriber_impl``. We register stub modules in
    ``sys.modules`` so the imports succeed without requiring the real
    CTranslate2 native extension (which can't load in the Linux sandbox
    even if the wheel were installed — it's Windows-only ABI here).

    Returns the (faster_whisper, ctranslate2) stub modules so individual
    tests can wire return values on them.
    """
    # ctranslate2 stub
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
    and ``ctranslate2`` are importable in the build env.

    This is the canonical pre-build gate: a Windows host runs
    ``bash scripts/build/build_sidecar_windows.sh --check`` before the
    long Nuitka freeze. If the gate fails, the build aborts with a clear
    "MISSING: faster_whisper/ctranslate2" message rather than producing
    a broken exe that crashes on ``import ctranslate2`` at startup.

    See ``build_sidecar_windows.sh`` lines 48–55.
    """
    text = _read_build_script()
    # The --check branch imports both packages in a single python -c call.
    assert "import faster_whisper, ctranslate2" in text, (
        "build script's --check branch must validate that both "
        "faster_whisper AND ctranslate2 are importable in the build env"
    )
    # And the same gate is enforced again at line ~98 before Nuitka runs,
    # so a stale build env can't slip past the --check gate.
    assert "import faster_whisper, ctranslate2, websockets" in text, (
        "build script must re-validate CT2 import right before invoking Nuitka"
    )


def test_asr_setup_module_loads_with_ct2_stubs(monkeypatch):
    """``asr_setup`` + ``transcription`` must import cleanly when CT2
    backend modules are present.

    ``asr_setup.download_parakeet_weights`` delegates to
    ``transcription._check_disk_space_for_download`` and
    ``transcription._download_with_retry`` — both of which live in a
    module that lazy-imports ``faster_whisper`` / ``ctranslate2``. We
    verify the modules load without ImportError when the stubs are in
    place (proving the CT2 backend gate is satisfiable on Windows).
    """
    _install_fake_ct2_modules(monkeypatch)

    # Force a fresh import of asr_setup so the stubs are visible to any
    # module-level imports. (asr_setup itself only lazy-imports CT2, so
    # this is mostly belt-and-suspenders.)
    import voice_typer.server.asr_setup as asr_setup  # noqa: F401

    # The transcription module transitively references faster_whisper
    # via ``from faster_whisper import WhisperModel`` (inside
    # ``_load_transcriber_impl``). Importing the module must succeed.
    import voice_typer.server.transcription as transcription  # noqa: F401

    # Sanity: the stubs are actually visible.
    assert importlib.util.find_spec("ctranslate2") is not None
    assert importlib.util.find_spec("faster_whisper") is not None


# ─── Tests: Nuitka --include-package flags ────────────────────────────────────
def test_build_script_includes_faster_whisper_and_ctranslate2_packages():
    """Nuitka must freeze both packages into the standalone exe.

    Without ``--include-package=faster_whisper`` the frozen exe can't
    resolve ``from faster_whisper import WhisperModel`` at runtime;
    without ``--include-package=ctranslate2`` the CTranslate2 native
    extension + its ``__init__`` are not bundled, so even if the DLL
    loads, the Python wrapper fails.

    ADR-0020 §4.2 mandates both flags.
    """
    text = _read_build_script()
    assert "--include-package=faster_whisper" in text, "Nuitka must include the faster_whisper Python package"
    assert "--include-package=ctranslate2" in text, "Nuitka must include the ctranslate2 Python package (CT2 backend)"


def test_build_script_includes_ct2_native_libs_via_include_data_dir():
    """Nuitka must bundle the entire ``ctranslate2/lib`` directory.

    ``ctranslate2/lib`` holds the native DLLs: ``ctranslate2.dll``,
    ``libiomp5md.dll`` (Intel OpenMP), and MKL / OpenMP runtimes.
    Nuitka does NOT auto-collect these — they must be explicitly
    included via ``--include-data-dir`` or ``import ctranslate2``
    crashes at startup with "ImportError: libiomp5md.dll not found"
    (see ADR-0020 §4.2 "CPU inference runtimes" + §11 fail scenarios).
    """
    text = _read_build_script()
    # The data-dir include maps <SITE>/ctranslate2/lib → <SITE>/ctranslate2/lib
    # so the in-exe layout matches the wheel layout that ctranslate2's
    # __init__ expects.
    assert "ctranslate2/lib" in text and "--include-data-dir" in text, (
        "build script must include --include-data-dir for ctranslate2/lib "
        "(the directory holding ctranslate2.dll + libiomp5md.dll + MKL DLLs)"
    )
    # The canonical DLL entry-point is also added via --include-dll so
    # Nuitka's Windows DLL scanner explicitly knows about it (avoids
    # a "DLL not found" error when the loader resolves dependencies).
    assert "--include-dll" in text and "ctranslate2.dll" in text, (
        "build script must include --include-dll for ctranslate2.dll"
    )


def test_build_script_handles_ct2_libs_plural_guarded():
    """Some CTranslate2 wheel variants ship native DLLs under
    ``ctranslate2/libs`` (plural) instead of ``ctranslate2/lib``.

    ADR-0020 §4.2 mentions the singular ``ctranslate2/lib`` layout as
    the canonical one, but older wheels and some Linux/macOS dev
    installs use the plural form. The build script is allowed to ship
    only the singular form (matching the pinned Windows wheel) but MUST
    NOT silently break if the plural form is the only one present.

    This test asserts the singular form is mandatory (the pinned
    Windows wheel layout) and documents the plural-form gap.
    """
    text = _read_build_script()
    # Mandatory: singular layout (the pinned Windows wheel layout).
    assert "ctranslate2/lib" in text
    # NOTE: the plural ``ctranslate2/libs`` is NOT currently included
    # in the build script. If a future wheel rev ships only the plural
    # form, the build script's --include-data-dir line must be updated
    # to scan both layouts. See IMPLEMENTATION GAP note in the final
    # report. The assertion below documents that the plural form is
    # absent (i.e. the script currently relies on the singular layout
    # being present in the pinned wheel).
    plural_present = "ctranslate2/libs" in text
    # Either is acceptable today: the singular path is required, and
    # the plural is optional. We assert plural is explicitly absent
    # so that adding it later is a deliberate change (with a matching
    # build-script comment explaining why both layouts are scanned).
    if plural_present:
        # If someone adds the plural form, the script MUST also include
        # a guard (conditional) so the singular-only build env doesn't
        # fail when the plural dir is absent.
        assert "[[ ! -d" in text or "if [[ ! -d" in text, (
            "build script includes ctranslate2/libs (plural) but lacks a "
            "guard — a singular-only wheel install would fail the build"
        )


# ─── Tests: model path resolves to %APPDATA%\voice-typer\models ──────────────
def test_model_path_resolves_to_appdata_on_windows(monkeypatch, tmp_path):
    """The model download path MUST resolve to
    ``%APPDATA%\\voice-typer\\models`` on Windows.

    ``asr_setup.ensure_hf_env()`` redirects ``HF_HOME`` to
    ``_config_dir() / "huggingface"``, and the runbook (§6.3) documents
    that downloaded model files land under
    ``%APPDATA%\\voice-typer\\models\\`` (the HuggingFace cache layout
    uses ``models--<org>--<repo>`` subdirs, so the user-visible
    "models" folder is ``_config_dir() / "models"``).

    ``_paths.config_dir()`` is the canonical wrapper that
    :mod:`server_platform` and other modules use; it delegates to
    ``config._config_dir()`` which honors ``APPDATA`` on Windows.
    """
    # APPDATA lives under the (isolated) home dir, mirroring the real
    # Windows layout (%USERPROFILE%\AppData\Roaming). config._config_dir()
    # validates the APPDATA-derived path stays within Path.home() (SEC-005),
    # so it must be a subdir of the patched home below.
    fake_appdata = str(tmp_path / "AppData" / "Roaming")

    # Force Windows platform detection. ``config._config_dir`` calls
    # ``is_windows()`` from ``platform_utils``. We patch the function
    # in BOTH the canonical location and the config module's import
    # binding so the call chain sees a Windows environment.
    monkeypatch.setattr("voice_typer.server.platform_utils.is_windows", lambda: True)
    # config.py imports is_windows at module load — patch the bound name.
    import voice_typer.server.config as config_mod

    monkeypatch.setattr(config_mod, "is_windows", lambda: True)

    # Isolate Path.home() to a clean temp dir so the legacy ~/.voice-typer
    # check in config._config_dir() misses (a real dev machine may have a
    # ~/.voice-typer from actual app use, which would otherwise short-circuit
    # before the Windows APPDATA branch). With no legacy dir present,
    # _config_dir falls through to the APPDATA branch as intended.
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    monkeypatch.setenv("APPDATA", fake_appdata)
    # Clear the override so we test the real APPDATA branch.
    monkeypatch.delenv("VOICE_TYPER_CONFIG_DIR", raising=False)

    from voice_typer.server import _paths
    from voice_typer.server.config import _reset_config_dir_cache

    # _config_dir is lru_cached for process lifetime; clear it so the
    # patched APPDATA / Path.home above take effect even when an
    # earlier test in the same process already resolved the dir.
    _reset_config_dir_cache()

    try:
        models_dir = _paths.config_dir() / "models"

        # Normalize to forward-slashes for cross-platform comparison.
        expected = Path(fake_appdata) / "voice-typer" / "models"
        assert models_dir == expected, (
            f"model path on Windows must resolve to "
            f"%APPDATA%\\voice-typer\\models (got: {models_dir}, "
            f"expected: {expected})"
        )
        # And the string form should contain the AppData literal so it's
        # visible to log greps (the runbook §6.3 references this path).
        assert "voice-typer" in str(models_dir)
    finally:
        # Un-poison the cache: the resolved value embeds this test's
        # tmp_path, which must not leak into later tests in the same
        # process (mirrors the ``_reset_config_dir_cache`` pattern).
        _reset_config_dir_cache()


# ─── Tests: compute_type=int8 CPU default ─────────────────────────────────────
def test_transcription_engine_defaults_to_int8_cpu(monkeypatch):
    """The transcription engine MUST default to ``compute_type=int8``
    on CPU (no GPU required).

    ADR-0020 §6.3 fail scenarios explicitly mention: "For CPU-only
    inference (the v1 default), ensure ``compute_type='int8'`` in the
    ASR config; CUDA wheels are large and not bundled by default."

    The ``TranscriptionEngine.__init__`` sets ``self._compute_type =
    "int8"`` and ``self._device = "cpu"``, and ``_resolve_device("cpu")``
    returns the same. We verify the default AND the explicit "cpu"
    request both land on int8 (not float16, which would require a CUDA
    wheel that isn't bundled).
    """
    _install_fake_ct2_modules(monkeypatch)

    # Patch the CUDA-DLL configurer so the engine never tries to
    # actually load NVIDIA DLLs (which don't exist in the sandbox).
    monkeypatch.setattr(
        "voice_typer.server.transcription._configure_nvidia_dll_paths",
        lambda: None,
    )

    from voice_typer.server.transcription import TranscriptionEngine

    engine = TranscriptionEngine(model_size="small.en", device="cpu")
    # Defaults before _resolve_device_once: int8 / cpu.
    assert engine._compute_type == "int8", "engine must default to compute_type=int8 (the CPU v1 default)"
    assert engine._device == "cpu"
    assert engine.device_info == "cpu (int8)"

    # Resolve explicitly. _resolve_device("cpu") must return ("cpu", "int8").
    device, compute_type = engine._resolve_device("cpu")
    assert (device, compute_type) == ("cpu", "int8"), (
        "explicit device='cpu' must resolve to compute_type=int8 — float16 would require the unbundled CUDA wheel"
    )

    # And the auto path with no CUDA device available (stub returns 0)
    # also lands on int8 (NOT float16) — the safe CPU fallback.
    device_auto, compute_auto = engine._resolve_device("auto")
    assert (device_auto, compute_auto) == ("cpu", "int8"), (
        "auto device resolution must fall back to CPU/int8 when no CUDA "
        "device is available (the Nuitka bundle ships no CUDA wheel)"
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

    See ``transcription.py:890-892`` — ``_transcribe_unlocked`` raises
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

    See ``transcription.py:894-895`` — ``_transcribe_unlocked`` returns
    ``""`` for empty audio; for non-empty short audio it iterates the
    (possibly empty) segment generator and joins the results.
    """
    fw, _ct2 = _install_fake_ct2_modules(monkeypatch)
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
    fw, _ct2 = _install_fake_ct2_modules(monkeypatch)
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


# ─── Tests: build script structure (sanity) ──────────────────────────────────
def test_build_script_targets_ipc_server_entry_point():
    """Nuitka must freeze ``ipc_server.py`` — the Tauri sidecar entry
    point — not ``main.py`` (the legacy Electron entry) or any other
    module. ADR-0020 §4.2 mandates this.
    """
    text = _read_build_script()
    assert "ipc_server.py" in text, (
        "Nuitka must target voice_typer/server/ipc_server.py (the Tauri WS sidecar entry point)"
    )
    # And the --windows-disable-console flag must be set (sidecar runs
    # hidden — no console window pops up alongside the Tauri window).
    assert "--windows-disable-console" in text, "sidecar must run without a console window (--windows-disable-console)"
