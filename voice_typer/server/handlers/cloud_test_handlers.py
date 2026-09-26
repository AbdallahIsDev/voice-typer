"""* violated the C-DATA-1 "offline application" promise: even though
    call": which is exactly the pattern C-DATA-1 prohibits.
  * the renderer code path stays network-free (C-DATA-1 compliant) —
cross-origin ``fetch`` directly to the provider's API endpoint
(``api.openai.com`` / ``api.groq.com`` / ``api.deepgram.com``). That
  * leaked the user's API key through the ``Authorization`` header on a
    ``TypeError: Failed to fetch`` with no actionable message;
    only the provider name; the key is read from the live ``Config``
The handler uses ``urllib.request`` (Python stdlib) so no new
timeout (matching ``CloudEngine._REQUEST_TIMEOUT_SECONDS``) so a stuck
the provider's ``/models`` (or ``/projects``) endpoint to verify the
"""

from __future__ import annotations

import contextlib
from urllib.error import HTTPError, URLError
from urllib.request import Request

from voice_typer.server._http_safety import build_secure_opener
from voice_typer.server.credential_store import PROVIDER_TO_CONFIG_FIELD
from voice_typer.server.handlers._base import HandlerBase
from voice_typer.server.handlers._log import log
from voice_typer.server.ipc.validation import (
    ErrorCodes,
    LegacyErrorCodes,
    ResponseEnvelope,
)

# Secure opener installs _NoRedirectHandler + _HttpsOnlyHTTPHandler to
_opener = build_secure_opener()

# Per-provider HTTP test endpoint + Authorization header scheme.
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

# Imported from ``credential_store``: the single authoritative source

# 10s timeout matches ``CloudEngine._REQUEST_TIMEOUT_SECONDS``. The test
_TEST_TIMEOUT_SECONDS: float = 10.0


class CloudTestHandlersMixin(HandlerBase):
    """violated C-DATA-1 and leaked the API key through browser dev-tools"""

    def _handle_test_cloud_connection(self, data: object | None, resp: ResponseEnvelope) -> ResponseEnvelope | None:
        """Handle the ``test_cloud_connection`` IPC command.

        Returns a response envelope with ``type = "cloud_test_result"``
        """

        def body(d: dict) -> dict:
            provider_raw = d.get("provider", "")
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
            config_field = PROVIDER_TO_CONFIG_FIELD.get(provider)
            if config_field is None:
                # Defensive: should be unreachable because the endpoint
                return self._error_response(
                    resp,
                    f"Unknown provider: {provider}",
                    code=ErrorCodes.INVALID_FIELD,
                    field="provider",
                )

            api_key = getattr(self.app.config, config_field, "") or ""
            if not api_key:
                # No key configured, surface as an "info" result so the
                return {
                    "type": "cloud_test_result",
                    "data": {
                        "ok": False,
                        "status": 0,
                        "message": "no_api_key",
                    },
                }

            # Build the authenticated GET request. The ``Authorization``
            url = endpoint["url"]
            auth_scheme = endpoint["auth_scheme"]
            headers = {
                "Authorization": f"{auth_scheme} {api_key}",
                "Accept": "application/json",
                # ``User-Agent`` helps some providers' WAFs accept the
                "User-Agent": "lausu-cloud-test/1.0",
            }
            req = Request(url=url, headers=headers, method="GET")

            try:
                with _opener.open(req, timeout=_TEST_TIMEOUT_SECONDS) as http_resp:
                    # ``http_resp.status`` is the HTTP status code (int).
                    status_code = int(getattr(http_resp, "status", 200) or 200)
                    # Drain the response body so the connection can be
                    with contextlib.suppress(Exception):
                        # Body read failure is non-fatal, the status
                        http_resp.read()
            except HTTPError as http_err:
                # HTTPError is raised for non-2xx responses. The error
                status_code = int(getattr(http_err, "code", 0) or 0)
                log.info(
                    "[IPC] test_cloud_connection: provider=%s status=%s",
                    provider,
                    status_code,
                )
                return {
                    "type": "cloud_test_result",
                    "data": {
                        "ok": False,
                        "status": status_code,
                        "message": _http_error_message(status_code),
                    },
                }
            except URLError as url_err:
                # URLError covers DNS failures, connection refused,
                log.info(
                    "[IPC] test_cloud_connection: provider=%s network error: %s",
                    provider,
                    url_err.reason,
                )
                return {
                    "type": "cloud_test_result",
                    "data": {
                        "ok": False,
                        "status": 0,
                        "message": "network_error",
                    },
                }
            except TimeoutError:
                # ``urlopen`` raises ``TimeoutError`` (a builtin) when
                log.info(
                    "[IPC] test_cloud_connection: provider=%s timed out after %ss",
                    provider,
                    _TEST_TIMEOUT_SECONDS,
                )
                return {
                    "type": "cloud_test_result",
                    "data": {
                        "ok": False,
                        "status": 0,
                        "message": "network_error",
                    },
                }

            # 2xx, the API key is valid and the provider is reachable.
            log.info(
                "[IPC] test_cloud_connection: provider=%s status=%s OK",
                provider,
                status_code,
            )
            return {
                "type": "cloud_test_result",
                "data": {
                    "ok": True,
                    "status": status_code,
                    "message": "ok",
                },
            }

        return self._wrap(
            cmd_name="test_cloud_connection",
            resp_type="cloud_test_result",
            data=data,
            resp=resp,
            body=body,
            schema={"provider": {"type": str, "required": False, "default": ""}},
            pre_coerce=False,
        )


def _http_error_message(status_code: int) -> str:
    """Return a short, renderer-stable message token for an HTTP status."""
    if status_code in (401, 403):
        return LegacyErrorCodes.AUTH_FAILED
    if status_code == 429:
        return LegacyErrorCodes.RATE_LIMITED
    if 500 <= status_code < 600:
        return "server_error"
    return "http_error"


# Exposed for tests / type-checkers; not part of the IPC contract.
__all__: list[str] = ["CloudTestHandlersMixin", "_http_error_message"]
