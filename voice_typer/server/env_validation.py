"""PLAT-008: Environment variable validation.

Extracted from ``voice_typer/server/app.py`` (REF-3). Re-exported from
``app.py`` as ``_validate_env_vars`` so existing callers (notably
``voice_typer.server.logging_setup._setup_logging``) and tests that do
``from voice_typer.server.app import _validate_env_vars`` keep working.

SEC-audit-011: this module calls ``_validate_systemroot`` from
:mod:`voice_typer.server.config` to reject attacker-controlled SystemRoot
values that could enable DLL injection.
"""

import logging
import os
import re

log = logging.getLogger(__name__)


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
            log.warning(
                "[ENV] Invalid value for %s=%r -- expected boolean (1/0/true/false/yes/no). Resetting to empty.",
                var,
                val,
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
        log.warning(
            "[ENV] Invalid value for VOICE_TYPER_CONFIG_DIR=%r -- expected valid path. Resetting to empty.",
            config_dir,
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
        log.warning(
            "[ENV] Invalid value for HF_HOME=%r -- expected valid path. Resetting to empty.",
            hf_home,
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
            log.warning(
                "[ENV] HF_HOME=%r failed path-safety validation (%s) — "
                "discarding to prevent import_model path traversal.",
                hf_home,
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
            log.warning(
                "[ENV] Invalid value for HF_ENDPOINT=%r -- expected valid URL. Resetting to empty.",
                hf_endpoint,
            )
            os.environ.pop("HF_ENDPOINT", None)
        else:
            _validate_hf_endpoint(hf_endpoint)


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
        log.warning(
            "[ENV] HF_ENDPOINT=%r rejected — must use https:// scheme "
            "(got %r). Discarding to prevent plaintext model downloads.",
            raw,
            scheme or "<empty>",
        )
        os.environ.pop("HF_ENDPOINT", None)
        return
    if not hostname:
        log.warning(
            "[ENV] HF_ENDPOINT=%r rejected — could not parse hostname. Discarding to prevent download redirection.",
            raw,
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
            "[ENV] HF_ENDPOINT=%r rejected — hostname %r is not in the "
            "allowlist %s. Discarding to prevent download redirection "
            "to an attacker-controlled server.",
            raw,
            hostname_lower,
            sorted(_ALLOWED_HF_ENDPOINT_HOSTS),
        )
        os.environ.pop("HF_ENDPOINT", None)
        return
    log.debug(
        "[ENV] HF_ENDPOINT=%r accepted (host=%s, scheme=https).",
        raw,
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
    """
    if os.environ.get("TAURI_SIDECAR") != "1":
        return  # Not a sidecar — skip validation

    for var, expected in _EXPECTED_SIDECAR_ENV.items():
        actual = os.environ.get(var)
        if actual is None:
            log.warning("[SIDECAR-ENV] expected env var %s is unset (expected %s)", var, expected)
        elif expected == "<non-empty>" and not actual:
            log.warning("[SIDECAR-ENV] env var %s is empty (expected non-empty)", var)
