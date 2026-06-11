"""Parakeet TDT v3 ASR engine — optional backend alongside Whisper/Qwen.

Uses NVIDIA's parakeet-tdt-0.6b-v3 via HuggingFace Transformers.
Auto-downloads model weights on first load via huggingface_hub.
Falls back gracefully on missing deps, CUDA errors, etc.
"""

import logging
import os
import threading
from typing import Optional, Callable
from pathlib import Path

import numpy as np

from voice_typer.server.hallucination import should_reject_low_audio_hallucination

log = logging.getLogger(__name__)

_PARAKERT_MODEL_ID = "nvidia/parakeet-tdt-0.6b-v3"


def _ensure_model_file(progress_callback: Optional[Callable[[str], None]] = None) -> bool:
    """Download model.safetensors with Ctrl+C-safe resume.

    HF hub deletes incomplete files on interruption, so we do the big
    file download ourselves via ``requests`` with ``Range:`` header.
    Once the file is complete, we place it in the HF cache so
    ``from_pretrained`` finds it.
    """
    from voice_typer.server.asr_setup import _config_dir, ensure_hf_env
    ensure_hf_env()

    filename = "model.safetensors"
    from huggingface_hub import hf_hub_url
    from huggingface_hub.utils import build_hf_headers

    url = hf_hub_url(_PARAKERT_MODEL_ID, filename)
    headers = build_hf_headers()

    # Get etag and size (follow redirects — HF CDN)
    import requests as _requests
    resp = _requests.head(url, headers=headers, timeout=30, allow_redirects=True)
    if resp.status_code != 200:
        log.warning("[PARAKEET] HEAD request for %s returned %s", url, resp.status_code)
        return False
    etag = (
        resp.headers.get("x-linked-etag")
        or resp.headers.get("etag", "").strip('"')
    )
    try:
        expected_size = int(resp.headers.get("content-length", 0))
    except (ValueError, TypeError):
        expected_size = 0

    if expected_size == 0:
        log.warning("[PARAKEET] Could not determine size of %s", filename)
        return False

    # Build cache paths
    cache_dir = _config_dir() / "huggingface" / "hub"
    storage_folder = cache_dir / f"models--{_PARAKERT_MODEL_ID.replace('/', '--')}"
    blobs_dir = storage_folder / "blobs"
    blobs_dir.mkdir(parents=True, exist_ok=True)

    blob_path = blobs_dir / etag
    partial_path = blob_path.with_suffix(".partial")

    # Already complete?
    if blob_path.exists() and blob_path.stat().st_size >= expected_size:
        return True

    # Resume from partial
    start_byte = 0
    if partial_path.exists():
        start_byte = partial_path.stat().st_size
        if start_byte >= expected_size:
            partial_path.rename(blob_path)
            return True
        log.info("[PARAKEET] Resuming %s from byte %d / %d", filename, start_byte, expected_size)

    # Download with Range header
    dl_headers = dict(headers)
    if start_byte > 0:
        dl_headers["Range"] = f"bytes={start_byte}-"

    mode = "ab" if start_byte > 0 else "wb"

    if progress_callback:
        pct = int(start_byte / expected_size * 100) if expected_size else 0
        size_str = f"{expected_size / 1024**3:.1f}GB"
        progress_callback(f"Downloading {filename} ({size_str}) — {pct}%...")

    log.info("[PARAKEET] Downloading %s%s (%s, %.1f GB)",
             filename, " (resume)" if start_byte > 0 else "",
             url, expected_size / 1024 ** 3)
    if start_byte > 0:
        log.info("[PARAKEET] Resuming from byte %d / %d", start_byte, expected_size)

    try:
        resp = _requests.get(url, headers=dl_headers, stream=True, timeout=(10, 300), allow_redirects=True)
        resp.raise_for_status()

        with open(partial_path, mode) as f:
            downloaded = start_byte
            last_pct = -1
            from tqdm import tqdm
            pbar = tqdm(
                total=expected_size,
                initial=start_byte,
                unit='B',
                unit_scale=True,
                unit_divisor=1024,
                desc=filename,
                mininterval=0.5,
            )
            for chunk in resp.iter_content(chunk_size=8 * 1024 * 1024):
                if chunk:
                    f.write(chunk)
                    f.flush()
                    downloaded += len(chunk)
                    pbar.update(len(chunk))
                    if progress_callback and expected_size:
                        pct = int(downloaded / expected_size * 100)
                        if pct != last_pct:
                            last_pct = pct
                            progress_callback(f"Downloading {filename} ({pct}%)...")
            pbar.close()
    except KeyboardInterrupt:
        log.warning("[PARAKEET] Download interrupted — partial saved at %s", partial_path)
        if progress_callback:
            progress_callback("Download paused (will resume next time)")
        return False
    except Exception as exc:
        log.error("[PARAKEET] Download error: %s", exc)
        if progress_callback:
            progress_callback(f"Download error: {exc}")
        return False

    # Complete → move to blob path
    partial_path.replace(blob_path)
    log.info("[PARAKEET] %s downloaded (%d bytes)", filename, blob_path.stat().st_size)
    return True


class ParakeetEngine:
    """Wraps NVIDIA Parakeet TDT v3 ASR model via Transformers.

    Implements TranscriberProtocol so the app can swap backends transparently.
    Model weights are auto-downloaded from HuggingFace on first load.
    """

    def __init__(
        self,
        device: str = "cuda",
        language: str = "en",
    ):
        self.device = device
        self.language = language
        self._model = None
        self._processor = None
        self._lock = threading.RLock()

    # ── TranscriberProtocol ──────────────────────────────────────────

    @property
    def is_loaded(self) -> bool:
        with self._lock:
            return self._model is not None and self._processor is not None

    def load(self, progress_callback: Optional[Callable[[str], None]] = None) -> bool:
        """Download (if needed) and load the Parakeet model.

        Uses custom resume-capable download for model.safetensors
        (HF hub deletes partials on Ctrl+C, so we handle it ourselves).
        Weights land in ``~/.voice-typer/huggingface/hub/``.

        Returns True on success, False on failure.
        """
        with self._lock:
            if self._model is not None:
                return True

            # Set up HF env vars (HF_HOME, suppress warnings, etc.)
            try:
                from voice_typer.server.asr_setup import ensure_hf_env
                ensure_hf_env()
            except Exception:
                pass

            # 1. Snapshot-download all small files first (config, tokenizer, etc.)
            try:
                from huggingface_hub import snapshot_download

                if progress_callback:
                    progress_callback("Downloading Parakeet model files...")

                snapshot_download(
                    repo_id=_PARAKERT_MODEL_ID,
                    local_files_only=True,
                )
            except Exception:
                try:
                    from huggingface_hub import snapshot_download
                    if progress_callback:
                        progress_callback("Downloading Parakeet model files (may take a moment)...")
                    log.info("[PARAKEET] Downloading model metadata...")
                    snapshot_download(
                        repo_id=_PARAKERT_MODEL_ID,
                        resume_download=True,
                    )
                except Exception as exc:
                    log.error("[PARAKEET] Metadata download failed: %s", exc)
                    if progress_callback:
                        progress_callback(f"Download failed: {exc}")
                    return False

            # 2. Ensure model.safetensors with resume support
            if not _ensure_model_file(progress_callback):
                log.warning("[PARAKEET] Model file download incomplete or failed")
                return False

            # 3. Load model from cache
            try:
                from transformers import AutoModelForTDT, AutoProcessor
                import torch

                if progress_callback:
                    progress_callback("Loading Parakeet TDT v3 model...")

                log.info("[PARAKEET] Loading model (device=%s)...", self.device)
                effective_device = self.device
                if effective_device == "cuda" and not torch.cuda.is_available():
                    log.warning("[PARAKEET] CUDA requested but not available, falling back to CPU")
                    effective_device = "cpu"

                self._processor = AutoProcessor.from_pretrained(_PARAKERT_MODEL_ID)
                self._model = AutoModelForTDT.from_pretrained(
                    _PARAKERT_MODEL_ID,
                    dtype=torch.float16 if effective_device == "cuda" else torch.float32,
                    device_map=effective_device,
                )
                log.info("[PARAKEET] Model loaded successfully")
                if progress_callback:
                    progress_callback("Parakeet model ready")
                return True

            except ImportError as exc:
                log.error("[PARAKEET] transformers package not installed: %s", exc)
                if progress_callback:
                    progress_callback(f"Missing dependency: {exc}")
                return False
            except KeyboardInterrupt:
                log.warning("[PARAKEET] Loading interrupted by user")
                if progress_callback:
                    progress_callback("Loading cancelled")
                return False
            except Exception as exc:
                log.error("[PARAKEET] Failed to load model: %s", exc)
                if progress_callback:
                    progress_callback(f"Model load failed: {exc}")
                return False

    def transcribe(self, audio: np.ndarray) -> str:
        """Transcribe audio array. Returns cleaned text string.

        Raises RuntimeError if the model is not loaded.
        """
        with self._lock:
            if self._model is None or self._processor is None:
                raise RuntimeError(
                    "Parakeet model not loaded. Call load() first or check logs."
                )

            if len(audio) == 0:
                return ""

            import torch

            try:
                inputs = self._processor(
                    [audio],
                    sampling_rate=16000,
                    return_tensors="pt",
                )
                inputs.to(device=self._model.device, dtype=self._model.dtype)
                output = self._model.generate(**inputs, return_dict_in_generate=True)
                text = self._processor.decode(
                    output.sequences,
                    skip_special_tokens=True,
                )
                text = text.strip()
            except Exception as exc:
                log.error("[PARAKEET] Transcription failed: %s", exc)
                return ""

            rms = float(np.sqrt(np.mean(np.square(audio), dtype=np.float64)))
            if should_reject_low_audio_hallucination(text, rms):
                log.warning("[PARAKEET] Rejected likely hallucination: %r", text[:80])
                return ""

            return text

    def transcribe_with_fallback(self, audio: np.ndarray) -> str:
        """transcribe with GPU→CPU fallback on CUDA errors."""
        with self._lock:
            if self._model is None or self._processor is None:
                raise RuntimeError("Parakeet model not loaded.")

            if len(audio) == 0:
                return ""

            try:
                return self.transcribe(audio)
            except Exception as exc:
                err_str = str(exc).lower()
                if self.device == "cuda" and ("cuda" in err_str or "cublas" in err_str or "cudnn" in err_str):
                    log.warning("[PARAKEET] CUDA error, retrying on CPU: %s", exc)
                    import torch
                    try:
                        self._model.to("cpu")
                        text = self._transcribe_impl(audio)
                        return text
                    except Exception as cpu_exc:
                        log.error("[PARAKEET] CPU fallback also failed: %s", cpu_exc)
                        return ""
                return ""

    def _transcribe_impl(self, audio: np.ndarray) -> str:
        """Core transcription without lock or error handling for fallback."""
        import torch
        inputs = self._processor(
            [audio],
            sampling_rate=16000,
            return_tensors="pt",
        )
        inputs.to(device=self._model.device, dtype=self._model.dtype)
        output = self._model.generate(**inputs, return_dict_in_generate=True)
        text = self._processor.decode(
            output.sequences,
            skip_special_tokens=True,
        )
        text = text.strip()

        rms = float(np.sqrt(np.mean(np.square(audio), dtype=np.float64)))
        if should_reject_low_audio_hallucination(text, rms):
            log.warning("[PARAKEET] Rejected likely hallucination: %r", text[:80])
            return ""

        return text

    def unload(self) -> None:
        with self._lock:
            self._model = None
            self._processor = None
        log.info("[PARAKEET] Model unloaded")

    @property
    def device_info(self) -> str:
        return f"parakeet/{self.device}"

    @property
    def loaded_via(self) -> str:
        return f"parakeet/{self.device}/{_PARAKERT_MODEL_ID}"


def download_parakeet_weights(
    progress_callback: Optional[Callable[[str], None]] = None,
) -> bool:
    """Download Parakeet TDT v3 model weights with resume support.

    Returns True if downloaded or already cached.
    """
    try:
        from huggingface_hub import snapshot_download
    except ImportError:
        log.error("[PARAKEET] huggingface_hub not available")
        if progress_callback:
            progress_callback("huggingface_hub not installed")
        return False

    try:
        snapshot_download(repo_id=_PARAKERT_MODEL_ID, local_files_only=True)
        if progress_callback:
            progress_callback("Parakeet model already cached")
        return True
    except Exception:
        pass

    msg = "Downloading Parakeet model files..."
    log.info("[PARAKEET] %s", msg)
    if progress_callback:
        progress_callback(msg)

    try:
        snapshot_download(repo_id=_PARAKERT_MODEL_ID, resume_download=True)
        if progress_callback:
            progress_callback("Model metadata downloaded")
    except Exception as exc:
        log.error("[PARAKEET] Metadata download failed: %s", exc)
        if progress_callback:
            progress_callback(f"Metadata download failed: {exc}")
        return False

    if not _ensure_model_file(progress_callback):
        log.warning("[PARAKEET] Model file download incomplete")
        return False

    if progress_callback:
        progress_callback("Parakeet model download complete")
    return True
