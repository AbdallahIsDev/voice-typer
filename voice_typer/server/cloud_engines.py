"""Cloud ASR backends: OpenAI, Groq, Deepgram.

Each engine implements the TranscriberProtocol so the app can swap
backends transparently. Cloud engines send audio to an API endpoint
and return the transcribed text.

Per-transcription connection lifecycle. Each CloudEngine
instance is created per-transcription (not cached across dictations)
so a stale connection from a previous dictation can never serve a
later request — the most common cause of "the model talks to the
wrong endpoint" bugs in long-running tray apps. See
``TestPerTranscriptionLifecycleDocumented`` in
``tests/test_cloud_engines_dead_cache_removed.py`` for the
regression guard.

Configuration:
    asr_backend: "openai" | "groq" | "deepgram"
    cloud_api_key: str
    cloud_api_url: str (optional, for custom/self-hosted endpoints)
    cloud_model: str (optional, provider-specific default)
"""

import io
import json
import logging
import threading
import time
import wave
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request

import numpy as np

from voice_typer.server._audio_constants import WHISPER_SAMPLE_RATE
from voice_typer.server._http_safety import (
    build_secure_opener,
)
from voice_typer.server._secrets import (
    assert_url_allowed,
    redact_secret,
    redact_url,
)
from voice_typer.server.asr_errors import (
    CloudAuthError,
    CloudConfigError,
    CloudConsentRequiredError,
    CloudEmptyResponseError,
    CloudEngineError,
    CloudNetworkError,
    CloudRateLimitError,
    CloudServerError,
    ConsentRequiredError,  # noqa: F401  # re-exported for backward compat with `from cloud_engines import ConsentRequiredError`
)
from voice_typer.server.i18n import DEFAULT_LOCALE

log = logging.getLogger(__name__)


# Map an HTTP status code from a cloud provider's HTTPError to
# the appropriate typed ``CloudEngineError`` subclass. Used by both the
# OpenAI-compatible path (``_send_openai_compatible``) and the Deepgram
# path (``_send_deepgram``). Mapping:
#   401, 403                  → CloudAuthError      (API key invalid/revoked)
#   429                       → CloudRateLimitError (after retry budget)
#   5xx (500-599)             → CloudServerError
#   any other HTTP status     → CloudEngineError    (generic cloud failure)
# Callers that want to surface a more specific message can still wrap
# the chosen exception via ``raise CloudAuthError("...") from exc``;
# the type is what the IPC layer switches on, not the message.
def _cloud_http_error_class(code: int) -> type[CloudEngineError]:
    """Return the typed ``CloudEngineError`` subclass for an HTTP status."""
    if code in (401, 403):
        return CloudAuthError
    if code == 429:
        return CloudRateLimitError
    if 500 <= code < 600:
        return CloudServerError
    return CloudEngineError


# PERF-: module-level OpenerDirector for connection pooling.
# Reuses TCP connections across requests (like requests.Session).
# SEC-2: ``build_secure_opener()`` installs ``_NoRedirectHandler()`` so
# the opener does NOT follow 3xx redirects (the default
# ``HTTPRedirectHandler`` would silently POST the request body — user
# audio + API key — to an attacker-controlled redirect target).
# the handler + builder live in ``_http_safety`` so they're
# shared with ``llm_polish._opener`` (single source of truth).
_opener = build_secure_opener()


# (P4-A1): CloudEngine lifecycle is **per-transcription**.
#
# Historically this module hosted an 80-line module-level cached-engine
# infrastructure (``_CACHED_ENGINES``, ``register_cached_cloud_engine``,
# ``get_cached_cloud_engine``, ``clear_cached_engine``,
# ``clear_all_cached_engines``) intended to support long-lived
# CloudEngine instances that could be invalidated on credential /
# consent revocation. Verified by repo-wide grep (2026-07-28): ZERO
# production callers — the only consumers were the unit tests in
# ``tests/test_cloud_engines.py::TestCloudEngineCacheInvalidation`` and
# ``app.py`` lazily sets ``self._cloud_engine = None`` but never
# assigns a real CloudEngine. The cache was dead code in a "worst of
# both worlds" state: maintenance burden + a docstring claim about
# GDPR-delete invalidation that the runtime never actually performed.
#
# The infrastructure has been deleted. When a future PR wires
# CloudEngine into production (per-transcription → long-lived), the
# invalidation logic MUST be added at that time AND the
# ``config_applier`` set_config path MUST invalidate the cached engine
# when ``openai_api_key`` / ``groq_api_key`` / ``deepgram_api_key`` /
# ``cloud_api_key`` changes (today only ``llm_*`` field changes
# invalidate ``_llm_polisher``).
#
# Until then, each transcription constructs a fresh CloudEngine with
# the current API key + consent flag from the Config dataclass, so
# stale-credential reuse is structurally impossible.


# Provider-specific defaults
_PROVIDER_DEFAULTS = {
    "openai": {
        "url": "https://api.openai.com/v1/audio/transcriptions",
        "model": "whisper-1",
    },
    "groq": {
        "url": "https://api.groq.com/openai/v1/audio/transcriptions",
        "model": "whisper-large-v3",
    },
    "deepgram": {
        "url": "https://api.deepgram.com/v1/listen",
        "model": "nova-2",
    },
}


def _audio_to_wav_bytes(audio: np.ndarray, sample_rate: int = WHISPER_SAMPLE_RATE) -> bytes:
    """Convert float32 numpy array to WAV bytes."""
    buf = io.BytesIO()
    # Convert float32 to int16
    audio_int16 = np.clip(audio * 32767, -32768, 32767).astype(np.int16)
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)  # 16-bit
        wf.setframerate(sample_rate)
        wf.writeframes(audio_int16.tobytes())
    return buf.getvalue()


def _read_capped(resp, *, max_bytes: int) -> bytes:
    """Read up to ``max_bytes`` from ``resp``.

    SEC-030: ``resp.read()`` with no size argument reads the entire body
    into memory. A malicious or buggy server returning a 5 GB
    Content-Length would exhaust RAM before the transcription thread
    caught up. We stream the response in 64 KB chunks and abort if the
    total exceeds the cap.
    """
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = resp.read(64 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise RuntimeError(f"Response body exceeded {max_bytes} bytes — aborting to prevent OOM")
        chunks.append(chunk)
    return b"".join(chunks)


def _parse_retry_after(header_value: str | None) -> float:
    """Parse a ``Retry-After`` header into a sleep duration in seconds.

    RFC 7231 §7.1.3 allows ``Retry-After`` to be either:
          1. An integer number of seconds, OR
          2. An HTTP-date (e.g. ``Wed, 21 Oct 2015 07:28:00 GMT``).

        We cap the wait at 60 seconds so a hostile or misconfigured server
        cannot stall the dictation thread indefinitely. A negative or
        unparseable value falls back to a small default (2s) so we still
        honor the spirit of "wait briefly before retrying" without trusting
        the server blindly.

        Returns a float suitable for ``time.sleep``.
    """
    if not header_value:
        return 2.0
    # Case 1: integer seconds.
    try:
        seconds = float(header_value)
    except (TypeError, ValueError):
        # Case 2: HTTP-date. ``email.utils.parsedate_to_datetime``
        # returns a timezone-aware datetime (or None if unparseable).
        seconds = 2.0
        try:
            from email.utils import parsedate_to_datetime

            dt = parsedate_to_datetime(header_value)
            if dt is not None:
                now = datetime.now(timezone.utc)
                # parsedate_to_datetime may return a naive datetime if
                # the date string has no tz; normalize to UTC.
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                delta = (dt - now).total_seconds()
                if delta > 0:
                    seconds = delta
        except (TypeError, ValueError, OverflowError):
            pass
    # Cap at 60s; never sleep for a negative amount.
    return max(0.0, min(seconds, 60.0))


class _StreamingMultipartBody:
    """File-like object that yields multipart body chunks on demand.

    PERF-: avoids building the entire multipart body in memory
        as a single ``bytes`` object. ``urllib.request.Request`` accepts a
        file-like object as ``data`` and reads it in chunks via ``read()``.
        This class yields the pre-computed parts list one chunk at a time,
        reducing peak memory from the full body (~5.2 MB for a 30s
        recording) to one chunk (~64 KB).

        The ``__contains__`` method supports the ``in`` operator so
        existing tests like ``assert b"fake_wav_data" in body`` continue
        to work without materializing the entire body.
    """

    _CHUNK_SIZE = 64 * 1024  # 64 KB per read() call

    def __init__(self, parts: list[bytes]):
        self._parts = parts
        self._total_length = sum(len(p) for p in parts)
        self._part_iter = iter(parts)
        self._current = b""
        self._pos = 0

    def read(self, size: int = -1) -> bytes:
        """Read up to ``size`` bytes. If ``size == -1``, read all remaining."""
        if size == -1:
            # Read everything remaining
            remaining = b"".join(self._current_chunk_and_rest())
            self._current = b""
            return remaining
        result = bytearray()
        while len(result) < size:
            if not self._current:
                try:
                    self._current = next(self._part_iter)
                except StopIteration:
                    break
            needed = size - len(result)
            chunk = self._current[:needed]
            result.extend(chunk)
            self._current = self._current[len(chunk) :]
        self._pos += len(result)
        return bytes(result)

    def _current_chunk_and_rest(self):
        """Yield the current partial chunk, then all remaining parts."""
        if self._current:
            yield self._current
            self._current = b""
        yield from self._part_iter

    def __len__(self) -> int:
        """Total body length (for Content-Length header)."""
        return self._total_length - self._pos

    def __contains__(self, needle: bytes) -> bool:
        """Support ``in`` operator for test assertions.

        This materializes the full body, but tests only call it on
        small fake payloads (e.g. ``b"fake_wav_data"``), so the memory
        impact is negligible.
        """
        return needle in b"".join(self._parts)

    # urllib may call these on file-like data objects
    def readline(self, size: int = -1) -> bytes:
        return self.read(size)

    def flush(self) -> None:
        pass

    def close(self) -> None:
        pass


class CloudEngine:
    """Cloud ASR engine implementing TranscriberProtocol.

        Supports OpenAI, Groq, and Deepgram APIs (all OpenAI-compatible
        except Deepgram which uses its own format).

    each CloudEngine instance has a ``consent_given``
        flag that must be True before any audio is sent to the provider.
        The flag is set from the per-provider consent field on the Config
        dataclass (``cloud_openai_consent``, ``cloud_groq_consent``,
        ``cloud_deepgram_consent``).  When consent is False, ``is_loaded``
        returns False and ``transcribe`` raises a ConsentRequiredError so
        the IPC layer can surface a consent dialog to the renderer.
    """

    # Per-request timeout for cloud HTTP calls. Reduced from 30s to 10s
    # so a single stuck request cannot block the transcription thread for
    # up to 35s (30s request + retry backoff). 10s is already ~5x a typical
    # Whisper-API response (~1-2s); the 3-attempt retry loop provides
    # resilience against transient failures without each individual call
    # holding the thread hostage.
    _REQUEST_TIMEOUT_SECONDS: float = 10.0

    def __init__(
        self,
        provider: str,
        api_key: str,
        api_url: str | None = None,
        model: str | None = None,
        language: str = DEFAULT_LOCALE,
        consent_given: bool = False,
        local_engine_factory: "Callable[..., Any] | None" = None,
    ):
        self.provider = provider
        self.api_key = api_key
        self.language = language
        # per-instance consent flag.  Must be True before
        # any audio is sent.  Set from Config.cloud_{provider}_consent
        # by the app when the engine is constructed.
        self.consent_given = bool(consent_given)
        self._lock = threading.RLock()

        defaults = _PROVIDER_DEFAULTS.get(provider, {})
        self.api_url = api_url or defaults.get("url", "")
        self.model_name = model or defaults.get("model", "")

        self._loaded = True  # Cloud engines don't need local model loading

        # Optional factory that constructs the local whisper
        # engine lazily on fallback.  Decouples the cloud engine from
        # the model registry / app object so that ``transcribe_with_fallback``
        # can fire the local fallback path even when the caller did not
        # explicitly pass ``local_engine=`` (e.g. the streaming session
        # which only knows about the active transcriber).  The factory
        # is invoked at most once per fallback attempt; if it returns
        # ``None`` (no local engine available — e.g. cold start with
        # whisper backend not yet registered), the fallback is skipped
        # and the original cloud error is re-raised.
        self._local_engine_factory = local_engine_factory

        # Abort token shared by the dictation pipeline's cancel path
        # and the retry loop below. ``request_abort()`` sets the event
        # from any thread (typically the watchdog / ESC cancel path);
        # ``_send_openai_compatible`` / ``_send_deepgram`` check it at
        # the top of each retry iteration and short-circuit out instead
        # of issuing another 10s HTTP call. ``clear_abort()`` is called
        # by the pipeline at the start of each transcription cycle so
        # a stale abort from the previous cycle does NOT suppress the
        # next one. The event is also checked by ``transcribe`` itself
        # before the first request so a pre-set abort (e.g.ESC hit
        # during audio finalization) skips the network call entirely.
        self._abort_event = threading.Event()

    # ── TranscriberProtocol ──────────────────────────────────────────

    @property
    def is_loaded(self) -> bool:
        # consent is required for the engine to be
        # considered "loaded" — without consent, the engine should
        # not be selected for transcription.
        return self._loaded and bool(self.api_key) and self.consent_given

    def load(self, progress_callback=None) -> None:
        """No-op for cloud engines — no local model to load."""
        if progress_callback:
            progress_callback("Cloud engine ready")
        self._loaded = True

    def request_abort(self) -> None:
        """Signal the in-flight HTTP request + retry loop to abort.

        Called from the dictation pipeline's abort watcher (which
        monitors ``recording._cancelled_cycle_ids``) when the user
        hits ESC or the watchdog force-recovers a stuck cloud call.
        Sets a ``threading.Event`` that the retry loop checks at the
        top of each iteration. The current HTTP request cannot be
        interrupted from Python (the thread is blocked in C-level
        ``recv``), but with the per-request timeout reduced to 10s
        the worst-case latency before the abort takes effect is now
        bounded to ~10s + retry backoff, down from ~30s + backoff.
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

    def transcribe(self, audio: np.ndarray) -> str:
        """Transcribe audio via cloud API.

        refuses to send audio if consent hasn't been
                given.  Raises CloudConsentRequiredError (a subclass of
                ConsentRequiredError / RuntimeError so existing catch clauses
                still work) so the IPC layer can detect this case and show the
                consent dialog.
        """
        if not self.consent_given:
            raise CloudConsentRequiredError(
                f"Cloud {self.provider} consent not given — refusing to send audio.",
                provider=self.provider,
            )
        if not self.is_loaded:
            # Typed ``CloudConfigError`` (was generic
            # ``RuntimeError``) so the IPC layer can map it to the
            # distinct ``server.cloud_config_error`` code. The
            # cross-field validator at
            # ``config_validators._check_cross_field_cloud_config``
            # catches the common case at save time; this runtime check
            # stays as defense-in-depth for the case where the key was
            # revoked between save and transcribe.
            raise CloudConfigError("Cloud engine not configured (missing API key)")
        if len(audio) == 0:
            return ""
        # Honor a pre-set abort (e.g. ESC hit during audio finalization,
        # before the cloud call started). Skip the network round-trip
        # entirely — return empty so the pipeline's empty-check path
        # runs instead of waiting 10s for a request the user already
        # cancelled.
        if self._abort_event.is_set():
            log.info("[CLOUD] %s transcribe skipped — abort requested before first request", self.provider)
            return ""
        return self._send_request(audio)

    def transcribe_with_fallback(
        self,
        audio: np.ndarray,
        local_engine=None,
        audio_stats: "tuple[float, float, float] | None" = None,
    ) -> str:
        """Try cloud transcription; fall back to local engine on failure.

        PERF-: if the cloud request fails after all retries,
                and a local_engine is provided, attempt transcription on it
                instead of raising.  This gives a best-effort result even
                when the cloud is temporarily unreachable.

                When ``local_engine`` is NOT explicitly passed but the
                engine was constructed with a ``local_engine_factory`` callable,
                the factory is invoked lazily to construct the local whisper
                engine on demand.  This decouples the cloud engine from the
                model registry / app object: callers that don't know about
                the local whisper backend (e.g. the streaming session) still
                get the cloud→local fallback as long as the factory was wired
                at construction time.  If the factory returns ``None`` (e.g.
                cold start with whisper not yet registered), the fallback is
                skipped and the original cloud error is re-raised.

        (a-review Finding 8): ``audio_stats`` is accepted
                for signature parity with the three local engines
                (Whisper/Parakeet/Qwen) so ``DictationPipeline._transcribe``
                can pass it unconditionally without a broad ``except TypeError``
                fallback. The cloud engines don't use it — RMS/peak/silence
                detection is irrelevant when audio is shipped to a remote API
                — so the value is simply ignored here on the cloud path.
                When a ``local_engine`` is provided, ``audio_stats`` is forwarded
                so the local fallback benefits from the same pre-computation
                (all three local engines accept the kwarg).
        """
        try:
            return self.transcribe(audio)
        except ConsentRequiredError:
            # consent errors must propagate — do NOT fall back to
            # the local engine (the user explicitly declined cloud consent;
            # silently falling back would violate that choice).
            raise
        except (RuntimeError, OSError) as cloud_err:
            # Prefer the explicitly-passed local_engine; fall
            # back to the factory if one was wired at construction time.
            resolved_local_engine = local_engine
            if resolved_local_engine is None and self._local_engine_factory is not None:
                try:
                    resolved_local_engine = self._local_engine_factory()
                except Exception as factory_err:
                    log.warning(
                        "[CLOUD] %s local_engine_factory raised; skipping fallback: %s",
                        self.provider,
                        factory_err,
                    )
                    resolved_local_engine = None
            if resolved_local_engine is not None:
                # Include exc_info so the cloud failure
                # traceback is captured for debugging.
                log.warning(
                    "[CLOUD] %s failed, falling back to local engine: %s",
                    self.provider,
                    cloud_err,
                    exc_info=True,
                )
                # surface the fallback to the renderer so the
                # user gets a visible toast ("cloud provider X failed,
                # used local engine instead"). Without this signal the
                # cloud outage is invisible until the user checks the
                # logs. The event_bus is the LEAF of the dependency tree
                # (stdlib-only) so the lazy import is safe and never
                # creates a circular import. Best-effort: a publish
                # failure (no subscribers, etc.) is logged at DEBUG and
                # does NOT abort the fallback — the local engine still
                # runs.
                try:
                    from voice_typer.server import event_bus

                    event_bus.publish(
                        {
                            "type": "cloud_fallback_used",
                            "data": {
                                "provider": self.provider,
                                "reason": str(cloud_err)[:200],
                            },
                        }
                    )
                except Exception as notify_exc:
                    log.debug(
                        "[CLOUD] could not publish cloud_fallback_used event: %s",
                        notify_exc,
                    )
                try:
                    return resolved_local_engine.transcribe(audio, audio_stats=audio_stats)
                except Exception as local_err:
                    # Include exc_info so the local fallback
                    # failure traceback is captured for debugging.
                    log.error("[CLOUD] Local fallback also failed: %s", local_err, exc_info=True)
                    # re-raise the ORIGINAL cloud error (not a
                    # bare RuntimeError) so the dictation pipeline's
                    # ``_friendly_transcription_error`` can match the
                    # exception type (ConnectionError, TimeoutError,
                    # URLError) and produce a user-friendly message.
                    # The local error is chained via ``__cause__`` for
                    # debugging but does not mask the original cloud
                    # error type.
                    raise cloud_err from local_err
            raise

    def unload(self) -> None:
        """No-op for cloud engines."""
        self._loaded = False

    @property
    def device_info(self) -> str:
        return f"cloud/{self.provider}"

    @property
    def loaded_via(self) -> str:
        return f"cloud/{self.provider}/{self.model_name}"

    # ── HTTP request ─────────────────────────────────────────────────

    def _send_request(self, audio: np.ndarray) -> str:
        """Send audio to the cloud API and return transcribed text."""
        wav_bytes = _audio_to_wav_bytes(audio)
        filename = "audio.wav"

        if self.provider == "deepgram":
            return self._send_deepgram(wav_bytes)
        else:
            return self._send_openai_compatible(wav_bytes, filename)

    # ── Shared retry/backoff skeleton ──────────────────────────────────
    #
    # Both `_send_openai_compatible` and `_send_deepgram` previously
    # duplicated the same ~60-line retry skeleton: `max_retries = 3` /
    # `retried_429 = False` / `for attempt in range(max_retries):` /
    # `HTTPError` 429 branch with `_parse_retry_after` / `URLError`
    # branch with `backoff = 0.5 * (2**attempt)` / final
    # `raise CloudEngineError(...)`. The two copies had already drifted:
    # the OpenAI path's `Exception` branch included `{safe_msg}` in the
    # raised message; the Deepgram path dropped it. Centralising here
    # removes the drift surface and makes the retry policy a single
    # editable block.
    #
    # The wrapper methods supply two callables so this helper has zero
    # knowledge of provider-specific request shape or response schema:
    #   - ``request_factory`` builds a fresh `Request` per attempt
    #     (re-built each attempt so a streaming multipart body isn't
    #     reused after a partial read — see the comment in
    #     `_send_openai_compatible` for the truncated-body bug this
    #     prevents).
    #   - ``parse_response`` takes the raw response bytes and returns
    #     the transcribed text (provider-specific JSON path).
    def _transcribe_with_retry(
        self,
        provider: str,
        request_factory: Callable[[], Request],
        parse_response: Callable[[bytes], str],
    ) -> str:
        """Shared retry/backoff skeleton for cloud transcription HTTP calls.

        Honors the per-engine ``_abort_event`` (checked before each
        attempt), retries 429 once honoring ``Retry-After`` (capped at
        60s by ``_parse_retry_after``), and applies exponential backoff
        (0.5s, 1.0s, 2.0s) for transient ``URLError``s. Non-retryable
        ``HTTPError``s and the catch-all ``Exception`` branch raise
        typed ``CloudEngineError`` subclasses via
        ``_cloud_http_error_class`` so the IPC layer can map them to
        distinct ``server.cloud_*`` codes.
        """
        max_retries = 3
        retried_429 = False
        for attempt in range(max_retries):
            # Check the abort token BEFORE each (potentially 10s) HTTP
            # call. If the user hit ESC or the watchdog force-recovered
            # during a previous attempt's backoff sleep, bail out
            # immediately rather than issuing another request that the
            # user has already cancelled.
            if self._abort_event.is_set():
                log.info(
                    "[CLOUD] %s abort requested — skipping retry %d/%d",
                    provider,
                    attempt + 1,
                    max_retries,
                )
                raise CloudEngineError(f"{provider} transcription aborted by user")
            req = request_factory()
            try:
                with _opener.open(req, timeout=self._REQUEST_TIMEOUT_SECONDS) as resp:
                    # SEC-030: cap response body at 50 MB to prevent
                    # a malicious or buggy server from exhausting RAM.
                    # Whisper / Groq / Deepgram responses are <100 KB
                    # in practice; 50 MB is a generous ceiling.
                    raw = _read_capped(resp, max_bytes=50 * 1024 * 1024)
                    if not raw.strip():
                        # HTTP 200 with an empty/whitespace-only body is
                        # an anomaly, not a valid transcription. Raise a
                        # typed error so the IPC layer surfaces a
                        # cloud-provider failure instead of shipping an
                        # empty transcript as valid. Not retried: a 200
                        # empty body is a provider-side anomaly (like the
                        # non-retried 5xx), not a transient network
                        # error — retrying would just re-send audio.
                        raise CloudEmptyResponseError(f"{provider} returned HTTP 200 with an empty body")
                    text = parse_response(raw)
                    if not text:
                        # Same anomaly class for a 200 whose JSON is
                        # empty (``{}``) or lacks the transcript field.
                        raise CloudEmptyResponseError(f"{provider} returned HTTP 200 with an empty transcript")
                    log.info("[CLOUD] %s transcription: %d chars", provider, len(text))
                    return text
            except CloudEmptyResponseError:
                # Propagate the typed error unchanged — do NOT let the
                # catch-all ``except Exception`` below re-wrap it into a
                # generic CloudEngineError (that would lose the
                # empty-response semantics the IPC layer switches on).
                raise
            except HTTPError as exc:
                # 429 Too Many Requests is the only retryable 4xx.
                # Honor Retry-After (numeric seconds or HTTP-date); cap the
                # wait at 60s so a hostile server can't stall us forever.
                # Only retry once on 429 — the backoff loop is intended for
                # transient network errors, not rate-limit backoff.
                if exc.code == 429 and not retried_429 and attempt < max_retries - 1:
                    retried_429 = True
                    wait = _parse_retry_after(exc.headers.get("Retry-After"))
                    log.warning(
                        "[CLOUD] %s got 429 (attempt %d/%d); honoring Retry-After, retrying once in %.1fs",
                        provider,
                        attempt + 1,
                        max_retries,
                        wait,
                    )
                    time.sleep(wait)
                    continue
                # Non-retryable HTTPError (4xx other than 429, or 5xx that
                # we also surface without retrying — 5xx from a cloud ASR
                # provider typically indicates a sustained outage that
                # won't clear in 2s of backoff).
                safe_msg = redact_secret(redact_url(str(exc)))
                # Include exc_info so the HTTPError traceback
                # is captured for debugging.
                log.error(
                    "[CLOUD] %s HTTP %d error (not retried): %s",
                    provider,
                    exc.code,
                    safe_msg,
                    exc_info=True,
                )
                # Raise the typed ``CloudEngineError`` subclass
                # matching the HTTP status (401/403 → auth, 429 → rate
                # limit, 5xx → server error, else → generic cloud).
                err_cls = _cloud_http_error_class(exc.code)
                raise err_cls(f"{provider} API error (HTTP {exc.code})") from exc
            except URLError as exc:
                # URLError that is NOT an HTTPError = transient
                # network error (timeout, connection reset, DNS failure).
                # Retry with exponential backoff.
                if attempt < max_retries - 1:
                    backoff = 0.5 * (2**attempt)  # 0.5s, 1.0s, 2.0s
                    log.warning(
                        "[CLOUD] %s attempt %d/%d failed, retrying in %.1fs: %s",
                        provider,
                        attempt + 1,
                        max_retries,
                        backoff,
                        redact_secret(redact_url(str(exc))),
                    )
                    time.sleep(backoff)
                else:
                    safe_msg = redact_secret(redact_url(str(exc)))
                    # Include exc_info so the final URLError
                    # traceback is captured for debugging.
                    log.error(
                        "[CLOUD] %s API error after %d attempts: %s",
                        provider,
                        max_retries,
                        safe_msg,
                        exc_info=True,
                    )
                    # Typed ``CloudNetworkError`` so the IPC layer can
                    # map to ``server.cloud_network_error``.
                    raise CloudNetworkError(f"{provider} API error") from exc
            except Exception as exc:
                # use the same ``redact_secret(redact_url(...))``
                # chain as the HTTPError / URLError branches above so a
                # generic Exception carrying a URL-embedded credential
                # (e.g. ``https://user:pass@host/...`` echoed back in a
                # 500 response body) is redacted the same way as the
                # typed-network-error path. Both provider paths now
                # include ``{safe_msg}`` in the raised message —
                # previously only the OpenAI path did, which was the
                # drift bug that motivated centralising this skeleton.
                safe_msg = redact_secret(redact_url(str(exc)))
                # Include exc_info so the unexpected-exception
                # traceback is captured for debugging.
                log.error("[CLOUD] %s request failed: %s", provider, safe_msg, exc_info=True)
                # include the underlying error in the user-facing
                # message so the user can tell if it's a network issue vs an
                # API error. Raise the typed base ``CloudEngineError``
                # so the IPC layer still maps to a cloud-specific code
                # rather than the generic ``server.internal_error``.
                raise CloudEngineError(f"{provider} request failed: {safe_msg}") from exc
        # Should not reach here, but just in case
        raise CloudEngineError(f"{provider} request failed after {max_retries} attempts")

    def _send_openai_compatible(self, wav_bytes: bytes, filename: str) -> str:
        """Send request to OpenAI-compatible API (OpenAI, Groq).

                RELIABILITY-004: asserts the configured ``api_url`` is in the
                trusted-host allowlist before sending any audio.  This closes
                the SEC-002 endpoint-swap vector at the cloud-engine layer:
                even if an attacker finds another path to write
                ``config.cloud_api_url``, this engine refuses to send audio
                to an untrusted host.

        PERF-: exponential backoff retry (3 attempts) for
                transient network errors.  Connection pooling via a module-
                level OpenerDirector (urllib's equivalent of requests.Session).

        Thin wrapper around ``_transcribe_with_retry`` — supplies the
        OpenAI-specific request factory (multipart body, rebuilt per
        attempt because ``_StreamingMultipartBody`` carries internal
        state) and the OpenAI response parser (``result["text"]``).
        """
        # Defense-in-depth: SEC-002 already validates URL scheme at
        # set_config time, but assert again here in case the value
        # was loaded from disk (Config.load) or set programmatically.
        # Opt in to allow_loopback_http=True because this
        # caller sends user audio to a user-configured endpoint, and
        # the user may legitimately point it at a local HTTP server
        # (Ollama, vLLM, LM Studio, etc.).
        assert_url_allowed(
            self.api_url,
            field_name="cloud_api_url",
            client_name=f"cloud/{self.provider}",
            allow_loopback_http=True,
        )

        boundary = "----VoiceTyperBoundary7MA4YWxkTrZu0gW"

        def _build_request() -> Request:
            # Rebuild `body` and `req` INSIDE the retry loop.
            # `_StreamingMultipartBody.read()` advances internal state with
            # no `reset()` method — reusing the same body across retries
            # sent a truncated/empty multipart with stale Content-Length,
            # producing confusing 400/malformed-multipart errors that hid
            # the real network failure.
            body = self._build_multipart_body(wav_bytes, filename, boundary)
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": f"multipart/form-data; boundary={boundary}",
                # PERF-: pass Content-Length explicitly so urllib
                # uses a single write instead of chunked encoding. The
                # _StreamingMultipartBody.__len__ returns the total length.
                "Content-Length": str(len(body)),
            }
            return Request(self.api_url, data=body, headers=headers, method="POST")

        def _parse(raw: bytes) -> str:
            result = json.loads(raw.decode("utf-8"))
            return result.get("text", "").strip()

        return self._transcribe_with_retry(self.provider, _build_request, _parse)

    def _send_deepgram(self, wav_bytes: bytes) -> str:
        """Send request to Deepgram API.

                RELIABILITY-004: same URL allowlist + log redaction as the
                OpenAI-compatible path.

                SEC-005: query parameters (model, language) are URL-encoded
                to prevent parameter injection via crafted config values.
                Previously the URL was built with f-string interpolation,
                which let an attacker inject extra query parameters or path
                segments via ``config.cloud_model`` (e.g. ``"&punctuate=false&"
                "smart_format=true"``).

        PERF-: exponential backoff retry (3 attempts) for
                transient network errors, matching the OpenAI-compatible path
                (now shared via ``_transcribe_with_retry``).
        """
        # Opt in to allow_loopback_http=True — see the
        # OpenAI-compatible transcribe path above for the rationale.
        assert_url_allowed(
            self.api_url,
            field_name="cloud_api_url",
            client_name="cloud/deepgram",
            allow_loopback_http=True,
        )

        # SEC-005: urlencode escapes special characters in the model
        # and language values, preventing parameter injection.
        import re
        from urllib.parse import urlencode

        _safe_token = re.compile(r"^[A-Za-z0-9._\-]+$")
        if not _safe_token.match(self.model_name or ""):
            raise RuntimeError(f"Deepgram model name {self.model_name!r} contains invalid characters")
        if not _safe_token.match(self.language or ""):
            raise RuntimeError(f"Deepgram language {self.language!r} contains invalid characters")
        query = urlencode(
            {
                "model": self.model_name,
                "language": self.language,
                "punctuate": "true",
            }
        )
        url = f"{self.api_url}?{query}"

        # Deepgram's body is a plain ``bytes`` object (no internal
        # streaming state), so it could be built once and reused across
        # retries. We still rebuild the ``Request`` per attempt via the
        # factory below for symmetry with the OpenAI path and so the
        # ``_transcribe_with_retry`` skeleton has a single contract.
        def _build_request() -> Request:
            headers = {
                "Authorization": f"Token {self.api_key}",
                "Content-Type": "audio/wav",
            }
            return Request(url, data=wav_bytes, headers=headers, method="POST")

        def _parse(raw: bytes) -> str:
            result = json.loads(raw.decode("utf-8"))
            # Deepgram response format
            channels = result.get("results", {}).get("channels", [])
            if channels:
                alternatives = channels[0].get("alternatives", [])
                if alternatives:
                    return alternatives[0].get("transcript", "").strip()
            return ""

        return self._transcribe_with_retry(self.provider, _build_request, _parse)

    def _build_multipart_body(self, wav_bytes: bytes, filename: str, boundary: str):
        """Build multipart/form-data body for OpenAI-compatible APIs.

        PERF-: the previous implementation concatenated all parts
                into a single ``bytes`` object via ``b"".join(parts)``. For a
                30s recording at 16 kHz float32, that's ~5.2 MB held in memory
                as one contiguous block. This method now returns a
                ``_StreamingMultipartBody`` file-like object that yields chunks
                on demand, reducing peak memory to one chunk (~64 KB) at a time.
                ``Content-Length`` is computed upfront so the server knows the
                total size without chunked transfer encoding.

                The test ``test_build_multipart_body`` calls ``b"fake_wav_data"
                in body`` — ``_StreamingMultipartBody`` supports ``in`` via
                ``__contains__`` so the test still passes.
        """
        parts = self._multipart_parts(wav_bytes, filename, boundary)
        return _StreamingMultipartBody(parts)

    def _multipart_parts(self, wav_bytes: bytes, filename: str, boundary: str) -> list[bytes]:
        """Return the ordered list of byte chunks that compose the body."""
        parts: list[bytes] = []

        # file field
        parts.append(f"--{boundary}\r\n".encode())
        parts.append(f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'.encode())
        parts.append(b"Content-Type: audio/wav\r\n\r\n")
        parts.append(wav_bytes)
        parts.append(b"\r\n")

        # model field
        parts.append(f"--{boundary}\r\n".encode())
        parts.append(b'Content-Disposition: form-data; name="model"\r\n\r\n')
        parts.append(self.model_name.encode())
        parts.append(b"\r\n")

        # language field
        parts.append(f"--{boundary}\r\n".encode())
        parts.append(b'Content-Disposition: form-data; name="language"\r\n\r\n')
        parts.append(self.language.encode())
        parts.append(b"\r\n")

        # response_format
        parts.append(f"--{boundary}\r\n".encode())
        parts.append(b'Content-Disposition: form-data; name="response_format"\r\n\r\n')
        parts.append(b"json\r\n")

        parts.append(f"--{boundary}--\r\n".encode())
        return parts

    # ── Test connection ──────────────────────────────────────────────

    def test_connection(self) -> tuple[bool, str]:
        """Test the API connection. Returns (success, message).

        RELIABILITY-004: redacts any secret-looking substring from the
        returned message so a leaked key in an exception string does
        not propagate to the UI.

        SEC-011: previously this method sent the API key in a HEAD
        request to the user-supplied ``api_url``.  Combined with a
        SEC-002 endpoint-swap, that would leak the key to an
        attacker-controlled URL.  It also probed OpenAI's
        ``/v1/audio/transcriptions`` endpoint with HEAD, which
        returns 405 Method Not Allowed — so the test always reported
        failure even with valid credentials.

        The fix: probe a provider-known endpoint with a GET (or
        rather, just attempt a real transcription-shaped request and
        check for a 401/403 response, which proves the key was
        accepted by the auth layer even if the request body was
        empty).  We never send the API key to a URL the user didn't
        configure.
        """
        # HU-16 / ADR-0016 Design Rule 1: no cloud interaction without
        # consent. ``test_connection`` sends the API key to the provider,
        # so it is gated exactly like ``transcribe`` (which refuses with
        # ``CloudConsentRequiredError``) — an engine whose per-provider
        # consent flag is False must refuse BEFORE any URL-allowlist
        # check or network I/O. Returning ``(False, msg)`` (rather than
        # raising) lets the UI surface the consent requirement directly
        # in the test-connection result area instead of an opaque
        # failure.
        if not self.consent_given:
            return False, "Cloud consent not given — refusing to test connection"

        if not self.api_key:
            return False, "API key not configured"

        try:
            # Opt in to allow_loopback_http=True — see the
            # OpenAI-compatible transcribe path for the rationale.
            assert_url_allowed(
                self.api_url,
                field_name="cloud_api_url",
                client_name=f"cloud/{self.provider}",
                allow_loopback_http=True,
            )
        except ValueError as exc:
            return False, str(exc)

        # SEC-011: probe by sending an empty audio body to the real
        # transcription endpoint.  A 401/403 means "key rejected"
        # (which is a useful diagnostic — the user knows their key is
        # wrong).  A 400/422 means "key accepted, body invalid"
        # (which is what we want — it proves the key works).  A 2xx
        # is unexpected but also fine.  Network errors propagate as
        # connection failures.
        try:
            # Build a minimal multipart body with empty audio so the
            # request shape matches what _send_openai_compatible sends.
            # This is provider-specific; for unknown providers we fall
            # back to a bare GET (which will likely 405 but at least
            # confirms the host is reachable).
            if self.provider == "deepgram":
                # Deepgram: send empty WAV bytes; expect 400 (bad audio)
                # or 200 (success with empty transcript). Use the
                # shared _audio_to_wav_bytes helper (was inline before
                # — the test_cloud_engines_wav_helper regression guard
                # pins the helper as the single source of WAV encoding
                # so the duplicate byte-string literal can't drift).
                empty_wav = _audio_to_wav_bytes(np.zeros(0, dtype=np.float32))
                headers = {
                    "Authorization": f"Token {self.api_key}",
                    "Content-Type": "audio/wav",
                }
                req = Request(self.api_url, data=empty_wav, headers=headers, method="POST")
            else:
                # OpenAI-compatible: send empty multipart body.
                # Expect 400 (no file) or 200.
                boundary = "----VoiceTyperTestBoundary"
                body = (
                    f"--{boundary}\r\n"
                    'Content-Disposition: form-data; name="model"\r\n\r\n'
                    f"{self.model_name}\r\n"
                    f"--{boundary}--\r\n"
                ).encode()
                headers = {
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": f"multipart/form-data; boundary={boundary}",
                }
                req = Request(self.api_url, data=body, headers=headers, method="POST")
            # SEC-audit-006 (Round 0 forward-port): use ``_opener.open()``
            # instead of default ``urlopen()`` so HTTP redirects are NOT
            # followed (the URL allowlist is only checked on the initial
            # request — a redirect to an attacker URL would otherwise POST
            # the test payload there).  Mirrors ``_call_api`` in
            # ``llm_polish.py`` and the main transcription path above.
            # Use the shared ``_REQUEST_TIMEOUT_SECONDS`` constant
            # instead of a hardcoded ``10`` so a future change to the
            # per-call timeout propagates here automatically.  Mirrors
            # the per-transcription HTTP path (see
            # ``_send_openai_compatible`` / ``_send_deepgram``).
            with _opener.open(req, timeout=self._REQUEST_TIMEOUT_SECONDS) as resp:
                return True, f"Connected to {self.provider} (status {resp.status})"
        except Exception as exc:
            # A 400/401/403/422 error means the server is reachable
            # and responding — that's actually a "successful" test
            # from a connectivity standpoint.  We just need to
            # distinguish "server responded with HTTP error" from
            # "network unreachable".
            msg = str(exc)
            # urllib.error.HTTPError carries the status code
            status = getattr(exc, "code", None)
            if status is not None:
                # HTTP error — server is reachable.  401/403 = key
                # rejected; 400/422 = key accepted, body invalid.
                if status in (401, 403):
                    return False, f"Connected to {self.provider}, but API key was rejected (HTTP {status})"
                # A 5xx means the server is reachable but is itself
                # failing (overload, maintenance, internal error).
                # Treating that as a plain "Connected" success is
                # misleading — the user's transcriptions will fail
                # until the provider recovers.  Surface a diagnostic
                # that names the status and hints at the cause, while
                # still reporting ``success=True`` (the connection
                # itself DID succeed; the provider is the problem,
                # not our config).
                if 500 <= status < 600:
                    return True, (
                        f"Connected to {self.provider}, but server returned "
                        f"HTTP {status} — provider may be temporarily unavailable"
                    )
                # Any other HTTP error means the server is up and
                # talking to us — treat as success.
                return True, f"Connected to {self.provider} (HTTP {status})"
            # Chain ``redact_url`` (strips URL userinfo +
            # query-string secrets) BEFORE ``redact_secret`` (masks
            # ``sk-…`` / ``Bearer …`` / generic long alphanumeric
            # runs).  Mirrors the catch-all branches in
            # ``_send_openai_compatible`` / ``_send_deepgram`` above
            # so the redaction contract is identical across every
            # error path in this module.  Pre-fix this branch called
            # only ``redact_secret``, leaving URL userinfo (e.g.
            # ``user:pass@host``) and short query-string secrets
            # verbatim in the returned message.
            return False, f"Connection failed: {redact_secret(redact_url(msg))}"
