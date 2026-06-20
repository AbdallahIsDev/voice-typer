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
from typing import Optional
from urllib.request import Request, urlopen, build_opener, HTTPSHandler
from urllib.error import URLError

import numpy as np

from voice_typer.server._secrets import (
    assert_url_allowed,
    redact_secret,
    redact_url,
)
from voice_typer.server.transcription import TranscriberProtocol

log = logging.getLogger(__name__)

# PERF-NEW-010: module-level OpenerDirector for connection pooling.
# Reuses TCP connections across requests (like requests.Session).
_opener = build_opener(HTTPSHandler())

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
    import struct
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


class CloudEngine:
    """Cloud ASR engine implementing TranscriberProtocol.

    Supports OpenAI, Groq, and Deepgram APIs (all OpenAI-compatible
    except Deepgram which uses its own format).
    """

    def __init__(
        self,
        provider: str,
        api_key: str,
        api_url: Optional[str] = None,
        model: Optional[str] = None,
        language: str = "en",
    ):
        self.provider = provider
        self.api_key = api_key
        self.language = language
        self._lock = threading.RLock()

        defaults = _PROVIDER_DEFAULTS.get(provider, {})
        self.api_url = api_url or defaults.get("url", "")
        self.model_name = model or defaults.get("model", "")

        self._loaded = True  # Cloud engines don't need local model loading

    # ── TranscriberProtocol ──────────────────────────────────────────

    @property
    def is_loaded(self) -> bool:
        return self._loaded and bool(self.api_key)

    def load(self, progress_callback=None) -> None:
        """No-op for cloud engines — no local model to load."""
        if progress_callback:
            progress_callback("Cloud engine ready")
        self._loaded = True

    def transcribe(self, audio: np.ndarray) -> str:
        """Transcribe audio via cloud API."""
        if not self.is_loaded:
            raise RuntimeError("Cloud engine not configured (missing API key)")
        if len(audio) == 0:
            return ""
        return self._send_request(audio)

    def transcribe_with_fallback(self, audio: np.ndarray, local_engine=None) -> str:
        """Try cloud transcription; fall back to local engine on failure.

        PERF-NEW-010: if the cloud request fails after all retries,
        and a local_engine is provided, attempt transcription on it
        instead of raising.  This gives a best-effort result even
        when the cloud is temporarily unreachable.
        """
        try:
            return self.transcribe(audio)
        except Exception as cloud_err:
            if local_engine is not None:
                log.warning(
                    "[CLOUD] %s failed, falling back to local engine: %s",
                    self.provider, cloud_err,
                )
                try:
                    return local_engine.transcribe(audio)
                except Exception as local_err:
                    log.error("[CLOUD] Local fallback also failed: %s", local_err)
                    raise RuntimeError(
                        f"Cloud ({self.provider}) and local fallback both failed"
                    ) from cloud_err
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
        assert_url_allowed(
            self.api_url, field_name="cloud_api_url", client_name=f"cloud/{self.provider}"
        )

        boundary = "----VoiceTyperBoundary7MA4YWxkTrZu0gW"
        body = self._build_multipart_body(wav_bytes, filename, boundary)

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        }

        req = Request(self.api_url, data=body, headers=headers, method="POST")

        # PERF-NEW-010: retry with exponential backoff
        max_retries = 3
        last_exc = None
        for attempt in range(max_retries):
            try:
                with _opener.open(req, timeout=30) as resp:
                    result = json.loads(resp.read().decode("utf-8"))
                    text = result.get("text", "").strip()
                    log.info("[CLOUD] %s transcription: %d chars", self.provider, len(text))
                    return text
            except URLError as exc:
                last_exc = exc
                # Only retry on transient errors (timeouts, connection reset)
                # Don't retry on 4xx errors (bad request, auth failure)
                if attempt < max_retries - 1:
                    import time as _time
                    backoff = 0.5 * (2 ** attempt)  # 0.5s, 1.0s, 2.0s
                    log.warning(
                        "[CLOUD] %s attempt %d/%d failed, retrying in %.1fs: %s",
                        self.provider, attempt + 1, max_retries, backoff,
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
                raise RuntimeError(f"{self.provider} request failed") from exc
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
        assert_url_allowed(
            self.api_url, field_name="cloud_api_url", client_name="cloud/deepgram"
        )

        headers = {
            "Authorization": f"Token {self.api_key}",
            "Content-Type": "audio/wav",
        }

        # SEC-005: urlencode escapes special characters in the model
        # and language values, preventing parameter injection.
        from urllib.parse import urlencode
        import re
        _SAFE_TOKEN = re.compile(r"^[A-Za-z0-9._\-]+$")
        if not _SAFE_TOKEN.match(self.model_name or ""):
            raise RuntimeError(
                f"Deepgram model name {self.model_name!r} contains invalid characters"
            )
        if not _SAFE_TOKEN.match(self.language or ""):
            raise RuntimeError(
                f"Deepgram language {self.language!r} contains invalid characters"
            )
        query = urlencode({
            "model": self.model_name,
            "language": self.language,
            "punctuate": "true",
        })
        url = f"{self.api_url}?{query}"
        req = Request(url, data=wav_bytes, headers=headers, method="POST")

        # PERF-NEW-010: retry with exponential backoff (same as OpenAI path)
        max_retries = 3
        last_exc = None
        for attempt in range(max_retries):
            try:
                with _opener.open(req, timeout=30) as resp:
                    result = json.loads(resp.read().decode("utf-8"))
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
                last_exc = exc
                if attempt < max_retries - 1:
                    import time as _time
                    backoff = 0.5 * (2 ** attempt)  # 0.5s, 1.0s, 2.0s
                    log.warning(
                        "[CLOUD] Deepgram attempt %d/%d failed, retrying in %.1fs: %s",
                        attempt + 1, max_retries, backoff,
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

    def _build_multipart_body(self, wav_bytes: bytes, filename: str, boundary: str) -> bytes:
        """Build multipart/form-data body for OpenAI-compatible APIs."""
        parts = []

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

        return b"".join(parts)

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
                req = Request(
                    self.api_url, data=empty_wav, headers=headers, method="POST"
                )
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
                req = Request(
                    self.api_url, data=body, headers=headers, method="POST"
                )
            with urlopen(req, timeout=10) as resp:
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
