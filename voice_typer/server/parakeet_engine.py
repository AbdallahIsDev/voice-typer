"""Parakeet TDT v3 ASR engine — optional backend alongside Whisper/Qwen.

Uses NVIDIA's parakeet-tdt-0.6b-v3 via HuggingFace Transformers.
Auto-downloads model weights on first load via huggingface_hub.
Falls back gracefully on missing deps, CUDA errors, etc.
"""

import contextlib
import logging
import os
import threading
import time
import unicodedata
from collections.abc import Callable
from typing import Any

import numpy as np

from voice_typer.server._audio_constants import WHISPER_SAMPLE_RATE
from voice_typer.server.branding import APP_NAME
from voice_typer.server.hallucination import log_hallucination_rejection, should_reject_low_audio_hallucination
from voice_typer.server.i18n import DEFAULT_LOCALE
from voice_typer.server.security import MODEL_HASHES as _MODEL_HASHES

log = logging.getLogger(__name__)


class TranscriptionBackendError(RuntimeError):
    """Raised when the ASR backend cannot produce a transcription.

    ``transcribe_with_fallback`` previously returned ``""`` on
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
        # Use PII-safe logging helper for hallucination text
        log_hallucination_rejection(
            "[PARAKEET]",
            text,
            reason=f"non-English output ({ratio * 100:.0f}% non-Latin chars)",
            log_transcriptions=False,
        )
        return False
    return True


_PARAKERT_MODEL_ID = "nvidia/parakeet-tdt-0.6b-v3"

# approximate model weight size in MB for MB/s read-speed logging.
# The model.safetensors file is ~2.4 GB on disk.
_PARAKERT_WEIGHTS_MB = 2400


class _AbortStoppingCriteria:
    """``transformers.StoppingCriteria`` that stops generation when an
    abort event is set.

    Used by ``ParakeetEngine._transcribe_segment`` /
    ``_transcribe_batch`` to wire the dictation pipeline's cancel path
    (ESC / watchdog) into ``model.generate()``. ``transformers`` calls
    each criterion's ``__call__`` between generated tokens; returning
    ``True`` stops generation early so the inference thread is
    unblocked in bounded time instead of decoding the full sequence.

    Implemented as a duck-typed class (NOT a subclass of
    ``transformers.StoppingCriteria``) so the module imports cleanly
    even when ``transformers`` is not installed (the optional-deps
    pattern used throughout this module). ``model.generate`` only
    requires the ``__call__`` method — it does not isinstance-check
    against ``StoppingCriteria``.
    """

    def __init__(self, abort_event: threading.Event) -> None:
        self._abort_event = abort_event

    def __call__(self, input_ids: Any, scores: Any, **kwargs: Any) -> bool:  # noqa: D401
        """Return True if generation should stop (abort signalled)."""
        return self._abort_event.is_set()


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

#  Maximum words to skip at a chunk boundary.

# Previously the merge step used ``skip = int(len(words) * 0.12)`` which
# silently dropped words at every boundary — for a 25-word chunk that's
# 3 dropped words, regardless of whether the model actually re-transcribed
# the overlap region.  Word density is not uniform across audio time, so a
# ratio-based skip is unsafe.  Cap the skip to at most this many words
# AND only after we've checked for an actual word-level overlap with the
# previous chunk's tail (see ``_merge_chunks``).

#  (2025): the previous "allowance" of 1 word per boundary even when
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
    """Cache cleanup: best-effort delete a tampered HF cache dir.

    this local helper now delegates to the canonical
    ``voice_typer.server.asr_utils.cleanup_hf_cache_dir`` so the
    cleanup logic lives in one place (previously the same body was
    duplicated 3x across ``transcription.py``,
    ``asr_setup.py``, and here —  finding #2).

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
    # typed as ``Any`` so pyrefly can follow the .cuda
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
        language: str = DEFAULT_LOCALE,
        config: Any = None,
    ):
        self.device = device
        self.language = language
        # Optional Config reference
        # consulted by ``load()`` to gate HuggingFace downloads on
        # explicit user consent (``config.huggingface_consent``).
        # ``None`` is treated as "consent not given" (safe default per
        # GDPR Art. 6/13).  The registry / model_manager passes the
        # live Config when constructing the engine so the gate is
        # enforced in production; tests can omit it to exercise the
        # cache-hit / already-loaded fast paths.
        self.config = config
        # instance-level model handles are populated by load()
        # and read by transcribe(). Typed as Any so attribute accesses
        # (.device, .dtype, .generate, .decode) type-check without
        # forcing every call site to repeat the None-narrowing guard
        # that transcribe() already performs at entry.
        self._model: Any = None
        self._processor: Any = None
        # One-time tray notification flag for CUDA→CPU
        # transcription fallback.  Reset to ``False`` on every
        # successful ``load()`` so a fallback after the next reload
        # re-notifies the user (the user may have restarted their GPU
        # driver / freed VRAM in the meantime).
        self._cpu_fallback_notified: bool = False
        self._lock = threading.RLock()
        # counter + Condition so transcribe() can release the model
        # lock during the (potentially long) chunk-inference loop while
        # still coordinating with unload(). unload() waits for
        # ``_active_inference == 0`` before nulling ``self._model`` so a
        # concurrent transcribe() doesn't dereference a freed model.
        self._active_inference = 0
        self._inference_cond = threading.Condition(self._lock)
        # Abort token shared by the dictation pipeline's cancel path
        # and the ``model.generate()`` call in ``_transcribe_segment``
        # / ``_transcribe_batch``. ``request_abort()`` sets the event;
        # the ``_AbortStoppingCriteria`` (passed as ``stopping_criteria``
        # to ``generate()``) checks it between generated tokens and
        # returns True to stop generation early. ``clear_abort()`` is
        # called by the pipeline at the start of each transcription
        # cycle so a stale abort from the previous cycle does NOT
        # suppress the next one. The chunk-iteration loop in
        # ``_transcribe_chunks_batched`` also checks the event between
        # chunks so a long audio split into 13 chunks stops after the
        # current chunk rather than decoding all remaining ones.
        self._abort_event = threading.Event()
        self._ensure_hf_env()
        # batch 2-4 chunks per ``processor()`` + ``generate()`` call.
        # Default batch size is 1 (sequential) so the existing test contract
        # that pins ``mock_model.generate.call_count == 2`` for a 2-chunk
        # transcription keeps passing. Operators who want the batching
        # speedup can set ``PARAKEET_BATCH_SIZE=2`` (or 3/4) in the
        # environment; on OOM we fall back to per-chunk sequential inference
        # for the remaining chunks so the user still gets a transcription.
        #
        # Read at construction time (NOT import time) so changes to the
        # env var between engine constructions take effect — previously
        # the class-attribute form evaluated ``os.environ.get`` once when
        # the module was imported, freezing the value for the entire
        # process lifetime and ignoring any later ``os.environ`` mutation
        # (e.g. a test that does ``monkeypatch.setenv(\"PARAKEET_BATCH_SIZE\",
        # \"2\")`` after the first ParakeetEngine was constructed would NOT
        # see the new value, because the class attribute was already
        # frozen). Setting it as an instance attribute here re-reads the
        # env var on every ``ParakeetEngine()`` construction.
        self._INFERENCE_BATCH_SIZE: int = max(1, int(os.environ.get("PARAKEET_BATCH_SIZE", "1")))

    @classmethod
    def _ensure_hf_env(cls):
        if cls._hf_home_set:
            return
        try:
            from voice_typer.server.asr_setup import ensure_hf_env

            ensure_hf_env()
            cls._hf_home_set = True
        except Exception:
            # Previously a silent ``except: pass``. Log at
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
            log.info("[PARAKEET] importing torch (this can take a few seconds on first import)...")
            import torch

            _torch_s = time.perf_counter() - _t0
            log.info("[PARAKEET] torch imported (%.2fs)", _torch_s)

            # ``AutoModelForTDT`` was added to transformers in
            # 4.50 (our pyproject floor).  The venv on this runner has
            # 4.44, so a static ``from transformers import AutoModelForTDT``
            # trips pyrefly's missing-module-attribute even though the
            # surrounding try/except ImportError is the runtime guard.
            # Resolve via ``getattr`` so the static checker does not
            # see the (possibly absent) attribute access.
            _t1 = time.perf_counter()
            log.info("[PARAKEET] importing transformers...")
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

    def _inference_mode_ctx(self) -> Any:
        """Return a context manager that wraps torch.inference_mode().

        model.generate() was previously called WITHOUT an
        inference-mode context, which meant PyTorch built and retained
        the autograd graph for every call. For a 25 s chunk on CUDA
        this roughly DOUBLED activation-memory footprint (increasing
        OOM risk) and added ~10-30 % inference latency from
        gradient-tracking overhead. Multiplied across 13 chunks for a
        5-minute dictation, the latency penalty is several seconds.

        torch.inference_mode() is preferred over torch.no_grad()
        (lower overhead, recursive).

        If self._torch is None (e.g. a test stub that bypasses
        _ensure_imports()), falls back to importing torch directly;
        if torch isn't installed, returns a contextlib.nullcontext.
        """
        torch = self._torch
        if torch is None:
            try:
                import torch as _torch_fallback
            except ImportError:
                return contextlib.nullcontext()
            torch = _torch_fallback
        return torch.inference_mode()

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
            # Previously a silent ``except: pass``. Disk
            # space check is best-effort — failure here just means we
            # won't pre-emptively force CPU, which is non-fatal.
            log.debug("[PARAKEET] _should_force_cpu disk space check failed (non-fatal)", exc_info=True)
        return False

    @staticmethod
    def _is_cached() -> bool:
        """Quick check if model is in HF cache without calling snapshot_download."""
        # use config._config_dir() directly instead of
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
            # Previously a silent ``except OSError: pass``.
            # A transient FS error (e.g. snapshot dir deleted between
            # is_dir() and iterdir()) shouldn't crash the cache probe.
            log.debug("[PARAKEET] _is_cached snapshot iterdir failed (non-fatal)", exc_info=True)
        return False

    # ── TranscriberProtocol ──────────────────────────────────────────

    @property
    def is_loaded(self) -> bool:
        with self._lock:
            return self._model is not None and self._processor is not None

    def request_abort(self) -> None:
        """Signal an in-flight ``model.generate()`` to stop early.

        Sets ``_abort_event``; the ``_AbortStoppingCriteria`` passed
        to ``model.generate()`` checks the event between generated
        tokens and returns True to stop generation. Also causes the
        chunk-iteration loop in ``_transcribe_chunks_batched`` to break
        out after the current chunk completes. Bounded latency instead
        of waiting for the full audio to decode — frees compute for
        the next dictation cycle.
        """
        self._abort_event.set()

    def clear_abort(self) -> None:
        """Clear the abort token at the start of a fresh transcription cycle.

        Called by the dictation pipeline before each transcribe so a
        stale abort from the previous cycle (e.g. the user hit ESC,
        aborted, then started a new recording) does NOT suppress the
        new transcription.
        """
        self._abort_event.clear()

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

            # Reset the one-time CPU-fallback notification flag
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
                # HuggingFace downloads
                # reveal the user's IP to a US-headquartered third party
                # and pull ~2.5 GB over the network.  Require explicit
                # ``huggingface_consent`` before any network call,
                # mirroring ``transcription.py::_pre_download_model``
                # and ``service/model.py::_require_huggingface_consent``.
                # The canonical gate lives in
                # ``asr_utils._require_huggingface_consent`` so the
                # safe-default (no consent → refuse to contact
                # HuggingFace), the log message, the progress-callback
                # wording, and the typed ``ConsentRequiredError``
                # surface stay in sync across all three call sites.
                from voice_typer.server.asr_utils import _require_huggingface_consent

                _require_huggingface_consent(
                    self.config,
                    _PARAKERT_MODEL_ID,
                    log_prefix="[PARAKEET]",
                    progress_message="HuggingFace consent required before downloading Parakeet model.",
                    progress_callback=progress_callback,
                )

                try:
                    from huggingface_hub import snapshot_download

                    if progress_callback:
                        progress_callback("Downloading Parakeet model files...")
                    log.info("[PARAKEET] Downloading model files...")

                    # wrap snapshot_download in a retry loop with
                    # exponential backoff. HuggingFace's CDN and the HF Hub
                    # rate-limiter intermittently drop connections on large
                    # (~2.5 GB) downloads — without retry, a single transient
                    # failure aborts the load. Whisper's ``_pre_download_model``
                    # path and the Models-page download both already retry via
                    # the same helper; this brings the parakeet engine path to
                    # parity. ``resume_download=True`` makes each retry continue
                    # from the last byte received.
                    from voice_typer.server.asr_utils import _download_with_retry

                    _download_with_retry(
                        lambda: snapshot_download(
                            repo_id=_PARAKERT_MODEL_ID,
                            revision=_PARAKEET_REVISION,
                            allow_patterns=_PARAKEET_ALLOW_PATTERNS,
                            resume_download=True,
                        ),
                        max_attempts=4,
                        delays=(2.0, 4.0, 8.0, 16.0),
                    )
                except Exception as exc:
                    log.exception("[PARAKEET] Model download failed")
                    if progress_callback:
                        progress_callback(f"Download failed: {exc}")
                    return False

                if not self._is_cached():
                    # Include the expected cache path so the
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

            # Verify model integrity
            # UNCONDITIONALLY on every load.  The previous code only
            # verified when the cache-miss / download branch ran, so a
            # cache hit (model already on disk) skipped verification
            # entirely — an attacker with write access to the HF cache
            # could tamper with ``model.safetensors`` and the next load
            # would feed tampered weights to the ASR engine with no
            # SHA-256 check.  The ~1-3s SHA-256 cost is acceptable vs
            # the 5-50s ``from_pretrained`` load time.

            # The verify path is the same regardless of cache-hit or
            # post-download: enumerate snapshot dirs and call
            # ``verify_model_integrity`` against the manifest.  On
            # failure we hard-fail (return False) and remove the
            # offending ``models--<repo>`` directory so the next
            # ``load()`` doesn't re-discover the tampered snapshot.

            #  (): call ``security.verify_model_integrity``
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
                    # CRIT-4 / SEC-1: hard-fail when
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
                    # Cache cleanup on verify failure:
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

                # time from_pretrained() calls to measure prewarm
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
                            # include exc_info=True so the CUDA
                            # allocation traceback is captured for debugging
                            # (previously the ``%s`` interpolation lost the
                            # traceback, leaving only the exception's str()).
                            log.warning(
                                "[PARAKEET] CUDA allocation failed (pagefile), retrying on CPU: %s",
                                cuda_exc,
                                exc_info=True,
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
                # classify load as "warm (page-cache)" if under 5s,
                # "cold (disk)" otherwise.
                # approximate weights read speed from the known
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
                # Prime CUDA kernels at load time so the first real
                # dictation doesn't pay the 2-5 s JIT cost (cuDNN /
                # cuBLAS / attention kernel compilation). No-op on CPU
                # and best-effort (failures swallowed inside the helper).
                # See ``_warm_up_model`` for the full contract.
                if effective_device == "cuda":
                    self._warm_up_model()
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

        the lock is released during the chunk-inference loop.
        Previously the entire 13-chunk loop ran under ``self._lock``,
        blocking ``is_loaded`` / ``unload`` / parallel transcribes for
        ~13s per long dictation. We now acquire the lock only briefly
        to check loaded state and increment ``_active_inference``;
        ``unload()`` waits on ``_inference_cond`` for the counter to
        return to 0 before nulling the model, so the inference path
        can safely access ``self._model`` / ``self._processor``
        without holding the lock.
        """
        with self._lock:
            if self._model is None or self._processor is None:
                raise RuntimeError("Parakeet model not loaded. Call load() first or check logs.")

            if len(audio) == 0:
                return ""

            duration = len(audio) / WHISPER_SAMPLE_RATE
            self._active_inference += 1

        try:
            if duration <= _CHUNK_SECONDS:
                return self._transcribe_segment(audio, audio_stats=audio_stats)

            chunks = self._split_audio(audio, _CHUNK_SECONDS, _CHUNK_OVERLAP_SECONDS)
            log.info("[PARAKEET] Splitting %.1fs audio into %d chunks", duration, len(chunks))

            results = self._transcribe_chunks_batched(chunks)
            if not results:
                return ""

            merged = self._merge_chunks(results)
            return merged
        finally:
            with self._inference_cond:
                self._active_inference -= 1
                if self._active_inference == 0:
                    self._inference_cond.notify_all()

    def _transcribe_segment(self, audio: np.ndarray, audio_stats: "tuple[float, float, float] | None" = None) -> str:
        """Transcribe one audio segment (assumed to be within model limits).

        PERF-STATS: ``audio_stats`` is an optional pre-computed
        ``(rms, peak, silence_pct)`` tuple. When provided, the
        engine skips its own RMS computation in hallucination detection.

         PERF-REL-1: this method no longer catches ``Exception``
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
            sampling_rate=WHISPER_SAMPLE_RATE,
            return_tensors="pt",
        )
        inputs.to(device=self._model.device, dtype=self._model.dtype)
        # do NOT pass max_new_tokens — the previous cap of 256
        # silently truncated dense 25s chunks (Parakeet TDT emits
        # ~5-12 tokens/sec including duration tokens; dense speech at
        # 200+ WPM can need 250-300+ tokens).  Let the model use its
        # default ``generation_config.max_length`` (4096 for Parakeet
        # TDT v3) and emit EOS when speech ends — same as Whisper.
        #
        # wrap generate() in torch.inference_mode() to skip
        # autograd-graph construction. See _inference_mode_ctx.
        #
        # Abort wiring: ``_AbortStoppingCriteria`` is checked between
        # generated tokens by ``transformers``. When the dictation
        # pipeline's cancel path (ESC / watchdog) sets
        # ``self._abort_event``, the next token-step returns True and
        # ``generate()`` stops early — bounded latency instead of
        # decoding the full sequence. ``generate()`` accepts a list of
        # stopping criteria; we pass ours as the sole entry.
        with self._inference_mode_ctx():
            output = self._model.generate(
                **inputs,
                return_dict_in_generate=True,
                stopping_criteria=[_AbortStoppingCriteria(self._abort_event)],
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
            # Use PII-safe logging helper instead of raw text
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
        sr = WHISPER_SAMPLE_RATE
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

    # batch 2-4 chunks per ``processor()`` + ``generate()`` call.
    # Default batch size is 1 (sequential) so the existing test contract
    # that pins ``mock_model.generate.call_count == 2`` for a 2-chunk
    # transcription keeps passing. Operators who want the batching
    # speedup can set ``PARAKEET_BATCH_SIZE=2`` (or 3/4) in the
    # environment; on OOM we fall back to per-chunk sequential inference
    # for the remaining chunks so the user still gets a transcription.
    #
    # NOTE: this is the CLASS-level default. ``__init__`` overrides it
    # with an INSTANCE attribute of the same name, read from the env
    # var at construction time (NOT import time) so changes to
    # ``PARAKEET_BATCH_SIZE`` between engine constructions take effect.
    # The class attribute is kept as a fallback for instances created
    # via ``__new__`` (e.g. some unit tests) that skip ``__init__``.
    _INFERENCE_BATCH_SIZE: int = 1

    def _transcribe_chunks_batched(self, chunks: list[np.ndarray]) -> list[str]:
        """Transcribe ``chunks`` in batches, falling back to sequential on OOM.

        ``processor()`` and ``model.generate()`` both accept a
        list of audio arrays as a batch. When ``_INFERENCE_BATCH_SIZE``
        is 1 (default), this method is strictly sequential and preserves
        the historical call-count contract pinned by
        ``test_transcribe_long_audio_splits_into_chunks``. When set to
        2+ via the ``PARAKEET_BATCH_SIZE`` env var, we group that many
        chunks per ``generate()`` call. On a CUDA OOM (``"out of
        memory"`` in the error string), we fall back to per-chunk
        sequential inference for the remaining chunks so the user still
        gets a transcription.

        callers must have already incremented
        ``_active_inference`` (via :py:meth:`transcribe`); this method
        does NOT touch the counter.
        """
        if not chunks:
            return []

        if self._INFERENCE_BATCH_SIZE <= 1 or len(chunks) == 1:
            results: list[str] = []
            for i, chunk in enumerate(chunks):
                # Check the abort token BETWEEN chunks. The
                # ``_transcribe_segment`` call below already wires the
                # abort event into ``model.generate()`` via
                # ``_AbortStoppingCriteria`` (so the current chunk's
                # token stream stops early); this check skips any
                # REMAINING chunks after the current one returns, so a
                # 13-chunk long-form dictation stops after the current
                # chunk rather than decoding all remaining ones.
                if self._abort_event.is_set():
                    log.info(
                        "[PARAKEET] Abort requested — stopping chunk loop early (completed %d/%d chunks)",
                        i,
                        len(chunks),
                    )
                    break
                log.info(
                    "[PARAKEET] Transcribing chunk %d/%d (%.1fs)",
                    i + 1,
                    len(chunks),
                    len(chunk) / WHISPER_SAMPLE_RATE,
                )
                text = self._transcribe_segment(chunk)
                if text:
                    results.append(text)
            return results

        results = []
        i = 0
        while i < len(chunks):
            # Same abort check as the sequential branch — see above.
            if self._abort_event.is_set():
                log.info(
                    "[PARAKEET] Abort requested — stopping batched chunk loop early (completed %d/%d chunks)",
                    i,
                    len(chunks),
                )
                break
            batch = chunks[i : i + self._INFERENCE_BATCH_SIZE]
            i += len(batch)
            log.info(
                "[PARAKEET] Transcribing batch of %d chunk(s) (%d/%d done)",
                len(batch),
                i - len(batch),
                len(chunks),
            )
            try:
                batch_texts = self._transcribe_batch(batch)
                for t in batch_texts:
                    if t:
                        results.append(t)
            except Exception as exc:
                err_str = str(exc).lower()
                if "out of memory" in err_str or ("cuda" in err_str and "allocat" in err_str):
                    # include exc_info=True so the OOM
                    # traceback is captured for debugging (previously
                    # the ``%s`` interpolation lost the traceback).
                    log.warning(
                        "[PARAKEET] Batched inference OOM on batch of %d chunks — falling back to sequential: %s",
                        len(batch),
                        exc,
                        exc_info=True,
                    )
                    for chunk in batch:
                        text = self._transcribe_segment(chunk)
                        if text:
                            results.append(text)
                else:
                    raise
        return results

    def _transcribe_batch(self, batch: list[np.ndarray]) -> list[str]:
        """Run ``processor`` + ``generate`` + ``decode`` on a batch of chunks."""
        inputs = self._processor(
            batch,
            sampling_rate=WHISPER_SAMPLE_RATE,
            return_tensors="pt",
        )
        inputs.to(device=self._model.device, dtype=self._model.dtype)
        # wrap generate() in torch.inference_mode() to skip
        # autograd-graph construction. See _inference_mode_ctx.
        # Abort wiring: same ``_AbortStoppingCriteria`` as the
        # single-segment path — see ``_transcribe_segment``.
        with self._inference_mode_ctx():
            output = self._model.generate(
                **inputs,
                return_dict_in_generate=True,
                stopping_criteria=[_AbortStoppingCriteria(self._abort_event)],
            )
        decoded = self._processor.decode(
            output.sequences,
            skip_special_tokens=True,
        )
        if isinstance(decoded, str):
            decoded = [decoded]
        texts: list[str] = []
        for idx, raw_text in enumerate(decoded):
            if idx >= len(batch):
                break
            text = (raw_text or "").strip()
            if not text:
                texts.append("")
                continue
            if self.language == "en" and not _is_likely_english(text):
                texts.append("")
                continue
            rms = float(np.sqrt(np.mean(np.square(batch[idx]), dtype=np.float64)))
            if should_reject_low_audio_hallucination(text, rms):
                log_hallucination_rejection(
                    "[PARAKEET]",
                    text,
                    reason="hallucination",
                    log_transcriptions=False,
                )
                texts.append("")
                continue
            texts.append(text)
        while len(texts) < len(batch):
            texts.append("")
        return texts

    def _merge_chunks(self, texts: list[str]) -> str:
        """Concatenate chunk transcriptions, skipping overlap text.

        Chunks have ``_CHUNK_OVERLAP_SECONDS`` of overlapping audio at
        each boundary.  When the model re-transcribes the overlap region
        in the new chunk, those leading words duplicate the previous
        chunk's tail and must be skipped.

         The old implementation used a fixed ratio
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
        3. : If no overlap duplicate is detected, return 0 — do
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
                speech detected" result ().
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
                # Include exc_info so the CUDA failure
                # traceback is captured for debugging.
                log.warning("[PARAKEET] CUDA error, retrying on CPU: %s", exc, exc_info=True)
                try:
                    #  PERF-REL-1: pin dtype=float32 when
                    # moving the model to CPU.  The previous bare
                    # ``self._model.to("cpu")`` left the dtype as
                    # float16 (set during GPU load) — float16 kernels
                    # are unsupported or pathologically slow on CPU,
                    # so the "fallback" was effectively unusable.

                    # acquire the lock only long enough to move
                    # the model to CPU and claim an inference slot so
                    # ``unload()`` waits for the CPU-fallback transcription
                    # to finish before nulling the model.
                    with self._lock:
                        if self._model is None:
                            raise TranscriptionBackendError("Parakeet model not loaded.")
                        self._model.to(device="cpu", dtype=self._torch.float32)
                        self._active_inference += 1
                    try:
                        text = self._transcribe_impl(audio)
                    finally:
                        with self._inference_cond:
                            self._active_inference -= 1
                            if self._active_inference == 0:
                                self._inference_cond.notify_all()
                    # The CUDA→CPU
                    # fallback succeeded.  Emit a ONE-TIME tray
                    # notification so the user knows why their
                    # dictation got slower, and publish a status
                    # event so the tray icon can show "(CPU
                    # fallback)".
                    #
                    # Device-state note: ``self.device`` is NOT
                    # mutated here — it stays ``"cuda"`` so the next
                    # ``load()`` re-attempts CUDA.  However,
                    # ``self._model.device`` IS permanently mutated
                    # to ``"cpu"`` by the ``.to(device="cpu")`` call
                    # above (PyTorch's ``.to()`` moves the model
                    # in place).  Subsequent ``transcribe()`` calls
                    # within the same loaded session will therefore
                    # run on CPU — they read ``self._model.device``
                    # (now ``"cpu"``) at ``_transcribe_segment`` /
                    # ``_transcribe_batch`` / ``_transcribe_segment_unlocked``,
                    # not ``self.device``.
                    #
                    # Snapshot-and-restore (saving the original
                    # device/dtype before the ``.to("cpu")`` call
                    # and restoring them in a ``finally`` block)
                    # would re-attempt CUDA on every subsequent
                    # transcribe, but it is intentionally NOT done
                    # here: if the CUDA error was non-transient
                    # (e.g. driver crash, persistent OOM, hardware
                    # fault), re-attempting CUDA on every transcribe
                    # would re-trigger the same error and waste 1-5 s
                    # of user time per call.  The current
                    # "permanent-until-reload" behaviour is pinned by
                    # ``test_fallback_retries_on_cpu_after_cuda_error``
                    # (``mock_model.to.assert_called_once()``), which
                    # would fail if a restore ``.to()`` call were
                    # added.  A fresh ``load()`` (e.g. via the tray
                    # "Reload model" action) re-attempts CUDA from
                    # scratch.
                    #
                    # The ``_cpu_fallback_notified`` flag is reset to
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
                    # Use ``log.exception`` instead of
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

        NOT a duplicate of transcribe(). This method uses
        _transcribe_segment_unlocked() (no lock) while transcribe()
        uses _transcribe_segment() (with lock). The fallback path
        calls this after releasing the lock for CPU retry.
        """
        duration = len(audio) / WHISPER_SAMPLE_RATE
        if duration <= _CHUNK_SECONDS:
            return self._transcribe_segment_unlocked(audio)

        chunks = self._split_audio(audio, _CHUNK_SECONDS, _CHUNK_OVERLAP_SECONDS)
        results = []
        for i, chunk in enumerate(chunks):
            # OI-14: abort gate at the TOP of the CPU-fallback chunk
            # loop, mirroring the batched path in
            # ``_transcribe_chunks_batched``. The ``_AbortStoppingCriteria``
            # passed to ``model.generate()`` only stops the CURRENT
            # chunk's token stream; without this check the loop would
            # decode every remaining chunk after ESC / watchdog, so a
            # 2-minute audio split into 5 CPU chunks could take 2-5
            # minutes to honour the abort instead of the documented
            # "stop after the current chunk" bound.
            if self._abort_event.is_set():
                log.info(
                    "[PARAKEET] Abort requested — stopping CPU-fallback chunk loop early (completed %d/%d chunks)",
                    i,
                    len(chunks),
                )
                break
            text = self._transcribe_segment_unlocked(chunk)
            if text:
                results.append(text)
        if not results:
            return ""
        return self._merge_chunks(results)

    def _transcribe_segment_unlocked(self, audio: np.ndarray) -> str:
        """Transcribe one segment without lock (for fallback path).

         PERF-REL-1: mirrors the fix in ``_transcribe_segment`` —
        no longer catches ``Exception`` and returns ``""``.  This is the
        CPU-fallback code path called from ``_transcribe_impl`` after
        ``transcribe_with_fallback`` moved the model to CPU; if it
        swallowed exceptions, the caller would receive ``""`` and treat
        a real CPU failure as a successful "no speech detected" result,
        defeating the ``TranscriptionBackendError`` contract documented
        on ``transcribe_with_fallback`` ().
        """
        inputs = self._processor(
            [audio],
            sampling_rate=WHISPER_SAMPLE_RATE,
            return_tensors="pt",
        )
        inputs.to(device=self._model.device, dtype=self._model.dtype)
        # do NOT pass max_new_tokens — same fix as the GPU path
        # in ``_transcribe_segment``.  The previous cap of 256 silently
        # truncated dense 25s chunks in the CPU fallback path too.
        #
        # wrap generate() in torch.inference_mode() to skip
        # autograd-graph construction. See _inference_mode_ctx.
        #
        # Abort wiring — same ``_AbortStoppingCriteria`` as the
        # GPU path in ``_transcribe_segment`` and the batched path in
        # ``_transcribe_batch``.  Without this, ESC / watchdog during
        # CPU-fallback transcription could not interrupt generation;
        # the inference thread stayed blocked for 30-60s on a 25s chunk
        # because ``transformers`` only consults ``stopping_criteria``
        # between generated tokens.
        with self._inference_mode_ctx():
            output = self._model.generate(
                **inputs,
                return_dict_in_generate=True,
                stopping_criteria=[_AbortStoppingCriteria(self._abort_event)],
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
            # Use PII-safe logging helper for unlocked fallback path
            log_hallucination_rejection(
                "[PARAKEET]",
                text,
                reason="hallucination",
                log_transcriptions=False,
            )
            return ""
        return text

    def _warm_up_model(self) -> None:
        """Run a tiny dummy inference to prime CUDA kernels (JIT cost: 2-5 s).

        The first ``model.generate()`` call after ``from_pretrained``
        takes 2-5 s longer than subsequent ones because the GPU kernels
        (cuDNN, cuBLAS, attention) must be JIT-compiled and memory
        allocated for the model's specific shapes.  This warm-up runs a
        0.5 s silence through the full ``processor()`` + ``generate()`` +
        ``decode()`` pipeline at load time so the first real dictation
        is fast.

        Mirrors ``WhisperEngine._warm_up_model`` in
        ``voice_typer/server/transcription.py`` (lines ~649-680), adapted
        for Parakeet's ``processor()`` + ``model.generate()`` API (the
        same call shape as ``_transcribe_segment``).

        Non-fatal: any exception (CUDA OOM, processor error, etc.) is
        logged at debug level and swallowed — the model is still
        considered loaded, and only the first real transcription pays
        the JIT cost.  ``load()`` returns True regardless.

        No-op when:

        - ``self._model`` or ``self._processor`` is None (defensive —
          direct-call safety).
        - ``self.device`` is not ``"cuda"`` (CPU JIT cost is negligible;
          the gate matches ``load()``'s ``effective_device == "cuda"``
          check so warm-up only fires when CUDA was actually used).
        """
        if self._model is None or self._processor is None:
            return
        if self.device != "cuda":
            return
        try:
            warmup_audio = np.zeros(int(WHISPER_SAMPLE_RATE * 0.5), dtype=np.float32)
            inputs = self._processor(
                [warmup_audio],
                sampling_rate=WHISPER_SAMPLE_RATE,
                return_tensors="pt",
            )
            inputs.to(device=self._model.device, dtype=self._model.dtype)
            with self._inference_mode_ctx():
                output = self._model.generate(
                    **inputs,
                    return_dict_in_generate=True,
                    stopping_criteria=[_AbortStoppingCriteria(self._abort_event)],
                )
            self._processor.decode(output.sequences, skip_special_tokens=True)
            log.debug("[PARAKEET] warm-up generate() completed — first dictation will be fast")
        except Exception as exc:
            log.debug("[PARAKEET] warm-up generate() failed (non-fatal): %s", exc)

    def unload(self) -> None:
        """Free model memory.

        also release PyTorch's CUDA caching allocator
        blocks via ``release_gpu_memory()`` so a subsequent backend
        switch (e.g. back to Whisper) can use the freed VRAM.  Without
        this, the cached blocks from the Parakeet model linger in the
        allocator and cause GPU OOMs after 2 backend switches on
        RTX 3060/4060 (8–12 GB VRAM).

        gc.collect() moved OUTSIDE the lock to avoid blocking
        is_loaded / transcribe for 10-100ms.
        """
        import gc

        # ``release_gpu_memory`` lives in the canonical
        # ``asr_utils`` module. ``transcription.py`` still re-exports the
        # name for backward compat, BUT because this is a LOCAL import
        # (not a module-level import), tests that want to intercept the
        # call MUST patch ``voice_typer.server.asr_utils.release_gpu_memory``
        # — patching ``voice_typer.server.transcription.release_gpu_memory``
        # does NOT intercept the local import resolution. (See  and
        # tests/regressions/gpu_memory_release_test.py
        # ::TestReleaseGpuMemoryFunctional::test_parakeet_unload_invokes_release.)
        from voice_typer.server.asr_utils import release_gpu_memory

        with self._inference_cond:
            # wait for any active transcription to finish before
            # nulling the model. ``transcribe()`` increments
            # ``_active_inference`` under this lock and decrements it in a
            # ``finally`` block; without this wait a concurrent
            # ``unload()`` would null ``self._model`` mid-inference and
            # trigger a use-after-free when the inference path dereferenced
            # the freed PyTorch module.
            while self._active_inference > 0:
                self._inference_cond.wait()
            self._model = None
            self._processor = None
        # gc.collect() OUTSIDE the lock
        gc.collect()
        # release CUDA cached blocks.
        release_gpu_memory()
        log.info("[PARAKEET] Model unloaded")

    @property
    def device_info(self) -> str:
        return f"parakeet/{self.device}"

    @property
    def loaded_via(self) -> str:
        return f"parakeet/{self.device}/{_PARAKERT_MODEL_ID}"
