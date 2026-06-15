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
            "[PARAKEET] Rejected non-English output (%.0f%% non-Latin chars): %s",
            ratio * 100, ascii(text[:80]),
        )
        return False
    return True

_PARAKERT_MODEL_ID = "nvidia/parakeet-tdt-0.6b-v3"

# Parakeet's Conformer encoder has a practical limit of ~30s of audio.
# Longer recordings are split into overlapping chunks.  3s overlap gives
# the model audio context at boundaries so it doesn't hallucinate repeated
# text at chunk starts.  The merge step skips the overlapped text portion
# from each subsequent chunk.
_CHUNK_SECONDS = 25
_CHUNK_OVERLAP_SECONDS = 3


class ParakeetEngine:
    """Wraps NVIDIA Parakeet TDT v3 ASR model via Transformers.

    Implements TranscriberProtocol so the app can swap backends transparently.
    Model weights are auto-downloaded from HuggingFace on first load.
    """

    # Cache these class-level so they're imported ONCE, not per instance.
    _imports_loaded = False
    _AutoModelForTDT = None
    _AutoProcessor = None
    _torch = None
    _hf_home_set = False

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
        self._ensure_hf_env()

    @classmethod
    def _ensure_hf_env(cls):
        if cls._hf_home_set:
            return
        try:
            from voice_typer.server.asr_setup import ensure_hf_env
            ensure_hf_env()
            cls._hf_home_set = True
        except Exception:
            pass

    @classmethod
    def _ensure_imports(cls):
        if cls._imports_loaded:
            return
        try:
            import torch
            from transformers import AutoModelForTDT, AutoProcessor
            cls._torch = torch
            cls._AutoModelForTDT = AutoModelForTDT
            cls._AutoProcessor = AutoProcessor
            cls._imports_loaded = True
        except ImportError:
            cls._imports_loaded = False

    @staticmethod
    def _is_cached() -> bool:
        """Quick check if model is in HF cache without calling snapshot_download."""
        from voice_typer.server.asr_setup import _config_dir
        cache_root = _config_dir() / "huggingface" / "hub"
        model_dir = cache_root / f"models--{_PARAKERT_MODEL_ID.replace('/', '--')}"
        snapshots = model_dir / "snapshots"
        if not snapshots.is_dir():
            return False
        try:
            for entry in snapshots.iterdir():
                if entry.is_dir() and (entry / "model.safetensors").exists():
                    return True
        except OSError:
            pass
        return False

    # ── TranscriberProtocol ──────────────────────────────────────────

    @property
    def is_loaded(self) -> bool:
        with self._lock:
            return self._model is not None and self._processor is not None

    def load(self, progress_callback: Optional[Callable[[str], None]] = None) -> bool:
        """Download (if needed) and load the Parakeet model.

        Weights land in ``~/.voice-typer/huggingface/hub/``.
        Returns True on success, False on failure.
        """
        # Ensure torch + transformers are imported before any model ops.
        self._ensure_imports()
        if not self._imports_loaded:
            log.warning("[PARAKEET] torch/transformers not installed, cannot load")
            if progress_callback:
                progress_callback("Missing dependencies: torch + transformers")
            return False

        with self._lock:
            if self._model is not None:
                return True

            # Quick cache check — avoids calling snapshot_download entirely
            # when model is already on disk.
            if not self._is_cached():
                try:
                    from huggingface_hub import snapshot_download

                    if progress_callback:
                        progress_callback("Downloading Parakeet model files...")
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

                if not self._is_cached():
                    log.error("[PARAKEET] Model not found in cache after download")
                    if progress_callback:
                        progress_callback("Model not found in cache after download")
                    return False

            # Load model from cache
            try:
                if progress_callback:
                    progress_callback("Loading Parakeet TDT v3 model...")

                log.info("[PARAKEET] Loading model (device=%s)...", self.device)
                effective_device = self.device
                if effective_device == "cuda" and not self._torch.cuda.is_available():
                    log.warning("[PARAKEET] CUDA requested but not available, falling back to CPU")
                    effective_device = "cpu"

                # Suppress Transformers' tqdm progress bar
                from contextlib import redirect_stderr
                import io as _io

                _stderr_buf = _io.StringIO()
                with redirect_stderr(_stderr_buf):
                    self._processor = self._AutoProcessor.from_pretrained(
                        _PARAKERT_MODEL_ID,
                        local_files_only=True,
                    )
                    self._model = self._AutoModelForTDT.from_pretrained(
                        _PARAKERT_MODEL_ID,
                        dtype=self._torch.float16 if effective_device == "cuda" else self._torch.float32,
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
        """Concatenate chunk transcriptions, skipping overlap text.

        Chunks have ``_CHUNK_OVERLAP_SECONDS`` of overlapping audio at
        each boundary.  For each subsequent chunk we skip the first
        (overlap_sec / chunk_sec) fraction of words — this removes the
        text that corresponds to the overlapping audio region that was
        already transcribed in the previous chunk.
        """
        if len(texts) <= 1:
            return texts[0] if texts else ""

        ratio = _CHUNK_OVERLAP_SECONDS / _CHUNK_SECONDS  # e.g. 3/25 = 0.12
        result = texts[0]
        for text in texts[1:]:
            words = text.split()
            skip = min(int(len(words) * ratio), len(words) - 1)
            tail = " ".join(words[skip:]) if skip > 0 else text
            if tail:
                result += " " + tail
        return result.strip()

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
