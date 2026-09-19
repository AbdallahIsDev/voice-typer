"""faster-whisper transcribe validation (Linux)."""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
import pytest

# seen in the baseline. No-op when xdist isn't active. (C-TEST-5: test isolation.)
pytestmark = pytest.mark.xdist_group("faster_whisper_linux")

PROJECT_ROOT = Path(__file__).resolve().parents[3]
BUILD_SCRIPT = PROJECT_ROOT / "scripts" / "build" / "build_sidecar_linux.sh"


def _read_build_script() -> str:
    """Read the Linux sidecar build script as text (fails loud if missing)."""
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


def test_build_script_pre_nuitka_check_validates_ct2_backend_importable():
    """presence of ``faster_whisper`` + ``ctranslate2`` in the build env's"""
    text = _read_build_script()
    # The site-packages directory existence check for faster_whisper.
    assert '"$SITE/faster_whisper"' in text, (
        "build script must verify $SITE/faster_whisper exists before "
        "invoking Nuitka (the cross-platform CT2 backend importability gate)"
    )
    # The site-packages directory existence check for ctranslate2.
    assert '"$SITE/ctranslate2"' in text, (
        "build script must verify $SITE/ctranslate2 exists before "
        "invoking Nuitka (the cross-platform CT2 backend importability gate)"
    )
    # The error message + pip install hint (so a stale python-build-standalone
    assert "pip install faster-whisper ctranslate2 websockets numpy" in text, (
        "build script must print a clear pip install hint when faster_whisper "
        "or ctranslate2 is missing from the build env's site-packages"
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
        "(the directory holding libctranslate2.so + libiomp5.so/libgomp.so)"
    )
    # The data-dir include is arch-agnostic, verbatim from the SITE path.
    assert '--include-data-dir="$SITE/ctranslate2/lib=$SITE/ctranslate2/lib"' in text, (
        "build script must include the verbatim --include-data-dir line for $SITE/ctranslate2/lib (singular layout)"
    )


def test_build_script_includes_ct2_libs_plural_layout_guarded():
    """The build script must also handle the plural ``ctranslate2/libs``"""
    text = _read_build_script()
    # The plural path is referenced + guarded.
    assert "ctranslate2/libs" in text, (
        "build script must reference the plural ctranslate2/libs path "
        "(some wheel variants ship .so files there instead of ctranslate2/lib)"
    )
    # The guard variable.
    assert 'CT2_LIBS_DIR="$SITE/ctranslate2/libs"' in text, (
        "build script must resolve CT2_LIBS_DIR from $SITE/ctranslate2/libs"
    )
    # The guard itself, a conditional that only appends the plural data-dir
    assert '[[ -d "$CT2_LIBS_DIR" ]]' in text, (
        "build script must guard the plural ctranslate2/libs include with "
        "a directory-existence check so singular-only installs (CPU-only "
        "aarch64 wheel) don't break the build"
    )


@pytest.mark.real_config_dir  # asserts the REAL resolver (XDG branch); resolves paths only, never writes
def test_model_path_resolves_to_xdg_data_home_on_linux(monkeypatch, tmp_path):
    """``~/.local/share/voice-typer/models`` on Linux."""
    # Force Linux platform detection. ``config._config_dir`` calls
    monkeypatch.setattr("voice_typer.server.platform_utils.is_macos", lambda: False)
    monkeypatch.setattr("voice_typer.server.platform_utils.is_windows", lambda: False)
    import voice_typer.server.config as config_mod

    monkeypatch.setattr(config_mod, "is_macos", lambda: False)
    monkeypatch.setattr(config_mod, "is_windows", lambda: False)

    # The real ``~/.voice-typer`` legacy dir may exist on developer
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    # Clear the override so we test the real Linux branch.
    monkeypatch.delenv("VOICE_TYPER_CONFIG_DIR", raising=False)
    # Also clear XDG_DATA_HOME so we test the default ~/.local/share path
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)

    from voice_typer.server.config_internals.paths import _reset_config_dir_cache

    _reset_config_dir_cache()

    from voice_typer.server import _paths

    models_dir = _paths.config_dir() / "models"

    expected = tmp_path / ".local" / "share" / "voice-typer" / "models"
    assert models_dir == expected, (
        f"model path on Linux must resolve to "
        f"~/.local/share/voice-typer/models "
        f"(got: {models_dir}, expected: {expected})"
    )
    # And the string form should contain the Linux literals so it's
    s = str(models_dir)
    assert ".local" in s and "share" in s and "voice-typer" in s, (
        f"Linux model path string must contain '.local/share/voice-typer' for log-grep visibility; got: {s!r}"
    )


def test_transcription_engine_defaults_to_int8_cpu_on_linux(monkeypatch):
    """The transcription engine MUST default to ``compute_type=int8``"""
    _install_fake_ct2_modules(monkeypatch)

    monkeypatch.setattr(
        "voice_typer.server.transcription._configure_nvidia_dll_paths",
        lambda: None,
    )

    from voice_typer.server.transcription import TranscriptionEngine

    engine = TranscriptionEngine(model_size="small.en", device="cpu")
    # Defaults before _resolve_device_once: int8 / cpu.
    assert engine._compute_type == "int8", "engine must default to compute_type=int8 (the Linux CPU default)"
    assert engine._device == "cpu"
    assert engine.device_info == "cpu (int8)"

    # Resolve explicitly. _resolve_device("cpu") must return ("cpu", "int8").
    device, compute_type = engine._resolve_device("cpu")
    assert (device, compute_type) == ("cpu", "int8"), (
        "explicit device='cpu' must resolve to compute_type=int8, "
        "float16 would require the unbundled CUDA wheel; CT2 has no MPS "
        "backend on Linux"
    )

    # And the auto path with no CUDA device available (stub returns 0)
    device_auto, compute_auto = engine._resolve_device("auto")
    assert (device_auto, compute_auto) == ("cpu", "int8"), (
        "auto device resolution must fall back to CPU/int8 when no CUDA "
        "device is available (the Linux Nuitka bundle ships no CUDA wheel; "
        "CT2 has no MPS backend)"
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
        "build script must document the cpython-3.12.x python-build-standalone naming convention (per ADR-0020 §4.4)"
    )

    # 2. Both arches are accepted (the case statement validates ARCH).
    assert "aarch64" in text and "x86_64" in text, (
        "build script must accept both aarch64 (ARM 64) and x86_64 (Intel/AMD) as the ARCH argument"
    )

    # 3. The Rust-style target triple is constructed from ARCH.
    assert 'TRIPLE="${ARCH}-unknown-linux-gnu"' in text, (
        "build script must construct the Rust-style target triple "
        "'${ARCH}-unknown-linux-gnu' so the output binary is named "
        "'python-sidecar-<arch>-unknown-linux-gnu'"
    )
    # And the output filename uses the triple (Tauri externalBin resolver).
    assert '"python-sidecar-$TRIPLE"' in text, (
        "build script must name the output binary 'python-sidecar-${TRIPLE}' "
        "so Tauri's externalBin resolver finds the per-arch binary"
    )

    # 4. The per-arch interpreter is selected via $VOICE_TYPER_PYBS_DIR
    assert "VOICE_TYPER_PYBS_DIR" in text, (
        "build script must honor $VOICE_TYPER_PYBS_DIR so the CI workflow "
        "can pass the per-arch python-build-standalone install"
    )
    # The auto-discovery glob cpython-3.12.*+<triple>/python/bin/python3
    assert '$PYBS_DIR"/cpython-3.12.*+"$TRIPLE"/python/bin/python3' in text or ('cpython-3.12.*+"$TRIPLE"' in text), (
        "build script must auto-discover the per-arch python-build-standalone "
        "interpreter via cpython-3.12.*+${TRIPLE}/python/bin/python3 (the "
        "verbose-layout install), selects x86_64 vs aarch64 install based "
        "on the ARCH argument"
    )
    # 5. Cross-build requires qemu-user-static (aarch64 on x86_64 host).
    assert "qemu-aarch64-static" in text, (
        "build script must require qemu-aarch64-static for cross-builds "
        "(aarch64 target on x86_64 host per ADR-0020 §4.4)"
    )


def test_build_script_bundles_openmp_runtime_libs():
    """The Linux Nuitka bundle MUST include the OpenMP runtime .so files"""
    text = _read_build_script()

    # 1. The header comment documents the OpenMP runtime .so files.
    assert "libiomp5.so" in text and "libgomp.so" in text, (
        "build script header must document that the ctranslate2/{lib,libs} "
        "include ships libiomp5.so (Intel OpenMP, x86_64) + libgomp.so "
        "(GNU OpenMP, aarch64), these are the OpenMP runtime .so files "
        "CT2's CPU inference path requires"
    )

    assert '--include-data-dir="$SITE/ctranslate2/lib=$SITE/ctranslate2/lib"' in text, (
        "build script must include --include-data-dir for $SITE/ctranslate2/lib "
        "(transitively ships libiomp5.so / libgomp.so alongside "
        "libctranslate2.so)"
    )

    assert 'CT2_LIBS_DIR="$SITE/ctranslate2/libs"' in text, (
        "build script must reference CT2_LIBS_DIR (the plural layout), "
        "some wheel variants ship the OpenMP runtime under "
        "ctranslate2/libs instead of ctranslate2/lib"
    )
    assert '[[ -d "$CT2_LIBS_DIR" ]]' in text, (
        "build script must guard the plural ctranslate2/libs include so "
        "the build doesn't fail on wheel installs that ship only the "
        "singular ctranslate2/lib layout"
    )
