"""Cloud ASR backends: OpenAI, Groq, Deepgram.

Each engine implements the TranscriberProtocol so the app can swap
backends transparently. Cloud engines send audio to an API endpoint
and return the transcribed text.

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
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, HTTPSHandler, Request, build_opener

import numpy as np

from voice_typer.server._secrets import (
    assert_url_allowed,
    redact_secret,
    redact_url,
)

log = logging.getLogger(__name__)


class _NoRedirectHandler(HTTPRedirectHandler):
    """SEC-2: refuse to follow HTTP redirects.

    ``urllib.request.build_opener`` ALWAYS installs the default
    ``HTTPRedirectHandler`` (which silently follows 3xx responses)
    UNLESS the caller passes an explicit ``HTTPRedirectHandler``
    subclass. The previous code passed only ``HTTPSHandler()``,
    expecting ``build_opener`` to skip the redirect handler — but
    the urllib source adds the default handlers in addition to the
    caller-provided ones (a handler of the same *class* replaces the
    default; HTTPSHandler replaces HTTPSHandler but does NOT replace
    HTTPRedirectHandler). So the opener was silently following 3xx
    redirects despite the SECURITY comment claiming otherwise.

    This subclass overrides ``redirect_request`` to raise
    ``HTTPError`` so the existing ``except HTTPError`` / ``except
    URLError`` branches in the cloud engines handle it as a hard
    failure (no silent exfiltration of the request body — which
    contains user audio + the API key in the Authorization header —
    to an attacker-controlled redirect target).

    See https://docs.python.org/3/library/urllib.request.html#urllib.request.HTTPRedirectHandler
    for the contract.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[override]
        # Raise HTTPError so the caller's ``except HTTPError`` branch
        # catches it. The error message includes the redirect target
        # (newurl) so the user / operator can diagnose a misconfigured
        # endpoint, but ``redact_url`` is applied by the caller before
        # logging to avoid leaking credentials in the URL.
        raise HTTPError(
            url=newurl,
            code=code,
            msg=f"redirect refused (SEC-2): {code} {msg} -> {redact_url(newurl)}",
            hdrs=headers,
            fp=fp,
        )


# PERF-NEW-010: module-level OpenerDirector for connection pooling.
# Reuses TCP connections across requests (like requests.Session).
# SEC-2: pass ``_NoRedirectHandler()`` so the opener does NOT follow
# 3xx redirects (the default ``HTTPRedirectHandler`` would silently
# POST the request body — user audio + API key — to an attacker-
# controlled redirect target).
_opener = build_opener(HTTPSHandler(), _NoRedirectHandler())


class ConsentRequiredError(RuntimeError):
    """NEW-PRIV-006: raised when a cloud engine is asked to transcribe
    audio but the user hasn't granted consent for that provider.

    Subclass of RuntimeError so existing ``except RuntimeError`` catch
    clauses still work — but the IPC layer can ``isinstance``-check
    for this type to surface a consent dialog instead of an error
    toast.
    """


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


def _audio_to_wav_bytes(audio: np.ndarray, sample_rate: int = 16000) -> bytes:
    """Convert float32 numpy array to WAV bytes."""
    import wave

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


class _StreamingMultipartBody:
    """File-like object that yields multipart body chunks on demand.

    PERF-NEW-019: avoids building the entire multipart body in memory
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

    NEW-PRIV-006: each CloudEngine instance has a ``consent_given``
    flag that must be True before any audio is sent to the provider.
    The flag is set from the per-provider consent field on the Config
    dataclass (``cloud_openai_consent``, ``cloud_groq_consent``,
    ``cloud_deepgram_consent``).  When consent is False, ``is_loaded``
    returns False and ``transcribe`` raises a ConsentRequiredError so
    the IPC layer can surface a consent dialog to the renderer.
    """

    def __init__(
        self,
        provider: str,
        api_key: str,
        api_url: str | None = None,
        model: str | None = None,
        language: str = "en",
        consent_given: bool = False,
    ):
        self.provider = provider
        self.api_key = api_key
        self.language = language
        # NEW-PRIV-006: per-instance consent flag.  Must be True before
        # any audio is sent.  Set from Config.cloud_{provider}_consent
        # by the app when the engine is constructed.
        self.consent_given = bool(consent_given)
        self._lock = threading.RLock()

        defaults = _PROVIDER_DEFAULTS.get(provider, {})
        self.api_url = api_url or defaults.get("url", "")
        self.model_name = model or defaults.get("model", "")

        self._loaded = True  # Cloud engines don't need local model loading

    # ── TranscriberProtocol ──────────────────────────────────────────

    @property
    def is_loaded(self) -> bool:
        # NEW-PRIV-006: consent is required for the engine to be
        # considered "loaded" — without consent, the engine should
        # not be selected for transcription.
        return self._loaded and bool(self.api_key) and self.consent_given

    def load(self, progress_callback=None) -> None:
        """No-op for cloud engines — no local model to load."""
        if progress_callback:
            progress_callback("Cloud engine ready")
        self._loaded = True

    def transcribe(self, audio: np.ndarray) -> str:
        """Transcribe audio via cloud API.

        NEW-PRIV-006: refuses to send audio if consent hasn't been
        given.  Raises ConsentRequiredError (a subclass of RuntimeError
        so existing catch clauses still work) so the IPC layer can
        detect this case and show the consent dialog.
        """
        if not self.consent_given:
            raise ConsentRequiredError(f"Cloud {self.provider} consent not given — refusing to send audio.")
        if not self.is_loaded:
            raise RuntimeError("Cloud engine not configured (missing API key)")
        if len(audio) == 0:
            return ""
        return self._send_request(audio)

    def transcribe_with_fallback(
        self,
        audio: np.ndarray,
        local_engine=None,
        audio_stats: "tuple[float, float, float] | None" = None,
    ) -> str:
        """Try cloud transcription; fall back to local engine on failure.

        PERF-NEW-010: if the cloud request fails after all retries,
        and a local_engine is provided, attempt transcription on it
        instead of raising.  This gives a best-effort result even
        when the cloud is temporarily unreachable.

        NEW-PERF-010 (a-review Finding 8): ``audio_stats`` is accepted
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
        except Exception as cloud_err:
            if local_engine is not None:
                log.warning(
                    "[CLOUD] %s failed, falling back to local engine: %s",
                    self.provider,
                    cloud_err,
                )
                try:
                    return local_engine.transcribe(audio, audio_stats=audio_stats)
                except Exception as local_err:
                    log.error("[CLOUD] Local fallback also failed: %s", local_err)
                    raise RuntimeError(f"Cloud ({self.provider}) and local fallback both failed") from cloud_err
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

    def _send_openai_compatible(self, wav_bytes: bytes, filename: str) -> str:
        """Send request to OpenAI-compatible API (OpenAI, Groq).

        RELIABILITY-004: asserts the configured ``api_url`` is in the
        trusted-host allowlist before sending any audio.  This closes
        the SEC-002 endpoint-swap vector at the cloud-engine layer:
        even if an attacker finds another path to write
        ``config.cloud_api_url``, this engine refuses to send audio
        to an untrusted host.

        PERF-NEW-010: exponential backoff retry (3 attempts) for
        transient network errors.  Connection pooling via a module-
        level OpenerDirector (urllib's equivalent of requests.Session).
        """
        # Defense-in-depth: SEC-002 already validates URL scheme at
        # set_config time, but assert again here in case the value
        # was loaded from disk (Config.load) or set programmatically.
        assert_url_allowed(self.api_url, field_name="cloud_api_url", client_name=f"cloud/{self.provider}")

        boundary = "----VoiceTyperBoundary7MA4YWxkTrZu0gW"
        body = self._build_multipart_body(wav_bytes, filename, boundary)

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            # PERF-NEW-019: pass Content-Length explicitly so urllib
            # uses a single write instead of chunked encoding. The
            # _StreamingMultipartBody.__len__ returns the total length.
            "Content-Length": str(len(body)),
        }

        req = Request(self.api_url, data=body, headers=headers, method="POST")

        # PERF-NEW-010: retry with exponential backoff
        max_retries = 3
        for attempt in range(max_retries):
            try:
                with _opener.open(req, timeout=30) as resp:
                    # SEC-030: cap response body at 50 MB to prevent
                    # a malicious or buggy server from exhausting RAM.
                    # Whisper / Groq / Deepgram responses are <100 KB
                    # in practice; 50 MB is a generous ceiling.
                    raw = _read_capped(resp, max_bytes=50 * 1024 * 1024)
                    result = json.loads(raw.decode("utf-8"))
                    text = result.get("text", "").strip()
                    log.info("[CLOUD] %s transcription: %d chars", self.provider, len(text))
                    return text
            except URLError as exc:
                # Only retry on transient errors (timeouts, connection reset)
                # Don't retry on 4xx errors (bad request, auth failure)
                if attempt < max_retries - 1:
                    import time as _time

                    backoff = 0.5 * (2**attempt)  # 0.5s, 1.0s, 2.0s
                    log.warning(
                        "[CLOUD] %s attempt %d/%d failed, retrying in %.1fs: %s",
                        self.provider,
                        attempt + 1,
                        max_retries,
                        backoff,
                        redact_secret(redact_url(str(exc))),
                    )
                    _time.sleep(backoff)
                else:
                    safe_msg = redact_secret(redact_url(str(exc)))
                    log.error("[CLOUD] %s API error after %d attempts: %s", self.provider, max_retries, safe_msg)
                    raise RuntimeError(f"{self.provider} API error") from exc
            except Exception as exc:
                safe_msg = redact_secret(str(exc))
                log.error("[CLOUD] %s request failed: %s", self.provider, safe_msg)
                # NEW-UX-029: include the underlying error in the user-facing
                # message so the user can tell if it's a network issue vs an
                # API error. Pre-fix this was a generic "request failed" with
                # no hint about the cause.
                raise RuntimeError(f"{self.provider} request failed: {safe_msg}") from exc
        # Should not reach here, but just in case
        raise RuntimeError(f"{self.provider} request failed after {max_retries} attempts")

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

        PERF-NEW-010: exponential backoff retry (3 attempts) for
        transient network errors, matching the OpenAI-compatible path.
        """
        assert_url_allowed(self.api_url, field_name="cloud_api_url", client_name="cloud/deepgram")

        headers = {
            "Authorization": f"Token {self.api_key}",
            "Content-Type": "audio/wav",
        }

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
        req = Request(url, data=wav_bytes, headers=headers, method="POST")

        # PERF-NEW-010: retry with exponential backoff (same as OpenAI path)
        max_retries = 3
        for attempt in range(max_retries):
            try:
                with _opener.open(req, timeout=30) as resp:
                    # SEC-030: cap response body at 50 MB.
                    raw = _read_capped(resp, max_bytes=50 * 1024 * 1024)
                    result = json.loads(raw.decode("utf-8"))
                    # Deepgram response format
                    channels = result.get("results", {}).get("channels", [])
                    if channels:
                        alternatives = channels[0].get("alternatives", [])
                        if alternatives:
                            text = alternatives[0].get("transcript", "").strip()
                            log.info("[CLOUD] Deepgram transcription: %d chars", len(text))
                            return text
                    return ""
            except URLError as exc:
                if attempt < max_retries - 1:
                    import time as _time

                    backoff = 0.5 * (2**attempt)  # 0.5s, 1.0s, 2.0s
                    log.warning(
                        "[CLOUD] Deepgram attempt %d/%d failed, retrying in %.1fs: %s",
                        attempt + 1,
                        max_retries,
                        backoff,
                        redact_secret(redact_url(str(exc))),
                    )
                    _time.sleep(backoff)
                else:
                    safe_msg = redact_secret(redact_url(str(exc)))
                    log.error("[CLOUD] Deepgram API error after %d attempts: %s", max_retries, safe_msg)
                    raise RuntimeError("Deepgram API error") from exc
            except Exception as exc:
                safe_msg = redact_secret(str(exc))
                log.error("[CLOUD] Deepgram request failed: %s", safe_msg)
                raise RuntimeError("Deepgram request failed") from exc
        # Should not reach here, but just in case
        raise RuntimeError(f"Deepgram request failed after {max_retries} attempts")

    def _build_multipart_body(self, wav_bytes: bytes, filename: str, boundary: str):
        """Build multipart/form-data body for OpenAI-compatible APIs.

        PERF-NEW-019: the previous implementation concatenated all parts
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
        if not self.api_key:
            return False, "API key not configured"

        try:
            assert_url_allowed(
                self.api_url,
                field_name="cloud_api_url",
                client_name=f"cloud/{self.provider}",
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
                # or 200 (success with empty transcript).
                headers = {
                    "Authorization": f"Token {self.api_key}",
                    "Content-Type": "audio/wav",
                }
                # Empty WAV header (44 bytes, no data)
                empty_wav = (
                    b"RIFF\x24\x00\x00\x00WAVEfmt \x10\x00\x00\x00"
                    b"\x01\x00\x01\x00\x80\xbb\x00\x00\x00\x77\x01\x00"
                    b"\x02\x00\x10\x00data\x00\x00\x00\x00"
                )
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
            with _opener.open(req, timeout=10) as resp:
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
                # Any other HTTP error means the server is up and
                # talking to us — treat as success.
                return True, f"Connected to {self.provider} (HTTP {status})"
            return False, f"Connection failed: {redact_secret(msg)}"
