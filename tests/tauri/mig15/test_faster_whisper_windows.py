"""faster-whisper transcribe validation."""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[3]
BUILD_SCRIPT = PROJECT_ROOT / "scripts" / "build" / "build_sidecar_windows.sh"


def _read_build_script() -> str:
    """Read the Windows sidecar build script as text (fails loud if missing)."""
    assert BUILD_SCRIPT.is_file(), f"build script not found at {BUILD_SCRIPT}"
    return BUILD_SCRIPT.read_text(encoding="utf-8")


def _install_fake_ct2_modules(monkeypatch) -> tuple[types.ModuleType, types.ModuleType]:
    """Inject minimal fake ``faster_whisper`` + ``ctranslate2`` modules."""
    # ctranslate2 stub
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
    # And the same gate is enforced again at line ~98 before Nuitka runs,
    assert "import faster_whisper, ctranslate2, websockets" in text, (
        "build script must re-validate CT2 import right before invoking Nuitka"
    )


def test_asr_setup_module_loads_with_ct2_stubs(monkeypatch):
    """``asr_setup`` + ``transcription`` must import cleanly when CT2"""
    _install_fake_ct2_modules(monkeypatch)

    # Force a fresh import of asr_setup so the stubs are visible to any
    import voice_typer.server.asr_setup as asr_setup  # noqa: F401

    # The transcription module transitively references faster_whisper
    import voice_typer.server.transcription as transcription  # noqa: F401

    # Sanity: the stubs are actually visible.
    assert importlib.util.find_spec("ctranslate2") is not None
    assert importlib.util.find_spec("faster_whisper") is not None


def test_build_script_includes_faster_whisper_and_ctranslate2_packages():
    """Nuitka must freeze both packages into the standalone exe."""
    text = _read_build_script()
    assert "--include-package=faster_whisper" in text, "Nuitka must include the faster_whisper Python package"
    assert "--include-package=ctranslate2" in text, "Nuitka must include the ctranslate2 Python package (CT2 backend)"


def test_build_script_includes_ct2_native_libs_via_include_data_dir():
    """Nuitka must bundle the entire ``ctranslate2/lib`` directory."""
    text = _read_build_script()
    # The data-dir include maps <SITE>/ctranslate2/lib → <SITE>/ctranslate2/lib
    assert "ctranslate2/lib" in text and "--include-data-dir" in text, (
        "build script must include --include-data-dir for ctranslate2/lib "
        "(the directory holding ctranslate2.dll + libiomp5md.dll + MKL DLLs)"
    )
    assert "--include-dll" in text and "ctranslate2.dll" in text, (
        "build script must include --include-dll for ctranslate2.dll"
    )


def test_build_script_handles_ct2_libs_plural_guarded():
    """Some CTranslate2 wheel variants ship native DLLs under"""
    text = _read_build_script()
    # Mandatory: singular layout (the pinned Windows wheel layout).
    assert "ctranslate2/lib" in text
    plural_present = "ctranslate2/libs" in text
    if plural_present:
        # If someone adds the plural form, the script MUST also include
        assert "[[ ! -d" in text or "if [[ ! -d" in text, (
            "build script includes ctranslate2/libs (plural) but lacks a "
            "guard, a singular-only wheel install would fail the build"
        )


@pytest.mark.real_config_dir  # asserts the REAL resolver (APPDATA branch); resolves paths only, never writes
def test_model_path_resolves_to_appdata_on_windows(monkeypatch, tmp_path):
    """``%APPDATA%\\lausu\\models`` on Windows."""
    # validates the APPDATA-derived path stays within Path.home() (SEC-005),
    fake_appdata = str(tmp_path / "AppData" / "Roaming")

    # Force Windows platform detection. ``config._config_dir`` calls
    monkeypatch.setattr("voice_typer.server.platform_utils.is_windows", lambda: True)
    import voice_typer.server.config as config_mod

    monkeypatch.setattr(config_mod, "is_windows", lambda: True)

    # Isolate Path.home() to a clean temp dir so the legacy ~/.lausu
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    monkeypatch.setenv("APPDATA", fake_appdata)
    # Clear the override so we test the real APPDATA branch.
    monkeypatch.delenv("VOICE_TYPER_CONFIG_DIR", raising=False)

    from voice_typer.server import _paths
    from voice_typer.server.config import _reset_config_dir_cache

    _reset_config_dir_cache()

    try:
        models_dir = _paths.config_dir() / "models"

        # Normalize to forward-slashes for cross-platform comparison.
        expected = Path(fake_appdata) / "lausu" / "models"
        assert models_dir == expected, (
            f"model path on Windows must resolve to %APPDATA%\\lausu\\models (got: {models_dir}, expected: {expected})"
        )
        # And the string form should contain the AppData literal so it's
        assert "lausu" in str(models_dir)
    finally:
        _reset_config_dir_cache()


def test_transcription_engine_defaults_to_int8_cpu(monkeypatch):
    """The transcription engine MUST default to ``compute_type=int8``"""
    _install_fake_ct2_modules(monkeypatch)

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
        "explicit device='cpu' must resolve to compute_type=int8, float16 would require the unbundled CUDA wheel"
    )

    # And the auto path with no CUDA device available (stub returns 0)
    device_auto, compute_auto = engine._resolve_device("auto")
    assert (device_auto, compute_auto) == ("cpu", "int8"), (
        "auto device resolution must fall back to CPU/int8 when no CUDA "
        "device is available (the Nuitka bundle ships no CUDA wheel)"
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
    fw, _ct2 = _install_fake_ct2_modules(monkeypatch)
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
    short_audio = np.ones(8000, dtype=np.float32) * 0.1
    result = engine.transcribe(short_audio)

    # The single segment's text was joined + stripped.
    assert result == "hi", f"engine must return the segment text for short audio; got: {result!r}"


def test_build_script_targets_ipc_server_entry_point():
    """Nuitka must freeze ``ipc_server.py``, the Tauri sidecar entry"""
    text = _read_build_script()
    assert "ipc_server.py" in text, (
        "Nuitka must target voice_typer/server/ipc_server.py (the Tauri WS sidecar entry point)"
    )
    # And the --windows-disable-console flag must be set (sidecar runs
    assert "--windows-disable-console" in text, "sidecar must run without a console window (--windows-disable-console)"
