"""PLAT-008: Environment variable validation.

Extracted from ``voice_typer/server/app.py`` (REF-3). Re-exported from
``app.py`` as ``_validate_env_vars`` so existing callers (notably
``voice_typer.server.logging_setup._setup_logging``) and tests that do
``from voice_typer.server.app import _validate_env_vars`` keep working.

SEC-audit-011: this module calls ``_validate_systemroot`` from
:mod:`voice_typer.server.config` to reject attacker-controlled SystemRoot
values that could enable DLL injection.

FR-18 (P4-A1): this module ALSO strips well-known cloud-provider API
keys / model-download tokens (``HF_TOKEN``, ``HUGGING_FACE_HUB_TOKEN``,
``OPENAI_API_KEY``, ``ANTHROPIC_API_KEY``, ``GEMINI_API_KEY``,
``DEEPGRAM_API_KEY``, ``GROQ_API_KEY``) from ``os.environ`` at startup.
The Tauri production path strips these via ``env_clear()`` in
``src-tauri/src/sidecar/spawn.rs``; the Electron launcher strips them
from the Electron FRONTEND child but NOT from the Python sidecar
process itself. Standalone mode (``python -m voice_typer.server``)
inherits the parent shell env verbatim — so a developer with
``HF_TOKEN`` exported in their shell would have huggingface_hub
silently attach their personal HF token to model-download requests
(``asr_setup.py:417`` calls ``snapshot_download()`` WITHOUT
``token=``). This validator closes that gap for the
Electron-sidecar / standalone paths.
"""

import logging
import os
import re

log = logging.getLogger(__name__)


# FR-18 (P4-A1): env-var names that are ALWAYS stripped from
# ``os.environ`` by ``_validate_env_vars`` at startup. These are the
# well-known cloud-provider API keys / model-download tokens that the
# Python server NEVER reads from env (cloud keys come from the
# keyring via ``credential_store``; the HF token is never used by
# Voice Typer itself).
#
# This list MUST stay in sync with ``electron_launcher._SENSITIVE_ENV_NAMES``.
# The duplication is deliberate (env_validation is a low-level startup
# module; importing electron_launcher would pull in ``_electron_build``
# and ``platform_utils`` at startup time, which is intentionally
# avoided — see ``shutdown_controller.py:917`` and
# ``ipc_server.py:2039`` which both lazy-import electron_launcher for
# the same reason). Drift is caught by
# ``tests/test_env_validation_sensitive_env.py::TestSensitiveEnvNamesDriftDetection``.
_SENSITIVE_ENV_NAMES = frozenset(
    {
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "GEMINI_API_KEY",
        "HF_TOKEN",
        "HUGGING_FACE_HUB_TOKEN",
        "DEEPGRAM_API_KEY",
        "GROQ_API_KEY",
    }
)


def _validate_env_vars() -> None:
    """PLAT-008: Validate all consumed environment variables.

    Rejects values that don't match expected patterns. Logs warnings
    for invalid values and resets them to safe defaults.
    """

    _bool_vars = {"VOICE_TYPER_QUIET", "VOICE_TYPER_DEBUG", "VOICE_TYPER_NO_TRAY", "VOICE_TYPER_STREAMING"}
    _bool_pattern = re.compile(r"^(1|0|true|false|yes|no)$", re.IGNORECASE)
    _token_pattern = re.compile(r"^[A-Za-z0-9._\-]{1,128}$")
    _path_pattern = re.compile(r"^[^\0]+$")  # no null bytes

    for var in _bool_vars:
        val = os.environ.get(var)
        if val is not None and not _bool_pattern.match(val):
            # GT-63: pre-redact the env-var value at the call site.
            # The previous ``%r`` of the raw value leaked whatever the
            # user typed (which may carry a username, partial secret,
            # or shell-injection payload) into the log record's msg
            # args -- any handler that bypasses the PIIRedactionFilter
            # (third-party handlers, the ``print()`` fallback in
            # ipc_diagnostics.py:154) would write it verbatim.  Booleans
            # are on the safe-list, but a *failed* boolean validation
            # means the value is NOT a boolean -- treat it as opaque.
            log.warning(
                "[ENV] Invalid value for %s=<redacted> -- expected boolean "
                "(1/0/true/false/yes/no). Resetting to empty.",
                var,
            )
            os.environ.pop(var, None)

    restart_val = os.environ.get("VOICE_TYPER_RESTART")
    if restart_val is not None and not _token_pattern.match(restart_val):
        log.warning(
            (
                "[ENV] Invalid value for VOICE_TYPER_RESTART=<redacted> -- "
                "expected alphanumeric token. Resetting to empty."
            ),
        )
        os.environ.pop("VOICE_TYPER_RESTART", None)

    config_dir = os.environ.get("VOICE_TYPER_CONFIG_DIR")
    if config_dir is not None and (not _path_pattern.match(config_dir) or len(config_dir) > 4096):
        # GT-63: pre-redact the path value -- ``VOICE_TYPER_CONFIG_DIR`` typically
        # carries a username (``/Users/jane.doe/...``) which is PII; never log raw.
        log.warning(
            "[ENV] Invalid value for VOICE_TYPER_CONFIG_DIR=<redacted> -- expected valid path. Resetting to empty.",
        )
        os.environ.pop("VOICE_TYPER_CONFIG_DIR", None)
    elif config_dir is not None:
        # XZ-14-07: path-traversal / out-of-home check.  Mirror the
        # SEC-HFHOME-001 (HIGH-12) pattern used for HF_HOME below —
        # VOICE_TYPER_CONFIG_DIR is consumed by ``config._config_dir()``
        # which already runs ``_validate_path_safety`` itself, but
        # defense-in-depth requires the env-var entry point to reject
        # traversal values *here* too (so the unsafe value never
        # reaches a downstream consumer that forgets to re-validate).
        # Import locally to avoid a circular import at module load time
        # (config.py is heavy and pulls in many dependencies).
        from pathlib import Path

        from voice_typer.server.config import _validate_path_safety

        try:
            _validate_path_safety(Path(config_dir), Path.home())
        except (ValueError, OSError, RuntimeError) as exc:
            # GT-63: pre-redact the CONFIG_DIR value (path -> PII).
            # GT-B1-14: include the exception *type name* so the operator
            # knows which validation predicate failed (ValueError vs
            # OSError vs RuntimeError) without having to grep the source.
            # ``%s`` of the exception instance itself is safe -- the
            # validation predicates raise with messages that describe the
            # *rule* that failed ("path escapes home directory"), not the
            # *value* that failed (the path itself stays redacted).
            log.warning(
                "[ENV] VOICE_TYPER_CONFIG_DIR=<redacted> failed path-safety validation (%s: %s) — "
                "discarding to prevent config path traversal.",
                type(exc).__name__,
                exc,
            )
            os.environ.pop("VOICE_TYPER_CONFIG_DIR", None)

    ipc_token = os.environ.get("VOICE_TYPER_IPC_TOKEN")
    if ipc_token is not None and not _token_pattern.match(ipc_token):
        log.warning(
            (
                "[ENV] Invalid value for VOICE_TYPER_IPC_TOKEN=<redacted> -- "
                "expected alphanumeric token. Resetting to empty."
            ),
        )
        os.environ.pop("VOICE_TYPER_IPC_TOKEN", None)

    # SEC-audit-011: Validate SystemRoot on Windows to prevent DLL injection
    from voice_typer.server.config import _validate_systemroot

    _validate_systemroot()

    # PLAT-008: Validate HF_HOME is a valid path if set.
    # SEC-HFHOME-001 (HIGH-12): HF_HOME is consumed as an allow-root by
    # ``config._validate_import_path`` (so ``import_model`` can import
    # directories under it).  A malicious value like ``HF_HOME=/etc``
    # would let the renderer import any directory under ``/etc``.
    # After the basic pattern check, also run the same
    # ``_validate_path_safety(Path(hf_home), Path.home())`` check that
    # ``_config_dir()`` uses for ``VOICE_TYPER_CONFIG_DIR`` — this
    # rejects values that escape the user's home directory via ``..``
    # or absolute paths outside home.  If validation fails, log a
    # warning and discard the unsafe value so downstream consumers
    # never see it.
    hf_home = os.environ.get("HF_HOME")
    if hf_home is not None and (not _path_pattern.match(hf_home) or len(hf_home) > 4096):
        # GT-63: pre-redact -- HF_HOME is a filesystem path, typically under
        # the user's home directory, so it carries a username.
        log.warning(
            "[ENV] Invalid value for HF_HOME=<redacted> -- expected valid path. Resetting to empty.",
        )
        os.environ.pop("HF_HOME", None)
    elif hf_home is not None:
        # SEC-HFHOME-001 (HIGH-12): path-traversal / out-of-home check.
        # Import locally to avoid a circular import at module load time
        # (config.py is heavy and pulls in many dependencies).
        from pathlib import Path

        from voice_typer.server.config import _validate_path_safety

        try:
            _validate_path_safety(Path(hf_home), Path.home())
        except (ValueError, OSError, RuntimeError) as exc:
            # GT-63: pre-redact the HF_HOME value (path -> PII).
            # GT-B1-14: include the exception *type name* so the operator
            # knows which validation predicate failed (ValueError vs
            # OSError vs RuntimeError) without having to grep the source.
            # ``%s`` of the exception instance itself is safe -- the
            # validation predicates raise with messages that describe the
            # *rule* that failed ("path escapes home directory"), not the
            # *value* that failed (the path itself stays redacted).
            log.warning(
                "[ENV] HF_HOME=<redacted> failed path-safety validation (%s: %s) — "
                "discarding to prevent import_model path traversal.",
                type(exc).__name__,
                exc,
            )
            os.environ.pop("HF_HOME", None)

    # G4-M-58: Validate HF_ENDPOINT if set. HF_ENDPOINT is consumed by
    # the ``huggingface_hub`` library as the base URL for model
    # downloads (used to redirect to mirrors like hf-mirror.com for
    # users in regions where huggingface.co is blocked or slow). An
    # attacker-controlled HF_ENDPOINT could redirect model downloads to
    # a malicious server that serves tampered weights, so we:
    #   1. Require HTTPS (reject http:// scheme).
    #   2. Validate the hostname is well-formed.
    #   3. Allowlist to huggingface.co and hf-mirror.com (the two
    #      officially supported endpoints).
    # On validation failure, log a WARNING and pop the env var so
    # downstream consumers never see the unsafe value (same pattern as
    # HF_HOME above).
    hf_endpoint = os.environ.get("HF_ENDPOINT")
    if hf_endpoint is not None:
        if not _path_pattern.match(hf_endpoint) or len(hf_endpoint) > 4096:
            # GT-63: pre-redact -- HF_ENDPOINT is a URL, may carry a username
            # (``https://jane%40example.com@mirror/``) or a query-string key.
            log.warning(
                "[ENV] Invalid value for HF_ENDPOINT=<redacted> -- expected valid URL. Resetting to empty.",
            )
            os.environ.pop("HF_ENDPOINT", None)
        else:
            _validate_hf_endpoint(hf_endpoint)

    # FR-18 (P4-A1): strip well-known cloud-provider API keys / model
    # download tokens from ``os.environ``. The Tauri production path
    # already strips these via ``env_clear()`` in
    # ``src-tauri/src/sidecar/spawn.rs``; the Electron launcher
    # (``electron_launcher._strip_sensitive_env``) strips them from the
    # Electron FRONTEND child only — NOT from the Python sidecar
    # process itself. Standalone mode (``python -m voice_typer.server``)
    # inherits the parent shell env verbatim. Closing this gap prevents
    # a developer's exported ``HF_TOKEN`` from being silently attached
    # to ``huggingface_hub.snapshot_download()`` calls in
    # ``asr_setup.py`` (which calls snapshot_download WITHOUT
    # ``token=`` so huggingface_hub falls back to ``os.environ``).
    #
    # GT-63: log the key NAME only — never the value (these are
    # secrets). The warning lets an operator diagnose "why is my env
    # var being ignored?" without leaking the secret to the log.
    for _sensitive_name in _SENSITIVE_ENV_NAMES:
        if os.environ.pop(_sensitive_name, None) is not None:
            log.warning(
                "[ENV] Sensitive env var %s was set in the parent shell — "
                "Voice Typer does not read it from env (cloud keys come from "
                "the keyring; the HF token is never used by Voice Typer "
                "itself). Discarding to prevent it from leaking into child "
                "processes (e.g. huggingface_hub model downloads).",
                _sensitive_name,
            )

    # EC-FIX-10 / EC-24: wire the sidecar-env contract check into the
    # single env-validation entry point so missing sidecar env vars are
    # logged at startup. No-op when not running under the Tauri host
    # (i.e. when ``TAURI_SIDECAR != "1"``).
    _validate_sidecar_env()


# G4-M-58: Allowlist of hostnames that HF_ENDPOINT may point to.
# ``huggingface.co`` is the official upstream; ``hf-mirror.com`` is the
# widely-used community mirror for users in regions where the official
# endpoint is blocked or slow. Any other hostname is rejected because
# model downloads would land on an attacker-controlled server.
_ALLOWED_HF_ENDPOINT_HOSTS = frozenset(
    {
        "huggingface.co",
        "hf-mirror.com",
    }
)


def _validate_hf_endpoint(raw: str) -> None:
    """G4-M-58: validate and (if unsafe) pop ``HF_ENDPOINT`` from ``os.environ``.

    Parameters
    ----------
    raw:
        The raw value of the ``HF_ENDPOINT`` env var (already
        basic-pattern-checked by the caller — non-empty, no NUL bytes,
        length ≤ 4096).

    Validation rules:
      1. The URL MUST use the ``https://`` scheme. ``http://`` (even
         ``http://localhost``) is rejected because model-download
         traffic would travel in plaintext and a MitM could swap the
         weights.
      2. The hostname MUST be well-formed (parseable by
         :class:`urllib.parse.urlparse` with a non-empty ``hostname``).
      3. The hostname MUST be in :data:`_ALLOWED_HF_ENDPOINT_HOSTS`
         (``huggingface.co`` or ``hf-mirror.com``). Subdomains
         (e.g. ``cdn.huggingface.co``) are also accepted — the check
         uses ``endswith`` against the suffix ``.<host>``.

    On any failure, the env var is removed via ``os.environ.pop`` and a
    WARNING is logged (same pattern as the ``HF_HOME`` path-safety
    check above). The function never raises.
    """
    from urllib.parse import urlparse

    parsed = urlparse(raw)
    scheme = (parsed.scheme or "").lower()
    hostname = parsed.hostname
    if scheme != "https":
        # GT-63: pre-redact the raw HF_ENDPOINT value (URL -> PII / secret).
        log.warning(
            "[ENV] HF_ENDPOINT=<redacted> rejected — must use https:// scheme "
            "(got %r). Discarding to prevent plaintext model downloads.",
            scheme or "<empty>",
        )
        os.environ.pop("HF_ENDPOINT", None)
        return
    if not hostname:
        log.warning(
            "[ENV] HF_ENDPOINT=<redacted> rejected — could not parse hostname. "
            "Discarding to prevent download redirection.",
        )
        os.environ.pop("HF_ENDPOINT", None)
        return
    hostname_lower = hostname.lower()
    allowed = any(
        hostname_lower == allowed_host or hostname_lower.endswith("." + allowed_host)
        for allowed_host in _ALLOWED_HF_ENDPOINT_HOSTS
    )
    if not allowed:
        log.warning(
            "[ENV] HF_ENDPOINT=<redacted> rejected — hostname %r is not in the "
            "allowlist %s. Discarding to prevent download redirection "
            "to an attacker-controlled server.",
            hostname_lower,
            sorted(_ALLOWED_HF_ENDPOINT_HOSTS),
        )
        os.environ.pop("HF_ENDPOINT", None)
        return
    log.debug(
        "[ENV] HF_ENDPOINT=<redacted> accepted (host=%s, scheme=https).",
        hostname_lower,
    )


# Sidecar env-var contract — set by Rust host in src-tauri/src/sidecar/spawn.rs:79-84
_EXPECTED_SIDECAR_ENV = {
    "TAURI_SIDECAR": "1",
    "VOICE_TYPER_IPC_TOKEN": "<non-empty>",
    "VOICE_TYPER_NATIVE_DIR": "<non-empty path>",
    "VOICE_TYPER_PREWARM_EXE": "<non-empty path>",
}


def _validate_sidecar_env() -> None:
    """Log warnings for expected-but-unset sidecar env vars.

    Per sub-agent 1-2 Finding I: each module independently reads its env vars
    with os.environ.get(...), with no shared validator. A future change to
    spawn.rs that drops an env var would silently degrade Python-side
    behavior with no startup warning.

    EC-FIX-10 / EC-24: now wired into :func:`_validate_env_vars` (called
    from :func:`voice_typer.server.logging_setup._setup_logging` at
    startup), so the sidecar env-var contract is checked on every server
    boot. No-op when ``TAURI_SIDECAR != "1"`` (i.e. when running standalone
    via ``python -m voice_typer.server``).
    """
    if os.environ.get("TAURI_SIDECAR") != "1":
        return  # Not a sidecar — skip validation

    for var, expected in _EXPECTED_SIDECAR_ENV.items():
        actual = os.environ.get(var)
        if actual is None:
            log.warning("[SIDECAR-ENV] expected env var %s is unset (expected %s)", var, expected)
        elif expected == "<non-empty>" and not actual:
            log.warning("[SIDECAR-ENV] env var %s is empty (expected non-empty)", var)
