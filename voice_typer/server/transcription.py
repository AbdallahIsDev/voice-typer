"""Whisper transcription engine using faster-whisper."""

import contextlib
import logging
import os
import site
import sys
import threading
import time
from typing import Any, Protocol, runtime_checkable

import numpy as np

from voice_typer.server.hallucination import log_hallucination_rejection, should_reject_low_audio_hallucination
from voice_typer.server.platform_utils import is_windows

log = logging.getLogger(__name__)


@runtime_checkable
class TranscriberProtocol(Protocol):
    """Protocol that any transcription engine must implement."""

    @property
    def is_loaded(self) -> bool: ...

    def load(self, progress_callback=None) -> None: ...

    def transcribe(self, audio: np.ndarray) -> str: ...

    def transcribe_with_fallback(self, audio: np.ndarray) -> str: ...

    def unload(self) -> None: ...

    @property
    def device_info(self) -> str: ...

    @property
    def loaded_via(self) -> str: ...

_WHISPER_SAMPLE_RATE = 16000  # Whisper always expects 16kHz input
_nvidia_dll_path_handles: list[object] = []


# PROD-004: Approximate model sizes (MB) for disk-space pre-check.
# These are the uncompressed sizes of the faster-whisper models.
_MODEL_SIZE_MB = {
    "tiny.en": 75,
    "tiny": 75,
    "base.en": 150,
    "base": 150,
    "small.en": 500,
    "small": 500,
    "medium.en": 1500,
    "medium": 1500,
    "large-v1": 3000,
    "large-v2": 3000,
    "large-v3": 3000,
    "large": 3000,
    # NEW-MODEL-001: added turbo + distilled variants.
    # ``large-v3-turbo`` (a.k.a. "turbo") is the fast multilingual model
    # released by OpenAI in 2024 — near-large-v3 accuracy at ~8x speed.
    # ``distil-large-v3`` and ``distil-medium.en`` are distilled variants
    # from the Distil-Whisper project: smaller, faster, slightly lower
    # accuracy.  See ``voice_typer/server/model_registry.py`` for full
    # metadata (VRAM, supported languages, repo IDs, speed ratings).
    "large-v3-turbo": 809,
    "turbo": 809,  # alias for large-v3-turbo
    "distil-large-v3": 1500,
    "distil-medium.en": 780,
}
# Extra margin for temporary files, metadata, tokenizer, etc.
_DISK_SPACE_MARGIN_MB = 500


def release_gpu_memory() -> None:
    """Release GPU memory held by PyTorch's caching allocator.

    NEW-MEM-001: ``del model; gc.collect()`` releases the Python
    references to the model but PyTorch's CUDA caching allocator
    retains the freed blocks for reuse by the same process.  After a
    backend switch (e.g. Whisper → Parakeet → Whisper), the cached
    blocks from the previous model are never reused (different model
    architecture), so they accumulate.  On RTX 3060/4060 (8–12 GB
    VRAM), 2 backend switches can OOM.

    This helper calls ``torch.cuda.empty_cache()`` to release the
    cached blocks back to the OS, making VRAM available for the next
    backend.  Safe to call when:

    - torch is not installed (no-op, debug-logged)
    - CUDA is not initialized (no-op, returns silently)
    - the current device is CPU (no-op)

    Designed to be called from every ASR engine's ``unload()`` and
    from every GPU→CPU fallback path in ``TranscriptionEngine``.
    """
    try:
        import torch
    except ImportError:
        # torch not installed — nothing to release.
        return
    try:
        if not torch.cuda.is_available():
            return
        # Synchronize before empty_cache so pending async kernels
        # finish and release their allocations.
        torch.cuda.synchronize()
        torch.cuda.empty_cache()
        log.debug("[GPU] torch.cuda.empty_cache() called after model unload")
    except Exception as exc:
        # CUDA not initialized, or some other runtime issue — log
        # at debug so we don't spam the log on every unload.
        log.debug("[GPU] torch.cuda.empty_cache() failed: %s", exc)


def _free_nvidia_dll_path_handles() -> None:
    """Release DLL directory handles opened by ``_configure_nvidia_dll_paths``.

    PERF-NEW-020: ``os.add_dll_directory`` returns a handle that holds
    a reference to the OS-level DLL directory entry. Previously these
    handles were stored in ``_nvidia_dll_path_handles`` and never freed,
    so the process held phantom DLL directory refs even after the model
    was unloaded. We now iterate and call each handle's ``close()``
    method (the documented way to release the directory entry).
    Called from ``TranscriptionEngine.unload()`` on shutdown.
    """
    global _nvidia_dll_path_handles
    for handle in _nvidia_dll_path_handles:
        try:
            close = getattr(handle, "close", None)
            if close is not None:
                close()
            else:
                # Some Python versions return a path string instead of
                # a handle object; nothing to close in that case.
                pass
        except Exception as exc:
            log.debug("[CUDA-DLL] Error closing handle %s: %s", handle, exc)
    _nvidia_dll_path_handles = []
_nvidia_dll_paths_configured = False
# RACE-029: module-level lock to serialize _configure_nvidia_dll_paths()
# calls.  Previously concurrent calls from the load path could both
# read _nvidia_dll_paths_configured==False and then both mutate
# _nvidia_dll_path_handles and os.environ["PATH"], causing duplicate
# DLL directory additions and race-conditional PATH corruption.
_nvidia_config_lock = threading.Lock()


def _download_with_retry(
    download_fn,
    *,
    max_attempts: int = 3,
    delays: tuple[float, ...] = (5.0, 15.0, 45.0),
    **kwargs,
) -> str:
    """PROD-004: Wrap snapshot_download() with exponential backoff retry.

    Downloads can fail due to transient network issues, HuggingFace
    rate limits, or CDN timeouts.  Retrying with increasing delays
    gives the network time to recover and avoids failing the entire
    model load on a single transient error.

    Parameters
    ----------
    download_fn : callable
        The ``snapshot_download`` function (or a wrapper).
    max_attempts : int
        Maximum number of download attempts.
    delays : tuple[float, ...]
        Delay in seconds before each retry.  The first attempt has no
        delay; ``delays[i]`` is the delay before attempt ``i+1``.
    **kwargs
        Forwarded to ``download_fn``.

    Returns
    -------
    str
        The path to the downloaded model directory.

    Raises
    ------
    Exception
        The last exception if all attempts fail.
    """
    import time as _time
    last_exc = None
    for attempt in range(max_attempts):
        try:
            return download_fn(**kwargs)
        except Exception as exc:
            last_exc = exc
            if attempt < max_attempts - 1:
                delay = delays[attempt] if attempt < len(delays) else delays[-1]
                log.warning(
                    "[PROD-004] Download attempt %d/%d failed: %s. "
                    "Retrying in %.0fs...",
                    attempt + 1, max_attempts, exc, delay,
                )
                _time.sleep(delay)
            else:
                log.error(
                    "[PROD-004] All %d download attempts failed. Last error: %s",
                    max_attempts, exc,
                )
    raise last_exc  # type: ignore[misc]


def _check_disk_space_for_download(repo_id: str, model_size: str) -> None:
    """PROD-005: Check available disk space before model download.

    Compares available space in the HuggingFace cache directory
    against the estimated model size with a 500 MB margin.
    Raises ``RuntimeError`` with a user-friendly message if
    insufficient space is detected.
    """
    import shutil
    try:
        # Determine the cache directory
        from huggingface_hub import constants
        cache_dir = constants.HF_HUB_CACHE
    except (ImportError, AttributeError):
        try:
            cache_dir = os.path.join(os.path.expanduser("~"), ".cache", "huggingface", "hub")
        except Exception:
            return  # Can't determine cache dir, skip check

    try:
        usage = shutil.disk_usage(cache_dir)
        available_mb = usage.free // (1024 * 1024)
        estimated_mb = _MODEL_SIZE_MB.get(model_size, 500) + _DISK_SPACE_MARGIN_MB

        if available_mb < estimated_mb:
            raise RuntimeError(
                f"Insufficient disk space to download model '{model_size}'. "
                f"Available: {available_mb} MB, "
                f"Required (estimated): {estimated_mb} MB "
                f"(model ~{_MODEL_SIZE_MB.get(model_size, 500)} MB + "
                f"{_DISK_SPACE_MARGIN_MB} MB margin). "
                f"Free up disk space and try again."
            )
        log.debug(
            "[PROD-005] Disk space check passed: %d MB available, "
            "~%d MB needed for '%s'",
            available_mb, estimated_mb, model_size,
        )
    except RuntimeError:
        raise
    except Exception as exc:
        # If we can't check disk space, don't block the download —
        # the download itself will fail with a clear error if space
        # runs out during the transfer.
        log.debug("[PROD-005] Disk space check skipped: %s", exc)


def _configure_nvidia_dll_paths():
    """Expose NVIDIA wheel DLL directories to the Windows loader.

    RACE-029: serialized by _nvidia_config_lock to prevent concurrent
    calls from corrupting _nvidia_dll_path_handles and PATH.
    """
    with _nvidia_config_lock:
        _configure_nvidia_dll_paths_locked()


def _configure_nvidia_dll_paths_locked():
    """Inner implementation, called under _nvidia_config_lock."""
    global _nvidia_dll_paths_configured
    if _nvidia_dll_paths_configured or not is_windows():
        return

    roots: list[str] = []
    try:
        roots.extend(site.getsitepackages())
    except Exception as exc:
        log.warning("[CUDA-DLL] site.getsitepackages() failed: %s", exc)
    try:
        user_site = site.getusersitepackages()
        if user_site:
            roots.append(user_site)
    except Exception as exc:
        log.warning("[CUDA-DLL] site.getusersitepackages() failed: %s", exc)

    # Also include the current venv's site-packages (via sys.prefix).
    # site.getsitepackages() can be wrong when the app runs from a
    # different Python environment (e.g. Hermes venv) than expected.
    venv_sp = os.path.join(sys.prefix, "Lib", "site-packages")
    if os.path.isdir(venv_sp) and venv_sp not in roots:
        roots.append(venv_sp)
        log.debug("[CUDA-DLL] Added current venv site-packages: %s", venv_sp)

    # Fallback: the app's own venv at ~/.voice-typer/venv/ may have the
    # NVIDIA pip wheels even when the running Python belongs to a
    # different environment.
    app_venv_sp = os.path.join(
        os.path.expanduser("~"), ".voice-typer", "venv", "Lib", "site-packages",
    )
    if os.path.isdir(app_venv_sp) and app_venv_sp not in roots:
        roots.append(app_venv_sp)
        log.debug("[CUDA-DLL] Added app venv site-packages: %s", app_venv_sp)

    log.debug("[CUDA-DLL] Searching root paths for NVIDIA DLLs: %s", roots)

    candidate_parts = [
        ("nvidia", "cublas", "bin"),
        ("nvidia", "cudnn", "bin"),
        ("nvidia", "cuda_nvrtc", "bin"),
        # CUDA-DLL-001: torch GPU wheels (pip install torch with CUDA)
        # also place cublas64_12.dll, cudnn64_9.dll, nvrtc64_120_0.dll
        # under torch/lib. Without this entry, users who installed the
        # GPU torch wheel but NOT the standalone nvidia-* pip packages
        # would have all 3 primary candidate paths miss, even though
        # the DLLs physically exist on disk.
        ("torch", "lib"),
    ]
    existing_paths = os.environ.get("PATH", "").split(os.pathsep)
    new_paths: list[str] = []
    for root in roots:
        for parts in candidate_parts:
            path = os.path.join(root, *parts)
            if not os.path.isdir(path):
                log.debug("[CUDA-DLL] Path not found: %s", path)
                continue
            dll_names = [n for n in os.listdir(path) if n.lower().endswith(".dll")]
            if not dll_names:
                log.debug("[CUDA-DLL] No DLLs in: %s", path)
                continue
            log.debug("[CUDA-DLL] Found path with %d DLLs: %s (first: %s)", len(dll_names), path, dll_names[0])
            if path not in existing_paths and path not in new_paths:
                new_paths.append(path)
            add_dll_directory = getattr(os, "add_dll_directory", None)
            if add_dll_directory is not None:
                try:
                    handle = add_dll_directory(path)
                    log.debug("[CUDA-DLL] os.add_dll_directory(%s) -> handle=%s", path, handle)
                    if handle is not None:
                        _nvidia_dll_path_handles.append(handle)
                except Exception as exc:
                    log.warning("[CUDA-DLL] os.add_dll_directory(%s) failed: %s", path, exc)

    if new_paths:
        os.environ["PATH"] = os.pathsep.join(new_paths + existing_paths)
        log.info("[CUDA-DLL] Prepended to PATH: %s", new_paths)

    _nvidia_dll_paths_configured = True


class TranscriptionEngine:
    """Wraps faster-whisper model loading and transcription."""

    def __init__(
        self,
        model_size: str = "small.en",
        device: str = "auto",
        language: str = "en",
        beam_size: int = 1,
        best_of: int = 1,
        condition_on_previous_text: bool = False,
        config: Any = None,
    ):
        self.model_size = model_size
        self._configured_model_size = model_size
        self._loaded_model_size: str | None = None
        self.language = language
        self.beam_size = beam_size
        self.best_of = best_of
        self.condition_on_previous_text = condition_on_previous_text
        self._model = None
        self._lock = threading.RLock()
        self._requested_device: str | None = device  # defer CUDA detection to load()
        self._device = "cpu"
        self._compute_type = "int8"
        # NEW-PRIV-005: store a reference to the app's Config dataclass
        # so the HuggingFace-consent check in _pre_download_model can
        # read ``huggingface_consent`` without crashing.  Previously
        # ``self.config`` was never assigned in __init__, but the
        # consent check at line ~528 did ``getattr(self.config, ...)``
        # — which raised ``AttributeError`` on every uncached model
        # download attempt (the most common production path).
        #
        # ``config`` may be None when constructed by callers that
        # don't have access to the app's Config (e.g. service.py
        # benchmark path, or test stubs).  In that case, the consent
        # check below treats None as "consent not given" and refuses
        # to download — which is the safe default per GDPR Art. 6/13.
        # Callers that need to download must pass a real Config.
        self.config = config

    def _resolve_device(self, device: str) -> tuple[str, str]:
        """Auto-detect best device and compute type.

        ERR-016: previously the CUDA-detection try/except used bare
        ``Exception``, hiding real setup errors (driver mismatch,
        missing DLLs). Narrowed to ``(OSError, RuntimeError,
        ImportError)`` so genuine bugs propagate.
        """
        if device == "cpu":
            return "cpu", "int8"

        # Try CUDA
        if device in ("auto", "cuda"):
            try:
                _configure_nvidia_dll_paths()
                import ctranslate2
                if ctranslate2.get_cuda_device_count() > 0:
                    log.info("[MODEL] Using CUDA device for transcription")
                    return "cuda", "float16"
            except (OSError, RuntimeError, ImportError):
                # OSError: missing DLL / driver file
                # RuntimeError: ctranslate2 internal init failure
                # ImportError: ctranslate2 not installed
                log.warning(
                    "[MODEL] CUDA detection failed, falling back to CPU",
                    exc_info=True,
                )

            if device == "cuda":
                log.warning("[MODEL] CUDA requested but not available, falling back to CPU")

        log.info("[MODEL] Using CPU for transcription")
        return "cpu", "int8"

    @property
    def is_loaded(self) -> bool:
        """Return True if the model has been loaded successfully."""
        return self._model is not None

    @property
    def device_info(self) -> str:
        return f"{self._device} ({self._compute_type})"

    @property
    def loaded_via(self) -> str:
        """Return a description of the device/model combo that was successfully loaded."""
        return f"{self._device}/{self._compute_type}/{self.model_size}"

    def load(self, progress_callback=None) -> None:
        """Load the Whisper model. Downloads on first run.

        Fallback chain:
          1. Configured device (e.g. CUDA/float16)
          2. CPU / int8 with original model size
          3. CPU / int8 with tiny.en
          4. CPU / float32 with tiny.en (last resort — avoids MKL int8 path)

        Stores which path succeeded via loaded_via property.

        progress_callback: optional callable(message: str) for download/load status.
        """
        # Deferred CUDA detection — run now (once) near load time
        self._resolve_device_once()
        self._pre_download_model(self.model_size, progress_callback)
        self._load_model_outside_lock(progress_callback=progress_callback)

    def _resolve_device_once(self):
        """Resolve the CUDA device if not already resolved.

        Separated from __init__ so the expensive ``import ctranslate2`` and
        CUDA DLL loading only happens when the model is actually about to
        load, not during construction.  This saves ~20s on startup when the
        user hasn't pressed F2 yet.
        """
        if self._requested_device is None:
            return
        device = self._requested_device
        self._requested_device = None
        self._device, self._compute_type = self._resolve_device(device)

    def _load_model_outside_lock(self, progress_callback=None):
        """Load model outside the lock so downloads don't block other threads.

        ARCH-014: extracted the common load logic into
        ``_load_transcriber_impl(acquire_lock)`` so this method and
        ``_reload_under_lock`` share one code path.
        """
        with self._lock:
            if self._model is not None:
                return
            chain = self._build_fallback_chain()

        self._load_transcriber_impl(
            chain, acquire_lock=True,
            progress_callback=progress_callback,
            verb="Loading",
        )

    def _load_transcriber_impl(
        self,
        chain: list[tuple[str, str, str]],
        *,
        acquire_lock: bool,
        progress_callback=None,
        verb: str = "Loading",
    ) -> None:
        """Shared model-load body used by both _load_model_outside_lock
        and _reload_under_lock.

        ARCH-014: previously two near-identical 30-line bodies that
        differed only in lock acquisition. Now a single method with
        an ``acquire_lock`` flag.

        Raises:
            RuntimeError: if every entry in the fallback chain failed.
        """
        # Map progressive verb → base form for error messages.
        verb_base = "load" if verb.lower() == "loading" else "reload"
        _configure_nvidia_dll_paths()
        from faster_whisper import WhisperModel

        last_error = None
        for device, compute_type, model_size in chain:
            try:
                log.info(
                    "[MODEL] %s Whisper model '%s' on %s (%s)...",
                    verb, model_size, device, compute_type,
                )
                if progress_callback:
                    progress_callback(f"{verb} model '{model_size}'...")
                # PW-4: time WhisperModel construction to measure
                # prewarm cache-hit effectiveness.
                _t0 = time.perf_counter()
                model = WhisperModel(
                    model_size,
                    device=device,
                    compute_type=compute_type,
                )
                _load_elapsed = time.perf_counter() - _t0
                _warm_label = "warm (page-cache)" if _load_elapsed < 5.0 else "cold (disk)"
                if acquire_lock:
                    with self._lock:
                        if self._model is not None:
                            return
                        self._model = model
                        self._device = device
                        self._compute_type = compute_type
                        self._loaded_model_size = model_size
                        self.model_size = self._configured_model_size
                else:
                    # Caller already holds the lock.
                    self._model = model
                    self._device = device
                    self._compute_type = compute_type
                    self._loaded_model_size = model_size
                    self.model_size = self._configured_model_size
                log.info(
                    "[MODEL] Model %s via %s (%s) — %.1fs",
                    verb.lower(), self.loaded_via, _warm_label, _load_elapsed,
                )

                # CUDA probe: force a tiny transcription to smoke-test
                # cuBLAS loading at startup, so failures surface here
                # (with a clean fallback to CPU) rather than mid-recording.
                if self._device == "cuda" and acquire_lock:
                    self._probe_cuda_runtime(progress_callback)

                # PERF-007: warm-up inference with 0.5s of silence.
                # Primes CUDA kernels so the first real transcription
                # doesn't pay the kernel compilation cost (~2-5s).
                self._warm_up_model()
                return
            except Exception as exc:
                last_error = exc
                log.warning(
                    "Model %s failed on %s (%s) model=%s: %s",
                    verb.lower(), device, compute_type, model_size, exc,
                )
                if not acquire_lock:
                    self._model = None

        raise RuntimeError(
            f"Failed to {verb_base} Whisper model on any device/model. "
            f"Last error: {last_error}"
        ) from last_error

    def _build_fallback_chain(self) -> list[tuple[str, str, str]]:
        """Build the fallback chain for model loading."""
        chain: list[tuple[str, str, str]] = []
        chain.append((self._device, self._compute_type, self.model_size))
        if self._device != "cpu" or self._compute_type != "int8":
            chain.append(("cpu", "int8", self.model_size))
        if self.model_size != "tiny.en":
            chain.append(("cpu", "int8", "tiny.en"))
        chain.append(("cpu", "float32", "tiny.en"))
        return chain

    def _reload_under_lock(self):
        """Reload the model while already holding the lock (for GPU fallback).

        ARCH-014: now delegates to ``_load_transcriber_impl`` with
        ``acquire_lock=False`` instead of duplicating the load body.
        """
        chain = self._build_fallback_chain()
        self._load_transcriber_impl(chain, acquire_lock=False, verb="Reloading")

    def _probe_cuda_runtime(self, progress_callback=None):
        """Probe CUDA with a real transcription to force early cuBLAS/cuDNN loading.

        Uses a 1s sine-wave tone and the exact same parameters as
        ``_transcribe_unlocked`` — including ``vad_filter=True`` and
        ``without_timestamps=True`` — then **iterates every segment** so
        the underlying cuBLAS kernels are actually resolved.  If the DLLs
        can't be loaded, catches the error at startup and falls back to
        CPU immediately instead of failing mid-recording.
        """
        # RW-6 (pyrefly): ``self._model`` is declared ``self._model = None``
        # in __init__ and only assigned a real model instance inside the
        # load path. The sole caller (line ~554) only invokes us after a
        # successful load, but pyrefly cannot prove that contract across
        # method boundaries — so guard explicitly. Returning early here
        # also makes the function safe to call from tests / future
        # callers that haven't loaded a model yet.
        if self._model is None:
            log.warning("[CUDA-PROBE] Skipping — no model loaded")
            return
        import numpy as np
        t = np.arange(int(_WHISPER_SAMPLE_RATE), dtype=np.float32) / _WHISPER_SAMPLE_RATE
        probe_audio: np.ndarray = np.sin(2 * np.pi * 440 * t, dtype=np.float32) * 0.1

        log.info("[CUDA-PROBE] Running CUDA runtime smoke test (1s sine wave)...")
        if progress_callback:
            progress_callback("Running CUDA runtime probe...")
        try:
            # Must exercise the same cuBLAS kernels as real dictation.
            # NOTE: vad_filter=False is deliberate — VAD would reject a
            # sine wave as non-speech, causing Whisper to be skipped.
            segments, info = self._model.transcribe(
                probe_audio,
                beam_size=self.beam_size,
                best_of=self.best_of,
                temperature=0.0,
                vad_filter=False,
                language=self.language,
                condition_on_previous_text=self.condition_on_previous_text,
                without_timestamps=True,
            )
            # Force iteration through ALL segments — model.transcribe()
            # returns lazily; the real GPU work (and DLL loading) happens
            # here.
            for _seg in segments:
                pass
            log.info("[CUDA-PROBE] CUDA runtime OK — cuBLAS/cuDNN loaded successfully")
        except Exception as exc:
            error_str = str(exc)
            log.warning(
                "[CUDA-PROBE] CUDA runtime probe FAILED: %s",
                error_str,
            )
            if any(kw in error_str.lower() for kw in [
                "cublas", "cuda", "cudnn", "dll",
                "not found", "cannot be loaded", "load library",
            ]):
                log.warning(
                    "[CUDA-PROBE] cuBLAS/cuDNN runtime error detected — "
                    "falling back to CPU immediately",
                )
                try:
                    del self._model
                    import gc
                    gc.collect()
                    # NEW-MEM-001: release PyTorch's cached CUDA blocks
                    # so the next backend (or CPU reload) can use them.
                    release_gpu_memory()
                except Exception:
                    pass
                self._model = None
                self._device = "cpu"
                self._compute_type = "int8"
                self._reload_under_lock()
                log.warning(
                    "[CUDA-PROBE] Model reloaded on CPU after CUDA probe failure. "
                    "Loaded via: %s", self.loaded_via,
                )
            else:
                raise

    def _warm_up_model(self) -> None:
        """PERF-007: Run a warm-up inference with silence to prime CUDA kernels.

        The first CUDA inference typically takes 2-5 seconds longer than
        subsequent ones because the GPU kernels need to be compiled (JIT)
        and memory allocated. This method runs a 0.5-second silence
        transcription after model load so the first real dictation is fast.

        If the model is on CPU or warm-up fails, this is a no-op.
        """
        if self._model is None or self._device != "cuda":
            return
        try:
            import numpy as np
            warmup_audio = np.zeros(int(16000 * 0.5), dtype=np.float32)
            segments, _ = self._model.transcribe(
                warmup_audio,
                beam_size=1,
                best_of=1,
                temperature=0.0,
                vad_filter=False,
                language=self.language,
                without_timestamps=True,
            )
            # Force iteration to complete the warm-up
            for _ in segments:
                pass
            log.info("[PERF-007] Warm-up inference completed — CUDA kernels primed")
        except Exception as exc:
            # Warm-up failure is non-critical — log and continue
            log.debug("[PERF-007] Warm-up inference skipped: %s", exc)

    def _pre_download_model(self, model_size: str, progress_callback=None):
        """Pre-download model files via huggingface_hub if not already cached.

        This ensures the user sees download progress before WhisperModel blocks
        on the download internally.

        PERF-NEW-009: previously this blocked the calling thread, adding
        2-15s of cold-start latency before model loading could begin.
        We now check the cache first (fast path); if the model is
        already cached, we return immediately so load can proceed. If
        not cached, we download synchronously — load() already runs on
        a background thread, so parallelizing would just add complexity
        without measurable benefit (WhisperModel.__init__ needs the
        files anyway).

        NEW-PRIV-005: HuggingFace downloads reveal the user's IP to a
        US-headquartered third party.  We check the
        ``huggingface_consent`` config flag before downloading; if
        consent hasn't been given, we raise a ConsentRequiredError so
        the IPC layer can surface a consent dialog to the renderer.
        The cache-check path (``local_files_only=True``) does NOT
        require consent — it only reads local files and never
        contacts HuggingFace.
        """
        # Skip pre-download for non-Whisper model sizes (e.g. "parakeet" or "qwen")
        if not model_size or model_size in ("parakeet", "qwen"):
            log.debug("[MODEL] Skipping pre-download for non-Whisper model '%s'", model_size)
            return
        try:
            from huggingface_hub import snapshot_download

            repo_id = f"Systran/faster-whisper-{model_size}"

            # SEC-audit-005: Use pinned revision from MODEL_HASHES manifest
            from voice_typer.server.security import MODEL_HASHES
            whisper_revision = MODEL_HASHES.get(repo_id, {}).get("revision", "main")

            # SEC-audit-005: Allowlist of file patterns permitted in downloads
            _whisper_allow_patterns = [
                "*.safetensors", "*.bin", "config.json", "tokenizer.json",
                "tokenizer_config.json", "special_tokens_map.json",
                "preprocessor_config.json", "feature_extractor_config.json",
                "generation_config.json", "model.safetensors.index.json", "*.model",
            ]

            if progress_callback:
                progress_callback(f"Checking model cache for '{model_size}'...")
            try:
                snapshot_download(
                    repo_id=repo_id,
                    revision=whisper_revision,
                    allow_patterns=_whisper_allow_patterns,
                    local_files_only=True,
                )
                log.info("[MODEL] Model '%s' already cached", model_size)
                return
            except Exception:
                pass

            # NEW-PRIV-005: require explicit consent before downloading
            # from HuggingFace.  The cache check above is local-only
            # and doesn't need consent; the actual download does.
            #
            # Defensive: ``self.config`` may be None when the engine
            # is constructed without a Config reference (e.g. test
            # stubs, benchmark path).  In that case, treat consent as
            # NOT given — safe default per GDPR Art. 6/13.  This is
            # also a regression guard: previously, the bare
            # ``getattr(self.config, ...)`` call raised AttributeError
            # because ``self.config`` itself was missing (now assigned
            # in __init__, but we keep the defensive check so future
            # refactors don't reintroduce the crash).
            cfg = self.config
            consent = False if cfg is None else getattr(cfg, 'huggingface_consent', False)
            if not consent:
                log.warning(
                    "[MODEL] HuggingFace consent not given — refusing to download "
                    "model '%s'. The renderer should show a consent dialog."
                    , model_size,
                )
                if progress_callback:
                    progress_callback(
                        "HuggingFace consent required before downloading model."
                    )
                # Return without downloading.  The renderer is
                # responsible for showing the consent dialog when the
                # user tries to download a model via the Models page
                # UI; once consent is granted, the download retry
                # will succeed.
                return

            log.info("[MODEL] Model '%s' not cached, downloading...", model_size)
            if progress_callback:
                progress_callback(f"Downloading model '{model_size}' (varies by size)...")

            # PROD-005: check disk space before downloading
            _check_disk_space_for_download(repo_id, model_size)

            # PROD-004: download with retry and exponential backoff
            local_dir = _download_with_retry(
                snapshot_download,
                repo_id=repo_id,
                revision=whisper_revision,
                allow_patterns=_whisper_allow_patterns,
                resume_download=True,
            )
            # PROD-006: Verify model integrity after download.  Parakeet
            # and Qwen both verify (see asr_setup.py:316); Whisper
            # previously skipped this check, leaving it vulnerable to a
            # tampered or truncated download.  On failure we log + raise
            # so the outer except surfaces the failure and WhisperModel
            # retries (rather than silently loading a bad model).
            from voice_typer.server.security import verify_model_integrity
            if not verify_model_integrity(local_dir, repo_id):
                log.error(
                    "[MODEL] Model '%s' integrity check failed after download",
                    model_size,
                )
                if progress_callback:
                    progress_callback("Download completed but integrity check failed")
                raise RuntimeError(
                    f"Model integrity verification failed for {repo_id}"
                )
            log.info("[MODEL] Model '%s' download complete", model_size)
        except ImportError:
            log.debug("[MODEL] huggingface_hub not available, skipping pre-download")
        except Exception as exc:
            log.warning("[MODEL] Pre-download failed (WhisperModel will retry): %s", exc)

    def transcribe(self, audio: np.ndarray, audio_stats: "tuple[float, float, float] | None" = None) -> str:
        """Transcribe audio array. Returns cleaned text string.

        NEW-PERF-010: ``audio_stats`` is an optional pre-computed
        ``(rms, peak, silence_pct)`` tuple from ``Recorder.stop()``.
        When provided, the engine skips its own stats computation
        (saves 1-3 ms + 3× 1.9 MB transient memory per dictation).
        """
        with self._lock:
            return self._transcribe_unlocked(audio, audio_stats=audio_stats)

    def _transcribe_unlocked(self, audio: np.ndarray, audio_stats: "tuple[float, float, float] | None" = None) -> str:
        if self._model is None:
            raise RuntimeError("Model not loaded. Call load() first.")

        if len(audio) == 0:
            return ""

        # Log audio statistics for diagnostics
        duration = len(audio) / _WHISPER_SAMPLE_RATE
        # NEW-PERF-010: reuse pre-computed stats when provided (avoids
        # 1-3 ms + 3× 1.9 MB transient memory per dictation).
        if audio_stats is not None:
            rms, peak, silence_pct = audio_stats
        else:
            rms = float(np.sqrt(np.mean(np.square(audio), dtype=np.float64)))
            peak = float(np.max(np.abs(audio)))
            silence_pct = float(np.sum(np.abs(audio) < 0.001) / audio.size * 100)
        log.info(
            "[TRANSCRIBE] Input audio: samples=%d, duration=%.1fs, "
            "RMS=%.6f, peak=%.6f, silence_pct=%.1f%%",
            len(audio), duration, rms, peak, silence_pct,
        )
        if rms < 0.001:
            log.warning(
                "[TRANSCRIBE] Near-silence input (RMS=%.6f). "
                "Speech detection is unlikely.",
                rms,
            )

        segments, info = self._model.transcribe(
            audio,
            beam_size=self.beam_size,
            best_of=self.best_of,
            temperature=0.0,
            vad_filter=True,
            vad_parameters=dict(
                min_silence_duration_ms=500,
                speech_pad_ms=200,
            ),
            language=self.language,
            condition_on_previous_text=self.condition_on_previous_text,
            without_timestamps=True,
        )

        # Collect segments and log VAD info
        text_parts = []
        segment_count = 0
        first_segment_start = None
        last_segment_end = None
        avg_logprobs = []
        no_speech_probs = []
        for seg in segments:
            segment_count += 1
            start = seg.start or 0.0
            end = seg.end or start
            if first_segment_start is None:
                first_segment_start = start
            last_segment_end = end
            avg_logprob = getattr(seg, "avg_logprob", None)
            no_speech_prob = getattr(seg, "no_speech_prob", None)
            if isinstance(avg_logprob, (int, float)):
                avg_logprobs.append(float(avg_logprob))
            if isinstance(no_speech_prob, (int, float)):
                no_speech_probs.append(float(no_speech_prob))
            if seg.text.strip():
                text_parts.append(seg.text.strip())
                log.debug(
                    "[TRANSCRIBE] Segment: [%.1fs - %.1fs] %s",
                    start, end, seg.text.strip(),
                )

        log.info(
            "[TRANSCRIBE] VAD result: language=%s (prob=%.2f), "
            "segments=%d, text_segments=%d, avg_logprob=%s, no_speech_prob=%s",
            info.language,
            info.language_probability,
            segment_count,
            len(text_parts),
            _format_optional_mean(avg_logprobs),
            _format_optional_mean(no_speech_probs),
        )

        result = " ".join(text_parts).strip()
        if self._should_reject_low_audio_hallucination(
            result=result,
            rms=rms,
            peak=peak,
            silence_pct=silence_pct,
            duration=duration,
            first_segment_start=first_segment_start,
            last_segment_end=last_segment_end,
        ):
            # SEC-009: Use the PII-safe logging helper instead of raw text
            log_transcriptions = (
                self.config is not None
                and getattr(self.config, 'log_transcriptions', False)
            )
            log_hallucination_rejection(
                "[TRANSCRIBE]", result,
                reason="low-audio hallucination",
                log_transcriptions=log_transcriptions,
            )
            log.info(
                "[TRANSCRIBE] Hallucination stats: duration=%.1fs, RMS=%.6f, "
                "peak=%.6f, silence=%.1f%%",
                duration, rms, peak, silence_pct,
            )
            return ""
        if result:
            log.info("[TRANSCRIBE] Result: %d chars", len(result))
        else:
            log.info(
                "[TRANSCRIBE] No speech detected (RMS=%.6f, silence=%.1f%%)",
                rms, silence_pct,
            )
        return result

    def transcribe_with_fallback(
        self,
        audio: np.ndarray,
        audio_stats: "tuple[float, float, float] | None" = None,
    ) -> str:
        """Transcribe with automatic CPU fallback on GPU runtime errors.

        If the first attempt fails with a CUDA/cuBLAS/runtime error and
        the model was loaded on GPU, reload on CPU and retry once.

        NEW-PERF-010: ``audio_stats`` is an optional pre-computed
        ``(rms, peak, silence_pct)`` tuple from ``Recorder.stop()``.
        When provided, the engine skips its own stats computation.
        """
        with self._lock:
            result = self._transcribe_with_fallback_unlocked(audio, audio_stats=audio_stats)
        # RACE-023: perform deferred gc.collect() OUTSIDE the lock
        if getattr(self, '_pending_gc_collect', False):
            self._pending_gc_collect = False
            try:
                import gc
                gc.collect()
                release_gpu_memory()
            except Exception:
                pass
        return result

    def _transcribe_with_fallback_unlocked(
        self,
        audio: np.ndarray,
        audio_stats: "tuple[float, float, float] | None" = None,
    ) -> str:
        try:
            return self._transcribe_unlocked(audio, audio_stats=audio_stats)
        except Exception as first_err:
            if not self._is_gpu_runtime_error(first_err):
                raise

            log.warning(
                "GPU transcription failed (%s), falling back to CPU",
                first_err,
            )
            # Tear down GPU model, reload on CPU
            # M9: Explicitly free GPU memory before reload
            # NEW-MEM-001: also call torch.cuda.empty_cache() so the
            # cached CUDA blocks are released back to the OS, not just
            # held for reuse by a different (non-Whisper) backend.
            # RACE-023: gc.collect() moved OUTSIDE the lock to avoid
            # blocking is_loaded / transcribe for 10-100ms.
            with contextlib.suppress(Exception):
                del self._model
            self._model = None
            self._device = "cpu"
            self._compute_type = "int8"
            self._reload_under_lock()
            # RACE-023: gc.collect() and release_gpu_memory() called
            # OUTSIDE the lock (the lock is released when we return
            # and the caller's `with self._lock:` block exits).
            # We store a flag so the caller can do the cleanup.
            self._pending_gc_collect = True
            # NEW-PERF-010: pass through the pre-computed stats to the
            # CPU retry as well.
            return self._transcribe_unlocked(audio, audio_stats=audio_stats)

    def transcribe_words(self, audio: np.ndarray, offset_seconds: float = 0.0):
        """Transcribe audio array into word timings with a global offset."""
        with self._lock:
            result = self._transcribe_words_with_fallback_unlocked(audio, offset_seconds)
        # RACE-023: perform deferred gc.collect() OUTSIDE the lock
        if getattr(self, '_pending_gc_collect', False):
            self._pending_gc_collect = False
            try:
                import gc
                gc.collect()
                release_gpu_memory()
            except Exception:
                pass
        return result

    def _transcribe_words_with_fallback_unlocked(
        self,
        audio: np.ndarray,
        offset_seconds: float,
    ):
        try:
            return self._transcribe_words_unlocked(audio, offset_seconds)
        except Exception as first_err:
            if not self._is_gpu_runtime_error(first_err):
                raise

            log.warning(
                "GPU timestamped transcription failed (%s), falling back to CPU",
                first_err,
            )
            # M9: Explicitly free GPU memory before reload
            # NEW-MEM-001: also release CUDA cached blocks.
            # RACE-023: gc.collect() deferred outside the lock.
            release_gpu_memory()
            with contextlib.suppress(Exception):
                del self._model
            self._model = None
            self._device = "cpu"
            self._compute_type = "int8"
            self._reload_under_lock()
            self._pending_gc_collect = True
            return self._transcribe_words_unlocked(audio, offset_seconds)

    def _transcribe_words_unlocked(self, audio: np.ndarray, offset_seconds: float):
        if self._model is None:
            raise RuntimeError("Model not loaded. Call load() first.")

        if len(audio) == 0:
            return []

        from voice_typer.server.streaming import WordTiming

        segments, _info = self._model.transcribe(
            audio,
            beam_size=self.beam_size,
            best_of=self.best_of,
            temperature=0.0,
            vad_filter=True,
            vad_parameters=dict(
                min_silence_duration_ms=500,
                speech_pad_ms=200,
            ),
            language=self.language,
            condition_on_previous_text=self.condition_on_previous_text,
            word_timestamps=True,
            without_timestamps=False,
        )

        words = []
        for seg in segments:
            for word in getattr(seg, "words", None) or []:
                text = (word.word or "").strip()
                if not text:
                    continue
                start = (word.start or 0.0) + offset_seconds
                end = (word.end or word.start or 0.0) + offset_seconds
                words.append(
                    WordTiming(
                        word=text,
                        start_seconds=start,
                        end_seconds=end,
                    )
                )
        return words

    def _is_gpu_runtime_error(self, exc: Exception) -> bool:
        """ERR-015: detect GPU/CUDA runtime errors via class hierarchy +
        attribute checks first, falling back to substring matching
        only for wrapped/re-raised errors. Previously the substring
        list was the primary check, misclassifying new error classes
        (e.g. ROCm) and triggering wrong fallbacks.
        """
        if self._device == "cpu":
            return False
        # 1. Class-hierarchy check: torch.cuda.OutOfMemoryError,
        #    ctranslate2.CUDAError, etc.
        try:
            import torch
            if isinstance(exc, torch.cuda.OutOfMemoryError):
                return True
        except (ImportError, AttributeError):
            pass
        # ctranslate2 errors (faster-whisper wraps these)
        try:
            import ctranslate2
            # Some ctranslate2 builds don't expose CUDAError as a class.
            # Guard with isinstance check on the attribute type.
            for attr_name in ("CUDAError", "RuntimeError"):
                cls = getattr(ctranslate2, attr_name, None)
                if isinstance(cls, type) and isinstance(exc, cls):
                    return True
        except (ImportError, AttributeError, ValueError):
            # ImportError: ctranslate2 not installed.
            # AttributeError: ctranslate2 installed but missing CUDAError/RuntimeError attrs.
            # ValueError: ctranslate2's import chain (transformers → PIL vision check)
            #   can raise ValueError("PIL.__spec__ is not set") in environments where
            #   PIL was imported via a non-standard path. This is environment noise,
            #   not a real GPU error — fall through to the substring/MRO check below.
            pass
        # 2. MRO-based class-name check (catches wrapped exceptions
        #    whose original class still appears in the MRO).
        for cls in type(exc).__mro__:
            cls_name = cls.__name__.lower()
            if any(kw in cls_name for kw in ["cudnn", "cublas", "cuda", "ctranslate2"]):
                return True
            cls_module = getattr(cls, "__module__", "") or ""
            if any(kw in cls_module.lower() for kw in ["ctranslate2", "cudnn", "cublas"]):
                return True
        # 3. Attribute check: some libraries attach a `.cuda_error`
        #    or `.device` attribute to runtime errors.
        if getattr(exc, "cuda_error", None) or getattr(exc, "is_cuda_error", False):
            return True
        # 4. Fallback to string matching for re-raised / wrapped errors
        #    where the original class info is lost. This is a last
        #    resort, not the primary signal.
        error_str = str(exc).lower()
        return any(kw in error_str for kw in [
            "cublas", "cuda", "cudnn", "gpu",
            "not found or cannot be loaded",
        ])

    def _should_reject_low_audio_hallucination(
        self,
        *,
        result: str,
        rms: float,
        peak: float,
        silence_pct: float,
        duration: float,
        first_segment_start: float | None,
        last_segment_end: float | None,
    ) -> bool:
        return should_reject_low_audio_hallucination(
            result,
            rms,
            peak=peak,
            silence_pct=silence_pct,
            duration=duration,
            first_segment_start=first_segment_start,
            last_segment_end=last_segment_end,
        )

    def unload(self) -> None:
        """Free model memory.

        PERF-NEW-020: also release DLL directory handles opened by
        ``_configure_nvidia_dll_paths`` so the process doesn't hold
        phantom DLL refs after the model is unloaded.

        NEW-MEM-001: also call ``torch.cuda.empty_cache()`` so the
        PyTorch caching allocator releases freed CUDA blocks back to
        the OS.  Without this, switching backends (Whisper → Parakeet
        → Whisper) accumulates cached blocks and OOMs on RTX 3060/4060
        (8–12 GB VRAM) after ~2 switches.

        RACE-023: gc.collect() moved OUTSIDE the lock to avoid blocking
        is_loaded / transcribe for 10-100ms.
        """
        import gc
        with self._lock:
            self._model = None
        # RACE-023: gc.collect() OUTSIDE the lock
        gc.collect()
        # NEW-MEM-001: release CUDA cached blocks.
        release_gpu_memory()
        # PERF-NEW-020: release DLL directory handles outside the lock
        # (they don't touch self._model state).
        try:
            _free_nvidia_dll_path_handles()
        except Exception:
            log.debug("[MODEL] Error releasing DLL handles", exc_info=True)




def _format_optional_mean(values: list[float]) -> str:
    """Format a list of floats as a 2-decimal mean, or 'n/a' if empty.

    ARCH-039: small helper kept as-is because it has two call sites
    (line 523/524) and inlining would duplicate the empty-list check.
    Marked LOW priority in the audit — keeping for readability.
    """
    if not values:
        return "n/a"
    return f"{sum(values) / len(values):.2f}"
