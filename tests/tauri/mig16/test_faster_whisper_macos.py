"""faster-whisper transcribe validation (macOS)."""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[3]
BUILD_SCRIPT = PROJECT_ROOT / "scripts" / "build" / "build_sidecar_macos.sh"


def _read_build_script() -> str:
    """Read the macOS sidecar build script as text (fails loud if missing)."""
    assert BUILD_SCRIPT.is_file(), f"build script not found at {BUILD_SCRIPT}"
    return BUILD_SCRIPT.read_text(encoding="utf-8")


def _install_fake_ct2_modules(monkeypatch) -> tuple[types.ModuleType, types.ModuleType]:
    """Inject minimal fake ``faster_whisper`` + ``ctranslate2`` modules."""
    ct2 = types.ModuleType("ctranslate2")
    ct2.__version__ = "4.0.0-test"
    ct2.get_cuda_device_count = MagicMock(return_value=0)
    ct2.__spec__ = importlib.util.spec_from_loader("ctranslate2", loader=None)

    fw = types.ModuleType("faster_whisper")
    fw.WhisperModel = MagicMock(name="WhisperModel")
    fw.__version__ = "1.0.0-test"
    fw.__spec__ = importlib.util.spec_from_loader("faster_whisper", loader=None)

    # Pre-existing real modules (if any) are saved by monkeypatch.setitem
    monkeypatch.setitem(sys.modules, "ctranslate2", ct2)
    monkeypatch.setitem(sys.modules, "faster_whisper", fw)
    return fw, ct2


def test_build_script_check_flag_validates_ct2_backend_importable():
    """The build script's ``--check`` flag must assert ``faster_whisper``"""
    text = _read_build_script()
    # The --check branch imports both packages in a single python -c call.
    assert "import faster_whisper, ctranslate2" in text, (
        "build script's --check branch must validate that both "
        "faster_whisper AND ctranslate2 are importable in the build env"
    )
    # And the same gate is enforced again right before Nuitka runs,
    assert "import faster_whisper, ctranslate2, websockets" in text, (
        "build script must re-validate CT2 import right before invoking Nuitka"
    )


def test_asr_setup_and_transcription_modules_load_with_ct2_stubs(monkeypatch):
    """``asr_setup`` + ``transcription`` must import cleanly when CT2"""
    _install_fake_ct2_modules(monkeypatch)

    # Force a fresh import of asr_setup so the stubs are visible to any
    import voice_typer.server.asr_setup  # noqa: F401

    # The transcription module transitively references faster_whisper
    import voice_typer.server.transcription  # noqa: F401

    # Sanity: the stubs are actually visible.
    assert importlib.util.find_spec("ctranslate2") is not None
    assert importlib.util.find_spec("faster_whisper") is not None


def test_build_script_includes_faster_whisper_and_ctranslate2_packages():
    """Nuitka must freeze both packages into the standalone binary."""
    text = _read_build_script()
    assert "--include-package=faster_whisper" in text, "Nuitka must include the faster_whisper Python package"
    assert "--include-package=ctranslate2" in text, "Nuitka must include the ctranslate2 Python package (CT2 backend)"


def test_build_script_includes_ct2_native_libs_singular_layout():
    """Nuitka must bundle the entire ``ctranslate2/lib`` directory"""
    text = _read_build_script()
    # The data-dir include maps <SITE>/ctranslate2/lib → <SITE>/ctranslate2/lib
    assert "ctranslate2/lib" in text and "--include-data-dir" in text, (
        "build script must include --include-data-dir for ctranslate2/lib "
        "(the directory holding libctranslate2.dylib + libiomp5.dylib)"
    )
    # The script also enforces the dir exists pre-build, without this
    assert 'CT2_LIB_DIR="$SITE/ctranslate2/lib"' in text, (
        "build script must resolve CT2_LIB_DIR from $SITE/ctranslate2/lib"
    )


def test_build_script_includes_ct2_libs_plural_layout_guarded():
    """The build script must also handle the plural ``ctranslate2/libs``"""
    text = _read_build_script()
    # The plural path is referenced + guarded.
    assert "ctranslate2/libs" in text, (
        "build script must reference the plural ctranslate2/libs path "
        "(some wheel variants ship dylibs there instead of ctranslate2/lib)"
    )
    # The guard: a conditional that only appends the plural data-dir
    assert 'CT2_LIBS_DIR="$SITE/ctranslate2/libs"' in text, (
        "build script must resolve CT2_LIBS_DIR from $SITE/ctranslate2/libs"
    )
    # The guard itself, either [[ -d ... ]] or if [[ -d ... ]].
    assert '[[ -d "$CT2_LIBS_DIR" ]]' in text or "[[ ! -d" in text, (
        "build script must guard the plural ctranslate2/libs include with "
        "a directory-existence check so singular-only installs don't break"
    )


@pytest.mark.real_config_dir  # asserts the REAL resolver (macOS branch); resolves paths only, never writes
def test_model_path_resolves_to_library_application_support_on_macos(monkeypatch):
    """``~/Library/Application Support/lausu/models`` on macOS."""
    # Force macOS platform detection. ``config._config_dir`` calls
    monkeypatch.setattr("voice_typer.server.platform_utils.is_macos", lambda: True)
    monkeypatch.setattr("voice_typer.server.platform_utils.is_windows", lambda: False)
    import voice_typer.server.config as config_mod

    monkeypatch.setattr(config_mod, "is_macos", lambda: True)
    monkeypatch.setattr(config_mod, "is_windows", lambda: False)

    # No legacy ~/.lausu dir in the sandbox (we don't create one),
    monkeypatch.delenv("VOICE_TYPER_CONFIG_DIR", raising=False)
    # Also clear XDG_DATA_HOME (a Linux-only env var, but defensive).
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)

    from voice_typer.server import _paths

    models_dir = _paths.config_dir() / "models"

    expected = Path.home() / "Library" / "Application Support" / "lausu" / "models"
    assert models_dir == expected, (
        f"model path on macOS must resolve to "
        f"~/Library/Application Support/lausu/models "
        f"(got: {models_dir}, expected: {expected})"
    )
    # And the string form should contain the macOS literals so it's
    s = str(models_dir)
    assert "Library" in s and "Application Support" in s and "lausu" in s, (
        f"macOS model path string must contain 'Library/Application Support/lausu' for log-grep visibility; got: {s!r}"
    )


def test_transcription_engine_defaults_to_int8_cpu_on_macos(monkeypatch):
    """The transcription engine MUST default to ``compute_type=int8``"""
    _install_fake_ct2_modules(monkeypatch)

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
        "explicit device='cpu' must resolve to compute_type=int8, "
        "float16 would require the unbundled CUDA wheel; 'metal'/'mps' "
        "is not a CT2 backend"
    )

    # And the auto path with no CUDA device available (stub returns 0)
    device_auto, compute_auto = engine._resolve_device("auto")
    assert (device_auto, compute_auto) == ("cpu", "int8"), (
        "auto device resolution must fall back to CPU/int8 when no CUDA "
        "device is available (the macOS Nuitka bundle ships no CUDA wheel; "
        "CT2 has no MPS backend)"
    )


def test_engine_does_not_consult_torch_backends_mps_for_device_resolution(monkeypatch):
    """CTranslate2 has NO MPS (Metal Performance Shaders) backend, so"""
    _install_fake_ct2_modules(monkeypatch)
    monkeypatch.setattr(
        "voice_typer.server.transcription._configure_nvidia_dll_paths",
        lambda: None,
    )

    # Inject a fake torch module whose mps.is_available() returns True.
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

    # MPS available or not, the engine MUST land on CPU/int8, CT2 has
    assert (device, compute_type) == ("cpu", "int8"), (
        "engine must NOT use MPS even when torch.backends.mps.is_available() "
        "is True, CT2 has no MPS backend; the engine resolves to CPU/int8 "
        f"(got: device={device!r}, compute_type={compute_type!r})"
    )
    # And the MPS probe was never called as part of device resolution
    assert not torch_mps.is_available.called, (
        "_resolve_device must not call torch.backends.mps.is_available(), "
        "CT2 has no MPS backend, so MPS detection is moot"
    )


def test_engine_surfaces_helpful_error_when_model_not_loaded(monkeypatch):
    """Calling ``transcribe()`` before ``load()`` MUST raise a helpful"""
    _install_fake_ct2_modules(monkeypatch)
    monkeypatch.setattr(
        "voice_typer.server.transcription._configure_nvidia_dll_paths",
        lambda: None,
    )

    from voice_typer.server.transcription import TranscriptionEngine

    engine = TranscriptionEngine(model_size="small.en", device="cpu")
    # Engine has NOT had load() called, _model is None.
    assert engine._model is None
    assert engine.is_loaded is False

    # 1 second of silence at 16 kHz (Whisper's expected sample rate).
    audio = np.zeros(16000, dtype=np.float32)

    with pytest.raises(RuntimeError) as exc_info:
        engine.transcribe(audio)

    msg = str(exc_info.value)
    # Must be a clear, actionable error, not "AttributeError: 'NoneType'
    assert "Model not loaded" in msg, f"expected helpful 'Model not loaded' error, got: {msg!r}"
    # The IPC layer greps for "load" in the error to decide which toast
    assert "load" in msg.lower(), (
        f"error message must reference load() so the IPC layer can route to the Models-page toast; got: {msg!r}"
    )


def test_engine_handles_short_audio_without_crashing(monkeypatch):
    """The engine MUST handle short audio (≤ 1 s) without crashing."""
    _install_fake_ct2_modules(monkeypatch)
    monkeypatch.setattr(
        "voice_typer.server.transcription._configure_nvidia_dll_paths",
        lambda: None,
    )

    from voice_typer.server.transcription import TranscriptionEngine

    engine = TranscriptionEngine(model_size="small.en", device="cpu")

    # Wire a fake model whose transcribe() returns an EMPTY segment
    fake_model = MagicMock(name="WhisperModel")
    fake_info = MagicMock(name="TranscriptionInfo")
    fake_info.language = "en"
    fake_info.language_probability = 1.0
    fake_model.transcribe.return_value = ([], fake_info)
    engine._model = fake_model
    engine._device = "cpu"
    engine._compute_type = "int8"

    # Exactly 1 second of audio at 16 kHz.
    short_audio = np.zeros(16000, dtype=np.float32)
    result = engine.transcribe(short_audio)

    # No segments → empty string. NOT a crash.
    assert result == "", f"short audio with no VAD segments must return empty string, not crash; got: {result!r}"

    assert fake_model.transcribe.called, (
        "engine must call model.transcribe() even on short audio, short-circuiting would hide VAD / model bugs"
    )
    call_args = fake_model.transcribe.call_args
    # First positional arg is the audio array.
    passed_audio = call_args.args[0]
    assert len(passed_audio) == 16000, (
        f"engine must pass the full 1s audio to model.transcribe; got {len(passed_audio)} samples"
    )


def test_engine_handles_short_audio_with_one_segment(monkeypatch):
    """segment (e.g. a single quick word). The segment iteration loop"""
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
    short_audio = np.ones(8000, dtype=np.float32) * 0.1
    result = engine.transcribe(short_audio)

    # The single segment's text was joined + stripped.
    assert result == "hi", f"engine must return the segment text for short audio; got: {result!r}"


def test_build_script_supports_both_arches_with_python_build_standalone():
    """``x86_64``) and resolve the Rust-style target triple"""
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
    """Nuitka must freeze ``ipc_server.py`` (the Tauri sidecar entry"""
    text = _read_build_script()
    assert "ipc_server.py" in text, (
        "Nuitka must target voice_typer/server/ipc_server.py (the Tauri WS sidecar entry point)"
    )
    assert "--macos-create-bundle" in text, "Nuitka must create a .app bundle (--macos-create-bundle)"
    assert "--macos-app-name=LausuSidecar" in text, (
        "Nuitka must set the sidecar .app name (--macos-app-name=LausuSidecar)"
    )
    assert "--macos-signed-app-name=com.Lausu.sidecar" in text, (
        "Nuitka must set the sidecar signed-app name "
        "(--macos-signed-app-name=com.Lausu.sidecar) for codesign + "
        "notarization continuity"
    )
    assert "--macos-app-mode=background" in text, (
        "sidecar must run in background mode (--macos-app-mode=background → LSUIElement=true → no Dock icon)"
    )
