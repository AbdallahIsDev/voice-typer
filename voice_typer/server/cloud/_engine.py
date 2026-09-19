"""CloudEngine: the cloud ASR engine implementing TranscriberProtocol.

Engine-dispatch half of the ``cloud_engines.py`` monolith split: the
full ``CloudEngine`` class (lifecycle, consent gate, shared retry
skeleton, provider send paths, connection probe) lives HERE; the
stateless plumbing (transport, retry policy, provider defaults,
request shaping) lives in the sibling leaf modules
(:mod:`._transport`, :mod:`._retry`, :mod:`._defaults`,
:mod:`._providers.*`).

FACADE-NAMESPACE PATCH CONTRACT: tests and production patch
engine-adjacent singletons through the facade module's namespace
(``setattr(cloud_engines, "_opener", mock)``,
``patch("voice_typer.server.cloud_engines.assert_url_allowed")``).
This module therefore resolves ``_opener`` and ``assert_url_allowed``
from the facade namespace at call time (see :func:`_facade`) instead
of importing them statically. Every other dependency is imported
statically from its owning leaf module.
"""

from __future__ import annotations

import json
import logging
import threading
from collections.abc import Callable
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request

import numpy as np

from voice_typer.server._secrets import redact_secret, redact_url
from voice_typer.server.asr_errors import (
    CloudConfigError,
    CloudConsentRequiredError,
    CloudEmptyResponseError,
    CloudEngineError,
    CloudNetworkError,
    ConsentRequiredError,
)
from voice_typer.server.cloud._defaults import _PROVIDER_DEFAULTS
from voice_typer.server.cloud._providers.deepgram import build_listen_url
from voice_typer.server.cloud._providers.openai import build_multipart_body, build_multipart_parts
from voice_typer.server.cloud._retry import _cloud_http_error_class, _parse_retry_after
from voice_typer.server.cloud._transport import _audio_to_wav_bytes, _read_capped
from voice_typer.server.i18n import DEFAULT_LOCALE

log = logging.getLogger(__name__)


def _facade():
    """Resolve the compatibility facade namespace at call time.

    The facade module (``voice_typer.server.cloud_engines``) owns the
    engine-adjacent singletons tests rebind to steer the engine
    (``_opener``, ``assert_url_allowed``). Reading them through the
    facade at call time, instead of importing them at module level —
    keeps that contract intact now that the class body lives in this
    leaf. Call-time-only import: the facade imports this package at
    module level, so the facade is always fully initialized by the
    time an engine method runs (no import cycle).
    """
    from voice_typer.server import cloud_engines

    return cloud_engines


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
    _REQUEST_TIMEOUT_SECONDS: float = 10.0

    def __init__(
        self,
        provider: str,
        api_key: str,
        api_url: str | None = None,
        model: str | None = None,
        language: str = DEFAULT_LOCALE,
        consent_given: bool = False,
        local_engine_factory: Callable[..., Any] | None = None,
    ):
        self.provider = provider
        self.api_key = api_key
        self.language = language
        # per-instance consent flag.  Must be True before
        self.consent_given = bool(consent_given)
        self._lock = threading.RLock()

        defaults = _PROVIDER_DEFAULTS.get(provider, {})
        self.api_url = api_url or defaults.get("url", "")
        self.model_name = model or defaults.get("model", "")

        self._loaded = True  # Cloud engines don't need local model loading

        # Optional factory that constructs the local whisper
        self._local_engine_factory = local_engine_factory

        # Abort token shared by the dictation pipeline's cancel path
        self._abort_event = threading.Event()

    @property
    def is_loaded(self) -> bool:
        # consent is required for the engine to be
        return self._loaded and bool(self.api_key) and self.consent_given

    def load(self, progress_callback=None) -> None:
        """No-op for cloud engines, no local model to load."""
        if progress_callback:
            progress_callback("Cloud engine ready")
        self._loaded = True

    def request_abort(self) -> None:
        """Signal the in-flight HTTP request + retry loop to abort.

        Called from the dictation pipeline's abort watcher (which
        monitors ``recording._cancelled_cycle_ids``) when the user
        hits ESC or the watchdog force-recovers a stuck cloud call.
        Sets a ``threading.Event`` that the retry loop checks at the
        top of each iteration AND inside every retry backoff /
        Retry-After wait (``Event.wait(timeout=...)`` returns early
        when the event is set, so an abort during a 60s rate-limit
        wait takes effect immediately). The current HTTP request
        cannot be interrupted from Python (the thread is blocked in
        C-level ``recv``), but with the per-request timeout reduced to
        10s the worst-case latency before the abort takes effect is
        now bounded to ~10s, down from ~30s.
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
                f"Cloud {self.provider} consent not given, refusing to send audio.",
                provider=self.provider,
            )
        if not self.is_loaded:
            # Typed ``CloudConfigError`` (was generic
            raise CloudConfigError("Cloud engine not configured (missing API key)")
        if len(audio) == 0:
            return ""
        # Honor a pre-set abort (e.g. ESC hit during audio finalization,
        if self._abort_event.is_set():
            log.info("[CLOUD] %s transcribe skipped, abort requested before first request", self.provider)
            return ""
        return self._send_request(audio)

    def transcribe_with_fallback(
        self,
        audio: np.ndarray,
        local_engine=None,
        audio_stats: tuple[float, float, float] | None = None,
    ) -> str:
        """Try cloud transcription; fall back to local engine on failure.

        PERF: if the cloud request fails after all retries,
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

        Signature note: ``audio_stats`` is accepted
                for signature parity with the three local engines
                (Whisper/Parakeet/Qwen) so ``DictationPipeline._transcribe``
                can pass it unconditionally without a broad ``except TypeError``
                fallback. The cloud engines don't use it, RMS/peak/silence
                detection is irrelevant when audio is shipped to a remote API
               , so the value is simply ignored here on the cloud path.
                When a ``local_engine`` is provided, ``audio_stats`` is forwarded
                so the local fallback benefits from the same pre-computation
                (all three local engines accept the kwarg).
        """
        try:
            return self.transcribe(audio)
        except ConsentRequiredError:
            # consent errors must propagate, do NOT fall back to
            raise
        except (RuntimeError, OSError) as cloud_err:
            # Prefer the explicitly-passed local_engine; fall
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
                log.warning(
                    "[CLOUD] %s failed, falling back to local engine: %s",
                    self.provider,
                    cloud_err,
                    exc_info=True,
                )
                # surface the fallback to the renderer so the
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
                    log.error("[CLOUD] Local fallback also failed: %s", local_err, exc_info=True)
                    # re-raise the ORIGINAL cloud error (not a
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

    def _send_request(self, audio: np.ndarray) -> str:
        """Send audio to the cloud API and return transcribed text."""
        wav_bytes = _audio_to_wav_bytes(audio)
        filename = "audio.wav"

        if self.provider == "deepgram":
            return self._send_deepgram(wav_bytes)
        else:
            return self._send_openai_compatible(wav_bytes, filename)

    # Both `_send_openai_compatible` and `_send_deepgram` previously
    def _transcribe_with_retry(
        self,
        provider: str,
        request_factory: Callable[[], Request],
        parse_response: Callable[[bytes], str],
    ) -> str:
        """Shared retry/backoff skeleton for cloud transcription HTTP calls.

        Honors the per-engine ``_abort_event`` (checked before each
        attempt AND interruptibly during every Retry-After / backoff
        wait, so an ESC-abort takes effect mid-wait instead of at the
        top of the next attempt), retries 429 once honoring
        ``Retry-After`` (capped at 60s by ``_parse_retry_after``), and
        applies exponential backoff
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
            if self._abort_event.is_set():
                log.info(
                    "[CLOUD] %s abort requested, skipping retry %d/%d",
                    provider,
                    attempt + 1,
                    max_retries,
                )
                raise CloudEngineError(f"{provider} transcription aborted by user")
            req = request_factory()
            try:
                # The opener singleton resolves through the facade
                opener = _facade()._opener
                with opener.open(req, timeout=self._REQUEST_TIMEOUT_SECONDS) as resp:
                    # SEC-030: cap response body at 50 MB to prevent
                    raw = _read_capped(resp, max_bytes=50 * 1024 * 1024)
                    if not raw.strip():
                        # HTTP 200 with an empty/whitespace-only body is
                        raise CloudEmptyResponseError(f"{provider} returned HTTP 200 with an empty body")
                    text = parse_response(raw)
                    if not text:
                        # Same anomaly class for a 200 whose JSON is
                        raise CloudEmptyResponseError(f"{provider} returned HTTP 200 with an empty transcript")
                    log.info("[CLOUD] %s transcription: %d chars", provider, len(text))
                    return text
            except CloudEmptyResponseError:
                # Propagate the typed error unchanged, do NOT let the
                raise
            except HTTPError as exc:
                # 429 Too Many Requests is the only retryable 4xx.
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
                    # Interruptible wait: ``Event.wait`` returns True the
                    if self._abort_event.wait(timeout=wait):
                        log.info(
                            "[CLOUD] %s abort requested, aborting Retry-After wait",
                            provider,
                        )
                        raise CloudEngineError(f"{provider} transcription aborted by user") from exc
                    continue
                # Non-retryable HTTPError (4xx other than 429, or 5xx that
                safe_msg = redact_secret(redact_url(str(exc)))
                # Include exc_info so the HTTPError traceback
                log.error(
                    "[CLOUD] %s HTTP %d error (not retried): %s",
                    provider,
                    exc.code,
                    safe_msg,
                    exc_info=True,
                )
                # Raise the typed ``CloudEngineError`` subclass
                err_cls = _cloud_http_error_class(exc.code)
                raise err_cls(f"{provider} API error (HTTP {exc.code})") from exc
            except URLError as exc:
                # URLError that is NOT an HTTPError = transient
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
                    # Interruptible wait (same rationale as the 429 branch
                    if self._abort_event.wait(timeout=backoff):
                        log.info(
                            "[CLOUD] %s abort requested, aborting backoff wait",
                            provider,
                        )
                        raise CloudEngineError(f"{provider} transcription aborted by user") from exc
                else:
                    safe_msg = redact_secret(redact_url(str(exc)))
                    # Include exc_info so the final URLError
                    log.error(
                        "[CLOUD] %s API error after %d attempts: %s",
                        provider,
                        max_retries,
                        safe_msg,
                        exc_info=True,
                    )
                    # Typed ``CloudNetworkError`` so the IPC layer can
                    raise CloudNetworkError(f"{provider} API error") from exc
            except Exception as exc:
                # use the same ``redact_secret(redact_url(...))``
                safe_msg = redact_secret(redact_url(str(exc)))
                # Include exc_info so the unexpected-exception
                log.error("[CLOUD] %s request failed: %s", provider, safe_msg, exc_info=True)
                # include the underlying error in the user-facing
                raise CloudEngineError(f"{provider} request failed: {safe_msg}") from exc
        # Should not reach here, but just in case
        raise CloudEngineError(f"{provider} request failed after {max_retries} attempts")

    def _send_openai_compatible(self, wav_bytes: bytes, filename: str) -> str:
        """Send request to OpenAI-compatible API (OpenAI, Groq).

        URL allowlist: asserts the configured ``api_url`` is in the
        trusted-host allowlist before sending any audio.  This closes
        the SEC-002 endpoint-swap vector at the cloud-engine layer:
        even if an attacker finds another path to write
        ``config.cloud_api_url``, this engine refuses to send audio
        to an untrusted host.

        PERF: exponential backoff retry (3 attempts) for transient
        network errors. HTTP goes through the shared module-level
        OpenerDirector (built once, so the handler chain, redirect
        refusal, plaintext-HTTP refusal, is not reconstructed per
        request); note the stdlib opener does NOT pool connections —
        each request opens a fresh TCP/TLS connection and sends
        ``Connection: close``.

        Thin wrapper around ``_transcribe_with_retry``, supplies the
        OpenAI-specific request factory (multipart body, rebuilt per
        attempt because ``_StreamingMultipartBody`` carries internal
        state) and the OpenAI response parser (``result["text"]``).
        """
        # Defense-in-depth: SEC-002 already validates URL scheme at
        _facade().assert_url_allowed(
            self.api_url,
            field_name="cloud_api_url",
            client_name=f"cloud/{self.provider}",
            allow_loopback_http=True,
        )

        boundary = "----VoiceTyperBoundary7MA4YWxkTrZu0gW"

        def _build_request() -> Request:
            # Rebuild `body` and `req` INSIDE the retry loop.
            body = self._build_multipart_body(wav_bytes, filename, boundary)
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": f"multipart/form-data; boundary={boundary}",
                # PERF: pass Content-Length explicitly so urllib
                "Content-Length": str(len(body)),
            }
            return Request(self.api_url, data=body, headers=headers, method="POST")

        def _parse(raw: bytes) -> str:
            result = json.loads(raw.decode("utf-8"))
            return result.get("text", "").strip()

        return self._transcribe_with_retry(self.provider, _build_request, _parse)

    def _send_deepgram(self, wav_bytes: bytes) -> str:
        """Send request to Deepgram API.

                Same URL allowlist + log redaction as the
                OpenAI-compatible path.

                SEC-005: query parameters (model, language) are validated
                and URL-encoded by the Deepgram provider module
                (``voice_typer.server.cloud._providers.deepgram``) to prevent
                parameter injection via crafted config values.

        PERF: exponential backoff retry (3 attempts) for
        transient network errors, matching the OpenAI-compatible path
        (now shared via ``_transcribe_with_retry``).
        """
        # Opt in to allow_loopback_http=True: see the
        _facade().assert_url_allowed(
            self.api_url,
            field_name="cloud_api_url",
            client_name="cloud/deepgram",
            allow_loopback_http=True,
        )

        # SEC-005: the provider module escapes special characters in the
        url = build_listen_url(self.api_url, self.model_name, self.language)

        # Deepgram's body is a plain ``bytes`` object (no internal
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

        PERF: returns a streaming ``_StreamingMultipartBody`` file-like
        object (defined in ``voice_typer.server.cloud._transport``) that
        yields the pre-built parts as ~64 KB chunks on demand, avoiding a
        SECOND full-body copy, the naive ``b"".join(parts)`` built one
        contiguous ~5.2 MB ``bytes`` object next to the WAV that is
        already resident in ``parts``; ``Content-Length`` is computed
        upfront via ``__len__`` so the server knows the total size
        without chunked transfer encoding.
        Shaping itself lives in
        ``voice_typer.server.cloud._providers.openai``.
        """
        return build_multipart_body(wav_bytes, filename, boundary, self.model_name, self.language)

    def _multipart_parts(self, wav_bytes: bytes, filename: str, boundary: str) -> list[bytes]:
        """Return the ordered list of byte chunks that compose the body.

        Shaping lives in ``voice_typer.server.cloud._providers.openai``.
        """
        return build_multipart_parts(wav_bytes, filename, boundary, self.model_name, self.language)

    def test_connection(self) -> tuple[bool, str]:
        """Test the API connection. Returns (success, message).

        Redaction contract: any secret-looking substring is stripped from the
        returned message so a leaked key in an exception string does
        not propagate to the UI.

        SEC-011: previously this method sent the API key in a HEAD
        request to the user-supplied ``api_url``.  Combined with a
        SEC-002 endpoint-swap, that would leak the key to an
        attacker-controlled URL.  It also probed OpenAI's
        ``/v1/audio/transcriptions`` endpoint with HEAD, which
        returns 405 Method Not Allowed, so the test always reported
        failure even with valid credentials.

        The fix: probe a provider-known endpoint with a GET (or
        rather, just attempt a real transcription-shaped request and
        check for a 401/403 response, which proves the key was
        accepted by the auth layer even if the request body was
        empty).  We never send the API key to a URL the user didn't
        configure.
        """
        # No cloud interaction without consent (ADR-0016 Design Rule 1).
        if not self.consent_given:
            return False, "Cloud consent not given, refusing to test connection"

        if not self.api_key:
            return False, "API key not configured"

        try:
            # Opt in to allow_loopback_http=True: see the
            _facade().assert_url_allowed(
                self.api_url,
                field_name="cloud_api_url",
                client_name=f"cloud/{self.provider}",
                allow_loopback_http=True,
            )
        except ValueError as exc:
            return False, str(exc)

        # SEC-011: probe by sending an empty audio body to the real
        try:
            # Build a minimal multipart body with empty audio so the
            if self.provider == "deepgram":
                # Deepgram: send empty WAV bytes; expect 400 (bad audio)
                empty_wav = _audio_to_wav_bytes(np.zeros(0, dtype=np.float32))
                headers = {
                    "Authorization": f"Token {self.api_key}",
                    "Content-Type": "audio/wav",
                }
                req = Request(self.api_url, data=empty_wav, headers=headers, method="POST")
            else:
                # OpenAI-compatible: send empty multipart body.
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
            # SEC-audit-006 (Round 0 forward-port): use the shared
            opener = _facade()._opener
            with opener.open(req, timeout=self._REQUEST_TIMEOUT_SECONDS) as resp:
                return True, f"Connected to {self.provider} (status {resp.status})"
        except Exception as exc:
            # A 400/401/403/422 error means the server is reachable
            msg = str(exc)
            # urllib.error.HTTPError carries the status code
            status = getattr(exc, "code", None)
            if status is not None:
                # HTTP error, server is reachable.  401/403 = key
                if status in (401, 403):
                    return False, f"Connected to {self.provider}, but API key was rejected (HTTP {status})"
                # A 5xx means the server is reachable but is itself
                if 500 <= status < 600:
                    return True, (
                        f"Connected to {self.provider}, but server returned "
                        f"HTTP {status}, provider may be temporarily unavailable"
                    )
                # Any other HTTP error means the server is up and
                return True, f"Connected to {self.provider} (HTTP {status})"
            # Chain ``redact_url`` (strips URL userinfo +
            return False, f"Connection failed: {redact_secret(redact_url(msg))}"
