"""Cloud-connection test IPC handler mixin: test_cloud_connection.

This handler exists to keep the cloud-provider "Test Connection" probe
on the Python side of the IPC boundary. Previously the renderer issued a
cross-origin ``fetch`` directly to the provider's API endpoint
(``api.openai.com`` / ``api.groq.com`` / ``api.deepgram.com``). That
renderer-side fetch:

  * leaked the user's API key through the ``Authorization`` header on a
    cross-origin request (the browser exposes the request headers to any
    extension / dev-tools observer);
  * surfaced CORS / DNS / offline failures as an opaque
    ``TypeError: Failed to fetch`` with no actionable message;
  * violated the C-DATA-1 "offline application" promise: even though
    the *test* network call is initiated by an explicit user action on
    the Cloud tab, the production code path is "renderer makes a network
    call" — which is exactly the pattern C-DATA-1 prohibits.

Routing the probe through a Python IPC handler resolves all three:

  * the API key never leaves the Python process (the renderer sends
    only the provider name; the key is read from the live ``Config``
    dataclass);
  * network / DNS / TLS failures are caught and surfaced with a
    specific message ("network unreachable" vs. "HTTP 401");
  * the renderer code path stays network-free (C-DATA-1 compliant) —
    the only network call is on the Python side, gated by an explicit
    user click.

The handler uses ``urllib.request`` (Python stdlib) so no new
third-party dependency is added. The HTTP call is bounded by a 10s
timeout (matching ``CloudEngine._REQUEST_TIMEOUT_SECONDS``) so a stuck
provider endpoint cannot block the IPC dispatcher thread indefinitely.

Consent: this handler does NOT consult the per-provider consent flag.
Consent governs audio transmission during dictation; the "Test
Connection" probe sends no audio — it issues an authenticated GET to
the provider's ``/models`` (or ``/projects``) endpoint to verify the
key. Requiring consent for a key-validity probe would be confusing
(the user clicks "Test Connection" specifically to validate the key
they just entered). The consent gate remains on the dictation path.
"""

from __future__ import annotations

import contextlib
from urllib.error import HTTPError, URLError
from urllib.request import Request

from voice_typer.server._http_safety import build_secure_opener
from voice_typer.server.handlers._base import HandlerBase
from voice_typer.server.handlers._log import log
from voice_typer.server.ipc.validation import (
    ErrorCodes,
    LegacyErrorCodes,
    ResponseEnvelope,
    _validate_dict_payload,
)

# Secure opener installs _NoRedirectHandler + _HttpsOnlyHTTPHandler to
# prevent API key exfiltration via 3xx redirect.
_opener = build_secure_opener()

# Per-provider HTTP test endpoint + Authorization header scheme.
# ``/v1/models`` (OpenAI / Groq) and ``/v1/projects`` (Deepgram) are
# the cheapest authenticated GETs each provider exposes — they return a
# small JSON list and require a valid API key, so a 200 means "the key
# works", a 401/403 means "the key is invalid", and any other status
# (or network failure) means "the provider is unreachable / erroring".
_PROVIDER_TEST_ENDPOINTS: dict[str, dict[str, str]] = {
    "openai": {
        "url": "https://api.openai.com/v1/models",
        "auth_scheme": "Bearer",
    },
    "groq": {
        "url": "https://api.groq.com/openai/v1/models",
        "auth_scheme": "Bearer",
    },
    "deepgram": {
        "url": "https://api.deepgram.com/v1/projects",
        "auth_scheme": "Token",
    },
}

# Map provider name → Config dataclass field name holding the API key.
# Kept in sync with ``credential_store.PROVIDER_TO_CONFIG_FIELD`` (the
# canonical source) but inlined here to avoid importing the keyring
# module just for the lookup. If a new cloud provider is added, BOTH
# this dict and ``credential_store.PROVIDER_TO_CONFIG_FIELD`` must be
# updated.
_PROVIDER_TO_CONFIG_FIELD: dict[str, str] = {
    "openai": "openai_api_key",
    "groq": "groq_api_key",
    "deepgram": "deepgram_api_key",
}

# 10s timeout matches ``CloudEngine._REQUEST_TIMEOUT_SECONDS``. The test
# endpoint returns a small JSON list (~1-2 KB) so 10s is ample even on a
# slow link; the bound prevents a stalled provider from holding the IPC
# dispatcher thread indefinitely.
_TEST_TIMEOUT_SECONDS: float = 10.0


class CloudTestHandlersMixin(HandlerBase):
    """Mixin: cloud-provider connection-test IPC handler.

    Registered as the ``test_cloud_connection`` IPC command. The
    renderer's ``useCloudProviders.testConnection`` action calls this
    handler instead of issuing a cross-origin ``fetch`` directly (which
    violated C-DATA-1 and leaked the API key through browser dev-tools
    observability).
    """

    def _handle_test_cloud_connection(self, data: object | None, resp: ResponseEnvelope) -> ResponseEnvelope | None:
        """Handle the ``test_cloud_connection`` IPC command.

        Payload: ``{"provider": "openai" | "groq" | "deepgram"}``.

        Returns a response envelope with ``type = "cloud_test_result"``
        and ``data = {"ok": bool, "status": int, "message": str}``:

          * ``ok=True, status=200`` — the API key is valid; the provider
            is reachable.
          * ``ok=False, status=401|403`` — the API key is invalid or
            revoked. The renderer surfaces a "key invalid" message.
          * ``ok=False, status=429`` — rate-limited. The renderer
            surfaces a "rate-limited, retry shortly" message.
          * ``ok=False, status=5xx`` — provider server error.
          * ``ok=False, status=0`` — network / DNS / TLS failure (the
            request never reached the provider). The renderer surfaces
            a "network blocked the request" message.

        Per-command validation errors (missing / unknown provider,
        missing API key) are routed through the inline ``error`` block
        below (explicit, documented error shape the renderer switches
        on). The catch-all ``except Exception`` path uses the generic
        WS-path envelope via ``_respond_with_error`` (no ``str(exc)``
        leak).
        """
        # TODO: not migrated to ``_wrap`` — has side effects
        # (HTTP request via ``_opener.open``, multiple ``log.info`` /
        # ``log.warning`` calls, multiple early-return error envelopes
        # with distinct shapes that don't fit ``_wrap``'s merge contract).
        try:
            validated, error = _validate_dict_payload(
                data,
                {
                    "provider": {"type": str, "required": False, "default": ""},
                },
            )
            if error:
                return error
            assert validated is not None  # narrowed by the error guard above
            provider_raw = validated.get("provider", "")
            provider = provider_raw if isinstance(provider_raw, str) else ""

            if not provider:
                log.warning("[IPC] test_cloud_connection called without provider")
                return self._error_response(
                    resp,
                    "Missing 'provider' parameter",
                    code=ErrorCodes.MISSING_FIELD,
                    field="provider",
                )

            endpoint = _PROVIDER_TEST_ENDPOINTS.get(provider)
            if endpoint is None:
                log.warning(
                    "[IPC] test_cloud_connection called with unknown provider: %s",
                    provider,
                )
                return self._error_response(
                    resp,
                    f"Unknown provider: {provider}",
                    code=ErrorCodes.INVALID_FIELD,
                    field="provider",
                )

            # Look up the API key from the live Config dataclass (NOT
            # from ``service.get_config()`` which returns a sanitized
            # view with ``<redacted>`` sentinels for secret fields).
            config_field = _PROVIDER_TO_CONFIG_FIELD.get(provider)
            if config_field is None:
                # Defensive: should be unreachable because the endpoint
                # lookup above already rejected unknown providers.
                return self._error_response(
                    resp,
                    f"Unknown provider: {provider}",
                    code=ErrorCodes.INVALID_FIELD,
                    field="provider",
                )

            api_key = getattr(self.app.config, config_field, "") or ""
            if not api_key:
                # No key configured — surface as an "info" result so the
                # renderer can prompt the user to enter a key first
                # (matches the renderer-side pre-check that existed
                # before this handler was added).
                resp["type"] = "cloud_test_result"
                resp["data"] = {
                    "ok": False,
                    "status": 0,
                    "message": "no_api_key",
                }
                return resp

            # Build the authenticated GET request. The ``Authorization``
            # header value is constructed here in Python — the renderer
            # never sees the key (only the provider name crosses IPC).
            url = endpoint["url"]
            auth_scheme = endpoint["auth_scheme"]
            headers = {
                "Authorization": f"{auth_scheme} {api_key}",
                "Accept": "application/json",
                # ``User-Agent`` helps some providers' WAFs accept the
                # request (Deepgram in particular rejects the urllib
                # default UA with a 403). Identifies the probe as the
                # Voice Typer "Test Connection" feature.
                "User-Agent": "voice-typer-cloud-test/1.0",
            }
            req = Request(url=url, headers=headers, method="GET")

            try:
                with _opener.open(req, timeout=_TEST_TIMEOUT_SECONDS) as http_resp:
                    # ``http_resp.status`` is the HTTP status code (int).
                    status_code = int(getattr(http_resp, "status", 200) or 200)
                    # Drain the response body so the connection can be
                    # reused (urllib's connection pool benefits from a
                    # fully-consumed response). The body itself is not
                    # needed — the test only cares about the status code.
                    with contextlib.suppress(Exception):
                        # Body read failure is non-fatal — the status
                        # code is what we report.
                        http_resp.read()
            except HTTPError as http_err:
                # HTTPError is raised for non-2xx responses. The error
                # carries the status code on ``http_err.code``.
                status_code = int(getattr(http_err, "code", 0) or 0)
                resp["type"] = "cloud_test_result"
                resp["data"] = {
                    "ok": False,
                    "status": status_code,
                    "message": _http_error_message(status_code),
                }
                log.info(
                    "[IPC] test_cloud_connection: provider=%s status=%s",
                    provider,
                    status_code,
                )
                return resp
            except URLError as url_err:
                # URLError covers DNS failures, connection refused,
                # TLS errors, timeouts. Surface as status=0 so the
                # renderer can show a "network blocked" message distinct
                # from any HTTP status the provider would return.
                log.info(
                    "[IPC] test_cloud_connection: provider=%s network error: %s",
                    provider,
                    url_err.reason,
                )
                resp["type"] = "cloud_test_result"
                resp["data"] = {
                    "ok": False,
                    "status": 0,
                    "message": "network_error",
                }
                return resp
            except TimeoutError:
                # ``urlopen`` raises ``TimeoutError`` (a builtin) when
                # the ``timeout`` argument is exceeded. Map it to the
                # same status=0 "network_error" bucket as URLError.
                log.info(
                    "[IPC] test_cloud_connection: provider=%s timed out after %ss",
                    provider,
                    _TEST_TIMEOUT_SECONDS,
                )
                resp["type"] = "cloud_test_result"
                resp["data"] = {
                    "ok": False,
                    "status": 0,
                    "message": "network_error",
                }
                return resp

            # 2xx — the API key is valid and the provider is reachable.
            resp["type"] = "cloud_test_result"
            resp["data"] = {
                "ok": True,
                "status": status_code,
                "message": "ok",
            }
            log.info(
                "[IPC] test_cloud_connection: provider=%s status=%s OK",
                provider,
                status_code,
            )
        except Exception as exc:
            # Catch-all: generic WS-path envelope (no ``str(exc)`` leak).
            self._respond_with_error(resp, exc, "test_cloud_connection")
        return resp


def _http_error_message(status_code: int) -> str:
    """Return a short, renderer-stable message token for an HTTP status.

    The renderer's ``useCloudProviders.testConnection`` switches on
    these tokens (rather than the raw HTTP status) so the i18n layer
    can localize the message. Tokens:

      * ``auth_failed``    — 401, 403 (key invalid / revoked)
      * ``rate_limited``   — 429
      * ``server_error``   — 5xx
      * ``http_error``     — any other non-2xx status

    Kept module-private (no underscore prefix on the export would make
    it part of the public surface); the renderer never sees these
    tokens directly — they're an internal contract between this handler
    and the renderer's status-code-to-i18n-key mapping.
    """
    if status_code in (401, 403):
        return LegacyErrorCodes.AUTH_FAILED
    if status_code == 429:
        return LegacyErrorCodes.RATE_LIMITED
    if 500 <= status_code < 600:
        return "server_error"
    return "http_error"


# Exposed for tests / type-checkers; not part of the IPC contract.
__all__: list[str] = ["CloudTestHandlersMixin", "_http_error_message"]


# Silence unused-import warning for ``json`` (kept for future
# response-body parsing if a provider's test endpoint ever returns a
# payload we need to surface to the renderer).
