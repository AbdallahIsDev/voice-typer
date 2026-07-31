"""Tests that ``_SECRET_CONFIG_FIELDS`` is a SINGLE canonical frozenset.

Previously ``ipc.history_bounds._SECRET_CONFIG_FIELDS`` was a hand-
maintained literal copy of the 5-field set, divergent from the
canonical ``config_sanitizer._SECRET_CONFIG_FIELDS`` (which is
structurally derived from
``credential_store.PROVIDER_TO_CONFIG_FIELD.values()``). A contributor
adding a new provider to ``PROVIDER_TO_CONFIG_FIELD`` without updating
the history_bounds literal would silently leave the new API key
un-redacted on the IPC path — a credential disclosure.

The fix: ``history_bounds._SECRET_CONFIG_FIELDS`` is now an ALIAS
IMPORT from ``config_sanitizer``, so the two names refer to the SAME
frozenset object. Adding a provider to
``PROVIDER_TO_CONFIG_FIELD`` automatically updates both names.
"""

from __future__ import annotations

import pytest
from voice_typer.server.config_sanitizer import (
    _SECRET_CONFIG_FIELDS as _CANONICAL_FIELDS,
)
from voice_typer.server.ipc.history_bounds import (
    _SECRET_CONFIG_FIELDS as _HISTORY_BOUNDS_FIELDS,
)
from voice_typer.server.ipc.history_bounds import (
    _is_secret_field_name,
    _sanitize_config_for_ipc,
)


class TestSecretConfigFieldsAlias:
    """The two ``_SECRET_CONFIG_FIELDS`` names must be the SAME object."""

    def test_history_bounds_alias_is_canonical_object(self):
        """``history_bounds._SECRET_CONFIG_FIELDS`` must be the SAME
        frozenset object as ``config_sanitizer._SECRET_CONFIG_FIELDS``
        — not a copy, not a divergent literal."""
        assert _HISTORY_BOUNDS_FIELDS is _CANONICAL_FIELDS, (
            "history_bounds._SECRET_CONFIG_FIELDS must be an alias import "
            "from config_sanitizer (same object identity), not a separate "
            "frozenset literal. Found divergent objects: "
            f"history_bounds={sorted(_HISTORY_BOUNDS_FIELDS)!r}, "
            f"config_sanitizer={sorted(_CANONICAL_FIELDS)!r}"
        )

    def test_ipc_server_reexport_is_also_canonical(self):
        """``ipc_server._SECRET_CONFIG_FIELDS`` (the legacy import path
        used by ``crash_recovery.py``) must also be the same object."""
        from voice_typer.server.ipc_server import (
            _SECRET_CONFIG_FIELDS as _IPC_SERVER_FIELDS,
        )

        assert _IPC_SERVER_FIELDS is _CANONICAL_FIELDS, (
            "ipc_server._SECRET_CONFIG_FIELDS must re-export the canonical "
            "frozenset from config_sanitizer (same object identity)."
        )

    def test_ipc_package_reexport_is_also_canonical(self):
        """``voice_typer.server.ipc._SECRET_CONFIG_FIELDS`` (re-exported
        via ``ipc/__init__.py``) must also be the same object."""
        from voice_typer.server.ipc import (
            _SECRET_CONFIG_FIELDS as _IPC_PACKAGE_FIELDS,
        )

        assert _IPC_PACKAGE_FIELDS is _CANONICAL_FIELDS, (
            "ipc._SECRET_CONFIG_FIELDS must re-export the canonical "
            "frozenset from config_sanitizer (same object identity)."
        )


class TestSecretConfigFieldsContainsKnownProviders:
    """The canonical frozenset must contain AT LEAST the 5 known
    provider fields (the historical literal). A future contributor
    adding a provider to ``PROVIDER_TO_CONFIG_FIELD`` will expand this
    set — the test asserts "contains at least" rather than "exactly"
    so the addition doesn't break the test."""

    @pytest.mark.parametrize(
        "field_name",
        [
            "cloud_api_key",
            "openai_api_key",
            "groq_api_key",
            "deepgram_api_key",
            "llm_api_key",
        ],
    )
    def test_known_provider_field_is_in_canonical_set(self, field_name):
        assert field_name in _CANONICAL_FIELDS, (
            f"Known provider field {field_name!r} must be in the canonical "
            f"_SECRET_CONFIG_FIELDS (derived from "
            f"credential_store.PROVIDER_TO_CONFIG_FIELD.values()). Got: "
            f"{sorted(_CANONICAL_FIELDS)!r}"
        )

    def test_history_bounds_alias_contains_same_fields(self):
        """The alias must contain the same fields as the canonical set
        (because it IS the same object)."""
        assert {
            "cloud_api_key",
            "openai_api_key",
            "groq_api_key",
            "deepgram_api_key",
            "llm_api_key",
        } <= _HISTORY_BOUNDS_FIELDS, (
            "history_bounds._SECRET_CONFIG_FIELDS must contain at least the 5 known provider fields."
        )


class TestSanitizerUsesCanonicalSet:
    """``_sanitize_config_for_ipc`` (in history_bounds) must use the
    canonical frozenset — redacting a field that's in the canonical
    set but NOT in a stale local literal."""

    def test_canonical_field_is_redacted_by_history_bounds_sanitizer(self):
        """If a field is in ``_SECRET_CONFIG_FIELDS``, the
        ``history_bounds._sanitize_config_for_ipc`` function must
        redact it."""

        class _ConfigLike:
            def __init__(self, **kwargs):
                for k, v in kwargs.items():
                    setattr(self, k, v)

        # Pick the first field in the canonical set and verify it's
        # redacted by the history_bounds sanitizer.
        field_name = next(iter(_CANONICAL_FIELDS))
        cfg = _ConfigLike(**{field_name: "sk-test-key"})
        out = _sanitize_config_for_ipc(cfg)
        assert out[field_name] == "<redacted>", (
            f"Field {field_name!r} is in the canonical "
            f"_SECRET_CONFIG_FIELDS but was NOT redacted by "
            f"history_bounds._sanitize_config_for_ipc. This means the "
            f"sanitizer is using a STALE local literal instead of the "
            f"canonical alias."
        )

    def test_is_secret_field_name_uses_canonical_set(self):
        """``_is_secret_field_name`` must classify a field in the
        canonical set as secret."""
        field_name = next(iter(_CANONICAL_FIELDS))
        assert _is_secret_field_name(field_name) is True, (
            f"Field {field_name!r} is in the canonical "
            f"_SECRET_CONFIG_FIELDS but _is_secret_field_name returned "
            f"False. The function must consult the canonical alias."
        )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
