"""The cloud "Test Connection" handler must use the canonical provider
map, not a manual copy.

``handlers/cloud_test_handlers.py`` historically kept a 3-key manual
copy of ``credential_store.PROVIDER_TO_CONFIG_FIELD`` (the canonical
5-key map) with a comment claiming the copy existed to "avoid
importing the keyring module", a false rationale: the
``credential_store`` package imports keyring lazily (only inside the
backend probe), so importing the map costs no keyring import.

The copy was a drift trap: ``credential_store`` gained ``cloud`` and
``llm`` entries the copy never saw, and every future provider would
need a THIRD hand-edit before "Test Connection" could read its API key
— miss it and the user gets "Unknown provider" for a provider the rest
of the app fully supports.

These tests pin:
1. The handler module resolves the SAME map object as the canonical
   source (identity, a re-copy or a rebind to a different dict fails).
2. Every provider with a test endpoint is covered by the canonical map
   (endpoint-map keys are a subset of canonical providers).
3. A provider newly added to the canonical map (+ an endpoint) is
   AUTOMATICALLY supported by "Test Connection": the key is read from
   the Config field the canonical map names, with no third dict to
   update. Simulated by adding a provider to the canonical map object
   in place (the handler's binding is the same dict object) and to the
   endpoint map, then asserting the authenticated request the handler
   builds carries that provider's key.
4. The provider-name → config-field resolution is behavioral: for each
   real provider with a test endpoint, the Authorization header is
   built from the canonical field's value.
"""

from __future__ import annotations

import subprocess
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from voice_typer.server.credential_store import PROVIDER_TO_CONFIG_FIELD
from voice_typer.server.handlers import cloud_test_handlers

# ---------------------------------------------------------------------------
# Structural: single source of truth
# ---------------------------------------------------------------------------


class TestProviderMapSingleSource:
    """The provider → config-field mapping used by the "Test
    Connection" handler must be the canonical credential_store map."""

    def test_handler_uses_canonical_provider_map_object(self):
        """The handler's map IS ``credential_store.PROVIDER_TO_CONFIG_FIELD``
        (same object), never a re-copied dict that can drift."""
        handler_map = getattr(cloud_test_handlers, "PROVIDER_TO_CONFIG_FIELD", None)
        assert handler_map is PROVIDER_TO_CONFIG_FIELD, (
            "cloud_test_handlers must use credential_store.PROVIDER_TO_CONFIG_FIELD "
            "directly (same object), a manual copy of the provider map drifts when "
            "the canonical map gains a provider (the cloud/llm entries were already "
            "missing from the copy)."
        )

    def test_no_manual_provider_field_map_defined_in_source(self):
        """The module source must not define its own provider→field dict
        (the drift trap itself must not come back)."""
        import inspect

        source = inspect.getsource(cloud_test_handlers)
        assert "_PROVIDER_TO_CONFIG_FIELD" not in source, (
            "cloud_test_handlers defines a module-local provider→field map, the "
            "canonical credential_store.PROVIDER_TO_CONFIG_FIELD must be imported "
            "instead (no second definition to keep in sync)."
        )

    def test_endpoint_map_providers_are_canonical(self):
        """Every provider that has a test endpoint must exist in the
        canonical map, otherwise the endpoint is unreachable dead
        config and the key lookup has no field to read."""
        endpoints = cloud_test_handlers._PROVIDER_TEST_ENDPOINTS
        unknown = set(endpoints) - set(PROVIDER_TO_CONFIG_FIELD)
        assert not unknown, (
            f"_PROVIDER_TEST_ENDPOINTS contains providers missing from the "
            f"canonical credential_store map: {sorted(unknown)}"
        )

    def test_importing_handler_does_not_import_keyring(self):
        """The historical rationale for the copy ("avoid importing the
        keyring module") was false, importing the handler module (and
        with it ``credential_store``) never imports keyring, which is
        resolved lazily inside the backend probe.

        Checked in a fresh subprocess so the assertion is independent
        of whatever other tests may have loaded into this process's
        ``sys.modules`` (order-dependence)."""
        import sys

        code = (
            "import sys; "
            "import voice_typer.server.handlers.cloud_test_handlers; "
            "sys.exit(0 if not any(m == 'keyring' or m.startswith('keyring.') "
            "for m in sys.modules) else 1)"
        )
        completed = subprocess.run([sys.executable, "-c", code], capture_output=True, timeout=60)
        assert completed.returncode == 0, (
            "importing cloud_test_handlers pulled keyring into sys.modules, the "
            "credential-store keyring import must stay lazy (the old 'avoid "
            "importing the keyring module' rationale for the manual map copy)."
            f" stderr: {completed.stderr.decode()[:500]}"
        )


# ---------------------------------------------------------------------------
# Behavioral: key lookup follows the canonical map
# ---------------------------------------------------------------------------


def _make_handler_with_config(**config_fields) -> cloud_test_handlers.CloudTestHandlersMixin:
    """Build a ``CloudTestHandlersMixin`` with a fake app whose
    ``config`` carries the given API-key fields (mirrors the fake-app
    pattern in test_cloud_test_handlers_redirect.py)."""
    handler = cloud_test_handlers.CloudTestHandlersMixin()
    handler.app = SimpleNamespace(config=SimpleNamespace(**config_fields))
    handler.service = MagicMock()
    handler._send = MagicMock()
    return handler


class TestKeyLookupFollowsCanonicalMap:
    """For each real provider with a test endpoint, the Authorization
    header the handler builds must carry the API key stored in the
    Config field named by the canonical map."""

    @pytest.mark.parametrize(
        ("provider", "config_field", "auth_scheme"),
        [
            ("openai", "openai_api_key", "Bearer"),
            ("groq", "groq_api_key", "Bearer"),
            ("deepgram", "deepgram_api_key", "Token"),
        ],
    )
    def test_authorization_header_uses_canonical_config_field(self, provider, config_field, auth_scheme):
        """The Authorization header is ``<scheme> <key>`` where <key>
        comes from ``app.config.<canonical field name>``, pinning the
        provider→field resolution behaviorally for every testable
        provider."""
        secret = f"{provider}-test-key-DO-NOT-USE"
        handler = _make_handler_with_config(**{config_field: secret})
        resp: dict = {"type": "", "data": {}}

        captured: dict = {}

        def capture_and_raise(req, timeout=None):
            captured["headers"] = dict(req.header_items())
            raise cloud_test_handlers.HTTPError(url=req.full_url, code=401, msg="unauthorized", hdrs=None, fp=None)

        with patch.object(cloud_test_handlers, "_opener") as opener_mock:
            opener_mock.open = MagicMock(side_effect=capture_and_raise)
            handler._handle_test_cloud_connection({"provider": provider}, resp)

        auth = captured["headers"].get("Authorization")
        assert auth == f"{auth_scheme} {secret}", (
            f"Authorization header for {provider} must be '{auth_scheme} <key>' with "
            f"the key read from app.config.{config_field} (the canonical-map field), "
            f"got {auth!r}"
        )

    def test_newly_added_canonical_provider_is_supported_without_third_edit(self):
        """A provider added to the canonical map (in place, the
        handler's binding is the same dict object, exactly like a new
        process start) plus a test endpoint is AUTOMATICALLY supported:
        the handler reads its API key from the canonical field name with
        no manual provider→field copy to update."""
        provider = "provtest"
        field = "provtest_api_key"
        PROVIDER_TO_CONFIG_FIELD[provider] = field
        saved_endpoint = None
        try:
            saved_endpoint = cloud_test_handlers._PROVIDER_TEST_ENDPOINTS.get(provider)
            cloud_test_handlers._PROVIDER_TEST_ENDPOINTS[provider] = {
                "url": "https://api.provtest.example/v1/models",
                "auth_scheme": "Bearer",
            }
            secret = "provtest-key-DO-NOT-USE"
            handler = _make_handler_with_config(**{field: secret})
            resp: dict = {"type": "", "data": {}}

            captured: dict = {}

            def capture_and_raise(req, timeout=None):
                captured["headers"] = dict(req.header_items())
                captured["url"] = req.full_url
                raise cloud_test_handlers.HTTPError(url=req.full_url, code=401, msg="unauthorized", hdrs=None, fp=None)

            with patch.object(cloud_test_handlers, "_opener") as opener_mock:
                opener_mock.open = MagicMock(side_effect=capture_and_raise)
                handler._handle_test_cloud_connection({"provider": provider}, resp)

            # No error envelope: the provider was recognized end-to-end.
            assert resp["type"] == "cloud_test_result"
            assert captured["headers"].get("Authorization") == f"Bearer {secret}", (
                "a provider present in the canonical map (and the endpoint map) must "
                "have its API key read via the canonical field, no third dict to edit"
            )
            assert captured["url"] == "https://api.provtest.example/v1/models"
        finally:
            # In-place mutation of the canonical map must be undone, it
            # is the SAME dict object shared with credential_store.
            del PROVIDER_TO_CONFIG_FIELD[provider]
            if saved_endpoint is None:
                del cloud_test_handlers._PROVIDER_TEST_ENDPOINTS[provider]
            else:
                cloud_test_handlers._PROVIDER_TEST_ENDPOINTS[provider] = saved_endpoint
