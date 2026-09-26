"""Schema constants for the credential store package."""

from __future__ import annotations

import logging
from typing import TypeVar

log = logging.getLogger("voice_typer.server.credential_store")

_T = TypeVar("_T")

#: The ``keyring`` service name. All Lausu secrets live under this
KEYRING_SERVICE_NAME = "com.Lausu.keyring"

#: Reserved keyring username under which the history at-rest-encryption
DATA_ENCRYPTION_KEY_USERNAME = "__data_encryption_key__"

#: Prior service names used by Lausu. :func:`migrate_secrets_to_keyring`
_LEGACY_KEYRING_SERVICE_NAMES: tuple[str, ...] = ("app.Lausu", "lausu")

#: Config flag key (in ``config.json``) that gates the legacy-service-name
_SERVICE_NAME_MIGRATED_FLAG = f"service_name_migrated_{KEYRING_SERVICE_NAME.replace('.', '_')}"

#: The prefix used in config.json reference tokens. A flat api_key field
KEYRING_REF_PREFIX = "keyring://"

#: Map of provider name -> Config dataclass field name.
PROVIDER_TO_CONFIG_FIELD: dict[str, str] = {
    "openai": "openai_api_key",
    "groq": "groq_api_key",
    "deepgram": "deepgram_api_key",
    "cloud": "cloud_api_key",
    "llm": "llm_api_key",
}

#: Reverse lookup: Config dataclass field name -> provider name.
CONFIG_FIELD_TO_PROVIDER: dict[str, str] = {v: k for k, v in PROVIDER_TO_CONFIG_FIELD.items()}

#: deprecated / typo'd provider names that prior app versions may have
_KNOWN_PROVIDERS_HISTORY: frozenset[str] = frozenset(PROVIDER_TO_CONFIG_FIELD.keys())

#: Maximum length of a sanitized reason / diagnostic string.
_REASON_MAX_LEN = 200

__all__ = [
    "CONFIG_FIELD_TO_PROVIDER",
    "DATA_ENCRYPTION_KEY_USERNAME",
    "KEYRING_REF_PREFIX",
    "KEYRING_SERVICE_NAME",
    "PROVIDER_TO_CONFIG_FIELD",
    "_KNOWN_PROVIDERS_HISTORY",
    "_LEGACY_KEYRING_SERVICE_NAMES",
    "_REASON_MAX_LEN",
    "_SERVICE_NAME_MIGRATED_FLAG",
    "_T",
    "log",
]
