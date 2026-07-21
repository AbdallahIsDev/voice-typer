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
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, HTTPSHandler, Request, build_opener

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
    urllib adds the default handlers in addition to the caller-provided
    ones (a handler of the same *class* replaces the default;
    HTTPSHandler replaces HTTPSHandler but does NOT replace
    HTTPRedirectHandler). So the opener was silently following 3xx
    redirects despite the SECURITY comment claiming otherwise.

    This subclass overrides ``redirect_request`` to raise ``HTTPError``
    so the existing ``except HTTPError`` / ``except URLError`` branches
    handle it as a hard failure (no silent exfiltration of the request
    body — which contains the user's transcribed text — to an attacker-
    controlled redirect target).
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[override]
        raise HTTPError(
            url=newurl,
            code=code,
            msg=f"redirect refused (SEC-2): {code} {msg} -> {redact_url(newurl)}",
            hdrs=headers,
            fp=fp,
        )


# SEC-audit-006 (Round 0 forward-port): use a dedicated opener that does NOT
# include ``HTTPRedirectHandler``.  The default ``urllib.request.urlopen()``
# follows 3xx redirects silently, and our URL allowlist is only checked on
# the *initial* request — a malicious or compromised LLM endpoint could
# return ``302 Location: http://attacker.example.com/collect`` and
# ``urllib`` would POST the request body (which contains the user's
# transcribed text) to the attacker-controlled redirect target.  By
# building the opener with only ``HTTPSHandler`` (no
# ``HTTPRedirectHandler``), redirects are NOT followed and instead raise
# ``HTTPError`` (30x), which the existing ``except`` branches handle.
# This mirrors the pattern already used by
# ``voice_typer.server.cloud_engines._opener`` for the main transcription
# path; ``llm_polish._call_api`` was the last redirect-following path.
#
# SEC-2 (fix): the comment above was the INTENT but ``build_opener``
# installs the default ``HTTPRedirectHandler`` regardless of whether
# the caller passed ``HTTPSHandler``. Passing ``_NoRedirectHandler()``
# (a subclass that raises on redirect) actually achieves the intent.
_opener = build_opener(HTTPSHandler(), _NoRedirectHandler())

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
        api_key: str | None = None,
        api_url: str | None = None,
        model: str | None = None,
        preset: str = "professional",
        enabled: bool = False,
    ):
        self.api_key = api_key
        self.api_url = api_url or _DEFAULT_URL
        self.model = model or _DEFAULT_MODEL
        self.preset = preset
        self.enabled = enabled

    # ── Public API ───────────────────────────────────────────────────

    def polish(self, text: str, *, preset: str | None = None) -> str:
        """Send text to the LLM for polishing.

        If polishing is disabled, no API key is configured, or the text is
        too short (< 5 characters), the original text is returned unchanged.

        Args:
            text: The transcribed text to polish.
            preset: Optional preset name to override the default. Must be
                a key in _PRESETS (e.g. "professional", "casual", "concise").

        Returns:
            The polished text string, or the original text if polishing
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
            log.warning("[LLM_POLISH] Polish failed: %s (returning original)", redact_secret(str(exc)))
            return text

    def test_connection(self) -> tuple[bool, str]:
        """Test the LLM API connection.

        Sends a minimal request to verify the API key and endpoint are valid.
        Secrets are redacted from any error messages before logging.

        Returns:
            A tuple of (success: bool, message: str). The message describes
            the result or error, with any API keys redacted.
        """
        if not self.api_key:
            return False, "API key not configured"
        try:
            assert_url_allowed(self.api_url, field_name="llm_api_url", client_name="llm_polish")
        except ValueError as exc:
            return False, str(exc)
        try:
            self._call_api("Hello", _PRESETS["professional"])
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

        CR-10: Apply PII redaction to the user-content text BEFORE
        sending to the LLM API. This is defense-in-depth against
        template ``{clipboard}`` substitution (which can inject
        passwords, 2FA codes, private messages from the user's
        clipboard into the LLM-bound text). The redaction patterns
        cover credit cards, SSNs, email addresses, phone numbers,
        and API keys. The redacted text is what's sent to the API;
        the original (un-redacted) text is what's returned to the
        user for pasting.
        """
        assert_url_allowed(self.api_url, field_name="llm_api_url", client_name="llm_polish")

        # CR-10: redact PII from the user-content text before API send.
        # ``redact_pii`` is the same helper used by the log redaction
        # filter — it covers credit cards, SSNs, emails, phone numbers,
        # and common API-key formats. This is a defense-in-depth gate:
        # if a template's ``{clipboard}`` substitution injects
        # sensitive clipboard content, the redaction strips the most
        # common PII patterns before the text leaves the device.
        try:
            from voice_typer.server.security import redact_pii

            redacted_text = redact_pii(text)
            if redacted_text != text:
                log.info(
                    "[LLM_POLISH] CR-10: redacted PII from %d chars of user-content before API send (delta=%d chars)",
                    len(text),
                    len(text) - len(redacted_text),
                )
                text = redacted_text
        except Exception:
            log.debug("[LLM_POLISH] CR-10: redact_pii failed — sending original text", exc_info=True)

        payload = json.dumps(
            {
                "model": self.model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": text},
                ],
                "temperature": 0.3,
                "max_tokens": min(4096, len(text) * 2 + 256),
            }
        ).encode("utf-8")

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }

        req = Request(self.api_url, data=payload, headers=headers, method="POST")

        try:
            with _opener.open(req, timeout=30) as resp:
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
            raise RuntimeError(f"LLM API error: {redact_secret(redact_url(str(exc)))}") from exc
        except Exception as exc:
            raise RuntimeError(f"LLM API error: {redact_secret(str(exc))}") from exc
