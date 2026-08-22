"""Schema constants for the credential store package.

This module owns the "constants & provider map" concern. It holds the
service-name constants, the provider<->config-field mapping, the legacy
service-name history used by the migration cutover, and the redaction
length cap. These values are immutable across the lifetime of the
process; tests do not monkey-patch them (with one exception noted
below), so submodules can safely bind them at import time.

One exception: :data:`_KNOWN_PROVIDERS_HISTORY` IS monkey-patched by
tests (``tests/test_credential_store_gdpr.py`` replaces it with a
test-specific frozenset). Consumers therefore read it via the package
module (``_cs._KNOWN_PROVIDERS_HISTORY``) at call time so a test-time
override on ``voice_typer.server.credential_store`` propagates to the
call site.

The shared :data:`log` logger and the :data:`_T` ``TypeVar`` also live
here so every submodule can ``from ._schema import log`` and write log
records under the canonical ``voice_typer.server.credential_store``
logger name (pinned by ``tests/test_credential_store_group_fixes.py``
which does ``caplog.at_level(logging.WARNING,
logger=credential_store.log.name)``).
"""

from __future__ import annotations

import logging
from typing import TypeVar

log = logging.getLogger("voice_typer.server.credential_store")

_T = TypeVar("_T")

#: The ``keyring`` service name. All Voice Typer secrets live under this
#: single service, with the provider name as the username key.
KEYRING_SERVICE_NAME = "com.voicetyper.keyring"

#: Prior service names used by Voice Typer. :func:`migrate_secrets_to_keyring`
#: copies any keyring entries stored under these names to
#: :data:`KEYRING_SERVICE_NAME` and deletes the originals.
_LEGACY_KEYRING_SERVICE_NAMES: tuple[str, ...] = ("app.voicetyper", "voice-typer")

#: Config flag key (in ``config.json``) that gates the legacy-service-name
#: cutover. Derived from the CURRENT :data:`KEYRING_SERVICE_NAME` so a
#: future service-name change automatically re-runs the cutover.
_SERVICE_NAME_MIGRATED_FLAG = f"service_name_migrated_{KEYRING_SERVICE_NAME.replace('.', '_')}"

#: The prefix used in config.json reference tokens. A flat api_key field
#: whose value starts with this prefix is treated as "the real secret
#: lives in the OS keychain".
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

#: Superset of :data:`PROVIDER_TO_CONFIG_FIELD` keys plus historical /
#: deprecated / typo'd provider names that prior app versions may have
#: stored in the OS keychain. The GDPR delete path
#: (:func:`voice_typer.server.credential_store._crud.delete_secret`)
#: iterates this superset so orphaned keychain entries are cleaned up
#: alongside current providers.
_KNOWN_PROVIDERS_HISTORY: frozenset[str] = frozenset(PROVIDER_TO_CONFIG_FIELD.keys())

#: Maximum length of a sanitized reason / diagnostic string.
_REASON_MAX_LEN = 200

__all__ = [
    "CONFIG_FIELD_TO_PROVIDER",
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
