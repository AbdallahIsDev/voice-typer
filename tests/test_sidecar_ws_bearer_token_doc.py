"""Documentation regression test for the WS auth scheme docstring.

Background
----------
review.md entry XZ-R4-001 flagged that ``voice_typer/server/sidecar_ws.py``
described its handshake as "HMAC" in module + function docstrings, but
the actual implementation is a one-shot **bearer-token** check using
``hmac.compare_digest`` purely as a constant-time *comparison* helper
(no key derivation, no signing, no per-message MAC, no nonce).

ADR-0020 §3 was reconciled under ZR-56 (the section is now titled
"Bearer token lifecycle (cross-platform)" with an explicit reconciliation
note). The misleading "HMAC" wording in ``sidecar_ws.py`` was a residual
of the same historical naming drift.

These tests lock in the corrected docstring wording so a future
refactor does not silently revert the docstring to claim an HMAC
scheme that the implementation does not provide. A docstring claiming
HMAC would mislead reviewers into believing per-message MAC / nonce /
replay protection exists when it does not — a security-relevant
documentation drift.

Scope
-----
This is a documentation-only regression test. It does NOT re-assert
the runtime auth behaviour (that is covered by
``tests/test_sidecar_ws_auth_failed.py``). It only verifies the
docstrings + module-level comments describe the actual bearer-token
model.
"""

from __future__ import annotations

import inspect

from voice_typer.server import sidecar_ws  # noqa: E402

# ── Helpers ────────────────────────────────────────────────────────────


def _strip_code_refs(text: str) -> str:
    """Remove accurate code references to the ``hmac.compare_digest`` helper.

    ``hmac.compare_digest`` IS used at runtime for constant-time
    comparison; references to the function name are accurate and must
    NOT be flagged. Only standalone "HMAC scheme" / "HMAC token" /
    "HMAC auth frame" claims are misleading.
    """
    cleaned = text.replace("hmac.compare_digest", "")
    cleaned = cleaned.replace(":func:`hmac.compare_digest`", "")
    return cleaned


# ── Tests ─────────────────────────────────────────────────────────────


def test_module_docstring_describes_bearer_token_not_hmac_scheme() -> None:
    """The module docstring must describe the bearer-token auth model.

    The module-level docstring's architecture block previously said
    "sends the HMAC auth frame" — misleading because the
    implementation is a one-shot bearer-token comparison. After the
    XZ-R4-001 fix, the docstring must:

      1. Mention "bearer-token" (the actual model).
      2. Document the compensating controls (loopback-only bind,
         ephemeral port, per-respawn token rotation) — these are the
         threat-model note ADR-0020 §3 requires.
      3. NOT claim an HMAC scheme (the implementation does not derive
         a key, sign, or verify a MAC).
    """
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
            f"module docstring must not claim '{misleading_phrase}' — the "
            "implementation is a bearer-token comparison, not an HMAC scheme "
            "(XZ-R4-001). References to the ``hmac.compare_digest`` Python "
            "helper are accurate and allowed."
        )


def test_authenticate_docstring_describes_bearer_token_not_hmac_scheme() -> None:
    """The ``_authenticate`` docstring must describe the bearer-token model.

    Previously the docstring opened with "Read the first WS frame and
    validate the HMAC token" — misleading for the same reason as the
    module docstring. After XZ-R4-001 the docstring must:

      1. Open with "bearer token" (not "HMAC token").
      2. Explicitly state the implementation is NOT an HMAC scheme.
      3. Cross-reference the compensating controls documented at the
         module level.
    """
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
        f"_authenticate docstring opening line must not say 'HMAC token' — got: {first_line!r}"
    )


def test_authenticate_uses_hmac_compare_digest_at_runtime() -> None:
    """The runtime path must still use ``hmac.compare_digest`` for comparison.

    A regression that swapped ``hmac.compare_digest`` for a plain ``==``
    would reintroduce a timing side-channel on the auth comparison.
    This test locks in the constant-time comparison contract — the
    XZ-R4-001 fix is docstring-only and must NOT change the runtime
    behaviour.
    """
    source = inspect.getsource(sidecar_ws._authenticate)
    # VP-8: the constant-time comparison moved to the shared helper
    # ``voice_typer.server.ipc.auth.tokens_equal`` (which wraps
    # ``hmac.compare_digest``). Accept either form — the contract is
    # "constant-time comparison via hmac", and a regression to a plain
    # ``==`` fails BOTH anchors.
    assert "hmac.compare_digest" in source or "tokens_equal" in source, (
        "_authenticate must use hmac.compare_digest (directly or via the "
        "shared ipc.auth.tokens_equal helper) for constant-time token "
        "comparison (XZ-R4-001 fix is docstring-only; runtime behaviour is "
        "unchanged)"
    )
