"""Parakeet TDT v3 ASR engine — optional backend alongside Whisper/Qwen.

Uses NVIDIA's parakeet-tdt-0.6b-v3 via HuggingFace Transformers.
Auto-downloads model weights on first load via huggingface_hub.
Falls back gracefully on missing deps, CUDA errors, etc.
"""

import logging
import os
import threading
import time
import unicodedata
from collections.abc import Callable
from typing import Any

import numpy as np

from voice_typer.server.branding import APP_NAME
from voice_typer.server.hallucination import log_hallucination_rejection, should_reject_low_audio_hallucination
from voice_typer.server.security import MODEL_HASHES as _MODEL_HASHES

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
            "[PARAKEET]",
            text,
            reason=f"non-English output ({ratio * 100:.0f}% non-Latin chars)",
            log_transcriptions=False,
        )
        return False
    return True


_PARAKERT_MODEL_ID = "nvidia/parakeet-tdt-0.6b-v3"

# PW-4: approximate model weight size in MB for MB/s read-speed logging.
# The model.safetensors file is ~2.4 GB on disk.
_PARAKERT_WEIGHTS_MB = 2400

# SEC-audit-005 / CRIT-5 / SEC-2: allow-list imported from the shared
# ``_model_integrity`` module so ``parakeet_engine`` and ``asr_setup``
# can never drift out of sync.  See ``_model_integrity.py`` for the
# sync requirement with ``model_hashes.json`` — pinned files in the
# manifest MUST be a subset of these allow-patterns, otherwise
# ``verify_model_integrity()`` hard-fails on every download.
from voice_typer.server._model_integrity import ALLOW_PATTERNS_PARAKEET as _PARAKEET_ALLOW_PATTERNS  # noqa: E402

# SEC-audit-005: Pin to a specific revision for reproducibility.
# Use the centralized MODEL_HASHES manifest from security.py.
_PARAKEET_REVISION = _MODEL_HASHES.get(_PARAKERT_MODEL_ID, {}).get("revision", "main")

# Parakeet's Conformer encoder has a practical limit of ~30s of audio.
# Longer recordings are split into overlapping chunks.  3s overlap gives
# the model audio context at boundaries so it doesn't hallucinate repeated
# text at chunk starts.  The merge step skips the overlapped text portion
# from each subsequent chunk.
_CHUNK_SECONDS = 25
_CHUNK_OVERLAP_SECONDS = 3

# NEW-CQ-030 / RW-T1: Maximum words to skip at a chunk boundary.
#
# Previously the merge step used ``skip = int(len(words) * 0.12)`` which
# silently dropped words at every boundary — for a 25-word chunk that's
# 3 dropped words, regardless of whether the model actually re-transcribed
# the overlap region.  Word density is not uniform across audio time, so a
# ratio-based skip is unsafe.  Cap the skip to at most this many words
# AND only after we've checked for an actual word-level overlap with the
# previous chunk's tail (see ``_merge_chunks``).
#
# RW-T1 (2025): the previous "allowance" of 1 word per boundary even when
# no overlap was detected silently dropped up to 14 legitimate words per
# 5-minute recording (one per chunk boundary).  The allowance is now 0 —
# boundary hallucinations like "Thanks." at chunk starts are already
# filtered upstream by ``should_reject_low_audio_hallucination`` in
# ``_transcribe_segment``.  This constant now bounds only *true* overlap
# duplicate runs that are actually found in the previous chunk's tail.
_MAX_BOUNDARY_SKIP_WORDS = 2
# Number of trailing words of the previous chunk to compare against the
# leading words of the new chunk when detecting true overlap duplicates.
_OVERLAP_DEDUP_WINDOW = 3


def _cleanup_hf_cache_dir(model_dir: "Any") -> None:
    """G4-CR-06 / cache cleanup: best-effort delete a tampered HF cache dir.

    EC-FIX-8: this local helper now delegates to the canonical
    ``voice_typer.server.asr_utils.cleanup_hf_cache_dir`` so the
    cleanup logic lives in one place (previously the same body was
    duplicated 3x across ``transcription.py``,
    ``asr_setup.py``, and here — EC-17 finding #2).

    The ``model_dir`` argument is preserved for backward compatibility
    with the existing call site in ``ParakeetEngine.load()`` (which
    already resolved the path from ``_PARAKERT_MODEL_ID``).  The
    canonical helper re-resolves the path from the repo_id
    (``_PARAKERT_MODEL_ID``) — the two paths are guaranteed identical
    because both use ``_config_dir() / "huggingface" / "hub" /
    f"models--{_PARAKERT_MODEL_ID.replace('/', '--')}"``.  The
    ``model_dir`` argument is therefore accepted but ignored.

    Best-effort: logs but does not raise if the cleanup itself fails
    (e.g. file is locked on Windows, permission denied on POSIX).  The
    integrity hard-fail (``return False`` / ``RuntimeError`` in the
    caller) is the security gate; this cleanup is just hygiene so a
    retry doesn't silently re-load the same tampered files.
    """
    from voice_typer.server.asr_utils import cleanup_hf_cache_dir

    # ``model_dir`` is intentionally ignored — see docstring above.
    cleanup_hf_cache_dir(_PARAKERT_MODEL_ID, log_prefix="[PARAKEET]")


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
        config: Any = None,
    ):
        self.device = device
        self.language = language
        # G4-H-04 (Session 7 — Group 4): optional Config reference
        # consulted by ``load()`` to gate HuggingFace downloads on
        # explicit user consent (``config.huggingface_consent``).
        # ``None`` is treated as "consent not given" (safe default per
        # GDPR Art. 6/13).  The registry / model_manager passes the
        # live Config when constructing the engine so the gate is
        # enforced in production; tests can omit it to exercise the
        # cache-hit / already-loaded fast paths.
        self.config = config
        # TASK-10: instance-level model handles are populated by load()
        # and read by transcribe(). Typed as Any so attribute accesses
        # (.device, .dtype, .generate, .decode) type-check without
        # forcing every call site to repeat the None-narrowing guard
        # that transcribe() already performs at entry.
        self._model: Any = None
        self._processor: Any = None
        # G4-M-44: one-time tray notification flag for CUDA→CPU
        # transcription fallback.  Reset to ``False`` on every
        # successful ``load()`` so a fallback after the next reload
        # re-notifies the user (the user may have restarted their GPU
        # driver / freed VRAM in the meantime).
        self._cpu_fallback_notified: bool = False
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
            # PVT-G5-040: previously a silent ``except: pass``. Log at
            # DEBUG (non-fatal — the engine still works without HF env
            # tweaks) and include exc_info so a non-trivial failure is
            # visible in the log file when debugging.
            log.debug("[PARAKEET] ensure_hf_env failed (non-fatal)", exc_info=True)

    @classmethod
    def _ensure_imports(cls):
        if cls._imports_loaded:
            log.info("[PARAKEET] AI libraries already imported — skipping re-import")
            return
        # OBSERVE-001: the torch + transformers import is the single most
        # expensive step on a fresh process (several seconds of CPU work,
        # not disk I/O once prewarm has warmed the OS page cache). It used
        # to be silent, leaving a mysterious gap between "backend
        # registered" and "Loading model". Log each import with its own
        # elapsed time so the gap is fully visible.
        _t0 = time.perf_counter()
        try:
            log.info("[PARAKEET] importing torch (this can take a few seconds on first import)…")
            import torch

            _torch_s = time.perf_counter() - _t0
            log.info("[PARAKEET] torch imported (%.2fs)", _torch_s)

            # TASK-14: ``AutoModelForTDT`` was added to transformers in
            # 4.50 (our pyproject floor).  The venv on this runner has
            # 4.44, so a static ``from transformers import AutoModelForTDT``
            # trips pyrefly's missing-module-attribute even though the
            # surrounding try/except ImportError is the runtime guard.
            # Resolve via ``getattr`` so the static checker does not
            # see the (possibly absent) attribute access.
            _t1 = time.perf_counter()
            log.info("[PARAKEET] importing transformers…")
            import transformers

            _tf_s = time.perf_counter() - _t1
            log.info("[PARAKEET] transformers imported (%.2fs)", _tf_s)
            cls._torch = torch
            cls._AutoModelForTDT = getattr(transformers, "AutoModelForTDT", None)
            cls._AutoProcessor = getattr(transformers, "AutoProcessor", None)
            if cls._AutoModelForTDT is None or cls._AutoProcessor is None:
                raise ImportError(
                    "transformers package is missing AutoModelForTDT / AutoProcessor — install transformers>=4.50"
                )
            cls._imports_loaded = True
            log.info(
                "[PARAKEET] AI libraries imported (torch=%.2fs, transformers=%.2fs, total=%.2fs)",
                _torch_s,
                _tf_s,
                time.perf_counter() - _t0,
            )
        except ImportError:
            cls._imports_loaded = False
            log.warning("[PARAKEET] AI library import failed — torch/transformers not installed?")

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

            system_drive = os.environ.get("SYSTEMDRIVE", "C:") + "\\"
            usage = psutil.disk_usage(system_drive)
            free_mb = usage.free // (1024 * 1024)
            if free_mb < 500:
                log.warning(
                    "[PARAKEET] Only %d MB free on %s — forcing CPU (CUDA needs pagefile space to allocate GPU memory)",
                    free_mb,
                    system_drive,
                )
                return True
        except Exception:
            # PVT-G5-040: previously a silent ``except: pass``. Disk
            # space check is best-effort — failure here just means we
            # won't pre-emptively force CPU, which is non-fatal.
            log.debug("[PARAKEET] _should_force_cpu disk space check failed (non-fatal)", exc_info=True)
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
            # PVT-G5-040: previously a silent ``except OSError: pass``.
            # A transient FS error (e.g. snapshot dir deleted between
            # is_dir() and iterdir()) shouldn't crash the cache probe.
            log.debug("[PARAKEET] _is_cached snapshot iterdir failed (non-fatal)", exc_info=True)
        return False

    # ── TranscriberProtocol ──────────────────────────────────────────

    @property
    def is_loaded(self) -> bool:
        with self._lock:
            return self._model is not None and self._processor is not None

    def load(self, progress_callback: Callable[[str], None] | None = None) -> bool:
        """Download (if needed) and load the Parakeet model.

        Weights land in ``~/.voice-typer/huggingface/hub/``.
        Returns True on success, False on failure.
        """
        # Ensure torch + transformers are imported before any model ops.
        log.info("[PARAKEET] load() entered — importing AI libraries if needed")
        self._ensure_imports()
        if not self._imports_loaded:
            log.warning("[PARAKEET] torch/transformers not installed, cannot load")
            if progress_callback:
                progress_callback("Missing dependencies: torch + transformers")
            return False

        with self._lock:
            if self._model is not None:
                return True

            # G4-M-44: reset the one-time CPU-fallback notification flag
            # on every fresh ``load()``.  A fallback that fired during a
            # previous transcription session must not silently suppress
            # the next session's notification — the user may have
            # restarted their GPU driver or freed VRAM in the meantime,
            # so the next fallback is fresh information worth surfacing.
            self._cpu_fallback_notified = False

            # Quick cache check — avoids calling snapshot_download entirely
            # when model is already on disk.
            _cache_t0 = time.perf_counter()
            _cached = self._is_cached()
            log.info(
                "[PARAKEET] model cache check: cached=%s (%.2fs)",
                _cached,
                time.perf_counter() - _cache_t0,
            )
            if not _cached:
                # G4-H-04 (Session 7 — Group 4): HuggingFace downloads
                # reveal the user's IP to a US-headquartered third party
                # and pull ~2.5 GB over the network.  Require explicit
                # ``huggingface_consent`` before any network call,
                # mirroring ``transcription.py::_pre_download_model``
                # (lines ~821-849) and
                # ``service.py::_require_huggingface_consent``.  When
                # ``self.config`` is ``None`` (degenerate / test path),
                # treat consent as NOT given — safe default per GDPR
                # Art. 6/13.  ``ConsentRequiredError`` is the typed
                # exception the IPC layer ``isinstance``-checks to
                # surface a consent dialog instead of a generic error
                # toast.
                cfg = self.config
                consent = False if cfg is None else bool(getattr(cfg, "huggingface_consent", False))
                if not consent:
                    log.warning(
                        "[PARAKEET] HuggingFace consent not given — refusing to download %s. "
                        "The renderer should show a consent dialog.",
                        _PARAKERT_MODEL_ID,
                    )
                    if progress_callback:
                        progress_callback("HuggingFace consent required before downloading Parakeet model.")
                    # EC-FIX-8: import ConsentRequiredError from the
                    # canonical ``asr_errors`` module (previously
                    # imported from ``cloud_engines`` — EC-30 finding
                    # #12 / EC-B4: local engines should not depend on
                    # the cloud-engines module just for a 5-line
                    # exception class).
                    from voice_typer.server.asr_errors import ConsentRequiredError

                    raise ConsentRequiredError(
                        f"HuggingFace consent not given — refusing to download {_PARAKERT_MODEL_ID}."
                    )

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
                    log.exception("[PARAKEET] Model download failed")
                    if progress_callback:
                        progress_callback(f"Download failed: {exc}")
                    return False

                if not self._is_cached():
                    # PVT-G5-042: include the expected cache path so the
                    # operator can investigate (e.g. check permissions,
                    # disk space, or HF cache state) without filing a bug.
                    from voice_typer.server.config import _config_dir

                    _miss_cache_root = _config_dir() / "huggingface" / "hub"
                    _miss_model_dir = _miss_cache_root / f"models--{_PARAKERT_MODEL_ID.replace('/', '--')}"
                    log.error(
                        "[PARAKEET] Model not found in cache after download (expected at %s)",
                        _miss_model_dir,
                    )
                    if progress_callback:
                        progress_callback("Model not found in cache after download")
                    return False

            # G4-CR-06 (Session 7 — Group 4): verify model integrity
            # UNCONDITIONALLY on every load.  The previous code only
            # verified when the cache-miss / download branch ran, so a
            # cache hit (model already on disk) skipped verification
            # entirely — an attacker with write access to the HF cache
            # could tamper with ``model.safetensors`` and the next load
            # would feed tampered weights to the ASR engine with no
            # SHA-256 check.  The ~1-3s SHA-256 cost is acceptable vs
            # the 5-50s ``from_pretrained`` load time.
            #
            # The verify path is the same regardless of cache-hit or
            # post-download: enumerate snapshot dirs and call
            # ``verify_model_integrity`` against the manifest.  On
            # failure we hard-fail (return False) and remove the
            # offending ``models--<repo>`` directory so the next
            # ``load()`` doesn't re-discover the tampered snapshot.
            #
            # EC-FIX-8 (EC-B4): call ``security.verify_model_integrity``
            # directly with the canonical (local_dir, repo_id) argument
            # order.  Previously this went through the
            # ``asr_setup._verify_model_integrity`` wrapper which had a
            # swapped (repo_id, local_dir) signature — the wrapper just
            # re-swapped the args back to the canonical order, but the
            # indirection was a footgun (callers had to remember which
            # order each wrapper expected).  The wrapper has been
            # deleted; callers now use ``security.verify_model_integrity``
            # directly with the canonical order.
            from voice_typer.server.config import _config_dir
            from voice_typer.server.security import verify_model_integrity

            cache_root = _config_dir() / "huggingface" / "hub"
            model_dir = cache_root / f"models--{_PARAKERT_MODEL_ID.replace('/', '--')}"
            if model_dir.is_dir():
                verified = False
                verify_exc: Exception | None = None
                try:
                    for snapshot in (model_dir / "snapshots").iterdir():
                        if snapshot.is_dir() and verify_model_integrity(str(snapshot), _PARAKERT_MODEL_ID):
                            verified = True
                            break
                except OSError as exc:
                    verify_exc = exc
                if not verified:
                    # CRIT-4 / SEC-1 / G4-CR-06: hard-fail when
                    # integrity check fails — do NOT fall through to
                    # load the model anyway.  The previous code only
                    # logged a ``warning`` and continued, which combined
                    # with CRIT-5 (manifest pinning files the allow-list
                    # omits) meant every Parakeet download triggered
                    # this branch and loaded the model regardless —
                    # net effect: zero supply-chain protection.
                    # Mirrors the hard-fail semantics in
                    # ``asr_setup.download_parakeet_weights``.
                    log.error(
                        "[PARAKEET] Model integrity check failed%s for %s at %s. "
                        "Refusing to load tampered model. To fix: rm -rf %s",
                        f" (OSError: {verify_exc})" if verify_exc else "",
                        _PARAKERT_MODEL_ID,
                        model_dir,
                        model_dir,
                    )
                    if progress_callback:
                        progress_callback("Model integrity check failed; refusing to load tampered or corrupted model.")
                    # G4-CR-06 / cache cleanup on verify failure:
                    # remove the offending ``models--<repo>`` directory
                    # so the next ``load()`` doesn't re-discover the
                    # tampered snapshot.  Best-effort: log but don't
                    # raise if the cleanup itself fails (e.g. file is
                    # locked on Windows) — the integrity hard-fail is
                    # the security gate, the cleanup is just hygiene.
                    _cleanup_hf_cache_dir(model_dir)
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
                if effective_device == "cuda" and self._should_force_cpu():
                    effective_device = "cpu"

                # PW-4: time from_pretrained() calls to measure prewarm
                # cache-hit effectiveness.  <5s suggests OS page-cache
                # hit (warm), >=5s suggests cold disk read.
                _load_start = time.perf_counter()

                # Suppress Transformers' tqdm progress bar
                import io as _io
                from contextlib import redirect_stderr

                _stderr_buf = _io.StringIO()
                with redirect_stderr(_stderr_buf):
                    _t0 = time.perf_counter()
                    self._processor = self._AutoProcessor.from_pretrained(
                        _PARAKERT_MODEL_ID,
                        local_files_only=True,
                    )
                    _proc_elapsed = time.perf_counter() - _t0

                    try:
                        _t1 = time.perf_counter()
                        self._model = self._AutoModelForTDT.from_pretrained(
                            _PARAKERT_MODEL_ID,
                            dtype=self._torch.float16 if effective_device == "cuda" else self._torch.float32,
                            device_map=effective_device,
                            low_cpu_mem_usage=True,
                            local_files_only=True,
                        )
                        _model_elapsed = time.perf_counter() - _t1
                    except Exception as cuda_exc:
                        err_str = str(cuda_exc).lower()
                        if effective_device == "cuda" and ("1455" in err_str or "paging file" in err_str):
                            log.warning(
                                "[PARAKEET] CUDA allocation failed (pagefile), retrying on CPU: %s",
                                cuda_exc,
                            )
                            if progress_callback:
                                progress_callback("CUDA memory error, retrying on CPU...")
                            _t1 = time.perf_counter()
                            self._model = self._AutoModelForTDT.from_pretrained(
                                _PARAKERT_MODEL_ID,
                                dtype=self._torch.float32,
                                device_map="cpu",
                                low_cpu_mem_usage=True,
                                local_files_only=True,
                            )
                            _model_elapsed = time.perf_counter() - _t1
                        else:
                            raise

                _total_elapsed = time.perf_counter() - _load_start
                # PW-4: classify load as "warm (page-cache)" if under 5s,
                # "cold (disk)" otherwise.
                # PW-4: approximate weights read speed from the known
                # model file size (~2.4 GB for model.safetensors).
                _read_speed_mbs = _PARAKERT_WEIGHTS_MB / max(_model_elapsed, 0.1)
                _warm_label = "warm (page-cache)" if _total_elapsed < 5.0 else "cold (disk)"
                log.info(
                    "[PARAKEET] Model loaded successfully (%s) — processor=%.1fs, model=%.1fs, total=%.1fs (%.0f MB/s)",
                    _warm_label,
                    _proc_elapsed,
                    _model_elapsed,
                    _total_elapsed,
                    _read_speed_mbs,
                )
                if progress_callback:
                    progress_callback("Parakeet model ready")
                return True

            except ImportError as exc:
                log.exception("[PARAKEET] transformers package not installed")
                if progress_callback:
                    progress_callback(f"Missing dependency: {exc}")
                return False
            except KeyboardInterrupt:
                log.warning("[PARAKEET] Loading interrupted by user")
                if progress_callback:
                    progress_callback("Loading cancelled")
                return False
            except Exception as exc:
                log.exception("[PARAKEET] Failed to load model")
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
                raise RuntimeError("Parakeet model not loaded. Call load() first or check logs.")

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

        HIGH-18 / PERF-REL-1: this method no longer catches ``Exception``
        and returns ``""``.  The previous broad ``except`` swallowed
        CUDA errors (cublas, cudnn, OOM) so ``transcribe_with_fallback``
        received ``""`` — indistinguishable from a legitimate "no speech
        detected" result — and the GPU→CPU fallback branch was
        unreachable.  Genuine "no speech" cases do NOT raise (the model
        returns an empty sequence and ``decode`` returns "") so letting
        exceptions propagate is safe.
        """
        inputs = self._processor(
            [audio],
            sampling_rate=16000,
            return_tensors="pt",
        )
        inputs.to(device=self._model.device, dtype=self._model.dtype)
        # RW-T1: do NOT pass max_new_tokens — the previous cap of 256
        # silently truncated dense 25s chunks (Parakeet TDT emits
        # ~5-12 tokens/sec including duration tokens; dense speech at
        # 200+ WPM can need 250-300+ tokens).  Let the model use its
        # default ``generation_config.max_length`` (4096 for Parakeet
        # TDT v3) and emit EOS when speech ends — same as Whisper.
        output = self._model.generate(
            **inputs,
            return_dict_in_generate=True,
        )
        text = self._processor.decode(
            output.sequences,
            skip_special_tokens=True,
        )
        if isinstance(text, list):
            text = text[0] if text else ""
        text = text.strip()

        # English-only filter: only active when language="en" is configured
        if self.language == "en" and not _is_likely_english(text):
            return ""

        # PERF-STATS: reuse pre-computed RMS when provided
        rms = audio_stats[0] if audio_stats is not None else float(np.sqrt(np.mean(np.square(audio), dtype=np.float64)))
        if should_reject_low_audio_hallucination(text, rms):
            # SEC-009: Use PII-safe logging helper instead of raw text
            log_hallucination_rejection(
                "[PARAKEET]",
                text,
                reason="hallucination",
                log_transcriptions=False,
            )
            return ""

        return text

    def _split_audio(self, audio: np.ndarray, chunk_sec: float, overlap_sec: float) -> list[np.ndarray]:
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

        NEW-CQ-030 / RW-T1: The old implementation used a fixed ratio
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
        3. RW-T1: If no overlap duplicate is detected, return 0 — do
           NOT drop legitimate words.  The previous "allowance" of 1
           word per boundary silently dropped up to 14 words per
           5-minute recording (one per chunk boundary) even when the
           model did not re-transcribe any overlap text.  Boundary
           hallucinations are already filtered upstream by
           ``should_reject_low_audio_hallucination``.
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
            tail = words[skip:] if skip > 0 else words
            if tail:
                result_words.extend(tail)
        return " ".join(result_words).strip()

    @staticmethod
    def _compute_overlap_skip(prev_words: list[str], new_words: list[str]) -> int:
        """Return how many leading words of *new_words* to skip.

        We detect a true overlap duplicate by searching (case-insensitively,
        ignoring punctuation) for the leading run of ``new_words`` as a
        *contiguous subsequence* within the trailing window of
        ``prev_words``.  We pick the longest match that fits within
        ``_OVERLAP_DEDUP_WINDOW`` words on the new side, is at most
        ``_MAX_BOUNDARY_SKIP_WORDS`` long, and ends within the trailing
        ``_OVERLAP_DEDUP_WINDOW + _MAX_BOUNDARY_SKIP_WORDS`` words of the
        previous chunk.  If no match is found, return 0 (do not drop
        legitimate words).
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
                if prev_tail[start : start + length] == candidate:
                    best = length
                    break
            if best > 0:
                break

        if best > 0:
            return best

        # No true overlap detected.  Return 0 — do NOT drop legitimate
        # words.  The previous "allowance" of 1 word per boundary silently
        # dropped up to 14 words per 5-minute recording (one per chunk
        # boundary) even when the model did not re-transcribe any overlap
        # text.  Boundary hallucinations like "Thanks." at chunk starts
        # are already filtered upstream by `should_reject_low_audio_hallucination`
        # in `_transcribe_segment`.
        return 0

    def transcribe_with_fallback(
        self,
        audio: np.ndarray,
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
                    # PVT-G5-041: include exc_info so the CUDA failure
                    # traceback is captured for debugging.
                    log.warning("[PARAKEET] CUDA error, retrying on CPU: %s", exc, exc_info=True)
                    try:
                        # HIGH-18 / PERF-REL-1: pin dtype=float32 when
                        # moving the model to CPU.  The previous bare
                        # ``self._model.to("cpu")`` left the dtype as
                        # float16 (set during GPU load) — float16 kernels
                        # are unsupported or pathologically slow on CPU,
                        # so the "fallback" was effectively unusable.
                        self._model.to(device="cpu", dtype=self._torch.float32)
                        text = self._transcribe_impl(audio)
                        # G4-M-44 (Session 7 — Group 4): the CUDA→CPU
                        # fallback succeeded.  Emit a ONE-TIME tray
                        # notification so the user knows why their
                        # dictation got slower, and publish a status
                        # event so the tray icon can show "(CPU
                        # fallback)".  ``self.device`` is NOT mutated
                        # here — it stays ``"cuda"`` so the next
                        # ``load()`` re-attempts CUDA (per-transcription
                        # fallback, not permanent).  The
                        # ``_cpu_fallback_notified`` flag is reset to
                        # ``False`` at the top of ``load()`` so a
                        # fallback after the next reload re-notifies.
                        # Coordinate with agent 2-r for tray.py: the
                        # ``"type": "parakeet_cpu_fallback"`` event is
                        # the contract for the tray "(CPU fallback)"
                        # status suffix; the ``"notification"`` event
                        # surfaces the user-facing toast.
                        if not self._cpu_fallback_notified:
                            self._cpu_fallback_notified = True
                            try:
                                from voice_typer.server import event_bus

                                event_bus.publish(
                                    {
                                        "type": "notification",
                                        "data": {
                                            "title": APP_NAME,
                                            "message": (
                                                "GPU transcription failed — switched to CPU. "
                                                "Transcription will be slower until restart."
                                            ),
                                            "duration_ms": 10000,
                                        },
                                    }
                                )
                                event_bus.publish(
                                    {
                                        "type": "parakeet_cpu_fallback",
                                        "data": {"device": "cpu", "reason": str(exc)[:200]},
                                    }
                                )
                            except Exception as notify_exc:
                                log.debug(
                                    "[PARAKEET] could not publish CPU-fallback notification: %s",
                                    notify_exc,
                                )
                        return text
                    except Exception as cpu_exc:
                        # PVT-G5-041: use ``log.exception`` instead of
                        # ``log.error(..., exc_info=True)`` to satisfy the
                        # ``test_log_exception_no_exc_arg`` regression
                        # test that flags ``log.error(..., exc_info=True)``
                        # in this file. ``log.exception`` is semantically
                        # equivalent (auto-captures the active exception).
                        log.exception("[PARAKEET] CPU fallback also failed")
                        raise TranscriptionBackendError(
                            f"Parakeet GPU transcription failed ({exc}) and CPU fallback also failed ({cpu_exc})"
                        ) from cpu_exc
                # Non-CUDA error: surface it instead of swallowing as ""
                raise TranscriptionBackendError(f"Parakeet transcription failed: {exc}") from exc

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
        """Transcribe one segment without lock (for fallback path).

        HIGH-18 / PERF-REL-1: mirrors the fix in ``_transcribe_segment`` —
        no longer catches ``Exception`` and returns ``""``.  This is the
        CPU-fallback code path called from ``_transcribe_impl`` after
        ``transcribe_with_fallback`` moved the model to CPU; if it
        swallowed exceptions, the caller would receive ``""`` and treat
        a real CPU failure as a successful "no speech detected" result,
        defeating the ``TranscriptionBackendError`` contract documented
        on ``transcribe_with_fallback`` (ERR-007).
        """
        inputs = self._processor(
            [audio],
            sampling_rate=16000,
            return_tensors="pt",
        )
        inputs.to(device=self._model.device, dtype=self._model.dtype)
        # RW-T1: do NOT pass max_new_tokens — same fix as the GPU path
        # in ``_transcribe_segment``.  The previous cap of 256 silently
        # truncated dense 25s chunks in the CPU fallback path too.
        output = self._model.generate(
            **inputs,
            return_dict_in_generate=True,
        )
        text = self._processor.decode(
            output.sequences,
            skip_special_tokens=True,
        )
        if isinstance(text, list):
            text = text[0] if text else ""
        text = text.strip()

        # English-only filter: only active when language="en" is configured
        if self.language == "en" and not _is_likely_english(text):
            return ""

        rms = float(np.sqrt(np.mean(np.square(audio), dtype=np.float64)))
        if should_reject_low_audio_hallucination(text, rms):
            # SEC-009: Use PII-safe logging helper for unlocked fallback path
            log_hallucination_rejection(
                "[PARAKEET]",
                text,
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

        # EC-FIX-8: ``release_gpu_memory`` lives in the canonical
        # ``asr_utils`` module. ``transcription.py`` still re-exports the
        # name for backward compat, BUT because this is a LOCAL import
        # (not a module-level import), tests that want to intercept the
        # call MUST patch ``voice_typer.server.asr_utils.release_gpu_memory``
        # — patching ``voice_typer.server.transcription.release_gpu_memory``
        # does NOT intercept the local import resolution. (See WR-4 and
        # tests/regressions/gpu_memory_release_test.py
        # ::TestReleaseGpuMemoryFunctional::test_parakeet_unload_invokes_release.)
        from voice_typer.server.asr_utils import release_gpu_memory

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
