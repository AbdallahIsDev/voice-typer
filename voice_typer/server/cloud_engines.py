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
from urllib.request import Request, urlopen
from urllib.error import URLError

import numpy as np

from voice_typer.server.transcription import TranscriberProtocol

log = logging.getLogger(__name__)

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

    def transcribe_with_fallback(self, audio: np.ndarray) -> str:
        """Same as transcribe for cloud engines — no local fallback."""
        return self.transcribe(audio)

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
        """Send request to OpenAI-compatible API (OpenAI, Groq)."""
        boundary = "----VoiceTyperBoundary7MA4YWxkTrZu0gW"
        body = self._build_multipart_body(wav_bytes, filename, boundary)

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        }

        req = Request(self.api_url, data=body, headers=headers, method="POST")

        try:
            with urlopen(req, timeout=30) as resp:
                result = json.loads(resp.read().decode("utf-8"))
                text = result.get("text", "").strip()
                log.info("[CLOUD] %s transcription: %d chars", self.provider, len(text))
                return text
        except URLError as exc:
            log.error("[CLOUD] %s API error: %s", self.provider, exc)
            raise RuntimeError(f"{self.provider} API error: {exc}") from exc
        except Exception as exc:
            log.error("[CLOUD] %s request failed: %s", self.provider, exc)
            raise RuntimeError(f"{self.provider} request failed: {exc}") from exc

    def _send_deepgram(self, wav_bytes: bytes) -> str:
        """Send request to Deepgram API."""
        headers = {
            "Authorization": f"Token {self.api_key}",
            "Content-Type": "audio/wav",
        }

        url = f"{self.api_url}?model={self.model_name}&language={self.language}&punctuate=true"
        req = Request(url, data=wav_bytes, headers=headers, method="POST")

        try:
            with urlopen(req, timeout=30) as resp:
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
            log.error("[CLOUD] Deepgram API error: %s", exc)
            raise RuntimeError(f"Deepgram API error: {exc}") from exc
        except Exception as exc:
            log.error("[CLOUD] Deepgram request failed: %s", exc)
            raise RuntimeError(f"Deepgram request failed: {exc}") from exc

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
        """Test the API connection. Returns (success, message)."""
        if not self.api_key:
            return False, "API key not configured"

        # Send a minimal request to check auth
        try:
            headers = {"Authorization": f"Bearer {self.api_key}"}
            req = Request(self.api_url, headers=headers, method="HEAD")
            with urlopen(req, timeout=10) as resp:
                return True, f"Connected to {self.provider} (status {resp.status})"
        except Exception as exc:
            return False, f"Connection failed: {exc}"
