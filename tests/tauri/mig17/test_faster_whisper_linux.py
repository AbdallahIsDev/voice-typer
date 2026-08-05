"""MIG-1.7 Phase 0-L Gate Check 4 — faster-whisper transcribe validation (Linux).

These tests validate the ASR setup path for the Nuitka-frozen Linux
sidecar (ADR-0020 §4.4 + §6.3 "faster-whisper transcribes inside the
Nuitka bundle" gate point 3). They cover BOTH Linux arches:

  - ``x86_64-unknown-linux-gnu``   (Intel / AMD — runs CT2 CPU mode, int8)
  - ``aarch64-unknown-linux-gnu``  (ARM 64 — runs CT2 CPU mode, int8)

Coverage map (each item below maps to a test function):

1. The Linux build script's pre-Nuitka directory check asserts
   ``$SITE/faster_whisper`` and ``$SITE/ctranslate2`` exist (the
   cross-platform CT2 backend gate that prevents shipping a broken
   sidecar). Together with asr_setup loading cleanly under CT2 stubs,
   this proves the CT2 backend is importable in the Linux build env.
2. ``asr_setup`` + ``transcription`` modules import cleanly when CT2
   stubs are in ``sys.modules`` (proves the backend gate is satisfiable
   on Linux without the real native extension).
3. The Nuitka invocation includes ``--include-package=faster_whisper``
   and ``--include-package=ctranslate2`` so the frozen binary can import
   them at runtime.
4. The Nuitka invocation includes ``--include-data-dir`` for
   ``ctranslate2/lib`` (the CT2 native .so files: ``libctranslate2.so``,
   ``libiomp5.so`` / ``libgomp.so`` — OpenMP runtime). This is the
   singular-layout directory mandated by ADR-0020 §4.4 for the pinned
   Linux wheel.
5. The build script also handles the plural ``ctranslate2/libs`` layout
   via a guarded conditional (``if [[ -d ... ]]``) so a wheel variant
   shipping only the plural form doesn't break the build — and so a
   CPU-only aarch64 wheel that ships no ``ctranslate2/libs/`` (per
   ADR-0020 §4.4 note) doesn't fail the build.
6. The model path resolves to ``~/.local/share/voice-typer/models``
   on Linux via :func:`voice_typer.server._paths.config_dir` (the
   canonical wrapper over :func:`config._config_dir`).
7. The transcription engine defaults to ``compute_type=int8`` on Linux
   (CT2 CPU mode — the ADR-0020 §4.4 + §6.3 default). On Linux both
   x86_64 and aarch64 ship CPU-only CT2 wheels (no CUDA wheel is
   bundled), so the safe fallback is always CPU/int8.
8. The engine surfaces a helpful ``RuntimeError`` when ``transcribe()``
   is called before ``load()`` — not a NoneType crash.
9. The engine handles short audio (≤ 1 s) without crashing — both the
   empty-segment (VAD found no speech) and single-segment paths.
10. The build script accepts an ``ARCH`` argument (``x86_64`` or
    ``aarch64``) and resolves the Rust-style target triple
    (``<arch>-unknown-linux-gnu``) so the per-arch python-build-standalone
    install (``cpython-3.12.x+<arch>-unknown-linux-gnu``) is selected,
    and so the output binary is named ``python-sidecar-<triple>``.
11. OpenMP runtimes (``libiomp5.so`` / ``libgomp.so``) are bundled —
    the build script documents this in its header comment and includes
    the ``ctranslate2/lib`` data dir which transitively ships the
    OpenMP runtime .so files alongside ``libctranslate2.so``.

All tests mock ``faster_whisper.WhisperModel`` and ``ctranslate2`` —
no real model load and no CT2 native extension is exercised here
(the CT2 .so ABI can't load in the sandbox without the matching wheel
install, and we don't want this gate to depend on a network download).

VALIDATE ON LINUX HOST:
    1. Launch Voice Typer
    2. Press F8 — speak a 5-second test phrase
    3. Press F8 again to stop
    4. Check ~/.local/share/voice-typer/logs/voice-typer.log for:
       - "[ASR] loading model small.en from ~/.local/share/voice-typer/models"
       - "[ASR] model loaded in X.Xs (compute_type=int8)"
       - "[TRANSCRIBE] result: '<your text>' (latency=X.Xs)"
    5. Verify transcribed text appears in UI + History page
    Expected: transcription completes within 3s on both x86_64 + aarch64

    Companion gate: docs/migration/linux-validation-runbook.md §6.3
    (gate point 3 — faster-whisper transcribes inside the Nuitka bundle,
    BOTH arches + BOTH X11 AND Wayland session types). The runbook's
    operational Step 7 is the documented manual procedure for this gate.
    Run on BOTH arches:
      - x86_64 host:   bash scripts/build/build_sidecar_linux.sh x86_64
      - aarch64 host:  bash scripts/build/build_sidecar_linux.sh aarch64
      - Cross-build aarch64 on x86_64 requires qemu-user-static:
        sudo apt-get install qemu-user-static binfmt-support
        sudo update-binfmts --enable qemu-aarch64
    Log-tail companion command (per runbook Step 7):
      tail -f ~/.local/share/voice-typer/logs/sidecar.log \\
        | grep -E "model_loaded|whisper|transcrib|ctranslate2"
    OpenMP runtime verification (per runbook §6.3 common failures):
      ldd src-tauri/bin/python-sidecar-<triple> | grep -E 'libiomp|libgomp'
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
# parents[0]=mig17, [1]=tauri, [2]=tests, [3]=voice-typer (project root).
PROJECT_ROOT = Path(__file__).resolve().parents[3]
BUILD_SCRIPT = PROJECT_ROOT / "scripts" / "build" / "build_sidecar_linux.sh"


# ─── Helpers ──────────────────────────────────────────────────────────────────
def _read_build_script() -> str:
    """Read the Linux sidecar build script as text (fails loud if missing)."""
    assert BUILD_SCRIPT.is_file(), f"build script not found at {BUILD_SCRIPT}"
    return BUILD_SCRIPT.read_text(encoding="utf-8")


def _install_fake_ct2_modules(monkeypatch) -> tuple[types.ModuleType, types.ModuleType]:
    """Inject minimal fake ``faster_whisper`` + ``ctranslate2`` modules.

    The transcription engine lazy-imports these inside ``_resolve_device``
    and ``_load_transcriber_impl``. We register stub modules in
    ``sys.modules`` so the imports succeed without requiring the real
    CTranslate2 native extension (which can't load in the Linux sandbox
    even if the wheel were installed for the wrong arch — the test host
    here may be either x86_64 or aarch64, and we don't want this gate
    to depend on a wheel install).

    Returns the (faster_whisper, ctranslate2) stub modules so individual
    tests can wire return values on them.
    """
    # ctranslate2 stub — get_cuda_device_count() returns 0 to model the
    # Linux default (no CUDA wheel bundled per ADR-0020 §4.4; CT2 falls
    # back to CPU/int8 on both x86_64 and aarch64).
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
def test_build_script_pre_nuitka_check_validates_ct2_backend_importable():
    """The Linux build script must gate the long Nuitka freeze on the
    presence of ``faster_whisper`` + ``ctranslate2`` in the build env's
    site-packages (cross-platform CT2 backend importability gate).

    The Linux build script doesn't have a separate ``--check`` flag like
    the macOS build script — instead, it asserts (lines ~133-146) that
    ``$SITE/faster_whisper`` AND ``$SITE/ctranslate2`` directories exist
    before invoking Nuitka. If either is missing, the script aborts with
    a clear error message + a `pip install faster-whisper ctranslate2
    websockets numpy` hint, rather than producing a broken binary that
    crashes on ``import ctranslate2`` at startup.

    See ``scripts/build/build_sidecar_linux.sh`` lines ~133-146.
    """
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
    # install surfaces a clear actionable error instead of a Nuitka traceback).
    assert "pip install faster-whisper ctranslate2 websockets numpy" in text, (
        "build script must print a clear pip install hint when faster_whisper "
        "or ctranslate2 is missing from the build env's site-packages"
    )


def test_asr_setup_and_transcription_modules_load_with_ct2_stubs(monkeypatch):
    """``asr_setup`` + ``transcription`` must import cleanly when CT2
    backend modules are present (cross-platform).

    ``asr_setup.download_parakeet_weights`` delegates to
    ``transcription._check_disk_space_for_download`` and
    ``transcription._download_with_retry`` — both of which live in a
    module that lazy-imports ``faster_whisper`` / ``ctranslate2``. We
    verify the modules load without ImportError when the stubs are in
    place (proving the CT2 backend gate is satisfiable on Linux without
    requiring the real native extension to load).
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
    extension + its ``__init__`` are not bundled, so even if the .so
    loads, the Python wrapper fails.

    ADR-0020 §4.4 mandates both flags (verbatim) for both Linux arches.
    """
    text = _read_build_script()
    assert "--include-package=faster_whisper" in text, "Nuitka must include the faster_whisper Python package"
    assert "--include-package=ctranslate2" in text, "Nuitka must include the ctranslate2 Python package (CT2 backend)"


# ─── Tests: CT2 native libs (both lib + libs layouts) ─────────────────────────
def test_build_script_includes_ct2_native_libs_singular_layout():
    """Nuitka must bundle the entire ``ctranslate2/lib`` directory
    (singular layout — the pinned Linux wheel layout for both arches).

    ``ctranslate2/lib`` holds the native .so files: ``libctranslate2.so``
    + ``libiomp5.so`` (Intel OpenMP) on x86_64, and
    ``libctranslate2.so`` + ``libgomp.so`` (GNU OpenMP) on aarch64 —
    both required by CT2's CPU inference path on Linux. Nuitka does NOT
    auto-collect these — they must be explicitly included via
    ``--include-data-dir`` or ``import ctranslate2`` crashes at startup
    with "libctranslate2.so: cannot open shared object file: No such
    file or directory" (see ADR-0020 §4.4 + runbook §6.3 fail scenarios).

    This flag is arch-agnostic — the same line ships the .so files for
    both ``x86_64`` (Intel/AMD) and ``aarch64`` (ARM 64) because the
    build script is invoked once per arch with the matching
    python-build-standalone install.
    """
    text = _read_build_script()
    # The data-dir include maps <SITE>/ctranslate2/lib → <SITE>/ctranslate2/lib
    # so the in-binary layout matches the wheel layout that ctranslate2's
    # __init__ expects.
    assert "ctranslate2/lib" in text and "--include-data-dir" in text, (
        "build script must include --include-data-dir for ctranslate2/lib "
        "(the directory holding libctranslate2.so + libiomp5.so/libgomp.so)"
    )
    # The data-dir include is arch-agnostic — verbatim from the SITE path.
    assert '--include-data-dir="$SITE/ctranslate2/lib=$SITE/ctranslate2/lib"' in text, (
        "build script must include the verbatim --include-data-dir line for $SITE/ctranslate2/lib (singular layout)"
    )


def test_build_script_includes_ct2_libs_plural_layout_guarded():
    """The build script must also handle the plural ``ctranslate2/libs``
    layout — guarded by a directory-existence check.

    ADR-0020 §4.4 mentions the singular ``ctranslate2/lib`` layout as
    the canonical one for the pinned Linux wheel, but some wheel
    variants ship native .so files under ``ctranslate2/libs`` (plural)
    instead. The build script MUST NOT silently break if the plural
    form is the only one present — and it MUST NOT fail the build when
    the plural dir is absent (the CPU-only aarch64 wheel install case,
    which ADR-0020 §4.4 explicitly notes ships libctranslate2.so +
    libiomp5.so under ``ctranslate2/lib/`` only, with no
    ``ctranslate2/libs/`` directory).

    The guarded conditional (``if [[ -d "$CT2_LIBS_DIR" ]]; then``)
    satisfies both: it appends the plural ``--include-data-dir`` only
    when the directory exists, so a singular-only install is unaffected.
    """
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
    # The guard itself — a conditional that only appends the plural data-dir
    # include if the directory exists. Without the guard, a singular-only
    # wheel install would fail the build (Nuitka errors on missing
    # --include-data-dir source).
    assert '[[ -d "$CT2_LIBS_DIR" ]]' in text, (
        "build script must guard the plural ctranslate2/libs include with "
        "a directory-existence check so singular-only installs (CPU-only "
        "aarch64 wheel) don't break the build"
    )


# ─── Tests: model path resolves to ~/.local/share/voice-typer/models ──────────
def test_model_path_resolves_to_xdg_data_home_on_linux(monkeypatch, tmp_path):
    """The model download path MUST resolve to
    ``~/.local/share/voice-typer/models`` on Linux.

    ``asr_setup.ensure_hf_env()`` redirects ``HF_HOME`` to
    ``_config_dir() / "huggingface"``, and the runbook (§6.3 + Step 7
    common failures) documents that downloaded model files land under
    ``~/.local/share/voice-typer/models/`` (the HuggingFace cache layout
    uses ``models--<org>--<repo>`` subdirs, so the user-visible "models"
    folder is ``_config_dir() / "models"``).

    ``_paths.config_dir()`` is the canonical wrapper that
    :mod:`server_platform` and other modules use; it delegates to
    ``config._config_dir()`` which honors the Linux branch
    (``$XDG_DATA_HOME/voice-typer``, falling back to
    ``~/.local/share/voice-typer``) when neither ``is_windows()`` nor
    ``is_macos()`` returns True.

    ADR-0020 §8 mandates this path for both Linux arches — the Tauri
    build writes to the same location the Electron build did, so the
    Rollback procedure preserves user data.
    """
    # Force Linux platform detection. ``config._config_dir`` calls
    # ``is_macos()`` + ``is_windows()`` from ``platform_utils``. We
    # patch both functions in the canonical location AND the config
    # module's import bindings so the call chain sees a Linux env.
    monkeypatch.setattr("voice_typer.server.platform_utils.is_macos", lambda: False)
    monkeypatch.setattr("voice_typer.server.platform_utils.is_windows", lambda: False)
    # config.py imports is_macos + is_windows at module load — patch the
    # bound names so the already-imported references see the Linux env.
    import voice_typer.server.config as config_mod

    monkeypatch.setattr(config_mod, "is_macos", lambda: False)
    monkeypatch.setattr(config_mod, "is_windows", lambda: False)

    # The real ``~/.voice-typer`` legacy dir may exist on developer
    # machines / sandboxes (other tests in this suite create it via
    # _config_dir() side effects). The production _config_dir() checks
    # for the legacy dir FIRST (migration path — existing users keep
    # their data where it is) and returns it if it exists, which would
    # short-circuit the Linux XDG branch we want to exercise here. Mock
    # ``Path.home()`` to a tmp_path that has no ``.voice-typer`` subdir
    # so _config_dir falls through to the Linux XDG branch.
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    # Clear the override so we test the real Linux branch.
    monkeypatch.delenv("VOICE_TYPER_CONFIG_DIR", raising=False)
    # Also clear XDG_DATA_HOME so we test the default ~/.local/share path
    # (the runbook §6.3 + ADR-0020 §8 default).
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)

    # _config_dir() is memoized via functools.lru_cache for the process
    # lifetime — clear the cache so the monkeypatched Path.home() +
    # env vars take effect on the next call.
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
    # visible to log greps (the runbook §6.3 references this path).
    s = str(models_dir)
    assert ".local" in s and "share" in s and "voice-typer" in s, (
        f"Linux model path string must contain '.local/share/voice-typer' for log-grep visibility; got: {s!r}"
    )


# ─── Tests: compute_type=int8 CPU default on Linux ────────────────────────────
def test_transcription_engine_defaults_to_int8_cpu_on_linux(monkeypatch):
    """The transcription engine MUST default to ``compute_type=int8``
    on Linux (CT2 CPU mode — no CUDA wheel is bundled per ADR-0020 §4.4).

    ADR-0020 §4.4 + §6.3 explicitly require CPU/int8 as the default on
    both x86_64 and aarch64 Linux. The Linux Nuitka bundle ships no
    CUDA wheel (no ``nvidia-*`` pip packages, no ``--include-package=torch``
    in ``build_sidecar_linux.sh``), so even if the host has an NVIDIA GPU,
    the frozen sidecar can't use it without the missing CUDA libs.

    The ``TranscriptionEngine.__init__`` sets ``self._compute_type =
    "int8"`` and ``self._device = "cpu"``, and ``_resolve_device("cpu")``
    returns the same. We verify the default AND the explicit "cpu"
    request both land on int8 (not float16, which would require the
    unbundled CUDA wheel — and not "metal"/"mps", which CT2 doesn't
    support and is macOS-only anyway).
    """
    _install_fake_ct2_modules(monkeypatch)

    # Patch the CUDA-DLL configurer so the engine never tries to
    # actually load NVIDIA DLLs (which don't exist in the sandbox and
    # are irrelevant on Linux CPU-only builds anyway).
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
        "explicit device='cpu' must resolve to compute_type=int8 — "
        "float16 would require the unbundled CUDA wheel; CT2 has no MPS "
        "backend on Linux"
    )

    # And the auto path with no CUDA device available (stub returns 0)
    # also lands on int8 (NOT float16) — the safe CPU fallback.
    # This models the Linux runtime: ctranslate2.get_cuda_device_count()
    # returns 0 on every Linux Nuitka bundle (no CUDA wheel shipped).
    device_auto, compute_auto = engine._resolve_device("auto")
    assert (device_auto, compute_auto) == ("cpu", "int8"), (
        "auto device resolution must fall back to CPU/int8 when no CUDA "
        "device is available (the Linux Nuitka bundle ships no CUDA wheel; "
        "CT2 has no MPS backend)"
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
    (``<arch>-unknown-linux-gnu``) so the per-arch python-build-standalone
    install (``cpython-3.12.x+<arch>-unknown-linux-gnu``) is selected,
    and so the output binary is named ``python-sidecar-<triple>`` (which
    Tauri's ``externalBin`` resolver expects per ADR-0020 §4.1).

    ADR-0020 §4.4 mandates:
      - ``python-build-standalone cpython-3.12.x+x86_64-unknown-linux-gnu``
        for Intel/AMD builds (glibc 2.35 baseline — Ubuntu 22.04+)
      - ``python-build-standalone cpython-3.12.x+aarch64-unknown-linux-gnu``
        for ARM 64 builds (glibc 2.35 baseline — runs on Pi 5, Ampere
        Altra, AWS Graviton, etc.)

    The build script doesn't hard-code the python-build-standalone URL —
    it accepts a ``$VOICE_TYPER_PYBS_DIR`` env var (set by CI to the
    extracted python-build-standalone install for the target arch) and
    auto-discovers the matching cpython-3.12.x+<triple> tree. The
    per-arch install is selected by the CI workflow running the script
    once per arch with the matching ``VOICE_TYPER_PYBS_DIR``.

    Cross-arch (aarch64 on x86_64 host) requires ``qemu-user-static`` +
    binfmt_misc; the script refuses to cross-build without
    ``qemu-aarch64-static`` available.
    """
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
    #    AND the auto-discovery glob matches the per-arch triple.
    assert "VOICE_TYPER_PYBS_DIR" in text, (
        "build script must honor $VOICE_TYPER_PYBS_DIR so the CI workflow "
        "can pass the per-arch python-build-standalone install"
    )
    # The auto-discovery glob cpython-3.12.*+<triple>/python/bin/python3
    # selects the per-arch interpreter from the python-build-standalone
    # verbose-layout tree (and $PYBS_DIR/python/bin/python3 for the
    # install_only layout).
    assert '$PYBS_DIR"/cpython-3.12.*+"$TRIPLE"/python/bin/python3' in text or ('cpython-3.12.*+"$TRIPLE"' in text), (
        "build script must auto-discover the per-arch python-build-standalone "
        "interpreter via cpython-3.12.*+${TRIPLE}/python/bin/python3 (the "
        "verbose-layout install) — selects x86_64 vs aarch64 install based "
        "on the ARCH argument"
    )
    # 5. Cross-build requires qemu-user-static (aarch64 on x86_64 host).
    assert "qemu-aarch64-static" in text, (
        "build script must require qemu-aarch64-static for cross-builds "
        "(aarch64 target on x86_64 host per ADR-0020 §4.4)"
    )


# ─── Tests: OpenMP runtime .so files are bundled ──────────────────────────────
def test_build_script_bundles_openmp_runtime_libs():
    """The Linux Nuitka bundle MUST include the OpenMP runtime .so files
    (``libiomp5.so`` on x86_64 Intel OpenMP, ``libgomp.so`` on aarch64
    GNU OpenMP) — without them, CTranslate2's CPU inference path fails
    to start with "libiomp5.so: cannot open shared object file" or
    "libgomp.so: cannot open shared object file".

    The build script does NOT include these .so files by name — they're
    transitively bundled via the ``--include-data-dir`` for
    ``$SITE/ctranslate2/lib`` (which is where the CT2 wheel ships both
    ``libctranslate2.so`` AND the OpenMP runtime .so files together).
    The plural ``ctranslate2/libs`` include (guarded) covers wheel
    variants that ship the .so files under that subdirectory instead.

    This test asserts:
      1. The build script's header comment documents that the CT2 lib
         includes ship ``libiomp5.so`` + ``libgomp.so`` (so a reader
         knows what's in the bundle without running `ldd`).
      2. The CT2 lib data-dir include is present (transitively ships
         the OpenMP runtime).
      3. The CT2 libs (plural) guarded include is present (covers the
         plural-layout wheel variant).

    Runbook §6.3 common failures explicitly mentions: "ctranslate2
    ImportError → The build env was missing libiomp5.so / libgomp.so.
    The build script's --include-data-dir=$SITE/ctranslate2/lib=...
    should pick these up; verify with
    ``ldd src-tauri/bin/python-sidecar-<triple> | grep -E 'libiomp|libgomp'``".
    """
    text = _read_build_script()

    # 1. The header comment documents the OpenMP runtime .so files.
    assert "libiomp5.so" in text and "libgomp.so" in text, (
        "build script header must document that the ctranslate2/{lib,libs} "
        "include ships libiomp5.so (Intel OpenMP, x86_64) + libgomp.so "
        "(GNU OpenMP, aarch64) — these are the OpenMP runtime .so files "
        "CT2's CPU inference path requires"
    )

    # 2. The CT2 lib data-dir include is present (transitively ships the
    #    OpenMP runtime alongside libctranslate2.so).
    assert '--include-data-dir="$SITE/ctranslate2/lib=$SITE/ctranslate2/lib"' in text, (
        "build script must include --include-data-dir for $SITE/ctranslate2/lib "
        "(transitively ships libiomp5.so / libgomp.so alongside "
        "libctranslate2.so)"
    )

    # 3. The CT2 libs (plural) guarded include is present (covers the
    #    plural-layout wheel variant).
    assert 'CT2_LIBS_DIR="$SITE/ctranslate2/libs"' in text, (
        "build script must reference CT2_LIBS_DIR (the plural layout) — "
        "some wheel variants ship the OpenMP runtime under "
        "ctranslate2/libs instead of ctranslate2/lib"
    )
    assert '[[ -d "$CT2_LIBS_DIR" ]]' in text, (
        "build script must guard the plural ctranslate2/libs include so "
        "the build doesn't fail on wheel installs that ship only the "
        "singular ctranslate2/lib layout"
    )
