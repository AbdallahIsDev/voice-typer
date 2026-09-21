"""Environment validation probes at startup."""

import logging
import os
import re

# Import the canonical env-var name from the single source
from voice_typer.server._paths import IPC_TOKEN_ENV_VAR  # noqa: E402

log = logging.getLogger(__name__)

# Pre-compiled validation patterns (hoisted to module level so the
_BOOL_VALUE_PATTERN = re.compile(r"^(1|0|true|false|yes|no)$", re.IGNORECASE)
_TOKEN_VALUE_PATTERN = re.compile(r"^[A-Za-z0-9._\-]{1,128}$")
_PATH_VALUE_PATTERN = re.compile(r"^[^\0]+$")  # no null bytes


# Env-var names that are ALWAYS stripped from the child environment.
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
    """Validate all consumed environment variables."""

    _bool_vars = {"VOICE_TYPER_QUIET", "VOICE_TYPER_DEBUG", "VOICE_TYPER_NO_TRAY", "VOICE_TYPER_STREAMING"}

    for var in _bool_vars:
        val = os.environ.get(var)
        if val is not None and not _BOOL_VALUE_PATTERN.match(val):
            # pre-redact the env-var value at the call site.
            log.warning(
                "[ENV] Invalid value for %s=<redacted> -- expected boolean "
                "(1/0/true/false/yes/no). Resetting to empty.",
                var,
            )
            os.environ.pop(var, None)

    restart_val = os.environ.get("VOICE_TYPER_RESTART")
    if restart_val is not None and not _TOKEN_VALUE_PATTERN.match(restart_val):
        log.warning(
            (
                "[ENV] Invalid value for VOICE_TYPER_RESTART=<redacted> -- "
                "expected alphanumeric token. Resetting to empty."
            ),
        )
        os.environ.pop("VOICE_TYPER_RESTART", None)

    config_dir = os.environ.get("VOICE_TYPER_CONFIG_DIR")
    if config_dir is not None and (not _PATH_VALUE_PATTERN.match(config_dir) or len(config_dir) > 4096):
        # pre-redact the path value -- ``VOICE_TYPER_CONFIG_DIR`` typically
        log.warning(
            "[ENV] Invalid value for VOICE_TYPER_CONFIG_DIR=<redacted> -- expected valid path. Resetting to empty.",
        )
        os.environ.pop("VOICE_TYPER_CONFIG_DIR", None)
    elif config_dir is not None:
        # SEC-HFHOME-001 () pattern used for HF_HOME below —
        from pathlib import Path

        from voice_typer.server.config import _validate_path_safety

        try:
            _validate_path_safety(Path(config_dir), Path.home())
        except (ValueError, OSError, RuntimeError) as exc:
            # pre-redact the CONFIG_DIR value (path -> PII).
            log.warning(
                "[ENV] VOICE_TYPER_CONFIG_DIR=<redacted> failed path-safety validation (%s: %s), "
                "discarding to prevent config path traversal.",
                type(exc).__name__,
                exc,
            )
            os.environ.pop("VOICE_TYPER_CONFIG_DIR", None)

    ipc_token = os.environ.get(IPC_TOKEN_ENV_VAR)
    if ipc_token is not None and not _TOKEN_VALUE_PATTERN.match(ipc_token):
        log.warning(
            (
                f"[ENV] Invalid value for {IPC_TOKEN_ENV_VAR}=<redacted> -- "
                "expected alphanumeric token. Resetting to empty."
            ),
        )
        os.environ.pop(IPC_TOKEN_ENV_VAR, None)

    # SEC-audit-011: Validate SystemRoot on Windows to prevent DLL injection
    from voice_typer.server.config import _validate_systemroot

    _validate_systemroot()

    # SEC-HFHOME-001 (): HF_HOME is consumed as an allow-root by
    hf_home = os.environ.get("HF_HOME")
    if hf_home is not None and (not _PATH_VALUE_PATTERN.match(hf_home) or len(hf_home) > 4096):
        # pre-redact -- HF_HOME is a filesystem path, typically under
        log.warning(
            "[ENV] Invalid value for HF_HOME=<redacted> -- expected valid path. Resetting to empty.",
        )
        os.environ.pop("HF_HOME", None)
    elif hf_home is not None:
        # SEC-HFHOME-001 (): path-traversal / out-of-home check.
        from pathlib import Path

        from voice_typer.server.config import _validate_path_safety

        try:
            _validate_path_safety(Path(hf_home), Path.home())
        except (ValueError, OSError, RuntimeError) as exc:
            # pre-redact the HF_HOME value (path -> PII).
            log.warning(
                "[ENV] HF_HOME=<redacted> failed path-safety validation (%s: %s), "
                "discarding to prevent import_model path traversal.",
                type(exc).__name__,
                exc,
            )
            os.environ.pop("HF_HOME", None)

    # Validate HF_ENDPOINT if set. HF_ENDPOINT is consumed by
    hf_endpoint = os.environ.get("HF_ENDPOINT")
    if hf_endpoint is not None:
        if not _PATH_VALUE_PATTERN.match(hf_endpoint) or len(hf_endpoint) > 4096:
            # pre-redact -- HF_ENDPOINT is a URL, may carry a username
            log.warning(
                "[ENV] Invalid value for HF_ENDPOINT=<redacted> -- expected valid URL. Resetting to empty.",
            )
            os.environ.pop("HF_ENDPOINT", None)
        else:
            _validate_hf_endpoint(hf_endpoint)

    # Strip well-known cloud-provider API keys / model tokens from the
    for _sensitive_name in _SENSITIVE_ENV_NAMES:
        if os.environ.pop(_sensitive_name, None) is not None:
            log.warning(
                "[ENV] Sensitive env var %s was set in the parent shell, "
                "Voice Typer does not read it from env (cloud keys come from "
                "the keyring; the HF token is never used by Voice Typer "
                "itself). Discarding to prevent it from leaking into child "
                "processes (e.g. huggingface_hub model downloads).",
                _sensitive_name,
            )

    #  wire the sidecar-env contract check into the
    _validate_sidecar_env()


# Allowlist of hostnames that HF_ENDPOINT may point to.
_ALLOWED_HF_ENDPOINT_HOSTS = frozenset(
    {
        "huggingface.co",
        "hf-mirror.com",
    }
)


def _validate_hf_endpoint(raw: str) -> None:
    """validate and (if unsafe) pop ``HF_ENDPOINT`` from ``os.environ``."""
    from urllib.parse import urlparse

    parsed = urlparse(raw)
    scheme = (parsed.scheme or "").lower()
    hostname = parsed.hostname
    if scheme != "https":
        # pre-redact the raw HF_ENDPOINT value (URL -> PII / secret).
        log.warning(
            "[ENV] HF_ENDPOINT=<redacted> rejected: must use https:// scheme "
            "(got %r). Discarding to prevent plaintext model downloads.",
            scheme or "<empty>",
        )
        os.environ.pop("HF_ENDPOINT", None)
        return
    if not hostname:
        log.warning(
            "[ENV] HF_ENDPOINT=<redacted> rejected, could not parse hostname. "
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
            "[ENV] HF_ENDPOINT=<redacted> rejected, hostname %r is not in the "
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


# Sidecar env-var contract. Set by Rust host in src-tauri/src/sidecar/spawn.rs.
_EXPECTED_SIDECAR_ENV = {
    "TAURI_SIDECAR": "1",
    IPC_TOKEN_ENV_VAR: "<non-empty>",
    "VOICE_TYPER_NATIVE_DIR": "<non-empty path>",
}


def _validate_sidecar_env() -> None:
    """Log warnings for expected-but-unset sidecar env vars."""
    if os.environ.get("TAURI_SIDECAR") != "1":
        return  # Not a sidecar, skip validation

    from pathlib import Path

    from voice_typer.server.config import _validate_path_safety

    for var, expected in _EXPECTED_SIDECAR_ENV.items():
        actual = os.environ.get(var)
        if actual is None:
            log.warning("[SIDECAR-ENV] expected env var %s is unset (expected %s)", var, expected)
            continue

        if not actual:
            # Empty value: log + pop so downstream sees "unset" rather
            log.warning(
                "[SIDECAR-ENV] env var %s is empty (expected %s), popping",
                var,
                expected,
            )
            os.environ.pop(var, None)
            continue

        if expected == "<non-empty path>":
            # Mirror the HF_HOME / VOICE_TYPER_CONFIG_DIR pattern: first
            if len(actual) > 4096 or "\0" in actual:
                # pre-redact the path value (path -> PII).
                log.warning(
                    "[SIDECAR-ENV] env var %s=<redacted> failed basic path validation (length or NUL byte), popping",
                    var,
                )
                os.environ.pop(var, None)
                continue
            try:
                _validate_path_safety(Path(actual), Path.home())
            except (ValueError, OSError, RuntimeError) as exc:
                # pre-redact the path value (path -> PII). Log
                log.warning(
                    "[SIDECAR-ENV] env var %s=<redacted> failed path-safety validation (%s), popping",
                    var,
                    type(exc).__name__,
                )
                os.environ.pop(var, None)
