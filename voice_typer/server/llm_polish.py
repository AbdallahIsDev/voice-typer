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
from urllib.request import Request

from voice_typer.server._http_safety import (
    build_secure_opener,
)
from voice_typer.server._paths import (
    DEFAULT_LLM_API_URL,
    DEFAULT_LLM_MODEL,
)
from voice_typer.server._secrets import (
    assert_url_allowed,
    redact_secret,
    redact_url,
)
from voice_typer.server.asr_errors import (
    CloudEngineError,
    CloudNetworkError,
    CloudServerError,
)

log = logging.getLogger(__name__)


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

# SEC-2 (fix): the comment above was the INTENT but ``build_opener``
# installs the default ``HTTPRedirectHandler`` regardless of whether
# the caller passed ``HTTPSHandler``. Passing ``_NoRedirectHandler()``
# (a subclass that raises on redirect) actually achieves the intent.

# the handler + builder now live in ``_http_safety`` so
# they're shared with ``cloud_engines._opener`` (single source of
# truth — previously the class was duplicated verbatim across both
# modules,  finding #1).
_opener = build_secure_opener()

# upper bound on input size. Dictations above this length are
# short-circuited before the API call — shipping 30k+ char inputs in full
# to the LLM endpoint was wasteful (the ``max_tokens = 1024`` cap below
# means only the first ~1024 tokens ever come back) and slow (the API
# round-trip alone took several seconds for huge payloads). 8000 chars
# comfortably covers normal prose dictation while preventing the worst
# pathological cases. See ``tests/test_llm_polish.py``.
MAX_INPUT_CHARS = 8000

# configurable API call timeout. The previous hard-coded ``timeout=30``
# blocked the transcription thread for up to 30s on a stalled connection.
# 10s is generous for <500-char dictations (LLM completions typically
# finish in <3s) and bounds the worst-case user wait. Callers can override
# per-call via the ``timeout_s`` kwarg on ``polish`` / ``_call_api``.
DEFAULT_TIMEOUT_S = 10

# flat ``max_tokens`` value. The previous formula
# ``min(4096, len(text) * 2 + 256)`` was dead code above ~1920 chars
# (always hit the 4096 ceiling) and produced tiny requests for short
# inputs (e.g. 5-char input → 266 tokens). A flat 1024 is the documented
# ceiling for OpenAI-compatible chat completions in practice and avoids
# the input-length coupling entirely.
_FLAT_MAX_TOKENS = 1024

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

# canonical LLM endpoint + model defaults live in
# ``_paths.py`` (single source of truth) so they can be shared with
# ``config.py`` (dataclass field defaults) without a circular import.
# The local underscore-prefixed aliases preserve the original
# ``LLMPolisher.__init__`` call sites that reference ``_DEFAULT_URL``
# / ``_DEFAULT_MODEL``.
_DEFAULT_URL = DEFAULT_LLM_API_URL
_DEFAULT_MODEL = DEFAULT_LLM_MODEL


class LLMPolisher:
    """Polish transcribed text using an LLM API."""

    # default API call timeout in seconds. Exposed as a class
    # attribute so tests can assert ``LLMPolisher.DEFAULT_TIMEOUT_S == 10``
    # without importing the module-level constant.
    DEFAULT_TIMEOUT_S = DEFAULT_TIMEOUT_S

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

    def polish(self, text: str, *, preset: str | None = None, timeout_s: float | None = None) -> str:
        """Send text to the LLM for polishing.

        If polishing is disabled, no API key is configured, or the text is
        too short (< 5 characters), the original text is returned unchanged.

        Args:
            text: The transcribed text to polish.
            preset: Optional preset name to override the default. Must be
                a key in _PRESETS (e.g. "professional", "casual", "concise").
            timeout_s: Optional override for the API call timeout (seconds).
                When ``None``, falls back to ``DEFAULT_TIMEOUT_S`` (10s).

        Returns:
            The polished text string, or the original text if polishing
            fails or is disabled.
        """
        if not self.enabled or not self.api_key:
            return text

        if not text or len(text.strip()) < 5:
            return text

        # short-circuit when the input exceeds MAX_INPUT_CHARS.
        # Shipping oversized inputs to the LLM endpoint was wasteful
        # (the flat ``max_tokens = 1024`` means only the first ~1024
        # tokens ever come back) and slow. Log at INFO so operators can
        # see why polish was skipped. The guard is strict-greater-than
        # so the boundary (input length == MAX_INPUT_CHARS) is allowed.
        if len(text) > MAX_INPUT_CHARS:
            log.info(
                "[LLM_POLISH] Skipping polish: input length %d exceeds MAX_INPUT_CHARS=%d",
                len(text),
                MAX_INPUT_CHARS,
            )
            return text

        use_preset = preset or self.preset
        system_prompt = _PRESETS.get(use_preset, _PRESETS["professional"])

        try:
            result = self._call_api(text, system_prompt, timeout_s=timeout_s)
            if result and result.strip():
                log.info("[LLM_POLISH] Polished text: %d -> %d chars", len(text), len(result))
                return result.strip()
            return text
        except Exception as exc:
            # RELIABILITY-004: redact any secret-looking string from
            # the exception before logging, so a leaked API key in
            # an error response body doesn't end up in the log file.
            log.warning("[LLM_POLISH] Polish failed: %s (returning original)", redact_secret(str(exc)))
            # publish ``llm_polish_failed`` so the renderer
            # can surface a one-time toast. Without this the user pays
            # for an LLM API call that never produces a polished result,
            # and the only signal is a log line they will never see.
            # Best-effort: ``event_bus`` import + publish are both
            # wrapped in ``suppress(Exception)`` so a broken event bus
            # cannot double-fault the polish path (the user still gets
            # their un-polished transcription pasted).
            try:
                from voice_typer.server import event_bus

                event_bus.publish({"type": "llm_polish_failed"})
            except Exception:
                log.debug("[LLM_POLISH] could not publish llm_polish_failed event", exc_info=True)
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
            # Opt in to allow_loopback_http=True because this
            # caller sends user-supplied text to a user-configured
            # endpoint, and the user may legitimately point it at a
            # local HTTP server (Ollama, vLLM, LM Studio, etc.).
            assert_url_allowed(
                self.api_url,
                field_name="llm_api_url",
                client_name="llm_polish",
                allow_loopback_http=True,
            )
        except ValueError as exc:
            return False, str(exc)
        try:
            self._call_api("Hello", _PRESETS["professional"])
            return True, f"Connected (model: {self.model})"
        except Exception as exc:
            return False, f"Connection failed: {redact_secret(str(exc))}"

    # ── API call ─────────────────────────────────────────────────────

    def _call_api(self, text: str, system_prompt: str, *, timeout_s: float | None = None) -> str:
        """Call the OpenAI-compatible chat completions API.

        RELIABILITY-004: asserts ``self.api_url`` is in the trusted
        allowlist before sending any text. This prevents an
        endpoint-swap attack from exfiltrating transcribed speech
        even if the config layer's allowlist check was bypassed.

        PII redaction is applied to the user-content text BEFORE it is
        sent to the LLM (defense-in-depth against template
        ``{clipboard}`` substitution, which can inject passwords, 2FA
        codes, or private messages into the LLM-bound text — the
        redaction patterns cover credit cards, SSNs, email addresses,
        phone numbers, and API keys). The REDACTED text is what leaves
        the device. When the call fails, or redaction itself fails,
        the ORIGINAL un-polished text is returned to the user instead
        (see ``polish`` and the fail-closed branch below).

        Args:
            text: The user-content text to send (redacted before the
                API call).
            system_prompt: The system prompt for the chosen preset.
            timeout_s: Optional override for the API call timeout (seconds).
                When ``None``, falls back to ``DEFAULT_TIMEOUT_S`` (10s).
        """
        # Opt in to allow_loopback_http=True — see the
        # test_connection path above for the rationale.
        assert_url_allowed(
            self.api_url,
            field_name="llm_api_url",
            client_name="llm_polish",
            allow_loopback_http=True,
        )

        # redact PII from the user-content text before API send.
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
                    "[LLM_POLISH] redacted PII from %d chars of user-content before API send (delta=%d chars)",
                    len(text),
                    len(text) - len(redacted_text),
                )
                text = redacted_text
        except Exception:
            # Fail CLOSED: if PII redaction fails for any reason
            # (broken security module, regex regression, etc.) we must
            # NOT send the un-redacted user-content to the LLM
            # endpoint. The user-content may contain PII injected via
            # template ``{clipboard}`` substitution (passwords, 2FA
            # codes, private messages). Previously this branch
            # swallowed the failure at DEBUG level and shipped the
            # original text to the LLM anyway — a fail-OPEN PII leak.
            # Now we log at WARNING (operators need to see this) and
            # return the original text UNPOLISHED, skipping the API
            # call entirely. The user gets their transcription pasted
            # without LLM polish rather than risking a PII leak.
            log.warning(
                "[LLM_POLISH] redact_pii failed — skipping LLM API call (returning original text unpolished)",
                exc_info=True,
            )
            return text

        payload = json.dumps(
            {
                "model": self.model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": text},
                ],
                "temperature": 0.3,
                # flat ``max_tokens`` — the previous
                # ``min(4096, len(text) * 2 + 256)`` formula was dead
                # code above ~1920 chars (always hit the 4096 ceiling)
                # and produced tiny requests for short inputs. A flat 1024
                # is the documented ceiling for OpenAI-compatible chat
                # completions in practice and decouples the request from
                # the input length.
                "max_tokens": _FLAT_MAX_TOKENS,
            }
        ).encode("utf-8")

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }

        req = Request(self.api_url, data=payload, headers=headers, method="POST")

        # configurable timeout. When ``timeout_s`` is ``None`` we
        # fall back to ``DEFAULT_TIMEOUT_S`` (10s). The previous hard-coded
        # ``timeout=30`` blocked the transcription thread for up to 30s on
        # a stalled connection.
        effective_timeout = DEFAULT_TIMEOUT_S if timeout_s is None else timeout_s

        try:
            with _opener.open(req, timeout=effective_timeout) as resp:
                # SEC-030: cap response at 50 MB to prevent OOM from
                # a malicious / buggy LLM endpoint.
                from voice_typer.server.cloud_engines import _read_capped

                raw = _read_capped(resp, max_bytes=50 * 1024 * 1024)
                result = json.loads(raw.decode("utf-8"))
                choices = result.get("choices", [])
                if choices:
                    return choices[0].get("message", {}).get("content", "")
                return ""
        except HTTPError as exc:
            # HTTPError is a subclass of URLError, so it MUST be
            # caught BEFORE URLError. Map 5xx → ``CloudServerError``;
            # other HTTP errors (4xx, including 401/403/429) → generic
            # ``CloudEngineError``. The LLM polish path is best-effort
            # (``polish()`` swallows exceptions and returns the original
            # text), so the typed exception here is for the
            # ``test_connection`` path that surfaces errors to the user.
            # RELIABILITY-004: redact URL and any secret-looking
            # substring from the exception before propagating.
            safe_msg = redact_secret(redact_url(str(exc)))
            if 500 <= exc.code < 600:
                raise CloudServerError(f"LLM API server error (HTTP {exc.code}): {safe_msg}") from exc
            raise CloudEngineError(f"LLM API error (HTTP {exc.code}): {safe_msg}") from exc
        except URLError as exc:
            # Typed ``CloudNetworkError`` (was generic
            # ``RuntimeError``) so the IPC layer can map to
            # ``server.cloud_network_error``.
            # RELIABILITY-004: redact URL and any secret-looking
            # substring from the exception before propagating.
            raise CloudNetworkError(f"LLM API error: {redact_secret(redact_url(str(exc)))}") from exc
        except Exception as exc:
            # Typed base ``CloudEngineError`` (was generic
            # ``RuntimeError``) so the IPC layer still maps to a
            # cloud-specific code rather than the generic
            # ``server.internal_error``.
            raise CloudEngineError(f"LLM API error: {redact_secret(str(exc))}") from exc
