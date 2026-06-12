"""Parakeet TDT v3 ASR engine — optional backend alongside Whisper/Qwen.

Uses NVIDIA's parakeet-tdt-0.6b-v3 via HuggingFace Transformers.
Auto-downloads model weights on first load via huggingface_hub.
Falls back gracefully on missing deps, CUDA errors, etc.
"""

import logging
import os
import threading
import unicodedata
from typing import Optional, Callable
from pathlib import Path

import numpy as np

from voice_typer.server.hallucination import should_reject_low_audio_hallucination

log = logging.getLogger(__name__)

# Maximum allowed ratio of non-Latin-script characters before we reject
# a transcription segment as a language-hallucination.
# The model is English-only; output with >30% non-Latin characters is
# almost certainly a decoding error, not valid speech.
_NON_LATIN_RATIO_LIMIT = 0.30


def _is_latin_char(ch: str) -> bool:
    """Return True if *ch* belongs to the Latin script (or is whitespace/digit/punct)."""
    cat = unicodedata.category(ch)
    if cat.startswith("P") or cat.startswith("Z") or cat.startswith("S"):
        return True
    if ch.isdigit():
        return True
    script = unicodedata.name(ch, "").split(" ")[0] if ch else ""
    return script == "LATIN"


def _is_likely_english(text: str) -> bool:
    """Return False if *text* contains too many non-Latin-script characters.

    The Parakeet model is English-only but sometimes hallucinates text in
    unrelated scripts (CJK, Arabic, Devanagari, etc.).  This filter rejects
    those segments rather than pasting garbled text into the user's field.
    """
    if not text or not text.strip():
        return True
    non_latin = sum(1 for ch in text if not _is_latin_char(ch))
    ratio = non_latin / len(text)
    if ratio > _NON_LATIN_RATIO_LIMIT:
        log.info(
            "[PARAKEET] Rejected non-English output (%.0f%% non-Latin chars): %r",
            ratio * 100, text[:80],
        )
        return False
    return True

_PARAKERT_MODEL_ID = "nvidia/parakeet-tdt-0.6b-v3"

# Parakeet's Conformer encoder has a practical limit of ~30s of audio.
# Longer recordings are split into non-overlapping chunks and merged by
# simple concatenation.  Zero overlap avoids ASR boundary duplication.
_CHUNK_SECONDS = 25
_CHUNK_OVERLAP_SECONDS = 0

# ── Resume metadata helpers (outside HF hub cache) ────────────────


def _write_resume_meta(path: Path, data: dict) -> None:
    """Write small JSON metadata alongside the partial so we can verify
    etag/URL across reboots."""
    import json
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _cleanup_resume_meta(download_dir: Path, etag: str) -> None:
    """Remove resume metadata files for a completed download."""
    for p in download_dir.glob(f"*.{etag}.resume.json"):
        try:
            p.unlink()
        except OSError:
            pass


def _ensure_model_file(progress_callback: Optional[Callable[[str], None]] = None) -> bool:
    """Download model.safetensors with Ctrl+C-safe resume.

    HF hub deletes incomplete files on interruption AND may clean
    up orphaned blobs on subsequent ``snapshot_download`` calls, so
    we store the partial OUTSIDE the HF hub cache tree in
    ``~/.voice-typer/downloads/`` where nothing else touches it.

    A small ``.resume.json`` metadata file stores the etag, expected
    size, and URL so we can verify the file is still valid across
    reboots and CDN changes.

    On completion we place a copy in the HF blob cache so
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
    try:
        resp = _requests.head(url, headers=headers, timeout=30, allow_redirects=True)
    except _requests.exceptions.ConnectionError as e:
        log.warning("[PARAKEET] HEAD request failed (connection reset): %s", e)
        return False
    except _requests.exceptions.RequestException as e:
        log.warning("[PARAKEET] HEAD request failed: %s", e)
        return False
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

    # ── Download paths (outside HF hub cache) ──────────────────────
    download_dir = _config_dir() / "downloads"
    download_dir.mkdir(parents=True, exist_ok=True)

    partial_path = download_dir / f"{filename}.{etag}.partial"
    resume_meta_path = download_dir / f"{filename}.{etag}.resume.json"

    # ── HF blob path (for from_pretrained to find) ─────────────────
    cache_dir = _config_dir() / "huggingface" / "hub"
    storage_folder = cache_dir / f"models--{_PARAKERT_MODEL_ID.replace('/', '--')}"
    blobs_dir = storage_folder / "blobs"
    blob_path = blobs_dir / etag

    # Check if blob already exists in HF cache
    if blob_path.exists() and blob_path.stat().st_size >= expected_size:
        return True

    # Fallback: search blobs dir by size (etag may differ across CDN responses)
    if expected_size > 0 and blobs_dir.exists():
        for f in blobs_dir.iterdir():
            if f.is_file() and f.stat().st_size >= expected_size:
                log.info("[PARAKEET] Found existing blob by size match: %s", f.name)
                return True

    # Check if we already have a complete download in our own dir
    if partial_path.exists() and partial_path.stat().st_size >= expected_size:
        blobs_dir.mkdir(parents=True, exist_ok=True)
        partial_path.replace(blob_path)
        log.info("[PARAKEET] %s already complete in download dir, moved to HF cache", filename)
        _cleanup_resume_meta(download_dir, etag)
        return True

    # Resume from partial
    start_byte = 0
    if partial_path.exists():
        start_byte = partial_path.stat().st_size
        if start_byte >= expected_size:
            blobs_dir.mkdir(parents=True, exist_ok=True)
            partial_path.replace(blob_path)
            _cleanup_resume_meta(download_dir, etag)
            return True
        log.info("[PARAKEET] Resuming %s from byte %d / %d", filename, start_byte, expected_size)

    # Write resume metadata
    _write_resume_meta(resume_meta_path, {
        "etag": etag,
        "url": url,
        "expected_size": expected_size,
    })

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

    # Complete → copy to HF blob cache + clean up meta
    blobs_dir.mkdir(parents=True, exist_ok=True)
    import shutil
    shutil.copy2(partial_path, blob_path)
    if blob_path.exists() and blob_path.stat().st_size >= expected_size:
        log.info("[PARAKEET] %s downloaded (%d bytes)", filename, blob_path.stat().st_size)
        partial_path.unlink(missing_ok=True)
        _cleanup_resume_meta(download_dir, etag)
        return True
    blob_path.unlink(missing_ok=True)
    log.error("[PARAKEET] Downloaded file is incomplete or missing after copy")
    return False


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

            # 1. Snapshot-download all files (config, tokenizer, model weights).
            #    Uses HF Hub's built-in cache with resume support.
            #    Custom _ensure_model_file is NOT used here — it would duplicate
            #    the 2.3GB download and cause cache structure issues with
            #    from_pretrained (which expects proper snapshot symlinks).
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
                    log.info("[PARAKEET] Downloading model files...")
                    snapshot_download(
                        repo_id=_PARAKERT_MODEL_ID,
                        resume_download=True,
                    )
                except Exception as exc:
                    log.error("[PARAKEET] Model download failed: %s", exc)
                    if progress_callback:
                        progress_callback(f"Download failed: {exc}")
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

                # Suppress Transformers' tqdm progress bar (printed directly to
                # stderr, bypasses our log formatter so it can't be colored).
                from contextlib import redirect_stderr
                import io as _io

                _stderr_buf = _io.StringIO()
                with redirect_stderr(_stderr_buf):
                    self._processor = AutoProcessor.from_pretrained(
                        _PARAKERT_MODEL_ID,
                        local_files_only=True,
                    )
                    self._model = AutoModelForTDT.from_pretrained(
                        _PARAKERT_MODEL_ID,
                        dtype=torch.float16 if effective_device == "cuda" else torch.float32,
                        device_map=effective_device,
                        low_cpu_mem_usage=True,
                        local_files_only=True,
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

        Long audio (>CHUNK_SECONDS) is split into overlapping chunks
        to stay within the Conformer encoder's input-length limit.
        """
        with self._lock:
            if self._model is None or self._processor is None:
                raise RuntimeError(
                    "Parakeet model not loaded. Call load() first or check logs."
                )

            if len(audio) == 0:
                return ""

            duration = len(audio) / 16000
            if duration <= _CHUNK_SECONDS:
                return self._transcribe_segment(audio)

            chunks = self._split_audio(audio, _CHUNK_SECONDS, _CHUNK_OVERLAP_SECONDS)
            log.info("[PARAKEET] Splitting %.1fs audio into %d chunks", duration, len(chunks))

            results = []
            for i, chunk in enumerate(chunks):
                log.info("[PARAKEET] Transcribing chunk %d/%d (%.1fs)", i + 1, len(chunks), len(chunk) / 16000)
                text = self._transcribe_segment(chunk)
                if text:
                    results.append(text)

            if not results:
                return ""

            merged = self._merge_chunks(results)
            return merged

    def _transcribe_segment(self, audio: np.ndarray) -> str:
        """Transcribe one audio segment (assumed to be within model limits)."""
        import torch

        try:
            inputs = self._processor(
                [audio],
                sampling_rate=16000,
                return_tensors="pt",
            )
            inputs.to(device=self._model.device, dtype=self._model.dtype)
            output = self._model.generate(
                **inputs,
                return_dict_in_generate=True,
                max_new_tokens=256,
            )
            text = self._processor.decode(
                output.sequences,
                skip_special_tokens=True,
            )
            if isinstance(text, list):
                text = text[0] if text else ""
            text = text.strip()
        except Exception as exc:
            log.error("[PARAKEET] Segment transcription failed: %s", exc)
            return ""

        # English-only filter: only active when language="en" is configured
        if self.language == "en" and not _is_likely_english(text):
            return ""

        rms = float(np.sqrt(np.mean(np.square(audio), dtype=np.float64)))
        if should_reject_low_audio_hallucination(text, rms):
            log.warning("[PARAKEET] Rejected likely hallucination: %r", text[:80])
            return ""

        return text

    def _split_audio(
        self, audio: np.ndarray, chunk_sec: float, overlap_sec: float
    ) -> list[np.ndarray]:
        """Split audio into overlapping chunks."""
        sr = 16000
        chunk_len = int(chunk_sec * sr)
        overlap_len = int(overlap_sec * sr)
        step = chunk_len - overlap_len
        chunks: list[np.ndarray] = []
        start = 0
        while start < len(audio):
            end = min(start + chunk_len, len(audio))
            chunks.append(audio[start:end])
            if end == len(audio):
                break
            start += step
        return chunks

    def _merge_chunks(self, texts: list[str]) -> str:
        """Concatenate chunk transcriptions.  Chunks are non-overlapping
        so simple concatenation produces exact text with no duplication."""
        return " ".join(t for t in texts if t).strip()

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
        """Core transcription without lock or error handling for fallback.

        Applies the same chunked approach as transcribe() for long audio.
        """
        duration = len(audio) / 16000
        if duration <= _CHUNK_SECONDS:
            return self._transcribe_segment_unlocked(audio)

        chunks = self._split_audio(audio, _CHUNK_SECONDS, _CHUNK_OVERLAP_SECONDS)
        results = []
        for chunk in chunks:
            text = self._transcribe_segment_unlocked(chunk)
            if text:
                results.append(text)
        if not results:
            return ""
        return self._merge_chunks(results)

    def _transcribe_segment_unlocked(self, audio: np.ndarray) -> str:
        """Transcribe one segment without lock (for fallback path)."""
        import torch
        try:
            inputs = self._processor(
                [audio],
                sampling_rate=16000,
                return_tensors="pt",
            )
            inputs.to(device=self._model.device, dtype=self._model.dtype)
            output = self._model.generate(
                **inputs,
                return_dict_in_generate=True,
                max_new_tokens=256,
            )
            text = self._processor.decode(
                output.sequences,
                skip_special_tokens=True,
            )
            if isinstance(text, list):
                text = text[0] if text else ""
            text = text.strip()
        except Exception:
            return ""

        # English-only filter: only active when language="en" is configured
        if self.language == "en" and not _is_likely_english(text):
            return ""

        rms = float(np.sqrt(np.mean(np.square(audio), dtype=np.float64)))
        if should_reject_low_audio_hallucination(text, rms):
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
