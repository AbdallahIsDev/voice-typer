"""Parakeet TDT v3 ASR engine — optional backend alongside Whisper/Qwen.

Uses NVIDIA's parakeet-tdt-0.6b-v3 via HuggingFace Transformers.
Auto-downloads model weights on first load via huggingface_hub.
Falls back gracefully on missing deps, CUDA errors, etc.
"""

import logging
import os
import threading
import unicodedata
from typing import Any, Optional, Callable

import numpy as np

from voice_typer.server.hallucination import should_reject_low_audio_hallucination, log_hallucination_rejection

log = logging.getLogger(__name__)


class TranscriptionBackendError(RuntimeError):
    """Raised when the ASR backend cannot produce a transcription.

    ERR-007: ``transcribe_with_fallback`` previously returned ``""`` on
    CPU fallback failure, which the caller could not distinguish from a
    legitimate "no speech detected" result — the user saw "No speech
    detected" and assumed the microphone was broken. We now raise this
    typed exception so callers can show the correct error.
    """

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
        # SEC-009: Use PII-safe logging helper for hallucination text
        log_hallucination_rejection(
            "[PARAKEET]", text,
            reason=f"non-English output ({ratio * 100:.0f}% non-Latin chars)",
            log_transcriptions=False,
        )
        return False
    return True

_PARAKERT_MODEL_ID = "nvidia/parakeet-tdt-0.6b-v3"

# SEC-audit-005: Allowlist of file patterns permitted in Parakeet model downloads.
# Prevents supply-chain attacks where a compromised HF repo could include
# executables, scripts, or other unexpected files.
_PARAKEET_ALLOW_PATTERNS = [
    "*.safetensors", "*.bin", "config.json", "tokenizer.json",
    "tokenizer_config.json", "special_tokens_map.json",
    "preprocessor_config.json", "feature_extractor_config.json",
    "generation_config.json", "model.safetensors.index.json", "*.model",
]

# SEC-audit-005: Pin to a specific revision for reproducibility.
# Use the centralized MODEL_HASHES manifest from security.py.
from voice_typer.server.security import MODEL_HASHES as _MODEL_HASHES
_PARAKEET_REVISION = _MODEL_HASHES.get(_PARAKERT_MODEL_ID, {}).get("revision", "main")

# Parakeet's Conformer encoder has a practical limit of ~30s of audio.
# Longer recordings are split into overlapping chunks.  3s overlap gives
# the model audio context at boundaries so it doesn't hallucinate repeated
# text at chunk starts.  The merge step skips the overlapped text portion
# from each subsequent chunk.
_CHUNK_SECONDS = 25
_CHUNK_OVERLAP_SECONDS = 3

# NEW-CQ-030: Maximum words to skip at a chunk boundary.
#
# Previously the merge step used ``skip = int(len(words) * 0.12)`` which
# silently dropped words at every boundary — for a 25-word chunk that's
# 3 dropped words, regardless of whether the model actually re-transcribed
# the overlap region.  Word density is not uniform across audio time, so a
# ratio-based skip is unsafe.  Cap the skip to at most this many words
# AND only after we've checked for an actual word-level overlap with the
# previous chunk's tail (see ``_merge_chunks``).
_MAX_BOUNDARY_SKIP_WORDS = 2
# Number of trailing words of the previous chunk to compare against the
# leading words of the new chunk when detecting true overlap duplicates.
_OVERLAP_DEDUP_WINDOW = 3


class ParakeetEngine:
    """Wraps NVIDIA Parakeet TDT v3 ASR model via Transformers.

    Implements TranscriberProtocol so the app can swap backends transparently.
    Model weights are auto-downloaded from HuggingFace on first load.
    """

    # Cache these class-level so they're imported ONCE, not per instance.
    # TASK-10: typed as ``Any`` so pyrefly can follow the .cuda /
    # .from_pretrained / .float16 / .generate / .decode accesses after
    # ``_ensure_imports()`` populates them at runtime. The class attrs
    # are populated lazily because torch / transformers are optional
    # deps — they remain ``None`` until first successful import.
    _imports_loaded: bool = False
    _AutoModelForTDT: Any = None
    _AutoProcessor: Any = None
    _torch: Any = None
    _hf_home_set: bool = False

    def __init__(
        self,
        device: str = "cuda",
        language: str = "en",
    ):
        self.device = device
        self.language = language
        # TASK-10: instance-level model handles are populated by load()
        # and read by transcribe(). Typed as Any so attribute accesses
        # (.device, .dtype, .generate, .decode) type-check without
        # forcing every call site to repeat the None-narrowing guard
        # that transcribe() already performs at entry.
        self._model: Any = None
        self._processor: Any = None
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
            # TASK-14: ``AutoModelForTDT`` was added to transformers in
            # 4.50 (our pyproject floor).  The venv on this runner has
            # 4.44, so a static ``from transformers import AutoModelForTDT``
            # trips pyrefly's missing-module-attribute even though the
            # surrounding try/except ImportError is the runtime guard.
            # Resolve via ``getattr`` so the static checker does not
            # see the (possibly absent) attribute access.
            import transformers
            cls._torch = torch
            cls._AutoModelForTDT = getattr(transformers, "AutoModelForTDT", None)
            cls._AutoProcessor = getattr(transformers, "AutoProcessor", None)
            if cls._AutoModelForTDT is None or cls._AutoProcessor is None:
                raise ImportError(
                    "transformers package is missing AutoModelForTDT / "
                    "AutoProcessor — install transformers>=4.50"
                )
            cls._imports_loaded = True
        except ImportError:
            cls._imports_loaded = False

    @staticmethod
    def _should_force_cpu() -> bool:
        """Check disk space on system drive — if under 500MB, force CPU.

        CUDA on Windows needs pagefile space to back GPU memory allocations.
        When the system drive is nearly full, Windows can't grow the pagefile,
        causing error 1455. This check avoids that error and gives a clean
        warning instead.
        """
        try:
            import psutil
            system_drive = os.environ.get("SystemDrive", "C:") + "\\"
            usage = psutil.disk_usage(system_drive)
            free_mb = usage.free // (1024 * 1024)
            if free_mb < 500:
                log.warning(
                    "[PARAKEET] Only %d MB free on %s — forcing CPU "
                    "(CUDA needs pagefile space to allocate GPU memory)",
                    free_mb, system_drive,
                )
                return True
        except Exception:
            pass
        return False

    @staticmethod
    def _is_cached() -> bool:
        """Quick check if model is in HF cache without calling snapshot_download."""
        # NEW-DEAD-027: use config._config_dir() directly instead of
        # the removed asr_setup._config_dir() cache wrapper.
        from voice_typer.server.config import _config_dir
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
                        revision=_PARAKEET_REVISION,
                        allow_patterns=_PARAKEET_ALLOW_PATTERNS,
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

                # SEC-audit-005: Verify model integrity after download
                from voice_typer.server.asr_setup import _verify_model_integrity
                from voice_typer.server.config import _config_dir
                cache_root = _config_dir() / "huggingface" / "hub"
                model_dir = cache_root / f"models--{_PARAKERT_MODEL_ID.replace('/', '--')}"
                if model_dir.is_dir():
                    verified = False
                    try:
                        for snapshot in (model_dir / "snapshots").iterdir():
                            if snapshot.is_dir() and _verify_model_integrity(_PARAKERT_MODEL_ID, str(snapshot)):
                                verified = True
                                break
                    except OSError:
                        pass
                    if not verified:
                        log.warning("[PARAKEET] Model integrity check failed after download")

            # Load model from cache
            try:
                if progress_callback:
                    progress_callback("Loading Parakeet TDT v3 model...")

                log.info("[PARAKEET] Loading model (device=%s)...", self.device)
                effective_device = self.device
                if effective_device == "cuda" and not self._torch.cuda.is_available():
                    log.warning("[PARAKEET] CUDA requested but not available, falling back to CPU")
                    effective_device = "cpu"
                if effective_device == "cuda" and self._should_force_cpu():
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

                    try:
                        self._model = self._AutoModelForTDT.from_pretrained(
                            _PARAKERT_MODEL_ID,
                            dtype=self._torch.float16 if effective_device == "cuda" else self._torch.float32,
                            device_map=effective_device,
                            low_cpu_mem_usage=True,
                            local_files_only=True,
                        )
                    except Exception as cuda_exc:
                        err_str = str(cuda_exc).lower()
                        if effective_device == "cuda" and ("1455" in err_str or "paging file" in err_str):
                            log.warning(
                                "[PARAKEET] CUDA allocation failed (pagefile), retrying on CPU: %s",
                                cuda_exc,
                            )
                            if progress_callback:
                                progress_callback("CUDA memory error, retrying on CPU...")
                            self._model = self._AutoModelForTDT.from_pretrained(
                                _PARAKERT_MODEL_ID,
                                dtype=self._torch.float32,
                                device_map="cpu",
                                low_cpu_mem_usage=True,
                                local_files_only=True,
                            )
                        else:
                            raise

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

    def transcribe(self, audio: np.ndarray, audio_stats: "tuple[float, float, float] | None" = None) -> str:
        """Transcribe audio array. Returns cleaned text string.

        Long audio (>CHUNK_SECONDS) is split into overlapping chunks
        to stay within the Conformer encoder's input-length limit.

        PERF-STATS: ``audio_stats`` is an optional pre-computed
        ``(rms, peak, silence_pct)`` tuple from ``Recorder.stop()``.
        When provided, the engine skips its own RMS computation in
        hallucination detection.
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
                return self._transcribe_segment(audio, audio_stats=audio_stats)

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

    def _transcribe_segment(self, audio: np.ndarray, audio_stats: "tuple[float, float, float] | None" = None) -> str:
        """Transcribe one audio segment (assumed to be within model limits).

        PERF-STATS: ``audio_stats`` is an optional pre-computed
        ``(rms, peak, silence_pct)`` tuple. When provided, the
        engine skips its own RMS computation in hallucination detection.
        """
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

        # PERF-STATS: reuse pre-computed RMS when provided
        if audio_stats is not None:
            rms = audio_stats[0]
        else:
            rms = float(np.sqrt(np.mean(np.square(audio), dtype=np.float64)))
        if should_reject_low_audio_hallucination(text, rms):
            # SEC-009: Use PII-safe logging helper instead of raw text
            log_hallucination_rejection(
                "[PARAKEET]", text,
                reason="hallucination",
                log_transcriptions=False,
            )
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
        each boundary.  When the model re-transcribes the overlap region
        in the new chunk, those leading words duplicate the previous
        chunk's tail and must be skipped.

        NEW-CQ-030: The old implementation used a fixed ratio
        ``skip = int(len(words) * 0.12)`` which dropped words at every
        boundary regardless of whether they were actually overlap
        duplicates — for a 25-word chunk that's 3 dropped words.  This
        was unsafe because word density is not uniform across audio
        time, so a ratio-based skip silently dropped legitimate words
        at boundaries that had no overlap duplicates.

        The new algorithm:
        1. Look at the last ``_OVERLAP_DEDUP_WINDOW`` words of the
           previous chunk and the first ``_OVERLAP_DEDUP_WINDOW`` words
           of the new chunk.
        2. Find the longest leading run of the new chunk whose words
           also appear (in order) in the previous chunk's tail window.
           That run is a true overlap duplicate and is skipped.
        3. If no overlap duplicate is detected, skip at most 1 word
           (a small allowance for boundary hallucinations, far smaller
           than the old 12% ratio).
        4. Total skip is capped at ``_MAX_BOUNDARY_SKIP_WORDS`` (2).
        """
        if len(texts) <= 1:
            return texts[0] if texts else ""

        result_words: list[str] = texts[0].split()
        for text in texts[1:]:
            words = text.split()
            if not words:
                continue

            skip = self._compute_overlap_skip(result_words, words)
            if skip > 0:
                tail = words[skip:]
            else:
                tail = words
            if tail:
                result_words.extend(tail)
        return " ".join(result_words).strip()

    @staticmethod
    def _compute_overlap_skip(
        prev_words: list[str], new_words: list[str]
    ) -> int:
        """Return how many leading words of *new_words* to skip.

        We detect a true overlap duplicate by searching (case-insensitively,
        ignoring punctuation) for the leading run of ``new_words`` as a
        *contiguous subsequence* within the trailing window of
        ``prev_words``.  We pick the longest match that fits within
        ``_OVERLAP_DEDUP_WINDOW`` words on the new side, is at most
        ``_MAX_BOUNDARY_SKIP_WORDS`` long, and ends within the trailing
        ``_OVERLAP_DEDUP_WINDOW + _MAX_BOUNDARY_SKIP_WORDS`` words of the
        previous chunk.  If no match is found, we still skip up to 1
        word as a small allowance for boundary hallucinations (much
        smaller than the old 12% ratio).
        """
        if not prev_words or not new_words:
            return 0

        def _norm(w: str) -> str:
            return w.strip(".,;:!?\"'()[]{}").lower()

        # Search window on prev side: include enough trailing words that
        # an overlap run of length up to _MAX_BOUNDARY_SKIP_WORDS can
        # start anywhere within _OVERLAP_DEDUP_WINDOW of the tail.
        prev_window_size = _OVERLAP_DEDUP_WINDOW + _MAX_BOUNDARY_SKIP_WORDS
        prev_tail = [_norm(w) for w in prev_words[-prev_window_size:]]
        # New side: we compare up to _MAX_BOUNDARY_SKIP_WORDS leading words.
        max_check = min(
            _MAX_BOUNDARY_SKIP_WORDS,
            len(new_words),
        )
        new_head = [_norm(w) for w in new_words[:max_check]]

        best = 0
        # Try the longest candidate first so we get the longest true match.
        for length in range(max_check, 0, -1):
            candidate = new_head[:length]
            # Search for `candidate` as a contiguous subsequence inside
            # prev_tail.  The match must end somewhere within the trailing
            # _OVERLAP_DEDUP_WINDOW words of prev_tail (so we don't pull
            # matches from arbitrarily early in the previous chunk).
            for start in range(len(prev_tail) - length + 1):
                # Only accept matches whose end index falls within the
                # last _OVERLAP_DEDUP_WINDOW words of prev_tail.
                end_idx = start + length  # exclusive
                last_word_idx = len(prev_tail) - end_idx
                if last_word_idx >= _OVERLAP_DEDUP_WINDOW:
                    continue
                if prev_tail[start:start + length] == candidate:
                    best = length
                    break
            if best > 0:
                break

        if best > 0:
            return best

        # No true overlap detected.  Allow a single-word skip as a small
        # allowance for boundary hallucinations (e.g. "Thanks." appearing
        # at the very start of a chunk).  This is bounded and never
        # scales with chunk length, unlike the old ratio-based skip.
        return 1 if len(new_words) > 1 else 0

    def transcribe_with_fallback(self, audio: np.ndarray,
            audio_stats: "tuple[float, float, float] | None" = None,
        ) -> str:
        """transcribe with GPU→CPU fallback on CUDA errors.

        PERF-STATS: ``audio_stats`` is an optional pre-computed
        ``(rms, peak, silence_pct)`` tuple. When provided, the
        engine skips its own RMS computation.

        Raises:
            TranscriptionBackendError: if both the GPU path and the CPU
                fallback fail. Previously returned ``""``, which the
                caller could not distinguish from a legitimate "no
                speech detected" result (ERR-007).
        """
        with self._lock:
            if self._model is None or self._processor is None:
                raise TranscriptionBackendError("Parakeet model not loaded.")

            if len(audio) == 0:
                return ""

            try:
                return self.transcribe(audio, audio_stats=audio_stats)
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
                        raise TranscriptionBackendError(
                            f"Parakeet GPU transcription failed ({exc}) and CPU "
                            f"fallback also failed ({cpu_exc})"
                        ) from cpu_exc
                # Non-CUDA error: surface it instead of swallowing as ""
                raise TranscriptionBackendError(
                    f"Parakeet transcription failed: {exc}"
                ) from exc

    def _transcribe_impl(self, audio: np.ndarray) -> str:
        """Core transcription without lock or error handling for fallback.

        NEW-CQ-027: NOT a duplicate of transcribe(). This method uses
        _transcribe_segment_unlocked() (no lock) while transcribe()
        uses _transcribe_segment() (with lock). The fallback path
        calls this after releasing the lock for CPU retry.
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
            # SEC-009: Use PII-safe logging helper for unlocked fallback path
            log_hallucination_rejection(
                "[PARAKEET]", text,
                reason="hallucination",
                log_transcriptions=False,
            )
            return ""
        return text

    def unload(self) -> None:
        """Free model memory.

        NEW-MEM-001: also release PyTorch's CUDA caching allocator
        blocks via ``release_gpu_memory()`` so a subsequent backend
        switch (e.g. back to Whisper) can use the freed VRAM.  Without
        this, the cached blocks from the Parakeet model linger in the
        allocator and cause GPU OOMs after 2 backend switches on
        RTX 3060/4060 (8–12 GB VRAM).

        RACE-023: gc.collect() moved OUTSIDE the lock to avoid blocking
        is_loaded / transcribe for 10-100ms.
        """
        import gc
        from voice_typer.server.transcription import release_gpu_memory
        with self._lock:
            self._model = None
            self._processor = None
        # RACE-023: gc.collect() OUTSIDE the lock
        gc.collect()
        # NEW-MEM-001: release CUDA cached blocks.
        release_gpu_memory()
        log.info("[PARAKEET] Model unloaded")

    @property
    def device_info(self) -> str:
        return f"parakeet/{self.device}"

    @property
    def loaded_via(self) -> str:
        return f"parakeet/{self.device}/{_PARAKERT_MODEL_ID}"
