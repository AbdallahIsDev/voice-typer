"""LLM text polishing: 4 presets, OpenAI-compatible API support.

After rule-based cleanup, optionally send text to an LLM for grammar
fixing, filler removal, and restructuring. Uses any OpenAI-compatible
API (OpenAI, Groq, Ollama, vLLM, llama.cpp).

Presets:
    professional — formal, concise, grammar-perfect
    casual       — natural, conversational, fix grammar only
    email        — structured, professional email format
    code         — preserve code/formatting, fix only prose comments

Pipeline order: transcribe → text cleanup → vocabulary → templates → LLM polish → auto-punctuate → paste
"""

import json
import logging
import re
from typing import Optional
from urllib.request import Request, urlopen
from urllib.error import URLError

from voice_typer.server._secrets import (
    assert_url_allowed,
    redact_secret,
    redact_url,
)

log = logging.getLogger(__name__)

# ─── Preset prompts ─────────────────────────────────────────────────────

_PRESETS = {
    "professional": (
        "You are a professional text editor. Clean up the following speech-to-text output. "
        "Fix grammar, remove filler words (um, uh, like, you know), "
        "improve sentence structure, and make it concise and professional. "
        "Preserve the original meaning. Output only the cleaned text, nothing else."
    ),
    "casual": (
        "You are a casual text editor. Fix grammar and remove filler words "
        "from this speech-to-text output, but keep the conversational tone. "
        "Don't make it overly formal. Output only the cleaned text."
    ),
    "email": (
        "You are an email writing assistant. Transform this speech-to-text output "
        "into a well-structured professional email. Add appropriate greeting "
        "and sign-off if the text suggests an email context. "
        "Output only the email text."
    ),
    "code": (
        "You are a code-aware text editor. Clean up this speech-to-text output "
        "that may contain code snippets, variable names, or technical terms. "
        "Fix grammar in prose sections only. Preserve code formatting, "
        "variable names, and technical terms exactly as they appear. "
        "Output only the cleaned text."
    ),
}

_DEFAULT_URL = "https://api.openai.com/v1/chat/completions"
_DEFAULT_MODEL = "gpt-4o-mini"


class LLMPolisher:
    """Polish transcribed text using an LLM API."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        api_url: Optional[str] = None,
        model: Optional[str] = None,
        preset: str = "professional",
        enabled: bool = False,
    ):
        self.api_key = api_key
        self.api_url = api_url or _DEFAULT_URL
        self.model = model or _DEFAULT_MODEL
        self.preset = preset
        self.enabled = enabled

    # ── Public API ───────────────────────────────────────────────────

    def polish(self, text: str, *, preset: Optional[str] = None) -> str:
        """Send text to the LLM for polishing.

        Returns the polished text, or the original text if polishing
        fails or is disabled.
        """
        if not self.enabled or not self.api_key:
            return text

        if not text or len(text.strip()) < 5:
            return text

        use_preset = preset or self.preset
        system_prompt = _PRESETS.get(use_preset, _PRESETS["professional"])

        try:
            result = self._call_api(text, system_prompt)
            if result and result.strip():
                log.info("[LLM_POLISH] Polished text: %d -> %d chars", len(text), len(result))
                return result.strip()
            return text
        except Exception as exc:
            # RELIABILITY-004: redact any secret-looking string from
            # the exception before logging, so a leaked API key in
            # an error response body doesn't end up in the log file.
            log.warning("[LLM_POLISH] Polish failed: %s (returning original)",
                        redact_secret(str(exc)))
            return text

    def test_connection(self) -> tuple[bool, str]:
        """Test the LLM API connection. Returns (success, message).

        RELIABILITY-004: redacts the exception string in the failure
        message and asserts the configured URL is in the allowlist.
        """
        if not self.api_key:
            return False, "API key not configured"
        try:
            assert_url_allowed(
                self.api_url, field_name="llm_api_url", client_name="llm_polish"
            )
        except ValueError as exc:
            return False, str(exc)
        try:
            result = self._call_api("Hello", _PRESETS["professional"])
            return True, f"Connected (model: {self.model})"
        except Exception as exc:
            return False, f"Connection failed: {redact_secret(str(exc))}"

    # ── API call ─────────────────────────────────────────────────────

    def _call_api(self, text: str, system_prompt: str) -> str:
        """Call the OpenAI-compatible chat completions API.

        RELIABILITY-004: asserts ``self.api_url`` is in the trusted
        allowlist before sending any text.  This prevents an
        endpoint-swap attack from exfiltrating transcribed speech
        text even if SEC-002's allowlist is somehow bypassed.
        """
        assert_url_allowed(
            self.api_url, field_name="llm_api_url", client_name="llm_polish"
        )

        payload = json.dumps({
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": text},
            ],
            "temperature": 0.3,
            "max_tokens": min(4096, len(text) * 2 + 256),
        }).encode("utf-8")

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }

        req = Request(self.api_url, data=payload, headers=headers, method="POST")

        try:
            with urlopen(req, timeout=30) as resp:
                # SEC-030: cap response at 50 MB to prevent OOM from
                # a malicious / buggy LLM endpoint.
                from voice_typer.server.cloud_engines import _read_capped
                raw = _read_capped(resp, max_bytes=50 * 1024 * 1024)
                result = json.loads(raw.decode("utf-8"))
                choices = result.get("choices", [])
                if choices:
                    return choices[0].get("message", {}).get("content", "")
                return ""
        except URLError as exc:
            # RELIABILITY-004: redact URL and any secret-looking
            # substring from the exception before propagating.
            raise RuntimeError(
                f"LLM API error: {redact_secret(redact_url(str(exc)))}"
            ) from exc
        except Exception as exc:
            raise RuntimeError(
                f"LLM API error: {redact_secret(str(exc))}"
            ) from exc
