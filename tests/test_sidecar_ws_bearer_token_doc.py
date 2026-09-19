"""Documentation regression test for the WS auth scheme docstring."""

from __future__ import annotations

import inspect

from voice_typer.server import sidecar_ws  # noqa: E402


def _strip_code_refs(text: str) -> str:
    """Remove accurate code references to the ``hmac.compare_digest`` helper."""
    cleaned = text.replace("hmac.compare_digest", "")
    cleaned = cleaned.replace(":func:`hmac.compare_digest`", "")
    return cleaned


def test_module_docstring_describes_bearer_token_not_hmac_scheme() -> None:
    """The module docstring must describe the bearer-token auth model."""
    module_doc = inspect.getdoc(sidecar_ws) or ""
    assert module_doc, "sidecar_ws module must have a docstring"

    # The actual auth model must be named.
    assert "bearer-token" in module_doc.lower() or "bearer token" in module_doc.lower(), (
        "module docstring must describe the bearer-token auth model "
        "(XZ-R4-001: previously claimed 'HMAC' which is misleading)"
    )

    # Compensating controls must be documented (ADR-0020 §3 threat-model note).
    assert "loopback" in module_doc.lower(), (
        "module docstring must document the loopback-only bind compensating control"
    )
    assert "ephemeral" in module_doc.lower(), "module docstring must document the ephemeral-port compensating control"
    assert "rotation" in module_doc.lower() or "rotat" in module_doc.lower(), (
        "module docstring must document the per-respawn token rotation compensating control"
    )

    # No standalone "HMAC scheme" / "HMAC token" / "HMAC auth frame" claims.
    cleaned = _strip_code_refs(module_doc)
    lowered = cleaned.lower()
    for misleading_phrase in ("hmac scheme", "hmac token", "hmac auth frame", "validate the hmac"):
        assert misleading_phrase not in lowered, (
            f"module docstring must not claim '{misleading_phrase}', the "
            "implementation is a bearer-token comparison, not an HMAC scheme "
            "(XZ-R4-001). References to the ``hmac.compare_digest`` Python "
            "helper are accurate and allowed."
        )


def test_authenticate_docstring_describes_bearer_token_not_hmac_scheme() -> None:
    """The ``_authenticate`` docstring must describe the bearer-token model."""
    auth_doc = inspect.getdoc(sidecar_ws._authenticate) or ""
    assert auth_doc, "sidecar_ws._authenticate must have a docstring"

    lowered = auth_doc.lower()

    # The actual auth model must be named.
    assert "bearer" in lowered, (
        "_authenticate docstring must describe the bearer-token auth model "
        "(XZ-R4-001: previously opened with 'validate the HMAC token')"
    )

    # Must explicitly disclaim the HMAC scheme.
    assert "not an hmac" in lowered, (
        "_authenticate docstring must explicitly state the implementation is "
        "NOT an HMAC scheme (so reviewers do not assume per-message MAC / "
        "nonce / replay protection exists)"
    )

    # Must cross-reference the compensating controls.
    assert "loopback" in lowered or "compensating" in lowered, (
        "_authenticate docstring must cross-reference the compensating "
        "controls (loopback-only bind + ephemeral port + per-respawn rotation)"
    )

    # The opening line must NOT say "validate the HMAC token".
    first_line = auth_doc.splitlines()[0] if auth_doc else ""
    assert "hmac token" not in first_line.lower(), (
        f"_authenticate docstring opening line must not say 'HMAC token', got: {first_line!r}"
    )


def test_authenticate_uses_hmac_compare_digest_at_runtime() -> None:
    """
    The runtime path must still use ``hmac.compare_digest`` for comparison.
    XZ-R4-001 fix is docstring-only and must NOT change the runtime
    """
    source = inspect.getsource(sidecar_ws._authenticate)
    # ``hmac.compare_digest``). Accept either form, the contract is
    assert "hmac.compare_digest" in source or "tokens_equal" in source, (
        "_authenticate must use hmac.compare_digest (directly or via the "
        "shared ipc.auth.tokens_equal helper) for constant-time token "
        "comparison (XZ-R4-001 fix is docstring-only; runtime behaviour is "
        "unchanged)"
    )
