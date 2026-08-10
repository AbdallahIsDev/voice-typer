"""Security package (EO-23 consolidation).

Consolidates the former top-level security modules into one cohesive
package so a security review reads a single threat-model surface:

- :mod:`voice_typer.server.security.redaction` — secret + PII redaction
  (the redaction half of the former ``_secrets.py`` merged with the PII
  filter from the former ``security.py``).
- :mod:`voice_typer.server.security.url_allowlist` — cloud URL allowlist
  + SSRF defense (the allowlist half of the former ``_secrets.py``).
- :mod:`voice_typer.server.security.file_io` — secure atomic file I/O
  (the former ``secure_file_io.py``).
- :mod:`voice_typer.server.security.http_safety` — no-redirect /
  HTTPS-only urllib opener (the former ``_http_safety.py``).
- :mod:`voice_typer.server.security.model_integrity` — SHA-256 model
  verification + download allowlists (the integrity half of the former
  ``security.py`` merged with the former ``_model_integrity.py``).
- :mod:`voice_typer.server.security.win32_dacl` — Win32 restrictive DACL
  (the former ``_security_attributes.py``).

Backward compatibility: the old top-level module paths
(``voice_typer.server._secrets``, ``voice_typer.server.security``,
``voice_typer.server.secure_file_io``, ``voice_typer.server._http_safety``,
``voice_typer.server._security_attributes``, ``voice_typer.server._model_integrity``)
remain importable as re-export shims. New code should import from this
package (or its submodules) directly.
"""

from pathlib import Path as _Path  # noqa: F401 — re-exported for tests that patch security.Path.exists

from .file_io import (  # noqa: F401
    PersistedJSON,
    _chmod_owner_only,
    _secure_atomic_write,
    _secure_read_text,
    _windows_fsync_directory,
)
from .http_safety import build_secure_opener  # noqa: F401
from .model_integrity import (  # noqa: F401
    _INTEGRITY_CACHE_VERSION,
    ALLOW_PATTERNS_PARAKEET,
    ALLOW_PATTERNS_WHISPER,
    MODEL_HASHES,
    _integrity_cache_lock,
    _integrity_cache_path,
    _integrity_cache_path_override,
    _load_integrity_cache,
    _load_model_hashes,
    _save_integrity_cache,
    compute_file_sha256,
    verify_model_integrity,
)
from .redaction import (  # noqa: F401
    _FAST_TRIGGER,
    _FLAG_KEY_PATTERNS,
    _HOME_PATH_RE_CACHE,
    _KEY_PATTERNS,
    _MIN_REDACT_LEN,
    _PUBLIC_ENV_VAR_NAMES,
    _SECRET_KEYWORDS,
    PIIRedactionFilter,
    _flag_sub,
    _redact_home_path,
    _redact_home_path_in_text,
    _redact_text,
    _resolve_home_dirs,
    install_lastresort_pii_filter,
    redact_api_keys,
    redact_for_export,
    redact_pii,
    redact_secret,
    redact_url,
)
from .url_allowlist import (  # noqa: F401
    _DEFAULT_ALLOWED_HOSTS,
    _ENV_TRUSTED_HOSTS_VAR,
    _is_ip_literal,
    _is_private_ip,
    _load_env_allowlist_extensions,
    _normalize_host,
    _user_extensions,
    assert_url_allowed,
    extend_url_allowlist,
    get_url_allowlist,
    is_url_allowed,
)
from .win32_dacl import _create_restrictive_security_attributes  # noqa: F401

# ``security.Path`` must be the ``pathlib.Path`` class itself — tests
# monkeypatch ``security.Path.exists`` (see ``tests/test_model_integrity.py``)
# and the re-export keeps that contract identical to the former
# ``security.py`` module (which did ``from pathlib import Path``).
Path = _Path  # noqa: F401

__all__ = [
    # redaction
    "PIIRedactionFilter",
    "install_lastresort_pii_filter",
    "redact_api_keys",
    "redact_for_export",
    "redact_pii",
    "redact_secret",
    "redact_url",
    # url_allowlist
    "assert_url_allowed",
    "extend_url_allowlist",
    "get_url_allowlist",
    "is_url_allowed",
    # file_io
    "PersistedJSON",
    # http_safety
    "build_secure_opener",
    # model_integrity
    "ALLOW_PATTERNS_PARAKEET",
    "ALLOW_PATTERNS_WHISPER",
    "MODEL_HASHES",
    "compute_file_sha256",
    "verify_model_integrity",
    # win32_dacl
    "_create_restrictive_security_attributes",
]
