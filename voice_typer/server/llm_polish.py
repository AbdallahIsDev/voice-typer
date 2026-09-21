"""LLM polish over dictated text (user-configured only)."""

import json
import logging
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
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


# SEC-audit-006 / SEC-2: the secure opener MUST NOT follow redirects;
# the handler + builder live in ``_http_safety`` so the default
# redirect-following ``build_opener`` can never be used here.
_opener = build_secure_opener()

# upper bound on input size. Dictations above this length are
MAX_INPUT_CHARS = 8000

# configurable API call timeout. The previous hard-coded ``timeout=30``
DEFAULT_TIMEOUT_S = 10

# flat ``max_tokens`` value. The previous formula
_FLAT_MAX_TOKENS = 1024


def _is_openai_first_party_endpoint(api_url: str) -> bool:
    """True when *api_url* targets OpenAI's own API."""
    try:
        host = (urlsplit(api_url).hostname or "").lower()
    except Exception:
        return False
    return host == "api.openai.com" or host.endswith(".api.openai.com")


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
_DEFAULT_URL = DEFAULT_LLM_API_URL
_DEFAULT_MODEL = DEFAULT_LLM_MODEL


def _verify_llm_peer(req: Request, resp: object) -> None:
    """Verify the LLM peer IP matches the validated URL IPs."""
    from voice_typer.server._secrets import resolve_allowed_url_ips, verify_peer_ip_allowed

    try:
        expected = resolve_allowed_url_ips(
            req.full_url,
            field_name="llm_api_url",
            client_name="llm_polish",
            allow_loopback_http=True,
        )
    except Exception:
        log.debug("[LLM_POLISH] peer-IP pin lookup failed", exc_info=True)
        return
    try:
        raw = getattr(resp, "fp", None)
        sock = getattr(raw, "raw", None)
        sock = getattr(sock, "_sock", sock)
        peer = sock.getpeername()[0] if hasattr(sock, "getpeername") else None
    except Exception:
        log.debug("[LLM_POLISH] peer-IP read failed", exc_info=True)
        return
    if peer is None:
        return
    try:
        verify_peer_ip_allowed(peer, expected, host=req.host)
    except ValueError as exc:
        log.exception("[LLM_POLISH] peer IP %r outside validated set, refusing", peer)
        raise CloudNetworkError("LLM peer IP outside validated set") from exc


class LLMPolisher:
    """Polish transcribed text using an LLM API."""

    # default API call timeout in seconds. Exposed as a class
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

    def polish(self, text: str, *, preset: str | None = None, timeout_s: float | None = None) -> str:
        """Send text to the LLM for polishing.

        Returns:
        """
        if not self.enabled or not self.api_key:
            return text

        if not text or len(text.strip()) < 5:
            return text

        # short-circuit when the input exceeds MAX_INPUT_CHARS.
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
            # Redact any secret-looking string from
            log.warning("[LLM_POLISH] Polish failed: %s (returning original)", redact_secret(str(exc)))
            # publish ``llm_polish_failed`` so the renderer
            try:
                from voice_typer.server import event_bus

                event_bus.publish({"type": "llm_polish_failed"})
            except Exception:
                log.debug("[LLM_POLISH] could not publish llm_polish_failed event", exc_info=True)
            return text

    def test_connection(self) -> tuple[bool, str]:
        """Test the LLM API connection.

        Returns:
        """
        if not self.api_key:
            return False, "API key not configured"
        try:
            # Opt in to allow_loopback_http=True because this
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

    def _call_api(self, text: str, system_prompt: str, *, timeout_s: float | None = None) -> str:
        """Call the OpenAI-compatible chat completions API."""
        # Opt in to allow_loopback_http=True: see the
        assert_url_allowed(
            self.api_url,
            field_name="llm_api_url",
            client_name="llm_polish",
            allow_loopback_http=True,
        )

        # redact PII from the user-content text before API send.
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
            log.warning(
                "[LLM_POLISH] redact_pii failed, skipping LLM API call (returning original text unpolished)",
                exc_info=True,
            )
            return text

        # Endpoint-aware sampling params. OpenAI reasoning
        if _is_openai_first_party_endpoint(self.api_url):
            sampling_params: dict = {
                "temperature": 1,
                "max_completion_tokens": _FLAT_MAX_TOKENS,
            }
        else:
            sampling_params = {
                "temperature": 0.3,
                # flat ``max_tokens``: the previous
                "max_tokens": _FLAT_MAX_TOKENS,
            }
        payload = json.dumps(
            {
                "model": self.model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": text},
                ],
                **sampling_params,
            }
        ).encode("utf-8")

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }

        req = Request(self.api_url, data=payload, headers=headers, method="POST")

        # configurable timeout. When ``timeout_s`` is ``None`` we
        effective_timeout = DEFAULT_TIMEOUT_S if timeout_s is None else timeout_s

        try:
            with _opener.open(req, timeout=effective_timeout) as resp:
                _verify_llm_peer(req, resp)
                # SEC-030: cap response at 50 MB to prevent OOM from
                from voice_typer.server.cloud_engines import _read_capped

                raw = _read_capped(resp, max_bytes=50 * 1024 * 1024)
                result = json.loads(raw.decode("utf-8"))
                choices = result.get("choices", [])
                if choices:
                    return choices[0].get("message", {}).get("content", "")
                return ""
        except HTTPError as exc:
            # HTTPError is a subclass of URLError, so it MUST be
            safe_msg = redact_secret(redact_url(str(exc)))
            if 500 <= exc.code < 600:
                raise CloudServerError(f"LLM API server error (HTTP {exc.code}): {safe_msg}") from exc
            raise CloudEngineError(f"LLM API error (HTTP {exc.code}): {safe_msg}") from exc
        except URLError as exc:
            # Typed ``CloudNetworkError`` (was generic
            raise CloudNetworkError(f"LLM API error: {redact_secret(redact_url(str(exc)))}") from exc
        except Exception as exc:
            # Typed base ``CloudEngineError`` (was generic
            raise CloudEngineError(f"LLM API error: {redact_secret(str(exc))}") from exc
